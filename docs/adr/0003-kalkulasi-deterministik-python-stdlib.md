# Kalkulasi komersial dijalankan script Python stdlib, bukan LLM atau spreadsheet

Perhitungan laytime dari NOR + SOF, rollup PDA, dan rekonsiliasi FDA dijalankan oleh script Python yang memakai standard library saja — bukan ditanyakan ke model bahasa, dan bukan diserahkan ke spreadsheet ad-hoc. Setiap script menerima parameter kontrak (mis. SHEX/SHINC, WWD, whether in berth or on arrival) sebagai input eksplisit dan tidak menafsirkan klausul sendiri.

Alasan: angka yang keluar dari model bahasa tidak dapat direproduksi atau diperiksa ulang, padahal angka inilah yang menjadi dasar tagihan ke principal dan sengketa demurrage. Script stdlib memberi hasil yang sama untuk input yang sama, bisa dijalankan ulang saat audit, dan tidak menambah dependensi yang harus dipelihara.

Konsekuensi: kontrak yang tidak lazim tetap dihitung manual oleh agen; script menolak menebak, dan menandai parameter yang tidak diberikan.
