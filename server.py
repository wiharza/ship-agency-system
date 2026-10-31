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

from engine.db import get_db, init_db, seed_sample_data, calculate_file_sha256, log_audit
from engine.laytime import LaytimeCalculator, parse_iso

PORT = 8000

# ---------------------------------------------------------------------------
# Clearance gate SPB & checklist tenggat (PM 28/2022, PM 8/2022, PMK 158/2017
# jo PMK 97/2020, UU 6/2011 Ps 18, UU 17/2023 + PP 28/2024).
# Sistem ini TRACKER INTERNAL: status APPROVED di sini berarti "nomor dokumen
# resmi sudah dicatat & diverifikasi agen di portal resmi", bukan penerbitan
# oleh sistem ini. Pengajuan resmi tetap di Inaportnet/INSW/CEISA/Simkapel/
# SINKARKES. NOR/SOF dan PDA/FDA adalah praktik komersial charter party,
# bukan regulasi Kemenhub.
# ---------------------------------------------------------------------------

def _norm_doc(doc_type):
    return (doc_type or "").upper()


def _matches(doc_type, agency, category):
    t = _norm_doc(doc_type)
    a = (agency or "").upper()
    if category == "free_pratique":
        return "FREE PRATIQUE" in t or "MDH" in t
    if category == "rksp":
        return "RKSP" in t or "BC 1.0" in t
    if category == "inward":
        return "INWARD" in t or (("BC 1.1" in t or "BC11" in t) and ("MASUK" in t or "BC11-IN" in t))
    if category == "outward":
        return "OUTWARD" in t or "BC 1.2" in t or (("BC 1.1" in t) and "KELUAR" in t)
    if category == "imm_crew":
        return a == "IMIGRASI" and "CREW LIST" in t
    if category == "imm_out":
        return a == "IMIGRASI" and "CLEARANCE OUT" in t
    if category == "phc_out":
        return "PORT HEALTH" in t and "OUT" in t
    if category == "msd":
        return "MASTER SAILING" in t or "SAILING DECLARATION" in t or t.strip() == "MSD"
    if category == "pkka":
        return "PKKA" in t or "PPKKA" in t or "WARTA MASUK" in t
    if category == "spog_in":
        return "SPOG" in t and "MASUK" in t
    if category == "spog_out":
        return "SPOG" in t and "KELUAR" in t
    if category == "spb":
        return "SPB" in t
    return False


def _approved(rows, category):
    return any(r["status"] == "APPROVED" for r in rows if _matches(r["doc_type"], r["agency"], category))


def _exists(rows, category):
    return any(_matches(r["doc_type"], r["agency"], category) for r in rows)


def clearance_spb_gate(clearances):
    """Kembalikan (blockers, can_issue_spb). Wajib: Free Pratique/MDH,
    RKSP/BC 1.0, Inward Manifest/BC 1.1. Bersyarat (hanya bila itemnya ada):
    Immigration Crew List, Outward Manifest, Immigration Clearance Out,
    Port Health Clearance Out, Master Sailing Declaration."""
    blockers = []
    required = [
        ("free_pratique", "MDH & Free Pratique belum APPROVED (Karantina Kesehatan)"),
        ("rksp", "RKSP / BC 1.0 belum APPROVED (Bea Cukai)"),
        ("inward", "Inward Manifest / BC 1.1 belum APPROVED (Bea Cukai)"),
    ]
    conditional = [
        ("imm_crew", "Immigration Crew List belum APPROVED (Imigrasi)"),
        ("outward", "Outward Manifest / BC 1.1 belum APPROVED (Bea Cukai)"),
        ("imm_out", "Immigration Clearance Out belum APPROVED (Imigrasi)"),
        ("phc_out", "Port Health Clearance Out belum APPROVED (Karantina Kesehatan)"),
        ("msd", "Master Sailing Declaration belum APPROVED"),
    ]
    for cat, msg in required:
        if not _approved(clearances, cat):
            blockers.append(msg)
    for cat, msg in conditional:
        if _exists(clearances, cat) and not _approved(clearances, cat):
            blockers.append(msg)
    return blockers, len(blockers) == 0


def _fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M") if dt else None


