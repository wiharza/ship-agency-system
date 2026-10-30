# Urutan deliverable: playbook → spec → aplikasi

Playbook manual disusun sampai selesai sebelum spec sistem web dimulai, dan spec selesai sebelum baris kode pertama. Alasan: spec yang mendahului playbook akan mengarang alur pelabuhan, sedangkan playbook yang disesuaikan ke spec akan menulis aturan menurut kenyamanan aplikasi, bukan menurut kenyataan pelabuhan. Batas ini juga mencegah sistem tumbuh dari asumsi UI alih-alih dari prosedur yang sudah terbukti manual.

Konsekuensi: setiap kebutuhan fitur di spec wajib menunjuk ke langkah playbook yang melahirkannya. Fitur yang tidak bisa ditelusuri ke playbook ditolak atau playbook-nya yang direvisi lebih dulu.
