# ADR-0006: Gate SPB, tenggat clearance, audit trail, dan seeding bertahap

## Status
Diterima, 31 Oktober 2026 (branch `lengkapi-clearance-in-out`).

## Konteks
Sistem sebelumnya menerbitkan SPB tanpa memeriksa prasyarat apa pun, template
clearance baru hanya berisi 4 item, seeding DB baru gagal (foreign key),
tidak ada tenggat yang dihitung, dan `audit_logs` tidak pernah ditulis —
padahal semuanya dibutuhkan agar prosedur clearance in/out dapat dijalankan
end-to-end sesuai peraturan pemerintah.

## Keputusan

1. **Gate SPB di server, bukan di UI.** `POST /api/port-calls/<id>/spb`
   menolak (400 + daftar blocker) bila MDH & Free Pratique, RKSP / BC 1.0,
   atau Inward Manifest / BC 1.1 belum APPROVED; serta bila item bersyarat
   yang ada (Immigration Crew List, Outward Manifest, Immigration Clearance
   Out, Port Health Clearance Out, Master Sailing Declaration) belum
   APPROVED. Gate di server karena UI tidak boleh menjadi satu-satunya
   penjaga kepatuhan. SPB tetap kedaluwarsa 24 jam (PM 28/2022 Ps 9).
2. **Template clearance 12 item** untuk port call baru, dibedakan kapal
   ASING (PKKA) vs DOMESTIK (Warta Masuk / PKK). Daftar lengkap ada di
   `docs/playbook/lampiran/checklist-clearance-in-out.md`.
3. **Tenggat dihitung server** (`GET /api/port-calls/<id>/clearance-checklist`)
   dari ETA/ETD: RKSP & warta kapal ETA−24 jam, Inward Manifest sebelum
   bongkar/ETA−24 jam, Outward Manifest sebelum ETD, permohonan SPB ETD−4
   jam. Peringatan `overdue` / `due_soon` (< 4 jam) dikembalikan sebagai data,
   bukan sekadar tampilan, agar dapat diuji otomatis.
4. **Audit trail ditulis** lewat helper `log_audit()` di `engine/db.py`,
   minimal untuk: update clearance, tambah clearance, terbit SPB, milestone,
   close-ops, close-finance, tambah disbursement. Audit ditulis dalam
   transaksi yang sama dengan perubahan yang diaudit.
5. **Seeding bertahap per tabel** di `engine/db.py`: tiap tabel di-seed bila
   kosong, independen. Sample port call di-seed bila ID contoh belum ada;
   DB lama dimigrasikan (item gate SPB yang hilang ditambahkan sebagai
   APPROVED dengan nomor contoh + normalisasi nama lama PPKKA/BC 1.0/BC 1.1).
6. **Endpoint master & finance yang hilang ditambahkan**: `POST
   /api/master/vessels` (validasi IMO unik) dan `POST
   /api/port-calls/<id>/disbursements` (+ alias top-level
   `POST /api/disbursements`).
7. **Perbaikan harness tes**: `allow_reuse_address` harus disetel sebagai
   atribut kelas *sebelum* `TCPServer` dibuat (di `server.py:run_server`
   dan `tests/test_api.py`); menyetelnya pada instance setelah bind tidak
   berpengaruh dan menyebabkan `Address already in use` (TIME_WAIT) saat
   tes/server dijalankan ulang dengan cepat.

## Konsekuensi
- Port call baru tidak bisa "jalan pintas" ke SPB tanpa melengkapi
  clearance; alur demo harus menyelesaikan checklist dulu (lihat README).
- `agency.db` yang di-commit adalah hasil seeding bersih (sample
  PC-2026-JKT-0001, PRE_ARRIVAL, SPB PENDING, semua prasyarat APPROVED).
- Tidak ada autentikasi/RBAC di versi ini — audit mencatat `user` dari
  payload apa adanya; lihat README bagian Keamanan & Batasan.