def build_clearance_checklist(pc, clearances):
    """Hitung tenggat clearance dari ETA/ETD. RKSP & warta kapal: ETA-24 jam
    (PM 28/2022 Ps 4; PMK 158/2017 jo PMK 97/2020). Inward Manifest: sebelum
    bongkar / ETA-24 jam. Outward Manifest: sebelum ETD. Permohonan SPB:
    ETD-4 jam (PM 8/2022: verifikasi keberangkatan maks 4 jam sebelum ETD
    kapal barang). SPB berlaku 24 jam sejak diterbitkan (PM 28/2022 Ps 9)."""
    now = datetime.now()
    try:
        eta = parse_iso(pc["eta"]) if pc.get("eta") else None
    except Exception:
        eta = None
    try:
        etd = parse_iso(pc["etd"]) if pc.get("etd") else None
    except Exception:
        etd = None
    is_foreign = (pc.get("vessel_category") or "FOREIGN") == "FOREIGN"
    specs = [
        ("CLEARANCE_IN", "pkka",
         "PKKA / Pemberitahuan Kedatangan Kapal Asing" if is_foreign else "Warta Masuk / PKK",
         "INAPORTNET_SIMPADU", eta - timedelta(hours=24) if eta else None,
         "PM 28/2022 Ps 4: warta + dokumen paling lambat 1x24 jam sebelum tiba"),
        ("CLEARANCE_IN", "rksp", "RKSP / BC 1.0", "BEA_CUKAI",
         eta - timedelta(hours=24) if eta else None,
         "PMK 158/2017 jo PMK 97/2020: paling lambat 24 jam sebelum kedatangan"),
        ("CLEARANCE_IN", "inward", "Inward Manifest / BC 1.1", "BEA_CUKAI",
         eta - timedelta(hours=24) if eta else None,
         "PMK 158/2017 jo PMK 97/2020: sebelum bongkar; manifes nihil bila tanpa muatan"),
        ("CLEARANCE_IN", "free_pratique", "MDH & Free Pratique", "KARANTINA_KESEHATAN",
         eta, "UU 17/2023 + PP 28/2024: sebelum kapal berhubungan dengan darat"),
        ("CLEARANCE_IN", "imm_crew", "Immigration Crew List", "IMIGRASI",
         eta, "UU 6/2011 Ps 18: pemberitahuan + crew list; shore pass via TPI"),
        ("CLEARANCE_IN", "spog_in", "SPOG Masuk", "INAPORTNET_SPS",
         eta, "PM 28/2022: persetujuan olah gerak masuk oleh Syahbandar"),
        ("CLEARANCE_OUT", "outward", "Outward Manifest / BC 1.1", "BEA_CUKAI",
         etd, "PMK 158/2017 jo PMK 97/2020: sebelum keberangkatan"),
        ("CLEARANCE_OUT", "phc_out", "Port Health Clearance Out", "KARANTINA_KESEHATAN",
         etd, "UU 17/2023 + PP 28/2024: karantina keberangkatan; tanpa persetujuan karantina kesehatan, Syahbandar dilarang terbitkan SPB"),
        ("CLEARANCE_OUT", "imm_out", "Immigration Clearance Out", "IMIGRASI",
         etd, "UU 6/2011: outward immigration clearance sebelum SPB"),
        ("CLEARANCE_OUT", "msd", "Master Sailing Declaration", "INAPORTNET_SPS",
         etd - timedelta(hours=4) if etd else None,
         "PM 28/2022 Ps 11: MSD syarat permohonan SPB elektronik"),
        ("CLEARANCE_OUT", "spog_out", "SPOG Keluar", "INAPORTNET_SPS",
         etd, "PM 28/2022: persetujuan olah gerak keluar oleh Syahbandar"),
        ("CLEARANCE_OUT", "spb", "SPB (Surat Persetujuan Berlayar)", "INAPORTNET_SPS",
         etd - timedelta(hours=4) if etd else None,
         "PM 28/2022 Ps 9: SPB berlaku 24 jam, 1x pelayaran; verifikasi keberangkatan maks 4 jam sebelum ETD (PM 8/2022)"),
    ]
    items = []
    for phase, cat, label, agency, deadline, basis in specs:
        matched = [r for r in clearances if _matches(r["doc_type"], r["agency"], cat)]
        if matched:
            approved = [r for r in matched if r["status"] == "APPROVED"]
            chosen = approved[0] if approved else matched[0]
            status = chosen["status"]
            doc_number = chosen["doc_number"]
            clearance_id = chosen["id"]
        else:
            status, doc_number, clearance_id = "BELUM ADA", None, None
        overdue = bool(deadline and now > deadline and status != "APPROVED")
        due_soon = bool(deadline and status != "APPROVED" and not overdue
                        and (deadline - now) <= timedelta(hours=4))
        items.append({
            "phase": phase, "category": cat, "doc_type": label, "agency": agency,
            "status": status, "doc_number": doc_number, "clearance_id": clearance_id,
            "deadline": _fmt(deadline), "basis": basis,
            "overdue": overdue, "due_soon": due_soon,
            "warning": ("Tenggat sudah lewat dan dokumen belum APPROVED" if overdue
                        else ("Tenggat < 4 jam lagi" if due_soon
                              else ("ETD belum diisi — tenggat clearance-out belum dapat dihitung" if deadline is None and phase == "CLEARANCE_OUT" else None))),
        })
    blockers, can_issue = clearance_spb_gate(clearances)
    return {
        "port_call_id": pc["id"], "vessel_category": pc.get("vessel_category"),
        "eta": pc.get("eta"), "etd": pc.get("etd"),
        "spb_issued_at": pc.get("spb_issued_at"), "spb_expiry_at": pc.get("spb_expiry_at"),
        "can_issue_spb": can_issue, "blockers": blockers,
        "clearance_in": [i for i in items if i["phase"] == "CLEARANCE_IN"],
        "clearance_out": [i for i in items if i["phase"] == "CLEARANCE_OUT"],
        "items": items,
        "note": "Tracker internal — bukan portal resmi. Pengajuan resmi tetap di Inaportnet/INSW/CEISA/Simkapel/SINKARKES.",
    }


