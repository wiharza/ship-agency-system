# ADR-0016: Audit keamanan & koreksi lanjutan (hash palsu, XSS, SHEX, ID, session, celah kecil)

Tanggal: 2 November 2026
Status: Diterima
Konteks: Audit lanjutan dari pengguna atas sistem (temuan bernomor 2–7;
temuan 1 adalah sign-off ops yang sudah ditangani ADR-0014/0015). Setiap
temuan diverifikasi dulu terhadap kode sebelum diperbaiki — dua klaim
ternyata sudah benar di kode (lihat "Yang dicek dan ternyata sudah benar").

## 2. "Hash bukti SHA-256" bukan hash berkas — DIHAPUS

Temuan terkonfirmasi, dan lebih parah dari yang dilaporkan: seed juga
mengandung hash palsu, termasuk SHA-256 dari string kosong
(`e3b0c44298fc...`) dan nama berkas `.pdf` yang tidak pernah ada.

- `server.py` (clearance update): `file_sha256` dihitung dari
  `f"{port_call_id}-{doc_type}-{doc_number}"` — variabelnya bahkan bernama
  `fake_content`.
- `server.py` (clearance tambah): `file_sha256` dari string yang sama
  (`fake_file_content`) + `file_name` dibuat-buat (`f"{doc_slug}.pdf"`).
- `engine/db.py` (seed): 9 baris clearance contoh membawa `file_name` +
  hash palsu.
- UI menampilkan kolom "Hash Integritas (SHA-256)" — untuk jejak audit
  10 tahun (Q27) ini menyesatkan: nomor dokumen yang sama selalu
  menghasilkan hash yang sama, tanpa ada berkas.

Perbaikan:
- Server tidak lagi membuat hash: create/update clearance membiarkan
  `file_sha256`/`file_name` NULL.
- Seed: 9 baris di-NULL-kan (seperti baris SPB yang memang sudah NULL).
- UI: kolom menjadi "Sidik jari (SHA-256)", sel menampilkan
  "Tidak ada berkas" bila NULL; bila suatu hari ada hash lama, judulnya
  menjelaskan itu sidik jari referensi, bukan bukti isi berkas.
- `calculate_file_sha256()` dipertahankan — fungsinya benar, hanya
  pemakaiannya yang salah. Docstring kini melarang pemanggilan dengan
  konten yang dibuat-buat; fungsi ini hanya untuk bytes berkas yang
  benar-benar diunggah (fitur upload belum ada).
- **Koreksi Q18:** janji "setiap berkas lampiran wajib dihitung hash
  SHA-256-nya" hanya berlaku bila ada berkas yang diunggah. Sampai fitur
  upload ada, kolom `file_sha256`/`file_name` kosong secara sah. PRD
  §4.2 dan §8 diperbaiki sesuai ini.

## 3. XSS tersimpan lewat innerHTML — DI-ESCAPE

Temuan terkonfirmasi: 11 situs `innerHTML` di `ui/index.html`
menyisipkan data dari server tanpa escape — `vessel_name`, `doc_number`,
`doc_type`, `remarks`, `item_name`, `principal_reason`, `ntpn`,
nama pelabuhan/kapal/terminal. Cookie HttpOnly tidak bisa dicuri, tetapi
skrip tertanam tetap bisa memanggil API sebagai user yang sedang login
(terbitkan SPB, tutup finance) — itu ancaman yang sebenarnya.

Perbaikan: helper `esc()` (escape `& < > " '`) di awal script, dipakai
di semua interpolasi data pada template `innerHTML`: daftar port call,
badge, tabel clearance (termasuk atribut `title`), tabel stoppage,
tabel finance, tabel dispute, baris checklist + daftar `blockers` +
`note`, dan semua `<option>` (port/kapal/terminal, nilai maupun teks).
Yang tidak diubah: `innerText`/`textContent` (sudah aman), nilai angka
(`Number(...)`), ID numerik di `onclick` (integer dari DB), dan teks
statis. Verifikasi render browser dengan data beracun (`<img onerror>`,
`<script>`, `<svg onload>`, `<iframe srcdoc>`, `xlink:href`): tidak ada
yang dieksekusi (`window.__xss` tidak pernah tersetel), tidak ada elemen
`img`/`svg`/`iframe` yang terbentuk, payload tampil sebagai teks biasa
(`&lt;img ...&gt;`), tanpa overflow di 390px.

## 4. SHEX disimpan tapi tidak dihitung — DIHITUNG (Minggu saja)

Temuan terkonfirmasi: `LaytimeCalculator` menyimpan `self.terms` dan
tidak pernah membacanya. ADR-0003 dan PRD §5.2 menjanjikan kalkulasi
dari klausul kontrak (SHINC/SHEX).

