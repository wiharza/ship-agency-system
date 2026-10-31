"""
Web Application & REST API Server Sistem Keagenan Kapal Nasional (Python stdlib).
Berdasarkan ADR-0002, ADR-0003, dan Putusan Q1 s.d. Q32.
"""

import http.server
import socketserver
import json
import urllib.parse
import os
import sys
from datetime import datetime, timedelta
from decimal import Decimal

from engine.db import get_db, init_db, seed_sample_data, calculate_file_sha256
from engine.laytime import LaytimeCalculator, parse_iso

PORT = 8000


class AgencyAPIHandler(http.server.SimpleHTTPRequestHandler):
    def send_json(self, data, status=200):
        response_bytes = json.dumps(data, indent=2, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response_bytes)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(response_bytes)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/":
            self.serve_ui()
            return

        if path == "/api/port-calls":
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("""
            SELECT pc.*, v.name as vessel_name, v.flag as vessel_flag, p.name as port_name, pr.name as principal_name, b.name as branch_name
            FROM port_calls pc
            JOIN master_vessels v ON pc.vessel_imo = v.imo
            JOIN master_ports p ON pc.port_code = p.code
            JOIN master_principals pr ON pc.principal_id = pr.id
            JOIN master_branches b ON pc.branch_id = b.id
            ORDER BY pc.created_at DESC
            """)
            rows = [dict(r) for r in cursor.fetchall()]
            conn.close()
            self.send_json({"port_calls": rows})
            return

        if path.startswith("/api/port-calls/"):
            parts = path.strip("/").split("/")
            if len(parts) == 3:
                pc_id = parts[2]
                conn = get_db()
                c = conn.cursor()
                c.execute("""
                SELECT pc.*, v.name as vessel_name, v.flag as vessel_flag, v.grt, v.dwt, v.loa, v.max_draft,
                       p.name as port_name, p.inaportnet_level, pr.name as principal_name, pr.email as principal_email,
                       b.name as branch_name
                FROM port_calls pc
                JOIN master_vessels v ON pc.vessel_imo = v.imo
                JOIN master_ports p ON pc.port_code = p.code
                JOIN master_principals pr ON pc.principal_id = pr.id
                JOIN master_branches b ON pc.branch_id = b.id
                WHERE pc.id = ?
                """, (pc_id,))
                pc = c.fetchone()
                if not pc:
                    conn.close()
                    self.send_json({"error": "Port Call tidak ditemukan"}, status=404)
                    return

                res = dict(pc)

                # Charter party params
                c.execute("SELECT * FROM port_call_charter_parties WHERE port_call_id = ?", (pc_id,))
                cp = c.fetchone()
                res["charter_party"] = dict(cp) if cp else None

                # Clearances
                c.execute("SELECT * FROM clearances WHERE port_call_id = ? ORDER BY id ASC", (pc_id,))
                res["clearances"] = [dict(r) for r in c.fetchall()]

                # Milestones
                c.execute("SELECT * FROM milestones WHERE port_call_id = ? ORDER BY event_time ASC", (pc_id,))
                res["milestones"] = [dict(r) for r in c.fetchall()]

                # SOF Stoppages
                c.execute("SELECT * FROM sof_stoppages WHERE port_call_id = ? ORDER BY start_time ASC", (pc_id,))
                res["stoppages"] = [dict(r) for r in c.fetchall()]

                # Disbursements
                c.execute("SELECT * FROM disbursements WHERE port_call_id = ? ORDER BY id ASC", (pc_id,))
                res["disbursements"] = [dict(r) for r in c.fetchall()]

                # Disputes
                c.execute("""
                SELECT d.*, dis.item_name, dis.category
                FROM fda_disputes d
                JOIN disbursements dis ON d.disbursement_id = dis.id
                WHERE d.port_call_id = ?
                ORDER BY d.id DESC
                """, (pc_id,))
                res["disputes"] = [dict(r) for r in c.fetchall()]

                conn.close()
                self.send_json(res)
                return

            elif len(parts) == 4 and parts[3] == "handover":
                # Dynamic Handover Shift Summary (Putusan Q28)
                pc_id = parts[2]
                conn = get_db()
                c = conn.cursor()
                c.execute("SELECT * FROM port_calls WHERE id = ?", (pc_id,))
                pc = c.fetchone()
                if not pc:
                    conn.close()
                    self.send_json({"error": "Not found"}, status=404)
                    return

                # Pending clearances
                c.execute("SELECT doc_type, agency FROM clearances WHERE port_call_id = ? AND status != 'APPROVED'", (pc_id,))
                pending_docs = [f"{r['doc_type']} ({r['agency']})" for r in c.fetchall()]

                # Latest milestone
                c.execute("SELECT milestone_code, event_time, remarks FROM milestones WHERE port_call_id = ? ORDER BY id DESC LIMIT 1", (pc_id,))
                last_ms = c.fetchone()

                conn.close()

                handover_text = (
                    f"=== RINGKASAN HANDOVER SHIFT TIM KEAGENAN (24/7) ===\n"
                    f"Port Call ID: {pc_id}\n"
                    f"Status Kapal: {pc['status']} | Jenis: {pc['vessel_category']}\n"
                    f"Posisi/Terminal: {pc['terminal_name']}\n"
                    f"Peristiwa Terakhir: {last_ms['milestone_code'] if last_ms else 'Belum ada'} pada {last_ms['event_time'] if last_ms else '-'}\n"
                    f"Izin & Dokumen Tertunda: {', '.join(pending_docs) if pending_docs else 'Nihil / Semua Disetujui'}\n"
                    f"Peringatan Kritis: {'SPB Aktif! Perhatikan batas waktu 24 jam' if pc['spb_issued_at'] else 'Normal'}\n"
                    f"Waktu Dibuat: {datetime.now().strftime('%Y-%m-%d %H:%M:%S WIB')}\n"
                    f"======================================================"
                )
                self.send_json({"handover_text": handover_text})
                return

        self.send_json({"error": "Endpoint tidak ditemukan"}, status=404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        try:
            payload = json.loads(body)
        except Exception:
            payload = {}

        if path.startswith("/api/port-calls/"):
            parts = path.strip("/").split("/")

            # POST /api/port-calls/<id>/milestone
            if len(parts) == 4 and parts[3] == "milestone":
                pc_id = parts[2]
                code = payload.get("milestone_code")
                event_time = payload.get("event_time", datetime.now().strftime("%Y-%m-%d %H:%M"))
                recorded_by = payload.get("recorded_by", "agent_ops")
                remarks = payload.get("remarks", "")

                conn = get_db()
                with conn:
                    conn.execute("""
                    INSERT INTO milestones (port_call_id, milestone_code, event_time, recorded_by, remarks)
                    VALUES (?, ?, ?, ?, ?)
                    """, (pc_id, code, event_time, recorded_by, remarks))

                    # Update status port call jika milestone kunci
                    new_status = None
                    if code in ("EOSP", "ANCHORED"):
                        new_status = "ARRIVED"
                    elif code in ("FIRST_LINE", "ALL_FAST", "COMMENCED_CARGO"):
                        new_status = "BERTHED"
                    elif code in ("CAST_OFF", "SAILED"):
                        new_status = "DEPARTED"

                    if new_status:
                        conn.execute("UPDATE port_calls SET status = ?, updated_at = ? WHERE id = ?", (new_status, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), pc_id))

                conn.close()
                self.send_json({"status": "success", "message": f"Milestone {code} dicatat"})
                return

            # POST /api/port-calls/<id>/stoppage
            if len(parts) == 4 and parts[3] == "stoppage":
                pc_id = parts[2]
                conn = get_db()
                with conn:
                    conn.execute("""
                    INSERT INTO sof_stoppages (port_call_id, start_time, end_time, category, percentage_counted, remarks)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        pc_id,
                        payload.get("start_time"),
                        payload.get("end_time"),
                        payload.get("category", "OTHER"),
                        float(payload.get("percentage_counted", 0)),
                        payload.get("remarks", "")
                    ))
                conn.close()
                self.send_json({"status": "success", "message": "Jeda SOF berhasil ditambahkan"})
                return

            # POST /api/port-calls/<id>/spb
            if len(parts) == 4 and parts[3] == "spb":
                pc_id = parts[2]
                issue_time_str = payload.get("issued_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                doc_number = payload.get("doc_number", "SPB-ONLINE-001")
                issue_dt = parse_iso(issue_time_str)
                # PM 28/2022 Ps 9 ayat (1): Kedaluwarsa tepat 24 jam sejak diterbitkan
                expiry_dt = issue_dt + timedelta(hours=24)
                expiry_time_str = expiry_dt.strftime("%Y-%m-%d %H:%M:%S")

                conn = get_db()
                with conn:
                    conn.execute("""
                    UPDATE port_calls 
                    SET spb_issued_at = ?, spb_expiry_at = ?, updated_at = ?
                    WHERE id = ?
                    """, (issue_time_str, expiry_time_str, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), pc_id))

                    conn.execute("""
                    UPDATE clearances
                    SET doc_number = ?, approval_time = ?, expiry_time = ?, status = 'APPROVED', remarks = 'Berlaku 24 jam'
                    WHERE port_call_id = ? AND doc_type LIKE '%SPB%'
                    """, (doc_number, issue_time_str, expiry_time_str, pc_id))
                conn.close()
                self.send_json({
                    "status": "success",
                    "message": "SPB resmi diterbitkan. Peringatan: hitung mundur 24 jam aktif!",
                    "spb_issued_at": issue_time_str,
                    "spb_expiry_at": expiry_time_str
                })
                return

            # POST /api/port-calls/<id>/clearance
            if len(parts) == 4 and parts[3] == "clearance":
                pc_id = parts[2]
                agency = payload.get("agency")
                doc_type = payload.get("doc_type")
                doc_number = payload.get("doc_number")
                status = payload.get("status", "APPROVED")
                remarks = payload.get("remarks", "")
                fake_file_content = f"{pc_id}-{doc_type}-{doc_number}".encode("utf-8")
                file_sha256 = calculate_file_sha256(fake_file_content)

                doc_slug = (doc_type or "doc").lower().replace(' ', '_')
                conn = get_db()
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                with conn:
                    conn.execute("""
                    INSERT INTO clearances (port_call_id, agency, doc_type, doc_number, submission_time, approval_time, status, file_name, file_sha256, remarks, submitted_by)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (pc_id, agency, doc_type, doc_number, now_str, now_str, status, f"{doc_slug}.pdf", file_sha256, remarks, "ops_agent"))
                conn.close()
                self.send_json({"status": "success", "file_sha256": file_sha256})
                return

            # POST /api/port-calls/<id>/laytime/calculate
            if len(parts) == 5 and parts[3] == "laytime" and parts[4] == "calculate":
                pc_id = parts[2]
                conn = get_db()
                c = conn.cursor()
                c.execute("SELECT * FROM port_call_charter_parties WHERE port_call_id = ?", (pc_id,))
                cp = c.fetchone()
                c.execute("SELECT cargo_tonnage FROM port_calls WHERE id = ?", (pc_id,))
                pc = c.fetchone()

                if not cp or not pc:
                    conn.close()
                    self.send_json({"error": "Data kontrak atau kargo tidak lengkap"}, status=400)
                    return

                c.execute("SELECT * FROM sof_stoppages WHERE port_call_id = ? ORDER BY start_time ASC", (pc_id,))
                stoppages = [
                    {
                        "start": r["start_time"],
                        "end": r["end_time"],
                        "category": r["category"],
                        "percentage_counted": r["percentage_counted"],
                        "description": r["remarks"]
                    }
                    for r in c.fetchall()
                ]
                conn.close()

                nor_str = payload.get("nor_tendered_time", "2026-11-01 08:00")
                comm_str = payload.get("commenced_time", "2026-11-01 14:00")
                comp_str = payload.get("completed_time", "2026-11-04 14:00")

                calc = LaytimeCalculator({
                    "cargo_quantity": pc["cargo_tonnage"] or 50000,
                    "loading_rate_per_day": cp["loading_rate_per_day"],
                    "terms": cp["laytime_terms"],
                    "demurrage_rate_per_day": cp["demurrage_usd_per_day"],
                    "despatch_rate_per_day": cp["despatch_usd_per_day"],
                    "nor_notice_hours": cp["nor_notice_hours"]
                })
                result = calc.calculate(nor_str, comm_str, comp_str, stoppages)
                self.send_json(result)
                return

            # POST /api/port-calls/<id>/close-ops (Otorisasi 1)
            if len(parts) == 4 and parts[3] == "close-ops":
                pc_id = parts[2]
                user_name = payload.get("user", "Staf Operasional")
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                conn = get_db()
                with conn:
                    conn.execute("""
                    UPDATE port_calls 
                    SET ops_closed_by = ?, ops_closed_at = ?, status = 'CLEARANCE_CLOSED', updated_at = ?
                    WHERE id = ?
                    """, (user_name, now_str, now_str, pc_id))
                conn.close()
                self.send_json({"status": "success", "message": f"Otorisasi 1 (Clearance Operasional) disahkan oleh {user_name}"})
                return

            # POST /api/port-calls/<id>/close-finance (Otorisasi 2)
            if len(parts) == 4 and parts[3] == "close-finance":
                pc_id = parts[2]
                user_name = payload.get("user", "Finance Manager")
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                conn = get_db()
                c = conn.cursor()
                c.execute("SELECT ops_closed_at FROM port_calls WHERE id = ?", (pc_id,))
                row = c.fetchone()
                if not row or not row["ops_closed_at"]:
                    conn.close()
                    self.send_json({"error": "Gagal: Otorisasi 1 (Operasional) wajib diselesaikan terlebih dahulu!"}, status=400)
                    return

                with conn:
                    conn.execute("""
                    UPDATE port_calls 
                    SET finance_closed_by = ?, finance_closed_at = ?, status = 'CLOSED', updated_at = ?
                    WHERE id = ?
                    """, (user_name, now_str, now_str, pc_id))
                conn.close()
                self.send_json({"status": "success", "message": f"Otorisasi 2 (Finance) disahkan. Port Call resmi CLOSED (Arsip 10 Tahun Terkunci)."})
                return

            # POST /api/port-calls/<id>/dispute
            if len(parts) == 4 and parts[3] == "dispute":
                pc_id = parts[2]
                disbursement_id = payload.get("disbursement_id", 1)
                amount = float(payload.get("disputed_amount", 0))
                reason = payload.get("reason", "Keberatan tarif")
                deadline = (datetime.now() + timedelta(days=14)).strftime("%Y-%m-%d")

                conn = get_db()
                with conn:
                    conn.execute("""
                    INSERT INTO fda_disputes (port_call_id, disbursement_id, disputed_amount, principal_reason, deadline_date, status, resolution_notes)
                    VALUES (?, ?, ?, ?, ?, 'OPEN', 'Tiket sengketa dibuka')
                    """, (pc_id, disbursement_id, amount, reason, deadline))
                conn.close()
                self.send_json({"status": "success", "message": "Tiket sengketa FDA berhasil didaftarkan"})
                return

        self.send_json({"error": "Endpoint tidak dikenali"}, status=404)

    def serve_ui(self):
        ui_path = os.path.join(os.path.dirname(__file__), "ui", "index.html")
        if os.path.exists(ui_path):
            with open(ui_path, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"UI file not found")


def run_server(port=PORT):
    init_db()
    seed_sample_data()
    server = socketserver.TCPServer(("", port), AgencyAPIHandler)
    server.allow_reuse_address = True
    print(f"Server Keagenan Kapal berjalan di http://localhost:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer dihentikan.")
        server.server_close()


if __name__ == "__main__":
    p = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    run_server(p)
