"""
Database & Domain Repository Menggunakan SQLite (Python stdlib sqlite3).
Memenuhi kebutuhan PRD, ADR-0003, dan Putusan Q18, Q20, Q23, Q25, Q26, Q27, Q30, Q32.
"""

import sqlite3
import hashlib
import json
import os
import shutil
import tempfile
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

try:
    import libsql as _libsql
except ImportError:  # lokal tanpa Turso: tetap jalan dengan sqlite3 stdlib
    _libsql = None


# Daftar tabel skema (satu sumber kebenaran; dipakai init_db() dan
# pemeriksaan kesiapan DB saat cold start di server.py).
TABLES = [
    "master_ports", "master_terminals", "master_vessels", "master_branches",
    "master_principals", "port_calls", "port_call_charter_parties", "clearances",
    "milestones", "sof_stoppages", "disbursements", "fda_disputes", "audit_logs",
]


class _Row:
    """Baris hasil query yang berperilaku seperti sqlite3.Row.

    Dibutuhkan karena koneksi libsql mengembalikan tuple polos tanpa
    row_factory, sementara server.py mengakses baris by nama (r["status"]),
    by indeks (r[0]), dan dict(r)."""

    __slots__ = ("_keys", "_values")

    def __init__(self, keys, values):
        self._keys = list(keys)
        self._values = tuple(values)

    def __getitem__(self, key):
        if isinstance(key, str):
            return self._values[self._keys.index(key)]
        return self._values[key]

    def keys(self):
        return list(self._keys)

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __repr__(self):
        return repr(dict(zip(self._keys, self._values)))


def _wrap_rows(cursor, rows):
    keys = [d[0] for d in (cursor.description or [])]
    return [_Row(keys, tuple(r)) for r in rows]


class _LibSQLCursor:
    """Cursor libsql yang mengembalikan _Row (mirip sqlite3.Row)."""

    def __init__(self, cursor):
        self._cur = cursor

    @property
    def description(self):
        return self._cur.description

    @property
    def lastrowid(self):
        return self._cur.lastrowid

    @property
    def rowcount(self):
        return self._cur.rowcount

    def execute(self, sql, params=()):
        self._cur.execute(sql, params)
        return self

    def executemany(self, sql, seq):
        self._cur.executemany(sql, seq)
        return self

    def executescript(self, sql):
        self._cur.executescript(sql)
        return self

    def fetchone(self):
        row = self._cur.fetchone()
        if row is None:
            return None
        return _wrap_rows(self, [row])[0]

    def fetchall(self):
        return _wrap_rows(self, self._cur.fetchall())

    def fetchmany(self, size=None):
        rows = self._cur.fetchmany(size) if size is not None else self._cur.fetchmany()
        return _wrap_rows(self, rows)

    def close(self):
        self._cur.close()


class _LibSQLConnection:
    """Koneksi libsql yang meniru antarmuka sqlite3 yang dipakai server.py:
    execute/cursor/commit/rollback/close + `with conn:`.

    Bila `shared=True`, koneksi ini adalah koneksi remote yang di-cache
    (dipakai ulang antar-request, lihat get_db()) dan `close()` sengaja
    menjadi no-op: koneksi dimiliki modul, bukan pemanggil — semua handler
    server.py memanggil conn.close() di akhir, dan close() yang sungguhan
    akan memutus cache. Satu-satunya jalan memutus koneksi cache adalah
    drop_remote_connection() (dipakai saat koneksi putus/stale)."""

    def __init__(self, conn, shared=False):
        self._conn = conn
        self._shared = shared

    def cursor(self):
        return _LibSQLCursor(self._conn.cursor())

    def execute(self, sql, params=()):
        return _LibSQLCursor(self._conn.execute(sql, params))

    def executemany(self, sql, seq):
        return _LibSQLCursor(self._conn.executemany(sql, seq))

    def executescript(self, sql):
        self._conn.executescript(sql)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        if self._shared:
            return  # dimiliki modul; lihat docstring kelas
        self._conn.close()

    def _close_real(self):
        """Tutup koneksi betulan, termasuk bila shared (hanya untuk
        drop_remote_connection())."""
        self._conn.close()

    @property
    def in_transaction(self):
        return self._conn.in_transaction

    @property
    def isolation_level(self):
        return self._conn.isolation_level

    def __enter__(self):
        self._conn.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        return self._conn.__exit__(exc_type, exc, tb)


