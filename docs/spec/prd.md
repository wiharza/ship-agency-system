# Product Requirements Document (PRD) — Sistem Keagenan Kapal Nasional

Dokumen Spesifikasi Kebutuhan Produk (*System Specification*) ini menjadi acuan tunggal perancangan arsitektur dan pembangunan aplikasi keagenan kapal berbasis web, merujuk pada seluruh putusan ADR (ADR-0001 s.d. ADR-0007) serta Log Keputusan Q1–Q37.

> **Status dokumen vs kode (31 Okt 2026, lihat §9):** PRD ini memuat target
> produk sekaligus yang sudah diimplementasi. Bagian yang **belum
> diimplementasi** ditandai **[TARGET]** dan dirinci di §9 — jangan dibaca
> sebagai klaim fitur yang sudah ada di kode.

---

## 1. Ikhtisar & Tujuan Sistem

### 1.1 Latar Belakang & Visi
Operasional keagenan kapal di Indonesia diatur secara ketat oleh hukum maritim (UU 17/2008 jo. UU 66/2024, PM 59/2021 — menggantikan PM 65/2019, PM 28/2022) serta melibatkan multi-instansi CIGP (Bea Cukai, Imigrasi, Karantina Kesehatan, Barantin) dan BUP (Pelindo/Tersus). Risiko denda keterlambatan (Pasal 7A UU Kepabeanan), gugurnya izin berlayar (SPB 24 jam), serta sengketa komersial bernilai ratusan ribu dolar (*laytime, demurrage, time-bar FDA*) menuntut adanya sistem pencatat internal yang disiplin, akurat, dan berintegritas tinggi.

### 1.2 Tujuan Utama Produk
1. **Integritas Jejak Audit (*Single Source of Truth*)**: Mencatat seluruh perizinan dan tonggak waktu faktual pelabuhan secara objektif (*commercial witness*).
2. **Kepatuhan Tenggat Ketat**: Mengelola sistem peringatan bertingkat (Hukum vs Internal Agen) untuk mencegah keterlambatan warta dan izin.
3. **Kalkulasi Deterministik**: Menghitung klaim komersial (NOR, SOF, laytime, demurrage/despatch) secara otomatis menggunakan algoritma Python stdlib tanpa deviasi kalkulasi.
4. **Rekonsiliasi Finansial Terukur**: Menghubungkan estimasi PDA dengan realisasi FDA serta memfasilitasi penanganan sengketa biaya (*dispute tracking*).
5. **Ketahanan Lapangan**: Menyediakan form pencatatan SOF yang tahan terhadap kendala sinyal buruk (*blank spot*) di dermaga.

---

## 2. Aktor & Matriks Hak Akses (RBAC) — sebagian terimplementasi (1 Nov 2026, ADR-0011)

Merujuk pada Putusan **Q11, Q25, dan Q26**, sistem **dirancang** menerapkan
*Role-Based Access Control* berbasis cabang (*branch-scoped*). **Status 1 Nov
2026: autentikasi dasar terimplementasi** — tabel `users` dengan role
`OPS`/`FINANCE`/`ADMIN`, session cookie ter-sign HMAC, gate role di
close-ops (OPS/ADMIN) dan close-finance (FINANCE/ADMIN), dan `user_id` audit
diambil dari session (bukan payload). Yang **masih [TARGET]**: peran
Doc Agent / Boarding Agent / Principal read-only di tabel bawah, manajemen
pengguna lewat UI, unlock override, dan isolasi per cabang (branch-scoped)
— role saat ini berlaku global, bukan per cabang. Tabel di bawah ini
adalah spesifikasi target; baris yang sudah berlaku ditandai:

| Peran (*Role*) | Tanggung Jawab Utama | Izin Akses Kunci |
| :--- | :--- | :--- |
| **Super Admin / Branch Head** | Manajemen master data cabang, pengawasan seluruh port call, audit trail | Akses penuh CRUD, manajemen pengguna cabang, unlock override |
| **Staf Dokumen (Doc Agent)** | Pengurusan warta Inaportnet, dokumen CIGP, permohonan SPOG & SPB | Input perizinan, upload berkas, **Otorisasi 1 Penutupan Port Call (Clearance)** |
| **Staf Operasional (Boarding Agent)** | Lapangan, boarding, log tonggak waktu, pencatatan SOF menit ke menit | Input waktu faktual, form SOF offline-first, upload foto lapangan |
| **Staf Keuangan (Finance)** | Penyusunan PDA, input voucher vendor, penyusunan FDA, rekonsiliasi kas | Input pos biaya, NTPN SIMPONI, modul sengketa, **Otorisasi 2 Penutupan (Finance)** |
| **Principal / Ship Owner (External)** | Pengawasan armada kapal secara transparan | **Read-Only**: Dashboard pergerakan kapal, status dokumen, live SOF, download FDA |

---

## 3. Struktur Organisasi & Multi-Cabang