Perbaikan (`engine/laytime.py`):
- SHEX kini mengecualikan hari Minggu: setiap Minggu yang beririsan
  dengan `[actual_start, comp_time]` dideduksi penuh dan dicatat di
  `stoppage_details` sebagai `SHEX_SUNDAY` (tanggal, durasi, keterangan).
  Minggu ditentukan dari kalender — tidak butuh data eksternal.
- Hasil kalkulasi kini membawa `terms` dan `sundays_excluded`
  (`count`/`seconds`/`dates`), dan `laytime_used` membawa
  `sundays_excluded_formatted`; aritmetika tetap konsisten
  (gross − deduksi = net, deduksi sudah termasuk Minggu).
- **Hari libur TIDAK dikecualikan otomatis** — sistem tidak punya
  kalender libur (libur berbeda per pelabuhan/negara). Libur dicatat
  sebagai jeda SOF manual (kategori OTHER + remarks "Libur: ...") dan
  ikut terdeduksi lewat mekanisme stoppage. UI menampilkan catatan ini
  bila terms = SHEX, supaya label tidak lebih kuat dari perhitungan.
- `terms` tak dikenal (di luar SHINC/SHEX) kini ditolak `ValueError`
  (kolom DB sudah CHECK, ini pertahanan kedua). `parse_iso()` kini
  melempar satu pesan yang konsisten ("Format timestamp tidak dikenali")
  untuk semua kegagalan parse — sebelumnya `strptime` melempar pesannya
  sendiri sehingga handler laytime salah mengklasifikasikannya (ketahuan
  saat Tes 23 gagal di tengah pekerjaan ini).
- Handler laytime membedakan error waktu ("Waktu tidak valid: ...")
  dari error lain (mis. istilah tak dikenal) — pesan yang kedua
  dikembalikan apa adanya.

Tes: `tests/test_laytime.py` — SHEX Minggu penuh (Jumat–Senin: net 3 hari→2 hari,
demurrage $40.000→$20.000), Minggu sebagian (12 jam terpotong, despatch
$3.333,33), libur hari kerja tetap dihitung (tanpa kalender), terms
tidak dikenal ditolak. Verifikasi API langsung: terms SHEX dari DB →
respons membawa `sundays_excluded` yang benar.

## 5. ID port call bisa bentrok — MAX+1 + retry

Temuan terkonfirmasi: `PC-YYYY-PORT-XXXX` dari `COUNT(*)+1` di luar
transaksi. Dua request bersamaan (server sudah multi-thread, ADR-0013)
bisa mendapat nomor yang sama; yang kedua gagal 500 di primary key.
COUNT juga memakai ulang nomor bila port call lama dihapus.

Perbaikan (`server.py`, pembuatan port call):
- Nomor berikutnya = `MAX(CAST(SUBSTR(id, -4) AS INTEGER))+1` dalam
  lingkup tahun yang sama (semua pelabuhan, seperti SELECT lama).
- Seluruh pembuatan (ID + insert port call, charter party, template
  clearance) diulang sampai 5x bila primary key bentrok — yang kalah
  mengulang dengan nomor baru. PK menjamin dua ID sama tidak pernah
  sama-sama lolos; yang berubah hanya yang kalah tidak lagi gagal 500.
  Error selain bentrok constraint dilempar ulang seperti sebelumnya.
- Tes 24: nomor baru = MAX+1; 5 pembuatan bersamaan (server tes kini
  `ThreadingTCPServer`, seperti `run_server`) semuanya 200 dengan ID
  berbeda.

## 6. Session tidak dicek ulang ke database — 2 dari 3 diperbaiki

- **Username mengandung `|` MERUSAK login (diperbaiki):** payload cookie
  `username|role|expiry` di-`split("|")` menjadi lebih dari 3 bagian
  → `verify_session_cookie()` selalu gagal → user itu tidak pernah bisa
  login. `create_user()` dan `scripts/create_user.py` (termasuk jalur
  `--update`) kini menolak `|` dan username kosong.
- **Rate limit bisa dipakai mengunci akun (diperbaiki):** kunci dulu
  hanya username — 5x login gagal dari mana pun mengunci `ops_jakarta`
  15 menit. Kini kunci = (username, IP klien): IP dari hop pertama
  `X-Forwarded-For` bila ada (di belakang Vercel), kalau tidak alamat
  koneksi. Sisa yang disadari: penyerang yang memalsukan header
  X-Forwarded-For bisa mengganti IP-nya dan lolos dari batas — limit
  tetap best-effort per ADR-0011, bukan pengganti WAF. Tes 26: 5x gagal
  dari satu IP → 429 di IP itu, login benar dari IP lain tetap lolos.
