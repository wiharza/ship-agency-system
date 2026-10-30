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
