# ADR-0027: Tindak lanjut review celah SOP (2–6)

Tanggal: 3 November 2026
Status: Diterima
Konteks: Review atas ADR-0022–0026 menemukan 3 celah kecil (non-blocking)
plus 2 poin yang ternyata sudah terpenuhi. Semuanya diverifikasi dulu
terhadap kode + probe API langsung sebelum diperbaiki.

## 1. Yang diperbaiki

- **Tooltip catatan di badge sertifikat (ADR-0025).** Catatan
  "Tanggal kedaluwarsa placeholder…" sudah ada di DB seed dan dikirim
  API (`_cert_with_status` mengembalikan baris penuh), tapi `certBadge()`
  tidak menampilkannya — operator tidak bisa membedakan tanggal
  placeholder dari tanggal resmi. Kini badge memakai `title` dari
  `notes` bila ada (aman: `esc()` meng-escape tanda kutip). `notes`
  juga ditambahkan ke dict `cert_alerts_for`, supaya tooltip bekerja di
  badge papan shift, bukan hanya di detail.
- **Validasi expiry >= issued (ADR-0025).** Probe review membuktikan
  sertifikat dengan terbit 2026-06-15 dan kedaluwarsa 2026-01-01
  diterima (200) dan tersimpan. Kini server menolak 400
  ("expiry_date harus sama atau setelah issued_date") dan UI mencegah
  di `submitNewCert` sebelum POST. Tanpa `issued_date`, atau
  expiry == issued, tetap diterima.
- **Klarifikasi SPKK (ADR-0026).** Basis checklist + remarks template
  kini berbunyi "…penghentian paksa oleh Syahbandar (tanpa tenggat
  tanggal — peristiwa, bukan jam)". Nama dokumen TIDAK diubah — dipakai
  `_matches` dan Tes 43.

## 2. Yang ternyata sudah terpenuhi (tidak diubah)

- **Audit log GANGWAY_DOWN + COB (ADR-0024).** Handler milestone
  mencatat `f"MILESTONE_{code}"` (server.py) — otomatis untuk semua
  kode. Probe langsung: `audit_logs` berisi `MILESTONE_GANGWAY_DOWN`
  dan `MILESTONE_COB`. Catatan: nama polanya `MILESTONE_<kode>`, bukan
  `MILESTONE_ADD` seperti disebut review — substansi sama.
- **Pengingat pemilik kapal hanya badge in-app.** Sesuai kesepakatan:
  kanal email/Telegram memang ditunda; tidak ada perubahan.

## 3. Koreksi terhadap review

Review menyatakan tanggal SSCEC placeholder "bisa langsung diedit
lewat modal sertifikat" — **tidak benar**: tidak ada endpoint edit
sertifikat (hanya GET + POST tambah, sesuai ADR-0025). Cara yang benar:
tambah baris SSCEC baru dengan tanggal asli — logika latest-per-type
(`cert_alerts_for`) membuat baris baru menggantikan placeholder untuk
badge — atau update langsung di DB.

## 4. Verifikasi

Tes 44 baru: expiry < issued -> 400 + tidak tersimpan; expiry ==
issued -> 200; tanpa issued -> 200; UI tersaji memuat tooltip;
alert papan shift membawa notes; basis SPKK memuat "tanpa tenggat".
Eksekusi `certBadge` di Node vs data seed asli: badge SSCEC membawa
`title="Tanggal kedaluwarsa placeholder…"`, badge tanpa notes tidak
berubah.

Terverifikasi: API 44/44, laytime + libsql lulus.
