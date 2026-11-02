# ADR-0024: Milestone GANGWAY_DOWN + COB

Tanggal: 2 November 2026
Status: Diterima
Konteks: SOP Tahap 3 §3.1 mewajibkan pencatatan "menit tepat first
line, all fast, dan gangway down", dan Tahap 4 menjadikan COB (cargo
documents on board) sebagai pemicu clearance-out — tetapi
`MILESTONE_CODES` hanya berisi 11 kode dan keduanya tidak ada, sehingga
dua peristiwa yang wajib dicatat menit tepatnya itu tidak bisa masuk
sistem.

## 1. Perubahan

- `MILESTONE_CODES` (server.py) bertambah 2 kode menjadi 13:
  `GANGWAY_DOWN` (setelah ALL_FAST, sebelum COMMENCED_CARGO) dan
  `COB` (setelah COMPLETED_CARGO, sebelum CAST_OFF) — urutan sesuai
  alur operasional.
- Dropdown milestone di UI (`#ms-code`) bertambah 2 opsi yang sama:
  "Tangga Turun (Gangway Down)" dan "Dokumen Muatan di Kapal (COB)".

## 2. Keduanya tidak mengubah status

Perubahan status port call hanya dipicu daftar eksplisit di handler
milestone (EOSP/ANCHORED -> ARRIVED, FIRST_LINE/ALL_FAST/
COMMENCED_CARGO -> BERTHED, CAST_OFF/SAILED -> DEPARTED). GANGWAY_DOWN
dan COB tidak ada di daftar itu, jadi keduanya tercatat murni sebagai
fakta dengan `event_time` menit tepat (dan audit `MILESTONE_<kode>`),
tanpa menggerakkan status — sesuai usulan.

## 3. Verifikasi

Tes 41 baru: `gangway_down` huruf kecil dinormalisasi jadi
GANGWAY_DOWN (ADR-0019), COB tercatat, keduanya membawa `event_time`
yang dikirim, status port call tetap PRE_ARRIVAL. Terverifikasi: API
41/41, laytime + libsql lulus.
