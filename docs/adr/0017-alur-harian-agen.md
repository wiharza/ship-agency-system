# ADR-0017: Alur harian agen — status hanya maju, arsip CLOSED terkunci, papan shift, koreksi ETA, lengkapi FDA, fixture laytime tersimpan

Tanggal: 2 November 2026
Status: Diterima
Konteks: PR #10 (ADR-0014/0015/0016) menutup temuan audit sebelumnya
(alasan penolakan tampil, hash palsu dihapus, XSS di-escape, SHEX dihitung,
ID anti-bentrok, CI + lisensi). Yang tersisa adalah gesekan kerja harian,
bukan pengulangan audit itu. Setiap klaim diverifikasi dulu terhadap kode
sebelum diperbaiki — satu klaim ternyata separuh salah (lihat "Yang dicek
dan ternyata sudah benar").

## 1. Status bisa mundur — DIPERBAIKI

Milestone `EOSP` selalu menulis status `ARRIVED`, `ALL_FAST` selalu
`BERTHED`, tanpa melihat status sekarang. Kapal yang sudah `DEPARTED`
bisa kembali ke `ARRIVED` hanya karena seseorang mencatat EOSP yang telat.
Setelah `CLOSED` pun milestone, dokumen, dan biaya tetap bisa diubah —
label "arsip 10 tahun terkunci" baru label.

Perbaikan:
- Peta urutan `_STATUS_RANK`: PRE_ARRIVAL 0, ARRIVED 1, BERTHED 2,
  DEPARTED 3, CLEARANCE_CLOSED 4, CLOSED 5. Perubahan status dari
  milestone hanya berlaku bila peringkat target > peringkat sekarang;
  milestone tetap tercatat sebagai fakta lapangan, dan respons membawa
  `note` yang menjelaskan status tidak bergerak (mis. "EOSP dicatat
  sebagai koreksi, status tetap DEPARTED").
- Kunci tulis setelah `CLOSED`: milestone, stoppage, tambah/ubah
  clearance, tambah/ubah disbursement, dan terbit SPB ditolak 400/403
  dengan kalimat yang menyebut siapa menutup dan kapan
  (`_closed_lock_message`: "Port call sudah CLOSED oleh {finance_closed_by}
  pada {finance_closed_at} — perubahan tidak diizinkan (arsip 10 tahun
  terkunci)"). Hitung laytime juga terkunci karena kini ia menyimpan
  milestone + baris kalkulasi.
- Satu pengecualian: finance boleh mencatat resolusi atas sengketa yang
  sudah terbuka (`POST /api/disputes/<id>/resolve`, FINANCE/ADMIN,
  status akhir ACCEPTED_REVISED / REJECTED_FINAL). Sengketa OPEN
  memblokir close-finance (lihat §6), jadi jalur normalnya adalah
  sengketa yang sempat UNDER_REVIEW sebelum penutupan.
- Sebelum `CLOSED`, koreksi waktu milestone tetap boleh
  (`POST /api/milestones/<id>`, cek urutan terhadap tetangga) tetapi
  tidak menarik status ke belakang.
- Close-finance yang sudah ditutup kini ditolak bila dipanggil dua kali
  (sebelumnya lolos diam-diam).

## 2. SPB kedua menimpa yang pertama — DIPERBAIKI

Endpoint terbit SPB tidak menolak bila SPB masih berlaku, dan nomor lama
tidak disimpan di baris clearance — hanya di audit. Penerbitan ulang yang
sah (SPB hangus, PM 28/2022) perlu jejak nomor sebelumnya.

Perbaikan:
- SPB masih berlaku (expiry > sekarang) → 400 yang menyebut nomor dan
  waktu kedaluwarsanya.
- SPB kedaluwarsa → terbit ulang diizinkan; sebelum ditimpa, nomor lama
  masuk tabel baru `spb_history` (nomor, issued_at, expiry_at,
  superseded_at, superseded_by) dan ditampilkan di detail
  (`GET /api/port-calls/<id>` membawa `spb_history`).
- Gate role: terbit SPB kini OPS/ADMIN, sama seperti sign-off ops —
  dicek SEBELUM gate clearance, supaya FINANCE mendapat 403 yang jelas
  bukan "dokumen belum lengkap". UI menyembunyikan tombol Terbit SPB
  untuk role FINANCE (server tetap yang menegakkan).

## 3. Waktu milestone belum divalidasi — DIPERBAIKI (sebagian, sengaja)

Stoppage sudah menolak format salah; milestone tidak — `event_time` apa
pun tetap masuk, lalu merusak urutan SOF.

Perbaikan:
- Format `event_time` milestone kini divalidasi `parse_iso` → 400
  terbaca (sebelumnya apa pun masuk).
- Monotonisitas TIDAK ditolak: koreksi telat adalah kejadian sah, dan
  tampilan sudah mengurutkan berdasarkan `event_time` ASC. Respons
  membawa `note` bila waktu yang dicatat lebih awal dari milestone
  terakhir ("dicatat sebagai koreksi"). Keputusan ini sengaja, bukan
  kelalaian — menolak koreksi akan memaksa petugas berbohong soal waktu.

## 4. Kontrak laytime tidak bisa diisi — DIPERBAIKI

Tiap port call baru disisipkan charter party tetap: 10.000 MT/hari,
SHINC, demurrage 20.000, notice 6 jam. Kalkulator memakai angka itu,
bukan recap fixture. Hasil hitung juga tidak disimpan — shift berikutnya
tidak melihatnya.

Perbaikan:
- `POST /api/port-calls/<id>/charter-party` (OPS/ADMIN, kunci CLOSED):
  charterer, rate, terms SHINC/SHEX, demurrage, despatch, notice;
  audit `CHARTER_PARTY_UPDATE`. UI: form fixture di tab laytime,
  terisi dari `charter_party` saat ini.
- `POST .../laytime/calculate` kini MENYIMPAN: NOR, mulai muat, dan
  selesai muat di-upsert sebagai milestone (insert bila belum ada,
  update waktu bila berubah — status tidak pernah disentuh saat koreksi;
  status hanya maju saat insert), dan baris `laytime_calculations`
  (terms, NOR/comm/comp, allowed/used, claim, amount, dihitung oleh +
  kapan). Detail membawa `latest_laytime`; handover membawa baris
  "Laytime Terakhir:"; hitung ulang dengan waktu/parameter yang sama
  tidak menggandakan milestone (terverifikasi curl: tetap 3 baris).
- Tabel baru `laytime_calculations` di `engine/db.py` (init_db +
  TABLES, kini 16 entri; DB lama mendapatkannya lewat readiness check).

## 5. FDA tidak bisa dilengkapi — DIPERBAIKI

Biaya hanya bisa ditambah, tidak bisa diubah. Di lapangan PDA dicatat
dulu, NTPN dan realisasi FDA datang kemudian. Sekarang harus menambah
baris baru, jadi rekonsiliasi dobel.

Perbaikan:
- `POST /api/disbursements/<id>` (alias `/api/port-calls/<id>/disbursements`
  tetap untuk tambah): ubah `fda_actual`, `ntpn`, `simponi_billing`,
  `invoice_number` pada baris yang sama; `pda_estimate` tidak berubah;
  audit `DISBURSEMENT_UPDATE`. UI: tombol "Lengkapi FDA" di tiap baris
  tabel keuangan → modal form.
- Close-finance kini diblokir bila masih ada sengketa OPEN atau FDA
  masih 0 pada baris yang PDA-nya sudah terisi — dengan daftar itemnya
  (`blockers`), pola yang sama seperti blocker close-ops (ADR-0015).
  Sengketa UNDER_REVIEW tidak memblokir (sudah dalam proses).

## 6. Teks lama masih menjanjikan hash — DIPERBAIKI

Banner tab dokumen masih menulis "bukti (hash SHA-256)" padahal kolom
itu sudah sengaja kosong sampai ada unggah berkas (ADR-0016/Q48).

Perbaikan: banner kini berbunyi kolom sidik jari berkas (SHA-256)
sengaja kosong sampai ada fitur unggah berkas, dan menyebut koreksi
ADR-0016. Catatan kecil di bawah judul tab disesuaikan. Tidak ada
perubahan perilaku — hanya teks yang berhenti berbohong.

## 7. Papan tugas shift, satu layar — BARU

Daftar port call hanya menampilkan nama dan status. Petugas jaga butuh:
dokumen lewat tenggat, SPB sisa kurang dari 6 jam, kapal sudah sandar
tapi SPB belum diajukan, sengketa FDA masih OPEN. Datanya sudah ada di
checklist dan `spb_expiry_at`; yang kurang hanya ringkasan di kartu,
tanpa buka satu per satu. Ini menggantikan notifikasi email untuk tim
kecil (notifikasi §6 PRD tetap [TARGET]).

Perbaikan:
- `GET /api/shift-board`: hanya port call aktif (`status != 'CLOSED'`);
  per kartu: `overdue_docs` (label dokumen), `spb_state`
  (none/ok/under_6h/expired), `spb_hours_left`, `needs_spb` (BERTHED +
  belum ada SPB), `open_disputes` (item + nominal); `totals`; diurutkan
  paling mendesak dulu. Memakai ulang `build_clearance_checklist`
  (bukan logika tenggat kedua).
- UI: baris ringkasan di atas daftar + badge per kartu (merah: dokumen
  lewat tenggat / SPB kedaluwarsa / sengketa OPEN; kuning: SPB < 6 jam /
  SPB belum diterbitkan).

## 8. Koreksi ETA, ETD, dan muatan — BARU

Tenggat RKSP (ETA−24 jam) dan permohonan SPB (ETD−4 jam) dihitung dari
dua kolom itu, tetapi tidak ada cara mengubahnya setelah port call
dibuat. ETA yang geser adalah kejadian harian.

Perbaikan:
- `POST /api/port-calls/<id>/update` (OPS/ADMIN, kunci CLOSED):
  `eta`/`etd` (format divalidasi, ETD ≥ ETA), `cargo_name`,
  `cargo_tonnage` (angka ≥ 0); tanpa perubahan → 400; audit
  `PORT_CALL_UPDATE`; respons membawa port call + checklist yang
  sudah dihitung ulang. UI: tombol "Ubah ETA / ETD / Muatan" di tab
  Ringkasan (juga menampilkan ETD — sebelumnya hanya ETA) → modal form.

## 9. Ganti prompt() dengan formulir — BARU

Di dermaga dialog `prompt` mudah salah ketik dan tidak menunjukkan
prasyarat. Setelah perubahan ini tidak ada lagi `prompt(` di
`ui/index.html` (hanya disebut di komentar):

- SPB: modal form (nomor, waktu terbit, daftar blocker) — tombol
  terbit nonaktif bila `can_issue_spb` false; setelah terbit, alert
  menyebut nomor + masa berlaku 24 jam.
- Tambah biaya: modal (kategori, item, PDA, FDA, billing, NTPN,
  invoice). Lengkapi FDA: modal (lihat §5). Sengketa: modal (nilai +
  alasan) + modal resolusi (lihat §1). Tambah kapal master: modal
  (IMO, nama, bendera, jenis, GRT).
- Koreksi ETA/ETD/muatan dan koreksi milestone: modal (lihat §8, §1).
- `submitMilestone` kini menampilkan `note` dari respons;
  `signFinanceClosure` kini menampilkan `blockers` seperti
  `signOpsClosure` (ADR-0014/0015).

## Yang dicek dan ternyata sudah benar

- Stoppage: format waktu sudah divalidasi (ADR-0015); yang belum hanya
  "selesai sebelum mulai" — kini ditolak 400 ("Waktu selesai jeda harus
  sesudah waktu mulai").
- Close-finance: klaim "tidak ada blocker" benar untuk sengketa/FDA —
  itulah yang ditambahkan §5; tetapi close-finance ganda ternyata
  lolos diam-diam (kini ditolak, §1).

## Verifikasi

- `tests/test_api.py` 33/33 (Tes 27–29: status maju-saja + kunci
  CLOSED + SPB histori + validasi waktu; Tes 30: koreksi ETA/ETD/muatan
  + checklist hitung ulang + audit; Tes 31: lengkapi FDA + blocker
  close-finance; Tes 32: fixture CP + laytime tersimpan + handover;
  Tes 33: papan shift). `test_laytime.py` lulus, `test_libsql_backend.py`
  lulus. Tes 5/7/16b/28 disesuaikan: tanggal SPB dibuat kedaluwarsa
  supaya terbit ulang sah; JKT-0001 dibuka ulang via DB karena Tes 7
  menutupnya; disbursement pc2 di Tes 28 diberi `fda_actual` supaya
  blocker FDA-0 yang baru tidak memblokir Tes 28.
- Curl end-to-end (server lokal, DB sementara): hitung laytime
  menyimpan milestone (NOR/COMMENCED/COMPLETED) + `latest_laytime`
  (DESPATCH $1.125,00) + handover membawa "Laytime Terakhir:"; hitung
  ulang tidak menggandakan milestone (tetap 3 baris); status bergerak
  PRE_ARRIVAL → BERTHED.
- Render browser (headless Chrome + CDP, fixture dari respons API
  nyata): papan shift + badge di kartu (2 merah, 2 kuning), modal SPB
  terbuka dengan pesan prasyarat hijau + tombol aktif, tabel log
  milestone (2 baris), tombol "Selesaikan" sengketa, 5 tombol
  "Lengkapi FDA", form fixture terisi (Global Chartering Ltd.,
  8.000 MT/hari) — 390px dan 820px tanpa overflow halaman.
  Selama verifikasi ketahuan satu bug UI dari commit ini sendiri:
  penutup `</div>` modal dokumen lama ikut terhapus saat menyisipkan
  8 modal baru, sehingga semuanya bersarang di dalam
  `#modal-new-clearance` (`display: none`) dan tidak pernah tampil —
  diperbaiki, lalu render ulang membuktikan modal SPB tampil.
- Cakupan audit baru (§8 PRD): `PORT_CALL_UPDATE`,
  `DISBURSEMENT_UPDATE`, `CHARTER_PARTY_UPDATE`, `MILESTONE_UPDATE`,
  `DISPUTE_RESOLVE`.

## Yang sengaja TIDAK dikerjakan (sesuai prioritas pengguna)

Unggah berkas, PDF SOF, dan Telegram — papan shift, koreksi ETA, dan
isi FDA menutup lebih banyak pekerjaan nyata dengan perubahan yang
kecil. Tetap [TARGET]: pengajuan portal resmi, RBAC halus, notifikasi
email/Telegram, dashboard/timeline, SOF offline, HTTPS/TLS + isolasi
cabang, master data nyata, perintah demo README gaya Windows.