Merujuk pada Putusan **Q25**:
- **Pusat (*Headquarters*)**: Mengelola Master Data terpusat (Data Kapal, Principal, Pelabuhan, Tarif Standar, Template Regulasi).
- **Kantor Cabang (*Branch Office*)**: Unit operasional mandiri (misal: Cabang Jakarta/Tanjung Priok, Cabang Surabaya/Tanjung Perak, Cabang Batam). Staf cabang hanya dapat mengelola Port Call yang ditugaskan ke cabangnya.

---

## 4. Model Data & Entitas Utama

Unit kerja pusat sistem adalah **Port Call** (Putusan Q12). Seluruh entitas terikat pada ID Port Call unik.

```
  ┌──────────────────────────────────────────────────────────────┐
  │                          PORT CALL                           │
  │  ID: PC-2026-JKT-0042                                        │
  │  Status: DRAFT | PRE_ARRIVAL | IN_PORT | SAILED | CLOSED     │
  │  Jenis Kapal: ASING | DOMESTIK (Putusan Q23)                 │
  └──────┬──────────────┬──────────────┬──────────────┬──────────┘
         │              │              │              │
         ▼              ▼              ▼              ▼
  ┌──────────────┐┌──────────────┐┌──────────────┐┌──────────────┐
  │  MASTER REF  ││  PERIZINAN   ││  TIMELINE &  ││  KEUANGAN    │
  │  - Kapal     ││  - CIGP      ││  SOF LOG     ││  - PDA       │
  │  - Principal ││  - Inaportnet││  - Milestones││  - FDA       │
  │  - Pelabuhan ││  - Syahbandar││  - Stoppages ││  - Dispute   │
  │  - Kontrak CP││  - Hash SHA  ││  - Laytime   ││  - Vouchers  │
  └──────────────┘└──────────────┘└──────────────┘└──────────────┘
```

### 4.1 Entitas Port Call (Root Aggregate)
- `port_call_id`: String unik format `PC-{TAHUN}-{KODE_PELABUHAN}-{COUNTER}` (e.g., `PC-2026-JKT-0001`).
- `branch_id`: Relasi ke kantor cabang pengelola.
- `vessel_id`: Relasi ke Master Data Kapal.
- `vessel_type`: Enum `FOREIGN` (Kapal Asing) | `DOMESTIC` (Kapal Domestik).
- `principal_id`: Relasi ke Master Data Principal.
- `charter_party_id`: Relasi ke parameter kontrak fixture.
- `port_code`: Kode UN/LOCODE pelabuhan (e.g., `IDTPP` Tanjung Priok).
- `eta`, `ata`, `etd`, `atd`: Timestamp stempel waktu pergerakan. `etd` bersifat opsional saat pembuatan port call (`POST /api/port-calls` menerima `etd`); bila `etd` kosong, tenggat clearance-out tidak dapat dihitung sistem (lihat §9).
- `status`: Enum di kode (`engine/db.py`): `PRE_ARRIVAL` $\to$ `ARRIVED` $\to$ `BERTHED` $\to$ `DEPARTED` $\to$ `CLEARANCE_CLOSED` $\to$ `CLOSED` (tidak ada status `DRAFT`/`IN_PORT`/`SAILED` di kode; `SAILED` hanya sebagai kode milestone).
- `operational_closed_by`, `operational_closed_at`: Otorisasi tahap 1 (Staf Dokumen).
- `financial_closed_by`, `financial_closed_at`: Otorisasi tahap 2 (Staf Finance).
- `created_at`, `updated_at`: Timestamp sistem.

### 4.2 Entitas Perizinan & Dokumen (*Compliance & Clearance*)
Merujuk pada Putusan **Q18 & Q29**:
- `id`: INTEGER AUTOINCREMENT di kode (bukan UUID).
- **Template clearance baru (31 Okt 2026, ADR-0006):** setiap port call baru
  otomatis mendapat 14 item — clearance-in: PKKA (asing) atau Warta Masuk /
  PKK (domestik), RKSP / BC 1.0, Inward Manifest / BC 1.1, MDH & Free
  Pratique, Immigration Crew List, SPOG Masuk, LK3 Masuk, SPKK / RKBM; clearance-out: Outward
  Manifest / BC 1.1, Port Health Clearance Out, Immigration Clearance Out,
  Master Sailing Declaration, SPOG Keluar, SPB. Daftar lengkap + tenggat:
  `docs/playbook/lampiran/checklist-clearance-in-out.md`.
- **Gate SPB (31 Okt 2026, ADR-0006):** `POST /api/port-calls/<id>/spb`
  menolak (400 + daftar blocker) bila MDH & Free Pratique, RKSP / BC 1.0,
  atau Inward Manifest / BC 1.1 belum APPROVED, atau bila item bersyarat
  yang ada (Immigration Crew List, Outward Manifest, Immigration Clearance
  Out, Port Health Clearance Out, Master Sailing Declaration) belum APPROVED.
  SPB tetap kedaluwarsa 24 jam (PM 28/2022 Ps 9).