def _resolve_db_file():
    """Pilih lokasi file SQLite yang selalu writable.

    Di Vercel/Lambda, isi proyek bersifat read-only sehingga SQLite tidak bisa
    membuka journal/WAL di lokasi aslinya. Bila agency.db bawaan tidak dapat
    dibuka untuk menulis, salin sekali ke /tmp per cold start dan gunakan
    salinan itu. (Konsekuensi: data yang ditulis bersifat per-instance dan
    hilang saat instance baru dibuat — wajar untuk demo/preview.)
    """
    bundled = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "agency.db"))
    try:
        # Probe tulis: bila agency.db belum ada, "ab" membuatnya (lokal:
        # init_db() akan mengisinya — sama seperti sebelumnya). Bila direktori
        # proyek read-only (Vercel/Lambda), probe ini gagal dengan OSError
        # dan kita jatuh ke /tmp — termasuk bila agency.db tidak ikut
        # ter-bundle (sejak PR #3, agency.db tidak lagi ter-commit di Git).
        with open(bundled, "ab"):
            return bundled
    except OSError:  # filesystem read-only (Vercel/Lambda)
        target = os.path.join(tempfile.gettempdir(), "agency.db")
        if not os.path.exists(target):
            if os.path.exists(bundled):
                staging = f"{target}.{os.getpid()}.{threading.get_ident()}.tmp"
                shutil.copyfile(bundled, staging)
                os.replace(staging, target)  # atomik: aman dari race antar-request
            # Bila tidak ada bundled DB: /tmp/agency.db dibiarkan belum ada;
            # init_db() akan membuatnya di sana saat cold start.
        return target


DB_FILE = _resolve_db_file()


# Koneksi Turso remote di-cache per thread (threading.local), bukan dibuka
# baru di tiap get_db(). Alasannya: sekali libsql.connect() ke Turso remote
# butuh ~1,5 dtk (hangat) sampai ~7 dtk (dingin) dari VM ini, dan tiap
# request API memanggil get_db() — jadi setiap request membayar koneksi baru.
# Thread-local (bukan global tunggal) dipilih supaya tidak ada dua thread
# yang memakai satu koneksi yang sama: server lokal memakai
# ThreadingHTTPServer, dan runtime Vercel pun bisa memakai beberapa thread.
# Mode lokal (sqlite3) dan mode file libsql tidak di-cache: connect()-nya
# murah (file lokal) dan perilakunya harus tetap seperti sebelumnya.
_remote_conn_local = threading.local()


def _is_remote_url(url):
    return (url.startswith("libsql://") or url.startswith("https://")
            or url.startswith("http://"))


def _connect_remote(url, token):
    raw = _libsql.connect(url, auth_token=token)
    conn = _LibSQLConnection(raw, shared=True)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def drop_remote_connection():
    """Buang koneksi Turso yang di-cache (dipakai saat koneksi putus/stale).

    Aman dipanggil kapan saja: no-op bila tidak ada koneksi remote yang
    di-cache (mode lokal / mode file libsql). Request berikutnya akan membuka
    koneksi baru lewat get_db()."""
    cached = getattr(_remote_conn_local, "conn", None)
    if cached is not None:
        try:
            cached._close_real()
        except Exception:
            pass
        _remote_conn_local.conn = None


