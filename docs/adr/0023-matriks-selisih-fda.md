# ADR-0023: Matriks selisih FDA (variance) di tabel keuangan

Tanggal: 2 November 2026
Status: Diterima
Konteks: SOP Tahap 5 mewajibkan draf FDA dengan matriks selisih
(*variance analysis*) pada langkah rekonsiliasi 14 hari pasca-ATD,
tetapi tabel keuangan hanya menampilkan PDA dan FDA berdampingan —
nol kemunculan "variance/selisih" di kode (yang ada hanya "Selisih
Waktu Komersial" di konteks laytime). Akibatnya agen tidak bisa melihat
pos mana yang melebihi estimasi tanpa menghitung manual per baris.

## 1. Perubahan (UI saja, sisi klien)

Data yang sudah dirender sudah cukup — tidak ada perubahan server:

- Kolom baru **"Selisih FDA − PDA (IDR)"** per pos: `FDA − PDA`.
  Merah bila FDA > PDA (melebihi estimasi, mis. `Rp +3.200.000`),
  hijau bila di bawah PDA, abu-abu bila 0.
- Pos yang FDA-nya masih 0 padahal PDA > 0 menampilkan **"Belum
  diisi"**, bukan selisih negatif — mengikuti aturan close-finance
  yang sama (FDA 0 pada baris berp-PDA memblokir penutupan finance).
  Menampilkan selisih negatif di sini akan menyesatkan (bukan
  penghematan, melainkan belum ditagih).
- Baris **TOTAL** di bawah tabel: total PDA, total FDA, total selisih
  (= total FDA − total PDA), plus catatan bila ada pos belum diisi
  ("* N pos FDA belum diisi — total FDA & selisih belum final") atau
  "Semua pos FDA sudah terisi" bila lengkap.

## 2. Verifikasi

Kode render yang sama persis dengan yang berjalan di browser
(diekstrak dari `ui/index.html`) dieksekusi di Node terhadap respons
API asli yang ditangkap dari server lokal (PC contoh + 1 pos FDA 0
yang ditambahkan via API): 6 pos + TOTAL, selisih Jasa Tambat
`Rp +3.200.000` (merah), Bunker "Belum diisi", total PDA
Rp 151.500.000, total FDA Rp 114.700.000, total selisih
Rp −36.800.000, footnote 1 pos belum diisi — semua cocok.
Render browser langsung tidak dilakukan kali ini: file kebijakan
WebGL Chrome (`/tmp/webgl-domains.txt`) tidak bertahan melewati
restart VM dan format kontraknya tidak dapat dipulihkan dari berkas
yang tersisa, sehingga Chrome menolak start; verifikasi eksekusi kode
+ data asli di atas adalah penggantinya.
