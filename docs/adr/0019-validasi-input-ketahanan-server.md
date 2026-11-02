# ADR-0019: Validasi input + ketahanan server — whitelist enum, safety net 500 JSON, security headers, /api/health

Tanggal: 2 November 2026
Status: Diterima
Konteks: Tinjauan keamanan eksternal atas sistem ini (dokumen rekomendasi
yang ditempel pengguna, 2 Nov 2026). Setiap klaim diverifikasi dulu
terhadap kode + probe langsung ke server lokal (DB sementara) sebelum
diperbaiki — tiga klaim ternyata tidak akurat / sudah ditangani (lihat
"Yang dicek dan ternyata tidak perlu diperbaiki"). Yang dikerjakan di sini
adalah temuan yang TERBUKTI, bukan semua yang ditulis reviewer.

## 1. Status/agency clearance tidak divalidasi — DIPERBAIKI (kritis)

`POST /api/clearances/<id>/update` mengambil `status` mentah dari payload
dan `POST /api/port-calls/<id>/clearance` mengambil `agency` + `status`
mentah. Nilai di luar CHECK constraint (`"approved"` huruf kecil,
`"POLISI"`) melempar `sqlite3.IntegrityError` di tengah transaksi — di
server lokal koneksi ditutup tanpa respons JSON sama sekali (HTTP 000,
terbukti probe), di Vercel 500.

Perbaikan:
- Helper baru di `server.py` (ADR-0019): `_norm_enum(value, allowed,
  field)` — normalisasi `strip().upper()` + whitelist, melempar
  `_BadRequest` (→ 400 JSON) bila bukan teks / tidak dikenal;
  `_opt_text` / `_req_text` untuk teks opsional/wajib;
  `_strip_or_empty` untuk field lama yang memakai
  `(payload.get(...) or "").strip()` (nilai non-string dulu
  `AttributeError`).
- Konstanta: `CLEARANCE_STATUSES` (PENDING, SUBMITTED, APPROVED,
  REJECTED, EXPIRED), `CLEARANCE_AGENCIES` (7 instansi sesuai CHECK di
  `engine/db.py`), `MILESTONE_CODES` (11 kode dari `<select>` UI).
- Diterapkan di: update clearance, tambah clearance, SPB (`doc_number`),
  sengketa (`reason`), master vessels, login (`username`), resolusi
  sengketa (`resolution_notes`), disbursement (`item_name`).
- F-string query dinamis (`server.py` master-data, `engine/db.py`
  `_count`): TERBUKTI hanya menginterpolasi tuple/nama konstan — bukan
  kerentanan. Ditambah komentar `WHITELIST ONLY` supaya pola ini tidak
  disalahgunakan nanti.

## 2. Milestone tanpa code + laytime tanpa waktu — DIPERBAIKI (kritis)

- Milestone tanpa `milestone_code` dulu melempar `IntegrityError`
  (NOT NULL) dan menutup koneksi (probe [G]); kode sembarang juga lolos
  ke SOF. Kini kode WAJIB + di-whitelist (11 kode); huruf kecil
  dinormalisasi (`"eosp"` → `EOSP`).
- Laytime tanpa body LEBIH PARAH dari yang ditulis reviewer: tidak 500,
  tapi **200 dan menyimpan milestone dengan tanggal demo hardcoded**
  (NOR 2026-11-01 08:00, selesai 2026-11-04 14:00) sebagai fakta
  lapangan. Kini ketiga waktu (`nor_tendered_time`, `commenced_time`,
  `completed_time`) WAJIB diisi — default tanggal demo dihapus. UI tidak
  terdampak: formulir laytime selalu mengirim ketiganya.

## 3. JSON malformed ditelan diam-diam — DIPERBAIKI (separuh benar)

Klaim reviewer ("bisa menjalar ke 500") tidak terbukti di jalur yang
diprobe: body `{bukan json` ke `/api/login` menghasilkan 400
"Username dan password wajib diisi", karena `do_POST` menelan error
JSON (`payload = {}`) lalu endpoint gagal di validasi field-nya. Tapi
perilaku menelan itu nyata dan berbahaya di endpoint lain (lihat §2:
laytime jalan dengan default). Kini: body bukan JSON → 400 "Body harus
berupa JSON valid"; body bukan objek JSON (`[1,2,3]`) → 400 "Body JSON
harus berupa objek"; body bukan UTF-8 → 400. Batas 1 MB sudah enforced
sebelumnya (`MAX_BODY_BYTES` → 413).

## 4. Safety net global — DIPERBAIKI

`do_GET`/`do_POST` kini wrapper tipis di atas `_do_get_impl` /
`_do_post_impl`: exception tak terduga → 500 JSON, `_BadRequest` →
400 JSON. Tidak ada lagi koneksi yang ditutup tanpa respons. Jaring
pengaman ini juga menutup ekor panjang pola yang sama di handler yang
belum divalidasi satu per satu. (Di jalur WSGI, adapter sudah
mengembalikan 500 JSON — wrapper ini menyamakan perilaku server lokal.)

## 5. Security headers — DIPERBAIKI (kecuali CSP ketat)

