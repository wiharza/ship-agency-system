# ADR-0007: PRD sebagai target, bukan klaim implementasi

## Status
Diterima, 31 Oktober 2026 (branch `lengkapi-clearance-in-out`).

## Konteks
Audit dokumen-vs-kode 31 Okt 2026 menemukan PRD mengklaim fitur yang tidak
ada di kode: RBAC (§2), dashboard utama (§5.1), tab Timeline Terpadu (§5.2),
auto-save SOF offline dan cetak BIMCO/FONASBA PDF (§5.2), notifikasi
email/Telegram dan pengingat tenggat (§6), serta HTTPS/TLS dan isolasi cabang
(§8). PRD §1.1 juga masih mengutip PM 65/2019 (digantikan PM 59/2021), dan
enum status di §4.1/§4.2 tidak cocok dengan `engine/db.py`. Dokumen yang
mengklaim fitur fiktif menyesatkan pengguna dan auditor — lebih berbahaya
daripada dokumen yang jujur mengakui kekurangan.

## Keputusan
1. PRD menandai setiap bagian yang belum diimplementasi dengan **[TARGET]**
   dan merinci status implementasi per fitur di **PRD §9** (tabel sumber
   kebenaran vs kode). Bila PRD dan kode bertentangan, §9 yang berlaku
   sampai salah satunya diperbaiki.
2. Setiap fitur baru wajib memperbarui PRD §9; dokumen tidak boleh mengklaim
   fitur yang tidak ada di kode.
3. Regulasi kedaluwarsa diperbaiki di tempat dengan catatan koreksi, tanpa
   menghapus kutipan verbatim yang masih berlaku sebagai arsip historis:
   PM 65/2019 → PM 59/2021; UU 6/2018 sudah dicabut UU 17/2023 (sudah
   konsisten di `docs/referensi-hukum.md` §4 dan playbook).
4. Cakupan audit trail yang diklaim PRD §8 diselaraskan dengan
   `log_audit()` yang sebenarnya (ADR-0006): yang diaudit dan yang belum,
   serta fakta bahwa alamat IP tidak disimpan.

## Konsekuensi
- Pengguna yang membaca PRD kini tahu persis mana yang sudah bisa dipakai
  (gate SPB, checklist tenggat, audit, laytime, dual sign-off) dan mana yang
  masih rencana (RBAC, notifikasi, dashboard, auto-save offline).
- Fitur [TARGET] tetap menjadi backlog yang sah — bukan dihapus — tetapi
  tidak boleh dijual/dijanjikan sebagai sudah ada.
