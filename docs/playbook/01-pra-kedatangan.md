# Playbook Keagenan Kapal — Tahap 1: Pra-Kedatangan

**Rentang**: dari penunjukan agen / pemberitahuan ETA kapal sampai kapal tiba di perairan pelabuhan.
**Pemicu**: ETA masuk dari principal, atau permintaan appointment dari charterer/operator.

Setiap langkah ditulis: pelaksana → sistem → keluaran dokumen → tenggat → bukti yang disimpan → jebakan yang sering terjadi.

---

## 1.1 Legalisasi agen & akses sistem

| Langkah | Aktor | Sistem | Keluaran | Tenggat | Bukti | Jebakan |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Verifikasi PMKU keagenan kapal aktif | Staf dokumen | inaportnet | Nomor/status PMKU aktif | Sebelum agen menerima port call pertama | Tangkapan layar status PMKU | PMKU kedaluwarsa atau penanggung jawab berubah — akses tertolak saat kapal sudah di laut |
| Pastikan akun pengguna inaportnet terverifikasi | Staf dokumen | inaportnet (SIMPADU verifikasi) | Hak akses disetujui | Sebelum port call | Persetujuan hak akses | Verifikasi hak akses maksimal 1x24 jam — mengajukan mendadak = kapal sudah sandar |

Sumber: PM 8 Tahun 2022 tentang Tata Cara Pelayanan Kapal Melalui Inaportnet; KM 37 Tahun 2023 Lampiran I (SIMPADU sebagai modul verifikasi internal). Diakses 30 Oktober 2026 melalui portal publik Ditjen Hubla.

---

## 1.2 Pemberitahuan kedatangan kapal

| Langkah | Aktor | Sistem | Keluaran | Tenggat | Bukti | Jebakan |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| PKK Masuk (pemberitahuan kedatangan) | Agen | inaportnet | Draft pemberitahuan + billing PNBP | Sebelum kedatangan; lazim H-2 sampai H-1 | Nomor pemberitahuan | Data trayek/ETA berubah tapi draft tidak direvisi — billing jadi tidak cocok |
| PKKA (kapal asing) vs PKK (kapal domestik) | Agen | inaportnet / INSW | Nomor PKKA (asing) atau PKK (domestik) | Paling lambat 1x24 jam sebelum tiba (PM 28/2022 Ps 4) | Nomor trayek | Kapal asing diajukan sebagai PKK domestik (atau sebaliknya) — ditolak verifikasi; di sistem, template clearance membedakan keduanya otomatis dari `vessel_category` |
| SPM (Surat Persetujuan Masuk) | Penyelenggara pelabuhan | inaportnet (SIMPADU) | SPM terbit / penolakan | Sebelum kapal masuk | Salinan SPM | SPM tidak terbit karena sertifikasi kapal di SIMKAPEL tidak lengkap |

Catatan: verifikasi status sertifikasi kelaiklautan kapal merujuk database SIMKAPEL (e-DPKN). Kapal bersertifikat kedaluwarsa akan tertahan di tahap ini.

---

## 1.3 Pemberitahuan pabean (Bea Cukai)

**Dasar hukum**: UU No. 17 Tahun 2006 tentang Kepabeanan Pasal 7A ayat (1) dan (2); PMK No. 158/PMK.04/2017 jo. PMK No. 97/PMK.04/2020.

Kutipan UU 17/2006 Pasal 7A ayat (1):

> "(1) Pengangkut yang sarana pengangkutnya akan datang dari: a. luar daerah pabean; atau b. dalam daerah pabean yang mengangkut barang impor, barang ekspor, dan/atau barang asal daerah pabean yang diangkut ke tempat lain dalam daerah pabean melalui luar daerah pabean, wajib memberitahukan rencana kedatangan sarana pengangkut ke Kantor Pabean tujuan sebelum kedatangan sarana pengangkut."

Ayat (2): pengangkut wajib mencantumkan barang sebagaimana dimaksud ayat (1) dalam manifesnya.

