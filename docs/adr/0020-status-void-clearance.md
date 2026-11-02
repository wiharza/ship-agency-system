# ADR-0020: Status VOID (batal) untuk clearance salah input — baris tetap ada, gate mengabaikannya

Tanggal: 2 November 2026
Status: Diterima
Konteks: Dokumen clearance (CIGP) tidak bisa dihapus — disengaja (retensi
10 tahun, Q27; jejak audit append-only, ADR-0006). Tapi `doc_type` dan
`agency` juga tidak bisa diubah lewat update (hanya `doc_number`,
`status`, `remarks`). Jadi bila petugas salah memilih tipe dokumen saat
input, baris itu tidak bisa diperbaiki dan tidak bisa dibuang. Alur yang
dianjurkan sebelumnya — tandai REJECTED lalu tambah baris baru — ternyata
tidak cukup: gate SPB memeriksa item bersyarat dengan
"`_exists` DAN belum APPROVED", sehingga baris bersyarat yang salah ketik
lalu di-REJECT tetap memblokir SPB dan satu-satunya jalan keluar adalah
menyetujui baris yang salah itu. ADR ini menambahkan status VOID khusus
untuk "salah input".

## 1. Semantik VOID

- VOID = "baris ini salah input, batal". Baris TETAP tersimpan dan tampil
  di detail (riwayat), tapi dianggap tidak ada oleh semua logika proses.
- VOID final: baris VOID tidak bisa diubah lagi — termasuk
  "menghidupkannya" kembali. Koreksi = tambah baris baru. Ini konsisten
  dengan filosofi append-only: pembatalan dicatat, bukan dihapus.
- Hanya OPS/ADMIN yang boleh mem-void (tindakan ini menghapus blocker
  gate, jadi bukan untuk FINANCE). Update status biasa tetap tanpa gate
  role seperti sebelumnya.
- Baris baru tidak bisa langsung dibuat VOID (400) — VOID hanya untuk
  membatalkan baris yang sudah ada.
- Mem-void menghapus `approval_time` (otomatis: hanya APPROVED yang
  menyimpan waktu persetujuan).

## 2. Gate SPB dan daftar tertunda mengabaikan VOID

- `_exists()` (item bersyarat) mengecualikan baris VOID — inilah perbaikan
  celahnya: baris bersyarat yang salah lalu di-void tidak lagi memblokir.
- `_approved()` tidak berubah: VOID tidak pernah terhitung APPROVED,
  jadi baris WAJIB yang di-void (Free Pratique, RKSP, Inward Manifest)
  TETAP memblokir sampai ada baris baru yang APPROVED. Void bukan
  persetujuan.
- Checklist (`build_clearance_checklist`): kategori yang tersisa hanya
  baris void tampil dengan status "VOID" (bukan "BELUM ADA" — riwayat
  tidak disembunyikan) dan tidak pernah ditandai overdue/due_soon. Papan
  shift memakai checklist ini, jadi ikut benar. Handover ("Izin &
  Dokumen Tertunda") mengecualikan VOID.

## 3. Skema: CHECK mengenal VOID — migrasi rebuild tabel

SQLite tidak bisa mengubah CHECK lewat ALTER TABLE. Satu-satunya cara
adalah membangun ulang tabel (pola resmi: RENAME -> CREATE -> INSERT
SELECT -> DROP). Aman di sini karena: (1) tidak ada tabel lain yang
mereferensikan clearances (hanya clearances yang mereferensikan
port_calls), (2) tidak ada indeks/trigger di clearances, (3) INSERT
SELECT menyalin SEMUA kolom termasuk id — AUTOINCREMENT mengikuti
max(id), (4) satu transaksi: gagal di tengah -> rollback utuh.
DDL dipakai bersama lewat konstanta `CLEARANCES_CREATE_SQL` di
`engine/db.py` (dipakai init_db() dan migrasi) supaya definisi baru
tidak ditulis dua kali.

Dua lapis seperti ADR-0018:
1. Cepat, di Turso (tanpa redeploy): `scripts/migrasi-adr-0020.sql` —
   rebuild + SELECT verifikasi; teruji di sqlite lokal dua kali
   (idempotent, data utuh 10/10 baris).
2. Permanen, di kode: `_upgrade_clearances_void(conn)` mendeteksi CHECK
   lama dari `sqlite_master` dan me-rebuild bila perlu. Dipanggil
   `init_db()` (server lokal + tes) dan `_prepare_database_once()`
   (cold start Vercel). No-op bila skema sudah baru.

## 4. Temuan sampingan: override AGENCY_DB_FILE tidak bekerja di tengah suite

Saat menulis Tes 38, tukar `os.environ["AGENCY_DB_FILE"]` di tengah
suite ternyata TIDAK memindahkan DB — karena `DB_FILE` ditentukan sekali
di tingkat modul saat import (`engine/db.py:212`) dan `get_db()` memakai
`DB_FILE` itu. Akibatnya Tes 34 (ADR-0018) selama ini diam-diam
beroperasi di DB utama (DROP dua tabel lalu dibuat lagi) dan lolos hanya
karena urutan tes — bukan karena memakai file legacy sungguhan.
Diperbaiki: `get_db()` kini membaca override env di tiap panggilan lewat
`_local_db_path()` (produksi tidak berubah: tanpa env, jalurnya sama
dengan `DB_FILE`). Tes 34 dan Tes 38 kini benar-benar memakai file
legacy terpisah.

## 5. UI

- Modal edit clearance: opsi baru "VOID (Batal — salah input; final,
  tidak bisa diubah lagi)". Modal tambah TIDAK punya opsi VOID.
- Badge VOID abu-abu (`.badge-gray`) di tabel dokumen.

## 6. Yang SENGAJA tidak dikerjakan

- Mengubah `doc_type`/`agency` di tempat (opsi 1 dari diskusi): ditolak
  pengguna — dipilih status VOID.
- Mengizinkan "un-void": VOID final, tanpa pengecualian.
- Menghapus baris: tetap tidak ada fungsi hapus (Q27).

## Verifikasi

- Tes 37 (baru): alur VOID end-to-end — MSD bersyarat yang di-void
  (huruf kecil "void" -> dinormalisasi) menghapus blocker; RKSP wajib
  yang di-void TETAP memblokir sampai baris baru APPROVED; baris VOID
  ditolak diubah (400 "sudah VOID"); FINANCE ditolak mem-void (403);
  baris baru tidak bisa langsung VOID (400); 2 baris VOID tetap tampil
  di detail sebagai riwayat.
- Tes 38 (baru): DB legacy CHECK lama -> cold start menaikkan CHECK
  (data utuh: jumlah + id sama), init_db() juga menaikkan, status VOID
  bisa disimpan, upgrade idempotent (dua kali panggil tetap lulus).
- Suite: API 38/38, laytime lulus, libsql backend lulus.
- Script migrasi diuji langsung di sqlite lokal: CHECK lama -> baru,
  10/10 baris utuh, jalan kedua tetap bersih.
- UI tersaji (login -> GET /): opsi `value="VOID"` 1x, `badge-gray` 2x.
