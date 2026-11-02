# Keamanan — Sistem Keagenan Kapal

Dokumen ini menjelaskan postur keamanan sistem ini dan apa yang TIDAK
dijaminnya. Sistem ini adalah **tracker internal** tim keagenan, bukan
portal resmi pemerintah: pengajuan resmi tetap dilakukan di
Inaportnet / INSW / CEISA / Simkapel / SINKARKES.

## Yang sudah ada

- **Autentikasi session cookie** (ADR-0011): cookie di-sign HMAC dengan
  `SESSION_SECRET` (env, wajib diisi — server gagal start bila kosong),
  `HttpOnly`, `SameSite=Lax`, `Secure` bila lewat HTTPS, berlaku 12 jam.
  Semua route `/api/*` (kecuali `/api/login` dan `/api/health`) menolak
  tanpa session valid. Role: OPS, FINANCE, ADMIN — aksi sensitif
  (terbit SPB, close-ops, close-finance) dibatasi per role di server,
  bukan hanya disembunyikan di UI.
- **Rate limit login**: 5 kali gagal per (username, IP) dalam 15 menit
  → 429. Rate limit ini in-memory per instance — di Vercel tidak global
  antar instance (limitasi yang didokumentasikan, ADR-0016).
- **Validasi input** (ADR-0019): nilai enum dari payload (status/agency
  clearance, kode milestone, waktu laytime) dinormalisasi + di-whitelist
  sebelum query. Body JSON rusak → 400 eksplisit. Safety net global di
  `do_GET`/`do_POST` mengubah exception tak terduga menjadi 500 JSON —
  koneksi tidak pernah ditutup diam-diam.
- **Security headers**: `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`
  di semua respons JSON, halaman login, dan UI.
- **XSS**: semua teks dari DB di-escape sebelum masuk DOM (ADR-0016).
- **Hash berkas**: `file_sha256` hanya terisi bila ada upload berkas
  sungguhan (belum ada fiturnya). Server TIDAK membuat hash dari string
  referensi dan menampilkannya sebagai "bukti integritas" (ADR-0016);
  UI menampilkan "Tidak ada berkas" bila kolom kosong.
- **Kunci CLOSED**: arsip yang sudah CLOSED terkunci untuk milestone,
  dokumen, biaya, dan SPB — kecuali penyelesaian sengketa FDA yang sudah
  terbuka (FINANCE/ADMIN).

## Yang belum / sengaja tidak ada

- **CSP ketat**: UI memakai inline `<script>` + `onclick`, jadi CSP
  tanpa `unsafe-inline` akan memecahkan UI. CSP yang berarti menunggu
  ekstraksi JavaScript inline — pekerjaan terpisah, bukan satu baris header.
- **Token CSRF**: cookie `SameSite=Lax` sudah memblokir pengiriman cookie
  pada cross-site POST di browser modern, jadi CSRF form sudah tertahan.
  Token CSRF adalah defense in depth, bukan prioritas.
- **Pemeriksaan ulang session ke database**: session bersifat stateless
  (ADR-0011) — user yang dinonaktifkan atau role-nya diubah tetap sah
  sampai cookie habis (maks. 12 jam). Untuk tim kecil ini diterima;
  untuk akses yang lebih ketat, cabut dengan mengganti `SESSION_SECRET`
  (membatalkan SEMUA session aktif).
- **Enkripsi data-at-rest**: database Turso diakses lewat TLS
  (`libsql://`); mode SQLite lokal tidak dienkripsi — jangan simpan file
  `agency.db` di tempat yang bisa diakses orang lain.
- **Backup otomatis**: tidak ada. Data produksi ada di Turso — backup
  dengan `turso db dump agency-yansen` berkala. Menyalin `agency.db`
  lokal TIDAK membackup data produksi.

## Kredensial default — WAJIB diganti sebelum data real

User default `ops_jakarta` / `fin_jakarta` dibuat saat seed dengan
password default yang terdokumentasi di README (`OpsJakarta2026` /
`FinJakarta2026`, atau dari env `SEED_OPS_PASSWORD` /
`SEED_FIN_PASSWORD` bila diisi). Ganti setelah login pertama:

```bash
python scripts/create_user.py ops_jakarta --update
python scripts/create_user.py fin_jakarta --update
```

Jangan pernah menjalankan sistem dengan password default dan data real.
Jangan commit `SESSION_SECRET`, token Turso, atau password ke repo.

## Melaporkan kerentanan

Laporkan langsung ke pemilik repo (bukan lewat issue publik bila
menyangkut data produksi): sertakan endpoint, payload yang dipakai,
dan respons yang diterima. Jangan menguji kerentanan terhadap deployment
produksi dengan data real — gunakan instance lokal
(`AGENCY_DB_FILE` terpisah) untuk reproduksi.
