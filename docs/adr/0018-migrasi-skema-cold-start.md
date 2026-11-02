# ADR-0018: Cold start melengkapi skema DB lama (migrasi tabel baru)

Tanggal: 2 November 2026
Status: Diterima
Konteks: Setelah PR #11 (ADR-0017) di-merge ke main, Vercel men-deploy kode
baru — tapi detail port call di situs live tidak ter-load. Push dan merge
berhasil (main `fb74ed3b`); yang tertinggal adalah skema database Turso.

## Diagnosis (terverifikasi, bukan dugaan)

1. **Kode baru sudah live.** `GET /api/shift-board` di Vercel menjawab 401
   (rute ada, butuh login) — bukan 404. GitHub main = `fb74ed3b`, merge
   commit PR #11 (parents `54b6189` + `92d9b80`).
2. **Detail 500 di jalur Vercel.** Endpoint detail meng-query
   `spb_history` (server.py:602) dan `laytime_calculations` (server.py:606)
   tanpa syarat, begitu pula handover (server.py:661). Di Turso kedua
   tabel itu tidak ada → `no such table` → adapter WSGI menjawab 500
   `{"error": "Kesalahan internal server"}`. Daftar port call dan papan
   shift tetap tampil karena tidak menyentuh kedua tabel itu — persis
   gejala yang dilaporkan ("data tidak ter-load" begitu satu port call
   dibuka).
3. **Penyebab: cold start tidak pernah membuat tabel baru di DB lama.**
   `_prepare_database_once()` (ADR-0010) hanya COUNT `master_ports` +
   `port_calls`; bila keduanya > 0, `init_db()` tidak pernah jalan. Dua
   tabel ADR-0017 hanya ada di `init_db()`, jadi tidak pernah dibuat di
   Turso `agency-yansen` yang sudah berisi data sejak 31 Okt 2026.
4. **Asimetri lokal vs Vercel.** `run_server()` lokal memanggil
   `init_db()` tanpa syarat saat start — jadi di laptop/VM semuanya
   jalan. Terbukti saat reproduksi: DB lokal yang dua tabelnya di-DROP,
   setelah server lokal jalan kedua tabel muncul lagi dan detail
   menjawab 200.
5. **Reproduksi jalur Vercel (sebelum perbaikan).** Skrip WSGI
   (`application()` + cookie session asli, DB skema lama) pada kode
   `fb74ed3b`: detail menjawab **500 Internal Server Error**, kedua tabel
   tetap tidak ada. Pada kode yang diperbaiki: **200 OK**, kedua tabel
   dibuat.

## Perbaikan

### 1. Cepat, di Turso (tanpa deploy ulang)

`scripts/migrasi-adr-0017.sql` — dua `CREATE TABLE IF NOT EXISTS`
(`spb_history`, `laytime_calculations`, disalin persis dari
`engine/db.py`). Dijalankan sekali, pilih salah satu:

- Turso CLI: `turso db shell agency-yansen < scripts/migrasi-adr-0017.sql`
- Dashboard Turso: database `agency-yansen` → SQL Editor → tempel isi
  file → Run

Aman diulang; data lama tidak tersentuh. Setelah ini, situs live langsung
normal lagi tanpa deploy ulang — Vercel tidak perlu diapa-apakan.

### 2. Permanen, di kode (supaya tidak terulang)

`_prepare_database_once()` kini, setelah COUNT, memeriksa seluruh daftar
`TABLES` (16 tabel, `engine/db.py` — daftar ini sejak awal dikomentari
sebagai "dipakai init_db() dan pemeriksaan kesiapan DB saat cold start",
tapi tidak pernah dipakai untuk itu) lewat satu query
`SELECT name FROM sqlite_master WHERE type = 'table'`. Bila ada yang
hilang → `init_db()` (idempotent) melengkapinya. Biaya bila skema sudah
lengkap: 1 round-trip; bila ada yang hilang: `init_db()` sekali, cold
start berikutnya kembali murah. Penambahan tabel berikutnya otomatis
ikut terlengkapi.

### 3. Jebakan adapter yang ketahuan oleh tes

Versi pertama perbaikan mengiterasi cursor langsung
(`{r[0] for r in conn.execute(...)}`) — jalan di sqlite3, tapi
`_LibSQLCursor` tidak iterable, sehingga `test_libsql_backend.py` gagal
dan — lebih penting — cold start di Turso akan 500 di SEMUA request.
Diperbaiki dengan `.fetchall()` (ada di kedua adapter). Pelajaran:
setiap query baru di jalur cold start harus lolos suite libsql, bukan
hanya suite API (yang memakai sqlite3).

## Verifikasi

- Tes baru 34/34: DB skema lama (dua tabel di-DROP) → panggil
  `_prepare_database_once()` → kedua tabel ada, jumlah port call tidak
  berubah, panggilan kedua tetap lulus.
- `tests/test_api.py` 34/34, `test_laytime.py` lulus,
  `test_libsql_backend.py` lulus (setelah perbaikan `.fetchall()`).
- Repro WSGI sebelum/sesudah (di atas): 500 → 200.
- SQL migrasi diuji di sqlite lokal, termasuk dijalankan dua kali
  (idempotent).

## Yang tidak diubah

- Tidak ada perubahan skema selain dua tabel yang memang sudah ada di
  `init_db()` sejak ADR-0017 — tidak ada tabel ketiga, tidak ada
  perubahan kolom.
- Token Turso tidak disentuh dan tidak disimpan di repo.
- Sisa ADR-0010 tetap berlaku: cold start tetap 1 COUNT + 1 query
  `sqlite_master` bila skema lengkap.
