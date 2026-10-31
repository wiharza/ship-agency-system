# Lampiran: Checklist Clearance In & Clearance Out

Checklist ini adalah cerminan template clearance yang dibuat otomatis oleh
sistem untuk setiap port call baru (`POST /api/port-calls`) dan yang dihitung
tenggatnya oleh `GET /api/port-calls/<id>/clearance-checklist`.
Status di sistem adalah **catatan tracker internal** — APPROVED di sini
berarti nomor dokumen resmi sudah diterbitkan di portal resmi dan dicatat
agen, bukan diterbitkan oleh sistem ini.

## Aturan tenggat (dihitung sistem dari ETA/ETD)

| Dokumen | Tenggat sistem | Dasar |
|---|---|---|
| Warta kedatangan / PKKA (asing) / PKK (domestik) | ETA − 24 jam | PM 28/2022 Ps 4; PM 8/2022 (verifikasi maks 6 jam sebelum ETA) |
| RKSP / BC 1.0 | ETA − 24 jam | PMK 158/2017 jo PMK 97/2020 |
| Inward Manifest / BC 1.1 | ETA − 24 jam (sebelum bongkar) | PMK 158/2017 jo PMK 97/2020; manifes nihil bila tanpa muatan |
| MDH & Free Pratique | ETA (sebelum berhubungan dengan darat) | UU 17/2023 + PP 28/2024 |
| Immigration Crew List | ETA | UU 6/2011 Ps 18 |
| SPOG Masuk | ETA | PM 28/2022 |
| Outward Manifest / BC 1.1 | ETD | PMK 158/2017 jo PMK 97/2020 |
| Port Health Clearance Out | ETD | UU 17/2023 + PP 28/2024 |
| Immigration Clearance Out | ETD | UU 6/2011 |
| Master Sailing Declaration | ETD − 4 jam | PM 28/2022 Ps 11 |
| SPOG Keluar | ETD | PM 28/2022 |
| SPB | Permohonan ETD − 4 jam; berlaku 24 jam sejak terbit, 1x pelayaran | PM 28/2022 Ps 9; PM 8/2022 |

Denda keterlambatan manifes: Rp5–50 juta (Ps 7A UU 17/2006 jo UU 10/1995).

## Gate SPB (ditegakkan `POST /api/port-calls/<id>/spb`)

SPB **ditolak (400 + daftar blocker)** bila salah satu belum APPROVED:

- Wajib: MDH & Free Pratique, RKSP / BC 1.0, Inward Manifest / BC 1.1.
- Bersyarat (hanya bila itemnya ada di port call): Immigration Crew List,
  Outward Manifest / BC 1.1, Immigration Clearance Out,
  Port Health Clearance Out, Master Sailing Declaration.

Tanpa persetujuan karantina kesehatan, Syahbandar dilarang menerbitkan SPB.

## Clearance In — urutan kerja agen

1. Terima penunjukan keagenan + ship's particular dari principal/owner.
2. Kapal asing: ajukan PKKA; kapal domestik: Warta Masuk / PKK.
3. Ajukan RKSP / BC 1.0 dan Inward Manifest / BC 1.1 ke Bea Cukai (CEISA).
4. Ajukan MDH ke SINKARKES; pastikan SSCEC/SSCC masih berlaku (maks 6 bulan).
5. Serahkan crew list + passenger list ke Imigrasi (TPI); urus shore pass.
6. Ajukan PPKB ke BUP/Pelindo; konfirmasi pandu (wajib ≥ GT 500, PM 57/2015)
   dan tunda; ajukan SPOG Masuk ke Syahbandar.
7. Setelah Free Pratique terbit, kapal boleh berhubungan dengan darat dan
   bongkar/muat dimulai; tender NOR dan catat SOF.

## Clearance Out — urutan kerja agen

1. Serahkan Outward Manifest / BC 1.1 ke Bea Cukai sebelum keberangkatan.
2. Tarik shore pass; selesaikan Immigration Clearance Out (cap paspor keluar).
3. Selesaikan Port Health Clearance Out di BKK.
4. Lunasi PNBP (SIMPONI/NTPN) dan jasa BUP; pastikan sertifikat kelaiklautan
   masih berlaku.
5. Nakhoda menandatangani Master Sailing Declaration.
6. Ajukan SPOG Keluar, lalu permohonan SPB elektronik (paling lambat
   ETD − 4 jam). SPB terbit → hitung mundur 24 jam aktif; kapal wajib
   bertolak sebelum kedaluwarsa atau SPB gugur dan prosedur diulang.

## INSW vs Inaportnet — jangan disamakan

- **INSW** (Indonesia National Single Window): single window nasional lintas
  instansi. Alur agen: Warta I (data agen/operator), Warta II (data kapal +
  sertifikat), Warta III (data barang/manifest), Warta IV (data orang/crew
  list), Warta V (permohonan ke Kemenhub, Kemenkeu/RKSP, Kemenkes/SINKARKES,
  Imigrasi).
- **Inaportnet** (PM 8/2022): layanan kapal Ditjen Perhubungan Laut — 5
  layanan (kapal masuk, perpanjangan tambat/labuh, pindah tambat/labuh,
  kapal keluar, pembatalan), termasuk modul SPS untuk SPB/SPKK/SPOG.

Keduanya dipakai agen; data yang sama (mis. RKSP, warta kedatangan) dicatat
sekali di tracker ini dengan nomor dokumen resminya.

## Catatan komersial (bukan regulasi)

NOR / Statement of Facts dan PDA → husbandry → FDA adalah praktik
komersial charter party, bukan regulasi Kemenhub. Jangan klaim sebaliknya
di dokumen maupun ke principal.
