# ADR-0013: Server Lokal Multi-Threaded

Tanggal: 2026-11-01
Status: Diterima
Cabang: `server-multi-thread`

## Konteks

`run_server()` memakai `socketserver.TCPServer` single-threaded: request
diproses satu per satu secara berurutan. Satu koneksi yang terhubung tapi
tidak mengirim request (mis. preconnect browser) memblokir SEMUA request
berikutnya sampai koneksi itu ditutup. Ditemukan saat verifikasi UI mobile
(ADR-0012): server tes port 8899 benar-benar hang dan harus di-restart.
Untuk server lokal yang dipakai bergantian oleh browser + curl + tes,
perilaku ini tidak bisa diterima.

## Keputusan

`run_server()` memakai `socketserver.ThreadingTCPServer` lewat subclass
kecil `_ThreadedServer` di `server.py`:

- Satu thread per koneksi; request lain tetap dilayani walau satu
  koneksi menggantung.
- `daemon_threads = True` — Ctrl+C / shutdown tidak menunggu koneksi
  yang masih terbuka.
- `allow_reuse_address = True` sebagai atribut class (bukan lagi
  mutasi `socketserver.TCPServer.allow_reuse_address` global seperti
  sebelumnya) — restart cepat tidak kena TIME_WAIT.
- Jalur WSGI/Vercel (`application()`) tidak berubah; perubahan ini
  hanya untuk `run_server()` lokal.

## Mengapa aman untuk thread

- `get_db()` mode lokal membuat koneksi SQLite **baru per panggilan** —
  tidak ada koneksi yang dipakai lintas thread.
- Mode remote: koneksi libsql di-cache per thread (`threading.local`),
  dan transaksi tertinggal di-rollback sebelum dipakai ulang.
- Kesiapan aplikasi (`_application_ready`) sudah memakai
  `threading.Lock`; rate limit login memakai lock sendiri
  (`engine/auth.py`).
- `AgencyAPIHandler` tidak punya state mutable yang dipakai bersama
  antar request (satu instance handler per request).

Batasan yang disadari: SQLite hanya mengizinkan satu penulis dalam
satu waktu; dua tulis bersamaan bisa menunggu lock (default timeout
5 detik). Untuk server lokal uji coba dengan beban ringan ini cukup;
bukan masalah untuk produksi karena deploy produksi memakai Vercel +
Turso, bukan server ini.

## Verifikasi

- Uji kunci (skenario persis yang dulu membuat hang): satu koneksi
  dibuka dan ditahan 25 detik tanpa mengirim apa pun — selama itu,
  3× GET `/` berurutan tetap 200 dalam ~1 ms, 5 request paralel
  (login + GET) semua 200, dan GET `/api/port-calls` dengan cookie
  session 200. Sebelum perbaikan, request pertama sudah menggantung.
- Suite tetap hijau: `test_api.py` 2× berturut-turut lulus,
  `test_laytime.py`, `test_libsql_backend.py`.
- Selama verifikasi ketemu bug ketahanan tes (bukan bug server):
  `test_api.py` memilih IMO uji dari pool tetap 4 nilai, dan setelah
  4 run semua nilai sudah terpakai di DB sehingga `next()` melempar
  `StopIteration`. Diperbaiki di commit terpisah: bila pool habis,
  IMO diambil dari timestamp (`"9" + str(int(time.time()))[-6:]`,
  unik per detik) supaya suite bisa diulang tanpa batas.
