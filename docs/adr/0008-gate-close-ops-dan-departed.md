# ADR-0008: Gate close-ops dan gate DEPARTED berdasarkan SPB yang valid

- **Status:** Diterima (31 Okt 2026)
- **Konteks:** `POST /api/port-calls/<id>/close-ops` (Otorisasi 1) sebelumnya
  tidak memeriksa apa-apa: tanpa SPB pun ops bisa di-sign-off, sehingga
  sistem bisa mencatat port call sebagai `CLEARANCE_CLOSED` lalu `CLOSED`
  padahal kapalnya belum boleh berlayar secara hukum. Playbook tahap 5
  mendefinisikan Otorisasi 1 sebagai verifikasi bahwa seluruh izin CIGP,
  warta, SPB, dan dokumen kargo sudah selesai, dan alur status PRD adalah
  `PRE_ARRIVAL → ARRIVED → BERTHED → DEPARTED → CLEARANCE_CLOSED → CLOSED`.
  Milestone `CAST_OFF`/`SAILED` juga sebelumnya mengubah status jadi
  `DEPARTED` tanpa memeriksa SPB, dan kolom `atd` tidak pernah diisi.
- **Keputusan:**
  1. `close-ops` mengembalikan 404 bila port call tidak ada, dan 400 bila:
     sudah pernah ditutup (`ops_closed_at` terisi), SPB belum terbit,
     SPB sudah kedaluwarsa (24 jam, PM 28/2022 Ps 9 ayat (1)), atau status
     kapal belum `DEPARTED`.
  2. Milestone `CAST_OFF`/`SAILED` tanpa SPB yang valid tetap dicatat
     sebagai fakta lapangan (tidak dihapus — kejadiannya memang terjadi),
     tetapi status TIDAK berubah jadi `DEPARTED` dan endpoint mengembalikan
     400 dengan `milestone_recorded: true`.
  3. Bila `CAST_OFF`/`SAILED` lolos gate, `atd` diisi dari `event_time`
     milestone keberangkatan.
  4. Pola ini mengikuti gate yang sudah ada: gate SPB (ADR-0006) dan
     `close-finance` yang mengecek `ops_closed_at` dulu.
- **Konsekuensi:** Alur demo di README harus mencatat `SAILED` (dalam masa
  berlaku SPB) sebelum `close-ops`; tanpa itu demo ditolak 400. Tes 16a–16e
  menutup kelima cabang gate ini. Yang belum digate: `close-ops` tidak
  memeriksa ulang status clearance-out saat penutupan (prasyarat sudah
  ditegakkan sekali saat SPB diterbitkan).
- **Dasar hukum:** PM 28/2022 Ps 9 (SPB berlaku 24 jam, 1x pelayaran) dan
  Ps 11 (MSD syarat permohonan SPB); UU 17/2023 + PP 28/2024 (tanpa
  persetujuan karantina kesehatan, Syahbandar dilarang terbitkan SPB).
