# ADR-0015: Penolakan Server Menyebut Dokumen & Alasan yang Spesifik

Tanggal: 2026-11-02
Status: Diterima
Cabang: `pesan-error-proses` (commit ke-2, di atas ADR-0014)

## Konteks

Lanjutan ADR-0014. Setelah UI menampilkan `data.error`, user menunjuk
tiga kekurangan yang masih ada DI SERVER (bukan di UI):

1. Sign-off ops tidak menyebut dokumen mana yang belum APPROVED — ia
   hanya menolak karena SPB atau status. Daftar penghalang per dokumen
   hanya ada di penerbitan SPB dan di checklist.
2. Hitung laytime: waktu tidak valid jadi 500 "Kesalahan internal
   server" (WSGI) — tanpa kalimat yang menyebut format waktu.
3. Sengketa FDA tidak memastikan item biaya milik port call itu.

Verifikasi klaim user sebelum memperbaiki (curl, server lokal):
semua benar — dan modus gagal lokal lebih parah dari dugaan: bukan
500, melainkan koneksi PUTUS tanpa respons sama sekali (curl 52),
karena exception tak tertangani mematikan handler. Yang terkonfirmasi:
- Laytime waktu `"12-11-2026 08:00"` → koneksi putus (ValueError
  dari `parse_iso` tak tertangani).
- Dispute `disbursement_id` milik port call lain → **200 sukses** —
  sengketa terdaftar dengan item yang salah alamat.
- Stoppage `percentage_counted: "rusak"` → koneksi putus (ValueError
  dari `float()`); port call tak ada → koneksi putus (FK violation);
  waktu `"12 Nov 2026"` → **200 sukses** — waktu rusak tersimpan
  diam-diam, lalu merusak hitung laytime berikutnya (terbukti: Tes 6
  sempat gagal setelah probe ini, karena baris stoppage rusak itu
  masih di DB; baris probe dihapus, tes hijau kembali).

## Keputusan

- **Close-ops menyertakan `blockers`.** Tiga respons 400 gate
  (SPB belum terbit, SPB kedaluwarsa, status belum DEPARTED) kini
  membawa `"blockers": [...]` — daftar dokumen belum APPROVED dari
  `clearance_spb_gate()` yang sama dengan gate SPB (prasyarat wajib +
  item bersyarat yang sudah ada). UI `signOpsClosure()` menampilkannya
  sebagai "Dokumen belum lengkap:\n- ...". Gate-nya TIDAK berubah
  (tidak ada aturan blokir baru) — yang ditambah hanya penjelasannya.
- **Laytime: waktu salah → 400 terbaca.** `calc.calculate(...)`
  dibungkus `try/except ValueError` → 400 "Waktu tidak valid: ...
  Pakai format YYYY-MM-DD HH:MM (contoh: 2026-11-12 08:00) — periksa
  NOR, mulai/selesai bongkar, dan jeda SOF." (Nilai yang salah tetap
  disebut di pesan, berasal dari pesan asli `parse_iso`/strptime.)
- **Stoppage divalidasi SEBELUM insert.** Port call harus ada (404);
  kategori harus salah satu dari 9 nilai CHECK constraint (400);
  `percentage_counted` harus angka (400); `start_time`/`end_time`
  wajib ada dan lolos `parse_iso` (400). Sebelumnya semua ini
  melempar ke 500/koneksi putus, atau tersimpan diam-diam.
- **Dispute memeriksa kepemilikan item.** Port call harus ada (404);
  `disbursements WHERE id = ? AND port_call_id = ?` harus ketemu,
  bila tidak 400 "Item biaya tidak ditemukan pada port call ini —
  periksa kembali item PDA/FDA yang disengketakan". (Validasi angka
  nilai sengketa sudah ada dari ADR-0014.)
- Kategori stoppage di UI sudah benar (dropdown hanya berisi 6 nilai
  yang lolos CHECK) — nilai `"WEATHER"` yang sempat dipakai saat
  probe adalah kesalahan probe, bukan bug UI.

## Verifikasi

- Curl (server nyata): close-ops 400 + 8 blockers ("MDH & Free
  Pratique belum APPROVED (Karantina Kesehatan)", ...); laytime
  waktu salah 400 terbaca; dispute item port call lain 400;
  stoppage angka rusak 400.
- UI (render headless Chrome + CDP, fixture dengan respons API asli):
  alert sign-off ops kini menampilkan alasan + daftar "Dokumen belum
  lengkap" (8 butir); 5 alert lain tetap benar; tanpa exception.
- Suite: `test_api.py` 23/23 (Tes 23 baru: blockers close-ops,
  laytime 400, dispute milik port call, stoppage tervalidasi),
  laytime, libsql backend.
