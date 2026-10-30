# Integrasi otomatis disiapkan sebagai lapisan terpisah, bukan syarat berjalannya sistem

Perpindahan data ke sistem pemerintah dikerjakan lewat dua jalur berlapis: (1) jalur manusia — staf agen menginput di inaportnet/sistem instansi lalu mengunggah bukti ke aplikasi; (2) jalur otomatis — adaptor per sistem yang aktif hanya bila ada kanal resmi (API resmi dengan kredensial resmi, atau kerja sama tertulis dengan pengelola sistem). Semua fitur berjalan penuh pada jalur 1; jalur 2 hanya mempercepat, tidak boleh menjadi syarat.

Alasan: sistem harus tetap benar dan lengkap ketika jalur otomatis mati — saat kredensial dicabut, saat sistem pemerintah berubah, atau saat kapal harus berangkat sekarang. Membangun fitur inti di atas integrasi berarti satu perubahan endpoint mematikan operasional pelabuhan.

Konsekuensi: setiap adaptor diuji dengan data rekaman, bukan data langsung, dan hasilnya selalu ditandai asalnya. Kontrak data adaptor dipisahkan dari model data inti, supaya perubahan format pemerintah tidak menular ke seluruh sistem.
