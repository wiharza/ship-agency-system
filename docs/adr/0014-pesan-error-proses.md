# ADR-0014: Proses yang Gagal Harus Menjelaskan Alasannya di UI

Tanggal: 2026-11-02
Status: Diterima
Cabang: `pesan-error-proses`

## Konteks

Laporan user: sign-off ops sebelum dokumen lengkap memang ditolak —
benar — tapi sistem tidak memberi keterangan mengapa. Layar hanya
dimuat ulang seperti tidak terjadi apa-apa. User meminta audit
proses-proses lain: apakah mereka juga seperti ini?

## Temuan audit (semua POST di `ui/index.html` vs `server.py`)

API-nya **bukan** masalah: setiap penolakan sudah mengembalikan JSON
`{"error": "..."}` yang jelas (mis. close-ops: "SPB belum terbit —
kapal belum boleh berlayar, ops tidak bisa ditutup (PM 28/2022)").
Masalahnya murni di UI: sebagian handler tidak pernah membaca respons.

| Proses | UI menampilkan alasan? | Keterangan |
|---|---|---|
| Sign-off ops (`close-ops`) | ❌ TIDAK | `fetch` diabaikan, langsung reload — **yang dilaporkan user** |
| Sign-off finance (`close-finance`) | ❌ TIDAK | Pola sama persis |
| Milestone | ❌ TIDAK | Lebih parah: SAILED tanpa SPB valid tetap tercatat sebagai fakta tapi status tidak jadi DEPARTED — user tidak bisa membedakannya |
| Jeda SOF (stoppage) | ❌ TIDAK | Pola sama (peluang gagal kecil, tapi polanya sama) |
| Hitung laytime | ❌ TIDAK + crash | Saat server menolak (400), `data.result` undefined lalu `data.result.is_demurrage` melempar TypeError — output tidak berubah, tanpa pesan |
| Sengketa FDA (dispute) | ❌ TIDAK | `.then(() => reload)` tanpa cek; server pun melempar 500 HTML bila nilai bukan angka (bukan JSON) |
| Terbit SPB | ✅ Ya | Alert menampilkan daftar blocker |
| Tambah PDA/FDA, tambah kapal, buat port call, edit/tambah clearance | ✅ Ya | Pola `if (!res.ok) alert("Gagal: " + data.error)` |
| Login | ✅ Ya | Pesan error tampil di halaman login |
| "Sinkronkan ke Server" (SOF) | ⚠️ Sukses PALSU | Bukan gagal diam-diam — sebaliknya: mengklaim "Sinkronisasi ke server berhasil!" padahal TIDAK ADA request yang dikirim |

## Keputusan

- Keenam handler yang diam (ops, finance, milestone, stoppage, laytime,
  dispute) kini memakai pola yang sudah ada di handler lain:
  baca `res.json()` (aman bila respons bukan JSON), bila `!res.ok`
  tampilkan `alert("Gagal: " + data.error)`, dan jangan reload.
- Milestone mendapat pesan khusus: bila `milestone_recorded: true`,
  alert berbunyi "Milestone TERCATAT, tetapi status kapal tidak
  berubah: ..." — karena faktanya memang tercatat, hanya statusnya
  yang tidak berubah.
- Laytime: cek `!res.ok || !data.result` SEBELUM menyentuh
  `data.result` — menutup TypeError diam-diam.
- Server: `POST /dispute` dengan nilai bukan angka kini dijawab
  400 JSON `{"error": "Nilai sengketa harus angka (IDR)"}`, bukan
  lagi 500 HTML yang tidak bisa ditampilkan UI.
- `syncLocalSOF` tidak lagi mengklaim berhasil: teksnya kini
  "Tersimpan lokal di peramban — belum disinkronkan ke server
  (sinkronisasi otomatis belum tersedia)."
- Empat pesan server close-ops/close-finance kehilangan awalan
  "Gagal: " — karena UI sudah menambahkan "Gagal: " di depan, pesan
  tampil ganda ("Gagal: Gagal: SPB belum terbit ..."). Pesan server
  kini berisi alasannya saja; awalan hanya dari UI.
- Bonus konsistensi: 404 handover "Not found" → "Port Call tidak
  ditemukan" (sama seperti endpoint lain).

## Verifikasi

- API (curl, server nyata, port call baru PC-2026-TPP-0018 tanpa SPB):
  close-ops 400 + alasan, milestone SAILED 400 + `milestone_recorded:
  true`, laytime 400 "Data kontrak atau kargo tidak lengkap",
  dispute non-angka 400 JSON, close-finance 403 role.
- UI (render headless Chrome + CDP, fixture `ui/index.html` dengan
  fetch dimock memakai respons API asli yang ditangkap): 6 alert
  muncul dengan alasan server — close-ops, close-finance, milestone
  ("TERCATAT, tetapi status tidak berubah"), laytime, dispute,
  stoppage — tanpa exception di halaman; teks SOF jujur.
- Suite: `test_api.py` 22/22 (Tes 22 baru: penolakan harus menyebut
  alasan; dispute non-angka 400 JSON), laytime, libsql backend.
  Catatan: suite memakai IMO uji dari timestamp bila pool 4 nilai
  habis (perbaikan ketahanan tes yang sama seperti di ADR-0013) —
  tanpa itu suite gagal `StopIteration` setelah 4 run di DB yang
  sama.