| Dokumen | Tenggat (moda laut) | Sanksi keterlambatan |
| :--- | :--- | :--- |
| RKSP (BC 1.0) | Paling lambat 24 jam sebelum kedatangan bila waktu tempuh ≥ 24 jam; paling lambat sebelum kedatangan bila waktu tempuh < 24 jam | Denda administratif Rp 5.000.000,00 sampai Rp 50.000.000,00 (UU 17/2006 Pasal 7A ayat (7)) |
| Inward manifest (BC 1.1) | Sama seperti RKSP; wajib sebelum pembongkaran muatan | Sanksi administratif Pasal 7A ayat (7) |
| Outward manifest (BC 1.1) | Paling lambat sebelum keberangkatan; wajib manifes nihil bila tanpa muatan | Sanksi administratif Pasal 7A ayat (7) |

Sumber: [UU 17/2006 — JDIH Kemenkeu](https://jdih.kemenkeu.go.id/api/download/fulltext/2006/17TAHUN2006UU.htm); [PMK 97/PMK.04/2020 — JDIH BPK](https://peraturan.bpk.go.id/Details/143652/pmk-no-97pmk042020). Diakses 30 Oktober 2026.

---

## 1.4 Pemberitahuan keimigrasian

**Dasar hukum**: UU No. 6 Tahun 2011 tentang Keimigrasian Pasal 18 ayat (1) huruf a dan b.

> "(1) Penanggung Jawab Alat Angkut yang datang dari luar Wilayah Indonesia atau akan berangkat keluar Wilayah Indonesia diwajibkan untuk: a. memberitahukan rencana kedatangan atau rencana keberangkatan Alat Angkut kepada Pejabat Imigrasi secara tertulis atau melalui media elektronik; b. menyerahkan daftar Penumpang dan daftar awak Alat Angkut yang ditandatangani oleh Penanggung Jawab Alat Angkut."

| Langkah | Keluaran | Tenggat | Bukti | Jebakan |
| :--- | :--- | :--- | :--- | :--- |
| Serahkan crew list + passenger list ke TPI | Daftar awak disetujui | Sebelum kedatangan atau segera saat kapal tiba | Tanda terima TPI | Crew list tanpa tanda tangan nakhoda ditolak; nama tidak cocok dengan buku pelaut |
| Ajukan shore pass untuk awak yang akan turun | Shore pass terbit | Sebelum awak turun dari tangga kapal | Nomor shore pass | Awak turun tanpa shore pass = pelanggaran Pasal 16 ayat (3) UU 6/2011 |

Sanksi: Pasal 79 UU 6/2011 (biaya beban bagi penanggung jawab alat angkut yang tidak memenuhi kewajiban Pasal 18 ayat (1)); Pasal 113 (pidana bila masuk/keluar wilayah tanpa pemeriksaan TPI).
Sumber: [UU 6/2011 — JDIH BPK](https://peraturan.bpk.go.id/Details/39140/uu-no-6-tahun-2011). Diakses 30 Oktober 2026.

---

## 1.5 Karantina kesehatan

**Dasar hukum**: UU No. 17 Tahun 2023 tentang Kesehatan (mencabut UU No. 6 Tahun 2018 tentang Kekarantinaan Kesehatan) dan PP No. 28 Tahun 2024, mengacu International Health Regulations (IHR 2005).

| Dokumen | Keluaran | Tenggat | Bukti | Jebakan |
| :--- | :--- | :--- | :--- | :--- |
| Maritime Declaration of Health (MDH) | Pernyataan kesehatan kapal | Sebelum kapal masuk perairan pelabuhan | MDH ditandatangani nakhoda | MDH dilaporkan sakit padahal awak hanya demam biasa → kapal dikenai tindakan karantina tanpa perlu |
| SSCEC / SSCC | Sertifikat sanitasi kapal | Harus masih berlaku (maksimal 6 bulan) | Salinan sertifikat | Sertifikat kedaluwarsa saat kapal tiba → penerbitan ditunda |
| Port health clearance (free pratique) | Izin bebas karantina | Sebelum kapal berhubungan dengan darat | Salinan izin | Menunggu izin padahal harus diajukan sebelum kapal masuk zona labuh jangkar |

Kondisi penerbitan ditunda: ada awak sakit dengan gejala penyakit menular atau kematian di atas kapal; tanda-tanda vektor/hama aktif; kapal berasal dari pelabuhan terjangkit tanpa protokol desinfeksi.
Sumber: [UU 17/2023 — JDIH BPK](https://peraturan.bpk.go.id/Details/258028/uu-no-17-tahun-2023); [PP 28/2024 — JDIH Kemenkes](https://jdih.kemkes.go.id/documents/peraturan-pemerintah-nomor-28-tahun-2024); [SINKARKES Kemenkes](https://sinkarkes.kemkes.go.id/). Diakses 30 Oktober 2026.

---

## 1.6 Karantina muatan (Badan Karantina Indonesia)

**Dasar hukum**: UU No. 21 Tahun 2019 Pasal 33 dan Pasal 38; PP No. 29 Tahun 2023.

Berlaku ketika kapal membawa Media Pembawa HPKH, HPIK, atau OPTK — termasuk produk pertanian/perkebunan seperti CPO. Untuk muatan ekspor, dokumen (PC/HC) diterbitkan sebelum pemuatan. Untuk transit, penanggung jawab alat angkut wajib melaporkan ke Pejabat Karantina di Tempat Transit.
Sumber: [UU 21/2019 — JDIH BPK](https://peraturan.bpk.go.id/Details/122046/uu-no-21-tahun-2019). Diakses 30 Oktober 2026.

---

## 1.6A INSW vs Inaportnet — dua portal, jangan disamakan

**INSW** (Indonesia National Single Window) adalah single window nasional
lintas instansi. Alur agen di INSW: **Warta I** (data agen/operator),
**Warta II** (data kapal + sertifikat), **Warta III** (data barang/manifest),
**Warta IV** (data orang/crew list), **Warta V** (permohonan ke
Kemenhub, Kemenkeu/RKSP, Kemenkes/SINKARKES, Imigrasi).
**Inaportnet** (PM 8/2022) adalah layanan kapal Ditjen Hubla (5 layanan,
termasuk modul SPS untuk SPB/SPKK/SPOG). Sistem tracker ini **tidak
terhubung** ke keduanya — agen mengajukan di portal resmi, lalu mencatat
nomor dokumen + status + tenggat di tracker (ADR-0004). Detail:
`docs/playbook/lampiran/checklist-clearance-in-out.md`.

## 1.7 Persiapan finansial & logistik

| Langkah | Aktor | Tenggat internal | Keluaran | Bukti | Jebakan |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Susun PDA | Staf finance | H-3 sebelum ETA | Proforma disbursement account | PDA terkirim + tanda terima principal | PDA terlambat → kapal tiba tanpa dana; husbandry berhenti |
| Terima dana / cash advance | Staf finance | Sebelum kapal tiba | Konfirmasi dana masuk | Bukti transfer | Awak sudah butuh uang sebelum dana cair |
| Pesan pandu & tunda | Staf operasional | Sebelum tiba (PPK) | Jadwal PPK, instruksi pandu | Salinan PPK | Jadwal pandu bentrok → SPOG masuk tertunda |
| Siapkan husbandry (air, bunker, limbah) | Staf operasional | Sebelum kapal sandar | Jadwal suplai | Order + konfirmasi vendor | Vendor datang padahal kapal belum sandar = waiting time |

Catatan komersial: PDA disusun agen sebelum kapal tiba sebagai dasar permintaan dana ke principal. Klausul perlindungan standar (ITIC) menegaskan PDA adalah estimasi, bukan dokumen kontraktual — biaya aktual di FDA dapat berbeda. Sumber: [ITIC — Drafting Pro-forma Disbursement Accounts](https://www.itic-insure.com/our-publications/intermediary/drafting-pro-forma-disbursement-accounts-2790/). Diakses 30 Oktober 2026.

---

## Ringkasan tenggat tahap ini

| Pemicu | Tenggat | Sifat |
| :--- | :--- | :--- |
| RKSP & inward manifest ke Bea Cukai | 24 jam sebelum kedatangan (tempuh ≥ 24 jam) / sebelum kedatangan (tempuh < 24 jam) | **Hukum** — denda Rp 5–50 juta |
| Crew list & passenger list ke Imigrasi | Sebelum/ saat kedatangan | **Hukum** — biaya beban, pidana Pasal 113 |
| MDH & free pratique ke Karantina Kesehatan | Sebelum kapal berhubungan dengan darat | **Hukum** — kapal dikarantina |
| Sertifikat karantina muatan | Sebelum pemuatan / saat transit | **Hukum** — pidana Pasal 87 UU 21/2019 |
| PDA terkirim | H-3 | **Internal agen** |
| Dana diterima | Sebelum ETA | **Internal agen** |
