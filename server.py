"""
Web Application & REST API Server Sistem Keagenan Kapal Nasional (Python stdlib).
Berdasarkan ADR-0002, ADR-0003, dan Putusan Q1 s.d. Q32.
"""

import http.server
import socketserver
import json
import urllib.parse
import io
import os
import sys
import threading
from datetime import datetime, timedelta
from decimal import Decimal
from email.message import Message as WSGIHeaders

from engine.db import get_db, init_db, seed_sample_data, log_audit, drop_remote_connection, TABLES, _upgrade_clearances_void
from engine.auth import (
    SESSION_COOKIE_NAME, SESSION_TTL_SECONDS,
    get_session_secret, make_session_cookie, verify_session_cookie,
    verify_password, is_login_blocked, record_login_failure, clear_login_failures,
)
from engine.laytime import LaytimeCalculator, parse_iso

PORT = 8000
# Batas ukuran body JSON per request (ADR-0016): semua payload sah di
# aplikasi ini hanya JSON kecil (form + daftar); tanpa batas, satu POST
# raksasa bisa menghabiskan memori proses.
MAX_BODY_BYTES = 1_000_000  # 1 MB

# Batas buang body saat menjawab 413 (ADR-0019 follow-up): body yang
# dibuang seluruhnya bila <= 8 MB; lebih dari itu server berhenti membaca
# dan menutup koneksi (client yang mengirim > 8 MB sudah menyalahgunakan
# batas 1 MB — RST untuknya dapat diterima).
_MAX_DRAIN_BYTES = 8 * 1024 * 1024  # 8 MB

