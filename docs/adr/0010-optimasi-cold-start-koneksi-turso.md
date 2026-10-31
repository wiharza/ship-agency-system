# ADR-0010: Optimasi cold start Vercel + pemakaian ulang koneksi Turso

- **Status:** Diterima (1 Nov 2026)
- **Konteks:** ADR-0009 (backend Turso opsional)
- **Keputusan terkait:** Q40

## Konteks

Setelah env Turso diisi di Vercel, setiap respons terasa lambat. Pengukuran
langsung di deployment (31 Okt 2026, 23:39 WIB): request pertama
`POST close-ops` butuh **~39 detik**. Dua penyebab, keduanya dihitung:

1. **Cold start menjalankan init penuh.** `_ensure_database_ready()` selalu
   memanggil `init_db()` + `seed_sample_data()`: 13 `CREATE TABLE IF NOT
   EXISTS` + belasan `SELECT COUNT` + `INSERT`, masing-masing satu
   round-trip ke Turso (~600 ms dari VM ini).
2. **Tiap `get_db()` membuka koneksi remote baru.** `libsql.connect()` ke
   Turso remote butuh ~1,5 dtk (hangat) sampai ~7 dtk (dingin), dan setiap
   handler API memanggil `get_db()` — jadi tiap request membayar koneksi baru.

## Keputusan

### 1. Pemeriksaan kesiapan murah (server.py `_prepare_database_once()`)

Cold start sekarang hanya menjalankan 2 query:

- `SELECT name FROM sqlite_master WHERE type='table'` — bila semua tabel
  di `TABLES` (engine/db.py, satu sumber kebenaran, 13 tabel) ada, lanjut;
  bila ada yang kurang, jalankan `init_db()` + `seed_sample_data()` penuh.
- `SELECT COUNT(*) FROM port_calls` — bila 0, jalankan `seed_sample_data()`
  saja; bila > 0, tidak ada lagi yang dijalankan.

Bila koneksi remote putus di tengah cold start, koneksi yang di-cache
dibuang dan percobaan diulang sekali (`_ensure_database_ready()`).

### 2. Koneksi remote dipakai ulang per thread (engine/db.py)

`get_db()` meng-cache koneksi libsql remote di `threading.local`:

- **Thread-local, bukan global tunggal** — supaya tidak ada dua thread yang
  memakai satu koneksi yang sama (server lokal memakai ThreadingHTTPServer;
  runtime Vercel pun bisa memakai beberapa thread). Bila tiap request
  memakai thread baru, perilaku kembali seperti sebelum optimasi (tidak
  lebih buruk).
- **Mode lokal (sqlite3) dan mode file libsql tidak di-cache** — connect()
  file lokal murah dan perilakunya harus tetap seperti sebelumnya.
- **`close()` menjadi no-op untuk koneksi shared** — semua handler server.py
  memanggil `conn.close()` di akhir; close() yang sungguhan akan memutus
  cache. Satu-satunya jalan memutus koneksi cache adalah
  `drop_remote_connection()`.
- **Transaksi tertinggal dibatalkan** — bila koneksi yang dipakai ulang
  masih `in_transaction` (request sebelumnya gagal sebelum commit),
  `get_db()` melakukan rollback dulu.
- **Koneksi stale dibuang di batas request** — bila handler melempar
  exception, `application()` memanggil `drop_remote_connection()` sebelum
  mengembalikan 500, supaya request berikutnya membuka koneksi baru dan
  tidak mengulang kegagalan yang sama selamanya.

## Konsekuensi

Diukur ulang melawan Turso remote sungguhan (DB `agency-yansen`,
ap-southeast-1), proses baru, 1 Nov 2026:

| Request | Sebelum | Sesudah |
|---|---|---|
| Pertama (cold start) | ~39 dtk | **2,53 dtk** |
| Kedua (warm) | — (tiap request buka koneksi baru) | **0,44 dtk** |
| POST close-ops (gate 400) | 39,45 dtk | **0,45 dtk** |

Sisa latensi (~0,45 dtk/request hangat) adalah satu round-trip jaringan
ke Turso Singapura per query — tidak bisa dihilangkan tanpa mengubah
arsitektur (mode embedded replica: file SQLite lokal + sinkronisasi
berkala ke Turso; dicatat sebagai opsi lanjutan, bukan bagian ADR ini).

## Verifikasi

- `tests/test_api.py` 16/16 lulus (mode lokal tidak berubah).
- `tests/test_libsql_backend.py` lulus, termasuk tes cache remote baru
  (objek sama dipakai ulang; `close()` tidak memutus; `drop_remote_connection()`
  memaksa koneksi baru) — tes cache hanya jalan bila
  `TURSO_REMOTE_TEST_URL` + `TURSO_REMOTE_TEST_TOKEN` terisi di env,
  selain itu SKIP.
- Mode lokal (tanpa env Turso) tetap jalan; WSGI lokal 200.

## Risiko yang diterima

- Bila stream hrana koneksi yang di-cache mati saat instance idle, **satu**
  request gagal (500) sebelum koneksi dibuang dan request berikutnya pulih.
  Ini diterima: alternatifnya (probe `SELECT 1` di tiap request) menambah
  satu round-trip ke setiap request.
- `close()` no-op hanya berlaku untuk koneksi remote shared; koneksi file
  lokal dan SQLite tetap ditutup normal.