- `port_call_id`: Relasi ke Port Call.
- `agency_type`: Enum `INAPORTNET_SIMPADU` | `INAPORTNET_SPS` | `BEA_CUKAI` | `IMIGRASI` | `KARANTINA_KESEHATAN` | `BARANTIN` | `BUP_PELINDO`.
- `document_type`: String (e.g., `PPKKA`, `BC1.1_IN`, `FREE_PRATIQUE`, `SHORE_PASS`, `SPOG_IN`, `SPB`).
- `document_number`: Nomor surat / pendaftaran resmi.
- `submission_time`, `approval_time`: Stempel waktu pengajuan & penerbitan.
- `expiry_time`: Tenggat kedaluwarsa dokumen (khususnya timer 24 jam SPB).
- `status`: Enum di kode: `PENDING` | `SUBMITTED` | `APPROVED` | `REJECTED` | `EXPIRED` (tidak ada `DRAFT`; status awal template adalah `PENDING`).
- `file_name`: Nama berkas lampiran — HANYA terisi bila ada berkas yang benar-benar diunggah (fitur upload belum ada; sebelum ADR-0016 kolom ini diisi nama `.pdf` yang dibuat-buat — lihat Q48).
- `file_sha256`: Hash SHA-256 isi berkas — HANYA terisi bila ada berkas yang benar-benar diunggah (sebelum ADR-0016 diisi hash dari string referensi, bukan isi berkas — lihat Q48).
- `submitted_by`: User ID pelaksana.

### 4.3 Entitas Tonggak & Log SOF (*Milestones & Statement of Facts*)
- `id`: UUID.
- `port_call_id`: Relasi ke Port Call.
- `milestone_code`: Enum `EOSP`, `ANCHORED`, `NOR_TENDERED`, `FREE_PRATIQUE`, `PILOT_ON_BOARD`, `FIRST_LINE`, `ALL_FAST`, `HOLDS_PASSED`, `COMMENCED_CARGO`, `COMPLETED_CARGO`, `DOCS_ON_BOARD`, `SPB_ISSUED`, `CAST_OFF`, `SAILED`.
- `timestamp`: Stempel waktu faktual kejadian (akurasi menit).
- `recorded_by`: Staf operasional pencatat.
- `remarks`: Catatan lapangan.

**Sub-Tabel SOF Stoppages (Jeda Operasional)**:
- `start_time`, `end_time`: Rentang waktu kejadian.
- `category`: Enum `RAIN`, `WEATHER_OTHER`, `MEAL_BREAK`, `SHIFT_CHANGE`, `CRANE_BREAKDOWN_SHORE`, `WINCH_BREAKDOWN_SHIP`, `CARGO_WAITING`, `SHIFTING_BERTH`.
- `percentage_counted`: Persentase waktu yang dihitung laytime (0% s.d. 100%).
- `remarks`: Fakta lapangan (e.g., "Palka ditutup rapat, hujan deras").

### 4.4 Entitas Keuangan (PDA, FDA, Voucher, Dispute)
- **Implementasi di kode:** bukan field agregat di `port_calls`, melainkan
  baris `disbursements` per pos biaya (`category`, `item_name`,
  `pda_estimate`, `fda_actual`, `simponi_billing`, `ntpn`,
  `invoice_number`, `voucher_sha256`). Item baru ditambahkan lewat
  `POST /api/port-calls/<id>/disbursements` (atau `POST /api/disbursements`)
  — endpoint baru 31 Okt 2026 (ADR-0006). Field agregat di bawah ini adalah
  model konseptual (dihitung dari baris disbursements), bukan kolom tabel.
- `port_call_id`: Relasi ke Port Call.
- `pda_total_estimate`: Total estimasi biaya awal.
- `advance_fund_received`: Total dana muka yang telah disetor principal.
- `fda_total_actual`: Total pengeluaran riil berdasarkan kuitansi vendor sah.
- `balance_settlement`: Selisih akhir (`advance_fund_received - fda_total_actual`).
- `settlement_type`: Enum `REFUND_TO_PRINCIPAL` | `SUPPLEMENTARY_INVOICE`.
- `fda_status`: Enum `DRAFT` | `SUBMITTED_TO_PRINCIPAL` | `APPROVED` | `DISPUTED` | `SETTLED`.

**Sub-Tabel Sengketa FDA (*Dispute Tickets*) (Putusan Q32)**:
- `id`: UUID.
- `item_code`: Pos biaya yang disengketakan (e.g., `BUP-TMB`).
- `disputed_amount`: Nilai nominal selisih sengketa.
- `principal_reason`: Alasan penolakan principal.
- `deadline_date`: Batas waktu respon sanggahan agen.
- `status`: Enum `OPEN` | `UNDER_REVIEW` | `ACCEPTED_REVISED` | `REJECTED_FINAL`.
- `resolution_notes`: Riwayat kesepakatan akhir negosiasi.

---

## 5. Alur Layar & Kebutuhan Antarmuka Pengguna (UI/UX)

Sistem dirancang dengan tata letak ringkas, fungsional, dan sarat informasi (*information-dense*):

### 5.1 Dashboard Utama: Active Port Calls — [TARGET, belum diimplementasi]

