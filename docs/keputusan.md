# Log Keputusan

Catatan putusan pemilik sistem selama grilling. Setiap butir dipakai sebagai batas kerja; ADR hanya dibuat untuk putusan yang sulit dibalik.

| # | Putusan |
| :--- | :--- |
| Q1 | Cakupan end-to-end: perizinan → operasi → komersial → finansial. Bongkar/muat hanya titik sentuh. |
| Q2 | Cakupan nasional. Perbedaan daerah jadi profil pelabuhan (data), bukan aturan. |
| Q3 | Target akhir aplikasi web; sebelum itu playbook manual, lalu spec sistem. |
| Q4 | Patokan utama dokumen resmi; naskah non-resmi boleh sebagai pelengkap, selalu ditandai. |
| Q5 | Aturan wajib inaportnet sebagai baseline; deviasi lapangan dicatat terpisah. |
| Q6 | Lokasi catatan ditentukan penulis sistem (repo `ship-agency-system`). |
| Q7 | Sisi komersial pada tingkat konsep + template + aturan hitung; tafsir charter party tidak diambil. |
| Q8 | Kalkulasi dengan Python stdlib. |
| Q9 | Playbook: SOP 5 tahap + lampiran checklist dan template. Instansi jadi kolom di dalam tahap. |
| Q10 | Spec sistem setingkat PRD: aktor, use case, alur layar, entitas data, kebutuhan non-fungsional. |
| Q11 | Aktor: staf agen dengan peran terpisah (operasional, dokumen, finance) + akses baca principal. |
| Q12 | Unit kerja pusat: Port Call. |
| Q13 | Urutan dikunci: playbook → spec → aplikasi. |
| Q14 | Script laytime: hitung mundur NOR+SOF, rekap durasi per fase, dan laporan otomatis untuk principal. |
| Q15 | Profil pelabuhan disimpan sebagai berkas data per pelabuhan. |
| Q16 | Aplikasi sebagai pencatat internal, sekaligus integrasi sebagai pilihan — integrasi hanya lewat kanal resmi. |
| Q17 | Tenggat bertingkat: hukum (merah) dan internal agen (kuning). |
| Q18 | Jejak bukti: nomor dokumen, stempel waktu, pelaksana, lampiran, catatan, hash SHA-256 berkas. |
| Q19 | Bahasa Indonesia dengan istilah teknis Inggris apa adanya. |
| Q20 | Data induk: Principal, Kapal, Agen, Vendor jasa, Personel, Pelabuhan + profil, Kontrak/Charter Party. |
| Q21 | Notifikasi tiga kanal: dalam aplikasi, email, Telegram. |
| Q22 | Bongkar/muat hanya pembatas waktu + dokumen; tidak mengelola rencana muat. |
| Q23 | Klasifikasi kapal: dipisah sejak awal antara Kapal Asing (PKKA, BC 1.1, TPI) dan Kapal Domestik (PKK). |
| Q24 | Ketahanan koneksi: web app online, form SOF & checklist auto-save lokal di browser mencegah data hilang. |
| Q25 | Struktur organisasi: multi-kantor cabang; master data terpusat, operasional per cabang. |
| Q26 | Otorisasi penutupan Port Call ganda: Dokumen/Ops (clearance fisik selesai) dan Finance (FDA direkonsiliasi). |
| Q27 | Masa retensi data: minimal 10 tahun tanpa hapus otomatis (perlindungan sengketa charter party & audit pajak). |
| Q28 | Handover shift: ringkasan dinamis per port call (izin pending, tugas tertunda, deadline terdekat) untuk tim 24/7. |
| Q29 | Pelacakan modul Inaportnet: satu timeline terpadu per port call dengan tag modul (SIMPADU, SPS, BUP, dll). |
| Q30 | Pencatatan PNBP: catat nomor aju, kode billing SIMPONI, NTPN, dan bukti bayar; tidak menghitung tarif sendiri. |
| Q31 | Batas waktu penyusunan FDA: maksimal 30 hari kalender setelah keberangkatan kapal (ATD). |
| Q32 | Penanganan sengketa FDA: modul dispute tracking khusus (status klaim, deadline respon, riwayat negosiasi). |
| Q33 | Gate SPB ditegakkan di server (31 Okt 2026, ADR-0006): SPB ditolak 400 + daftar blocker bila prasyarat clearance belum APPROVED. UI tidak boleh menjadi satu-satunya penjaga kepatuhan. |
| Q34 | Template clearance port call baru: 12 item, dibedakan kapal ASING (PKKA) vs DOMESTIK (Warta Masuk / PKK); tenggat dihitung server dari ETA/ETD (RKSP & warta ETA−24 jam, Inward sebelum bongkar/ETA−24 jam, Outward sebelum ETD, permohonan SPB ETD−4 jam). |
| Q35 | Audit trail ditulis lewat `log_audit()` dalam transaksi yang sama dengan perubahan yang diaudit; cakupan saat ini: update/tambah clearance, terbit SPB, milestone, close-ops, close-finance, tambah disbursement (ADR-0006). |
| Q36 | Kejujuran spesifikasi (31 Okt 2026, ADR-0007): PRD menandai fitur yang belum diimplementasi sebagai [TARGET] dan merinci statusnya di PRD §9; dokumen tidak boleh mengklaim fitur yang tidak ada di kode (RBAC, notifikasi, dashboard, auto-save offline). |
| Q37 | ETD opsional saat pembuatan port call; bila ETD kosong, tenggat clearance-out tidak dapat dihitung sistem dan checklist menampilkan peringatan tersebut. |
| Q38 | Gate close-ops (31 Okt 2026, ADR-0008): Otorisasi 1 ditolak 400 bila SPB belum terbit, SPB kedaluwarsa 24 jam, atau status kapal belum DEPARTED; ditolak juga bila sudah ditutup. Milestone CAST_OFF/SAILED tanpa SPB valid tetap dicatat sebagai fakta lapangan tapi status tidak berubah jadi DEPARTED; bila lolos, atd diisi dari event_time milestone. |
| Q39 | Persistensi Turso (31 Okt 2026, ADR-0009): backend opsional berbasis env — bila TURSO_DATABASE_URL + TURSO_AUTH_TOKEN terisi, get_db() memakai libsql remote; bila tidak, SQLite lokal seperti biasa. Karena libsql tidak punya row_factory, koneksi dibungkus adapter _Row/_LibSQLConnection agar server.py tidak berubah. Token tidak pernah masuk Git. Diverifikasi 31 Okt 2026 terhadap Turso remote sungguhan (agency-yansen): row_factory, FK, transaksi, alur gate end-to-end lulus. |
| Q40 | Optimasi cold start (1 Nov 2026, ADR-0010): cold start Vercel hanya menjalankan 1 query kesiapan (COUNT gabungan master_ports + port_calls) bila DB sudah siap, bukan init_db()+seed penuh; koneksi libsql remote di-cache per thread dan dipakai ulang antar-request (close() no-op untuk koneksi shared; drop_remote_connection() bila stale; transaksi tertinggal di-rollback sebelum dipakai ulang). Hasil ukur vs Turso remote: request pertama 39 dtk → 2,53 dtk, request hangat ~0,44 dtk; cold start 3 statement (PRAGMA + COUNT gabungan + query). Sisa latensi adalah round-trip jaringan per query; embedded replica dicatat sebagai opsi lanjutan. |
| Q41 | Autentikasi pengguna (1 Nov 2026, ADR-0011): hibrida — identitas + role di Turso (tabel `users`, role OPS/FINANCE/ADMIN, hash PBKDF2), session secret di env var `SESSION_SECRET`, session cookie stateless ter-sign HMAC (verifikasi lokal tanpa query DB per request; TTL 12 jam). Semua `/api/*` menolak 401 tanpa session; `/` menampilkan halaman login bila belum login; gate role close-ops (OPS/ADMIN) dan close-finance (FINANCE/ADMIN) dicek sebelum gate SPB; actor audit/close diambil dari session, bukan payload client. Rate limit login 5x gagal/15 menit -> 429. Tanpa SESSION_SECRET server menolak jalan (fail closed). User dibuat lewat `scripts/create_user.py`, tidak ada pendaftaran publik dan tidak ada user bawaan. Batasan disadari: pencabutan user/role baru berlaku setelah cookie kedaluwarsa (kecuali rotate secret); role masih kasar (hanya 2 endpoint di-gate); belum ada isolasi per cabang. |
