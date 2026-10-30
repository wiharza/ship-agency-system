# Inaportnet tetap kanal resmi; aplikasi adalah pencatat internal, integrasi hanya lewat kanal resmi

Aplikasi keagenan ini tidak menggantikan inaportnet. Semua pemberitahuan dan permohonan yang wajib menurut aturan tetap diajukan lewat inaportnet (dan sistem instansi terkait), sementara aplikasi menyimpan status, tenggat, arsip, bukti, dan angka milik agen. Integrasi otomatis ke sistem pemerintah hanya dijalankan lewat kanal resmi yang disediakan — API resmi dengan kredensial resmi, atau kerja sama yang disetujui pengelola sistem. Selama kanal resmi itu belum tersedia, perpindahan data dilakukan oleh manusia (input ganda terkendali), bukan dengan mengais endpoint internal atau meniru sesi pengguna secara otomatis.

Alasan: penyampaian pemberitahuan lewat kanal non-resmi dapat membatalkan keabsahan dokumen dan menempatkan agen pada risiko hukum serta pemutusan akses. Selain itu sistem pemerintah berubah tanpa pemberitahuan, sehingga integrasi tak resmi akan rusak diam-diam pada saat paling buruk, yaitu ketika kapal akan berangkat.

Konsekuensi: sampai kanal resmi tersedia, aplikasi wajib diperlakukan sebagai *tracker* dan tetap meminta unggahan bukti dari inaportnet. Fitur yang mengandalkan integrasi otomatis ditandai "menunggu kanal resmi" di spec, bukan dihapus.