def _drain_body(rfile, nbytes):
    """Baca dan buang `nbytes` byte dari body request, tanpa menyimpan.
    Dipakai sebelum menjawab 413 supaya client yang masih mengunggah
    tidak mendapat Broken pipe (lihat komentar di cabang 413)."""
    remaining = nbytes
    while remaining > 0:
        chunk = rfile.read(min(65536, remaining))
        if not chunk:
            break
        remaining -= len(chunk)

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
    # ADR-0020: baris VOID (batal karena salah input) dianggap TIDAK ADA
    # — kalau tidak, satu baris bersyarat yang salah ketik lalu di-void
    # tetap memblokir SPB ("ada tapi belum APPROVED") dan tidak ada jalan
    # keluar selain menyetujui baris yang salah itu.
    return any(_matches(r["doc_type"], r["agency"], category) and r["status"] != "VOID" for r in rows)


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
        # ADR-0020: VOID (batal karena salah input) bukan dokumen hidup.
        # Bila yang tersisa hanya baris void, tampilkan VOID apa adanya —
        # jangan jatuhkan ke "BELUM ADA" (itu menyembunyikan riwayat) dan
        # jangan tandai overdue (tidak ada yang menunggu persetujuan;
        # yang menunggu adalah baris pengganti).
        live = [r for r in matched if r["status"] != "VOID"]
        if live:
            approved = [r for r in live if r["status"] == "APPROVED"]
            chosen = approved[0] if approved else live[0]
            status = chosen["status"]
            doc_number = chosen["doc_number"]
            clearance_id = chosen["id"]
        elif matched:
            status = "VOID"
            doc_number = matched[0]["doc_number"]
            clearance_id = matched[0]["id"]
        else:
            status, doc_number, clearance_id = "BELUM ADA", None, None
        overdue = bool(deadline and now > deadline and status not in ("APPROVED", "VOID"))
        due_soon = bool(deadline and status not in ("APPROVED", "VOID") and not overdue
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


# Urutan status port call (ADR-0017): status hanya boleh MAJU. Milestone
# yang dicatat telat (mis. EOSP setelah kapal DEPARTED) tetap tersimpan
# sebagai fakta lapangan, tapi TIDAK boleh menarik status ke belakang —
# dulu EOSP selalu menulis ARRIVED dan ALL_FAST selalu BERTHED tanpa
# melihat status sekarang.
_STATUS_RANK = {
    "PRE_ARRIVAL": 0, "ARRIVED": 1, "BERTHED": 2, "DEPARTED": 3,
    "CLEARANCE_CLOSED": 4, "CLOSED": 5,
}


def _closed_lock_message(pc):
    """Pesan penolakan bila port call sudah CLOSED (arsip 10 tahun).

    Menyebut SIAPA menutup dan KAPAN, supaya petugas tahu ke siapa harus
    bertanya — bukan sekadar "ditolak". Mengembalikan None bila port call
    belum CLOSED (boleh diubah)."""
    d = pc if isinstance(pc, dict) else dict(pc)
    if d.get("finance_closed_at"):
        return (f"Port call sudah CLOSED oleh {d.get('finance_closed_by') or '?'} "
                f"pada {d['finance_closed_at']} — perubahan tidak diizinkan "
                f"(arsip 10 tahun terkunci)")
    return None


# -- Validasi input (ADR-0019) -------------------------------------------
# CHECK constraint di skema (engine/db.py) adalah pertahanan terakhir,
# bukan validasi: nilai yang lolos dari sini melempar IntegrityError di
# tengah transaksi dan — di server lokal — menutup koneksi tanpa respons
# JSON sama sekali (terbukti: status "approved" huruf kecil, agency
# "POLISI", milestone tanpa code, doc_number angka). Karena itu setiap
# nilai payload yang masuk ke kolom ber-CHECK / NOT NULL harus
# dinormalisasi + di-whitelist DI SINI, sebelum query apa pun.
# _BadRequest diubah menjadi 400 JSON oleh safety net di do_GET/do_POST.
class _BadRequest(Exception):
    """Validasi input gagal — safety net mengubahnya menjadi 400 JSON."""

CLEARANCE_STATUSES = ("PENDING", "SUBMITTED", "APPROVED", "REJECTED", "EXPIRED", "VOID")
CLEARANCE_AGENCIES = ("INAPORTNET_SIMPADU", "INAPORTNET_SPS", "BEA_CUKAI", "IMIGRASI", "KARANTINA_KESEHATAN", "BARANTIN", "BUP_PELINDO")
MILESTONE_CODES = ("EOSP", "ANCHORED", "NOR_TENDERED", "FREE_PRATIQUE", "PILOT_ON_BOARD", "FIRST_LINE", "ALL_FAST", "COMMENCED_CARGO", "COMPLETED_CARGO", "CAST_OFF", "SAILED")

def _norm_enum(value, allowed, field):
    """Normalisasi + whitelist nilai enum dari payload. None berarti tidak
    diberikan (pemanggil yang memutuskan: pakai nilai lama / wajib isi)."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise _BadRequest(f"{field} harus berupa teks")
    v = value.strip().upper()
    if v not in allowed:
        raise _BadRequest(f"{field} tidak dikenal: {value.strip()!r} — harus salah satu dari: {', '.join(allowed)}")
    return v

def _opt_text(value, field):
    """Teks opsional: None bila tidak diberikan, string ter-strip bila ada."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise _BadRequest(f"{field} harus berupa teks")
    return value.strip()

def _req_text(value, field):
    """Teks wajib: tidak boleh kosong dan harus berupa string."""
    v = _opt_text(value, field)
    if not v:
        raise _BadRequest(f"{field} wajib diisi")
    return v

def _strip_or_empty(value):
    """Untuk field yang dulu memakai `(payload.get(...) or "").strip()`:
    nilai non-string (mis. angka) dulu melempar AttributeError dan menutup
    koneksi — kini diperlakukan seperti tidak diisi, lalu validasi
    'wajib diisi' di bawahnya yang berbicara (400)."""
    return value.strip() if isinstance(value, str) else ""


def _insert_disbursement(conn, pc_id, payload):
    category = payload.get("category")
    item_name = _strip_or_empty(payload.get("item_name"))
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



LOGIN_HTML = """<!DOCTYPE html>
<html lang="id">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Masuk — Sistem Keagenan Kapal Nasional</title>
  <style>
    :root { --bg: #0f172a; --card-bg: #1e293b; --border: #334155;
            --primary: #2563eb; --text: #f8fafc; --text-muted: #94a3b8;
            --red: #ef4444; --cyan: #06b6d4; }
    * { box-sizing: border-box; margin: 0; padding: 0;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
    body { background: var(--bg); color: var(--text); min-height: 100vh;
           display: flex; align-items: center; justify-content: center; padding: 20px; }
    .card { background: var(--card-bg); border: 1px solid var(--border);
            border-radius: 8px; padding: 32px; width: 100%; max-width: 360px; }
    h1 { font-size: 18px; color: var(--cyan); margin-bottom: 4px; }
    p.sub { font-size: 12px; color: var(--text-muted); margin-bottom: 20px; }
    label { display: block; font-size: 12px; color: var(--text-muted); margin: 12px 0 4px; }
    input { width: 100%; padding: 10px; background: #0f172a; border: 1px solid var(--border);
            border-radius: 4px; color: #fff; font-size: 14px; }
    input:focus { outline: none; border-color: var(--cyan); }
    button { width: 100%; margin-top: 20px; padding: 10px; background: var(--primary);
             color: #fff; border: none; border-radius: 6px; font-size: 14px;
             font-weight: 600; cursor: pointer; }
    button:hover { background: #1d4ed8; }
    .err { display: none; margin-top: 12px; padding: 10px; border-radius: 6px;
           background: rgba(239,68,68,.15); border: 1px solid var(--red);
           color: #f87171; font-size: 13px; }
    .note { margin-top: 16px; font-size: 11px; color: var(--text-muted); text-align: center; }
  </style>
</head>
<body>
  <div class="card">
    <h1>&#9875; SISTEM KEAGENAN KAPAL NASIONAL</h1>
    <p class="sub">Masuk untuk mengakses tracker clearance &amp; disbursement.</p>
    <form id="login-form">
      <label for="username">Username</label>
      <input id="username" name="username" autocomplete="username" autofocus>
      <label for="password">Password</label>
      <input id="password" name="password" type="password" autocomplete="current-password">
      <div class="err" id="err"></div>
      <button type="submit">Masuk</button>
    </form>
    <p class="note">Tidak ada pendaftaran publik — akun dibuat admin lewat scripts/create_user.py.</p>
  </div>
  <script>
    document.getElementById('login-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const err = document.getElementById('err');
      err.style.display = 'none';
      const res = await fetch('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          username: document.getElementById('username').value,
          password: document.getElementById('password').value
        })
      });
      if (res.ok) { location.href = '/'; return; }
      const data = await res.json().catch(() => ({}));
      err.textContent = data.error || 'Login gagal';
      err.style.display = 'block';
    });
  </script>
</body>
</html>"""


class AgencyAPIHandler(http.server.SimpleHTTPRequestHandler):
    # -- Autentikasi (ADR-0011) -------------------------------------------
    # Session = cookie ter-sign HMAC (stateless, diverifikasi lokal tanpa
    # query DB). Semua route /api/* menolak tanpa session valid; halaman
    # utama "/" menampilkan halaman login bila belum login.

    def _session_user(self):
        """(username, role) dari cookie ter-sign, atau None bila tidak ada /
        tidak valid / kedaluwarsa. Melempar RuntimeError bila SESSION_SECRET
        belum diisi (ditangani pemanggil menjadi 500 dengan pesan jelas)."""
        return verify_session_cookie(self.headers.get("Cookie"))

    def _require_auth(self, roles=None):
        """Kembalikan (username, role) bila session valid (dan role cukup).
        Bila tidak, kirim 401/403/500 JSON dan kembalikan None — pemanggil
        wajib `return` bila hasilnya None."""
        try:
            session = self._session_user()
        except RuntimeError as exc:
            self.send_json({"error": str(exc)}, status=500)
            return None
        if session is None:
            self.send_json({"error": "Belum login — silakan login dulu"}, status=401)
            return None
        if roles is not None and session[1] not in roles:
            self.send_json(
                {"error": f"Role {session[1]} tidak boleh melakukan aksi ini "
                          f"(butuh {', '.join(roles)})"}, status=403)
            return None
        return session

    def _session_cookie_header(self, value, max_age):
        """Bangun header Set-Cookie. Secure hanya bila request datang lewat
        HTTPS (Vercel meneruskan X-Forwarded-Proto: https); lokal (HTTP)
        tanpa Secure supaya login tetap jalan di http://localhost."""
        parts = [f"{SESSION_COOKIE_NAME}={value}", "HttpOnly", "Path=/",
                 "SameSite=Lax", f"Max-Age={max_age}"]
        if (self.headers.get("X-Forwarded-Proto") or "").lower() == "https":
            parts.append("Secure")
        return "; ".join(parts)

    def _send_login_page(self):
        content = LOGIN_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self._security_headers()
        self.end_headers()
        self.wfile.write(content)

    def _security_headers(self):
        """Header keamanan default (ADR-0019, defense in depth — UI memakai
        innerHTML di beberapa tempat, sudah di-escape sejak ADR-0016).
        CSP ketat DITUNDA: UI memakai inline <script> + onclick, jadi CSP
        tanpa 'unsafe-inline' akan memecahkan UI; butuh ekstraksi JS dulu."""
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")

    def send_json(self, data, status=200, extra_headers=None):
        response_bytes = json.dumps(data, indent=2, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response_bytes)))
        self._security_headers()
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(response_bytes)

    def do_OPTIONS(self):
        # Tanpa header CORS: UI dilayani dari origin yang sama, jadi tidak
        # ada permintaan lintas origin yang sah. Access-Control-Allow-Origin
        # "*" sebelumnya tidak berguna untuk cookie (browser menolak
        # credential dengan "*") dan hanya menambah permukaan.
        self.send_response(204)
        self._security_headers()
        self.end_headers()

    def do_GET(self):
        # Safety net (ADR-0019): exception yang lolos dari handler TIDAK
        # boleh menutup koneksi tanpa respons (gejala probe [B] di review
        # keamanan) — ubah menjadi 500 JSON. _BadRequest dari validasi
        # input menjadi 400 JSON dengan pesan yang bisa ditampilkan UI.
        try:
            self._do_get_impl()
        except _BadRequest as exc:
            self._send_json_safe({"error": str(exc)}, status=400)
        except Exception:
            self._send_json_safe({"error": "Kesalahan internal server"}, status=500)

    def _send_json_safe(self, data, status):
        """Kirim respons JSON dari safety net — diam bila respons sudah
        terlanjur terkirim sebagian (header sudah jalan)."""
        try:
            self.send_json(data, status=status)
        except Exception:
            pass

    def _do_get_impl(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/":
            # Halaman utama: tampilkan login bila belum login (ADR-0011).
            try:
                session = self._session_user()
            except RuntimeError as exc:
                self.send_json({"error": str(exc)}, status=500)
                return
            if session is None:
                self._send_login_page()
            else:
                self.serve_ui()
            return

        if path == "/api/health":
            # Tanpa auth (ADR-0019): health check Vercel/monitoring.
            # Sengaja tanpa versi/stack trace — hanya status DB +
            # keberadaan SESSION_SECRET. 503 bila salah satunya gagal,
            # supaya load balancer / uptime check bisa bereaksi.
            try:
                conn = get_db()
                conn.execute("SELECT 1").fetchone()
                conn.close()
                db_ok = True
            except Exception:
                db_ok = False
            secret_ok = bool(os.environ.get("SESSION_SECRET"))
            ok = db_ok and secret_ok
            self.send_json(
                {"status": "ok" if ok else "degraded",
                 "db": "ok" if db_ok else "error",
                 "session_secret": "ok" if secret_ok else "missing"},
                status=200 if ok else 503)
            return

        if path == "/api/me":
            session = self._require_auth()
            if session is None:
                return
            username, role = session
            conn = get_db()
            row = conn.execute(
                "SELECT full_name FROM users WHERE username = ?", (username,)).fetchone()
            conn.close()
            self.send_json({"username": username, "role": role,
                            "full_name": row["full_name"] if row else username})
            return

        if path.startswith("/api/"):
            if self._require_auth() is None:
                return

        if path == "/api/shift-board":
            # Papan tugas shift (ADR-0017): satu layar untuk petugas jaga —
            # dokumen lewat tenggat, SPB sisa < 6 jam, kapal sudah sandar
            # tapi SPB belum diterbitkan, sengketa FDA masih OPEN. Datanya
            # sudah ada (checklist + spb_expiry_at + disputes); endpoint
            # ini hanya meringkasnya per port call supaya petugas tidak
            # perlu membuka satu per satu. Menggantikan notifikasi email
            # untuk tim kecil (Q21 belum dibangun).
            conn = get_db()
            c = conn.cursor()
            c.execute("""
            SELECT pc.*, v.name as vessel_name, v.flag as vessel_flag, p.name as port_name, pr.name as principal_name, b.name as branch_name
            FROM port_calls pc
            JOIN master_vessels v ON pc.vessel_imo = v.imo
            JOIN master_ports p ON pc.port_code = p.code
            JOIN master_principals pr ON pc.principal_id = pr.id
            JOIN master_branches b ON pc.branch_id = b.id
            WHERE pc.status != 'CLOSED'
            ORDER BY pc.created_at DESC
            """)
            pcs = [dict(r) for r in c.fetchall()]
            c.execute("SELECT * FROM clearances")
            clearances_by_pc = {}
            for r in c.fetchall():
                clearances_by_pc.setdefault(r["port_call_id"], []).append(dict(r))
            c.execute("""
            SELECT d.port_call_id, d.disputed_amount, dis.item_name
            FROM fda_disputes d
            JOIN disbursements dis ON d.disbursement_id = dis.id
            WHERE d.status = 'OPEN'
            """)
            disputes_by_pc = {}
            for r in c.fetchall():
                disputes_by_pc.setdefault(r["port_call_id"], []).append(
                    {"item_name": r["item_name"], "disputed_amount": r["disputed_amount"]})
            conn.close()

            now = datetime.now()
            cards = []
            totals = {"overdue_docs": 0, "spb_under_6h": 0, "spb_expired": 0,
                      "needs_spb": 0, "open_disputes": 0, "active_calls": len(pcs)}
            for pc in pcs:
                chk = build_clearance_checklist(pc, clearances_by_pc.get(pc["id"], []))
                overdue = [i["doc_type"] for i in chk["items"] if i["overdue"]]
                spb_state = "none"
                spb_hours_left = None
                if pc.get("spb_expiry_at"):
                    try:
                        delta = parse_iso(pc["spb_expiry_at"]) - now
                        spb_hours_left = round(delta.total_seconds() / 3600, 1)
                        if delta.total_seconds() <= 0:
                            spb_state = "expired"
                        elif delta <= timedelta(hours=6):
                            spb_state = "under_6h"
                        else:
                            spb_state = "ok"
                    except Exception:
                        spb_state = "none"
                needs_spb = pc["status"] == "BERTHED" and not pc.get("spb_issued_at")
                open_dis = disputes_by_pc.get(pc["id"], [])
                totals["overdue_docs"] += len(overdue)
                totals["spb_under_6h"] += 1 if spb_state == "under_6h" else 0
                totals["spb_expired"] += 1 if spb_state == "expired" else 0
                totals["needs_spb"] += 1 if needs_spb else 0
                totals["open_disputes"] += len(open_dis)
                cards.append({
                    "id": pc["id"], "vessel_name": pc["vessel_name"],
                    "port_name": pc["port_name"], "status": pc["status"],
                    "vessel_category": pc["vessel_category"],
                    "eta": pc.get("eta"), "etd": pc.get("etd"),
                    "overdue_docs": overdue,
                    "spb_state": spb_state, "spb_hours_left": spb_hours_left,
                    "needs_spb": needs_spb, "open_disputes": open_dis,
                })
            # Paling mendesak dulu: ada peringatan, lalu dokumen lewat
            # tenggat terbanyak, lalu SPB paling dekat kedaluwarsa.
            def _urgency(card):
                has_alert = bool(card["overdue_docs"] or card["spb_state"] in ("under_6h", "expired")
                                 or card["needs_spb"] or card["open_disputes"])
                return (0 if has_alert else 1, -len(card["overdue_docs"]),
                        card["spb_hours_left"] if card["spb_hours_left"] is not None else 9999)
            cards.sort(key=_urgency)
            self.send_json({"cards": cards, "totals": totals})
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

                # Riwayat SPB yang sudah digantikan (ADR-0017)
                c.execute("SELECT * FROM spb_history WHERE port_call_id = ? ORDER BY id DESC", (pc_id,))
                res["spb_history"] = [dict(r) for r in c.fetchall()]

                # Hasil laytime terakhir yang tersimpan (ADR-0017)
                c.execute("SELECT * FROM laytime_calculations WHERE port_call_id = ? ORDER BY id DESC LIMIT 1", (pc_id,))
                _lt = c.fetchone()
                res["latest_laytime"] = dict(_lt) if _lt else None

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
                    self.send_json({"error": "Port Call tidak ditemukan"}, status=404)
                    return

                # Pending clearances
                c.execute("SELECT doc_type, agency FROM clearances WHERE port_call_id = ? AND status != 'APPROVED' AND status != 'VOID'", (pc_id,))
                pending_docs = [f"{r['doc_type']} ({r['agency']})" for r in c.fetchall()]

                # Latest milestone
                c.execute("SELECT milestone_code, event_time, remarks FROM milestones WHERE port_call_id = ? ORDER BY id DESC LIMIT 1", (pc_id,))
                last_ms = c.fetchone()

                # Hasil laytime terakhir yang tersimpan (ADR-0017)
                c.execute("SELECT * FROM laytime_calculations WHERE port_call_id = ? ORDER BY id DESC LIMIT 1", (pc_id,))
                last_lt = c.fetchone()

                conn.close()

                lt_line = ("Tidak ada hitung laytime tersimpan" if not last_lt else
                           f"{last_lt['claim_type']} ${last_lt['amount_usd']:,.2f} — "
                           f"NOR {last_lt['nor_tendered_time']}, mulai {last_lt['commenced_time']}, "
                           f"selesai {last_lt['completed_time']} ({last_lt['terms']}, "
                           f"dihitung {last_lt['calculated_by']} {last_lt['calculated_at']})")
                handover_text = (
                    f"=== RINGKASAN HANDOVER SHIFT TIM KEAGENAN (24/7) ===\n"
                    f"Port Call ID: {pc_id}\n"
                    f"Status Kapal: {pc['status']} | Jenis: {pc['vessel_category']}\n"
                    f"Posisi/Terminal: {pc['terminal_name']}\n"
                    f"Peristiwa Terakhir: {last_ms['milestone_code'] if last_ms else 'Belum ada'} pada {last_ms['event_time'] if last_ms else '-'}\n"
                    f"Izin & Dokumen Tertunda: {', '.join(pending_docs) if pending_docs else 'Nihil / Semua Disetujui'}\n"
                    f"Laytime Terakhir: {lt_line}\n"
                    f"Peringatan Kritis: {'SPB Aktif! Perhatikan batas waktu 24 jam' if pc['spb_issued_at'] else 'Normal'}\n"
                    f"Waktu Dibuat: {datetime.now().strftime('%Y-%m-%d %H:%M:%S WIB')}\n"
                    f"======================================================"
                )
                self.send_json({"handover_text": handover_text})
                return

        self.send_json({"error": "Endpoint tidak ditemukan"}, status=404)

    def do_POST(self):
        # Safety net (ADR-0019) — lihat do_GET.
        try:
            self._do_post_impl()
        except _BadRequest as exc:
            self._send_json_safe({"error": str(exc)}, status=400)
        except Exception:
            self._send_json_safe({"error": "Kesalahan internal server"}, status=500)

    def _do_post_impl(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            self.send_json({"error": "Content-Length tidak valid"}, status=400)
            return
        if length > MAX_BODY_BYTES:
            # Drain dulu baru 413 (ADR-0019 follow-up): menjawab 413 TANPA
            # membaca body membuat server menutup koneksi dengan body yang
            # belum terbaca — kernel mengirim RST, dan client yang masih
            # mengunggah mendapat Broken pipe (BUKAN 413). Terbukti di
            # probe lokal: 15/30 POST body 1,1 MB gagal URLError, 0
            # traceback server; dan Tes 26 gagal dengan cara yang sama di
            # CI (suite sampai di print TEST 25 lalu crash). Drain dibatasi
            # _MAX_DRAIN_BYTES: overshoot wajar (< 8 MB) dapat 413 yang
            # bersih; body yang jauh lebih besar tetap ditutup begitu saja
            # (RST) — itu penyalahgunaan, bukan client normal.
            _drain_body(self.rfile, min(length, _MAX_DRAIN_BYTES))
            self.send_json({"error": f"Body terlalu besar (maks {MAX_BODY_BYTES // 1024} KB)"}, status=413)
            return
        # Body yang bukan JSON valid dulu DITELAN diam-diam (payload = {})
        # lalu request jalan dengan nilai default — laytime tanpa body malah
        # menyimpan tanggal demo sebagai fakta (ADR-0019). Kini 400 eksplisit.
        try:
            body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        except UnicodeDecodeError:
            self.send_json({"error": "Body harus berupa teks UTF-8 yang valid"}, status=400)
            return
        try:
            payload = json.loads(body)
        except Exception:
            self.send_json({"error": "Body harus berupa JSON valid"}, status=400)
            return
        if not isinstance(payload, dict):
            self.send_json({"error": "Body JSON harus berupa objek"}, status=400)
            return

        # -- Login (publik, rate-limited) ---------------------------------
        if path == "/api/login":
            username = _strip_or_empty(payload.get("username"))
            password = payload.get("password") or ""
            if not username or not password:
                self.send_json({"error": "Username dan password wajib diisi"}, status=400)
                return
            # IP klien untuk rate limit (ADR-0016): hop pertama
            # X-Forwarded-For bila di belakang proxy (Vercel), kalau tidak
            # alamat koneksi langsung. Kunci per (username, IP) supaya satu
            # penyerang tidak bisa mengunci akun orang lain. getattr karena
            # jalur WSGI tidak punya socket (client_address diisi dari
            # REMOTE_ADDR di _WSGIRequest; "unknown" bila tetap tak ada).
            xff = self.headers.get("X-Forwarded-For", "")
            if xff:
                client_ip = xff.split(",")[0].strip()
            else:
                _addr = getattr(self, "client_address", None)
                client_ip = _addr[0] if _addr else "unknown"
            if is_login_blocked(username, client_ip):
                self.send_json(
                    {"error": "Terlalu banyak percobaan login — coba lagi 15 menit lagi"},
                    status=429)
                return
            conn = get_db()
            row = conn.execute(
                "SELECT username, password_hash, full_name, role, active "
                "FROM users WHERE username = ?", (username,)).fetchone()
            conn.close()
            # Pesan gagal disamakan untuk semua kasus (user tidak ada /
            # nonaktif / password salah) supaya tidak membocorkan user mana
            # yang terdaftar.
            if row is None or not row["active"] or not verify_password(password, row["password_hash"]):
                record_login_failure(username, client_ip)
                self.send_json({"error": "Username atau password salah"}, status=401)
                return
            clear_login_failures(username, client_ip)
            try:
                cookie_value = make_session_cookie(row["username"], row["role"])
            except RuntimeError as exc:
                self.send_json({"error": str(exc)}, status=500)
                return
            self.send_json(
                {"status": "success", "username": row["username"],
                 "full_name": row["full_name"], "role": row["role"]},
                extra_headers={"Set-Cookie": self._session_cookie_header(
                    cookie_value, SESSION_TTL_SECONDS)})
            return

        # -- Logout (publik; cukup hapus cookie) ---------------------------
        if path == "/api/logout":
            self.send_json({"status": "success", "message": "Berhasil keluar"},
                           extra_headers={"Set-Cookie": self._session_cookie_header("", 0)})
            return

        # -- Semua POST lain butuh session valid (ADR-0011) ----------------
        # Actor untuk audit/close diambil dari SESSION, bukan dari payload
        # client — menutup lubang pemalsuan user_id di audit_logs.
        session = self._require_auth()
        if session is None:
            return
        actor, actor_role = session

        if path == "/api/master/vessels":
            imo = _strip_or_empty(payload.get("imo"))
            name = _strip_or_empty(payload.get("name"))
            flag = _strip_or_empty(payload.get("flag"))
            vessel_type = _strip_or_empty(payload.get("vessel_type")) or "General Cargo"
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
            c.execute("SELECT id, finance_closed_at, finance_closed_by FROM port_calls WHERE id = ?", (pc_id,))
            _pc = c.fetchone()
            if not _pc:
                conn.close()
                self.send_json({"error": "Port Call tidak ditemukan"}, status=404)
                return
            lock_msg = _closed_lock_message(_pc)
            if lock_msg:
                conn.close()
                self.send_json({"error": lock_msg}, status=400)
                return
            with conn:
                dis_id, err = _insert_disbursement(conn, pc_id, payload)
                if not err:
                    log_audit(conn, pc_id, actor, "DISBURSEMENT_ADD",
                              None, f"{payload.get('category')} — {payload.get('item_name')}")
            conn.close()
            if err:
                self.send_json({"error": err}, status=400)
                return
            self.send_json({"status": "success", "disbursement_id": dis_id, "message": "Item PDA/FDA berhasil ditambahkan"})
            return

        if path.startswith("/api/disbursements/"):
            parts = path.strip("/").split("/")
            # POST /api/disbursements/<id> — lengkapi baris PDA yang sudah
            # ada (ADR-0017): di lapangan PDA dicatat dulu, NTPN dan
            # realisasi FDA datang kemudian. Dulu satu-satunya jalan
            # adalah menambah baris BARU, sehingga rekonsiliasi dobel.
            # Kini fda_actual, NTPN, dan nomor billing diubah pada item
            # yang sama (pda_estimate tidak disentuh).
            if len(parts) >= 3:
                dis_id = parts[2]
                conn = get_db()
                c = conn.cursor()
                c.execute("SELECT * FROM disbursements WHERE id = ?", (dis_id,))
                dis = c.fetchone()
                if not dis:
                    conn.close()
                    self.send_json({"error": "Item biaya tidak ditemukan"}, status=404)
                    return
                c.execute("SELECT finance_closed_at, finance_closed_by FROM port_calls WHERE id = ?", (dis["port_call_id"],))
                _pc = c.fetchone()
                lock_msg = _closed_lock_message(_pc) if _pc else None
                if lock_msg:
                    conn.close()
                    self.send_json({"error": lock_msg}, status=400)
                    return
                new_fda = dis["fda_actual"]
                if "fda_actual" in payload:
                    try:
                        new_fda = float(payload.get("fda_actual"))
                    except (TypeError, ValueError):
                        conn.close()
                        self.send_json({"error": "fda_actual harus angka (IDR)"}, status=400)
                        return
                    if new_fda < 0:
                        conn.close()
                        self.send_json({"error": "fda_actual tidak boleh negatif"}, status=400)
                        return
                new_ntpn = payload.get("ntpn", dis["ntpn"])
                new_billing = payload.get("simponi_billing", dis["simponi_billing"])
                new_invoice = payload.get("invoice_number", dis["invoice_number"])
                if (new_fda == dis["fda_actual"] and new_ntpn == dis["ntpn"]
                        and new_billing == dis["simponi_billing"] and new_invoice == dis["invoice_number"]):
                    conn.close()
                    self.send_json({"error": "Tidak ada perubahan (isi fda_actual, NTPN, atau nomor billing)"}, status=400)
                    return
                with conn:
                    conn.execute("""
                    UPDATE disbursements SET fda_actual = ?, ntpn = ?, simponi_billing = ?, invoice_number = ? WHERE id = ?
                    """, (new_fda, new_ntpn, new_billing, new_invoice, dis_id))
                    log_audit(conn, dis["port_call_id"], actor, "DISBURSEMENT_UPDATE",
                              f"{dis['item_name']}: FDA {dis['fda_actual']} / NTPN {dis['ntpn'] or '-'} / Bill {dis['simponi_billing'] or '-'}",
                              f"{dis['item_name']}: FDA {new_fda} / NTPN {new_ntpn or '-'} / Bill {new_billing or '-'}")
                conn.close()
                self.send_json({"status": "success", "message": "Item PDA/FDA berhasil dilengkapi"})
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
            try:
                cargo_tonnage = float(payload.get("cargo_tonnage", 10000))
            except (TypeError, ValueError):
                # ADR-0016: dulu float() yang gagal melempar ke 500.
                self.send_json({"error": "cargo_tonnage harus angka"}, status=400)
                return

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
            # WHITELIST ONLY (ADR-0019): f-string di bawah HANYA
            # menginterpolasi nama tabel/kolom dari tuple konstan di
            # bawah ini — nilai dari payload/path TIDAK PERNAH masuk ke
            # sana (nilai selalu lewat placeholder `?`). Jangan ubah pola
            # ini: bila tabel/kolom baru ditambah, tambahkan ke tuple.
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

            # Generate ID: PC-YYYY-PORT-XXXX (ADR-0016): dulu
            # COUNT(*)+1 di luar transaksi — dua request bersamaan bisa
            # mendapat nomor yang sama (satu gagal 500) dan nomor bisa
            # dipakai ulang bila port call lama dihapus. Kini MAX+1
            # dengan retry bila primary key bentrok; PK menjamin dua ID
            # sama tidak pernah sama-sama lolos — yang kalah mengulang
            # dengan nomor baru.
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            pc_id = None
            for _attempt in range(5):
                year_str = datetime.now().strftime("%Y")
                c.execute(
                    "SELECT COALESCE(MAX(CAST(SUBSTR(id, -4) AS INTEGER)), 0) "
                    "FROM port_calls WHERE id LIKE ?",
                    (f"PC-{year_str}-%",))
                count = c.fetchone()[0] + 1
                pc_id = f"PC-{year_str}-{port_code[2:]}-{count:04d}"
                try:
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
                    break
                except Exception as exc:
                    if "constraint failed" not in str(exc):
                        raise
                    if _attempt == 4:
                        conn.close()
                        self.send_json({"error": "Gagal membuat port call karena bentrok nomor — coba lagi"}, status=500)
                        return
                    continue


            conn.close()
            self.send_json({"status": "success", "port_call_id": pc_id, "message": f"Port Call {pc_id} berhasil dibuat"})
            return

        if path.startswith("/api/milestones/"):
            parts = path.strip("/").split("/")
            # POST /api/milestones/<id> — koreksi waktu/catatan milestone
            # yang sudah ada (ADR-0017). Beda dengan mencatat milestone
            # baru: koreksi TIDAK PERNAH mengubah status kapal (status
            # hanya maju lewat milestone baru). Waktu baru harus tetap
            # di antara milestone sebelum dan sesudahnya, supaya urutan
            # SOF tidak rusak dari arah lain.
            if len(parts) >= 3:
                milestone_id = parts[2]
                conn = get_db()
                c = conn.cursor()
                c.execute("SELECT * FROM milestones WHERE id = ?", (milestone_id,))
                ms = c.fetchone()
                if not ms:
                    conn.close()
                    self.send_json({"error": "Milestone tidak ditemukan"}, status=404)
                    return
                c.execute("SELECT finance_closed_at, finance_closed_by FROM port_calls WHERE id = ?", (ms["port_call_id"],))
                _pc = c.fetchone()
                lock_msg = _closed_lock_message(_pc) if _pc else None
                if lock_msg:
                    conn.close()
                    self.send_json({"error": lock_msg}, status=400)
                    return
                new_time = payload.get("event_time")
                new_remarks = payload.get("remarks")
                if new_time is not None:
                    try:
                        parse_iso(new_time)
                    except Exception as exc:
                        conn.close()
                        self.send_json({"error": f"Waktu milestone tidak valid: {exc}. Pakai format YYYY-MM-DD HH:MM."}, status=400)
                        return
                    c.execute("SELECT MAX(event_time) FROM milestones WHERE port_call_id = ? AND event_time < ? AND id != ?",
                              (ms["port_call_id"], ms["event_time"], milestone_id))
                    prev_time = c.fetchone()[0]
                    c.execute("SELECT MIN(event_time) FROM milestones WHERE port_call_id = ? AND event_time > ? AND id != ?",
                              (ms["port_call_id"], ms["event_time"], milestone_id))
                    next_time = c.fetchone()[0]
                    if prev_time and new_time < prev_time:
                        conn.close()
                        self.send_json({"error": f"Waktu baru ({new_time}) lebih awal dari milestone sebelumnya ({prev_time}) — urutan SOF harus kronologis"}, status=400)
                        return
                    if next_time and new_time > next_time:
                        conn.close()
                        self.send_json({"error": f"Waktu baru ({new_time}) lebih lambat dari milestone berikutnya ({next_time}) — urutan SOF harus kronologis"}, status=400)
                        return
                if new_time is None and new_remarks is None:
                    conn.close()
                    self.send_json({"error": "Tidak ada perubahan (isi event_time atau remarks)"}, status=400)
                    return
                with conn:
                    conn.execute("""
                    UPDATE milestones SET event_time = ?, remarks = ? WHERE id = ?
                    """, (new_time if new_time is not None else ms["event_time"],
                          new_remarks if new_remarks is not None else ms["remarks"],
                          milestone_id))
                    log_audit(conn, ms["port_call_id"], actor, "MILESTONE_UPDATE",
                              f"{ms['milestone_code']} @ {ms['event_time']} — {ms['remarks'] or ''}",
                              f"{ms['milestone_code']} @ {new_time or ms['event_time']} — {new_remarks if new_remarks is not None else (ms['remarks'] or '')} (koreksi waktu; status kapal tidak berubah)")
                conn.close()
                self.send_json({"status": "success", "message": "Milestone berhasil dikoreksi (status kapal tidak berubah)"})
                return

        if path.startswith("/api/disputes/"):
            parts = path.strip("/").split("/")
            # POST /api/disputes/<id>/resolve — mencatat resolusi sengketa
            # FDA (ADR-0017). SATU-SATUNYA pengecualian kunci CLOSED:
            # finance boleh menyelesaikan sengketa yang sudah terbuka
            # meski port call sudah CLOSED, karena sengketa bisa berlanjut
            # setelah arsip ditutup (Q32: deadline respon & negosiasi).
            if len(parts) == 4 and parts[3] == "resolve":
                dispute_id = parts[2]
                if actor_role not in ("FINANCE", "ADMIN"):
                    self.send_json({"error": "Resolusi sengketa hanya boleh dilakukan role FINANCE atau ADMIN"}, status=403)
                    return
                new_status = payload.get("status")
                resolution_notes = _strip_or_empty(payload.get("resolution_notes"))
                if new_status not in ("UNDER_REVIEW", "ACCEPTED_REVISED", "REJECTED_FINAL"):
                    self.send_json({"error": "status resolusi wajib salah satu dari: UNDER_REVIEW, ACCEPTED_REVISED, REJECTED_FINAL"}, status=400)
                    return
                conn = get_db()
                c = conn.cursor()
                c.execute("SELECT * FROM fda_disputes WHERE id = ?", (dispute_id,))
                dp = c.fetchone()
                if not dp:
                    conn.close()
                    self.send_json({"error": "Sengketa tidak ditemukan"}, status=404)
                    return
                if dp["status"] in ("ACCEPTED_REVISED", "REJECTED_FINAL"):
                    conn.close()
                    self.send_json({"error": f"Sengketa sudah diselesaikan ({dp['status']}) — tidak bisa diubah lagi"}, status=400)
                    return
                with conn:
                    conn.execute("""
                    UPDATE fda_disputes SET status = ?, resolution_notes = ? WHERE id = ?
                    """, (new_status, resolution_notes or dp["resolution_notes"], dispute_id))
                    log_audit(conn, dp["port_call_id"], actor, "DISPUTE_RESOLVE",
                              f"Sengketa #{dispute_id} [{dp['status']}]",
                              f"Sengketa #{dispute_id} [{new_status}] — {resolution_notes or ''}")
                conn.close()
                self.send_json({"status": "success", "message": f"Sengketa #{dispute_id} dicatat sebagai {new_status}"})
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
                # Kunci CLOSED (ADR-0017).
                c.execute("SELECT finance_closed_at, finance_closed_by FROM port_calls WHERE id = ?", (cl["port_call_id"],))
                _pc = c.fetchone()
                lock_msg = _closed_lock_message(_pc) if _pc else None
                if lock_msg:
                    conn.close()
                    self.send_json({"error": lock_msg}, status=400)
                    return
                # ADR-0020: baris VOID sudah mati — final, tidak bisa
                # diubah lagi (termasuk "menghidupkannya" kembali).
                # Koreksi = tambah baris baru; riwayat void tetap ada.
                if cl["status"] == "VOID":
                    conn.close()
                    self.send_json({"error": "Dokumen sudah VOID (batal karena salah input) — tidak bisa diubah lagi; tambah baris baru bila perlu"}, status=400)
                    return

                # Validasi (ADR-0019): status dinormalisasi + di-whitelist
                # SEBELUM UPDATE — nilai di luar daftar dulu melempar
                # IntegrityError (CHECK) dan menutup koneksi tanpa respons.
                _doc = _opt_text(doc_number, "doc_number")
                new_doc_number = _doc if _doc is not None else cl["doc_number"]
                _st = _norm_enum(status, CLEARANCE_STATUSES, "status")
                new_status = _st if _st is not None else cl["status"]
                _rem = _opt_text(remarks, "remarks")
                new_remarks = _rem if _rem is not None else cl["remarks"]
                # ADR-0020: mem-void hanya OPS/ADMIN — ini tindakan yang
                # menghapus blocker gate, jadi tidak untuk FINANCE.
                if new_status == "VOID" and actor_role not in ("OPS", "ADMIN"):
                    conn.close()
                    self.send_json({"error": "Hanya OPS/ADMIN yang boleh mem-void (membatalkan) dokumen"}, status=403)
                    return
                approval_time = cl["approval_time"]
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if new_status == "APPROVED" and not approval_time:
                    approval_time = now_str
                elif new_status != "APPROVED":
                    approval_time = None

                file_sha256 = cl["file_sha256"]
                # ADR-0016: JANGAN membuat hash dari string referensi
                # (port_call_id-doc_type-doc_number) lalu menampilkannya
                # sebagai "bukti integritas" — hash seperti itu tidak
                # membuktikan apa pun tentang berkas, karena tidak ada
                # berkas yang diunggah. file_sha256 hanya terisi bila ada
                # upload berkas sungguhan (belum ada fiturnya); sampai saat
                # itu nilainya tetap seperti di DB (NULL untuk data baru).

                with conn:
                    conn.execute("""
                    UPDATE clearances
                    SET doc_number = ?, status = ?, remarks = ?, approval_time = ?, file_sha256 = ?
                    WHERE id = ?
                    """, (new_doc_number, new_status, new_remarks, approval_time, file_sha256, clearance_id))
                    log_audit(conn, cl["port_call_id"], actor, "CLEARANCE_UPDATE",
                              f"{cl['doc_type']} [{cl['status']}] {cl['doc_number'] or ''}",
                              f"{cl['doc_type']} [{new_status}] {new_doc_number or ''}")
                conn.close()
                _msg = ("Dokumen di-VOID (batal) — baris tetap tersimpan sebagai riwayat dan diabaikan gate SPB"
                        if new_status == "VOID" else "Dokumen clearance berhasil diperbarui")
                self.send_json({"status": "success", "message": _msg, "file_sha256": file_sha256})
                return

        if path.startswith("/api/port-calls/"):
            parts = path.strip("/").split("/")

            # POST /api/port-calls/<id>/update — koreksi ETA/ETD/muatan
            # (ADR-0017): tenggat RKSP (ETA-24 jam) dan permohonan SPB
            # (ETD-4 jam) dihitung dari dua kolom ini, tapi dulu tidak ada
            # cara mengubahnya setelah port call dibuat — padahal ETA
            # geser adalah kejadian harian. Sekali diubah, checklist
            # dihitung ulang dari nilai baru (dikembalikan di respons)
            # dan perubahannya masuk audit.
            if len(parts) == 4 and parts[3] == "update":
                pc_id = parts[2]
                if actor_role not in ("OPS", "ADMIN"):
                    self.send_json({"error": "Koreksi ETA/ETD/muatan hanya boleh dilakukan role OPS atau ADMIN"}, status=403)
                    return
                conn = get_db()
                c = conn.cursor()
                c.execute("SELECT * FROM port_calls WHERE id = ?", (pc_id,))
                pc = c.fetchone()
                if not pc:
                    conn.close()
                    self.send_json({"error": "Port Call tidak ditemukan"}, status=404)
                    return
                lock_msg = _closed_lock_message(pc)
                if lock_msg:
                    conn.close()
                    self.send_json({"error": lock_msg}, status=400)
                    return
                new_eta = payload.get("eta", pc["eta"])
                new_etd = payload.get("etd", pc["etd"])
                new_cargo_name = payload.get("cargo_name", pc["cargo_name"])
                new_tonnage = pc["cargo_tonnage"]
                if "cargo_tonnage" in payload:
                    try:
                        new_tonnage = float(payload.get("cargo_tonnage"))
                    except (TypeError, ValueError):
                        conn.close()
                        self.send_json({"error": "cargo_tonnage harus angka"}, status=400)
                        return
                    if new_tonnage < 0:
                        conn.close()
                        self.send_json({"error": "cargo_tonnage tidak boleh negatif"}, status=400)
                        return
                for _field, _val in (("eta", new_eta), ("etd", new_etd)):
                    if _val:
                        try:
                            parse_iso(_val)
                        except Exception as exc:
                            conn.close()
                            self.send_json({"error": f"Waktu {_field} tidak valid: {exc}. Pakai format YYYY-MM-DD HH:MM."}, status=400)
                            return
                if new_eta and new_etd and parse_iso(new_etd) < parse_iso(new_eta):
                    conn.close()
                    self.send_json({"error": "ETD tidak boleh lebih awal dari ETA"}, status=400)
                    return
                if (new_eta == pc["eta"] and new_etd == pc["etd"]
                        and new_cargo_name == pc["cargo_name"] and new_tonnage == pc["cargo_tonnage"]):
                    conn.close()
                    self.send_json({"error": "Tidak ada perubahan (isi ETA, ETD, muatan, atau tonase)"}, status=400)
                    return
                with conn:
                    conn.execute("""
                    UPDATE port_calls SET eta = ?, etd = ?, cargo_name = ?, cargo_tonnage = ?, updated_at = ? WHERE id = ?
                    """, (new_eta, new_etd, new_cargo_name, new_tonnage,
                          datetime.now().strftime("%Y-%m-%d %H:%M:%S"), pc_id))
                    log_audit(conn, pc_id, actor, "PORT_CALL_UPDATE",
                              f"ETA {pc['eta']} / ETD {pc['etd'] or '-'} / {pc['cargo_name']} ({pc['cargo_tonnage']} MT)",
                              f"ETA {new_eta} / ETD {new_etd or '-'} / {new_cargo_name} ({new_tonnage} MT)")
                c.execute("SELECT * FROM clearances WHERE port_call_id = ? ORDER BY id ASC", (pc_id,))
                clearances = [dict(r) for r in c.fetchall()]
                c.execute("SELECT * FROM port_calls WHERE id = ?", (pc_id,))
                updated = dict(c.fetchone())
                conn.close()
                self.send_json({"status": "success",
                                "message": "ETA/ETD/muatan berhasil dikoreksi — checklist dihitung ulang",
                                "port_call": updated,
                                "checklist": build_clearance_checklist(updated, clearances)})
                return

            # POST /api/port-calls/<id>/milestone
            if len(parts) == 4 and parts[3] == "milestone":
                pc_id = parts[2]
                # Validasi (ADR-0019): kode wajib + di-whitelist — dulu
                # milestone tanpa code melempar IntegrityError (NOT NULL)
                # dan menutup koneksi; kode sembarang juga lolos ke SOF.
                code = _norm_enum(payload.get("milestone_code"), MILESTONE_CODES, "milestone_code")
                if code is None:
                    raise _BadRequest("milestone_code wajib diisi — harus salah satu dari: " + ", ".join(MILESTONE_CODES))
                event_time = payload.get("event_time", datetime.now().strftime("%Y-%m-%d %H:%M"))
                recorded_by = actor  # dari session, bukan payload
                remarks = _opt_text(payload.get("remarks"), "remarks") or ""

                conn = get_db()
                c = conn.cursor()
                c.execute("SELECT id, status, spb_issued_at, spb_expiry_at, finance_closed_at, finance_closed_by FROM port_calls WHERE id = ?", (pc_id,))
                pc = c.fetchone()
                if not pc:
                    conn.close()
                    self.send_json({"error": "Port Call tidak ditemukan"}, status=404)
                    return
                # Kunci CLOSED (ADR-0017): arsip yang sudah CLOSED tidak
                # boleh diubah lagi — sebelumnya milestone masih bisa masuk
                # dan "arsip 10 tahun terkunci" baru label di layar.
                lock_msg = _closed_lock_message(pc)
                if lock_msg:
                    conn.close()
                    self.send_json({"error": lock_msg}, status=400)
                    return
                # Validasi waktu (ADR-0017): dulu event_time apa pun
                # diterima mentah-mentah dan merusak urutan SOF; kini
                # format salah ditolak 400 yang menyebut format benar.
                try:
                    parse_iso(event_time)
                except Exception as exc:
                    conn.close()
                    self.send_json({"error": f"Waktu milestone tidak valid: {exc}. Pakai format YYYY-MM-DD HH:MM."}, status=400)
                    return
                # GATE berangkat (PM 28/2022): CAST_OFF/SAILED hanya boleh
                # mengubah status jadi DEPARTED bila SPB sudah terbit dan
                # belum kedaluwarsa (24 jam, Ps 9 ayat (1)). Tanpa SPB yang
                # valid, kapal belum boleh berlayar — milestone tetap dicatat
                # sebagai fakta lapangan, tapi status TIDAK berubah.
                spb_blocker = None
                if code in ("CAST_OFF", "SAILED"):
                    if not pc["spb_issued_at"]:
                        spb_blocker = "SPB belum terbit — kapal belum boleh berlayar (PM 28/2022)"
                    elif pc["spb_expiry_at"] and datetime.now() > parse_iso(pc["spb_expiry_at"]):
                        spb_blocker = f"SPB sudah kedaluwarsa {pc['spb_expiry_at']} — terbitkan SPB baru sebelum berlayar"
                # Waktu milestone terakhir — untuk memberi tahu bila entri
                # baru dicatat di luar urutan kronologis (koreksi telat).
                c.execute("SELECT MAX(event_time) FROM milestones WHERE port_call_id = ?", (pc_id,))
                latest_time = c.fetchone()[0]
                with conn:
                    conn.execute("""
                    INSERT INTO milestones (port_call_id, milestone_code, event_time, recorded_by, remarks)
                    VALUES (?, ?, ?, ?, ?)
                    """, (pc_id, code, event_time, recorded_by, remarks))
                    log_audit(conn, pc_id, recorded_by, f"MILESTONE_{code}",
                              None, f"{code} @ {event_time} — {remarks}"
                              + (f" [DITOLAK jadi DEPARTED: {spb_blocker}]" if spb_blocker else ""))

                    # Update status port call jika milestone kunci — TAPI
                    # hanya MAJU (ADR-0017). Dulu EOSP selalu menulis
                    # ARRIVED dan ALL_FAST selalu BERTHED, sehingga kapal
                    # yang sudah DEPARTED bisa kembali ke ARRIVED hanya
                    # karena seseorang mencatat EOSP yang telat.
                    new_status = None
                    if code in ("EOSP", "ANCHORED"):
                        new_status = "ARRIVED"
                    elif code in ("FIRST_LINE", "ALL_FAST", "COMMENCED_CARGO"):
                        new_status = "BERTHED"
                    elif code in ("CAST_OFF", "SAILED") and not spb_blocker:
                        new_status = "DEPARTED"

                    status_moved = False
                    if new_status and _STATUS_RANK[new_status] > _STATUS_RANK[pc["status"]]:
                        status_moved = True
                        if new_status == "DEPARTED":
                            # atd selama ini tidak pernah diisi; isi dari
                            # event_time milestone keberangkatan.
                            conn.execute("UPDATE port_calls SET status = ?, atd = ?, updated_at = ? WHERE id = ?", (new_status, event_time, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), pc_id))
                        else:
                            conn.execute("UPDATE port_calls SET status = ?, updated_at = ? WHERE id = ?", (new_status, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), pc_id))

                conn.close()
                if spb_blocker:
                    self.send_json({"error": spb_blocker, "milestone_recorded": True,
                                    "status": pc["status"]}, status=400)
                    return
                # Catatan untuk UI bila milestone tercatat tapi status tidak
                # bergerak, atau waktunya lebih awal dari milestone
                # terakhir (koreksi telat — dicatat sebagai fakta, status
                # tetap). Tanpa catatan ini petugas tidak tahu mengapa
                # status "tidak berubah" setelah ia menyimpan.
                note = None
                if new_status and not status_moved:
                    note = (f"Milestone dicatat, tetapi status tetap {pc['status']} — "
                            f"status hanya maju ({new_status} tidak menarik status ke belakang).")
                if latest_time and event_time < latest_time:
                    oo = (f"Waktu milestone ({event_time}) lebih awal dari milestone "
                          f"terakhir ({latest_time}) — dicatat sebagai koreksi; "
                          f"urutan SOF di layar tetap diurutkan berdasarkan waktu.")
                    note = f"{note} {oo}" if note else oo
                self.send_json({"status": "success", "message": f"Milestone {code} dicatat",
                                "note": note})
                return

            # POST /api/port-calls/<id>/stoppage
            if len(parts) == 4 and parts[3] == "stoppage":
                pc_id = parts[2]
                conn = get_db()
                c = conn.cursor()
                c.execute("SELECT id, finance_closed_at, finance_closed_by FROM port_calls WHERE id = ?", (pc_id,))
                _pc = c.fetchone()
                if not _pc:
                    conn.close()
                    self.send_json({"error": "Port Call tidak ditemukan"}, status=404)
                    return
                lock_msg = _closed_lock_message(_pc)
                if lock_msg:
                    conn.close()
                    self.send_json({"error": lock_msg}, status=400)
                    return
                # Validasi SEBELUM insert (ADR-0015): sebelumnya nilai
                # rusak langsung melempar ke 500/koneksi putus — float()
                # gagal, CHECK kategori gagal, FK gagal, dan waktu salah
                # format tersimpan diam-diam lalu merusak hitung laytime.
                category = payload.get("category", "OTHER")
                if category not in ("RAIN", "WEATHER_OTHER", "MEAL_BREAK", "SHIFT_CHANGE",
                                    "CRANE_BREAKDOWN_SHORE", "WINCH_BREAKDOWN_SHIP",
                                    "CARGO_WAITING", "SHIFTING_BERTH", "OTHER"):
                    conn.close()
                    self.send_json({"error": f"Kategori jeda tidak dikenal: {category}"}, status=400)
                    return
                try:
                    pct = float(payload.get("percentage_counted", 0))
                except (TypeError, ValueError):
                    conn.close()
                    self.send_json({"error": "Persentase jeda harus angka (0-100)"}, status=400)
                    return
                for _field, _val in (("start_time", payload.get("start_time")), ("end_time", payload.get("end_time"))):
                    if not _val:
                        conn.close()
                        self.send_json({"error": f"Waktu {_field} wajib diisi (format YYYY-MM-DD HH:MM)"}, status=400)
                        return
                    try:
                        parse_iso(_val)
                    except Exception as exc:
                        conn.close()
                        self.send_json({"error": f"Waktu {_field} tidak valid: {exc}. Pakai format YYYY-MM-DD HH:MM."}, status=400)
                        return
                # Selesai harus sesudah mulai (ADR-0017): dulu jeda dengan
                # end_time sebelum start_time lolos dan menghasilkan durasi
                # negatif di hitung laytime.
                if parse_iso(payload.get("end_time")) <= parse_iso(payload.get("start_time")):
                    conn.close()
                    self.send_json({"error": "Waktu selesai jeda harus sesudah waktu mulai"}, status=400)
                    return
                with conn:
                    conn.execute("""
                    INSERT INTO sof_stoppages (port_call_id, start_time, end_time, category, percentage_counted, remarks)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        pc_id,
                        payload.get("start_time"),
                        payload.get("end_time"),
                        category,
                        pct,
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
                # Gate role (ADR-0017): terbit SPB dibatasi OPS/ADMIN, sama
                # seperti sign-off ops — dulu siapa pun yang login (termasuk
                # FINANCE) bisa menerbitkan SPB.
                if actor_role not in ("OPS", "ADMIN"):
                    self.send_json({"error": "Terbit SPB hanya boleh dilakukan role OPS atau ADMIN"}, status=403)
                    return
                issue_time_str = payload.get("issued_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                doc_number = _opt_text(payload.get("doc_number"), "doc_number") or "SPB-ONLINE-001"
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
                c.execute("SELECT id, spb_issued_at, spb_expiry_at, finance_closed_at, finance_closed_by FROM port_calls WHERE id = ?", (pc_id,))
                pc = c.fetchone()
                if not pc:
                    conn.close()
                    self.send_json({"error": "Port Call tidak ditemukan"}, status=404)
                    return
                lock_msg = _closed_lock_message(pc)
                if lock_msg:
                    conn.close()
                    self.send_json({"error": lock_msg}, status=400)
                    return
                c.execute("SELECT doc_number FROM clearances WHERE port_call_id = ? AND doc_type LIKE '%SPB%'", (pc_id,))
                _spb_row = c.fetchone()
                old_doc_number = _spb_row["doc_number"] if _spb_row else None
                # SPB kedua menimpa yang pertama (ADR-0017): dulu endpoint
                # ini langsung menimpa spb_issued_at/expiry dan nomor di
                # baris clearance — nomor lama hanya tersisa di audit. Kini
                # SPB yang masih berlaku DITOLAK diterbitkan ulang; bila
                # sudah kedaluwarsa, penerbitan ulang sah (PM 28/2022) dan
                # nomor lama dicatat ke spb_history sebelum ditimpa.
                if pc["spb_issued_at"] and pc["spb_expiry_at"] and datetime.now() <= parse_iso(pc["spb_expiry_at"]):
                    conn.close()
                    self.send_json({"error": f"SPB {old_doc_number or ''} masih berlaku hingga {pc['spb_expiry_at']} — terbitkan ulang hanya setelah kedaluwarsa (PM 28/2022 Ps 9)"}, status=400)
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
                    if pc["spb_issued_at"]:
                        # SPB lama kedaluwarsa — simpan jejaknya dulu.
                        conn.execute("""
                        INSERT INTO spb_history (port_call_id, doc_number, issued_at, expiry_at, superseded_at, superseded_by)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """, (pc_id, old_doc_number, pc["spb_issued_at"], pc["spb_expiry_at"],
                              datetime.now().strftime("%Y-%m-%d %H:%M:%S"), actor))
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
                    log_audit(conn, pc_id, actor, "SPB_ISSUED",
                              f"SPB {old_doc_number} terbit {pc['spb_issued_at']}, kedaluwarsa {pc['spb_expiry_at']}" if pc["spb_issued_at"] else None,
                              f"SPB {doc_number} terbit {issue_time_str}, kedaluwarsa {expiry_time_str}")
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
                # Validasi (ADR-0019): agency/status dinormalisasi +
                # di-whitelist SEBELUM INSERT — "POLISI" / "approved"
                # huruf kecil dulu melempar IntegrityError (CHECK) dan
                # menutup koneksi tanpa respons (probe [B] review).
                agency = _norm_enum(payload.get("agency", "INAPORTNET_SIMPADU"), CLEARANCE_AGENCIES, "agency")
                doc_type = _opt_text(payload.get("doc_type"), "doc_type") or "Dokumen Tambahan"
                doc_number = _opt_text(payload.get("doc_number"), "doc_number") or ""
                status = _norm_enum(payload.get("status", "APPROVED"), CLEARANCE_STATUSES, "status")
                remarks = _opt_text(payload.get("remarks"), "remarks") or ""
                # ADR-0020: baris baru tidak bisa langsung VOID — VOID
                # hanya untuk membatalkan baris yang sudah ada (salah
                # input). Buat dulu, lalu void bila salah pilih tipe.
                if status == "VOID":
                    self.send_json({"error": "Baris baru tidak bisa langsung VOID — buat dulu, lalu void bila salah input"}, status=400)
                    return
                
                # ADR-0016: tidak ada berkas yang diunggah, jadi tidak ada
                # hash berkas. Dulu server membuat hash dari string
                # "port_call_id-doc_type-doc_number" — itu bukan bukti
                # integritas apa pun. file_sha256 dan file_name tetap NULL
                # sampai ada fitur upload berkas sungguhan.
                file_sha256 = None

                conn = get_db()
                c = conn.cursor()
                c.execute("SELECT id, finance_closed_at, finance_closed_by FROM port_calls WHERE id = ?", (pc_id,))
                _pc = c.fetchone()
                if not _pc:
                    conn.close()
                    self.send_json({"error": "Port Call tidak ditemukan"}, status=404)
                    return
                lock_msg = _closed_lock_message(_pc)
                if lock_msg:
                    conn.close()
                    self.send_json({"error": lock_msg}, status=400)
                    return
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                approval_time = now_str if status == "APPROVED" else None
                with conn:
                    conn.execute("""
                    INSERT INTO clearances (port_call_id, agency, doc_type, doc_number, submission_time, approval_time, status, file_name, file_sha256, remarks, submitted_by)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (pc_id, agency, doc_type, doc_number, now_str, approval_time, status, None, file_sha256, remarks, actor))
                    log_audit(conn, pc_id, actor, "CLEARANCE_ADD",
                              None, f"{agency} — {doc_type} [{status}] {doc_number}")
                conn.close()
                self.send_json({"status": "success", "file_sha256": file_sha256, "message": f"Dokumen {doc_type} berhasil dicatat"})
                return

            # POST /api/port-calls/<id>/disbursements
            if len(parts) == 4 and parts[3] == "disbursements":
                pc_id = parts[2]
                conn = get_db()
                c = conn.cursor()
                c.execute("SELECT id, finance_closed_at, finance_closed_by FROM port_calls WHERE id = ?", (pc_id,))
                _pc = c.fetchone()
                if not _pc:
                    conn.close()
                    self.send_json({"error": "Port Call tidak ditemukan"}, status=404)
                    return
                lock_msg = _closed_lock_message(_pc)
                if lock_msg:
                    conn.close()
                    self.send_json({"error": lock_msg}, status=400)
                    return
                with conn:
                    dis_id, err = _insert_disbursement(conn, pc_id, payload)
                    if not err:
                        log_audit(conn, pc_id, actor, "DISBURSEMENT_ADD",
                                  None, f"{payload.get('category')} — {payload.get('item_name')}")
                conn.close()
                if err:
                    self.send_json({"error": err}, status=400)
                    return
                self.send_json({"status": "success", "disbursement_id": dis_id, "message": "Item PDA/FDA berhasil ditambahkan"})
                return

            # POST /api/port-calls/<id>/charter-party — isi parameter
            # fixture (ADR-0017): tiap port call baru dulu disisipkan
            # charter party tetap (10.000 MT/hari, SHINC, demurrage
            # 20.000, notice 6 jam) dan tidak ada cara mengubahnya —
            # kalkulator memakai angka itu, bukan recap fixture. Kini
            # petugas mengisi rate bongkar, SHINC/SHEX, tarif
            # demurrage/despatch, dan jam notice dari recap.
            if len(parts) == 4 and parts[3] == "charter-party":
                pc_id = parts[2]
                if actor_role not in ("OPS", "ADMIN"):
                    self.send_json({"error": "Ubah parameter charter party hanya boleh dilakukan role OPS atau ADMIN"}, status=403)
                    return
                conn = get_db()
                c = conn.cursor()
                c.execute("SELECT id, finance_closed_at, finance_closed_by FROM port_calls WHERE id = ?", (pc_id,))
                _pc = c.fetchone()
                if not _pc:
                    conn.close()
                    self.send_json({"error": "Port Call tidak ditemukan"}, status=404)
                    return
                lock_msg = _closed_lock_message(_pc)
                if lock_msg:
                    conn.close()
                    self.send_json({"error": lock_msg}, status=400)
                    return
                c.execute("SELECT * FROM port_call_charter_parties WHERE port_call_id = ?", (pc_id,))
                cp = c.fetchone()
                if not cp:
                    conn.close()
                    self.send_json({"error": "Data kontrak tidak ditemukan"}, status=404)
                    return
                new_rate = cp["loading_rate_per_day"]
                new_terms = cp["laytime_terms"]
                new_dem = cp["demurrage_usd_per_day"]
                new_des = cp["despatch_usd_per_day"]
                new_notice = cp["nor_notice_hours"]
                new_charterer = cp["charterer_name"]
                for _field in ("loading_rate_per_day", "demurrage_usd_per_day", "despatch_usd_per_day", "nor_notice_hours"):
                    if _field in payload:
                        try:
                            _v = float(payload.get(_field))
                        except (TypeError, ValueError):
                            conn.close()
                            self.send_json({"error": f"{_field} harus angka"}, status=400)
                            return
                        if _v < 0:
                            conn.close()
                            self.send_json({"error": f"{_field} tidak boleh negatif"}, status=400)
                            return
                        if _field == "loading_rate_per_day":
                            if _v <= 0:
                                conn.close()
                                self.send_json({"error": "loading_rate_per_day harus lebih dari 0"}, status=400)
                                return
                            new_rate = _v
                        elif _field == "demurrage_usd_per_day":
                            new_dem = _v
                        elif _field == "despatch_usd_per_day":
                            new_des = _v
                        else:
                            new_notice = int(_v)
                if "laytime_terms" in payload:
                    if payload.get("laytime_terms") not in ("SHINC", "SHEX"):
                        conn.close()
                        self.send_json({"error": "laytime_terms wajib SHINC atau SHEX"}, status=400)
                        return
                    new_terms = payload.get("laytime_terms")
                if "charterer_name" in payload and payload.get("charterer_name"):
                    new_charterer = payload.get("charterer_name")
                if (new_rate == cp["loading_rate_per_day"] and new_terms == cp["laytime_terms"]
                        and new_dem == cp["demurrage_usd_per_day"] and new_des == cp["despatch_usd_per_day"]
                        and new_notice == cp["nor_notice_hours"] and new_charterer == cp["charterer_name"]):
                    conn.close()
                    self.send_json({"error": "Tidak ada perubahan (isi rate, terms, tarif, atau jam notice)"}, status=400)
                    return
                with conn:
                    conn.execute("""
                    UPDATE port_call_charter_parties
                    SET charterer_name = ?, loading_rate_per_day = ?, laytime_terms = ?,
                        demurrage_usd_per_day = ?, despatch_usd_per_day = ?, nor_notice_hours = ?
                    WHERE port_call_id = ?
                    """, (new_charterer, new_rate, new_terms, new_dem, new_des, new_notice, pc_id))
                    log_audit(conn, pc_id, actor, "CHARTER_PARTY_UPDATE",
                              f"{cp['loading_rate_per_day']} MT/hari {cp['laytime_terms']} dem {cp['demurrage_usd_per_day']} desp {cp['despatch_usd_per_day']} notice {cp['nor_notice_hours']} jam",
                              f"{new_rate} MT/hari {new_terms} dem {new_dem} desp {new_des} notice {new_notice} jam")
                conn.close()
                self.send_json({"status": "success", "message": "Parameter charter party berhasil disimpan",
                                "charter_party": {"charterer_name": new_charterer, "loading_rate_per_day": new_rate,
                                                  "laytime_terms": new_terms, "demurrage_usd_per_day": new_dem,
                                                  "despatch_usd_per_day": new_des, "nor_notice_hours": new_notice}})
                return

            # POST /api/port-calls/<id>/laytime/calculate
            if len(parts) == 5 and parts[3] == "laytime" and parts[4] == "calculate":
                pc_id = parts[2]
                conn = get_db()
                c = conn.cursor()
                c.execute("SELECT * FROM port_call_charter_parties WHERE port_call_id = ?", (pc_id,))
                cp = c.fetchone()
                c.execute("SELECT cargo_tonnage, status, finance_closed_at, finance_closed_by FROM port_calls WHERE id = ?", (pc_id,))
                pc = c.fetchone()

                if not cp or not pc:
                    conn.close()
                    self.send_json({"error": "Data kontrak atau kargo tidak lengkap"}, status=400)
                    return
                # Hitung laytime kini MENYIMPAN (ADR-0017) — milestone dan
                # baris hasil ikut ditulis — jadi kunci CLOSED berlaku:
                # arsip yang sudah CLOSED tidak boleh dihitung ulang.
                lock_msg = _closed_lock_message(pc)
                if lock_msg:
                    conn.close()
                    self.send_json({"error": lock_msg}, status=400)
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

                # Validasi (ADR-0019): ketiga waktu WAJIB diisi — dulu
                # ketiganya punya default tanggal demo (2026-11-01/04)
                # sehingga laytime tanpa body malah 200 dan MENYIMPAN
                # milestone demo sebagai fakta lapangan.
                nor_str = _req_text(payload.get("nor_tendered_time"), "nor_tendered_time")
                comm_str = _req_text(payload.get("commenced_time"), "commenced_time")
                comp_str = _req_text(payload.get("completed_time"), "completed_time")

                calc = LaytimeCalculator({
                    "cargo_quantity": pc["cargo_tonnage"] or 50000,
                    "loading_rate_per_day": cp["loading_rate_per_day"],
                    "terms": cp["laytime_terms"],
                    "demurrage_rate_per_day": cp["demurrage_usd_per_day"],
                    "despatch_rate_per_day": cp["despatch_usd_per_day"],
                    "nor_notice_hours": cp["nor_notice_hours"]
                })
                try:
                    result = calc.calculate(nor_str, comm_str, comp_str, stoppages)
                except ValueError as exc:
                    conn.close()
                    # Waktu salah format sebelumnya melempar ke 500
                    # ("Kesalahan internal server" di WSGI; koneksi putus
                    # tanpa respons di server lokal) — kini 400 yang
                    # menyebut format yang benar (ADR-0015). ValueError
                    # lain (mis. istilah laytime tak dikenal, ADR-0016)
                    # dikembalikan apa adanya, tanpa embel-embel format.
                    msg = str(exc)
                    if "Format timestamp" in msg:
                        self.send_json({"error": f"Waktu tidak valid: {msg}. Pakai format YYYY-MM-DD HH:MM (contoh: 2026-11-12 08:00) — periksa NOR, mulai/selesai bongkar, dan jeda SOF."}, status=400)
                    else:
                        self.send_json({"error": msg}, status=400)
                    return
                # Simpan hasil + NOR/mulai/selesai sebagai milestone
                # (ADR-0017): dulu ketiga waktu itu hanya angka di layar —
                # shift berikutnya tidak bisa melihatnya di SOF, dan hasil
                # hitung hilang begitu halaman dimuat ulang. Upsert per
                # kode: bila milestone berkode itu belum ada, buat baru
                # (status maju mengikuti aturan yang sama dengan POST
                # milestone); bila sudah ada dan waktunya berubah, koreksi
                # waktunya saja (status tidak disentuh).
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                saved_milestones = []
                with conn:
                    for _code, _time, _target in (
                            ("NOR_TENDERED", nor_str, None),
                            ("COMMENCED_CARGO", comm_str, "BERTHED"),
                            ("COMPLETED_CARGO", comp_str, None)):
                        c.execute("SELECT id, event_time FROM milestones WHERE port_call_id = ? AND milestone_code = ? ORDER BY id ASC LIMIT 1",
                                  (pc_id, _code))
                        _existing = c.fetchone()
                        if _existing:
                            if _existing["event_time"] != _time:
                                conn.execute("UPDATE milestones SET event_time = ? WHERE id = ?",
                                             (_time, _existing["id"]))
                                log_audit(conn, pc_id, actor, "MILESTONE_UPDATE",
                                          f"{_code} @ {_existing['event_time']}",
                                          f"{_code} @ {_time} (dari hitung laytime; status kapal tidak berubah)")
                                saved_milestones.append(_code)
                        else:
                            conn.execute("""
                            INSERT INTO milestones (port_call_id, milestone_code, event_time, recorded_by, remarks)
                            VALUES (?, ?, ?, ?, ?)
                            """, (pc_id, _code, _time, actor, "Dari hitung laytime"))
                            log_audit(conn, pc_id, actor, f"MILESTONE_{_code}",
                                      None, f"{_code} @ {_time} (dari hitung laytime)")
                            saved_milestones.append(_code)
                            if _target and _STATUS_RANK[_target] > _STATUS_RANK[pc["status"]]:
                                conn.execute("UPDATE port_calls SET status = ?, updated_at = ? WHERE id = ?",
                                             (_target, now_str, pc_id))
                    cur = conn.execute("""
                    INSERT INTO laytime_calculations (port_call_id, nor_tendered_time, commenced_time, completed_time, terms, allowed_days, used_days, claim_type, amount_usd, calculated_by, calculated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (pc_id, nor_str, comm_str, comp_str, result["terms"],
                          result["laytime_allowed"]["days_decimal"],
                          result["laytime_used"]["net_seconds"] / 86400,
                          result["result"]["claim_type"], result["result"]["amount_usd"],
                          actor, now_str))
                    calc_id = cur.lastrowid
                conn.close()
                result["saved"] = True
                result["calculation_id"] = calc_id
                result["milestones_saved"] = saved_milestones
                self.send_json(result)
                return

            # POST /api/port-calls/<id>/close-ops (Otorisasi 1)
            # GATE (PM 28/2022 + playbook tahap 5): Otorisasi 1 menyatakan
            # seluruh izin CIGP, warta, SPB, dan dokumen kargo sudah selesai,
            # jadi ditolak bila SPB belum terbit, SPB sudah kedaluwarsa
            # (24 jam, Ps 9 ayat (1)), atau kapal belum DEPARTED. Polanya
            # sama seperti close-finance yang mengecek ops_closed_at dulu.
            if len(parts) == 4 and parts[3] == "close-ops":
                pc_id = parts[2]
                # Gate role (ADR-0011): Otorisasi 1 hanya OPS/ADMIN. Dicek
                # SEBELUM gate SPB/status supaya penolakan role jelas.
                if actor_role not in ("OPS", "ADMIN"):
                    self.send_json({"error": "Otorisasi 1 hanya boleh dilakukan role OPS atau ADMIN"}, status=403)
                    return
                user_name = actor  # dari session, bukan payload
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                conn = get_db()
                c = conn.cursor()
                c.execute("SELECT id, status, spb_issued_at, spb_expiry_at, ops_closed_at FROM port_calls WHERE id = ?", (pc_id,))
                pc = c.fetchone()
                if not pc:
                    conn.close()
                    self.send_json({"error": "Port Call tidak ditemukan"}, status=404)
                    return
                if pc["ops_closed_at"]:
                    conn.close()
                    self.send_json({"error": f"Otorisasi 1 sudah disahkan {pc['ops_closed_at']} — tidak bisa sign-off dua kali"}, status=400)
                    return
                # Daftar dokumen yang belum APPROVED — supaya penolakan
                # menyebut DOKUMEN MANA yang belum lengkap (ADR-0015),
                # bukan hanya "SPB belum terbit". Daftar yang sama dengan
                # gate SPB (clearance_spb_gate): prasyarat wajib + item
                # bersyarat yang sudah ada tapi belum APPROVED.
                c.execute("SELECT * FROM clearances WHERE port_call_id = ?", (pc_id,))
                blockers, _ = clearance_spb_gate([dict(r) for r in c.fetchall()])
                if not pc["spb_issued_at"]:
                    conn.close()
                    self.send_json({"error": "SPB belum terbit — kapal belum boleh berlayar, ops tidak bisa ditutup (PM 28/2022)",
                                    "blockers": blockers}, status=400)
                    return
                if pc["spb_expiry_at"] and datetime.now() > parse_iso(pc["spb_expiry_at"]):
                    conn.close()
                    self.send_json({"error": f"SPB sudah kedaluwarsa {pc['spb_expiry_at']} — terbitkan SPB baru sebelum menutup ops",
                                    "blockers": blockers}, status=400)
                    return
                if pc["status"] != "DEPARTED":
                    conn.close()
                    self.send_json({"error": f"status kapal masih {pc['status']} — ops hanya bisa ditutup setelah kapal DEPARTED",
                                    "blockers": blockers}, status=400)
                    return
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
                # Gate role (ADR-0011): Otorisasi 2 hanya FINANCE/ADMIN.
                if actor_role not in ("FINANCE", "ADMIN"):
                    self.send_json({"error": "Otorisasi 2 hanya boleh dilakukan role FINANCE atau ADMIN"}, status=403)
                    return
                user_name = actor  # dari session, bukan payload
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                conn = get_db()
                c = conn.cursor()
                c.execute("SELECT ops_closed_at, finance_closed_at, finance_closed_by FROM port_calls WHERE id = ?", (pc_id,))
                row = c.fetchone()
                if not row or not row["ops_closed_at"]:
                    conn.close()
                    self.send_json({"error": "Otorisasi 1 (Operasional) wajib diselesaikan terlebih dahulu!"}, status=400)
                    return
                # Menutup dua kali (ADR-0017): dulu close-finance kedua
                # lolos diam-diam dan menimpa finance_closed_by/at.
                if row["finance_closed_at"]:
                    conn.close()
                    self.send_json({"error": f"Port call sudah CLOSED oleh {row['finance_closed_by']} pada {row['finance_closed_at']} — Otorisasi 2 tidak bisa diulang"}, status=400)
                    return
                # Blocker keuangan (ADR-0017): Otorisasi 2 ditolak bila
                # masih ada sengketa OPEN atau FDA masih 0 pada baris
                # yang PDA-nya sudah terisi — dengan daftar itemnya, pola
                # yang sama seperti blocker close-ops (ADR-0015). Sengketa
                # UNDER_REVIEW tidak menghalangi (masih bisa diselesaikan
                # setelah CLOSED — pengecualian kunci CLOSED).
                c.execute("""
                SELECT d.item_name FROM disbursements d
                JOIN fda_disputes f ON f.disbursement_id = d.id
                WHERE d.port_call_id = ? AND f.status = 'OPEN'
                """, (pc_id,))
                open_disputes = [r["item_name"] for r in c.fetchall()]
                c.execute("""
                SELECT item_name FROM disbursements
                WHERE port_call_id = ? AND pda_estimate > 0 AND (fda_actual IS NULL OR fda_actual = 0)
                """, (pc_id,))
                fda_pending = [r["item_name"] for r in c.fetchall()]
                fin_blockers = ([f"Sengketa OPEN: {n}" for n in open_disputes]
                                + [f"FDA belum diisi: {n}" for n in fda_pending])
                if fin_blockers:
                    conn.close()
                    self.send_json({"error": "Otorisasi 2 ditolak — rekonsiliasi FDA belum selesai",
                                    "blockers": fin_blockers}, status=400)
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
                try:
                    amount = float(payload.get("disputed_amount", 0))
                except (TypeError, ValueError):
                    # Sebelumnya float() yang gagal melempar ke handler 500
                    # (halaman HTML, bukan JSON) — UI tidak bisa menampilkan
                    # alasannya. Kini 400 JSON supaya UI bisa menjelaskannya.
                    self.send_json({"error": "Nilai sengketa harus angka (IDR)"}, status=400)
                    return
                reason = _opt_text(payload.get("reason"), "reason") or "Keberatan tarif"
                deadline = (datetime.now() + timedelta(days=14)).strftime("%Y-%m-%d")

                conn = get_db()
                c = conn.cursor()
                c.execute("SELECT id, finance_closed_at, finance_closed_by FROM port_calls WHERE id = ?", (pc_id,))
                _pc = c.fetchone()
                if not _pc:
                    conn.close()
                    self.send_json({"error": "Port Call tidak ditemukan"}, status=404)
                    return
                lock_msg = _closed_lock_message(_pc)
                if lock_msg:
                    conn.close()
                    self.send_json({"error": lock_msg}, status=400)
                    return
                # Item biaya HARUS milik port call ini (ADR-0015):
                # sebelumnya sengketa atas item port call lain diterima
                # begitu saja (200) — data sengketa jadi salah alamat.
                c.execute("SELECT id FROM disbursements WHERE id = ? AND port_call_id = ?",
                          (disbursement_id, pc_id))
                if not c.fetchone():
                    conn.close()
                    self.send_json({"error": "Item biaya tidak ditemukan pada port call ini — periksa kembali item PDA/FDA yang disengketakan"}, status=400)
                    return
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
            self._security_headers()
            self.end_headers()
            self.wfile.write(content)
        else:
            self.send_response(404)
            self._security_headers()
            self.end_headers()
            self.wfile.write(b"UI file not found")


class _ThreadedServer(socketserver.ThreadingTCPServer):
    # Satu thread per koneksi (ADR-0013). Server lama (TCPServer) memproses
    # request satu per satu secara berurutan — satu koneksi menggantung
    # (mis. preconnect browser yang tidak mengirim request) memblokir
    # SEMUA request berikutnya sampai koneksi itu ditutup. Dengan thread
    # per koneksi, request lain tetap dilayani. daemon_threads=True supaya
    # Ctrl+C / shutdown tidak menunggu koneksi yang masih terbuka.
    # Aman untuk thread: get_db() membuat koneksi SQLite baru per panggilan
    # (tidak ada koneksi yang dipakai lintas thread), koneksi libsql remote
    # di-cache per thread (threading.local), dan rate limit login sudah
    # memakai lock sendiri.
    daemon_threads = True
    allow_reuse_address = True  # restart cepat tidak kena TIME_WAIT


def run_server(port=PORT):
    # Gagal cepat bila SESSION_SECRET belum diisi (ADR-0011): lebih baik
    # server tidak jalan sama sekali daripada jalan tanpa autentikasi.
    get_session_secret()
    init_db()
    seed_sample_data()
    server = _ThreadedServer(("", port), AgencyAPIHandler)
    print(f"Server Keagenan Kapal berjalan di http://localhost:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer dihentikan.")
        server.server_close()


# ---------------------------------------------------------------------------
# WSGI entrypoint untuk Vercel (atau server WSGI lain).
#
# Vercel mendeteksi server.py sebagai Python entrypoint dan mewajibkan
# variabel top-level "app" (ASGI) atau "application" (WSGI); tanpa itu build
# gagal dengan PYTHON_ENTRYPOINT_NOT_FOUND. Kode API ditulis dengan gaya
# http.server (self.rfile/self.wfile), maka adapter ini menerjemahkan SATU
# request WSGI menjadi SATU pemanggilan handler di memori — tanpa dependensi
# tambahan (ADR-0003). Mode lokal `python server.py 8000` tidak berubah.
# ---------------------------------------------------------------------------

class _WSGIRequest(AgencyAPIHandler):
    """Kerangka handler tanpa socket: isi langsung atribut yang dipakai
    do_GET/do_POST/send_json, tangkap respons ke buffer memori."""

    def __init__(self, environ, body):
        # Sengaja TIDAK memanggil BaseRequestHandler.__init__ (yang menyentuh
        # socket); cukup atribut yang benar-benar dipakai logika API.
        self.command = environ.get("REQUEST_METHOD", "GET")
        query = environ.get("QUERY_STRING", "")
        self.path = environ.get("PATH_INFO", "/") + (f"?{query}" if query else "")
        self.request_version = environ.get("SERVER_PROTOCOL", "HTTP/1.1")
        self.requestline = f"{self.command} {self.path} {self.request_version}"
        self.headers = WSGIHeaders()
        for key, value in environ.items():
            if key.startswith("HTTP_"):
                self.headers[key[len("HTTP_"):].replace("_", "-")] = value
        if environ.get("CONTENT_TYPE"):
            self.headers["Content-Type"] = environ["CONTENT_TYPE"]
        if environ.get("CONTENT_LENGTH"):
            self.headers["Content-Length"] = environ["CONTENT_LENGTH"]
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        # Alamat klien untuk rate limit login (ADR-0016): di WSGI tidak ada
        # socket, jadi ambil dari REMOTE_ADDR yang diisi platform (Vercel
        # mengisinya dengan IP klien). Tanpa ini, handler login yang membaca
        # self.client_address akan 500 di Vercel.
        self.client_address = (environ.get("REMOTE_ADDR", "wsgi"), 0)

    def log_request(self, code="-", size="-"):  # request log dikelola platform WSGI
        pass

    def log_message(self, fmt, *args):
        pass


_application_ready = False
_application_lock = threading.Lock()


def _prepare_database_once():
    """Siapkan skema + data contoh, dengan biaya minimal bila DB sudah siap.

    Dulu _ensure_database_ready() selalu menjalankan init_db() +
    seed_sample_data() penuh: 13 CREATE TABLE IF NOT EXISTS + belasan
    SELECT COUNT + INSERT — masing-masing satu round-trip ke Turso
    (~600 ms dari VM ini) sehingga cold start pertama mencapai ~39 dtk.
    Sekarang cukup 1 query: dua COUNT dalam satu statement
    (master_ports + port_calls). Bila keduanya > 0, tidak ada lagi yang
    dijalankan. Bila salah satu 0 -> seed_sample_data() saja (mengisi tabel
    yang kosong; idempoten). Bila tabelnya belum ada sama sekali, query
    gagal dengan "no such table" -> init_db() + seed penuh.
    (Satu statement, bukan dua: COUNT gabungan ini adalah ide dari
    turso-speedup.patch — bedanya, patch itu hanya mengecek master_ports,
    sehingga DB yang tabelnya tidak lengkap lolos tanpa diperbaiki.)

    ADR-0018: COUNT di atas hanya membuktikan DUA tabel ada dan terisi.
    Tabel yang ditambahkan belakangan (spb_history dan
    laytime_calculations di ADR-0017) tidak pernah dibuat di DB Turso yang
    sudah ada — padahal endpoint detail/handover meng-query keduanya tanpa
    syarat. Akibatnya setelah PR #11 di-merge, Vercel 500 "no such table"
    dan detail port call tidak ter-load, sementara server lokal jalan
    normal karena run_server() selalu memanggil init_db(). Maka satu query
    tambahan ke sqlite_master memastikan KE-16 tabel (TABLES) ada; bila
    ada yang hilang, init_db() — idempotent, CREATE TABLE IF NOT EXISTS —
    melengkapinya. Biaya bila skema lengkap: 1 round-trip; bila ada yang
    hilang: init_db() sekali, lalu cold start berikutnya kembali murah."""
    conn = get_db()
    try:
        try:
            row = conn.execute(
                "SELECT (SELECT COUNT(*) FROM master_ports) AS n_ports, "
                "(SELECT COUNT(*) FROM port_calls) AS n_pc").fetchone()
        except Exception as exc:
            if "no such table" not in str(exc):
                raise
            row = None  # DB baru, tabel belum ada -> init penuh di bawah
        if row is None:
            init_db()
            seed_sample_data()
            return
        if row[0] == 0 or row[1] == 0:
            seed_sample_data()
        # ADR-0018: pastikan SELURUH skema ada, bukan hanya 2 tabel.
        # fetchall() — bukan iterasi cursor langsung — karena cursor
        # libsql (_LibSQLCursor) tidak iterable seperti cursor sqlite3.
        have = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
        if not set(TABLES).issubset(have):
            init_db()
        # ADR-0020: DB lama yang CHECK status clearances-nya belum
        # mengenal VOID dinaikkan (rebuild tabel, data utuh). No-op bila
        # skema sudah baru — sama seperti pola self-heal ADR-0018.
        _upgrade_clearances_void(conn)
    finally:
        conn.close()


def _ensure_database_ready():
    """Cold start: siapkan skema + data contoh sekali per instance.

    Bila koneksi remote putus di tengah cold start (stream hrana mati saat
    instance idle), buang koneksi yang di-cache dan coba sekali lagi."""
    global _application_ready
    if _application_ready:
        return
    with _application_lock:
        if _application_ready:
            return
        try:
            _prepare_database_once()
        except Exception:
            drop_remote_connection()
            _prepare_database_once()
        _application_ready = True


def application(environ, start_response):
    """Callable WSGI: menjembatani request platform <-> AgencyAPIHandler."""
    _ensure_database_ready()

    content_length = int(environ.get("CONTENT_LENGTH") or 0)
    body = environ["wsgi.input"].read(content_length) if content_length > 0 else b""
    handler = _WSGIRequest(environ, body)
    method = handler.command

    try:
        if method == "GET":
            handler.do_GET()
        elif method == "POST":
            handler.do_POST()
        elif method == "OPTIONS":
            handler.do_OPTIONS()
        else:
            handler.send_json({"error": f"Metode {method} tidak didukung"}, status=405)
    except Exception:
        # Bila koneksi remote yang di-cache sudah stale (stream hrana putus
        # saat instance idle), request ini gagal — tapi buang dulu koneksinya
        # supaya request berikutnya membuka koneksi baru, bukan mengulang
        # kegagalan yang sama selamanya.
        drop_remote_connection()
        if handler.wfile.tell() == 0:  # respons belum terkirim sama sekali
            handler.send_json({"error": "Kesalahan internal server"}, status=500)
        else:
            raise

    raw = handler.wfile.getvalue()
    head, _, payload = raw.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    pieces = lines[0].decode("latin-1").split(" ", 2)  # contoh: "HTTP/1.0 200 OK"
    try:
        code = int(pieces[1])
    except (IndexError, ValueError):
        code = 500
    reason = pieces[2] if len(pieces) > 2 else ""
    response_headers = []
    for line in lines[1:]:
        name, _, value = line.decode("latin-1").partition(":")
        name = name.strip()
        if name.lower() in ("server", "date"):  # milik platform WSGI
            continue
        response_headers.append((name, value.strip()))
    start_response(f"{code} {reason}".strip(), response_headers)
    return [payload]


if __name__ == "__main__":
    p = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    run_server(p)
