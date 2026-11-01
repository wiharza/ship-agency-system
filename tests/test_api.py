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

    socketserver.TCPServer.allow_reuse_address = True  # sebelum bind (TIME_WAIT)
    server = socketserver.TCPServer(("", TEST_PORT), AgencyAPIHandler)
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

        # 5. Test POST SPB (Surat Persetujuan Berlayar - 24h timer)
        req_spb = urllib.request.Request(
            f"{base_url}/api/port-calls/{pc_id}/spb",
            data=json.dumps({"doc_number": "SPB.IDTPP.2026.001", "issued_at": "2026-11-05 10:00:00"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req_spb) as res:
            assert res.status == 200
            spb_res = json.loads(res.read().decode("utf-8"))
            assert spb_res["spb_expiry_at"] == "2026-11-06 10:00:00"
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
            assert upd_res["file_sha256"] is not None

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
            assert len(det["clearances"]) == 12, f"Template baru harus 12 item, dapat {len(det['clearances'])}"
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
            assert len(chk["clearance_in"]) == 6 and len(chk["clearance_out"]) == 6
            rksp = next(i for i in chk["items"] if i["category"] == "rksp")
            assert rksp["deadline"] == "2026-11-09 08:00", f"RKSP deadline harus ETA-24 jam, dapat {rksp['deadline']}"
        # IMO uji harus unik per run (DB dipakai ulang antar run tes)
        with urllib.request.urlopen(f"{base_url}/api/master/ports") as res:
            existing_imos = {v["imo"] for v in json.loads(res.read().decode("utf-8"))["vessels"]}
        test_imo = next(i for i in ("9999999", "9999998", "9999997", "9999996") if i not in existing_imos)
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
        # -> status DEPARTED dan atd terisi dari event_time.
        now2 = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with urllib.request.urlopen(_post(f"/api/port-calls/{new_pc}/spb",
                {"doc_number": "SPB.TEST.002", "issued_at": now2})) as res:
            assert res.status == 200
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

        print("\nSEMUA INTEGRATION TEST LULUS (100% VERIFIED)!")

    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    test_integration()
