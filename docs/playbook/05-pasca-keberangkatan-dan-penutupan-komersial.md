# Playbook Keagenan Kapal — Tahap 5: Pasca-Keberangkatan & Penutupan Komersial

**Rentang**: dari kapal bertolak meninggalkan pelabuhan (*sailing* / ATD) hingga seluruh rekonsiliasi keuangan (FDA), komersial (Laytime/Demurrage/Despatch), dan penutupan *Port Call* secara sah.  
**Pemicu**: Kapal bertolak dari batas pelabuhan (*Actual Time of Departure* / ATD terkonfirmasi).

Setiap langkah ditulis: pelaksana → sistem → keluaran dokumen → tenggat → bukti yang disimpan → jebakan yang sering terjadi.

---

## 5.1 Pengumpulan & Verifikasi Faktur Pihak Ketiga (*Third-Party Invoices*)

| Langkah | Aktor | Sistem | Keluaran | Tenggat | Bukti | Jebakan |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Pengumpulan voucher & nota asli dari vendor | Staf Finance | Modul Keuangan Internal | Berkas fisik & scan kuitansi/invoice vendor | Maksimal 7 hari kalender pasca ATD | Kuitansi bermeterai/sah + faktur pajak | Nota vendor terlambat masuk hingga menunda pembuatan FDA melewati tenggat principal |
| Verifikasi keabsahan tarif & bukti dukung | Staf Finance | Pengecekan silang SOF | Lembar verifikasi faktur vs log waktu aktual | Sebelum diinput ke sistem FDA | Faktur vs jam SOF (jam pandu/tunda, tonase air/bunker) | Vendor BUP menagih waktu tunda melebihi jam riil yang dicatat agen di SOF |

---

## 5.2 Kalkulasi Laytime Deterministik (Python stdlib)

**Dasar Acuan**: ADR-0003 & ADR Putusan Q8, Q14; BIMCO Laytime Definitions 2013.

| Langkah | Aktor | Sistem | Keluaran | Tenggat | Bukti | Jebakan |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Ekstraksi parameter kontrak charter | Staf Ops / Finance | Form Kontrak | Parameter laytime: jenis hari (WWD/SHINC/SHEX), rate bongkar/muat, rate demurrage/despatch | Sebelum perhitungan SOF | Salinan recap charter party fixture | Salah menerapkan klausul hari libur lokal pelabuhan pada kontrak jenis SHEX (*Sundays and Holidays Excluded*) |
| Eksekusi kalkulasi laytime stdlib | Staf Ops / Finance | Engine Python stdlib (`engine/laytime.py`, endpoint `POST /api/port-calls/<id>/laytime/calculate`) | *Laytime Statement* & Lembar Perhitungan Demurrage/Despatch | Maksimal 3 hari pasca ATD | Lembar kalkulasi otomatis + rekaman log durasi SOF | Menghitung manual di spreadsheet dengan formula terpotong atau pembulatan menit yang keliru |

---

## 5.3 Penyusunan Final Disbursement Account (FDA) vs PDA

**Dasar Acuan**: ADR Putusan Q31; standar FONASBA / ITIC.

| Langkah | Aktor | Sistem | Keluaran | Tenggat | Bukti | Jebakan |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Rekonsiliasi pos per pos biaya (PDA vs Aktual) | Staf Finance | Modul FDA | Draf FDA dengan matriks selisih (*variance analysis*) | Maksimal 14 hari pasca ATD | Tabel komparasi pos estimasi vs realisasi biaya | Biaya tidak terduga (*unexpected cost*) tidak disertai berita acara lapangan |
| Pengesahan & Pengiriman FDA ke Principal | Finance Manager | Modul FDA / Email | Paket FDA Final (cover FDA + voucher pihak ketiga + receipt PNBP/NTPN) | **Maksimal 30 hari kalender pasca ATD** (Putusan Q31) | Tanda terima email pengiriman FDA | Pengiriman FDA melebihi 30/60/90 hari menyebabkan klaim biaya gugur akibat klausul *time-bar* principal |

---

## 5.4 Penanganan Sengketa Biaya FDA (*Dispute Tracking*)

**Dasar Acuan**: ADR Putusan Q32.

