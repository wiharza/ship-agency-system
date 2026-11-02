# ADR-0026: LK3 Masuk + SPKK/RKBM di template clearance

Tanggal: 2 November 2026
Status: Diterima
Konteks: LK3 Masuk (tenggat kedatangan+24 jam, Tahap 2 §2.6) dan
SPKK/RKBM (sebelum derek jalan — HUKUM, Tahap 3 §3.3) tidak ada di
template clearance 12 item, padahal keduanya dokumen wajib yang
berisiko menghentikan operasi (SPKK: PBM disetop pengawas KSOP bila
bongkar sebelum SPKK terbit).

## 1. Perubahan

Template port call baru kini **14 item** (keduanya tidak bersyarat —
SOP tidak membedakan ASING/DOMESTIK untuk keduanya, jadi masuk semua
port call, seperti 10 item umum lainnya):

- **LK3 Masuk / Laporan Kedatangan Kapal** (INAPORTNET_SIMPADU,
  setelah PKKA/Warta Masuk): tenggat **ETA + 24 jam** (PM 59/2021 +
  PM 8/2022: paling lambat 1x24 jam setelah kedatangan). Checklist
  menandainya overdue bila lewat dan belum APPROVED.
- **SPKK / RKBM (Persetujuan Kegiatan Kapal)** (INAPORTNET_SIMPADU,
  setelah SPOG Masuk): **tanpa tenggat tanggal** — "sebelum derek
  pertama bekerja" adalah peristiwa, bukan jam, dan sistem tidak punya
  referensi jam mulai derek saat template dibuat. Checklist memantau
  statusnya (PENDING/APPROVED) tanpa pernah menandai overdue; basis
  hukum dicantumkan di item ("HUKUM, penghentian paksa oleh
  Syahbandar").

Matcher `_matches` bertambah kategori `lk3` ("LK3" di doc_type) dan
`spkk` ("SPKK" atau "RKBM").

## 2. Gate SPB tidak berubah

`clearance_spb_gate()` memakai daftar required/conditional eksplisit
(Free Pratique, RKSP, Inward; bersyarat: Crew List, Outward, Imm
Out, PHC Out, MSD) — LK3 dan SPKK tidak ada di daftar itu, jadi SPB
tetap bisa terbit walau keduanya masih PENDING. Ini sesuai usulan
(gate tidak berubah): keduanya dipantau lewat checklist, bukan lewat
gate. Bila di lapangan SPKK ternyata harus menjadi syarat SPB, itu
keputusan terpisah (bukan bagian ADR ini).

## 3. Data lama

Port call lama (termasuk di Turso) hanya punya 12 baris — checklist
menampilkan LK3/SPKK sebagai "BELUM ADA" (dapat ditambah lewat modal
"Tambah Item Dokumen"). Port call contoh (seed) mendapat keduanya
sebagai APPROVED lewat migrasi seed di `seed_sample_data()` (pola yang
sama dengan 5 item gate lama), supaya contoh tetap lengkap.

## 4. Verifikasi

Tes 43 baru: port call baru -> 14 baris template + 14 item checklist
(8 clearance-in + 6 clearance-out); LK3 deadline eksak ETA+24 jam;
SPKK deadline None + warning None; SPB terbit 200 walau LK3 + SPKK
masih PENDING (gate tidak berubah); PC dengan ETA 2026-10-20 ->
LK3 overdue; seed PC membawa LK3 + SPKK APPROVED. Asersi lama yang
ikut diperbarui: template 12 -> 14 (Tes 13), checklist 6+6 -> 8+6
(Tes 14), seed clearances 10 -> 12 (test_libsql_backend).

Terverifikasi: API 43/43, laytime + libsql lulus.
