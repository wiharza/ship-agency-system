# ADR-0021: Tambah pelabuhan master lewat API + UI (POST /api/master/ports)

Tanggal: 2 November 2026
Status: Diterima
Konteks: Pelabuhan (master_ports, UN/LOCODE) sebelumnya hanya bisa
ditambah langsung di database — tidak ada endpoint POST dan tidak ada
form, padahal kapal (`POST /api/master/vessels` + modal "Tambah Kapal
Master") dan terminal (`POST /api/master/terminals` + "Input Terminal
Baru") sudah punya keduanya. Akibatnya pelabuhan baru (mis. Belawan)
harus di-INSERT manual ke Turso, dan edit seed di `engine/db.py` saja
tidak cukup (hanya berlaku untuk DB baru).

## 1. Endpoint: POST /api/master/ports

Validasi mengikuti pola ADR-0019 (`_req_text`/`_opt_text` + `_BadRequest`
-> 400 JSON, bukan IntegrityError):

- `code` (wajib): strip + upper; harus UN/LOCODE 5 karakter (2 huruf
  negara + 3 karakter lokasi, alfanumerik), mis. IDBLW. Huruf kecil
  dinormalisasi (`idblw` -> `IDBLW`).
- `name`, `province` (wajib): teks tidak kosong.
- `inaportnet_level` (wajib): dicek case-insensitive terhadap
  `INAPORTNET_LEVELS` dan disimpan dalam bentuk kanonik (mis. `kelas i`
  -> `Kelas I`). Daftar: Kelas Utama, Kelas I, Kelas II, Kelas III,
  Kelas III (KUPP).
- `unlocode` (opsional): default = `code`; bila diisi, dinormalisasi
  upper. Seed selalu mengisi nilai yang sama dengan `code`.
- `vts_channel`, `notes` (opsional): teks.
- `max_draft` (opsional): harus angka dan >= 0 bila diisi.
- Duplikat `code` -> 400 "Pelabuhan {code} sudah terdaftar" (pola yang
  sama dengan tambah kapal).

Tanpa gate role — konsisten dengan endpoint master lain (kapal dan
terminal juga tanpa gate). Tanpa audit log — `log_audit()` butuh
`port_call_id`, sedangkan ini master data global.

## 2. UI

- Tombol "+ Tambah Pelabuhan" di bawah dropdown pelabuhan pada modal
  "Buat Port Call Baru".
- Modal baru `#modal-new-port`: kode (maxlength 5, uppercase), nama,
  provinsi, level (select 5 nilai, default Kelas I), VTS, max draft,
  catatan.
- Setelah simpan: master data dimuat ulang, modal Port Call dibuka
  kembali dengan pelabuhan baru langsung terpilih (pola yang sama
  dengan `submitNewVessel`), terminal ikut ter-refresh lewat
  `onPortSelectChange()`. Pelabuhan tanpa terminal tetap bisa dipakai —
  dropdown terminal menampilkan "Pelabuhan Umum" (fallback yang sudah
  ada).

## 3. Yang SENGAJA tidak dikerjakan

- Edit/hapus pelabuhan: tidak ada (konsisten — master data lain pun
  belum punya edit/hapus; penghapusan pelabuhan yang sudah dipakai
  port call akan melanggar FK).
- Gate role: tidak ditambah (konsisten dengan kapal/terminal).

## Verifikasi

- Tes 39 (baru): tambah IDBLW huruf kecil -> 200 + tersimpan kanonik
  (code IDBLW, unlocode = code, level "Kelas I", max_draft 12.0);
  terminal baru untuk IDBLW ikut tampil di `GET /api/master/ports`;
  duplikat, kode 3 karakter, kode bersimbol, nama kosong, level tak
  dikenal, max_draft negatif — semua 400.
- Suite: API 39/39, laytime lulus, libsql backend lulus.
- UI tersaji (login -> GET /): modal + fungsi + tombol ada; probe
  langsung: POST `idblw` -> 200 "Pelabuhan Belawan (IDBLW) berhasil
  ditambahkan", GET master/ports menampilkan 5 pelabuhan.
