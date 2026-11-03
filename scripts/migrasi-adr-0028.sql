-- Migrasi cepat ADR-0028 (tanpa redeploy): index untuk filter tab daftar
-- port call. Jalankan sekali di Turso bila daftar terasa lambat:
--   turso db shell agency-yansen < scripts/migrasi-adr-0028.sql
-- Idempoten (IF NOT EXISTS). DB baru/lokal mendapatkannya otomatis dari
-- init_db(); index hanya mempercepat query — tanpanya pun daftar tetap
-- jalan (filter + paginasi tidak bergantung pada index ini).
CREATE INDEX IF NOT EXISTS idx_port_calls_status_closed
    ON port_calls(status, finance_closed_at, created_at);