| Langkah | Aktor | Sistem | Keluaran | Tenggat | Bukti | Jebakan |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Registrasi tiket sengketa dari Principal | Staf Finance | Modul Sengketa FDA | Tiket Sengketa (pos biaya disengketakan, selisih nilai, alasan principal) | Maksimal 24 jam setelah notifikasi principal diterima | Surat/email keberatan resmi principal | Komplain principal didiamkan hingga tenggat hak sanggah kedaluwarsa |
| Klarifikasi & Negosiasi dengan Vendor | Staf Finance / Ops | Log Sengketa | Berita Acara Klarifikasi / Surat Koreksi Vendor (Credit Note) | Sesuai batas waktu respon tiket | Log korespondensi + voucher revisi | Membebankan biaya sengketa langsung ke keuntungan agen tanpa mengonfirmasi vendor penerbit |
| Penutupan Tiket Sengketa | Finance Manager | Modul Sengketa | Putusan akhir sengketa (disetujui koreksi / ditolak dengan bukti SOF) | Sebelum pelunasan akhir dilakukan | Berita Acara Kesepakatan Final FDA | Status sengketa tidak di-update sehingga rekonsiliasi kas tertahan selamanya |

---

## 5.5 Rekonsiliasi Kas & Penyelesaian Saldo (*Balance Settlement*)

| Langkah | Aktor | Sistem | Keluaran | Tenggat | Bukti | Jebakan |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Penagihan kekurangan dana (*Supplementary Invoice*) | Staf Finance | Modul Invoice | Faktur Tagihan Tambahan (jika pengeluaran aktual > dana muka PDA) | Segera setelah FDA disetujui principal | Faktur tagihan tambahan sah | Principal menolak membayar selisih karena bukti setor asli hilang |
| Pengembalian kelebihan dana (*Refund*) | Staf Finance | Modul Kas / Bank | Bukti Transfer Pengembalian Sisa (jika dana muka PDA > aktual) | Sesuai kesepakatan agency agreement | Bukti transfer bank (*swift receipt*) | Menahan dana sisa principal terlalu lama sehingga menurunkan reputasi keagenan |

---

## 5.6 Otorisasi Penutupan Port Call Ganda (*Dual Authorization Closure*)

**Dasar Acuan**: ADR Putusan Q26.

Port Call **DILARANG** ditutup secara sepihak oleh satu divisi. Sistem memberlakukan dua pintu persetujuan berurutan:

```
[Port Call Aktif / Selesai Layar]
                 │
                 ▼
  ┌─────────────────────────────┐
  │ Otorisasi 1: Operasional    │  -> Memverifikasi seluruh izin CIGP, warta,
  │ (Staf Dokumen / Ops)        │     SPB, dan dokumen kargo fisik selesai
  └──────────────┬──────────────┘
                 │ (Status: Clearance Closed, Pending Financial Settlement)
                 ▼
  ┌─────────────────────────────┐
  │ Otorisasi 2: Keuangan       │  -> Memverifikasi seluruh vendor lunas,
  │ (Staf Finance / Manager)    │     FDA disetujui, dan saldo kas ter-settle
  └──────────────┬──────────────┘
                 │
                 ▼
     [PORT CALL RESMI CLOSED]
                 │
                 ▼
  ┌─────────────────────────────┐
  │ Penguncian Data & Arsip     │  -> Data terkunci mutlak (Read-Only)
  │ (Masa Retensi 10 Tahun)     │     Penyimpanan SHA-256 hash seluruh berkas
  └─────────────────────────────┘
```

---

## 5.7 Pengarsipan Permanen & Integritas Dokumen 10 Tahun

**Dasar Acuan**: ADR Putusan Q18 & Q27.

1. **Integritas Bukti**: Setiap berkas yang diunggah selama 5 tahap (izin CIGP, billing SIMPONI, SPB, SOF, voucher vendor, FDA) disimpan dengan rekaman:
   - Nomor dokumen / nomor aju.
   - Stempel waktu unggah (*timestamp UTC/WIB*).
   - Identitas pelaksana (User ID).
   - Catatan kontekstual.
   - **Hash SHA-256 berkas digital**.
2. **Retensi 10 Tahun**: Berkas dan rekaman audit log dipertahankan selama **minimal 10 tahun**. Sistem tidak menyediakan fungsi penghapusan (*hard delete*) untuk data port call yang telah berstatus *Closed*.

---

## Ringkasan Tenggat & Titik Kritis Tahap 5

| Titik Kritis | Tenggat | Sifat |
| :--- | :--- | :--- |
| Perhitungan Laytime & SOF | Maksimal 3 hari pasca ATD | **Komersial** — penyampaian klaim demurrage tepat waktu |
| **Penerbitan & Pengiriman FDA** | **Maksimal 30 hari kalender pasca ATD** | **Komersial & Finansial Mutlak** — menghindari *time-bar clause* |
| Penyelesaian Tiket Sengketa FDA | Maksimal 14 hari sejak klaim diajukan | **Finansial** — kepastian arus kas dan hubungan principal |
| Otorisasi Penutupan Ganda | Dilakukan setelah clearance & saldo lunas | **Audit & Tata Kelola** — proteksi risiko audit pajak dan hukum |
