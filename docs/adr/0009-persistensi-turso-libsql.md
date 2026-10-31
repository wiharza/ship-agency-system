# ADR-0009: Persistensi Turso (libSQL remote) sebagai backend opsional

- **Status:** Diterima (31 Okt 2026)
- **Konteks:** Di Vercel, disk serverless bersifat ephemeral: salinan
  `/tmp/agency.db` dari patch Vercel hanya hidup selama instance warm, lalu
  reset dan bisa berbeda antar-instance. Untuk demo itu cukup, tapi tidak
  untuk operasional nyata. Turso adalah libSQL (fork SQLite) yang di-hosting:
  data persisten dan sama untuk semua instance.
- **Keputusan:**
  1. `engine/db.py:get_db()` memilih backend dari environment: bila
     `TURSO_DATABASE_URL` + `TURSO_AUTH_TOKEN` terisi, pakai
     `libsql.connect(url, auth_token=token)` (mode remote); bila tidak ada,
     pakai SQLite lokal seperti sebelumnya. Lokal tetap jalan tanpa Turso
     dan tanpa dependensi tambahan.
  2. `libsql` (0.1.11) mengembalikan tuple polos tanpa `row_factory`,
     padahal `server.py` memakai `r["kolom"]`, `r[0]`, dan `dict(r)` di
     mana-mana. Maka koneksi libsql dibungkus `_LibSQLConnection` /
     `_LibSQLCursor` / `_Row` di `engine/db.py` yang meniru antarmuka
     sqlite3 (`execute`/`cursor`/`commit`/`rollback`/`close`, `with conn:`,
     `lastrowid`, `PRAGMA foreign_keys`). `server.py` tidak berubah.
  3. Dependensi baru hanya satu baris: `requirements.txt` berisi
     `libsql==0.1.11` (dibutuhkan Vercel saat deploy; lokal tetap stdlib).
  4. Token tidak pernah masuk Git: `.env.example` + `.gitignore` untuk
     `.env`; di Vercel lewat Environment Variables.
  5. Migrasi data sekali jalan: `scripts/migrate_to_turso.py` — menjalankan
     `init_db()` di Turso, menyalin INSERT dari dump SQLite lokal
     (diurutkan per tabel agar foreign key terpenuhi — `iterdump` tidak
     mengurutkan demikian), lalu memverifikasi jumlah baris per tabel.
     Tanpa `--force`, skrip batal bila Turso sudah berisi data.
- **Verifikasi (31 Okt 2026, tanpa akun Turso — mode file lokal libsql):**
  `tests/test_libsql_backend.py` lulus semua: `row_factory` (nama/indeks/
  `dict()`/`keys()`), foreign key ditegakkan, `with conn:` commit/rollback,
  `lastrowid`, dan alur server lewat WSGI (gate close-ops menolak tanpa SPB).
  Skrip migrasi diuji ke file libsql lokal: 42 INSERT tersalin, jumlah
  baris per tabel sama dengan lokal; rerun tanpa `--force` ditolak,
  dengan `--force` berhasil menimpa. Yang BELUM terverifikasi (butuh akun
  + token Turso asli): perilaku `PRAGMA foreign_keys` dan transaksi di
  koneksi remote, serta latensi per-request dari Vercel.
- **Alternatif yang dipertimbangkan:** mode *embedded replica* libsql
  (file lokal + `sync_url`, baca cepat + sync berkala) — ditunda karena
  menambah kerumitan sync; mode remote dulu, replica bila latensi terbukti
  jadi masalah. Postgres (Neon/Supabase) ditolak untuk saat ini karena
  memaksa penulisan ulang SQL (bukan SQLite) — lihat ADR-0003.
- **Biaya:** Tier gratis Turso (Starter): 500 database, 9 GB storage,
  1 miliar baris dibaca/bulan, 25 juta baris ditulis/bulan — jauh di atas
  kebutuhan aplikasi ini.