def get_db():
    """Koneksi database.

    Bila TURSO_DATABASE_URL + TURSO_AUTH_TOKEN terisi, pakai Turso (libSQL
    remote) — data persisten dan sama untuk semua instance Vercel. Koneksi
    remote dipakai ulang selama thread yang sama masih hidup (lihat
    _remote_conn_local); transaksi yang tertinggal dari request sebelumnya
    dibatalkan sebelum koneksi dipakai lagi. Bila tidak ada, pakai SQLite
    lokal seperti sebelumnya (tanpa dependensi tambahan).
    TURSO_DATABASE_URL berupa path file biasa juga diterima: libsql dipakai
    dalam mode file lokal (berguna untuk menguji backend libsql tanpa akun
    Turso). libsql mengembalikan tuple polos tanpa row_factory, jadi koneksi
    remote/file dibungkus _LibSQLConnection agar server.py tetap bisa memakai
    r["kolom"], r[0], dan dict(r) seperti pada sqlite3.Row."""
    url = os.environ.get("TURSO_DATABASE_URL", "").strip()
    token = os.environ.get("TURSO_AUTH_TOKEN", "").strip()
    if url and _libsql is not None:
        if _is_remote_url(url):
            if not token:
                raise RuntimeError(
                    "TURSO_AUTH_TOKEN wajib diisi bila TURSO_DATABASE_URL remote dipakai")
            cached = getattr(_remote_conn_local, "conn", None)
            if cached is not None:
                try:
                    # Transaksi yang tertinggal dari request sebelumnya
                    # (mis. gagal sebelum commit) harus dibatalkan agar
                    # tidak tercampur dengan request berikutnya.
                    if cached.in_transaction:
                        cached.rollback()
                    return cached
                except Exception:
                    drop_remote_connection()
            conn = _connect_remote(url, token)
            _remote_conn_local.conn = conn
            return conn
        raw = _libsql.connect(url)
        conn = _LibSQLConnection(raw)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn
    if url and _libsql is None:
        raise RuntimeError(
            "TURSO_DATABASE_URL terisi tapi paket 'libsql' belum terinstal "
            "(pip install -r requirements.txt)")
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    with conn:
        # Master Pelabuhan & Profil
        conn.execute("""
        CREATE TABLE IF NOT EXISTS master_ports (
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            province TEXT NOT NULL,
            unlocode TEXT NOT NULL,
            inaportnet_level TEXT NOT NULL, -- Kelas Pelabuhan
            vts_channel TEXT,
            max_draft REAL,
            notes TEXT
        )
        """)

        # Master Terminal / Dermaga / Tersus
        conn.execute("""
        CREATE TABLE IF NOT EXISTS master_terminals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            port_code TEXT NOT NULL,
            name TEXT NOT NULL,
            terminal_type TEXT DEFAULT 'UMUM', -- UMUM, PETIKEMAS, TERSUS, TUKS, CURAH
            max_draft REAL,
            FOREIGN KEY (port_code) REFERENCES master_ports (code),
            UNIQUE(port_code, name)
        )
        """)

        # Master Kapal
        conn.execute("""
        CREATE TABLE IF NOT EXISTS master_vessels (
            imo TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            flag TEXT NOT NULL,
            vessel_type TEXT NOT NULL,
            call_sign TEXT,
            grt REAL NOT NULL,
            dwt REAL NOT NULL,
            loa REAL NOT NULL,
            max_draft REAL NOT NULL
        )
        """)

        # Master Cabang Agen
        conn.execute("""
        CREATE TABLE IF NOT EXISTS master_branches (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            port_code TEXT NOT NULL,
            address TEXT,
            phone TEXT,
            FOREIGN KEY (port_code) REFERENCES master_ports (code)
        )
        """)

        # Master Principal
        conn.execute("""
        CREATE TABLE IF NOT EXISTS master_principals (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            country TEXT NOT NULL,
            email TEXT NOT NULL,
            contact_person TEXT
        )
        """)

        # Port Call (Agregat Root)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS port_calls (
            id TEXT PRIMARY KEY,
            branch_id TEXT NOT NULL,
            vessel_imo TEXT NOT NULL,
            principal_id TEXT NOT NULL,
            vessel_category TEXT NOT NULL CHECK(vessel_category IN ('FOREIGN', 'DOMESTIC')),
            port_code TEXT NOT NULL,
            terminal_name TEXT,
            status TEXT NOT NULL CHECK(status IN ('PRE_ARRIVAL', 'ARRIVED', 'BERTHED', 'DEPARTED', 'CLEARANCE_CLOSED', 'CLOSED')),
            eta TEXT NOT NULL,
            ata TEXT,
            etd TEXT,
            atd TEXT,
            cargo_name TEXT,
            cargo_tonnage REAL,
            spb_issued_at TEXT,
            spb_expiry_at TEXT, -- Wajib 24 jam pasca spb_issued_at
            ops_closed_by TEXT,
            ops_closed_at TEXT,
            finance_closed_by TEXT,
            finance_closed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (branch_id) REFERENCES master_branches (id),
            FOREIGN KEY (vessel_imo) REFERENCES master_vessels (imo),
            FOREIGN KEY (principal_id) REFERENCES master_principals (id),
            FOREIGN KEY (port_code) REFERENCES master_ports (code)
        )
        """)

        # Parameter Charter Party Port Call
        conn.execute("""
        CREATE TABLE IF NOT EXISTS port_call_charter_parties (
            port_call_id TEXT PRIMARY KEY,
            charterer_name TEXT NOT NULL,
            loading_rate_per_day REAL NOT NULL,
            laytime_terms TEXT NOT NULL CHECK(laytime_terms IN ('SHINC', 'SHEX')),
            demurrage_usd_per_day REAL NOT NULL,
            despatch_usd_per_day REAL NOT NULL,
            nor_notice_hours INTEGER DEFAULT 6,
            FOREIGN KEY (port_call_id) REFERENCES port_calls (id)
        )
        """)

        # Dokumen Perizinan CIGP & Inaportnet
        conn.execute("""
        CREATE TABLE IF NOT EXISTS clearances (
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
        )
        """)

        # Tonggak Waktu Lapangan (Milestones)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS milestones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            port_call_id TEXT NOT NULL,
            milestone_code TEXT NOT NULL,
            event_time TEXT NOT NULL,
            recorded_by TEXT NOT NULL,
            remarks TEXT,
            FOREIGN KEY (port_call_id) REFERENCES port_calls (id)
        )
        """)

        # Jeda Operasi SOF (Stoppages)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS sof_stoppages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            port_call_id TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            category TEXT NOT NULL CHECK(category IN ('RAIN', 'WEATHER_OTHER', 'MEAL_BREAK', 'SHIFT_CHANGE', 'CRANE_BREAKDOWN_SHORE', 'WINCH_BREAKDOWN_SHIP', 'CARGO_WAITING', 'SHIFTING_BERTH', 'OTHER')),
            percentage_counted REAL NOT NULL DEFAULT 0.0,
            remarks TEXT,
            FOREIGN KEY (port_call_id) REFERENCES port_calls (id)
        )
        """)

        # Pos Rekonsiliasi Finansial PDA vs FDA
        conn.execute("""
        CREATE TABLE IF NOT EXISTS disbursements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            port_call_id TEXT NOT NULL,
            category TEXT NOT NULL CHECK(category IN ('PNBP_KEMENHUB', 'BUP_PELINDO', 'CIGP_INSTANSI', 'HUSBANDRY', 'AGENCY_FEE')),
            item_name TEXT NOT NULL,
            pda_estimate REAL NOT NULL DEFAULT 0.0,
            fda_actual REAL NOT NULL DEFAULT 0.0,
            simponi_billing TEXT,
            ntpn TEXT,
            invoice_number TEXT,
            voucher_sha256 TEXT,
            FOREIGN KEY (port_call_id) REFERENCES port_calls (id)
        )
        """)

        # Sengketa Biaya FDA (Dispute Tracking)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS fda_disputes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            port_call_id TEXT NOT NULL,
            disbursement_id INTEGER NOT NULL,
            disputed_amount REAL NOT NULL,
            principal_reason TEXT NOT NULL,
            deadline_date TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('OPEN', 'UNDER_REVIEW', 'ACCEPTED_REVISED', 'REJECTED_FINAL')),
            resolution_notes TEXT,
            FOREIGN KEY (port_call_id) REFERENCES port_calls (id),
            FOREIGN KEY (disbursement_id) REFERENCES disbursements (id)
        )
        """)

        # Audit Trail (Immutable Log 10 Tahun)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            port_call_id TEXT,
            user_id TEXT NOT NULL,
            action TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            timestamp TEXT NOT NULL
        )
        """)

    conn.close()


def seed_sample_data():
    """Mengisi data awal master dan port call pengujian jika kosong.

    Seeding BERTAHAP per tabel: tiap tabel di-seed bila kosong, independen
    dari tabel lain. Bug lama: blok kedua hanya jalan bila master_ports kosong,
    padahal blok pertama sudah mengisi master_ports — sehingga di DB baru
    branches/principals/vessels/sample port call tidak pernah ter-seed dan
    POST /api/port-calls gagal foreign key.
    """
    conn = get_db()
    cursor = conn.cursor()

    def _count(table):
        return cursor.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    ports_sql = """
            INSERT OR IGNORE INTO master_ports VALUES
            ('IDTPP', 'Tanjung Priok', 'DKI Jakarta', 'IDTPP', 'Kelas Utama', 'VHF Ch 12/16', 14.0, 'Pelabuhan Utama Nasional'),
            ('IDTPS', 'Tanjung Perak', 'Jawa Timur', 'IDTPS', 'Kelas Utama', 'VHF Ch 14/16', 12.5, 'Gerbang Logistik Timur'),
            ('IDKOL', 'Kolonodale', 'Sulawesi Tengah', 'IDKOL', 'Kelas III (KUPP)', 'VHF Ch 16', 11.0, 'Pelabuhan Tambang Nikel & Curah'),
            ('IDBTM', 'Batu Ampar', 'Kepulauan Riau', 'IDBTM', 'Kelas I', 'VHF Ch 16/71', 13.0, 'Kawasan Perbatasan & FTZ')
            """
    terminals_sql = """
            INSERT OR IGNORE INTO master_terminals (port_code, name, terminal_type, max_draft) VALUES
            ('IDTPP', 'JICT (Jakarta Int. Container Terminal)', 'PETIKEMAS', 14.0),
            ('IDTPP', 'TPK Koja', 'PETIKEMAS', 13.5),
            ('IDTPP', 'NPCT1 (New Priok Container Terminal 1)', 'PETIKEMAS', 16.0),
            ('IDTPP', 'Dermaga 101-106 (Terminal Jamrud/Umum)', 'UMUM', 10.5),
            ('IDTPP', 'Terminal Curah Kering Bogasari', 'CURAH', 12.0),
            ('IDTPS', 'Terminal Petikemas Surabaya (TPS)', 'PETIKEMAS', 12.5),
            ('IDTPS', 'Terminal Teluk Lamong (TTL)', 'PETIKEMAS', 14.0),
            ('IDTPS', 'Terminal Nilam & Mirah', 'UMUM', 9.5),
            ('IDTPS', 'Terminal Jamrud Barat/Utara', 'CURAH', 10.5),
            ('IDKOL', 'Pelabuhan Umum KUPP Kolonodale', 'UMUM', 9.0),
            ('IDKOL', 'Tersus PT COR Industri Indonesia', 'TERSUS', 11.5),
            ('IDKOL', 'TUKS PT Bumanik', 'TUKS', 10.0),
            ('IDBTM', 'Dermaga Utara Batu Ampar', 'UMUM', 12.0),
            ('IDBTM', 'Terminal Petikemas Batu Ampar', 'PETIKEMAS', 13.0),
            ('IDBTM', 'Tersus Kabil Integrated Port', 'TERSUS', 12.5)
            """

    if _count("master_ports") == 0:
        with conn:
            conn.execute(ports_sql)
    if _count("master_terminals") == 0:
        with conn:
            conn.execute(terminals_sql)
    if _count("master_branches") == 0:
        with conn:
            conn.execute("""
            INSERT OR IGNORE INTO master_branches VALUES
            ('BR-JKT', 'Cabang Jakarta Raya', 'IDTPP', 'Jl. Pelabuhan Raya No. 10, Tj. Priok', '+62-21-439001'),
            ('BR-SUB', 'Cabang Surabaya', 'IDTPS', 'Jl. Perak Timur No. 44, Surabaya', '+62-31-329112')
            """)
    if _count("master_principals") == 0:
        with conn:
            conn.execute("""
            INSERT OR IGNORE INTO master_principals VALUES
            ('PR-OCN', 'Ocean Shipping Line Ltd.', 'Singapura', 'operations@oceanshipping.sg', 'Capt. Wong'),
            ('PR-PAC', 'Pacific Maritime Corp', 'Panama', 'agency@pacificmaritime.pa', 'Mr. David Miller')
            """)
    if _count("master_vessels") == 0:
        with conn:
            conn.execute("""
            INSERT OR IGNORE INTO master_vessels VALUES
            ('9876543', 'MV. SAMUDRA JAYA', 'Indonesia', 'Bulk Carrier', 'YBXX', 32500, 55000, 189.9, 12.8),
            ('9431289', 'MV. PACIFIC GLORY', 'Panama', 'Container Ship', '3EMA9', 41000, 52000, 222.0, 13.2)
            """)

    # Sample port call — di-seed bila ID contoh belum ada (bukan bila tabel kosong).
    sample_id = "PC-2026-JKT-0001"
    exists = cursor.execute(
        "SELECT 1 FROM port_calls WHERE id = ?", (sample_id,)
    ).fetchone()
    if not exists:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        eta_sample = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        with conn:
            conn.execute("""
            INSERT INTO port_calls VALUES (
                'PC-2026-JKT-0001', 'BR-JKT', '9431289', 'PR-PAC', 'FOREIGN', 'IDTPP', 'Terminal Nusantara 2',
                'PRE_ARRIVAL', ?, NULL, NULL, NULL, 'Peti Kemas Campuran', 25000, NULL, NULL, NULL, NULL, NULL, NULL, ?, ?
            )
            """, (eta_sample, now_str, now_str))
            conn.execute("""
            INSERT INTO port_call_charter_parties VALUES (
                'PC-2026-JKT-0001', 'Global Chartering Ltd.', 8000, 'SHINC', 18000, 9000, 6
            )
            """)
            conn.execute("""
            INSERT INTO clearances (port_call_id, agency, doc_type, doc_number, submission_time, status, file_name, file_sha256, remarks, submitted_by) VALUES
            ('PC-2026-JKT-0001', 'INAPORTNET_SIMPADU', 'PKKA / Pemberitahuan Kedatangan Kapal Asing', 'PPKKA-2026-10-881', ?, 'APPROVED', 'ppkka_mv_pacific.pdf', 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855', 'Izin trayek luar negeri disetujui', 'ops_agent_1'),
            ('PC-2026-JKT-0001', 'BEA_CUKAI', 'RKSP / BC 1.0', 'RKSP-002198', ?, 'APPROVED', 'rksp_002198.pdf', '9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08', 'Disetujui sistem CEISA', 'ops_agent_1'),
            ('PC-2026-JKT-0001', 'BEA_CUKAI', 'Inward Manifest / BC 1.1', 'BC11-IN-7712', ?, 'APPROVED', 'bc11_in.pdf', '4b227777d4dd1fc61c6f884f48641d02b4d121d3fd328cb08b5531fcacdabf8a', 'Pos pendaftaran terbit', 'ops_agent_1'),
            ('PC-2026-JKT-0001', 'KARANTINA_KESEHATAN', 'MDH & Free Pratique', 'MDH-JKT-992', ?, 'APPROVED', 'free_pratique.pdf', 'ef2d127de37b942baad06145e54b0c619a1f22327b2ebbcfbec78f5564afe39d', 'SSCEC valid hingga 2027', 'ops_agent_1'),
            ('PC-2026-JKT-0001', 'IMIGRASI', 'Immigration Crew List', 'IMM-CL-2026-441', ?, 'APPROVED', 'crew_list.pdf', 'a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90', 'Crew list + paspor diperiksa TPI', 'ops_agent_1'),
            ('PC-2026-JKT-0001', 'BEA_CUKAI', 'Outward Manifest / BC 1.1', 'BC11-OUT-7713', ?, 'APPROVED', 'bc11_out.pdf', 'b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90a1', 'Manifes keluar diserahkan sebelum berlayar', 'ops_agent_1'),
            ('PC-2026-JKT-0001', 'KARANTINA_KESEHATAN', 'Port Health Clearance Out', 'PHC-OUT-2026-118', ?, 'APPROVED', 'phc_out.pdf', 'c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2', 'Pemeriksaan karantina keberangkatan selesai', 'ops_agent_1'),
            ('PC-2026-JKT-0001', 'INAPORTNET_SPS', 'Master Sailing Declaration', 'MSD-2026-552', ?, 'APPROVED', 'msd.pdf', 'd4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3', 'Ditandatangani nakhoda', 'ops_agent_1'),
            ('PC-2026-JKT-0001', 'INAPORTNET_SPS', 'SPOG Keluar', 'SPOG-OUT-2026-337', ?, 'APPROVED', 'spog_out.pdf', 'e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4', 'Olah gerak keluar disetujui Syahbandar', 'ops_agent_1'),
            ('PC-2026-JKT-0001', 'INAPORTNET_SPS', 'SPB (Surat Persetujuan Berlayar)', NULL, NULL, 'PENDING', NULL, NULL, 'Belum diterbitkan (kapal belum sandar)', NULL)
            """, (now_str, now_str, now_str, now_str, now_str, now_str, now_str, now_str, now_str))
            conn.execute("""
            INSERT INTO disbursements (port_call_id, category, item_name, pda_estimate, fda_actual, simponi_billing, ntpn) VALUES
            ('PC-2026-JKT-0001', 'PNBP_KEMENHUB', 'Uang Rambu & Labuh', 15000000, 15000000, 'BILL-99120', '9812A9B12874'),
            ('PC-2026-JKT-0001', 'BUP_PELINDO', 'Jasa Tambat, Pandu & Tunda', 55000000, 58200000, NULL, NULL),
            ('PC-2026-JKT-0001', 'CIGP_INSTANSI', 'Clearance Karantina & Imigrasi', 6000000, 6000000, NULL, NULL),
            ('PC-2026-JKT-0001', 'HUSBANDRY', 'Pasokan Air Tawar 150 Ton', 10500000, 10500000, NULL, NULL),
            ('PC-2026-JKT-0001', 'AGENCY_FEE', 'Official Agency Fee', 25000000, 25000000, NULL, NULL)
            """)

    # Migrasi data lama: bila sample PC sudah ada (DB dari versi lama, hanya
    # 5 item clearance), tambahkan item gate SPB yang belum ada sebagai
    # APPROVED dengan nomor contoh, agar sample tetap lolos gate SPB.
    gate_items = [
        ("IMIGRASI", "Immigration Crew List", "IMM-CL-2026-441", "Crew list + paspor diperiksa TPI"),
        ("BEA_CUKAI", "Outward Manifest / BC 1.1", "BC11-OUT-7713", "Manifes keluar diserahkan sebelum berlayar"),
        ("KARANTINA_KESEHATAN", "Port Health Clearance Out", "PHC-OUT-2026-118", "Pemeriksaan karantina keberangkatan selesai"),
        ("INAPORTNET_SPS", "Master Sailing Declaration", "MSD-2026-552", "Ditandatangani nakhoda"),
        ("INAPORTNET_SPS", "SPOG Keluar", "SPOG-OUT-2026-337", "Olah gerak keluar disetujui Syahbandar"),
    ]
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with conn:
        for agency, doc_type, doc_number, remarks in gate_items:
            row = cursor.execute(
                "SELECT 1 FROM clearances WHERE port_call_id = ? AND doc_type = ?",
                (sample_id, doc_type),
            ).fetchone()
            if not row:
                conn.execute(
                    "INSERT INTO clearances (port_call_id, agency, doc_type, doc_number, submission_time, approval_time, status, remarks, submitted_by) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'APPROVED', ?, 'ops_agent_1')",
                    (sample_id, agency, doc_type, doc_number, now_str, now_str, remarks),
                )
        # Normalisasi nama lama agar cocok dengan matcher gate SPB.
        conn.execute(
            "UPDATE clearances SET doc_type = 'PKKA / Pemberitahuan Kedatangan Kapal Asing' "
            "WHERE port_call_id = ? AND doc_type = 'PPKKA'",
            (sample_id,),
        )
        conn.execute(
            "UPDATE clearances SET doc_type = 'RKSP / BC 1.0' "
            "WHERE port_call_id = ? AND doc_type = 'BC 1.0 (RKSP)'",
            (sample_id,),
        )
        conn.execute(
            "UPDATE clearances SET doc_type = 'Inward Manifest / BC 1.1' "
            "WHERE port_call_id = ? AND doc_type = 'BC 1.1 Masuk'",
            (sample_id,),
        )

    conn.close()


def log_audit(conn, port_call_id, user_id, action, old_value=None, new_value=None):
    """Tulis satu baris ke audit_logs. Dipanggil di dalam transaksi yang sama
    dengan perubahan yang diaudit, agar audit tidak pernah tertinggal."""
    conn.execute(
        "INSERT INTO audit_logs (port_call_id, user_id, action, old_value, new_value, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (port_call_id, user_id or "system", action, old_value, new_value,
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )


def calculate_file_sha256(content_bytes: bytes) -> str:
    """Menghasilkan hash SHA-256 untuk pembuktian integritas berkas (Putusan Q18)."""
    return hashlib.sha256(content_bytes).hexdigest()
