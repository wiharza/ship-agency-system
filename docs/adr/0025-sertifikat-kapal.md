# ADR-0025: Pelacakan sertifikat kapal + badge kedaluwarsa

Tanggal: 2 November 2026
Status: Diterima
Konteks: SSCEC (maks 6 bulan, Tahap 1) dan sertifikat statutori
kelaiklautan (Tahap 4 §4.6 — jebakan "masa berlaku habis") sebelumnya
hanya ada sebagai teks remarks di seed ("SSCEC valid hingga 2027").
Tidak ada yang mengingatkan agen bila sertifikat sudah kedaluwarsa —
padahal itulah nilai tambah agen bagi pemilik kapal: mengingatkan
sebelum kapal bermasalah di pemeriksaan.

## 1. Skema: tabel `vessel_certificates`

`id, vessel_imo, cert_type, cert_number, issued_date, expiry_date,
notes` (tanggal disimpan YYYY-MM-DD). Nama tabel masuk `TABLES`
sehingga cold start ADR-0018 otomatis membuatnya di Turso yang sudah
ada; `scripts/migrasi-adr-0025.sql` tersedia sebagai jalan pintas
manual. Seed: SSCEC MV. PACIFIC GLORY (9431289) dengan expiry
2027-12-31 — tanggal ini PLACEHOLDER dari remarks "valid hingga 2027"
(yang hanya menyebut tahun); wajib diganti tanggal asli sebelum dipakai.

## 2. Endpoint

- `GET /api/vessels/<imo>/certificates` — daftar (urut jenis +
  expiry turun), tiap baris membawa `days_left` + `status`
  (ok/expiring/expired), plus `alerts`.
- `POST /api/vessels/<imo>/certificates` — tambah sertifikat.
  Validasi pola ADR-0019: `cert_type` wajib teks (jenis bebas teks,
  tidak di-whitelist — sertifikat terlalu beragam); `expiry_date`
  wajib tanggal valid YYYY-MM-DD; `issued_date` opsional (bila diisi
  harus valid); kapal harus ada (404). Tanggal kedaluwarsa di masa lalu
  BOLEH dicatat — justru itulah yang memicu badge merah. Tanpa gate
  role dan tanpa audit (konsisten dengan master kapal/terminal/
  pelabuhan). Edit/hapus sertifikat SENGAJA tidak dibuat (pola yang
  sama dengan master pelabuhan ADR-0021).

## 3. Badge: expired / akan expired (<= 30 hari)

- Per jenis, hanya expiry TERBARU yang dinilai (kapal boleh punya
  baris lama + baru, mis. SSCEC lama yang sudah diganti — yang lama
  tidak boleh memicu alert palsu).
- `expired` bila sudah lewat; `expiring` bila sisa <= 30 hari
  (ambang yang dipakai umum untuk pengingat perpanjangan).
- Detail port call membawa `certificates` (lengkap) + `cert_alerts`;
  papan shift membawa `cert_alerts` per kartu + total `cert_alerts`,
  dan kartu dengan alert ikut urutan "paling mendesak".
- UI: badge di header detail ("Sertifikat Kapal: SSCEC — s.d.
  2027-12-31" hijau / "Garis Muat — kedaluwarsa 5d lalu" merah /
  "SMC — 10d lagi" kuning) + tombol "+ Tambah Sertifikat" (modal
  dengan datalist jenis umum: SSCEC, Keselamatan Konstruksi,
  Keselamatan Perlengkapan, Garis Muat, IOPP, SMC, ISSC) + badge yang
  sama di kartu papan shift + baris ringkasan.

## 4. Batasan

Yang dibangun adalah pelacakan + peringatan DI DALAM aplikasi.
Pengiriman pengingat sungguhan ke pemilik kapal (email/Telegram)
belum dibangun — kanal notifikasi memang sengaja ditunda (ADR-0017);
bila kanal itu ada, `cert_alerts` sudah menyediakan datanya.

## 5. Verifikasi

Tes 42 baru: SSCEC seed tidak alert; Garis Muat kedaluwarsa 5 hari
lalu -> alert `expired`; SSCEC baru 10 hari lagi tidak alert karena
expiry terbaru per jenis adalah 2027; SMC SAMUDRA JAYA 10 hari lagi
-> `expiring`; penolakan jenis kosong / tanggal rusak / kapal tidak
dikenal; detail membawa 3 sertifikat + 1 alert; papan shift membawa
alert di kartu + total >= 2. Selama tes ketahuan dua bug: (1) GET/POST
memakai `len(parts) == 3` padahal path punya 4 segmen — 404 di semua
panggilan, diperbaiki jadi 4; (2) IMO "9999999" ternyata sudah dipakai
kapal tes lain (Tes 22) — kasus kapal tidak dikenal memakai "0000000".

Terverifikasi: API 42/42, laytime + libsql lulus, probe langsung
(seed SSCEC ok 424 hari, Garis Muat expired −5, total papan shift 1)
+ UI tersaji (modal-new-cert, certBadge, ov-certs).