- **Session stateless tidak dicek ulang (DIBIARKAN, sesuai desain):**
  sudah diakui di ADR-0011 — user yang dinonaktifkan atau role-nya
  diubah tetap sah sampai cookie habis (maks. 12 jam), kecuali
  `SESSION_SECRET` di-rotate. Mengubahnya berarti query DB per request,
  yang membatalkan optimasi cold start ADR-0010. Tetap didokumentasikan
  sebagai batasan, bukan diperbaiki di sini.
- Perbaikan sampingan yang ketahuan saat pengujian: handler login
  membaca `self.client_address[0]` — atribut itu tidak ada di jalur
  WSGI (`_WSGIRequest`), sehingga login di Vercel akan 500. Kini
  `_WSGIRequest` mengisi `client_address` dari `REMOTE_ADDR`, dan
  handler memakai `getattr` dengan fallback `"unknown"`. Tes backend
  libsql yang gagal karena ini kini hijau.

## 7. Celah kecil lain

- `float(cargo_tonnage)` tidak ditangkap → input non-angka jadi 500.
  **Diperbaiki** (400 "cargo_tonnage harus angka", Tes 26).
- `float()` lain **ternyata sudah benar**: `pda_estimate`/`fda_actual`
  (disbursement) dan `grt`/`dwt`/`loa`/`max_draft` (master vessel)
  sudah dibungkus try/except dengan pesan 400 yang jelas;
  `percentage_counted` sudah diperbaiki di ADR-0015.
- **Tidak ada batas ukuran body (diperbaiki):** `do_POST` kini menolak
  `Content-Length` tidak valid (400) dan body > 1 MB (413). Semua
  payload sah aplikasi ini hanya JSON kecil.
- **SQLite lokal tanpa busy_timeout/WAL (diperbaiki):** `get_db()`
  kini menyetel `busy_timeout = 5000` (tulis bersamaan menunggu giliran
  5 detik, bukan langsung "database is locked") dan
  `journal_mode = WAL` (baca jalan bersamaan dengan tulis) untuk
  koneksi sqlite3 lokal; `busy_timeout` juga untuk mode file libsql.
  `.gitignore` ditambah `agency.db-wal`/`agency.db-shm`.
- **Access-Control-Allow-Origin: `*` (dihapus):** dari `send_json()`
  dan `do_OPTIONS()`. UI dilayani dari origin yang sama — tidak ada
  permintaan lintas origin yang sah; `*` juga tidak berguna untuk
  cookie (browser menolak credential dengan `*`).
- **Tes menulis ke agency.db sungguhan (diperbaiki):**
  `engine/db.py` kini menghormati env `AGENCY_DB_FILE` (dibaca saat
  import); `tests/test_api.py` menyetelnya ke direktori sementara
  sebelum mengimpor server, dan membersihkannya lewat `atexit`.
  DB lokal yang sudah tercemar (30 port call dari akumulasi run tes)
  direset ke seed (1 port call, 10 clearance, 0 hash).
- **Tidak ada CI (diperbaiki):** `.github/workflows/test.yml` —
  push/PR menjalankan ketiga suite di Python 3.12.
- **Tidak ada file lisensi (diperbaiki):** `LICENSE` MIT,
  Copyright (c) 2026 wiharza. Tanpa ini orang lain tidak boleh memakai
  kode ini secara legal.

## Konsekuensi

- Kolom `file_sha256`/`file_name` kini NULL untuk semua data baru dan
  seed; baris lama di Turso yang masih membawa hash palsu tetap ada di
  DB (tidak di-migrasi otomatis — hash itu tidak lagi ditampilkan
  sebagai bukti; bila diperlukan, NULL-kan manual).
- Hasil laytime SHEX berubah (Minggu kini dikecualikan) — angka lama
  yang dihitung saat SHEX diabaikan tidak lagi berlaku.
- ID port call kini berurutan dari MAX, bukan COUNT — nomor yang
  terhapus tidak dipakai ulang.
- Rate limit login kini per IP: perilaku 429 berubah (lihat §6).
- Tes memakai DB sementara: `agency.db` lokal tidak lagi tercemar run tes.

## Verifikasi

- `tests/test_laytime.py`: 4 fungsi (SHINC lama + SHEX + libur + terms).
- `tests/test_api.py`: 26/26 (Tes 24 ID, 25 hash, 26 rate-limit/body/
  cargo/`|`; Tes 12 disesuaikan — sebelumnya meng-assert hash tidak
  None).
- `tests/test_libsql_backend.py`: lulus (termasuk login WSGI yang sempat
  gagal karena `client_address`).
- Render browser (CDP, fixture beracun): payload XSS tidak dieksekusi,
  tidak membentuk elemen, tampil sebagai teks ter-escape, tanpa overflow
  390px.
- API langsung: SHEX dari DB → `sundays_excluded` benar (Minggu
  2026-11-01, net 3 hari→2 hari).
