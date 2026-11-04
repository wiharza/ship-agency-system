# ADR-0028: Daftar port call bertab + paginasi server

Tanggal: 3 November 2026
Status: Diterima
Konteks: `GET /api/port-calls` mengembalikan SEMUA baris tanpa filter
dan `loadPortCalls()` me-render semuanya ke `#pc-list`. Setelah sistem
berjalan lama, ratusan kartu menumpuk di sisi kanan: DOM membengkak,
scroll panjang, operator sulit menemukan yang butuh perhatian. Yang
berat bukan database, melainkan DOM.

## 1. Tab (definisi — diverifikasi terhadap kode, bukan asumsi)

- **Aktif** (`status != 'CLOSED'`) — default. Setara dengan
  `finance_closed_at IS NULL`: `finance_closed_at` hanya pernah diisi
  bersamaan dengan `status = 'CLOSED'` (close-finance, Otorisasi 2 —
  dan close-finance mensyaratkan Otorisasi 1 dulu). Definisi yang sama
  dengan papan shift (`WHERE pc.status != 'CLOSED'`).
- **Perlu Tindakan** — Aktif + ada alert apa pun menurut definisi
  papan shift yang sudah teruji (`_card_has_alert`: dokumen lewat
  tenggat, SPB < 6 jam/kedaluwarsa, sandar tanpa SPB, sengketa OPEN,
  tenggat pasca-ATD lewat, sertifikat expired/akan expired).
  "Milestone belum lengkap" TIDAK masuk definisi ini — tidak ada
  definisi "lengkap" di kode.
- **Selesai** — `status = 'CLOSED'` dan `finance_closed_at` < 30 hari
  lalu. **Arsip** — `status = 'CLOSED'` dan `finance_closed_at`
  >= 30 hari lalu.

## 2. API

`GET /api/port-calls?tab=active|action|done|archive&q=...&limit=40&offset=0`
mengembalikan `{port_calls, total, has_more, limit, offset, tab,
tab_counts}`. `limit` dijepit 1–100 (default 40); `q` dicari di nama
kapal / IMO / ID port call (`LIKE`, karakter `%`/`_` di-escape).
`tab_counts` berisi keempat tab (tanpa `q` — angka global untuk label
tab). **Tanpa query apa pun, perilaku lama dipertahankan** (semua
baris, tanpa `total`) — Tes 1b/13 mengandalkannya.

Urutan: tab Aktif/Perlu Tindakan diurutkan paling mendesak dulu
(kunci `_board_urgency` yang sama dengan papan shift), lalu
`created_at` terbaru; Selesai/Arsip `created_at` terbaru. Urgensi
tidak bisa dihitung di SQL, jadi untuk tab Aktif/Perlu Tindakan
baris yang lolos filter diurutkan di Python baru dipotong halaman
(tab aktif biasanya hanya puluhan baris); Selesai/Arsip memakai
`LIMIT/OFFSET` SQL. Index baru `idx_port_calls_status_closed
(status, finance_closed_at, created_at)` di `init_db()` +
`scripts/migrasi-adr-0028.sql` (Turso; tanpa index pun daftar tetap
jalan — index hanya mempercepat).

Refactor: perhitungan papan shift dipindah ke `_shift_board_data(conn)`
(+ `_card_has_alert`, `_board_urgency`) — dipakai `/api/shift-board`
dan `/api/port-calls`. Respons papan shift tidak berubah (Tes 33).

Catatan biaya: `tab_counts.action` butuh satu perhitungan papan per
request daftar — sama dengan yang sudah dilakukan endpoint
`/api/shift-board` di setiap muat daftar. Bila nanti terasa, langkah
berikutnya adalah melipat papan shift ke dalam respons daftar.

## 3. UI

Tab di atas daftar (dengan counter), kotak pencarian (debounce 300ms),
tombol "Muat lebih banyak" (append, pilihan tetap terjaga), baris
"Menampilkan X dari Y", tab + kata kunci terakhir diingat di
`localStorage`. Refresh sesudah aksi mengambil ulang semua baris yang
sedang tampil (bukan hanya halaman pertama) supaya pilihan + urutan
tidak hilang. Tab bisa di-scroll horizontal di layar kecil.
Virtual scroll SENGAJA tidak dipakai — "Muat lebih banyak" cukup
untuk ratusan entri (sesuai rencana).

## 4. Verifikasi

Tes 45 baru: tanpa query = perilaku lama; tiap tab berisi baris yang
tepat (Selesai/Arsip dipisah 30 hari, tanggal relatif terhadap hari
ini); Perlu Tindakan memuat PC ber-alert (LK3 overdue) dan bukan PC
segar; urutan urgensi (ber-alert di atas tanpa alert); pencarian
nama/IMO/ID; paginasi tanpa tumpang tindih; tab/limit tidak valid
-> 400; elemen UI tersaji. Render Chrome (fixture `file://` + fetch
dimock respons API asli): tab Aktif (3) / Perlu Tindakan (1) /
Selesai (0) / Arsip (1), kartu mendesak pertama dengan badge "7 DOK
LEWAT TENGGAT", klik Perlu Tindakan -> 1 kartu + "Menampilkan 1 dari
1 port call", `localStorage pcTab` tersimpan. Satu-satunya error JS
adalah fetch `/clearance-checklist` yang tidak di-mock di fixture —
bukan bug aplikasi.

Terverifikasi: API 45/45, laytime + libsql lulus.
