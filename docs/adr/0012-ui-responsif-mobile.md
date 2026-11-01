# ADR-0012: UI Responsif Mobile — Perbaiki Grid Blowout

Tanggal: 2026-11-01
Status: Diterima
Cabang: `ui-responsif-mobile`

## Konteks

UI utama (`ui/index.html`, 1.117 baris) adalah layout desktop-first murni:
viewport meta ada, tapi **nol `@media` query**. Di HP (390px) navigasi terasa
susah: daftar dan detail berdampingan, konten terpotong di kanan.

## Keputusan

Perbaikan CSS + satu sentuhan JS, semuanya di `ui/index.html` (desktop
tidak berubah — semua aturan baru hanya aktif di bawah 900px / 640px):

- Layout utama `.grid` (320px + 1fr) → 1 kolom di ≤900px; grid laytime
  (350px + 1fr) menumpuk; grid 2 kolom di tab/form/modal → 1 kolom di
  ≤640px (diubah dari inline `style` menjadi class `.grid-2`,
  `.grid-laytime`, `.grid-modal-form` supaya bisa diatur media query).
- Header wrap + judul 20px → 17px; banner Otorisasi Ganda + timer SPB
  menumpuk vertikal; tombol min. 44px (target sentuh); input 16px di HP
  (cegah auto-zoom iOS); body padding 20px → 12px.
- Tabel → `display: block; overflow-x: auto` di ≤640px (geser horizontal
  di dalam kartu, halaman tidak ikut melebar).
- Modal → kartu `max-height: calc(100vh - 32px)` + scroll internal,
  overlay bisa scroll; wrapper/kartu modal dipindah ke class
  `.modal-overlay` / `.modal-card`.
- JS: `selectPortCall()` menggulirkan kartu detail ke tampilan bila
  `window.innerWidth <= 900` (di HP, detail berada jauh di bawah daftar).

## Temuan saat verifikasi — grid blowout (akar masalah sebenarnya)

Perbaikan di atas saja **tidak cukup**. Pengukuran pertama di 390px
menunjukkan layout viewport mengembang sendiri ke 650px (tab Dokumen:
809px) sehingga halaman tetap terpotong di kanan. Uji eliminasi
(menyembunyikan satu per satu kandidat) membuktikan penyebabnya adalah
deretan tab (`.tabs`, ~600px):

- Item grid punya `min-width: auto` bawaan = min-content isinya.
- `.tabs` (block child dengan `overflow-x: auto`) tetap menyumbang
  min-content ~600px ke kartu, sehingga kolom `1fr` mengembang ke
  638px dan memaksa viewport melebar. `overflow-x: auto` saja tidak
  menahan min-content.
- Akibat berantai: viewport 650px > 640px membuat aturan ≤640px
  (tabel, tombol, banner) tidak aktif, dan modal 520px terpotong.

Perbaikan akar: `.grid > *, .grid-2 > *, .grid-laytime > *,
.grid-modal-form > * { min-width: 0; }` — item grid boleh menyusut ke
lebar kolom; konten lebar (tab, tabel) lalu di-scroll di dalam kartunya
sendiri. Ini perbaikan standar untuk grid blowout dan aman di desktop
(item di desktop sudah muat; `min-width: 0` hanya menghapus lantai
min-content).

## Verifikasi

Chrome headless langsung di VM tidak bisa dipakai tanpa trik (butuh
kebijakan domain WebGL Hatch + navigasi loopback diblokir), dan
Cloudflare Tunnel gagal provisioning (403) dari sandbox ini. Verifikasi
dilakukan dengan render lokal via CDP + fixture `file://` berisi HTML/
CSS/JS asli dengan data API asli yang direkam dari server lokal
(`fetch` di-mock; bukan bagian aplikasi):

- 390px: login, daftar+detail, tab Dokumen, tab Laytime, modal Buat
  Port Call, dan 820px — semua `innerWidth == scrollWidth`, tanpa
  overflow. Tabel checklist terukur 336px (muat kartu) dengan
  `scrollWidth` 665px (`display: block`, scroll internal).
- Screenshot diperiksa visual: header wrap rapi, daftar 1 kolom,
  banner SPB membungkus, tombol Otorisasi Ganda selebar penuh dan
  bertumpuk, modal menampilkan seluruh form sampai tombol Simpan.
- Suite tetap hijau: `test_laytime.py`, `test_api.py` 21/21,
  `test_libsql_backend.py`.

## Konsekuensi

- Tab bar (`.tabs`) tetap `overflow-x: auto`: di HP, tab yang aktif
  bisa berada di luar area pandang dan perlu digeser — itu perilaku
  desain yang sudah ada, bukan cacat baru.
- Server lokal `run_server()` masih single-threaded (`TCPServer`):
  satu koneksi menggantung (mis. preconnect browser) memblokir semua
  request berikutnya. Ditemukan saat verifikasi (server sempat hang
  dan harus di-restart). Di luar cakupan ADR ini; kandidat perbaikan
  berikutnya (`ThreadingHTTPServer`).
