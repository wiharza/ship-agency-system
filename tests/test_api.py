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
import urllib.request
import urllib.error

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from server import AgencyAPIHandler, run_server
import socketserver

TEST_PORT = 8999


def test_integration():
    from engine.db import get_db
    # Reset status sample port call agar test idempotent
    conn = get_db()
    with conn:
        conn.execute("UPDATE port_calls SET ops_closed_by = NULL, ops_closed_at = NULL, finance_closed_by = NULL, finance_closed_at = NULL, status = 'PRE_ARRIVAL' WHERE id = 'PC-2026-JKT-0001'")
    conn.close()

    server = socketserver.TCPServer(("", TEST_PORT), AgencyAPIHandler)
    server.allow_reuse_address = True
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    time.sleep(0.5)

    base_url = f"http://127.0.0.1:{TEST_PORT}"

    try:
        # 1. Test GET / (UI)
        with urllib.request.urlopen(f"{base_url}/") as res:
            assert res.status == 200
            html = res.read().decode("utf-8")
            assert "SISTEM KEAGENAN KAPAL NASIONAL" in html
        print("[TEST 1/8] GET / UI Berhasil diakses.")

        # 2. Test GET /api/port-calls
        with urllib.request.urlopen(f"{base_url}/api/port-calls") as res:
            assert res.status == 200
            data = json.loads(res.read().decode("utf-8"))
            assert len(data["port_calls"]) > 0
            pc_id = data["port_calls"][0]["id"]
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
        # Coba sign finance dulu -> Harusnya gagal (HTTP 400) karena ops belum sign-off
        req_fin = urllib.request.Request(
            f"{base_url}/api/port-calls/{pc_id}/close-finance",
            data=json.dumps({"user": "Finance Mgr"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        try:
            urllib.request.urlopen(req_fin)
            assert False, "Finance closure harus ditolak jika Ops belum sign-off"
        except urllib.error.HTTPError as e:
            assert e.code == 400
        print("[TEST 7/8] Dual Sign-off Barrier Valid (Finance tertolak sebelum Ops).")

        # Sekarang sign-off Ops dulu
        req_ops = urllib.request.Request(
            f"{base_url}/api/port-calls/{pc_id}/close-ops",
            data=json.dumps({"user": "Capt. Hadi"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req_ops) as res:
            assert res.status == 200

        # Lalu sign-off Finance
        with urllib.request.urlopen(req_fin) as res:
            assert res.status == 200
        print("[TEST 7/8b] Dual Sign-off Berhasil (Ops -> Finance -> Status CLOSED).")

        # 8. Test Dynamic Handover Shift Summary
        with urllib.request.urlopen(f"{base_url}/api/port-calls/{pc_id}/handover") as res:
            assert res.status == 200
            ho = json.loads(res.read().decode("utf-8"))
            assert "RINGKASAN HANDOVER SHIFT" in ho["handover_text"]
        print("[TEST 8/8] GET Dynamic Handover Shift Berhasil.")

        print("\nSEMUA INTEGRATION TEST LULUS (100% VERIFIED)!")

    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    test_integration()
