-- Migrasi ADR-0017 untuk database Turso yang sudah ada (agency-yansen).
--
-- MASALAH: dua tabel baru ADR-0017 (spb_history, laytime_calculations)
-- hanya dibuat oleh init_db(). Server lokal selalu memanggil init_db()
-- saat start (run_server), TAPI jalur Vercel (WSGI) hanya menjalankan
-- _prepare_database_once() yang sekadar COUNT master_ports + port_calls —
-- bila keduanya > 0, init_db() tidak pernah jalan, tabel baru tidak pernah
-- dibuat, dan endpoint detail/handover gagal 500 "no such table".
-- Daftar port call tetap tampil (tidak menyentuh tabel baru), tapi begitu
-- satu port call dibuka, detailnya tidak ter-load.
--
-- PERBAIKAN CEPAT (tanpa deploy ulang): jalankan file ini SEKALI ke Turso,
-- pilih salah satu cara:
--   Turso CLI:   turso db shell agency-yansen < scripts/migrasi-adr-0017.sql
--   Dashboard:   buka database agency-yansen -> SQL Editor -> tempel isi file ini -> Run
-- Aman diulang: CREATE TABLE IF NOT EXISTS — bila tabel sudah ada, tidak
-- terjadi apa-apa, data lama tidak tersentuh.
--
-- PERBAIKAN PERMANEN (kode): ADR-0018 — _prepare_database_once() kini
-- memeriksa seluruh daftar TABLES lewat sqlite_master dan memanggil
-- init_db() bila ada tabel yang hilang, supaya penambahan tabel
-- berikutnya tidak mengulang kejadian ini.

CREATE TABLE IF NOT EXISTS spb_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    port_call_id TEXT NOT NULL,
    doc_number TEXT,
    issued_at TEXT NOT NULL,
    expiry_at TEXT,
    superseded_at TEXT NOT NULL,
    superseded_by TEXT NOT NULL,
    FOREIGN KEY (port_call_id) REFERENCES port_calls (id)
);

CREATE TABLE IF NOT EXISTS laytime_calculations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    port_call_id TEXT NOT NULL,
    nor_tendered_time TEXT,
    commenced_time TEXT,
    completed_time TEXT,
    terms TEXT,
    allowed_days REAL,
    used_days REAL,
    claim_type TEXT,
    amount_usd REAL,
    calculated_by TEXT,
    calculated_at TEXT NOT NULL,
    FOREIGN KEY (port_call_id) REFERENCES port_calls (id)
);