`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
`Referrer-Policy: strict-origin-when-cross-origin` kini dikirim di
semua respons JSON, halaman login, dan UI (`_security_headers()`).
Koreksi terhadap rekomendasi reviewer: **CSP ketat DITUNDA** — UI
memakai inline `<script>` + `onclick`, jadi CSP tanpa `unsafe-inline`
akan memecahkan UI. CSP yang berarti menunggu ekstraksi JavaScript
inline (pekerjaan terpisah, bukan satu baris header).

## 6. /api/health + SECURITY.md — DITAMBAH

- `GET /api/health` tanpa auth: 200 `{"status": "ok", "db": "ok",
  "session_secret": "ok"}`; 503 bila DB / `SESSION_SECRET` gagal.
  Tanpa versi / stack trace.
- `SECURITY.md` di root repo: postur keamanan, yang sengaja tidak ada
  (CSP ketat, token CSRF, cek ulang session ke DB, enkripsi at-rest
  SQLite lokal, backup otomatis), peringatan kredensial default, dan
  cara melaporkan kerentanan.

## Yang dicek dan ternyata tidak perlu diperbaiki

- **Path traversal — TIDAK ADA celahnya.** `do_GET` tidak pernah
  mendelegasikan ke `SimpleHTTPRequestHandler`; semua path tak dikenal
  → 404 JSON; satu-satunya file yang disajikan adalah `ui/index.html`
  dari path tetap. Probe: `/..%2f..%2fagency.db`, `/server.py`,
  `/ui/index.html` → semua 404. Pewarisan class itu vestigial.
- **Hash integritas di UI — SUDAH DITANGANI (ADR-0016).** Bila
  `file_sha256` NULL, UI menampilkan "Tidak ada berkas" + tooltip
  penjelas (`ui/index.html`). Menyembunyikan kolom justru menghilangkan
  informasi.
- **Koreksi ETA / charter party — SUDAH ADA (ADR-0017).** Kunci CLOSED
  + audit (`PORT_CALL_UPDATE`, `CHARTER_PARTY_UPDATE`) sudah ada;
  Tes 30 mengujinya.
- **Sesi/CSRF — SUDAH CUKUP AMAN.** Cookie `SameSite=Lax` memblokir
  pengiriman cookie pada cross-site POST di browser modern; token CSRF
  hanya defense in depth, bukan urgent.
- **Backup — rekomendasi dikoreksi.** Menyalin `agency.db` lokal tidak
  membackup data produksi (ada di Turso). Yang benar: `turso db dump`
  berkala (didokumentasikan di `SECURITY.md`).

## Yang SENGAJA tidak dikerjakan (keputusan pengguna)

- **Kredensial default:** pengguna memutuskan 2 Nov 2026 — kecuali
  default credential, masalah lain boleh diperbaiki. Memblokir aksi
  sensitif sampai password diganti akan menghambat uji coba yang
  sedang berjalan (keputusan 1 Nov 2026: "tidak perlu risau, masih uji
  coba, belum ada data real"). Yang dilakukan: `SECURITY.md` menegaskan
  password default WAJIB diganti sebelum data real masuk. Banner
  peringatan di UI tidak ikut dibuat — itu pun bagian dari paket
  "default credential" yang ditunda.
- **Rate limit endpoint sensitif, token CSRF, pip-audit/bandit di CI:**
  nilai rendah (rate limit membatasi otomasi, bukan penyalahgunaan
  pemilik akun sah; codebase stdlib-only, praktis tanpa dependensi).

## Follow-up: drain body sebelum 413 (ditemukan lewat CI)

Setelah ADR-0019 di-push, CI gagal di Tes 26 dengan `BrokenPipeError`
padahal suite sudah sampai di print TEST 25 — bukan assertion yang
gagal, melainkan koneksi putus saat client mengunggah body 1,1 MB.
Penyebab: cabang 413 di `_do_post_impl` menjawab TANPA membaca body.
Server menutup koneksi dengan body belum terbaca → kernel mengirim RST
→ client yang masih mengunggah mendapat Broken pipe. Terbukti di probe
lokal: 15/30 POST body 1,1 MB gagal `URLError`, 0 traceback server
(persis gejala CI). Perbaikan: `_drain_body()` membuang body (maks 8 MB)
sebelum menjawab 413; Tes 26 kini mengulang 413 sebanyak 20x (race-nya
~50%, sekali lolos tidak membuktikan apa-apa). Setelah perbaikan: probe
30/30 bersih 413, suite 36/36.

Catatan: kegagalan CI pertama (di Tes 13b, SPB setelah approve
clearance) TIDAK terjelaskan oleh mekanisme ini — body di sana kecil
dan server sudah membacanya. Kegagalan itu tidak pernah ter-reproduksi
lokal (5/5 suite + 30/30 pengulangan alur Tes 13b lolos, log server
bersih) dan tidak ada jejak server-side-nya; kemungkinan besar
ketidakstabilan transport yang sama di runner CI. Bila Tes 13b gagal
lagi setelah perbaikan ini, langkah berikutnya adalah diagnostik
koneksi, bukan menebak lagi.

## Verifikasi

- Tes API 36/36 (Tes 35–36 baru: validasi whitelist/normalisasi
  clearance + milestone, laytime wajib 3 waktu, JSON rusak 400,
  server tetap hidup sesudahnya; `/api/health` tanpa auth + security
  headers di API, UI, halaman login), laytime lulus, libsql backend
  lulus.
- Probe ulang ke server lokal (DB sementara): status `"approved"` →
  200 (ternormalisasi APPROVED); agency `"POLISI"` → 400 menyebut
  daftar instansi; milestone tanpa code → 400 menyebut 11 kode;
  `doc_number` angka → 400 "harus berupa teks"; laytime `{}` → 400
  dan TIDAK ADA milestone demo tersimpan; body rusak → 400 "Body harus
  berupa JSON valid"; ketiga security header hadir;
  `/api/health` → 200 tanpa login; **0 traceback** di log server.
