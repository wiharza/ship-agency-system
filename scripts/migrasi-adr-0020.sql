-- Migrasi ADR-0020: CHECK status clearances mengenal 'VOID'.
-- Dijalankan SEKALI di Turso yang sudah ada (atau agency.db lokal lama).
--   turso db shell agency-yansen < scripts/migrasi-adr-0020.sql
--   sqlite3 agency.db < scripts/migrasi-adr-0020.sql
-- SQLite tidak bisa mengubah CHECK lewat ALTER TABLE; satu-satunya cara
-- adalah membangun ulang tabel (pola resmi: RENAME -> CREATE -> INSERT
-- SELECT -> DROP). Aman karena: tidak ada tabel lain yang mereferensikan
-- clearances, tidak ada indeks/trigger di clearances, INSERT SELECT
-- menyalin SEMUA kolom termasuk id (AUTOINCREMENT mengikuti max(id)).
-- Kode (init_db + cold start Vercel) melakukan hal yang sama secara
-- otomatis bila CHECK masih lama — script ini hanya jalan cepat tanpa
-- menunggu redeploy. Aman dijalankan dua kali: jalankan kedua membangun
-- ulang tabel yang sama dengan data yang sama; hasil akhirnya identik.
-- Bila ragu, cek dulu: SELECT sql FROM sqlite_master WHERE
-- name = 'clearances'; -- harus mengandung 'VOID' bila sudah termigrasi.
BEGIN;
DROP TABLE IF EXISTS clearances_old_void_mig;
ALTER TABLE clearances RENAME TO clearances_old_void_mig;
CREATE TABLE clearances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    port_call_id TEXT NOT NULL,
    agency TEXT NOT NULL CHECK(agency IN ('INAPORTNET_SIMPADU', 'INAPORTNET_SPS', 'BEA_CUKAI', 'IMIGRASI', 'KARANTINA_KESEHATAN', 'BARANTIN', 'BUP_PELINDO')),
    doc_type TEXT NOT NULL,
    doc_number TEXT,
    submission_time TEXT,
    approval_time TEXT,
    expiry_time TEXT,
    status TEXT NOT NULL CHECK(status IN ('PENDING', 'SUBMITTED', 'APPROVED', 'REJECTED', 'EXPIRED', 'VOID')),
    file_name TEXT,
    file_sha256 TEXT,
    remarks TEXT,
    submitted_by TEXT,
    FOREIGN KEY (port_call_id) REFERENCES port_calls (id)
);
INSERT INTO clearances SELECT * FROM clearances_old_void_mig;
DROP TABLE clearances_old_void_mig;
COMMIT;
-- Verifikasi: baris ini harus mengembalikan 1 baris dengan CHECK baru.
SELECT COUNT(*) AS jumlah_clearances, SUM(CASE WHEN status = 'VOID' THEN 1 ELSE 0 END) AS jumlah_void FROM clearances;