def _insert_disbursement(conn, pc_id, payload):
    category = payload.get("category")
    item_name = (payload.get("item_name") or "").strip()
    allowed = ("PNBP_KEMENHUB", "BUP_PELINDO", "CIGP_INSTANSI", "HUSBANDRY", "AGENCY_FEE")
    if category not in allowed:
        return None, f"category wajib salah satu dari: {', '.join(allowed)}"
    if not item_name:
        return None, "item_name wajib diisi"
    try:
        pda = float(payload.get("pda_estimate", 0) or 0)
        fda = float(payload.get("fda_actual", 0) or 0)
    except (TypeError, ValueError):
        return None, "pda_estimate / fda_actual harus angka"
    if pda < 0 or fda < 0:
        return None, "pda_estimate / fda_actual tidak boleh negatif"
    cur = conn.execute(
        "INSERT INTO disbursements (port_call_id, category, item_name, pda_estimate, fda_actual, simponi_billing, ntpn, invoice_number, voucher_sha256) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (pc_id, category, item_name, pda, fda,
         payload.get("simponi_billing"), payload.get("ntpn"),
         payload.get("invoice_number"), payload.get("voucher_sha256")),
    )
    return cur.lastrowid, None



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

        if path == "/api/master/ports":
            conn = get_db()
            c = conn.cursor()
            c.execute("SELECT * FROM master_ports ORDER BY name")
            ports = [dict(r) for r in c.fetchall()]
            c.execute("SELECT * FROM master_terminals ORDER BY port_code, name")
            terminals = [dict(r) for r in c.fetchall()]
            c.execute("SELECT imo, name, flag, vessel_type FROM master_vessels ORDER BY name")
            vessels = [dict(r) for r in c.fetchall()]
            conn.close()

            port_map = {}
            for p in ports:
                p["terminals"] = []
                port_map[p["code"]] = p

            for t in terminals:
                if t["port_code"] in port_map:
                    port_map[t["port_code"]]["terminals"].append(t)

            self.send_json({"ports": list(port_map.values()), "vessels": vessels})
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

            elif len(parts) == 4 and parts[3] == "clearance-checklist":
                pc_id = parts[2]
                conn = get_db()
                c = conn.cursor()
                c.execute("SELECT * FROM port_calls WHERE id = ?", (pc_id,))
                pc = c.fetchone()
                if not pc:
                    conn.close()
                    self.send_json({"error": "Port Call tidak ditemukan"}, status=404)
                    return
                c.execute("SELECT * FROM clearances WHERE port_call_id = ? ORDER BY id ASC", (pc_id,))
                clearances = [dict(r) for r in c.fetchall()]
                conn.close()
                self.send_json(build_clearance_checklist(dict(pc), clearances))
                return

            elif len(parts) == 4 and parts[3] == "audit-logs":
                pc_id = parts[2]
                conn = get_db()
                c = conn.cursor()
                c.execute("SELECT * FROM audit_logs WHERE port_call_id = ? ORDER BY id ASC", (pc_id,))
                rows = [dict(r) for r in c.fetchall()]
                conn.close()
                self.send_json({"port_call_id": pc_id, "audit_logs": rows})
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

        if path == "/api/master/vessels":
            imo = (payload.get("imo") or "").strip()
            name = (payload.get("name") or "").strip()
            flag = (payload.get("flag") or "").strip()
            vessel_type = (payload.get("vessel_type") or "").strip() or "General Cargo"
            if not imo or not name or not flag:
                self.send_json({"error": "imo, name, dan flag wajib diisi"}, status=400)
                return
            try:
                grt = float(payload.get("grt", 0) or 0)
                dwt = float(payload.get("dwt", 0) or 0)
                loa = float(payload.get("loa", 0) or 0)
                max_draft = float(payload.get("max_draft", 0) or 0)
            except (TypeError, ValueError):
                self.send_json({"error": "grt/dwt/loa/max_draft harus angka"}, status=400)
                return
            if min(grt, dwt, loa, max_draft) < 0:
                self.send_json({"error": "grt/dwt/loa/max_draft tidak boleh negatif"}, status=400)
                return
            conn = get_db()
            try:
                with conn:
                    conn.execute(
                        "INSERT INTO master_vessels (imo, name, flag, vessel_type, call_sign, grt, dwt, loa, max_draft) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (imo, name, flag, vessel_type, payload.get("call_sign"), grt, dwt, loa, max_draft),
                    )
            except Exception:
                conn.close()
                self.send_json({"error": f"Kapal IMO {imo} sudah terdaftar"}, status=400)
                return
            conn.close()
            self.send_json({"status": "success", "imo": imo, "message": f"Kapal {name} (IMO {imo}) berhasil ditambahkan"})
            return

        if path == "/api/disbursements":
            pc_id = payload.get("port_call_id")
            if not pc_id:
                self.send_json({"error": "port_call_id wajib diisi"}, status=400)
                return
            conn = get_db()
            c = conn.cursor()
            c.execute("SELECT id FROM port_calls WHERE id = ?", (pc_id,))
            if not c.fetchone():
                conn.close()
                self.send_json({"error": "Port Call tidak ditemukan"}, status=404)
                return
            with conn:
                dis_id, err = _insert_disbursement(conn, pc_id, payload)
                if not err:
                    log_audit(conn, pc_id, payload.get("user", "ops_agent"), "DISBURSEMENT_ADD",
                              None, f"{payload.get('category')} — {payload.get('item_name')}")
            conn.close()
            if err:
                self.send_json({"error": err}, status=400)
                return
            self.send_json({"status": "success", "disbursement_id": dis_id, "message": "Item PDA/FDA berhasil ditambahkan"})
            return

        if path == "/api/master/terminals":
            port_code = payload.get("port_code")
            terminal_name = payload.get("terminal_name")
            terminal_type = payload.get("terminal_type", "UMUM")
            if not port_code or not terminal_name:
                self.send_json({"error": "port_code dan terminal_name wajib diisi"}, status=400)
                return
            conn = get_db()
            with conn:
                conn.execute("""
                INSERT OR IGNORE INTO master_terminals (port_code, name, terminal_type)
                VALUES (?, ?, ?)
                """, (port_code, terminal_name.strip(), terminal_type))
            conn.close()
            self.send_json({"status": "success", "message": f"Terminal '{terminal_name}' berhasil ditambahkan ke {port_code}"})
            return

        if path == "/api/port-calls":
            # POST /api/port-calls (Tambah Port Call Baru)
            branch_id = payload.get("branch_id", "BR-JKT")
            vessel_imo = payload.get("vessel_imo")
            principal_id = payload.get("principal_id", "PR-PAC")
            vessel_category = payload.get("vessel_category", "FOREIGN")
            port_code = payload.get("port_code", "IDTPP")
            terminal_name = payload.get("terminal_name", "Terminal Umum")
            eta = payload.get("eta", datetime.now().strftime("%Y-%m-%d %H:%M"))
            etd = payload.get("etd")  # opsional; bila diisi, tenggat clearance-out dapat dihitung
            cargo_name = payload.get("cargo_name", "General Cargo")
            cargo_tonnage = float(payload.get("cargo_tonnage", 10000))

            if not vessel_imo:
                self.send_json({"error": "IMO kapal wajib diisi"}, status=400)
                return

            conn = get_db()
            c = conn.cursor()

            # Validasi pencegahan typo: Port Code wajib terdaftar di master_ports
            c.execute("SELECT code FROM master_ports WHERE code = ?", (port_code,))
            if not c.fetchone():
                conn.close()
                self.send_json({"error": f"Kode pelabuhan '{port_code}' tidak terdaftar di sistem master! Pilih dari daftar baku."}, status=400)
                return
            for _table, _col, _val, _label in (
                ("master_branches", "id", branch_id, "Cabang"),
                ("master_principals", "id", principal_id, "Principal"),
                ("master_vessels", "imo", vessel_imo, "Kapal (IMO)"),
            ):
                c.execute(f"SELECT 1 FROM {_table} WHERE {_col} = ?", (_val,))
                if not c.fetchone():
                    conn.close()
                    self.send_json({"error": f"{_label} '{_val}' tidak terdaftar di master! Daftarkan dulu via master data."}, status=400)
                    return

            # Pastikan terminal tersimpan di master_terminals
            c.execute("SELECT id FROM master_terminals WHERE port_code = ? AND name = ?", (port_code, terminal_name))
            if not c.fetchone():
                c.execute("INSERT OR IGNORE INTO master_terminals (port_code, name, terminal_type) VALUES (?, ?, 'UMUM')", (port_code, terminal_name))

            # Generate ID: PC-YYYY-PORT-XXXX
            year_str = datetime.now().strftime("%Y")
            c.execute("SELECT COUNT(*) FROM port_calls WHERE id LIKE ?", (f"PC-{year_str}%",))
            count = c.fetchone()[0] + 1
            pc_id = f"PC-{year_str}-{port_code[2:]}-{count:04d}"

            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with conn:
                conn.execute("""
                INSERT INTO port_calls (id, branch_id, vessel_imo, principal_id, vessel_category, port_code, terminal_name, status, eta, etd, cargo_name, cargo_tonnage, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'PRE_ARRIVAL', ?, ?, ?, ?, ?, ?)
                """, (pc_id, branch_id, vessel_imo, principal_id, vessel_category, port_code, terminal_name, eta, etd, cargo_name, cargo_tonnage, now_str, now_str))

                # Default CP params
                conn.execute("""
                INSERT INTO port_call_charter_parties VALUES (?, 'Charterer Standard', 10000, 'SHINC', 20000, 10000, 6)
                """, (pc_id,))

                # Clearances default template — checklist clearance-in & clearance-out
                # lengkap, dibedakan ASING (PKKA) vs DOMESTIK (PKK). Lihat
                # docs/playbook/lampiran/checklist-clearance-in-out.md.
                first_doc = ("PKKA / Pemberitahuan Kedatangan Kapal Asing"
                             if vessel_category == "FOREIGN" else "Warta Masuk / PKK")
                template = [
                    ("INAPORTNET_SIMPADU", first_doc,
                     "Warta kedatangan — PM 28/2022 Ps 4: paling lambat 1x24 jam sebelum tiba"),
                    ("BEA_CUKAI", "RKSP / BC 1.0",
                     "PMK 158/2017 jo PMK 97/2020: paling lambat 24 jam sebelum kedatangan"),
                    ("BEA_CUKAI", "Inward Manifest / BC 1.1",
                     "PMK 158/2017 jo PMK 97/2020: sebelum bongkar; manifes nihil bila tanpa muatan"),
                    ("KARANTINA_KESEHATAN", "MDH & Free Pratique",
                     "UU 17/2023 + PP 28/2024: sebelum kapal berhubungan dengan darat"),
                    ("IMIGRASI", "Immigration Crew List",
                     "UU 6/2011 Ps 18: pemberitahuan + crew list; shore pass via TPI"),
                    ("INAPORTNET_SPS", "SPOG Masuk",
                     "PM 28/2022: persetujuan olah gerak masuk oleh Syahbandar"),
                    ("BEA_CUKAI", "Outward Manifest / BC 1.1",
                     "PMK 158/2017 jo PMK 97/2020: sebelum keberangkatan"),
                    ("KARANTINA_KESEHATAN", "Port Health Clearance Out",
                     "UU 17/2023 + PP 28/2024: karantina keberangkatan"),
                    ("IMIGRASI", "Immigration Clearance Out",
                     "UU 6/2011: outward immigration clearance sebelum SPB"),
                    ("INAPORTNET_SPS", "Master Sailing Declaration",
                     "PM 28/2022 Ps 11: MSD syarat permohonan SPB elektronik"),
                    ("INAPORTNET_SPS", "SPOG Keluar",
                     "PM 28/2022: persetujuan olah gerak keluar oleh Syahbandar"),
                    ("INAPORTNET_SPS", "SPB (Surat Persetujuan Berlayar)",
                     "PM 28/2022 Ps 9: berlaku 24 jam, 1x pelayaran; diterbitkan saat clearance out"),
                ]
                conn.executemany(
                    "INSERT INTO clearances (port_call_id, agency, doc_type, status, remarks) "
                    "VALUES (?, ?, ?, 'PENDING', ?)",
                    [(pc_id, agency, doc_type, remarks) for agency, doc_type, remarks in template],
                )

            conn.close()
            self.send_json({"status": "success", "port_call_id": pc_id, "message": f"Port Call {pc_id} berhasil dibuat"})
            return

        if path.startswith("/api/clearances/"):
            parts = path.strip("/").split("/")
            # POST /api/clearances/<id>/update
            if len(parts) >= 3:
                clearance_id = parts[2]
                doc_number = payload.get("doc_number")
                status = payload.get("status")
                remarks = payload.get("remarks")

                conn = get_db()
                c = conn.cursor()
                c.execute("SELECT * FROM clearances WHERE id = ?", (clearance_id,))
                cl = c.fetchone()
                if not cl:
                    conn.close()
                    self.send_json({"error": "Dokumen clearance tidak ditemukan"}, status=404)
                    return

                new_doc_number = doc_number.strip() if doc_number is not None else cl["doc_number"]
                new_status = status if status is not None else cl["status"]
                new_remarks = remarks.strip() if remarks is not None else cl["remarks"]
                approval_time = cl["approval_time"]
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if new_status == "APPROVED" and not approval_time:
                    approval_time = now_str
                elif new_status != "APPROVED":
                    approval_time = None

                file_sha256 = cl["file_sha256"]
                if new_doc_number and (not file_sha256 or doc_number != cl["doc_number"]):
                    fake_content = f"{cl['port_call_id']}-{cl['doc_type']}-{new_doc_number}".encode("utf-8")
                    file_sha256 = calculate_file_sha256(fake_content)

                with conn:
                    conn.execute("""
                    UPDATE clearances
                    SET doc_number = ?, status = ?, remarks = ?, approval_time = ?, file_sha256 = ?
                    WHERE id = ?
                    """, (new_doc_number, new_status, new_remarks, approval_time, file_sha256, clearance_id))
                    log_audit(conn, cl["port_call_id"], payload.get("user", "ops_agent"), "CLEARANCE_UPDATE",
                              f"{cl['doc_type']} [{cl['status']}] {cl['doc_number'] or ''}",
                              f"{cl['doc_type']} [{new_status}] {new_doc_number or ''}")
                conn.close()
                self.send_json({"status": "success", "message": "Dokumen clearance berhasil diperbarui", "file_sha256": file_sha256})
                return

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
                    log_audit(conn, pc_id, recorded_by, f"MILESTONE_{code}",
                              None, f"{code} @ {event_time} — {remarks}")

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

            # POST /api/port-calls/<id>/spb — GATE: SPB hanya diterbitkan bila
            # prasyarat clearance sudah APPROVED (PM 28/2022 Ps 11; tanpa
            # persetujuan karantina kesehatan Syahbandar dilarang terbitkan SPB).
            if len(parts) == 4 and parts[3] == "spb":
                pc_id = parts[2]
                issue_time_str = payload.get("issued_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                doc_number = payload.get("doc_number", "SPB-ONLINE-001")
                try:
                    issue_dt = parse_iso(issue_time_str)
                except Exception:
                    self.send_json({"error": "Format issued_at tidak valid (YYYY-MM-DD HH:MM[:SS])"}, status=400)
                    return
                # PM 28/2022 Ps 9 ayat (1): Kedaluwarsa tepat 24 jam sejak diterbitkan
                expiry_dt = issue_dt + timedelta(hours=24)
                expiry_time_str = expiry_dt.strftime("%Y-%m-%d %H:%M:%S")

                conn = get_db()
                c = conn.cursor()
                c.execute("SELECT id FROM port_calls WHERE id = ?", (pc_id,))
                if not c.fetchone():
                    conn.close()
                    self.send_json({"error": "Port Call tidak ditemukan"}, status=404)
                    return
                c.execute("SELECT * FROM clearances WHERE port_call_id = ?", (pc_id,))
                blockers, can_issue = clearance_spb_gate([dict(r) for r in c.fetchall()])
                if not can_issue:
                    conn.close()
                    self.send_json({
                        "error": "SPB ditolak: prasyarat clearance belum terpenuhi",
                        "blockers": blockers, "can_issue_spb": False,
                    }, status=400)
                    return
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
                    log_audit(conn, pc_id, payload.get("user", "ops_agent"), "SPB_ISSUED",
                              None, f"SPB {doc_number} terbit {issue_time_str}, kedaluwarsa {expiry_time_str}")
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
                agency = payload.get("agency", "INAPORTNET_SIMPADU")
                doc_type = payload.get("doc_type", "Dokumen Tambahan")
                doc_number = (payload.get("doc_number") or "").strip()
                status = payload.get("status", "APPROVED")
                remarks = payload.get("remarks", "")
                
                file_sha256 = None
                if doc_number:
                    fake_file_content = f"{pc_id}-{doc_type}-{doc_number}".encode("utf-8")
                    file_sha256 = calculate_file_sha256(fake_file_content)

                doc_slug = (doc_type or "doc").lower().replace(' ', '_')
                conn = get_db()
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                approval_time = now_str if status == "APPROVED" else None
                with conn:
                    conn.execute("""
                    INSERT INTO clearances (port_call_id, agency, doc_type, doc_number, submission_time, approval_time, status, file_name, file_sha256, remarks, submitted_by)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (pc_id, agency, doc_type, doc_number, now_str, approval_time, status, f"{doc_slug}.pdf", file_sha256, remarks, "ops_agent"))
                    log_audit(conn, pc_id, payload.get("user", "ops_agent"), "CLEARANCE_ADD",
                              None, f"{agency} — {doc_type} [{status}] {doc_number}")
                conn.close()
                self.send_json({"status": "success", "file_sha256": file_sha256, "message": f"Dokumen {doc_type} berhasil dicatat"})
                return

            # POST /api/port-calls/<id>/disbursements
            if len(parts) == 4 and parts[3] == "disbursements":
                pc_id = parts[2]
                conn = get_db()
                c = conn.cursor()
                c.execute("SELECT id FROM port_calls WHERE id = ?", (pc_id,))
                if not c.fetchone():
                    conn.close()
                    self.send_json({"error": "Port Call tidak ditemukan"}, status=404)
                    return
                with conn:
                    dis_id, err = _insert_disbursement(conn, pc_id, payload)
                    if not err:
                        log_audit(conn, pc_id, payload.get("user", "ops_agent"), "DISBURSEMENT_ADD",
                                  None, f"{payload.get('category')} — {payload.get('item_name')}")
                conn.close()
                if err:
                    self.send_json({"error": err}, status=400)
                    return
                self.send_json({"status": "success", "disbursement_id": dis_id, "message": "Item PDA/FDA berhasil ditambahkan"})
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
                    log_audit(conn, pc_id, user_name, "CLOSE_OPS", None, f"Otorisasi 1 oleh {user_name}")
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
                    log_audit(conn, pc_id, user_name, "CLOSE_FINANCE", None, f"Otorisasi 2 oleh {user_name}")
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
    socketserver.TCPServer.allow_reuse_address = True  # harus sebelum bind, agar restart cepat tidak kena TIME_WAIT
    server = socketserver.TCPServer(("", port), AgencyAPIHandler)
    print(f"Server Keagenan Kapal berjalan di http://localhost:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer dihentikan.")
        server.server_close()


if __name__ == "__main__":
    p = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    run_server(p)
