-- Migrasi ADR-0025: tabel sertifikat kapal (vessel_certificates).
-- Jalankan sekali di Turso bila detail masih gagal setelah deploy
-- (cold start ADR-0018 seharusnya membuat tabel ini sendiri karena
-- namanya sudah ada di TABLES; skrip ini hanya jalan pintas manual).
-- Idempotent: CREATE TABLE IF NOT EXISTS.
CREATE TABLE IF NOT EXISTS vessel_certificates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vessel_imo TEXT NOT NULL,
    cert_type TEXT NOT NULL,
    cert_number TEXT,
    issued_date TEXT,
    expiry_date TEXT NOT NULL,
    notes TEXT,
    FOREIGN KEY (vessel_imo) REFERENCES master_vessels (imo)
);