UI saat ini (`ui/index.html`) tidak memiliki dashboard ringkasan; halaman
utama adalah daftar port call di kolom kiri + detail port call di kolom kanan.
Bagian di bawah ini adalah spesifikasi target:
- **Header Ringkasan Cepat**: Jumlah Port Call Aktif per status (Pra-Kedatangan, Di Labuh, Sandar, Siap Berangkat).
- **Tabel Port Call Aktif**:
  - Kolom: Port Call ID, Nama Kapal (Bendera/IMO), Jenis (Asing/Domestik), Terminal/Pelabuhan, Status Terkini, Status Dokumen Kunci (CIGP/SPB), Target Berikutnya, Sisa Waktu SPB (jika terbit).
  - Indikator Warna Tenggat: **Merah** (Tenggat hukum $\le 2$ jam atau SPB aktif), **Kuning** (Tenggat internal agen $\le 4$ jam), **Hijau** (Selesai/Aman).

### 5.2 Halaman Detail Port Call: Tab Terpadu
1. **Tab Ringkasan (*Overview*)**:
   - Kartu identitas kapal, principal, agen pelaksana, dan kontrak CP.
   - Tombol Aksi Kunci: Otorisasi Tutup Clearance (Ops) & Otorisasi Tutup Finansial (Finance).
   - Fitur **Handover Shift (Putusan Q28)**: Tombol cetak ringkasan serah terima tugas (daftar izin pending, aksi tertunda, kontak petugas jaga berikutnya).
2. **Tab Timeline Terpadu (Putusan Q29)** — [TARGET, belum diimplementasi]:
   UI saat ini memiliki 6 tab (Ringkasan, Dokumen & CIGP, SOF & Working Log,
   Kalkulator Laytime, Rekonsiliasi PDA/FDA & Dispute, Handover Shift 24/7)
   dan **tidak** memiliki tab timeline terpadu. Yang sudah ada: tab Dokumen &
   CIGP menampilkan panel checklist tenggat clearance (`GET
   /api/port-calls/<id>/clearance-checklist`) dengan badge merah/kuning/hijau
   dan status bisa-tidaknya SPB diterbitkan.
   - Satu garis waktu linear dari EOSP hingga Sailing.
   - Setiap entri memiliki badge asal modul: `[SIMPADU]`, `[SPS]`, `[BEA CUKAI]`, `[IMIGRASI]`, `[BKK]`, `[BARANTIN]`, `[BUP]`.
3. **Tab Form SOF & Working Log (Offline-First / Putusan Q24)**:
   - Antarmuka pencatatan waktu faktual per kejadian.
   - Mekanisme **auto-save lokal peramban** (LocalStorage/IndexedDB) dengan indikator: `"Tersimpan di perangkat lokal (Pukul HH:MM:SS)"` — **[TARGET, belum diimplementasi:** tombol *Sinkronkan ke Server* di UI saat ini hanya mengubah label status, tidak menyimpan ke LocalStorage/IndexedDB; pencatatan SOF dikirim langsung ke server. Lihat §9.**]
   - Tombol *"Sinkronkan ke Server"* saat koneksi internet kembali stabil.
   - Pilihan cetak format standar BIMCO / FONASBA PDF — **[TARGET, belum diimplementasi]**.
4. **Tab Rekonsiliasi Keuangan (PDA vs FDA)**:
   - Matriks perbandingan pos: Estimasi PDA vs Faktur Vendor Riil vs Selisih.
   - Tombol **+ Tambah Item PDA/FDA** (baru 31 Okt 2026) untuk menambah pos biaya lewat `POST /api/port-calls/<id>/disbursements`.
   - Tombol Input Billing SIMPONI & NTPN (Putusan Q30) — *dicatat sebagai kolom `simponi_billing`/`ntpn` pada item disbursement; tidak ada endpoint/form khusus di UI saat ini.*
   - Panel Sengketa FDA (*Dispute Desk*): Formulir tiket komplain dan batas waktu respon.

---

## 6. Modul Notifikasi Multi-Kanal — [TARGET, belum diimplementasi]

Merujuk pada Putusan **Q21**:
Sistem **dirancang** menyediakan antrean notifikasi terpusat (*notification
engine*) dengan tiga target saluran. **Status 31 Okt 2026: tidak ada
notifikasi di kode** — tidak ada pengiriman email/Telegram, tidak ada
pengingat tenggat otomatis; tenggat hanya dihitung dan ditampilkan saat
checklist dibuka (`GET /api/port-calls/<id>/clearance-checklist`). Yang ada
di UI hanyalah tombol salin teks handover (untuk ditempel manual ke Telegram)
dan banner hitung mundur SPB 24 jam. Rancangan salurannya:
1. **Dalam Aplikasi (*In-App Dashboard*)**: Lonceng notifikasi dan banner merah mengambang untuk peringatan kritis (SPB 24 jam, palka gagal inspeksi).
2. **Email Broadcast**:
   - Template notifikasi kedatangan resmi ke Principal, BUP, dan CIGP.
   - Notifikasi pengiriman draf FDA final ke principal (maks 30 hari pasca ATD).
3. **Telegram Bot Alert**:
   - Bot instan untuk agen lapangan dan grup operasional cabang.
   - Pesan otomatis: *"PERINGATAN: SPB MV Samudra Jaya terbit pukul 02:00 WIB. Wajib bertolak sebelum 02:00 WIB besok (Sisa waktu 18 jam)."*

---

## 9. Status Implementasi (31 Okt 2026) — sumber kebenaran vs kode

