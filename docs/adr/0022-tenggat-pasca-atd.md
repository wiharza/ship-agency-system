# ADR-0022: Tenggat pasca-ATD di shift-board + header detail

Tanggal: 2 November 2026
Status: Diterima
Konteks: SOP Tahap 5 menetapkan empat tenggat pasca-ATD — laytime
maksimal 3 hari, voucher maksimal 7 hari, FDA kirim maksimal 30 hari
(Q31), sengketa maksimal 14 hari sejak diajukan — tetapi tidak satu pun
dihitung di mana pun. Papan shift hanya memantau port call aktif
(dokumen/SPB/sengketa), sehingga kapal yang sudah DEPARTED hilang dari
perhatian tepat saat tenggat komersialnya berjalan. Datanya sudah ada
(atd, `laytime_calculations`, `finance_closed_at`,
`fda_disputes.deadline_date`), hanya belum pernah dirangkai jadi
hitung-mundur.

## 1. Helper `post_atd_deadlines()` (server.py)

Mengembalikan daftar item `{key, label, due, days_left, overdue, done}`
untuk port call yang ATD-nya terisi; `[]` bila ATD kosong. `days_left`
memakai `(due - now).days` (floor): tenggat hari ini -> 0, lewat 12 jam
-> -1 ("Terlambat 1d").

- **Voucher** — due ATD + 7 hari; selesai bila finance sudah closed.
- **Laytime** — due ATD + 3 hari; selesai bila sudah ada baris
  `laytime_calculations` (hasil kalkulasi tersimpan).
- **FDA** — due ATD + 30 hari; selesai bila finance sudah closed.
- **Sengketa** — satu item per sengketa OPEN/UNDER_REVIEW, due =
  `deadline_date` (sudah diisi `dibuat + 14 hari` saat tiket dibuka).

Batasan yang dicatat, bukan ditebak: tidak ada penanda "voucher
terkumpul" atau "FDA terkirim" yang lebih rinci di sistem, jadi
voucher + FDA dianggap selesai saat finance closed (penanda paling
dekat). Rekonsiliasi 14 hari (Tahap 5) TIDAK dibuat badge sendiri:
tidak ada status "draf FDA terkirim" yang membedakannya dari FDA
30 hari; hasil rekonsiliasinya adalah matriks selisih Gap 3 (ADR-0023).

Detail teknis: `deadline_date` disimpan sebagai tanggal saja
(`YYYY-MM-DD`) sedangkan `parse_iso()` hanya mengenal format jam
(`YYYY-MM-DD HH:MM`/`...:SS`) — helper memakai fallback
`strptime(..., "%Y-%m-%d")` untuk tanggal saja.

## 2. Detail + papan shift

- `GET /api/port-calls/<id>` membawa `post_atd` (semua item, termasuk
  yang selesai — header detail menampilkan badge hijau "Selesai").
- `GET /api/shift-board`: kartu membawa `post_atd` (hanya yang belum
  selesai) + `post_atd_overdue`; total membawa `post_atd_overdue`;
  kartu dengan tenggat pasca-ATD yang lewat ikut masuk urutan
  "paling mendesak". Query sengketa papan shift diperluas dari
  `status = 'OPEN'` menjadi `IN ('OPEN', 'UNDER_REVIEW')` beserta
  `deadline_date` (field `open_disputes` tetap OPEN saja).
- UI: helper `postAtdBadge()` dipakai kartu shift-board dan header
  detail (`#ov-post-atd` di tab Ringkasan). Warna: merah bila lewat,
  kuning bila sisa <= 2 hari, cyan bila masih jauh, hijau bila selesai.
  Format sesuai usulan: "Laytime due 2d" / "FDA due 28d".

## 3. Verifikasi

Tes 40 baru: port call DEPARTED dengan ATD 2 hari lalu -> due eksak
(ATD+3/+7/+30 hari), `days_left` 1/5/28, belum lewat/belum selesai;
laytime dihitung -> item laytime selesai; sengketa baru -> item
sengketa muncul dengan due hari ini + 14 hari; kartu papan shift
menyembunyikan laytime yang selesai; port call ATD 10 hari lalu ->
laytime + voucher lewat (2 item), FDA masih 20 hari; total
`post_atd_overdue` >= 2; port call baru tanpa ATD -> `post_atd` kosong.
Selama penulisan tes ketahuan: PC contoh lama di suite sudah berlayar
semua (seed PC di Tes 5, pc27 di Tes 31, pc2 di Tes 28) — kasus
"tanpa ATD" memakai port call baru, bukan PC lama.

Terverifikasi: API 40/40, laytime + libsql lulus, UI tersaji
(`ov-post-atd`/`postAtdBadge` ada di HTML yang dilayani) + probe
langsung shift-board (field `post_atd`/`post_atd_overdue` di kartu).
