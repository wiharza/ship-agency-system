"""
Automated Integration Test untuk Web API & Tracker Sistem Keagenan Kapal.
Menggunakan Python stdlib (urllib, threading, socketserver, unittest).
Dapat dijalankan langsung: python tests/test_api.py
"""

import sys
import os
import threading
import time
import json
from datetime import datetime, timedelta
import urllib.request
import urllib.error
import http.cookiejar

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Tes memakai DB SEMENTARA (ADR-0016), bukan agency.db sungguhan — suite
# tidak lagi menumpuk port call/kapal uji di DB lokal. HARUS sebelum
# `from server import ...` karena engine.db menentukan DB_FILE saat import.
import tempfile
import atexit
import shutil

_TEST_TMPDIR = tempfile.mkdtemp(prefix="ship-agency-test-")
os.environ["AGENCY_DB_FILE"] = os.path.join(_TEST_TMPDIR, "agency-test.db")
atexit.register(shutil.rmtree, _TEST_TMPDIR, True)

from server import AgencyAPIHandler, run_server
import socketserver

TEST_PORT = 8999


def test_integration():
    # Autentikasi aktif di tes ini (ADR-0011): secret khusus tes, bukan
    # secret produksi. Semua request memakai cookie session dari login.
    os.environ["SESSION_SECRET"] = "test-secret-lokal-jangan-dipakai-di-produksi"
    from engine.db import get_db, init_db, seed_sample_data, create_user
    # Siapkan skema + data contoh dulu: sejak agency.db tidak lagi di-commit,
    # clone baru (dan CI) tidak punya DB — init_db() membuatnya dari kode.
    init_db()
    seed_sample_data()
    # Reset status sample port call agar test idempotent
    conn = get_db()
    with conn:
        conn.execute("UPDATE port_calls SET ops_closed_by = NULL, ops_closed_at = NULL, finance_closed_by = NULL, finance_closed_at = NULL, spb_issued_at = NULL, spb_expiry_at = NULL, atd = NULL, status = 'PRE_ARRIVAL' WHERE id = 'PC-2026-JKT-0001'")
    conn.close()
    # User tes (idempotent): hapus dulu lalu buat lagi dengan password tetap.
    conn = get_db()
    with conn:
        conn.execute("DELETE FROM users WHERE username IN ('ops_tes', 'fin_tes', 'adm_tes')")
    conn.close()
    conn = get_db()
    create_user(conn, "ops_tes", "ops-pass-123", "Ops Tes", "OPS")
    create_user(conn, "fin_tes", "fin-pass-123", "Fin Tes", "FINANCE")
    create_user(conn, "adm_tes", "adm-pass-123", "Admin Tes", "ADMIN")
    conn.close()

    socketserver.ThreadingTCPServer.allow_reuse_address = True  # sebelum bind (TIME_WAIT)
    # Server tes multi-thread seperti run_server (ADR-0013) — perlu untuk
    # menguji pembuatan port call bersamaan (Test 24).
    server = socketserver.ThreadingTCPServer(("", TEST_PORT), AgencyAPIHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    time.sleep(0.5)

    base_url = f"http://127.0.0.1:{TEST_PORT}"

    def _login_opener(username, password):
        """Opener urllib dengan cookie jar sendiri; login sekali, lalu
        semua open() berikutnya otomatis membawa cookie session user itu."""
        jar = http.cookiejar.CookieJar()
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        req = urllib.request.Request(
            f"{base_url}/api/login",
            data=json.dumps({"username": username, "password": password}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with op.open(req) as res:
            assert res.status == 200, f"Login {username} gagal"
            set_cookie = res.headers.get("Set-Cookie") or ""
            assert "HttpOnly" in set_cookie, "Cookie session harus HttpOnly"
            assert "ship_agency_session=" in set_cookie
        return op

    try:
        # 1. Test GET / tanpa login -> halaman login (bukan UI aplikasi)
        with urllib.request.urlopen(f"{base_url}/") as res:
            assert res.status == 200
            html = res.read().decode("utf-8")
            assert 'id="login-form"' in html, "GET / tanpa session harus menampilkan halaman login"
        print("[TEST 1/22] GET / tanpa login menampilkan halaman login.")

        # 17. Test Auth: API tanpa cookie -> 401; login salah -> 401;
        #     login benar -> 200 + cookie HttpOnly.
        try:
            urllib.request.urlopen(f"{base_url}/api/port-calls")
            assert False, "API tanpa cookie harus 401"
        except urllib.error.HTTPError as e:
            assert e.code == 401
        req_bad = urllib.request.Request(
            f"{base_url}/api/login",
            data=json.dumps({"username": "ops_tes", "password": "salah"}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req_bad)
            assert False, "Login password salah harus 401"
        except urllib.error.HTTPError as e:
            assert e.code == 401
        ops_opener = _login_opener("ops_tes", "ops-pass-123")
        fin_opener = _login_opener("fin_tes", "fin-pass-123")
        urllib.request.install_opener(ops_opener)  # urlopen berikut = cookie OPS
        print("[TEST 17/22] Auth: 401 tanpa cookie, 401 password salah, login OK + cookie HttpOnly.")

        # 1b. Test GET / dengan session -> UI aplikasi (bukan halaman login)
        with urllib.request.urlopen(f"{base_url}/") as res:
            assert res.status == 200
            html = res.read().decode("utf-8")
            assert "SISTEM KEAGENAN KAPAL NASIONAL" in html
            assert 'id="login-form"' not in html
        with urllib.request.urlopen(f"{base_url}/api/me") as res:
            me = json.loads(res.read().decode("utf-8"))
            assert me["username"] == "ops_tes" and me["role"] == "OPS"
        print("[TEST 1b/22] GET / dengan session menampilkan UI + /api/me benar.")

        # 2. Test GET /api/port-calls
        with urllib.request.urlopen(f"{base_url}/api/port-calls") as res:
            assert res.status == 200
            data = json.loads(res.read().decode("utf-8"))
            assert len(data["port_calls"]) > 0
            pc = next(p for p in data["port_calls"] if p["id"] == "PC-2026-JKT-0001")
            pc_id = pc["id"]
        print(f"[TEST 2/8] GET /api/port-calls Berhasil (Ditemukan {len(data['port_calls'])} port calls).")

        # 3. Test GET /api/port-calls/<id>
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{pc_id}") as res:
            assert res.status == 200
            detail = json.loads(res.read().decode("utf-8"))
            assert detail["id"] == pc_id
            assert detail["vessel_name"] == "MV. PACIFIC GLORY"
            assert len(detail["clearances"]) >= 4
        print(f"[TEST 3/8] GET /api/port-calls/{pc_id} Detail valid.")

        # 4. Test POST Milestone (EOSP)
        req = urllib.request.Request(
            f"{base_url}/api/port-calls/{pc_id}/milestone",
            data=json.dumps({"milestone_code": "EOSP", "event_time": "2026-11-01 07:30", "remarks": "Tiba di ambang luar"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req) as res:
            assert res.status == 200
        print("[TEST 4/8] POST Milestone EOSP Berhasil.")

        # 5. Test POST SPB (Surat Persetujuan Berlayar - 24h timer).
        # Tanggal sengaja sudah kedaluwarsa saat Tes 7 menerbitkan ulang
        # (ADR-0017: terbit ulang hanya sah bila SPB lama kedaluwarsa).
        req_spb = urllib.request.Request(
            f"{base_url}/api/port-calls/{pc_id}/spb",
            data=json.dumps({"doc_number": "SPB.IDTPP.2026.001", "issued_at": "2026-10-29 10:00:00"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req_spb) as res:
            assert res.status == 200
            spb_res = json.loads(res.read().decode("utf-8"))
            assert spb_res["spb_expiry_at"] == "2026-10-30 10:00:00"
        print(f"[TEST 5/8] POST SPB Valid (Expiry tepat 24 jam: {spb_res['spb_expiry_at']}).")

        # 6. Test POST Laytime Calculation
        req_lt = urllib.request.Request(
            f"{base_url}/api/port-calls/{pc_id}/laytime/calculate",
            data=json.dumps({
                "nor_tendered_time": "2026-11-01 08:00",
                "commenced_time": "2026-11-01 14:00",
                "completed_time": "2026-11-03 14:00"
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req_lt) as res:
            assert res.status == 200
            lt_res = json.loads(res.read().decode("utf-8"))
            assert "claim_type" in lt_res["result"]
            assert lt_res["result"]["amount_usd"] > 0
        print(f"[TEST 6/8] POST Laytime Calculate Valid ({lt_res['result']['claim_type']} ${lt_res['result']['amount_usd']}).")

        # 7. Test Dual Sign-off Validation
        # Coba sign finance dulu -> Harusnya gagal (HTTP 400) karena ops belum sign-off.
        # Pakai cookie FINANCE supaya lolos gate role; 400-nya dari urutan otorisasi.
        req_fin = urllib.request.Request(
            f"{base_url}/api/port-calls/{pc_id}/close-finance",
            data=json.dumps({"user": "Finance Mgr"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        try:
            fin_opener.open(req_fin)
            assert False, "Finance closure harus ditolak jika Ops belum sign-off"
        except urllib.error.HTTPError as e:
            assert e.code == 400
        print("[TEST 7/8] Dual Sign-off Barrier Valid (Finance tertolak sebelum Ops).")

        # Gate close-ops: SPB test 5 bertanggal tetap (2026-11-05) bisa
        # kedaluwarsa bila tes dijalankan sesudahnya — terbitkan ulang
        # dengan issued_at = sekarang agar SPB dijamin masih berlaku,
        # lalu catat SAILED agar status jadi DEPARTED sebelum close-ops.
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        req_spb_now = urllib.request.Request(
            f"{base_url}/api/port-calls/{pc_id}/spb",
            data=json.dumps({"doc_number": "SPB.IDTPP.2026.001", "issued_at": now_str}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req_spb_now) as res:
            assert res.status == 200
        req_sailed = urllib.request.Request(
            f"{base_url}/api/port-calls/{pc_id}/milestone",
            data=json.dumps({"milestone_code": "SAILED", "event_time": now_str, "remarks": "Berangkat setelah SPB terbit"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req_sailed) as res:
            assert res.status == 200
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{pc_id}") as res:
            det = json.loads(res.read().decode("utf-8"))
            assert det["status"] == "DEPARTED", f"SAILED harus mengubah status jadi DEPARTED, dapat {det['status']}"
            assert det["atd"] == now_str, f"atd harus terisi dari event_time SAILED, dapat {det['atd']}"

        # Sekarang sign-off Ops dulu
        req_ops = urllib.request.Request(
            f"{base_url}/api/port-calls/{pc_id}/close-ops",
            data=json.dumps({"user": "Capt. Hadi"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req_ops) as res:
            assert res.status == 200

        # Lalu sign-off Finance (cookie FINANCE)
        with fin_opener.open(req_fin) as res:
            assert res.status == 200
        print("[TEST 7/8b] Dual Sign-off Berhasil (Ops -> Finance -> Status CLOSED).")

        # 8. Test Dynamic Handover Shift Summary
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{pc_id}/handover") as res:
            assert res.status == 200
            ho = json.loads(res.read().decode("utf-8"))
            assert "RINGKASAN HANDOVER SHIFT" in ho["handover_text"]
        print("[TEST 8/8] GET Dynamic Handover Shift Berhasil.")

        # Tes 7 menutup JKT-0001 sampai CLOSED — kunci CLOSED (ADR-0017)
        # akan menolak semua tulis berikutnya ke port call itu. Buka lagi
        # lewat DB (setup tes, bukan endpoint — tidak ada "buka kunci" di
        # API) supaya Tes 11/12/23 yang memakai port call contoh tetap
        # bisa menulis; kuncinya sendiri diuji penuh di Tes 28 (pc2).
        conn = get_db()
        with conn:
            conn.execute("UPDATE port_calls SET ops_closed_by = NULL, ops_closed_at = NULL, finance_closed_by = NULL, finance_closed_at = NULL, status = 'DEPARTED' WHERE id = 'PC-2026-JKT-0001'")
        conn.close()

        # 9. Test Master Ports & Pre-determined List (Typo Prevention)
        with urllib.request.urlopen(f"{base_url}/api/master/ports") as res:
            assert res.status == 200
            mdata = json.loads(res.read().decode("utf-8"))
            assert any(p["code"] == "IDTPP" for p in mdata["ports"])
            assert any(p["code"] == "IDKOL" for p in mdata["ports"])
            kol = next(p for p in mdata["ports"] if p["code"] == "IDKOL")
            assert len(kol["terminals"]) >= 2
        print("[TEST 9/10] Master Ports & Terminals Pre-determined List Berhasil dimuat.")

        # 10. Test Typo Prevention Barrier (Invalid port_code ditolak 400)
        req_invalid = urllib.request.Request(
            f"{base_url}/api/port-calls",
            data=json.dumps({
                "vessel_imo": "9876543",
                "port_code": "IDTYPO99", # Kode salah ketik
                "terminal_name": "Terminal Test"
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        try:
            urllib.request.urlopen(req_invalid)
            assert False, "Port code typo/tidak terdaftar harus ditolak 400"
        except urllib.error.HTTPError as e:
            assert e.code == 400
        print("[TEST 10/12] Typo Prevention Barrier Berhasil (Kode pelabuhan salah ditolak 400).")

        # 11. Test Add New Clearance Item
        req_add_cl = urllib.request.Request(
            f"{base_url}/api/port-calls/{pc_id}/clearance",
            data=json.dumps({
                "agency": "BEA_CUKAI",
                "doc_type": "Outward Manifest BC 1.2",
                "doc_number": "",
                "status": "SUBMITTED",
                "remarks": "Pengajuan manifes keberangkatan"
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req_add_cl) as res:
            assert res.status == 200
        print("[TEST 11/12] Tambah Item Dokumen CIGP Baru Berhasil.")

        # 12. Test Update Clearance Item (Nomor Dokumen & Status)
        # Ambil id clearance yang baru dibuat
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{pc_id}") as res:
            pdata = json.loads(res.read().decode("utf-8"))
            bc12 = next(c for c in pdata["clearances"] if c["doc_type"] == "Outward Manifest BC 1.2")
            cl_id = bc12["id"]

        req_upd_cl = urllib.request.Request(
            f"{base_url}/api/clearances/{cl_id}/update",
            data=json.dumps({
                "doc_number": "BC12-2026-009881",
                "status": "APPROVED",
                "remarks": "Disetujui CEISA Bea Cukai"
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req_upd_cl) as res:
            assert res.status == 200
            upd_res = json.loads(res.read().decode("utf-8"))
            assert upd_res["status"] == "success"
            # ADR-0016: tanpa berkas yang diunggah, tidak ada hash —
            # update tidak lagi membuat hash palsu dari nomor dokumen.
            assert upd_res["file_sha256"] is None

        with urllib.request.urlopen(f"{base_url}/api/port-calls/{pc_id}") as res:
            pdata = json.loads(res.read().decode("utf-8"))
            bc12_updated = next(c for c in pdata["clearances"] if c["id"] == cl_id)
            assert bc12_updated["doc_number"] == "BC12-2026-009881"
            assert bc12_updated["status"] == "APPROVED"
            assert bc12_updated["approval_time"] is not None
        print("[TEST 12/12] Update Nomor Dokumen & Status Clearance CIGP Berhasil.")

        # 13. Test SPB Gate: port call baru (semua clearance PENDING) -> SPB ditolak 400
        req_new = urllib.request.Request(
            f"{base_url}/api/port-calls",
            data=json.dumps({"vessel_imo": "9876543", "port_code": "IDTPP",
                             "terminal_name": "JICT (Jakarta Int. Container Terminal)",
                             "cargo_name": "Batu Bara", "cargo_tonnage": 50000,
                             "eta": "2026-11-10 08:00", "vessel_category": "FOREIGN"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req_new) as res:
            assert res.status == 200
            new_pc = json.loads(res.read().decode("utf-8"))["port_call_id"]
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{new_pc}") as res:
            det = json.loads(res.read().decode("utf-8"))
            assert len(det["clearances"]) == 14, f"Template baru harus 14 item, dapat {len(det['clearances'])}"
            assert any(c["doc_type"].startswith("PKKA") for c in det["clearances"]), "Kapal asing harus pakai PKKA"
        req_spb_bad = urllib.request.Request(
            f"{base_url}/api/port-calls/{new_pc}/spb",
            data=json.dumps({"doc_number": "SPB.TEST.001", "issued_at": "2026-11-10 10:00:00"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        try:
            urllib.request.urlopen(req_spb_bad)
            assert False, "SPB harus ditolak bila prasyarat PENDING"
        except urllib.error.HTTPError as e:
            assert e.code == 400
            err = json.loads(e.read().decode("utf-8"))
            assert err["blockers"], "Respons 400 SPB harus berisi daftar blocker"
            assert any("Free Pratique" in b for b in err["blockers"])
        print("[TEST 13/15] SPB Gate: ditolak 400 saat Free Pratique PENDING.")

        # 13b. Setujui semua clearance template (kecuali SPB) -> SPB lolos
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{new_pc}") as res:
            det = json.loads(res.read().decode("utf-8"))
        for cl in det["clearances"]:
            if "SPB" in cl["doc_type"]:
                continue
            req_u = urllib.request.Request(
                f"{base_url}/api/clearances/{cl['id']}/update",
                data=json.dumps({"doc_number": f"DOC-{cl['id']}-2026", "status": "APPROVED",
                                 "remarks": "Disetujui di portal resmi"}).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req_u) as res:
                assert res.status == 200
        with urllib.request.urlopen(req_spb_bad) as res:
            assert res.status == 200
            spb2 = json.loads(res.read().decode("utf-8"))
            assert spb2["spb_expiry_at"] == "2026-11-11 10:00:00"
        print("[TEST 13b/15] SPB Gate: lolos setelah semua prasyarat APPROVED.")

        # 14. Test GET clearance-checklist + POST master vessel + POST disbursement
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{new_pc}/clearance-checklist") as res:
            assert res.status == 200
            chk = json.loads(res.read().decode("utf-8"))
            assert chk["can_issue_spb"] is True
            assert len(chk["clearance_in"]) == 8 and len(chk["clearance_out"]) == 6
            rksp = next(i for i in chk["items"] if i["category"] == "rksp")
            assert rksp["deadline"] == "2026-11-09 08:00", f"RKSP deadline harus ETA-24 jam, dapat {rksp['deadline']}"
        # IMO uji harus unik per run (DB dipakai ulang antar run tes).
        with urllib.request.urlopen(f"{base_url}/api/master/ports") as res:
            existing_imos = {v["imo"] for v in json.loads(res.read().decode("utf-8"))["vessels"]}
        # Pool 4 nilai awal habis setelah 4 run; bila habis, pakai IMO
        # dari timestamp (unik per detik) supaya suite bisa diulang
        # tanpa batas tanpa mengotori DB dengan IMO yang sama.
        test_imo = next((i for i in ("9999999", "9999998", "9999997", "9999996") if i not in existing_imos), None)
        if test_imo is None:
            test_imo = "9" + str(int(time.time()))[-6:]
            assert test_imo not in existing_imos, "IMO timestamp bentrok — jalankan ulang tes"
        req_vessel = urllib.request.Request(
            f"{base_url}/api/master/vessels",
            data=json.dumps({"imo": test_imo, "name": "MV. TEST BARU", "flag": "ID",
                             "vessel_type": "Bulk Carrier", "grt": 12000, "dwt": 20000,
                             "loa": 150.0, "max_draft": 9.5}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req_vessel) as res:
            assert res.status == 200
        try:
            urllib.request.urlopen(req_vessel)
            assert False, "IMO duplikat harus ditolak"
        except urllib.error.HTTPError as e:
            assert e.code == 400
        req_dis = urllib.request.Request(
            f"{base_url}/api/port-calls/{new_pc}/disbursements",
            data=json.dumps({"category": "HUSBANDRY", "item_name": "Bunkering 100 MT",
                             "pda_estimate": 50000000, "fda_actual": 0}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req_dis) as res:
            assert res.status == 200
            assert json.loads(res.read().decode("utf-8"))["disbursement_id"]
        req_dis_bad = urllib.request.Request(
            f"{base_url}/api/port-calls/{new_pc}/disbursements",
            data=json.dumps({"category": "TIDAK_ADA", "item_name": "X"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        try:
            urllib.request.urlopen(req_dis_bad)
            assert False, "Kategori disbursement tidak valid harus ditolak 400"
        except urllib.error.HTTPError as e:
            assert e.code == 400
        print("[TEST 14/15] Clearance-checklist, POST master vessel, POST disbursement Berhasil.")

        # 15. Test audit_logs tercatat (SPB + clearance update + close)
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{new_pc}/audit-logs") as res:
            assert res.status == 200
            logs = json.loads(res.read().decode("utf-8"))["audit_logs"]
            actions = [l["action"] for l in logs]
            assert "SPB_ISSUED" in actions, f"Audit harus berisi SPB_ISSUED, dapat {actions}"
            assert any(a.startswith("CLEARANCE_UPDATE") for a in actions)
        print(f"[TEST 15/15] Audit Logs tercatat ({len(logs)} entri untuk {new_pc}).")

        # 16. Test Close-Ops Gate + Milestone DEPARTED Gate (PM 28/2022)
        def _post(path, payload):
            return urllib.request.Request(
                f"{base_url}{path}",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )

        def _expect_400(path, payload, frag):
            try:
                urllib.request.urlopen(_post(path, payload))
                assert False, f"{path} harus ditolak 400 ({frag})"
            except urllib.error.HTTPError as e:
                assert e.code == 400
                body = e.read().decode("utf-8")
                assert frag in body, f"Respons 400 harus menyebut '{frag}', dapat {body}"

        # 16a. new_pc: SPB sudah terbit tapi status masih PRE_ARRIVAL
        # -> close-ops ditolak 400 (belum DEPARTED).
        _expect_400(f"/api/port-calls/{new_pc}/close-ops", {"user": "Capt. Hadi"}, "DEPARTED")
        print("[TEST 16a/16] Close-Ops Gate: ditolak 400 saat kapal belum DEPARTED.")

        # 16b. Terbitkan ulang SPB dengan issued_at = sekarang (agar tidak
        # kedaluwarsa bila tes dijalankan sesudah 2026-11-11), lalu SAILED
        # -> status DEPARTED dan atd terisi dari event_time. SPB dari Tes
        # 13b (SPB.TEST.001, terbit 2026-11-10) masih berlaku sampai
        # 2026-11-11 — terbit ulang hanya sah bila SPB lama kedaluwarsa
        # (ADR-0017), jadi buat kedaluwarsa dulu lewat DB (setup tes).
        now2 = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = get_db()
        with conn:
            conn.execute("UPDATE port_calls SET spb_expiry_at = '2026-01-01 00:00:00' WHERE id = ?", (new_pc,))
        conn.close()
        with urllib.request.urlopen(_post(f"/api/port-calls/{new_pc}/spb",
                {"doc_number": "SPB.TEST.002", "issued_at": now2})) as res:
            assert res.status == 200
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{new_pc}") as res:
            det = json.loads(res.read().decode("utf-8"))
            assert any(h["doc_number"] == "SPB.TEST.001" for h in det.get("spb_history", [])), \
                "SPB lama (SPB.TEST.001) harus tercatat di spb_history setelah diterbitkan ulang"
        with urllib.request.urlopen(_post(f"/api/port-calls/{new_pc}/milestone",
                {"milestone_code": "SAILED", "event_time": now2, "remarks": "Berangkat"})) as res:
            assert res.status == 200
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{new_pc}") as res:
            det = json.loads(res.read().decode("utf-8"))
            assert det["status"] == "DEPARTED"
            assert det["atd"] == now2
        print("[TEST 16b/16] Milestone SAILED dengan SPB valid: DEPARTED + atd terisi.")

        # 16c. Setelah DEPARTED, close-ops lolos.
        with urllib.request.urlopen(_post(f"/api/port-calls/{new_pc}/close-ops",
                {"user": "Capt. Hadi"})) as res:
            assert res.status == 200
        print("[TEST 16c/16] Close-Ops Gate: lolos setelah SPB valid + DEPARTED.")

        # 16d. Port call baru tanpa SPB: close-ops ditolak; SAILED dicatat
        # sebagai fakta tapi status tetap PRE_ARRIVAL (tidak jadi DEPARTED).
        with urllib.request.urlopen(_post("/api/port-calls",
                {"vessel_imo": "9876543", "port_code": "IDTPP",
                 "terminal_name": "JICT (Jakarta Int. Container Terminal)",
                 "cargo_name": "Batu Bara", "cargo_tonnage": 50000,
                 "eta": "2026-11-12 08:00", "vessel_category": "FOREIGN"})) as res:
            assert res.status == 200
            pc2 = json.loads(res.read().decode("utf-8"))["port_call_id"]
        _expect_400(f"/api/port-calls/{pc2}/close-ops", {"user": "Capt. Hadi"}, "SPB belum terbit")
        _expect_400(f"/api/port-calls/{pc2}/milestone",
                    {"milestone_code": "SAILED", "event_time": now2, "remarks": "Coba berangkat tanpa SPB"},
                    "SPB belum terbit")
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{pc2}") as res:
            det2 = json.loads(res.read().decode("utf-8"))
            assert det2["status"] == "PRE_ARRIVAL", f"Status harus tetap PRE_ARRIVAL, dapat {det2['status']}"
            assert any(m["milestone_code"] == "SAILED" for m in det2["milestones"]), "Milestone SAILED harus tetap tercatat sebagai fakta"
            assert det2["atd"] is None, "atd harus tetap kosong bila belum DEPARTED"
        print("[TEST 16d/16] Tanpa SPB: close-ops + DEPARTED ditolak, milestone tetap tercatat.")

        # 16e. SPB kedaluwarsa (terbit 2026-10-29, lewat 24 jam): SAILED dan
        # close-ops sama-sama ditolak.
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{pc2}") as res:
            det2 = json.loads(res.read().decode("utf-8"))
        for cl in det2["clearances"]:
            if "SPB" in cl["doc_type"]:
                continue
            with urllib.request.urlopen(_post(f"/api/clearances/{cl['id']}/update",
                    {"doc_number": f"DOC-{cl['id']}-2026", "status": "APPROVED",
                     "remarks": "Disetujui di portal resmi"})) as res:
                assert res.status == 200
        with urllib.request.urlopen(_post(f"/api/port-calls/{pc2}/spb",
                {"doc_number": "SPB.TEST.003", "issued_at": "2026-10-29 10:00:00"})) as res:
            assert res.status == 200
            assert json.loads(res.read().decode("utf-8"))["spb_expiry_at"] == "2026-10-30 10:00:00"
        _expect_400(f"/api/port-calls/{pc2}/milestone",
                    {"milestone_code": "SAILED", "event_time": now2, "remarks": "Coba berangkat dengan SPB kedaluwarsa"},
                    "kedaluwarsa")
        _expect_400(f"/api/port-calls/{pc2}/close-ops", {"user": "Capt. Hadi"}, "kedaluwarsa")
        print("[TEST 16e/16] SPB kedaluwarsa: SAILED + close-ops ditolak 400.")

        # 18. Test Gate Role: close-ops oleh FINANCE -> 403; close-finance
        # oleh OPS -> 403. (pc2 dipakai untuk close-ops: gate role dicek
        # sebelum gate SPB, jadi 403 walau SPB-nya kedaluwarsa. new_pc sudah
        # CLEARANCE_CLOSED — close-finance oleh OPS ditolak role-nya.)
        def _expect_status(opener, path, payload, want, frag):
            try:
                opener.open(_post(path, payload))
                assert False, f"{path} harus ditolak {want} ({frag})"
            except urllib.error.HTTPError as e:
                assert e.code == want, f"{path} harus {want}, dapat {e.code}"
                body = e.read().decode("utf-8")
                assert frag in body, f"Respons {want} harus menyebut '{frag}', dapat {body}"
        _expect_status(fin_opener, f"/api/port-calls/{pc2}/close-ops",
                       {"user": "Finance Mgr"}, 403, "OPS atau ADMIN")
        _expect_status(ops_opener, f"/api/port-calls/{new_pc}/close-finance",
                       {"user": "Capt. Hadi"}, 403, "FINANCE atau ADMIN")
        print("[TEST 18/22] Gate Role: close-ops FINANCE -> 403, close-finance OPS -> 403.")

        # 19. Test Audit mengambil user dari SESSION, bukan payload.
        # close-ops 16c dikirim dengan payload user "Capt. Hadi" tapi cookie
        # OPS (ops_tes) — audit harus mencatat ops_tes, bukan Capt. Hadi.
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{new_pc}/audit-logs") as res:
            logs2 = json.loads(res.read().decode("utf-8"))["audit_logs"]
        close_ops_entries = [l for l in logs2 if l["action"] == "CLOSE_OPS"]
        assert close_ops_entries, "Audit harus berisi CLOSE_OPS"
        assert all(l["user_id"] == "ops_tes" for l in close_ops_entries), \
            f"CLOSE_OPS harus dicatat atas ops_tes, dapat {[l['user_id'] for l in close_ops_entries]}"
        assert not any(l["user_id"] == "Capt. Hadi" for l in logs2), \
            "Tidak boleh ada audit atas nama dari payload client"
        print("[TEST 19/22] Audit user_id dari session (ops_tes), bukan payload client.")

        # 20. Test Rate Limit Login: 5x gagal -> percobaan ke-6 ditolak 429.
        req_brute = urllib.request.Request(
            f"{base_url}/api/login",
            data=json.dumps({"username": "brute_tes", "password": "salah"}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        for i in range(5):
            try:
                urllib.request.urlopen(req_brute)
                assert False, "Login brute_tes harus 401"
            except urllib.error.HTTPError as e:
                assert e.code == 401, f"Percobaan {i+1} harus 401, dapat {e.code}"
        try:
            urllib.request.urlopen(req_brute)
            assert False, "Percobaan login ke-6 harus 429"
        except urllib.error.HTTPError as e:
            assert e.code == 429, f"Percobaan ke-6 harus 429, dapat {e.code}"
        print("[TEST 20/22] Rate Limit Login: 5x 401 lalu 429 pada percobaan ke-6.")

        # 21. Test Logout: setelah logout, API menolak 401 (cookie dihapus).
        req_logout = urllib.request.Request(
            f"{base_url}/api/logout",
            data=b"{}",
            headers={"Content-Type": "application/json"})
        with ops_opener.open(req_logout) as res:
            assert res.status == 200
        try:
            urllib.request.urlopen(f"{base_url}/api/port-calls")
            assert False, "API setelah logout harus 401"
        except urllib.error.HTTPError as e:
            assert e.code == 401
        print("[TEST 21/22] Logout: cookie dihapus, API menolak 401 sesudahnya.")

        # 22. Test pesan error: penolakan harus menjelaskan alasannya
        # (bukan sekadar 400) — close-ops tanpa SPB menyebut alasannya,
        # dispute dengan nilai bukan angka dijawab 400 JSON (bukan 500).
        # (Login ulang: tes 21 sudah logout, opener terpasang tak lagi
        # punya cookie.)
        ops2 = _login_opener("ops_tes", "ops-pass-123")
        urllib.request.install_opener(ops2)
        _expect_400(f"/api/port-calls/{pc2}/close-ops", {"user": "Capt. Hadi"}, "kedaluwarsa")
        try:
            urllib.request.urlopen(_post(f"/api/port-calls/{pc2}/dispute",
                                         {"disbursement_id": 1, "disputed_amount": "bukan-angka", "reason": "tes"}))
            assert False, "Dispute nilai bukan angka harus ditolak 400"
        except urllib.error.HTTPError as e:
            assert e.code == 400, f"Dispute non-angka harus 400, dapat {e.code}"
            body = json.loads(e.read().decode("utf-8"))
            assert "angka" in body.get("error", ""), f"Respons 400 dispute harus menjelaskan, dapat {body}"
        print("[TEST 22/22] Pesan error: close-ops menyebut alasan, dispute non-angka 400 JSON.")

        # 23. Test celah server dari audit pesan error (ADR-0015):
        # close-ops menyebut dokumen yang belum APPROVED; laytime waktu
        # salah jadi 400 (bukan 500); dispute menolak item milik port
        # call lain; stoppage menolak input rusak dengan 400/404.
        with urllib.request.urlopen(_post("/api/port-calls",
                {"vessel_imo": "9876543", "port_code": "IDTPP",
                 "terminal_name": "JICT (Jakarta Int. Container Terminal)",
                 "cargo_name": "Batu Bara", "cargo_tonnage": 50000,
                 "eta": "2026-11-12 08:00", "vessel_category": "FOREIGN"})) as res:
            assert res.status == 200
            pc3 = json.loads(res.read().decode("utf-8"))["port_call_id"]
        try:
            urllib.request.urlopen(_post(f"/api/port-calls/{pc3}/close-ops", {"user": "Capt. Hadi"}))
            assert False, "close-ops pc3 tanpa SPB harus ditolak 400"
        except urllib.error.HTTPError as e:
            assert e.code == 400
            body = json.loads(e.read().decode("utf-8"))
            assert body.get("blockers"), f"Respons 400 close-ops harus berisi daftar blockers, dapat {body}"
            assert any("Free Pratique" in b for b in body["blockers"]), f"Blockers harus menyebut Free Pratique, dapat {body['blockers']}"
        _expect_400("/api/port-calls/PC-2026-JKT-0001/laytime/calculate",
                    {"nor_tendered_time": "12-11-2026 08:00", "commenced_time": "2026-11-12 14:00",
                     "completed_time": "2026-11-13 14:00"}, "Waktu tidak valid")
        with urllib.request.urlopen(f"{base_url}/api/port-calls/PC-2026-JKT-0001") as res:
            det_jkt = json.loads(res.read().decode("utf-8"))
        dis_lain = det_jkt["disbursements"][0]["id"]
        _expect_400(f"/api/port-calls/{pc3}/dispute",
                    {"disbursement_id": dis_lain, "disputed_amount": 1000000, "reason": "tes"},
                    "tidak ditemukan pada port call ini")
        _expect_400(f"/api/port-calls/{pc3}/stoppage",
                    {"category": "RAIN", "start_time": "2026-11-12 10:00", "end_time": "2026-11-12 12:00",
                     "percentage_counted": "rusak", "remarks": "tes"}, "harus angka")
        _expect_400(f"/api/port-calls/{pc3}/stoppage",
                    {"category": "RAIN", "start_time": "12 Nov 2026", "end_time": "2026-11-12 12:00",
                     "percentage_counted": 0, "remarks": "tes"}, "tidak valid")
        _expect_400(f"/api/port-calls/{pc3}/stoppage",
                    {"category": "HUJAN", "start_time": "2026-11-12 10:00", "end_time": "2026-11-12 12:00",
                     "percentage_counted": 0, "remarks": "tes"}, "tidak dikenal")
        _expect_status(ops2, "/api/port-calls/PC-TIDAK-ADA/stoppage",
                       {"category": "RAIN", "start_time": "2026-11-12 10:00", "end_time": "2026-11-12 12:00",
                        "percentage_counted": 0, "remarks": "tes"}, 404, "tidak ditemukan")
        print("[TEST 23/23] ADR-0015: blockers close-ops, laytime 400, dispute milik port call, stoppage tervalidasi.")

        # 24. ID port call anti-bentrok (ADR-0016): MAX+1 + retry.
        # Nomor berurutan: suffix berikutnya = MAX(suffix)+1 (semua pelabuhan,
        # tahun yang sama — lingkup yang sama dengan SELECT lama).
        with urllib.request.urlopen(f"{base_url}/api/port-calls") as res:
            pcs = json.loads(res.read().decode("utf-8"))["port_calls"]
        max_suffix = max(int(p["id"].rsplit("-", 1)[1]) for p in pcs
                         if p["id"].startswith("PC-2026-"))
        with urllib.request.urlopen(_post("/api/port-calls",
                {"vessel_imo": "9876543", "port_code": "IDTPP",
                 "terminal_name": "JICT (Jakarta Int. Container Terminal)",
                 "cargo_name": "Batu Bara", "cargo_tonnage": 50000,
                 "eta": "2026-11-12 08:00", "vessel_category": "FOREIGN"})) as res:
            assert res.status == 200
            pc_seq = json.loads(res.read().decode("utf-8"))["port_call_id"]
        assert int(pc_seq.rsplit("-", 1)[1]) == max_suffix + 1, \
            f"ID baru harus MAX+1 ({max_suffix + 1}), dapat {pc_seq}"
        # 5 pembuatan bersamaan: semuanya harus 200 dengan ID berbeda —
        # tidak ada yang gagal 500 karena bentrok nomor.
        created_ids = []
        errors = []

        def _create_one():
            try:
                op = _login_opener("ops_tes", "ops-pass-123")
                with op.open(_post("/api/port-calls",
                        {"vessel_imo": "9876543", "port_code": "IDTPP",
                         "terminal_name": "JICT (Jakarta Int. Container Terminal)",
                         "cargo_name": "Batu Bara", "cargo_tonnage": 50000,
                         "eta": "2026-11-12 08:00", "vessel_category": "FOREIGN"})) as res:
                    created_ids.append(json.loads(res.read().decode("utf-8"))["port_call_id"])
            except Exception as exc:  # noqa: BLE001 — dikumpulkan, lalu di-assert
                errors.append(exc)

        threads = [threading.Thread(target=_create_one) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, f"Pembuatan bersamaan gagal: {errors}"
        assert len(set(created_ids)) == 5, f"ID harus 5 berbeda, dapat {created_ids}"
        print("[TEST 24/24] ADR-0016: ID port call MAX+1 + 5x pembuatan bersamaan tanpa bentrok.")

        # 25. Hash jujur (ADR-0016): tanpa berkas yang diunggah, tidak ada
        # hash — clearance baru & update tidak lagi membuat hash palsu.
        with urllib.request.urlopen(_post(f"/api/port-calls/{pc3}/clearance",
                {"agency": "BEA_CUKAI", "doc_type": "Dokumen Tes Hash",
                 "doc_number": "TES-HASH-001", "status": "APPROVED",
                 "remarks": "tes"})) as res:
            assert res.status == 200
            assert json.loads(res.read().decode("utf-8"))["file_sha256"] is None
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{pc3}") as res:
            det3 = json.loads(res.read().decode("utf-8"))
        cl_hash = next(c for c in det3["clearances"] if c["doc_number"] == "TES-HASH-001")
        assert cl_hash["file_sha256"] is None and cl_hash["file_name"] is None
        with urllib.request.urlopen(_post(f"/api/clearances/{cl_hash['id']}/update",
                {"doc_number": "TES-HASH-002", "status": "APPROVED"})) as res:
            assert res.status == 200
            assert json.loads(res.read().decode("utf-8"))["file_sha256"] is None
        # Seed juga sudah bersih: semua clearance contoh tanpa hash/berkas.
        with urllib.request.urlopen(f"{base_url}/api/port-calls/PC-2026-JKT-0001") as res:
            det_jkt2 = json.loads(res.read().decode("utf-8"))
        assert all(c["file_sha256"] is None for c in det_jkt2["clearances"]), \
            "Seed clearance tidak boleh lagi membawa hash palsu"
        print("[TEST 25/25] ADR-0016: clearance tanpa berkas -> file_sha256/file_name NULL.")

        # 26. Rate limit per IP, batas body, cargo_tonnage (ADR-0016).
        def _login_req(username, password, xff=None):
            headers = {"Content-Type": "application/json"}
            if xff:
                headers["X-Forwarded-For"] = xff
            return urllib.request.Request(
                f"{base_url}/api/login",
                data=json.dumps({"username": username, "password": password}).encode("utf-8"),
                headers=headers)

        # 5x gagal dari IP 203.0.113.1 -> percobaan ke-6 dari IP yang sama 429...
        for _ in range(5):
            try:
                urllib.request.urlopen(_login_req("ops_tes", "salah", "203.0.113.1"))
                assert False, "Login salah harus 401"
            except urllib.error.HTTPError as e:
                assert e.code == 401
        try:
            urllib.request.urlopen(_login_req("ops_tes", "salah", "203.0.113.1"))
            assert False, "Percobaan ke-6 dari IP yang sama harus 429"
        except urllib.error.HTTPError as e:
            assert e.code == 429
        # ...tapi login benar dari IP lain tetap lolos: akun tidak terkunci.
        with urllib.request.urlopen(_login_req("ops_tes", "ops-pass-123", "198.51.100.7")) as res:
            assert res.status == 200
        # cargo_tonnage non-angka -> 400, bukan 500.
        _expect_400("/api/port-calls",
                    {"vessel_imo": "9876543", "port_code": "IDTPP",
                     "cargo_tonnage": "rusak"}, "harus angka")
        # Body > 1 MB -> 413, SETIAP kali (bukan kadang Broken pipe).
        # Dulu server menjawab 413 TANPA membaca body: koneksi ditutup
        # dengan body belum terbaca -> kernel RST -> client yang masih
        # mengunggah mendapat URLError (terbukti 15/30 di probe lokal;
        # Tes 26 gagal dengan cara yang sama di CI). Kini server membuang
        # body dulu sebelum 413. Diulang 20x karena racenya ~50%: sekali
        # lolos tidak membuktikan apa-apa, 20x beruntun iya.
        for _ in range(20):
            try:
                urllib.request.urlopen(_login_req("x" * 1_100_000, "y"))
                assert False, "Body > 1 MB harus 413"
            except urllib.error.HTTPError as e:
                assert e.code == 413, f"Body besar harus 413, dapat {e.code}"
                assert "terlalu besar" in e.read().decode("utf-8")
        # Username mengandung '|' ditolak di create_user (merusak cookie).
        conn = get_db()
        try:
            create_user(conn, "ops|jakarta", "ops-pass-123", "Ops Pipa", "OPS")
            assert False, "Username '|' harus ditolak"
        except ValueError as exc:
            assert "'|'" in str(exc)
        finally:
            conn.close()
        print("[TEST 26/26] ADR-0016: rate-limit per IP, body 413, cargo_tonnage 400, username '|' ditolak.")

        # 27. SPB terbit-ulang & histori (ADR-0017): SPB yang masih
        # berlaku ditolak diterbitkan ulang; bila sudah kedaluwarsa,
        # terbit ulang sah dan nomor lama masuk spb_history. Terbit SPB
        # juga dibatasi role OPS/ADMIN.
        with urllib.request.urlopen(_post("/api/port-calls",
                {"vessel_imo": "9876543", "port_code": "IDTPP",
                 "terminal_name": "JICT (Jakarta Int. Container Terminal)",
                 "cargo_name": "Batu Bara", "cargo_tonnage": 50000,
                 "eta": "2026-11-12 08:00", "vessel_category": "FOREIGN"})) as res:
            assert res.status == 200
            pc27 = json.loads(res.read().decode("utf-8"))["port_call_id"]
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{pc27}") as res:
            det27 = json.loads(res.read().decode("utf-8"))
        for cl in det27["clearances"]:
            if "SPB" in cl["doc_type"]:
                continue
            with urllib.request.urlopen(_post(f"/api/clearances/{cl['id']}/update",
                    {"doc_number": f"DOC-{cl['id']}-2026", "status": "APPROVED"})) as res:
                assert res.status == 200
        # FINANCE tidak boleh menerbitkan SPB.
        _expect_status(fin_opener, f"/api/port-calls/{pc27}/spb",
                       {"doc_number": "SPB.FIN.001", "issued_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                       403, "OPS atau ADMIN")
        # Terbitkan SPB yang sudah kedaluwarsa (terbit 25 jam lalu).
        old_issue = (datetime.now() - timedelta(hours=25)).strftime("%Y-%m-%d %H:%M:%S")
        with urllib.request.urlopen(_post(f"/api/port-calls/{pc27}/spb",
                {"doc_number": "SPB.LAMA.001", "issued_at": old_issue})) as res:
            assert res.status == 200
        # Terbitkan ulang: sah, nomor lama masuk histori.
        now27 = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with urllib.request.urlopen(_post(f"/api/port-calls/{pc27}/spb",
                {"doc_number": "SPB.BARU.001", "issued_at": now27})) as res:
            assert res.status == 200
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{pc27}") as res:
            det27 = json.loads(res.read().decode("utf-8"))
            assert len(det27["spb_history"]) == 1
            assert det27["spb_history"][0]["doc_number"] == "SPB.LAMA.001"
            assert det27["spb_history"][0]["issued_at"] == old_issue
        # Terbitkan ulang lagi selagi masih berlaku -> 400.
        _expect_400(f"/api/port-calls/{pc27}/spb",
                    {"doc_number": "SPB.KETIGA.001", "issued_at": now27}, "masih berlaku")
        print("[TEST 27/27] ADR-0017: SPB terbit-ulang ditolak bila masih berlaku, histori nomor lama tersimpan, role OPS/ADMIN.")

        # 28. Status hanya maju + kunci CLOSED (ADR-0017).
        # new_pc sudah CLEARANCE_CLOSED (Tes 16c): EOSP telat tercatat
        # tapi status tetap CLEARANCE_CLOSED, dengan catatan di respons.
        with urllib.request.urlopen(_post(f"/api/port-calls/{new_pc}/milestone",
                {"milestone_code": "EOSP", "event_time": "2026-11-12 08:00", "remarks": "EOSP telat dicatat"})) as res:
            assert res.status == 200
            ms28 = json.loads(res.read().decode("utf-8"))
            assert ms28.get("note") and "tetap CLEARANCE_CLOSED" in ms28["note"]
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{new_pc}") as res:
            assert json.loads(res.read().decode("utf-8"))["status"] == "CLEARANCE_CLOSED"
        # Milestone waktu rusak -> 400.
        _expect_400(f"/api/port-calls/{pc3}/milestone",
                    {"milestone_code": "EOSP", "event_time": "12-11-2026 08:00"}, "tidak valid")
        # Stoppage selesai sebelum mulai -> 400.
        _expect_400(f"/api/port-calls/{pc3}/stoppage",
                    {"category": "RAIN", "start_time": "2026-11-12 12:00", "end_time": "2026-11-12 10:00",
                     "percentage_counted": 0, "remarks": "tes"}, "sesudah waktu mulai")
        # Tutup pc2 sampai CLOSED. Sengketa dibuat dulu lalu dibawa ke
        # UNDER_REVIEW sebelum close-finance, supaya pengecualian
        # resolusi sengketa setelah CLOSED bisa diuji (sengketa OPEN
        # akan menghalangi close-finance — lihat Tes 31). FDA baris ini
        # diisi penuh sejak awal supaya close-finance tidak terhalang
        # blocker FDA 0 (Tes 31 menguji blocker itu).
        with urllib.request.urlopen(_post(f"/api/port-calls/{pc2}/disbursements",
                {"category": "HUSBANDRY", "item_name": "Air Tawar", "pda_estimate": 1000000, "fda_actual": 1000000})) as res:
            assert res.status == 200
            dis28 = json.loads(res.read().decode("utf-8"))["disbursement_id"]
        with urllib.request.urlopen(_post(f"/api/port-calls/{pc2}/dispute",
                {"disbursement_id": dis28, "disputed_amount": 500000, "reason": "tarif air melebihi PDA"})) as res:
            assert res.status == 200
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{pc2}") as res:
            det28 = json.loads(res.read().decode("utf-8"))
            dp28 = det28["disputes"][0]["id"]
        with fin_opener.open(_post(f"/api/disputes/{dp28}/resolve",
                {"status": "UNDER_REVIEW", "resolution_notes": "Ditinjau finance"})) as res:
            assert res.status == 200
        with urllib.request.urlopen(_post(f"/api/port-calls/{pc2}/spb",
                {"doc_number": "SPB.PC2.001", "issued_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})) as res:
            assert res.status == 200
        with urllib.request.urlopen(_post(f"/api/port-calls/{pc2}/milestone",
                {"milestone_code": "SAILED", "event_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "remarks": "Berangkat"})) as res:
            assert res.status == 200
        with urllib.request.urlopen(_post(f"/api/port-calls/{pc2}/close-ops", {"user": "Capt. Hadi"})) as res:
            assert res.status == 200
        with fin_opener.open(_post(f"/api/port-calls/{pc2}/close-finance", {"user": "Finance Mgr"})) as res:
            assert res.status == 200
        # Setelah CLOSED: milestone, clearance, biaya, SPB, sengketa baru,
        # dan close-finance kedua ditolak dengan kalimat yang menyebut
        # siapa menutup dan kapan.
        _expect_400(f"/api/port-calls/{pc2}/milestone",
                    {"milestone_code": "EOSP", "event_time": "2026-11-12 08:00"}, "sudah CLOSED oleh fin_tes")
        _expect_400(f"/api/port-calls/{pc2}/clearance",
                    {"agency": "BEA_CUKAI", "doc_type": "Dokumen Telat", "doc_number": "X", "status": "APPROVED"}, "sudah CLOSED")
        _expect_400(f"/api/port-calls/{pc2}/disbursements",
                    {"category": "HUSBANDRY", "item_name": "Telat", "pda_estimate": 1}, "sudah CLOSED")
        _expect_400(f"/api/port-calls/{pc2}/spb",
                    {"doc_number": "SPB.TELAT.001", "issued_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}, "sudah CLOSED")
        _expect_400(f"/api/port-calls/{pc2}/dispute",
                    {"disbursement_id": dis28, "disputed_amount": 100, "reason": "telat"}, "sudah CLOSED")
        _expect_status(fin_opener, f"/api/port-calls/{pc2}/close-finance",
                       {"user": "Finance Mgr"}, 400, "sudah CLOSED")
        # Pengecualian: finance BOLEH menyelesaikan sengketa yang sudah
        # terbuka (di sini UNDER_REVIEW) meski port call sudah CLOSED;
        # OPS tidak boleh. (ops2 = opener OPS yang login ulang di Tes 22;
        # ops_opener sudah logout di Tes 21.)
        _expect_status(ops2, f"/api/disputes/{dp28}/resolve",
                       {"status": "ACCEPTED_REVISED", "resolution_notes": "tes"}, 403, "FINANCE atau ADMIN")
        with fin_opener.open(_post(f"/api/disputes/{dp28}/resolve",
                {"status": "ACCEPTED_REVISED", "resolution_notes": "Disetujui setengah tarif"})) as res:
            assert res.status == 200
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{pc2}") as res:
            det28 = json.loads(res.read().decode("utf-8"))
            assert det28["disputes"][0]["status"] == "ACCEPTED_REVISED"
        print("[TEST 28/28] ADR-0017: status hanya maju, kunci CLOSED menyebut penutup, resolusi sengketa tetap boleh.")

        # 29. Koreksi milestone (ADR-0017): ubah waktu milestone yang
        # sudah ada tanpa mengubah status; waktu baru harus tetap di
        # antara tetangganya supaya urutan SOF tidak rusak.
        with urllib.request.urlopen(_post(f"/api/port-calls/{pc3}/milestone",
                {"milestone_code": "EOSP", "event_time": "2026-11-12 08:00", "remarks": "tiba"})) as res:
            assert res.status == 200
        with urllib.request.urlopen(_post(f"/api/port-calls/{pc3}/milestone",
                {"milestone_code": "ALL_FAST", "event_time": "2026-11-12 10:00", "remarks": "sandar"})) as res:
            assert res.status == 200
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{pc3}") as res:
            det29 = json.loads(res.read().decode("utf-8"))
            assert det29["status"] == "BERTHED"
            ms29 = next(m for m in det29["milestones"] if m["milestone_code"] == "ALL_FAST")
        # Koreksi 10:00 -> 09:00: lolos, status tetap BERTHED.
        with urllib.request.urlopen(_post(f"/api/milestones/{ms29['id']}",
                {"event_time": "2026-11-12 09:00", "remarks": "sandar (koreksi)"})) as res:
            assert res.status == 200
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{pc3}") as res:
            det29 = json.loads(res.read().decode("utf-8"))
            assert det29["status"] == "BERTHED"
            assert next(m for m in det29["milestones"] if m["id"] == ms29["id"])["event_time"] == "2026-11-12 09:00"
        # Koreksi ke sebelum EOSP (08:00) -> 400; format rusak -> 400.
        _expect_400(f"/api/milestones/{ms29['id']}", {"event_time": "2026-11-12 07:00"}, "lebih awal dari milestone sebelumnya")
        _expect_400(f"/api/milestones/{ms29['id']}", {"event_time": "12-11-2026 09:00"}, "tidak valid")
        print("[TEST 29/29] ADR-0017: koreksi waktu milestone tanpa mengubah status, urutan SOF dijaga.")

        # 30. Koreksi ETA/ETD/muatan (ADR-0017): tenggat RKSP (ETA-24
        # jam) dan permohonan SPB (ETD-4 jam) ikut hitung ulang; audit
        # mencatat nilai lama dan baru.
        _expect_status(fin_opener, f"/api/port-calls/{pc3}/update",
                       {"eta": "2026-11-13 08:00"}, 403, "OPS atau ADMIN")
        _expect_400(f"/api/port-calls/{pc3}/update",
                    {"eta": "2026-11-13 08:00", "etd": "2026-11-12 08:00"}, "lebih awal dari ETA")
        _expect_400(f"/api/port-calls/{pc3}/update",
                    {"cargo_tonnage": "rusak"}, "harus angka")
        with urllib.request.urlopen(_post(f"/api/port-calls/{pc3}/update",
                {"eta": "2026-11-13 08:00", "etd": "2026-11-16 18:00",
                 "cargo_name": "Batu Bara", "cargo_tonnage": 60000})) as res:
            assert res.status == 200
            upd30 = json.loads(res.read().decode("utf-8"))
            assert upd30["port_call"]["eta"] == "2026-11-13 08:00"
            assert upd30["port_call"]["etd"] == "2026-11-16 18:00"
            rksp30 = next(i for i in upd30["checklist"]["items"] if i["category"] == "rksp")
            assert rksp30["deadline"] == "2026-11-12 08:00", f"RKSP harus ETA-24 jam yang baru, dapat {rksp30['deadline']}"
        _expect_400(f"/api/port-calls/{pc3}/update",
                    {"eta": "2026-11-13 08:00", "etd": "2026-11-16 18:00",
                     "cargo_name": "Batu Bara", "cargo_tonnage": 60000}, "Tidak ada perubahan")
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{pc3}/audit-logs") as res:
            logs30 = json.loads(res.read().decode("utf-8"))["audit_logs"]
            assert any(l["action"] == "PORT_CALL_UPDATE" for l in logs30), "Audit harus berisi PORT_CALL_UPDATE"
        print("[TEST 30/30] ADR-0017: koreksi ETA/ETD/muatan, checklist hitung ulang, audit tercatat.")

        # 31. Lengkapi FDA + blocker close-finance (ADR-0017): FDA,
        # NTPN, dan nomor billing diubah pada baris yang sama (bukan
        # baris baru); close-finance ditolak bila ada sengketa OPEN
        # atau FDA masih 0 pada baris yang PDA-nya terisi.
        with urllib.request.urlopen(_post(f"/api/port-calls/{pc27}/disbursements",
                {"category": "HUSBANDRY", "item_name": "Bunkering", "pda_estimate": 50000000, "fda_actual": 0})) as res:
            assert res.status == 200
            dis31a = json.loads(res.read().decode("utf-8"))["disbursement_id"]
        with urllib.request.urlopen(_post(f"/api/disbursements/{dis31a}",
                {"fda_actual": 52000000, "ntpn": "NTPN123", "simponi_billing": "BILL456"})) as res:
            assert res.status == 200
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{pc27}") as res:
            det31 = json.loads(res.read().decode("utf-8"))
            d31 = next(d for d in det31["disbursements"] if d["id"] == dis31a)
            assert d31["fda_actual"] == 52000000 and d31["ntpn"] == "NTPN123" and d31["simponi_billing"] == "BILL456"
            assert d31["pda_estimate"] == 50000000, "PDA tidak boleh berubah saat melengkapi FDA"
        _expect_400(f"/api/disbursements/{dis31a}", {"fda_actual": "rusak"}, "harus angka")
        with urllib.request.urlopen(_post(f"/api/port-calls/{pc27}/disbursements",
                {"category": "HUSBANDRY", "item_name": "Air Tawar", "pda_estimate": 1000000, "fda_actual": 0})) as res:
            assert res.status == 200
            dis31b = json.loads(res.read().decode("utf-8"))["disbursement_id"]
        with urllib.request.urlopen(_post(f"/api/port-calls/{pc27}/dispute",
                {"disbursement_id": dis31a, "disputed_amount": 2000000, "reason": "tarif bunker melebihi PDA"})) as res:
            assert res.status == 200
        with urllib.request.urlopen(_post(f"/api/port-calls/{pc27}/milestone",
                {"milestone_code": "SAILED", "event_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "remarks": "Berangkat"})) as res:
            assert res.status == 200
        with urllib.request.urlopen(_post(f"/api/port-calls/{pc27}/close-ops", {"user": "Capt. Hadi"})) as res:
            assert res.status == 200
        try:
            fin_opener.open(_post(f"/api/port-calls/{pc27}/close-finance", {"user": "Finance Mgr"}))
            assert False, "close-finance harus ditolak bila ada sengketa OPEN / FDA 0"
        except urllib.error.HTTPError as e:
            assert e.code == 400
            body31 = json.loads(e.read().decode("utf-8"))
            assert body31.get("blockers"), "Respons 400 close-finance harus berisi daftar blockers"
            assert any("Sengketa OPEN: Bunkering" in b for b in body31["blockers"]), f"Blocker harus menyebut sengketa Bunkering, dapat {body31['blockers']}"
            assert any("FDA belum diisi: Air Tawar" in b for b in body31["blockers"]), f"Blocker harus menyebut FDA Air Tawar, dapat {body31['blockers']}"
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{pc27}") as res:
            dp31 = json.loads(res.read().decode("utf-8"))["disputes"][0]["id"]
        with fin_opener.open(_post(f"/api/disputes/{dp31}/resolve",
                {"status": "ACCEPTED_REVISED", "resolution_notes": "Disetujui sebagian"})) as res:
            assert res.status == 200
        with urllib.request.urlopen(_post(f"/api/disbursements/{dis31b}", {"fda_actual": 1100000})) as res:
            assert res.status == 200
        with fin_opener.open(_post(f"/api/port-calls/{pc27}/close-finance", {"user": "Finance Mgr"})) as res:
            assert res.status == 200
        print("[TEST 31/31] ADR-0017: lengkapi FDA di baris yang sama, close-finance diblokir sengketa OPEN & FDA 0.")

        # 32. Form fixture + simpan laytime (ADR-0017): parameter CP
        # bisa diubah dari recap; hitung laytime menyimpan hasil dan
        # NOR/mulai/selesai sebagai milestone; handover membawanya.
        _expect_status(fin_opener, f"/api/port-calls/{pc3}/charter-party",
                       {"loading_rate_per_day": 5000}, 403, "OPS atau ADMIN")
        _expect_400(f"/api/port-calls/{pc3}/charter-party",
                    {"laytime_terms": "WWD"}, "SHINC atau SHEX")
        _expect_400(f"/api/port-calls/{pc3}/charter-party",
                    {"loading_rate_per_day": 0}, "lebih dari 0")
        with urllib.request.urlopen(_post(f"/api/port-calls/{pc3}/charter-party",
                {"loading_rate_per_day": 5000, "laytime_terms": "SHEX",
                 "demurrage_usd_per_day": 30000, "despatch_usd_per_day": 15000,
                 "nor_notice_hours": 12})) as res:
            assert res.status == 200
            cp32 = json.loads(res.read().decode("utf-8"))["charter_party"]
            assert cp32["loading_rate_per_day"] == 5000 and cp32["laytime_terms"] == "SHEX"
        with urllib.request.urlopen(_post(f"/api/port-calls/{pc3}/laytime/calculate",
                {"nor_tendered_time": "2026-11-13 08:00", "commenced_time": "2026-11-13 14:00",
                 "completed_time": "2026-11-16 14:00"})) as res:
            assert res.status == 200
            lt32 = json.loads(res.read().decode("utf-8"))
            assert lt32.get("saved") is True and lt32.get("calculation_id")
            assert lt32["terms"] == "SHEX"
            assert lt32["sundays_excluded"]["count"] == 1, f"SHEX harus mengecualikan Minggu 15 Nov, dapat {lt32['sundays_excluded']}"
            assert set(lt32["milestones_saved"]) == {"NOR_TENDERED", "COMMENCED_CARGO", "COMPLETED_CARGO"}
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{pc3}") as res:
            det32 = json.loads(res.read().decode("utf-8"))
            assert det32["latest_laytime"] is not None
            assert det32["latest_laytime"]["terms"] == "SHEX"
            assert det32["latest_laytime"]["claim_type"] == lt32["result"]["claim_type"]
            ms32 = {m["milestone_code"]: m["event_time"] for m in det32["milestones"]}
            assert ms32.get("NOR_TENDERED") == "2026-11-13 08:00"
            assert ms32.get("COMMENCED_CARGO") == "2026-11-13 14:00"
            assert ms32.get("COMPLETED_CARGO") == "2026-11-16 14:00"
        # Hitung ulang dengan waktu yang sama: tidak ada milestone ganda.
        with urllib.request.urlopen(_post(f"/api/port-calls/{pc3}/laytime/calculate",
                {"nor_tendered_time": "2026-11-13 08:00", "commenced_time": "2026-11-13 14:00",
                 "completed_time": "2026-11-16 14:00"})) as res:
            assert res.status == 200
            assert json.loads(res.read().decode("utf-8"))["milestones_saved"] == []
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{pc3}") as res:
            det32 = json.loads(res.read().decode("utf-8"))
            assert sum(1 for m in det32["milestones"] if m["milestone_code"] == "NOR_TENDERED") == 1
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{pc3}/handover") as res:
            ho32 = json.loads(res.read().decode("utf-8"))["handover_text"]
            assert "Laytime Terakhir:" in ho32 and lt32["result"]["claim_type"] in ho32
        print("[TEST 32/32] ADR-0017: fixture CP tersimpan, laytime tersimpan + milestone, handover membawa hasil.")

        # 33. Papan shift (ADR-0017): ringkasan per port call — dokumen
        # lewat tenggat, SPB < 6 jam, sandar tapi SPB belum diterbitkan,
        # sengketa OPEN; port call CLOSED tidak masuk papan.
        with urllib.request.urlopen(_post("/api/port-calls",
                {"vessel_imo": "9876543", "port_code": "IDTPP",
                 "terminal_name": "JICT (Jakarta Int. Container Terminal)",
                 "cargo_name": "Batu Bara", "cargo_tonnage": 50000,
                 "eta": "2026-11-01 08:00", "vessel_category": "FOREIGN"})) as res:
            assert res.status == 200
            pc33a = json.loads(res.read().decode("utf-8"))["port_call_id"]
        with urllib.request.urlopen(_post("/api/port-calls",
                {"vessel_imo": "9876543", "port_code": "IDTPP",
                 "terminal_name": "JICT (Jakarta Int. Container Terminal)",
                 "cargo_name": "Batu Bara", "cargo_tonnage": 50000,
                 "eta": "2026-11-12 08:00", "vessel_category": "FOREIGN"})) as res:
            assert res.status == 200
            pc33b = json.loads(res.read().decode("utf-8"))["port_call_id"]
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{pc33b}") as res:
            det33 = json.loads(res.read().decode("utf-8"))
        for cl in det33["clearances"]:
            if "SPB" in cl["doc_type"]:
                continue
            with urllib.request.urlopen(_post(f"/api/clearances/{cl['id']}/update",
                    {"doc_number": f"DOC-{cl['id']}-2026", "status": "APPROVED"})) as res:
                assert res.status == 200
        # SPB terbit 19 jam lalu -> sisa ~5 jam (di bawah 6 jam).
        old33 = (datetime.now() - timedelta(hours=19)).strftime("%Y-%m-%d %H:%M:%S")
        with urllib.request.urlopen(_post(f"/api/port-calls/{pc33b}/spb",
                {"doc_number": "SPB.PAPAN.001", "issued_at": old33})) as res:
            assert res.status == 200
        # Sengketa OPEN di pc3 (BERTHED, tanpa SPB -> needs_spb).
        with urllib.request.urlopen(_post(f"/api/port-calls/{pc3}/disbursements",
                {"category": "HUSBANDRY", "item_name": "Tunda", "pda_estimate": 2000000, "fda_actual": 2000000})) as res:
            assert res.status == 200
            dis33 = json.loads(res.read().decode("utf-8"))["disbursement_id"]
        with urllib.request.urlopen(_post(f"/api/port-calls/{pc3}/dispute",
                {"disbursement_id": dis33, "disputed_amount": 300000, "reason": "tarif tunda"})) as res:
            assert res.status == 200
        with urllib.request.urlopen(f"{base_url}/api/shift-board") as res:
            assert res.status == 200
            board = json.loads(res.read().decode("utf-8"))
            cards = {c["id"]: c for c in board["cards"]}
            assert pc2 not in cards, "Port call CLOSED tidak boleh masuk papan shift"
            assert pc3 in cards and pc33a in cards and pc33b in cards
            # pc33a: ETA 1 Nov (lewat) + template SUBMITTED -> dokumen lewat tenggat.
            assert len(cards[pc33a]["overdue_docs"]) >= 1, f"pc33a harus punya dokumen lewat tenggat, dapat {cards[pc33a]}"
            # pc33b: SPB sisa ~5 jam.
            assert cards[pc33b]["spb_state"] == "under_6h", f"pc33b SPB harus under_6h, dapat {cards[pc33b]}"
            assert 0 < cards[pc33b]["spb_hours_left"] <= 6
            # pc3: BERTHED tanpa SPB + sengketa OPEN.
            assert cards[pc3]["needs_spb"] is True, f"pc3 BERTHED tanpa SPB harus needs_spb, dapat {cards[pc3]}"
            assert len(cards[pc3]["open_disputes"]) == 1
            assert board["totals"]["overdue_docs"] >= 1
            assert board["totals"]["spb_under_6h"] >= 1
            assert board["totals"]["needs_spb"] >= 1
            assert board["totals"]["open_disputes"] >= 1
        print("[TEST 33/33] ADR-0017: papan shift merangkum dokumen lewat tenggat, SPB < 6 jam, needs_spb, sengketa OPEN.")

        # -- TES 34 (ADR-0018): cold start Vercel melengkapi skema DB lama.
        # Regresi insiden 2 Nov 2026: setelah PR #11 di-merge, Vercel 500
        # "no such table: spb_history" dan detail port call tidak ter-load.
        # Penyebab: _prepare_database_once() hanya COUNT master_ports +
        # port_calls — bila keduanya > 0, init_db() tidak pernah jalan,
        # sehingga tabel baru ADR-0017 tidak pernah dibuat di Turso yang
        # sudah ada (server lokal lolos karena run_server() selalu init_db).
        # Tes ini mensimulasikan DB Turso lama: skema penuh + seed, lalu
        # dua tabel baru di-DROP; jalur cold start Vercel harus membuatnya
        # lagi tanpa menyentuh data yang sudah ada.
        from server import _prepare_database_once
        from engine.db import TABLES
        old_db_file = os.environ["AGENCY_DB_FILE"]
        legacy_db = os.path.join(_TEST_TMPDIR, "agency-legacy.db")
        try:
            os.environ["AGENCY_DB_FILE"] = legacy_db
            init_db()
            seed_sample_data()
            conn = get_db()
            with conn:
                conn.execute("DROP TABLE spb_history")
                conn.execute("DROP TABLE laytime_calculations")
            have_before = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'")}
            assert "spb_history" not in have_before and "laytime_calculations" not in have_before
            n_pc_before = conn.execute("SELECT COUNT(*) FROM port_calls").fetchone()[0]
            conn.close()
            _prepare_database_once()  # jalur cold start Vercel
            conn = get_db()
            have_after = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'")}
            assert set(TABLES).issubset(have_after), f"tabel hilang setelah migrasi: {set(TABLES) - have_after}"
            n_pc_after = conn.execute("SELECT COUNT(*) FROM port_calls").fetchone()[0]
            assert n_pc_after == n_pc_before, "migrasi tidak boleh mengubah data lama"
            conn.close()
            # Panggil kedua kali: sudah lengkap -> tidak ada yang dijalankan
            # ulang (tetap murah) dan tetap lulus.
            _prepare_database_once()
        finally:
            os.environ["AGENCY_DB_FILE"] = old_db_file
        print("[TEST 34/45] ADR-0018: cold start melengkapi tabel baru di DB lama tanpa menyentuh data.")
        # 35. Test Validasi Input (ADR-0019): whitelist + normalisasi
        #     clearance, milestone_code wajib, laytime wajib 3 waktu,
        #     JSON rusak -> 400 (bukan koneksi putus / data demo tersimpan).
        #     Dulu semua ini melempar IntegrityError/AttributeError di
        #     tengah transaksi dan menutup koneksi tanpa respons JSON.
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{pc_id}") as res:
            detail35 = json.loads(res.read().decode("utf-8"))
            cl35 = detail35["clearances"][0]["id"]
        # status huruf kecil dinormalisasi -> 200 + tersimpan "APPROVED".
        with urllib.request.urlopen(_post(f"/api/clearances/{cl35}/update",
                {"status": "approved"})) as res:
            assert res.status == 200
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{pc_id}") as res:
            detail35 = json.loads(res.read().decode("utf-8"))
            cl35_row = next(c for c in detail35["clearances"] if c["id"] == cl35)
            assert cl35_row["status"] == "APPROVED", f"status 'approved' harus ternormalisasi jadi APPROVED, dapat {cl35_row['status']}"
        _expect_400(f"/api/clearances/{cl35}/update", {"status": "BOGUS"}, "status tidak dikenal")
        _expect_400(f"/api/clearances/{cl35}/update", {"doc_number": 12345}, "doc_number harus berupa teks")
        # agency di luar whitelist -> 400; huruf kecil dinormalisasi -> 200.
        _expect_400(f"/api/port-calls/{pc_id}/clearance",
                    {"agency": "POLISI", "doc_type": "Surat X", "status": "APPROVED"}, "agency tidak dikenal")
        with urllib.request.urlopen(_post(f"/api/port-calls/{pc_id}/clearance",
                {"agency": "bea_cukai", "doc_type": "Dokumen Tes Validasi", "status": "pending"})) as res:
            assert res.status == 200
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{pc_id}") as res:
            detail35 = json.loads(res.read().decode("utf-8"))
            baru35 = next(c for c in detail35["clearances"] if c["doc_type"] == "Dokumen Tes Validasi")
            assert baru35["agency"] == "BEA_CUKAI" and baru35["status"] == "PENDING", f"agency/status harus ternormalisasi, dapat {baru35}"
        # milestone: tanpa code -> 400; code tak dikenal -> 400; huruf
        # kecil -> 200 (dinormalisasi jadi EOSP).
        _expect_400(f"/api/port-calls/{pc_id}/milestone",
                    {"event_time": "2026-11-02 10:00"}, "milestone_code wajib diisi")
        _expect_400(f"/api/port-calls/{pc_id}/milestone",
                    {"milestone_code": "TERBANG", "event_time": "2026-11-02 10:00"}, "milestone_code tidak dikenal")
        with urllib.request.urlopen(_post(f"/api/port-calls/{pc_id}/milestone",
                {"milestone_code": "eosp", "event_time": "2026-11-02 10:00", "remarks": "tes normalisasi"})) as res:
            assert res.status == 200
        # laytime: body kosong -> 400 (BUKAN 200 + milestone tanggal demo);
        # satu waktu saja -> 400; lengkap -> 200.
        _expect_400(f"/api/port-calls/{pc_id}/laytime/calculate", {}, "nor_tendered_time wajib diisi")
        _expect_400(f"/api/port-calls/{pc_id}/laytime/calculate",
                    {"nor_tendered_time": "2026-11-01 08:00"}, "commenced_time wajib diisi")
        with urllib.request.urlopen(_post(f"/api/port-calls/{pc_id}/laytime/calculate",
                {"nor_tendered_time": "2026-11-01 08:00",
                 "commenced_time": "2026-11-01 14:00",
                 "completed_time": "2026-11-03 14:00"})) as res:
            assert res.status == 200
        # Body JSON rusak / bukan objek -> 400 eksplisit; server tetap
        # hidup sesudahnya (dulu koneksi putus tanpa respons).
        def _post_raw(path, raw_bytes):
            return urllib.request.Request(
                f"{base_url}{path}", data=raw_bytes,
                headers={"Content-Type": "application/json"})

        def _expect_raw_400(path, raw_bytes, frag):
            try:
                urllib.request.urlopen(_post_raw(path, raw_bytes))
                assert False, f"{path} harus ditolak 400 ({frag})"
            except urllib.error.HTTPError as e:
                assert e.code == 400
                body = e.read().decode("utf-8")
                assert frag in body, f"Respons 400 harus menyebut '{frag}', dapat {body}"

        _expect_raw_400("/api/login", b"{bukan json", "Body harus berupa JSON valid")
        _expect_raw_400("/api/login", b"[1, 2, 3]", "Body JSON harus berupa objek")
        with urllib.request.urlopen(f"{base_url}/api/port-calls") as res:
            assert res.status == 200, "Server harus tetap hidup setelah body rusak"
        print("[TEST 35/45] ADR-0019: validasi whitelist/normalisasi clearance+milestone, laytime wajib 3 waktu, JSON rusak 400.")

        # 36. Test Security Headers + /api/health (ADR-0019).
        anon_opener = urllib.request.build_opener()  # tanpa cookie
        with anon_opener.open(f"{base_url}/api/health") as res:
            assert res.status == 200
            health = json.loads(res.read().decode("utf-8"))
            assert health["status"] == "ok" and health["db"] == "ok" and health["session_secret"] == "ok", f"Health harus ok, dapat {health}"
        with urllib.request.urlopen(f"{base_url}/api/port-calls") as res:
            assert res.headers.get("X-Content-Type-Options") == "nosniff"
            assert res.headers.get("X-Frame-Options") == "DENY"
            assert res.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
        with urllib.request.urlopen(f"{base_url}/") as res:
            assert res.headers.get("X-Content-Type-Options") == "nosniff"
            assert res.headers.get("X-Frame-Options") == "DENY"
        with anon_opener.open(f"{base_url}/") as res:
            assert res.headers.get("X-Content-Type-Options") == "nosniff", "Halaman login juga harus membawa security headers"
        print("[TEST 36/45] ADR-0019: /api/health tanpa auth + security headers di API, UI, dan halaman login.")

        # 37. Status VOID (ADR-0020): baris salah input dibatalkan — tetap
        #     tersimpan sebagai riwayat, gate SPB + daftar tertunda
        #     mengabaikannya, dan baris VOID tidak bisa diubah lagi.
        #     Port call baru dengan ETA/ETD lampau supaya tenggat RKSP
        #     dan MSD sudah lewat (overdue sebelum di-void).
        with urllib.request.urlopen(_post("/api/port-calls",
                {"vessel_imo": "9876543", "port_code": "IDTPP",
                 "terminal_name": "JICT (Jakarta Int. Container Terminal)",
                 "cargo_name": "Batu Bara", "cargo_tonnage": 50000,
                 "eta": "2026-10-01 08:00", "etd": "2026-10-05 08:00",
                 "vessel_category": "FOREIGN"})) as res:
            assert res.status == 200
            pc37 = json.loads(res.read().decode("utf-8"))["port_call_id"]

        def _chk37():
            with urllib.request.urlopen(f"{base_url}/api/port-calls/{pc37}/clearance-checklist") as res:
                return json.loads(res.read().decode("utf-8"))

        def _row37(doc_type):
            with urllib.request.urlopen(f"{base_url}/api/port-calls/{pc37}") as res:
                det = json.loads(res.read().decode("utf-8"))
            return next(c for c in det["clearances"] if c["doc_type"] == doc_type)

        chk = _chk37()
        rksp = next(i for i in chk["items"] if i["category"] == "rksp")
        msd = next(i for i in chk["items"] if i["category"] == "msd")
        assert rksp["status"] == "PENDING" and rksp["overdue"], f"RKSP template harus PENDING + overdue, dapat {rksp}"
        assert msd["status"] == "PENDING" and msd["overdue"], f"MSD template harus PENDING + overdue, dapat {msd}"
        assert any("Master Sailing" in b for b in chk["blockers"]), f"MSD harus memblokir SPB, dapat {chk['blockers']}"
        assert any("RKSP" in b for b in chk["blockers"]), "RKSP harus memblokir SPB"
        # Void MSD (huruf kecil -> dinormalisasi): blocker bersyarat hilang,
        # item tampil VOID dan tidak lagi overdue.
        msd_row = _row37("Master Sailing Declaration")
        with urllib.request.urlopen(_post(f"/api/clearances/{msd_row['id']}/update",
                {"status": "void", "remarks": "Salah ketik — seharusnya bukan MSD"})) as res:
            assert res.status == 200
            assert "VOID" in json.loads(res.read().decode("utf-8"))["message"]
        assert _row37("Master Sailing Declaration")["status"] == "VOID", "MSD harus tersimpan VOID"
        chk = _chk37()
        msd = next(i for i in chk["items"] if i["category"] == "msd")
        assert msd["status"] == "VOID" and not msd["overdue"], f"MSD void harus tampil VOID tanpa overdue, dapat {msd}"
        assert not any("Master Sailing" in b for b in chk["blockers"]), \
            f"MSD yang di-void tidak boleh lagi memblokir SPB, dapat {chk['blockers']}"
        # Void RKSP (wajib): blocker TETAP ada — void bukan persetujuan.
        rksp_row = _row37("RKSP / BC 1.0")
        with urllib.request.urlopen(_post(f"/api/clearances/{rksp_row['id']}/update",
                {"status": "VOID", "remarks": "Salah tipe — diganti baris baru"})) as res:
            assert res.status == 200
        chk = _chk37()
        rksp = next(i for i in chk["items"] if i["category"] == "rksp")
        assert rksp["status"] == "VOID" and not rksp["overdue"], f"RKSP void harus tampil VOID tanpa overdue, dapat {rksp}"
        assert any("RKSP" in b for b in chk["blockers"]), \
            "RKSP wajib yang di-void harus TETAP memblokir SPB sampai ada baris baru yang APPROVED"
        # Jalan keluarnya: tambah baris RKSP baru lalu APPROVED.
        with urllib.request.urlopen(_post(f"/api/port-calls/{pc37}/clearance",
                {"agency": "BEA_CUKAI", "doc_type": "RKSP / BC 1.0",
                 "doc_number": "RKSP-2026-00099", "status": "APPROVED"})) as res:
            assert res.status == 200
        chk = _chk37()
        assert not any("RKSP" in b for b in chk["blockers"]), "RKSP baru yang APPROVED harus menghapus blocker"
        assert next(i for i in chk["items"] if i["category"] == "rksp")["status"] == "APPROVED"
        # Baris VOID tidak bisa diubah lagi — final.
        _expect_400(f"/api/clearances/{msd_row['id']}/update",
                    {"status": "APPROVED"}, "sudah VOID")
        # FINANCE tidak boleh mem-void.
        inward_row = _row37("Inward Manifest / BC 1.1")
        _expect_status(fin_opener, f"/api/clearances/{inward_row['id']}/update",
                       {"status": "VOID"}, 403, "OPS/ADMIN")
        assert _row37("Inward Manifest / BC 1.1")["status"] == "PENDING", \
            "Percobaan void oleh FINANCE tidak boleh mengubah baris"
        # Baris baru tidak bisa langsung VOID.
        _expect_400(f"/api/port-calls/{pc37}/clearance",
                    {"agency": "BEA_CUKAI", "doc_type": "Dokumen Uji VOID",
                     "status": "VOID"}, "tidak bisa langsung VOID")
        # Riwayat tetap ada: dua baris VOID masih tampil di detail.
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{pc37}") as res:
            det37 = json.loads(res.read().decode("utf-8"))
        voided = [c for c in det37["clearances"] if c["status"] == "VOID"]
        assert len(voided) == 2, f"Harus ada 2 baris VOID sebagai riwayat, dapat {len(voided)}"
        print("[TEST 37/45] ADR-0020: VOID membatalkan salah input — gate abaikan baris void bersyarat, wajib tetap blokir, VOID final + OPS/ADMIN.")

        # 38. Migrasi CHECK VOID (ADR-0020): DB lama yang CHECK status-nya
        #     belum mengenal VOID dinaikkan (rebuild tabel) tanpa menyentuh
        #     data — lewat cold start Vercel maupun init_db().
        from engine.db import _upgrade_clearances_void
        from server import _prepare_database_once
        old_db_file = os.environ["AGENCY_DB_FILE"]
        legacy_db = os.path.join(_TEST_TMPDIR, "agency-legacy-void.db")

        def _downgrade_clearances_check(conn):
            # Turunkan skema ke CHECK lama (tanpa VOID): kebalikan migrasi.
            with conn:
                conn.execute("DROP TABLE IF EXISTS clearances_old_void_mig")
                conn.execute("ALTER TABLE clearances RENAME TO clearances_old_void_mig")
                conn.execute("""
                CREATE TABLE clearances (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    port_call_id TEXT NOT NULL,
                    agency TEXT NOT NULL CHECK(agency IN ('INAPORTNET_SIMPADU', 'INAPORTNET_SPS', 'BEA_CUKAI', 'IMIGRASI', 'KARANTINA_KESEHATAN', 'BARANTIN', 'BUP_PELINDO')),
                    doc_type TEXT NOT NULL,
                    doc_number TEXT,
                    submission_time TEXT,
                    approval_time TEXT,
                    expiry_time TEXT,
                    status TEXT NOT NULL CHECK(status IN ('PENDING', 'SUBMITTED', 'APPROVED', 'REJECTED', 'EXPIRED')),
                    file_name TEXT,
                    file_sha256 TEXT,
                    remarks TEXT,
                    submitted_by TEXT,
                    FOREIGN KEY (port_call_id) REFERENCES port_calls (id)
                )""")
                conn.execute("INSERT INTO clearances SELECT * FROM clearances_old_void_mig")
                conn.execute("DROP TABLE clearances_old_void_mig")

        try:
            os.environ["AGENCY_DB_FILE"] = legacy_db
            init_db()
            seed_sample_data()
            conn = get_db()
            _downgrade_clearances_check(conn)
            n_cl_before = conn.execute("SELECT COUNT(*) FROM clearances").fetchone()[0]
            ids_before = [r[0] for r in conn.execute("SELECT id FROM clearances ORDER BY id").fetchall()]
            sql_before = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'clearances'").fetchone()[0]
            assert "'VOID'" not in sql_before, "Setup: CHECK lama tidak boleh mengenal VOID"
            conn.close()
            # Jalur cold start Vercel menaikkan CHECK-nya.
            _prepare_database_once()
            conn = get_db()
            sql_after = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'clearances'").fetchone()[0]
            assert "'VOID'" in sql_after, "CHECK harus mengenal VOID setelah cold start"
            n_cl_after = conn.execute("SELECT COUNT(*) FROM clearances").fetchone()[0]
            ids_after = [r[0] for r in conn.execute("SELECT id FROM clearances ORDER BY id").fetchall()]
            assert n_cl_after == n_cl_before and ids_after == ids_before, \
                "Migrasi CHECK tidak boleh mengubah data lama"
            conn.close()
            # Jalur init_db() (server lokal) juga menaikkan — turunkan lagi,
            # lalu init_db() saja.
            conn = get_db()
            _downgrade_clearances_check(conn)
            conn.close()
            init_db()
            conn = get_db()
            sql_after2 = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'clearances'").fetchone()[0]
            assert "'VOID'" in sql_after2, "CHECK harus mengenal VOID setelah init_db()"
            # Status VOID kini bisa disimpan (dulu CHECK menolaknya).
            with conn:
                conn.execute("INSERT INTO clearances (port_call_id, agency, doc_type, status) VALUES ('PC-2026-JKT-0001', 'BEA_CUKAI', 'Dokumen Uji VOID', 'VOID')")
            assert conn.execute("SELECT status FROM clearances WHERE doc_type = 'Dokumen Uji VOID'").fetchone()[0] == "VOID"
            conn.close()
            # Panggil upgrade langsung dua kali: idempotent, tetap lulus.
            c2 = get_db()
            _upgrade_clearances_void(c2)
            _upgrade_clearances_void(c2)
            c2.close()
        finally:
            os.environ["AGENCY_DB_FILE"] = old_db_file
        print("[TEST 38/45] ADR-0020: migrasi CHECK VOID (cold start + init_db) tanpa mengubah data; VOID bisa disimpan; idempotent.")

        # 39. Tambah pelabuhan master (ADR-0021): endpoint + validasi.
        # Kode huruf kecil -> dinormalisasi jadi IDBLW; duplikat -> 400;
        # kode bukan 5 karakter / bukan alfanumerik -> 400; nama/provinsi
        # kosong -> 400; level tak dikenal -> 400; level huruf kecil ->
        # disimpan bentuk kanonik; max_draft negatif -> 400.
        with urllib.request.urlopen(_post("/api/master/ports",
                {"code": "idblw", "name": "Belawan", "province": "Sumatera Utara",
                 "inaportnet_level": "kelas i", "vts_channel": "VHF Ch 16",
                 "max_draft": 12.0, "notes": "Pelabuhan utama Medan"})) as res:
            assert res.status == 200
            assert json.loads(res.read().decode("utf-8"))["code"] == "IDBLW"
        with urllib.request.urlopen(f"{base_url}/api/master/ports") as res:
            ports = json.loads(res.read().decode("utf-8"))["ports"]
        blw = next(p for p in ports if p["code"] == "IDBLW")
        assert blw["name"] == "Belawan" and blw["province"] == "Sumatera Utara", f"Belawan harus tersimpan lengkap, dapat {blw}"
        assert blw["unlocode"] == "IDBLW", "unlocode default harus = code"
        assert blw["inaportnet_level"] == "Kelas I", f"level harus kanonik 'Kelas I', dapat {blw['inaportnet_level']}"
        assert blw["max_draft"] == 12.0
        # Terminal untuk pelabuhan baru ikut tampil di master data.
        with urllib.request.urlopen(_post("/api/master/terminals",
                {"port_code": "IDBLW", "terminal_name": "Terminal Petikemas Belawan", "terminal_type": "PETIKEMAS"})) as res:
            assert res.status == 200
        with urllib.request.urlopen(f"{base_url}/api/master/ports") as res:
            ports = json.loads(res.read().decode("utf-8"))["ports"]
        blw = next(p for p in ports if p["code"] == "IDBLW")
        assert any(t["name"] == "Terminal Petikemas Belawan" for t in blw["terminals"]), "Terminal baru harus tampil di bawah IDBLW"
        # Penolakan: duplikat, kode salah, field wajib kosong, level salah, draft negatif.
        _expect_400("/api/master/ports",
                    {"code": "IDBLW", "name": "Belawan Lagi", "province": "Sumatera Utara", "inaportnet_level": "Kelas I"},
                    "sudah terdaftar")
        _expect_400("/api/master/ports",
                    {"code": "IDB", "name": "Pendek", "province": "Sumatera Utara", "inaportnet_level": "Kelas I"},
                    "5 karakter")
        _expect_400("/api/master/ports",
                    {"code": "IDB!W", "name": "Simbol", "province": "Sumatera Utara", "inaportnet_level": "Kelas I"},
                    "5 karakter")
        _expect_400("/api/master/ports",
                    {"code": "IDSBY", "name": "", "province": "Jawa Timur", "inaportnet_level": "Kelas I"},
                    "name")
        _expect_400("/api/master/ports",
                    {"code": "IDSBY", "name": "Surabaya", "province": "Jawa Timur", "inaportnet_level": "Kelas X"},
                    "inaportnet_level")
        _expect_400("/api/master/ports",
                    {"code": "IDSBY", "name": "Surabaya", "province": "Jawa Timur", "inaportnet_level": "Kelas I", "max_draft": -1},
                    "tidak boleh negatif")
        print("[TEST 39/45] ADR-0021: tambah pelabuhan master (normalisasi kode+level, duplikat/kode/level/draft ditolak, terminal ikut tampil).")

        # 40. ADR-0022 — tenggat pasca-ATD (laytime 3d, voucher 7d,
        # FDA 30d, sengketa 14d) dihitung dari ATD; badge di detail +
        # papan shift; yang selesai tidak lagi menarik perhatian.
        now40 = datetime.now()
        atd40 = now40 - timedelta(days=2)
        with urllib.request.urlopen(_post("/api/port-calls",
                {"vessel_imo": "9876543", "port_code": "IDTPP",
                 "terminal_name": "JICT (Jakarta Int. Container Terminal)",
                 "cargo_name": "Batu Bara", "cargo_tonnage": 50000,
                 "eta": "2026-11-12 08:00", "vessel_category": "FOREIGN"})) as res:
            assert res.status == 200
            pc40 = json.loads(res.read().decode("utf-8"))["port_call_id"]
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{pc40}") as res:
            det40 = json.loads(res.read().decode("utf-8"))
        for cl in det40["clearances"]:
            if "SPB" in cl["doc_type"]:
                continue
            with urllib.request.urlopen(_post(f"/api/clearances/{cl['id']}/update",
                    {"doc_number": f"DOC-{cl['id']}-2026", "status": "APPROVED"})) as res:
                assert res.status == 200
        with urllib.request.urlopen(_post(f"/api/port-calls/{pc40}/spb",
                {"doc_number": "SPB.T40.001", "issued_at": now40.strftime("%Y-%m-%d %H:%M:%S")})) as res:
            assert res.status == 200
        # Bertolak 2 hari lalu -> ATD = 2 hari lalu.
        with urllib.request.urlopen(_post(f"/api/port-calls/{pc40}/milestone",
                {"milestone_code": "SAILED", "event_time": atd40.strftime("%Y-%m-%d %H:%M"),
                 "remarks": "Bertolak 2 hari lalu (uji tenggat pasca-ATD)"})) as res:
            assert res.status == 200
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{pc40}") as res:
            det40 = json.loads(res.read().decode("utf-8"))
        assert det40["status"] == "DEPARTED", f"status harus DEPARTED, dapat {det40['status']}"
        assert det40["atd"], "ATD harus terisi dari milestone SAILED"
        patd = {i["key"]: i for i in det40["post_atd"]}
        assert set(patd) == {"voucher", "laytime", "fda"}, f"post_atd harus 3 item, dapat {list(patd)}"
        # Tenggat dihitung dari ATD (string due eksak), belum lewat, belum selesai.
        atd_dt = datetime.strptime(det40["atd"], "%Y-%m-%d %H:%M")
        assert patd["laytime"]["due"] == (atd_dt + timedelta(days=3)).strftime("%Y-%m-%d %H:%M")
        assert patd["voucher"]["due"] == (atd_dt + timedelta(days=7)).strftime("%Y-%m-%d %H:%M")
        assert patd["fda"]["due"] == (atd_dt + timedelta(days=30)).strftime("%Y-%m-%d %H:%M")
        assert patd["laytime"]["days_left"] in (0, 1) and not patd["laytime"]["overdue"] and not patd["laytime"]["done"]
        assert patd["voucher"]["days_left"] in (4, 5) and not patd["voucher"]["overdue"]
        assert patd["fda"]["days_left"] in (27, 28) and not patd["fda"]["overdue"]
        # Laytime dihitung -> item laytime selesai.
        with urllib.request.urlopen(_post(f"/api/port-calls/{pc40}/laytime/calculate",
                {"nor_tendered_time": "2026-11-12 08:00", "commenced_time": "2026-11-12 14:00",
                 "completed_time": "2026-11-14 14:00"})) as res:
            assert res.status == 200
        # Sengketa baru -> tenggatnya ikut muncul (dibuat + 14 hari).
        with urllib.request.urlopen(_post("/api/disbursements",
                {"port_call_id": pc40, "category": "AGENCY_FEE",
                 "item_name": "Official Agency Fee", "pda_estimate": 25000000})) as res:
            dis40 = json.loads(res.read().decode("utf-8"))["disbursement_id"]
        with urllib.request.urlopen(_post(f"/api/port-calls/{pc40}/dispute",
                {"disbursement_id": dis40, "disputed_amount": 500000,
                 "reason": "tarif melebihi PDA"})) as res:
            assert res.status == 200
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{pc40}") as res:
            det40 = json.loads(res.read().decode("utf-8"))
        patd = {i["key"]: i for i in det40["post_atd"]}
        assert patd["laytime"]["done"], "laytime harus selesai setelah kalkulasi"
        dis_item = [i for i in det40["post_atd"] if i["key"] == "dispute"]
        assert len(dis_item) == 1, f"sengketa harus muncul di post_atd, dapat {det40['post_atd']}"
        assert dis_item[0]["due"].startswith((now40 + timedelta(days=14)).strftime("%Y-%m-%d")), \
            f"tenggat sengketa harus hari ini + 14 hari, dapat {dis_item[0]['due']}"
        assert dis_item[0]["days_left"] in (13, 14) and not dis_item[0]["overdue"]
        # Papan shift: kartu hanya memuat yang belum selesai.
        with urllib.request.urlopen(f"{base_url}/api/shift-board") as res:
            board = json.loads(res.read().decode("utf-8"))
        card40 = next(c for c in board["cards"] if c["id"] == pc40)
        assert {i["key"] for i in card40["post_atd"]} == {"voucher", "fda", "dispute"}, \
            f"kartu harus menyembunyikan laytime yang selesai, dapat {card40['post_atd']}"
        assert card40["post_atd_overdue"] == 0
        # Port call yang ATD-nya 10 hari lalu: laytime + voucher lewat.
        with urllib.request.urlopen(_post("/api/port-calls",
                {"vessel_imo": "9876543", "port_code": "IDTPP",
                 "terminal_name": "JICT (Jakarta Int. Container Terminal)",
                 "cargo_name": "Batu Bara", "cargo_tonnage": 50000,
                 "eta": "2026-10-20 08:00", "vessel_category": "DOMESTIC"})) as res:
            pc41 = json.loads(res.read().decode("utf-8"))["port_call_id"]
        conn = get_db()
        conn.execute("UPDATE port_calls SET status = 'DEPARTED', atd = ? WHERE id = ?",
                     ((now40 - timedelta(days=10)).strftime("%Y-%m-%d %H:%M"), pc41))
        conn.commit()
        conn.close()
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{pc41}") as res:
            det41 = json.loads(res.read().decode("utf-8"))
        patd41 = {i["key"]: i for i in det41["post_atd"]}
        assert patd41["laytime"]["overdue"] and patd41["laytime"]["days_left"] in (-8, -7)
        assert patd41["voucher"]["overdue"] and patd41["voucher"]["days_left"] in (-4, -3)
        assert not patd41["fda"]["overdue"] and patd41["fda"]["days_left"] in (19, 20)
        with urllib.request.urlopen(f"{base_url}/api/shift-board") as res:
            board = json.loads(res.read().decode("utf-8"))
        card41 = next(c for c in board["cards"] if c["id"] == pc41)
        assert card41["post_atd_overdue"] == 2, f"2 tenggat harus lewat, dapat {card41['post_atd_overdue']}"
        assert board["totals"]["post_atd_overdue"] >= 2
        # Port call baru tanpa ATD (belum pernah SAILED): tidak ada
        # tenggat pasca-ATD. (PC lama di suite sudah berlayar semua:
        # seed PC di Tes 5, pc27 di Tes 31, pc2 di Tes 28.)
        with urllib.request.urlopen(_post("/api/port-calls",
                {"vessel_imo": "9876543", "port_code": "IDTPP",
                 "terminal_name": "JICT (Jakarta Int. Container Terminal)",
                 "cargo_name": "Batu Bara", "cargo_tonnage": 50000,
                 "eta": "2026-11-12 08:00", "vessel_category": "FOREIGN"})) as res:
            pc42 = json.loads(res.read().decode("utf-8"))["port_call_id"]
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{pc42}") as res:
            det42 = json.loads(res.read().decode("utf-8"))
        assert det42["post_atd"] == [], f"tanpa ATD post_atd harus kosong, dapat {det42['post_atd']}"
        print("[TEST 40/45] ADR-0022: tenggat pasca-ATD (laytime 3d/voucher 7d/FDA 30d/sengketa 14d) + badge detail & papan shift.")

        # 41. ADR-0024 — milestone GANGWAY_DOWN + COB (SOP Tahap 3-4):
        # wajib catat menit tepat, keduanya TIDAK mengubah status.
        with urllib.request.urlopen(_post("/api/port-calls",
                {"vessel_imo": "9876543", "port_code": "IDTPP",
                 "terminal_name": "JICT (Jakarta Int. Container Terminal)",
                 "cargo_name": "Batu Bara", "cargo_tonnage": 50000,
                 "eta": "2026-11-12 08:00", "vessel_category": "FOREIGN"})) as res:
            pc43 = json.loads(res.read().decode("utf-8"))["port_call_id"]
        # Huruf kecil dinormalisasi (ADR-0019); status tetap PRE_ARRIVAL.
        with urllib.request.urlopen(_post(f"/api/port-calls/{pc43}/milestone",
                {"milestone_code": "gangway_down", "event_time": "2026-11-12 10:05",
                 "remarks": "Tangga turun, menit tepat dicatat"})) as res:
            assert res.status == 200
        with urllib.request.urlopen(_post(f"/api/port-calls/{pc43}/milestone",
                {"milestone_code": "COB", "event_time": "2026-11-14 16:30",
                 "remarks": "Dokumen muatan di kapal — pemicu clearance-out"})) as res:
            assert res.status == 200
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{pc43}") as res:
            det43 = json.loads(res.read().decode("utf-8"))
        assert det43["status"] == "PRE_ARRIVAL", f"GANGWAY_DOWN/COB tidak boleh mengubah status, dapat {det43['status']}"
        codes43 = {m["milestone_code"] for m in det43["milestones"]}
        assert {"GANGWAY_DOWN", "COB"} <= codes43, f"kedua milestone harus tercatat, dapat {codes43}"
        gw = next(m for m in det43["milestones"] if m["milestone_code"] == "GANGWAY_DOWN")
        assert gw["event_time"] == "2026-11-12 10:05"
        print("[TEST 41/45] ADR-0024: milestone GANGWAY_DOWN + COB tercatat (menit tepat), status tidak berubah.")

        # 42. ADR-0025 — sertifikat kapal: tambah + badge expired/akan
        # expired (<= 30 hari) di detail + papan shift; expiry terbaru
        # per jenis yang dinilai.
        today42 = datetime.now().date()
        # Kapal seed (9431289) sudah punya SSCEC seed s.d. 2027-12-31.
        with urllib.request.urlopen(f"{base_url}/api/vessels/9431289/certificates") as res:
            certs_seed = json.loads(res.read().decode("utf-8"))
        assert len(certs_seed["certificates"]) == 1
        assert certs_seed["certificates"][0]["cert_type"] == "SSCEC"
        assert certs_seed["alerts"] == [], "SSCEC seed 2027 tidak boleh alert"
        # Sertifikat Garis Muat yang sudah kedaluwarsa 5 hari lalu.
        with urllib.request.urlopen(_post("/api/vessels/9431289/certificates",
                {"cert_type": "Sertifikat Garis Muat", "cert_number": "GL-2025-88",
                 "issued_date": (today42 - timedelta(days=400)).isoformat(),
                 "expiry_date": (today42 - timedelta(days=5)).isoformat(),
                 "notes": "kedaluwarsa — perpanjang sebelum sandar berikut"})) as res:
            assert res.status == 200
        # SSCEC baru yang lebih dekat (10 hari lagi) — expiry terbaru
        # per jenis adalah 2027, jadi SSCEC tetap tidak alert.
        with urllib.request.urlopen(_post("/api/vessels/9431289/certificates",
                {"cert_type": "SSCEC", "cert_number": "SSCEC-2026-200",
                 "expiry_date": (today42 + timedelta(days=10)).isoformat()})) as res:
            assert res.status == 200
        # Sertifikat SAMUDRA JAYA (9876543) yang akan expired 10 hari lagi.
        with urllib.request.urlopen(_post("/api/vessels/9876543/certificates",
                {"cert_type": "Sertifikat Manajemen Keselamatan (SMC)",
                 "expiry_date": (today42 + timedelta(days=10)).isoformat()})) as res:
            assert res.status == 200
        # Penolakan: jenis kosong, tanggal rusak, kapal tidak dikenal.
        _expect_400("/api/vessels/9431289/certificates",
                    {"cert_type": "", "expiry_date": "2027-01-01"}, "cert_type")
        _expect_400("/api/vessels/9431289/certificates",
                    {"cert_type": "SSCEC", "expiry_date": "31-12-2027"}, "expiry_date")
        try:
            urllib.request.urlopen(_post("/api/vessels/0000000/certificates",
                    {"cert_type": "SSCEC", "expiry_date": "2027-01-01"}))
            assert False, "Kapal tidak dikenal harus 404"
        except urllib.error.HTTPError as e:
            assert e.code == 404
        # Detail kapal seed: Garis Muat expired, SSCEC aman.
        with urllib.request.urlopen(f"{base_url}/api/port-calls/PC-2026-JKT-0001") as res:
            det_cert = json.loads(res.read().decode("utf-8"))
        assert len(det_cert["certificates"]) == 3
        alerts_cert = {a["cert_type"]: a for a in det_cert["cert_alerts"]}
        assert set(alerts_cert) == {"Sertifikat Garis Muat"}, f"hanya Garis Muat yang alert, dapat {list(alerts_cert)}"
        assert alerts_cert["Sertifikat Garis Muat"]["status"] == "expired"
        assert alerts_cert["Sertifikat Garis Muat"]["days_left"] in (-6, -5)
        # Papan shift: kartu kapal seed membawa alert expired; kartu
        # kapal SAMUDRA JAYA (pc40, DEPARTED) membawa alert expiring.
        with urllib.request.urlopen(f"{base_url}/api/shift-board") as res:
            board = json.loads(res.read().decode("utf-8"))
        card_seed = next(c for c in board["cards"] if c["id"] == "PC-2026-JKT-0001")
        assert len(card_seed["cert_alerts"]) == 1
        assert card_seed["cert_alerts"][0]["status"] == "expired"
        card40b = next(c for c in board["cards"] if c["id"] == pc40)
        smc = [a for a in card40b["cert_alerts"] if "SMC" in a["cert_type"]]
        assert len(smc) == 1 and smc[0]["status"] == "expiring"
        assert smc[0]["days_left"] in (9, 10)
        assert board["totals"]["cert_alerts"] >= 2
        print("[TEST 42/45] ADR-0025: sertifikat kapal (SSCEC + statutori) — tambah, badge expired/akan expired di detail & papan shift.")

        # 43. ADR-0026 — LK3 Masuk + SPKK/RKBM di template clearance:
        # template kini 14 item; LK3 tenggat ETA+24 jam; SPKK tanpa
        # tenggat tanggal; gate SPB TIDAK berubah (keduanya PENDING
        # tidak memblokir SPB).
        with urllib.request.urlopen(_post("/api/port-calls",
                {"vessel_imo": "9876543", "port_code": "IDTPP",
                 "terminal_name": "JICT (Jakarta Int. Container Terminal)",
                 "cargo_name": "Batu Bara", "cargo_tonnage": 50000,
                 "eta": "2026-11-12 08:00", "vessel_category": "FOREIGN"})) as res:
            pc44 = json.loads(res.read().decode("utf-8"))["port_call_id"]
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{pc44}") as res:
            det44 = json.loads(res.read().decode("utf-8"))
        assert len(det44["clearances"]) == 14, f"template harus 14 item, dapat {len(det44['clearances'])}"
        types44 = [c["doc_type"] for c in det44["clearances"]]
        assert "LK3 Masuk / Laporan Kedatangan Kapal" in types44
        assert "SPKK / RKBM (Persetujuan Kegiatan Kapal)" in types44
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{pc44}/clearance-checklist") as res:
            chk44 = json.loads(res.read().decode("utf-8"))
        assert len(chk44["items"]) == 14
        lk3 = next(i for i in chk44["items"] if i["category"] == "lk3")
        spkk = next(i for i in chk44["items"] if i["category"] == "spkk")
        assert lk3["deadline"] == "2026-11-13 08:00", f"LK3 tenggat harus ETA+24 jam, dapat {lk3['deadline']}"
        assert lk3["status"] == "PENDING"
        assert spkk["deadline"] is None and spkk["warning"] is None, "SPKK tidak punya tenggat tanggal"
        assert spkk["status"] == "PENDING"
        # Gate tidak berubah: SPB bisa terbit walau LK3 + SPKK masih PENDING.
        for cl in det44["clearances"]:
            if "SPB" in cl["doc_type"] or "LK3" in cl["doc_type"] or "SPKK" in cl["doc_type"]:
                continue
            with urllib.request.urlopen(_post(f"/api/clearances/{cl['id']}/update",
                    {"doc_number": f"DOC-{cl['id']}-2026", "status": "APPROVED"})) as res:
                assert res.status == 200
        with urllib.request.urlopen(_post(f"/api/port-calls/{pc44}/spb",
                {"doc_number": "SPB.T43.001", "issued_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})) as res:
            assert res.status == 200
        # LK3 overdue: ETA sudah lewat 24 jam + masih PENDING.
        with urllib.request.urlopen(_post("/api/port-calls",
                {"vessel_imo": "9876543", "port_code": "IDTPP",
                 "terminal_name": "JICT (Jakarta Int. Container Terminal)",
                 "cargo_name": "Batu Bara", "cargo_tonnage": 50000,
                 "eta": "2026-10-20 08:00", "vessel_category": "DOMESTIC"})) as res:
            pc45 = json.loads(res.read().decode("utf-8"))["port_call_id"]
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{pc45}/clearance-checklist") as res:
            chk45 = json.loads(res.read().decode("utf-8"))
        lk3b = next(i for i in chk45["items"] if i["category"] == "lk3")
        assert lk3b["overdue"], f"LK3 harus overdue (ETA 2026-10-20 + 24 jam sudah lewat), dapat {lk3b}"
        assert lk3b["deadline"] == "2026-10-21 08:00"
        # Port call contoh lama (seed) mendapat kedua item via migrasi seed.
        with urllib.request.urlopen(f"{base_url}/api/port-calls/PC-2026-JKT-0001") as res:
            det_seed2 = json.loads(res.read().decode("utf-8"))
        seed_types = {c["doc_type"]: c["status"] for c in det_seed2["clearances"]}
        assert seed_types.get("LK3 Masuk / Laporan Kedatangan Kapal") == "APPROVED"
        assert seed_types.get("SPKK / RKBM (Persetujuan Kegiatan Kapal)") == "APPROVED"
        print("[TEST 43/45] ADR-0026: LK3 Masuk + SPKK/RKBM di template (14 item); LK3 ETA+24 jam, SPKK tanpa tenggat tanggal, gate SPB tidak berubah.")

        # 44. Tindak lanjut review celah SOP: (a) sertifikat expiry < issued
        # ditolak server 400 (dulu lolos 200 — terbukti probe review);
        # expiry == issued dan tanpa issued tetap diterima; (b) tooltip
        # catatan di badge sertifikat (UI) + notes ikut di alert papan
        # shift; (c) basis SPKK menyebut "tanpa tenggat".
        _expect_400("/api/vessels/9431289/certificates",
                    {"cert_type": "IOPP", "cert_number": "T44-BAD",
                     "issued_date": "2026-06-15", "expiry_date": "2026-01-01"},
                    "sama atau setelah issued_date")
        with urllib.request.urlopen(f"{base_url}/api/vessels/9431289/certificates") as res:
            certs44 = json.loads(res.read().decode("utf-8"))["certificates"]
        assert not any(c["cert_number"] == "T44-BAD" for c in certs44), "sertifikat yang ditolak tidak boleh tersimpan"
        with urllib.request.urlopen(_post("/api/vessels/9431289/certificates",
                {"cert_type": "IOPP", "cert_number": "T44-EQ",
                 "issued_date": "2026-06-15", "expiry_date": "2026-06-15"})) as res:
            assert res.status == 200
        with urllib.request.urlopen(_post("/api/vessels/9431289/certificates",
                {"cert_type": "IOPP", "cert_number": "T44-NOISSUED",
                 "expiry_date": "2026-12-31", "notes": "tanpa tanggal terbit"})) as res:
            assert res.status == 200
        # (b) UI: certBadge memakai notes sebagai tooltip.
        with urllib.request.urlopen(f"{base_url}/") as res:
            html44 = res.read().decode("utf-8")
        assert 'title="${esc(c.notes)}"' in html44, "certBadge harus memakai notes sebagai tooltip"
        # Alert papan shift membawa notes (server): SMC kedaluwarsa ber-notes.
        with urllib.request.urlopen(_post("/api/vessels/9431289/certificates",
                {"cert_type": "SMC", "cert_number": "T44-EXP",
                 "issued_date": "2025-01-01", "expiry_date": "2026-10-28",
                 "notes": "T44 catatan alert"})) as res:
            assert res.status == 200
        with urllib.request.urlopen(f"{base_url}/api/shift-board") as res:
            sb44 = json.loads(res.read().decode("utf-8"))
        smc_alerts = [a for card in sb44["cards"] for a in (card.get("cert_alerts") or []) if a["cert_type"] == "SMC"]
        assert smc_alerts and smc_alerts[0].get("notes") == "T44 catatan alert", f"alert shift-board harus membawa notes, dapat {smc_alerts}"
        # (c) basis SPKK menyebut tanpa tenggat (checklist API).
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{pc44}/clearance-checklist") as res:
            chk44 = json.loads(res.read().decode("utf-8"))
        spkk44 = next(i for i in chk44["items"] if i["category"] == "spkk")
        assert "tanpa tenggat" in spkk44["basis"], f"basis SPKK harus menyebut tanpa tenggat, dapat {spkk44['basis']}"
        print("[TEST 44/45] Tindak lanjut review: expiry>=issued ditolak 400, tooltip notes di badge, basis SPKK 'tanpa tenggat'.")
        # 45. ADR-0028 — daftar port call bertab + paginasi server:
        # tab active/action/done/archive, pencarian q, limit/offset +
        # total/has_more/tab_counts, urutan urgensi; tanpa query =
        # perilaku lama (semua baris).
        # Siapkan 2 port call CLOSED: satu baru ditutup (< 30 hari),
        # satu sudah lama (>= 30 hari) — via SQL langsung (status +
        # finance_closed_at hanya diisi close-finance; alur penuh sudah
        # diuji Tes 13b/28). Tanggal dihitung relatif terhadap hari ini
        # supaya tes tidak basi. Kapal segar tanpa sertifikat untuk PC
        # tanpa alert (pembanding urutan urgensi).
        _now45 = datetime.now()
        _recent45 = (_now45 - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        _old45 = (_now45 - timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S")
        _cut45 = (_now45 - timedelta(days=30)).strftime("%Y-%m-%d")
        with urllib.request.urlopen(_post("/api/port-calls",
                {"vessel_imo": "9876543", "port_code": "IDTPP",
                 "terminal_name": "JICT (Jakarta Int. Container Terminal)",
                 "cargo_name": "Batu Bara", "cargo_tonnage": 50000,
                 "eta": "2026-11-12 08:00", "vessel_category": "DOMESTIC"})) as res:
            pc46 = json.loads(res.read().decode("utf-8"))["port_call_id"]
        with urllib.request.urlopen(_post("/api/port-calls",
                {"vessel_imo": "9431289", "port_code": "IDTPP",
                 "terminal_name": "JICT (Jakarta Int. Container Terminal)",
                 "cargo_name": "CPO", "cargo_tonnage": 30000,
                 "eta": "2026-11-12 08:00", "vessel_category": "FOREIGN"})) as res:
            pc47 = json.loads(res.read().decode("utf-8"))["port_call_id"]
        _db = get_db()
        with _db:
            _db.execute("UPDATE port_calls SET status = 'CLOSED', finance_closed_at = ?, finance_closed_by = 'fin_tes' WHERE id = ?", (_recent45, pc46))
            _db.execute("UPDATE port_calls SET status = 'CLOSED', finance_closed_at = ?, finance_closed_by = 'fin_tes' WHERE id = ?", (_old45, pc47))
        _db.close()
        with urllib.request.urlopen(_post("/api/master/vessels",
                {"imo": "9550001", "name": "MV. PAGINASI UJI", "flag": "Indonesia",
                 "vessel_type": "General Cargo", "grt": 12000, "dwt": 18000})) as res:
            assert res.status == 200
        with urllib.request.urlopen(_post("/api/port-calls",
                {"vessel_imo": "9550001", "port_code": "IDTPP",
                 "terminal_name": "JICT (Jakarta Int. Container Terminal)",
                 "cargo_name": "Batu Bara", "cargo_tonnage": 10000,
                 "eta": "2026-12-01 08:00", "vessel_category": "DOMESTIC"})) as res:
            pc48 = json.loads(res.read().decode("utf-8"))["port_call_id"]
        def _list(query):
            with urllib.request.urlopen(f"{base_url}/api/port-calls{query}") as res:
                return json.loads(res.read().decode("utf-8"))
        # Tanpa query: perilaku lama (semua baris, tanpa total).
        legacy = _list("")
        assert "total" not in legacy and len(legacy["port_calls"]) >= 3
        assert any(p["id"] == "PC-2026-JKT-0001" for p in legacy["port_calls"])
        # Tab active: hanya bukan CLOSED; pc46/pc47 tidak ada.
        act = _list("?tab=active")
        assert all(p["status"] != "CLOSED" for p in act["port_calls"])
        assert not any(p["id"] in (pc46, pc47) for p in act["port_calls"])
        assert act["total"] == len(act["port_calls"]) and act["tab"] == "active"
        assert act["tab_counts"]["active"] == act["total"]
        assert set(act["tab_counts"]) == {"active", "action", "done", "archive"}
        # Tab done: pc46 (tutup < 30 hari) ada, pc47 tidak; semua baris
        # memang tutup < 30 hari. Tab archive: sebaliknya.
        done = _list("?tab=done")
        ids_done = [p["id"] for p in done["port_calls"]]
        assert pc46 in ids_done and pc47 not in ids_done, ids_done
        assert all(p["finance_closed_at"] and p["finance_closed_at"][:10] >= _cut45 for p in done["port_calls"]), done["port_calls"]
        assert done["tab_counts"]["done"] == done["total"] and done["total"] >= 1
        arch = _list("?tab=archive")
        ids_arch = [p["id"] for p in arch["port_calls"]]
        assert pc47 in ids_arch and pc46 not in ids_arch, ids_arch
        assert all(p["finance_closed_at"] and p["finance_closed_at"][:10] < _cut45 for p in arch["port_calls"]), arch["port_calls"]
        assert arch["tab_counts"]["archive"] == arch["total"] and arch["total"] >= 1
        # Tab action: pc45 (LK3 overdue, Tes 43) ada; PC segar tanpa
        # alert (pc48) dan yang sudah CLOSED tidak ada.
        actn = _list("?tab=action")
        ids_action = [p["id"] for p in actn["port_calls"]]
        assert pc45 in ids_action, f"pc45 (LK3 overdue) harus di Perlu Tindakan, dapat {ids_action}"
        assert pc48 not in ids_action and pc46 not in ids_action and pc47 not in ids_action
        assert actn["tab_counts"]["action"] == actn["total"]
        # Urutan urgensi di tab active: pc45 (ada alert) di atas pc48 (tanpa alert).
        ids_active = [p["id"] for p in act["port_calls"]]
        assert ids_active.index(pc45) < ids_active.index(pc48), f"pc45 ber-alert harus di atas pc48, urutan {ids_active}"
        # Pencarian q: nama kapal / IMO / ID port call.
        assert [p["id"] for p in _list("?tab=active&q=PAGINASI")["port_calls"]] == [pc48]
        assert pc48 in [p["id"] for p in _list("?tab=active&q=9550001")["port_calls"]]
        assert pc48 in [p["id"] for p in _list(f"?tab=active&q={pc48}")["port_calls"]]
        assert pc48 not in [p["id"] for p in _list("?tab=active&q=SAMUDRA")["port_calls"]]
        # Paginasi: limit/offset + total/has_more, halaman tidak tumpang tindih.
        p1 = _list("?tab=active&limit=2&offset=0")
        assert len(p1["port_calls"]) == 2 and p1["has_more"] is True and p1["total"] == act["total"]
        p2 = _list("?tab=active&limit=2&offset=2")
        assert len(p2["port_calls"]) == 2
        assert not ({p["id"] for p in p1["port_calls"]} & {p["id"] for p in p2["port_calls"]})
        pend = _list(f"?tab=active&limit=2&offset={act['total']}")
        assert pend["port_calls"] == [] and pend["has_more"] is False
        # Tab/limit tidak valid -> 400.
        try:
            urllib.request.urlopen(f"{base_url}/api/port-calls?tab=bogus")
            assert False, "tab bogus harus ditolak 400"
        except urllib.error.HTTPError as e:
            assert e.code == 400
        try:
            urllib.request.urlopen(f"{base_url}/api/port-calls?tab=active&limit=abc")
            assert False, "limit bukan angka harus ditolak 400"
        except urllib.error.HTTPError as e:
            assert e.code == 400
        # UI: tab + pencarian + tombol muat lebih banyak tersaji.
        with urllib.request.urlopen(f"{base_url}/") as res:
            html45 = res.read().decode("utf-8")
        assert 'id="pc-tabs"' in html45 and 'id="pc-search"' in html45
        assert 'id="pc-load-more"' in html45 and 'id="pc-count"' in html45
        print("[TEST 45/45] ADR-0028: daftar bertab (active/action/done/archive) + pencarian + paginasi + urutan urgensi; tanpa query tetap perilaku lama.")

        print("\nSEMUA INTEGRATION TEST LULUS (100% VERIFIED)!")

    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    test_integration()