Tabel ini adalah pengikat antara PRD dan kode pada branch
`lengkapi-clearance-in-out` (commit `9befcb9` + revisi dokumen ini). Bila PRD
dan kode bertentangan, tabel ini yang berlaku sampai salah satunya diperbaiki.

| Fitur / klaim PRD | Status | Bukti di kode |
|---|---|---|
| Template clearance 14 item (ASING PKKA / DOMESTIK PKK) | ✅ Terimplementasi | `server.py` POST /api/port-calls; `tests/test_api.py` TEST 13 |
| LK3 Masuk + SPKK/RKBM di template (ADR-0026) | ✅ Terimplementasi | Template 12 -> 14 item: LK3 Masuk (INAPORTNET_SIMPADU, tenggat ETA+24 jam, PM 59/2021 + PM 8/2022) + SPKK/RKBM (INAPORTNET_SIMPADU, tanpa tenggat tanggal — "sebelum derek pertama bekerja" adalah peristiwa). Gate SPB TIDAK berubah (keduanya PENDING tidak memblokir SPB). Port call lama menampilkan keduanya "BELUM ADA"; seed PC mendapat keduanya APPROVED via migrasi seed. TEST 43 |
| Daftar port call bertab + paginasi server (ADR-0028) | ✅ Terimplementasi | `GET /api/port-calls?tab=active\|action\|done\|archive&q=...&limit=40&offset=0` -> `{port_calls, total, has_more, tab_counts}`. Aktif = `status != 'CLOSED'` (default); Perlu Tindakan = Aktif + ada alert papan shift; Selesai = CLOSED < 30 hari; Arsip = CLOSED >= 30 hari. Urutan urgensi (kunci papan shift) untuk Aktif/Perlu Tindakan. Tanpa query = perilaku lama. Index `idx_port_calls_status_closed`. UI: tab + counter, pencarian (debounce 300ms), "Muat lebih banyak", "Menampilkan X dari Y", `localStorage`. TEST 44 |
| Gate SPB (400 + blockers) | ✅ Terimplementasi | `server.py` POST .../spb + `clearance_spb_gate()`; TEST 13/13b |
| Gate close-ops + gate DEPARTED (400 bila SPB belum terbit/kedaluwarsa atau status belum DEPARTED; milestone CAST_OFF/SAILED tanpa SPB valid tetap tercatat tapi tidak jadi DEPARTED; atd diisi dari event_time) | ✅ Terimplementasi | `server.py` POST .../close-ops + POST .../milestone; TEST 16a–16e |
| Persistensi Turso (libSQL remote, opsional) | ✅ Terimplementasi + terverifikasi remote | `engine/db.py:get_db()` env-based + adapter `_Row`/`_LibSQLConnection`; `requirements.txt` (libsql); `scripts/migrate_to_turso.py`; `tests/test_libsql_backend.py`. Diverifikasi 31 Okt 2026 terhadap Turso remote sungguhan (DB agency-yansen, ap-southeast-1): row_factory, FK, transaksi `with conn:`, alur gate end-to-end — semua lulus; data contoh ter-seed di Turso. Token tidak pernah masuk Git. |
| Cold start Vercel + pemakaian ulang koneksi Turso | ✅ Terimplementasi | `server.py:_prepare_database_once()` (1 query COUNT gabungan master_ports + port_calls) + cache koneksi remote per thread di `engine/db.py:get_db()` (`drop_remote_connection()` bila stale). Diukur 1 Nov 2026 vs Turso remote: request pertama 39 dtk → 2,53 dtk (3 statement cold start); request hangat ~0,44 dtk. ADR-0010; tes cache remote di `tests/test_libsql_backend.py` §6 |
| Checklist tenggat ETA/ETD (`can_issue_spb`, overdue/due-soon) | ✅ Terimplementasi | `GET /api/port-calls/<id>/clearance-checklist`; TEST 14; tenggat: RKSP & warta ETA−24 jam, Inward sebelum bongkar/ETA−24 jam, Outward sebelum ETD, permohonan SPB ETD−4 jam |
| Tambah kapal master | ✅ Terimplementasi | `POST /api/master/vessels` (IMO unik); TEST 14; tombol di modal Buat Port Call |
| Tambah item PDA/FDA | ✅ Terimplementasi | `POST /api/port-calls/<id>/disbursements` + `POST /api/disbursements`; TEST 14 |
| Status hanya maju + kunci tulis CLOSED (ADR-0017) | ✅ Terimplementasi | `server.py` `_STATUS_RANK` (PRE_ARRIVAL→ARRIVED→BERTHED→DEPARTED; milestone hanya menaikkan status, tetap tercatat + `note`); `_closed_lock_message` menyebut penutup + waktu; satu pengecualian: resolusi sengketa terbuka (`POST /api/disputes/<id>/resolve`, FINANCE/ADMIN); koreksi waktu milestone (`POST /api/milestones/<id>`) tanpa menarik status mundur; close-finance ganda ditolak. TEST 27/28/29 |
| SPB terbit-ulang berhistori + gate role (ADR-0017) | ✅ Terimplementasi | `POST .../spb`: SPB masih berlaku → 400 menyebut nomor + kedaluwarsa; kedaluwarsa → terbit ulang, nomor lama ke tabel `spb_history` (ditampilkan di detail); gate role OPS/ADMIN sebelum gate clearance. TEST 27 |
| Papan tugas shift (ADR-0017) | ✅ Terimplementasi | `GET /api/shift-board` (hanya aktif): dokumen lewat tenggat, SPB < 6 jam / kedaluwarsa, sandar tapi SPB belum diterbitkan, sengketa OPEN; ringkasan + badge di kartu. TEST 33; render 390px/820px |
| Koreksi ETA/ETD/muatan (ADR-0017) | ✅ Terimplementasi | `POST /api/port-calls/<id>/update` (OPS/ADMIN; ETD ≥ ETA; audit `PORT_CALL_UPDATE`); checklist hitung ulang (RKSP ETA−24 jam, SPB ETD−4 jam); UI menampilkan ETD + modal koreksi. TEST 30 |
| Lengkapi FDA di baris yang sama + blocker close-finance (ADR-0017) | ✅ Terimplementasi | `POST /api/disbursements/<id>` (fda_actual, NTPN, billing, invoice; PDA tidak berubah; audit `DISBURSEMENT_UPDATE`); close-finance diblokir sengketa OPEN + FDA 0 pada baris berp-PDA, dengan daftar item. TEST 31 |
| Form fixture + laytime tersimpan (ADR-0017) | ✅ Terimplementasi | `POST /api/port-calls/<id>/charter-party` (audit `CHARTER_PARTY_UPDATE`); `POST .../laytime/calculate` menyimpan NOR/mulai/selesai sebagai milestone (upsert, tanpa duplikasi) + baris `laytime_calculations`; detail membawa `latest_laytime`, handover membawa "Laytime Terakhir:". TEST 32 |
| Formulir ganti prompt() (ADR-0017) | ✅ Terimplementasi | Tidak ada lagi `prompt(` di `ui/index.html`: modal SPB (blocker merah sebelum tombol, nonaktif bila belum bisa terbit), tambah/lengkapi biaya, sengketa + resolusi, tambah kapal master, koreksi ETA, koreksi milestone; `submitMilestone` menampilkan `note`, `signFinanceClosure` menampilkan `blockers`; banner dokumen berhenti menjanjikan hash SHA-256 |
| Migrasi skema saat cold start (ADR-0018) | ✅ Terimplementasi | `_prepare_database_once()` memeriksa seluruh `TABLES` (16) lewat `sqlite_master` dan memanggil `init_db()` bila ada tabel yang hilang; sebelumnya hanya COUNT 2 tabel sehingga tabel baru tidak pernah dibuat di Turso yang sudah ada (insiden 2 Nov 2026: detail 500 "no such table" setelah PR #11). Perbaikan cepat: `scripts/migrasi-adr-0017.sql`. TEST 34 |
| Validasi waktu milestone + stoppage selesai < mulai (ADR-0017) | ✅ Terimplementasi | `parse_iso` → 400 terbaca untuk `event_time` milestone; stoppage menolak selesai sebelum mulai. Monotonisitas milestone SENGAJA tidak ditolak (koreksi telat sah; respons membawa `note`). TEST 27/29 |
| Validasi input + ketahanan server (ADR-0019) | ✅ Terimplementasi | Status/agency clearance dinormalisasi + di-whitelist sebelum query (`_norm_enum`, 400 menyebut daftar sah); `milestone_code` wajib + whitelist 13 kode (11 awal + GANGWAY_DOWN, COB di ADR-0024); laytime wajib 3 waktu (default tanggal demo dihapus — dulu laytime tanpa body menyimpan milestone demo); body JSON rusak → 400 eksplisit; safety net global do_GET/do_POST (exception → 500 JSON, validasi → 400 JSON); security headers (nosniff, DENY, Referrer-Policy) di API + login + UI; `GET /api/health` tanpa auth; `SECURITY.md`. CSP ketat DITUNDA (UI inline script/onclick). Kredensial default SENGAJA tidak diblokir (masih uji coba). TEST 35/36; probe ulang 0 traceback |
| Status VOID clearance (ADR-0020) | ✅ Terimplementasi | Status baru VOID (batal karena salah input): baris tetap tersimpan sebagai riwayat, gate SPB + handover + papan shift mengabaikannya; baris wajib yang di-void TETAP memblokir sampai ada baris baru yang APPROVED; VOID final (tidak bisa diubah lagi) dan hanya OPS/ADMIN; baris baru tidak bisa langsung VOID; CHECK status dinaikkan lewat rebuild tabel (`scripts/migrasi-adr-0020.sql` + self-heal di cold start/init_db). Sampingan: override AGENCY_DB_FILE kini dibaca per panggilan (`_local_db_path`) — Tes 34 ternyata diam-diam memakai DB utama. TEST 37/38 |
| Tambah pelabuhan master (ADR-0021) | ✅ Terimplementasi | `POST /api/master/ports` + modal Tambah Pelabuhan di UI (tombol di modal Buat Port Call Baru): code UN/LOCODE 5 karakter dinormalisasi upper, name/province wajib, inaportnet_level di-whitelist + disimpan kanonik (Kelas Utama/I/II/III/III (KUPP)), unlocode default = code, max_draft angka >= 0, duplikat -> 400. Tanpa gate role (konsisten dengan kapal/terminal). TEST 39 |
| Tenggat pasca-ATD (ADR-0022) | ✅ Terimplementasi | `post_atd_deadlines()` dari ATD: laytime ATD+3d, voucher ATD+7d, FDA ATD+30d (Q31), sengketa per tiket dari `deadline_date` (dibuat+14d); selesai: laytime bila sudah ada hasil kalkulasi, voucher/FDA bila finance closed. Detail membawa `post_atd` (semua item); papan shift membawa `post_atd` (hanya belum selesai) + total `post_atd_overdue`; badge "Laytime due 2d"/"FDA due 28d" di header detail + kartu. Rekonsiliasi 14 hari tidak dibuat badge sendiri (hasilnya matriks selisih ADR-0023). TEST 40 |
| Matriks selisih FDA (ADR-0023) | ✅ Terimplementasi | Kolom "Selisih FDA − PDA" per pos di tabel keuangan (merah bila FDA > PDA, hijau bila di bawah) + baris TOTAL (total PDA, total FDA, total selisih); pos FDA 0 padahal PDA > 0 menampilkan "Belum diisi" (bukan selisih negatif) + catatan total belum final. Dihitung sisi klien dari data yang sudah dirender; tanpa perubahan server. Verifikasi: kode render dieksekusi di Node terhadap respons API asli. |
| Milestone GANGWAY_DOWN + COB (ADR-0024) | ✅ Terimplementasi | `MILESTONE_CODES` 11 -> 13 kode + dropdown UI: GANGWAY_DOWN (setelah ALL_FAST) dan COB (setelah COMPLETED_CARGO, pemicu clearance-out). Keduanya tercatat sebagai fakta (menit tepat) dan TIDAK mengubah status (perubahan status hanya dipicu daftar eksplisit). TEST 41 |
| Sertifikat kapal (ADR-0025) | ✅ Terimplementasi | Tabel `vessel_certificates` (imo, jenis, nomor, terbit, kedaluwarsa, catatan) + `GET/POST /api/vessels/<imo>/certificates` (jenis bebas teks, tanggal wajib YYYY-MM-DD, tanggal lalu boleh dicatat). Badge expired/akan expired (<= 30 hari) di header detail + kartu papan shift, per jenis hanya expiry terbaru yang dinilai. Seed SSCEC 2027-12-31 adalah placeholder (remarks hanya menyebut tahun) — ganti tanggal asli. Peringatan hanya di dalam aplikasi (tanpa kanal ke pemilik). TEST 42 |


| Audit trail (cakupan di §8) | ⚠️ Sebagian | `log_audit()`; `GET /api/port-calls/<id>/audit-logs`; TEST 15 |
| Kalkulator laytime deterministik | ✅ Terimplementasi | `engine/laytime.py`; `tests/test_laytime.py`. SHEX kini mengecualikan hari Minggu (ADR-0016); hari libur TIDAK dikecualikan otomatis — catat sebagai jeda SOF manual. |
| Hash bukti SHA-256 berkas | ⚠️ Belum ada upload | Kolom `file_sha256`/`file_name` ada tapi NULL sampai ada fitur upload berkas (ADR-0016/Q48 mengoreksi Q18; hash palsu dari string referensi sudah dihapus). |
| Escape XSS di UI | ✅ Terimplementasi | `ui/index.html` helper `esc()` di semua interpolasi data pada `innerHTML`; terverifikasi render browser dengan payload beracun (ADR-0016; TEST 25 tidak langsung — verifikasi CDP). |
| ID port call anti-bentrok | ✅ Terimplementasi | `server.py` POST /api/port-calls: MAX+1 + retry 5x bila PK bentrok; TEST 24 (termasuk 5x pembuatan bersamaan). |
| Rate limit login per IP | ✅ Terimplementasi | `engine/auth.py` kunci (username, IP); TEST 26. Sisa: X-Forwarded-For bisa dipalsukan — best-effort (ADR-0016). |
| Tes DB sementara + CI | ✅ Terimplementasi | `AGENCY_DB_FILE` (DB sementara per run tes); `.github/workflows/test.yml` (3 suite, Python 3.12). |
| Lisensi | ✅ MIT | `LICENSE` (Copyright (c) 2026 wiharza). |
| Dual sign-off close-ops → close-finance | ✅ Terimplementasi | `server.py`; TEST 7 |
| Handover shift | ✅ Terimplementasi | `GET /api/port-calls/<id>/handover`; TEST 8 |
| Timer SPB 24 jam (banner merah) | ✅ Terimplementasi | `ui/index.html` + `spb_expiry_at` |
| RBAC / autentikasi (§2) | ✅ Sebagian terimplementasi | `engine/auth.py` (PBKDF2 + session cookie HMAC + rate limit login) + `engine/db.py` tabel `users` + `scripts/create_user.py` + `server.py` enforcement di `do_GET`/`do_POST` (401 tanpa session) + gate role close-ops (OPS/ADMIN) & close-finance (FINANCE/ADMIN, 403) + actor audit dari session. TEST 1/17/18/19/20/21. Masih [TARGET]: role Doc Agent/Boarding Agent/Principal read-only, manajemen user lewat UI, isolasi per cabang. ADR-0011 |
| Dashboard utama (§5.1) | ❌ Belum | UI hanya daftar + detail port call |
| Tab Timeline Terpadu (§5.2) | ❌ Belum | UI punya 6 tab, tanpa timeline |
| Auto-save SOF offline / cetak BIMCO-FONASBA PDF (§5.2) | ❌ Belum | Tombol sinkron hanya mengubah label |
| Notifikasi email/Telegram, pengingat tenggat (§6) | ❌ Belum | Tidak ada pengiriman/pengingat otomatis |
| HTTPS/TLS, isolasi cabang (§8) | ❌ Belum | HTTP polos, tanpa auth |
| INSW Warta I–V sebagai modul sistem | ⚠️ Dokumen saja | INSW dijelaskan di `docs/playbook/lampiran/checklist-clearance-in-out.md` dan Glosarium; sistem TIDAK terhubung ke INSW — pengajuan tetap manual di portal resmi (ADR-0004) |

Aturan pemeliharaan: setiap fitur baru wajib memperbarui tabel ini; dokumen
tidak boleh mengklaim fitur yang tidak ada di kode.

## 7. Engine Kalkulasi Komersial Deterministik (Python stdlib)

Merujuk pada Putusan **Q8, Q14, dan ADR-0003**:
- Modul kalkulasi dibangun sepenuhnya berbasis pustaka standar Python (`datetime`, `decimal`, `json`).
- Input: Objek JSON SOF Log + Parameter Charter Party.
- Fungsi Logika:
  1. Menghitung durasi *Laytime Allowed* berdasarkan tonase kargo dan kecepatan bongkar/muat per hari.
  2. Menghitung *Laytime Used* dengan mengecualikan hujan, jam makan, dan jeda non-rekanan sesuai klausul kontrak (SHINC/SHEX).
  3. Menghasilkan *Laytime Saved* (Despatch) atau *Time on Demurrage*.
  4. Mengalikan durasi selisih dengan tarif harian demurrage/despatch dengan presisi desimal tanpa pembulatan mengambang (*floating point error*).
- Output: Dokumen laporan JSON dan teks kalkulasi resmi yang dapat diverifikasi oleh auditor eksternal.

---

## 8. Kebutuhan Non-Fungsional (*Non-Functional Requirements*)

| Dimensi | Kebutuhan Spesifik |
| :--- | :--- |
| **Integritas & Retensi Data (Q27)** | Seluruh data Port Call berstatus *Closed* disimpan minimal **10 tahun**. Tidak ada mekanisme hapus otomatis (*hard delete*) pada data arsip. |
| **Integritas Bukti Berkas (Q18, dikoreksi Q48/ADR-0016)** | Setiap berkas lampiran yang diunggah wajib dihitung hash SHA-256-nya pada saat diterima server dan disimpan di kolom `file_sha256`. **Koreksi:** janji ini hanya berlaku bila ada berkas yang diunggah — fitur upload belum ada, jadi kolom `file_sha256`/`file_name` kosong secara sah; hash yang dulu ditampilkan adalah hash string referensi, bukan bukti berkas, dan sudah dihapus. |
| **Keandalan Koneksi (Q24)** | Modul form SOF dan checklist wajib berfungsi dalam mode semi-offline (menyimpan perubahan di browser) saat staf operasional di dermaga mengalami gangguan sinyal. |
| **Keamanan Data & Otorisasi** | **[TARGET, belum diimplementasi]** Enkripsi data transit (HTTPS/TLS) dan isolasi multi-kantor cabang (staf cabang A tidak dapat memodifikasi port call cabang B kecuali Super Admin). Autentikasi dasar sudah ada sejak 1 Nov 2026 (ADR-0011: users + roles + session cookie), tapi server tetap berjalan HTTP polos — jangan expose ke internet tanpa TLS di depannya (lihat README). |
| **Audit Trail Penuh** | Dicatat lewat helper `log_audit()` di `engine/db.py` (31 Okt 2026, ADR-0006), dalam transaksi yang sama dengan perubahan yang diaudit. **Cakupan saat ini:** `CLEARANCE_UPDATE`, `CLEARANCE_ADD`, `SPB_ISSUED`, `MILESTONE_*`, `CLOSE_OPS`, `CLOSE_FINANCE`, `DISBURSEMENT_ADD`, `PORT_CALL_UPDATE`, `DISBURSEMENT_UPDATE`, `CHARTER_PARTY_UPDATE`, `MILESTONE_UPDATE`, `DISPUTE_RESOLVE` (lima terakhir: 2 Nov 2026, ADR-0017) — masing-masing menyimpan User ID, nilai lama, nilai baru, stempel waktu. **Belum diaudit:** stoppage SOF, pembuatan sengketa FDA, penambahan master data (kapal/terminal), dan pembuatan port call. **Tidak disimpan:** alamat IP (kolom tidak ada di tabel `audit_logs`). |
