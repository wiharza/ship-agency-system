# ship-agency-system

Sistem Keagenan Kapal Nasional — tracker internal operasional & komersial
untuk prosedur **clearance in** dan **clearance out** kapal di pelabuhan
Indonesia, sesuai peraturan pemerintah. Python stdlib saja (tanpa dependensi
baru), SQLite, UI HTML di `ui/index.html`.

> **BATASAN PENTING — BACA DULU.** Sistem ini adalah **tracker internal**,
> **bukan pengganti portal resmi**. Pengajuan resmi tetap dilakukan di
> **Inaportnet** (PM 8/2022), **INSW** (Warta I–V), **CEISA** (Bea Cukai),
> **Simkapel**, dan **SINKARKES** (Karantina Kesehatan). Sistem ini hanya
> mencatat nomor dokumen resmi, status, tenggat, dan bukti (hash SHA-256).
> **INSW berbeda dengan Inaportnet**: INSW adalah single window nasional
> lintas instansi; Inaportnet adalah layanan kapal Ditjen Perhubungan Laut.
> NOR / Statement of Facts dan PDA → husbandry → FDA adalah **praktik
> komersial charter party, bukan regulasi Kemenhub** — jangan klaim sebaliknya.
> Tidak ada tarif nominal di sistem ini; biaya mengikuti tarif resmi yang
> berlaku saat itu.

## Cara menjalankan

```bash
python3 server.py            # atau: python3 server.py 8000
# Buka http://localhost:8000
```

Server menjalankan `init_db()` + `seed_sample_data()` saat start. Data contoh
yang ter-seed: 4 pelabuhan, 15 terminal, 2 cabang, 2 principal, 2 kapal, dan
port call contoh **PC-2026-JKT-0001** (MV. PACIFIC GLORY, kapal asing,
Tanjung Priok, PRE_ARRIVAL, SPB PENDING — semua prasyarat SPB sudah APPROVED
dengan nomor contoh, sehingga siap dipakai demo penerbitan SPB).

## Alur demo end-to-end (dengan DB baru)

```bash
# 1. Mulai dari DB baru (cadangkan dulu bila ada data)
mv agency.db /tmp/agency.db.bak
python3 server.py 8000 &
# 2. Buat port call baru (kapal asing; template clearance 12 item dibuat otomatis)
curl -s -X POST localhost:8000/api/port-calls -H 'Content-Type: application/json' \
  -d '{"vessel_imo":"9431289","port_code":"IDTPP","terminal_name":"JICT (Jakarta Int. Container Terminal)","cargo_name":"Peti Kemas","cargo_tonnage":25000,"eta":"2026-11-10 08:00","vessel_category":"FOREIGN"}'
# -> {"port_call_id": "PC-2026-TPP-0002", ...}  (nomor urut mengikuti isi DB)
# 3. Lihat checklist tenggat (RKSP deadline = ETA-24 jam, dst.)
curl -s localhost:8000/api/port-calls/PC-2026-TPP-0002/clearance-checklist
# 4. Coba terbitkan SPB sekarang -> DITOLAK 400 + daftar blocker
curl -s -X POST localhost:8000/api/port-calls/PC-2026-TPP-0002/spb -H 'Content-Type: application/json' \
  -d '{"doc_number":"SPB.IDTPP.2026.001","issued_at":"2026-11-10 10:00:00"}'
# 5. Setujui semua clearance template (kecuali SPB) via GET detail -> ambil id
#    clearance, lalu POST /api/clearances/<id>/update {"doc_number":"...","status":"APPROVED"}
#    (di UI: tab Dokumen & CIGP -> Edit per baris)
# 6. Terbitkan SPB lagi -> 200, spb_expiry_at = issued_at + 24 jam (PM 28/2022 Ps 9)
# 7. Catat milestone + hitung laytime
curl -s -X POST localhost:8000/api/port-calls/PC-2026-TPP-0002/milestone -H 'Content-Type: application/json' \
  -d '{"milestone_code":"EOSP","event_time":"2026-11-10 07:30","remarks":"Tiba di ambang luar"}'
curl -s -X POST localhost:8000/api/port-calls/PC-2026-TPP-0002/laytime/calculate -H 'Content-Type: application/json' \
  -d '{"nor_tendered_time":"2026-11-10 08:00","commenced_time":"2026-11-10 14:00","completed_time":"2026-11-12 14:00"}'
# 8. Tambah item PDA/FDA, catat keberangkatan, lalu tutup ops + finance
#    (close-ops DIGATE: ditolak bila SPB belum terbit/kedaluwarsa atau kapal
#    belum DEPARTED — catat SAILED dulu, dalam masa berlaku SPB 24 jam)
curl -s -X POST localhost:8000/api/port-calls/PC-2026-TPP-0002/milestone -H 'Content-Type: application/json' \
  -d '{"milestone_code":"SAILED","event_time":"2026-11-10 18:00","remarks":"Berangkat setelah SPB terbit"}'
curl -s -X POST localhost:8000/api/port-calls/PC-2026-TPP-0002/disbursements -H 'Content-Type: application/json' \
  -d '{"category":"HUSBANDRY","item_name":"Bunkering 100 MT","pda_estimate":50000000,"fda_actual":0}'
curl -s -X POST localhost:8000/api/port-calls/PC-2026-TPP-0002/close-ops -H 'Content-Type: application/json' -d '{"user":"Capt. Hadi"}'
curl -s -X POST localhost:8000/api/port-calls/PC-2026-TPP-0002/close-finance -H 'Content-Type: application/json' -d '{"user":"Finance Mgr"}'
# 9. Periksa jejak audit
curl -s localhost:8000/api/port-calls/PC-2026-TPP-0002/audit-logs
```

Di UI (`http://localhost:8000`): tab **Dokumen & CIGP** menampilkan panel
checklist tenggat (badge merah/kuning/hijau + status bisa-tidaknya SPB
diterbitkan); tombol **+ Tambah Item PDA/FDA** ada di tab Finance; tombol
**+ Tambah Kapal Master** ada di modal Buat Port Call Baru.

## Endpoint

| Metode & path | Fungsi |
|---|---|
| `GET /` | UI HTML |
| `GET /api/port-calls` | Daftar port call |
| `GET /api/port-calls/<id>` | Detail (clearances, milestones, SOF, disbursements, disputes) |
| `POST /api/port-calls` | Buat port call + template clearance 12 item (ASING: PKKA, DOMESTIK: PKK) |
| `GET /api/port-calls/<id>/clearance-checklist` | Checklist in/out + tenggat dari ETA/ETD + `can_issue_spb` + blockers |
| `GET /api/port-calls/<id>/audit-logs` | Jejak audit port call |
| `GET /api/port-calls/<id>/handover` | Ringkasan handover shift |
| `POST /api/port-calls/<id>/milestone` | Catat milestone (EOSP/ANCHORED/FIRST_LINE/…) |
| `POST /api/port-calls/<id>/stoppage` | Catat jeda SOF |
| `POST /api/port-calls/<id>/laytime/calculate` | Hitung laytime/demurrage/despatch (deterministik, stdlib) |
| `POST /api/port-calls/<id>/spb` | Terbitkan SPB — **digate** prasyarat clearance; kedaluwarsa 24 jam |
| `POST /api/port-calls/<id>/clearance` | Tambah item dokumen clearance |
| `POST /api/clearances/<id>/update` | Update nomor/status clearance |
| `POST /api/port-calls/<id>/disbursements` | Tambah item PDA/FDA |
| `POST /api/disbursements` | Alias tambah item PDA/FDA (pakai `port_call_id` di body) |
| `POST /api/port-calls/<id>/dispute` | Buka tiket sengketa FDA |
| `POST /api/port-calls/<id>/close-ops` | Otorisasi 1 (operasional) — **digate**: ditolak 400 bila SPB belum terbit, SPB kedaluwarsa, atau kapal belum DEPARTED; ditolak bila sudah ditutup |
| `POST /api/port-calls/<id>/close-finance` | Otorisasi 2 (keuangan; menolak bila ops belum tutup) |
| `GET /api/master/ports` | Master pelabuhan + terminal + kapal |
| `POST /api/master/terminals` | Tambah terminal |
| `POST /api/master/vessels` | Tambah kapal master (IMO unik) |

## Aturan yang dipakai + sumber

- UU 17/2008 jo UU 66/2024 (Pelayaran); PP 31/2021; **PM 59/2021**
  (keagenan — menggantikan PM 65/2019 yang masih dikutip di
  `docs/referensi-hukum.md` §2 dan `docs/spec/prd.md` sebagai referensi lama).
- **PM 8/2022** (Inaportnet, 5 layanan; verifikasi kedatangan maks 6 jam
  sebelum ETA, keberangkatan maks 4 jam sebelum ETD kapal barang).
- **PM 28/2022** (SPB: permohonan elektronik + Master Sailing Declaration,
  berlaku 24 jam, 1x pelayaran, Ps 9; SPOG/SPKK).
- **PM 57/2015** (pandu wajib ≥ GT 500).
- Bea Cukai: **PMK 158/2017 jo PMK 97/2020** — RKSP/BC 1.0 dan Inward
  Manifest/BC 1.1 paling lambat 24 jam sebelum kedatangan (tempuh ≥ 24 jam)
  atau sebelum kedatangan (tempuh < 24 jam); Outward Manifest BC 1.1 sebelum
  keberangkatan; denda Ps 7A UU 17/2006 Rp5–50 juta.
- Imigrasi: **UU 6/2011 Ps 18** (pemberitahuan + crew list/passenger list),
  shore pass, sign on/off via TPI.
- Karantina Kesehatan: **UU 17/2023** (mencabut UU 6/2018) + **PP 28/2024**
  — MDH, SSCEC/SSCC maks 6 bulan, Free Pratique / Port Health Clearance;
  tanpa persetujuan karantina kesehatan, Syahbandar dilarang terbitkan SPB.
  Karantina muatan: **UU 21/2019 Ps 33/38** (Barantin).
- Detail per pasal: `docs/referensi-hukum.md`; alur kerja:
  `docs/playbook/01-pra-kedatangan.md` s.d. `05-pasca-keberangkatan-dan-penutupan-komersial.md`;
  checklist: `docs/playbook/lampiran/checklist-clearance-in-out.md`;
  keputusan arsitektur: `docs/adr/`.

## Dokumen & spesifikasi (perubahan 31 Okt 2026)

- **PRD** (`docs/spec/prd.md`): §1.1 diperbaiki PM 65/2019 → **PM 59/2021**;
  enum status §4.1/§4.2 diselaraskan dengan kode (`PRE_ARRIVAL…CLOSED`,
  `PENDING…EXPIRED`); ditambahkan gate SPB, template clearance 12 item,
  endpoint baru, dan **§9 Status Implementasi** — tabel sumber kebenaran vs
  kode. Fitur yang belum ada (RBAC, dashboard, tab timeline, auto-save SOF
  offline, notifikasi email/Telegram, HTTPS/TLS) ditandai **[TARGET]**,
  bukan diklaim sudah ada.
- **Referensi hukum** (`docs/referensi-hukum.md`): §2 PM 65/2019 diberi label
  **digantikan PM 59/2021** (kutipan verbatim dipertahankan sebagai arsip)
  + §2A ringkasan PM 59/2021; ditambah §3A PM 8/2022 (Inaportnet, 5 layanan).
  UU 6/2018 sudah konsisten sebagai "dicabut UU 17/2023" (§4).
- **Playbook**: Tahap 1 ditambah §1.6A INSW vs Inaportnet (Warta I–V) dan
  baris PKKA vs PKK; Tahap 2 §2.6 diperbaiki ke PM 59/2021; Tahap 3 §3.4
  ditandai auto-save offline belum diimplementasi; Tahap 4 §4.6 diselaraskan
  ke permohonan SPB ETD−4 jam + gate SPB; Tahap 5 §5.2 diperbaiki nama file
  `engine/laytime.py`; Lampiran checklist 5 tahap menunjuk ke lampiran
  checklist clearance in/out yang selaras dengan kode.
- **Keputusan/ADR**: `docs/keputusan.md` Q33–Q37 (gate SPB, template 12,
  audit, kejujuran spec, ETD opsional); `docs/adr/0006-*` (gate/tenggat/
  audit) dan `docs/adr/0007-*` (PRD sebagai target, bukan klaim implementasi).
- **Glosarium** (`GLOSSARY.md`): ditambah INSW, Warta I–V, PKK, MSD,
  Free Pratique, RKSP/BC 1.0, BC 1.1, SPKK, TPI, SINKARKES, Simkapel,
  PNBP/SIMPONI/NTPN, Shore Pass.

## Cara menjalankan tes

```bash
python3 -m py_compile server.py engine/db.py engine/laytime.py
python3 tests/test_laytime.py
python3 tests/test_api.py     # tes integrasi HTTP (port 8999); memutasi agency.db
# Backend libsql (opsional, butuh paket libsql):
pip install -r requirements.txt
python3 tests/test_libsql_backend.py   # SKIP bila libsql belum terinstal
```

**Perhatian:** `tests/test_api.py` memutasi `agency.db` (menutup port call
contoh, membuat port call + kapal uji). Cadangkan dulu (`cp agency.db
/tmp/agency.db.bak`) dan kembalikan sesudahnya, atau bangun ulang DB bersih
dengan menghapus `agency.db` lalu menjalankan `python3 server.py` sekali
(seeding otomatis) dan menghentikannya.

## Persistensi Turso (opsional — untuk deploy Vercel)

Tanpa Turso, data di Vercel hanya hidup selama instance warm (salinan
`/tmp/agency.db`), lalu reset. Dengan Turso (libSQL remote), data persisten
dan sama untuk semua instance. Lokal tetap bisa jalan tanpa Turso.

```bash
# 1. Buat database + token (butuh Turso CLI)
turso auth login
turso db create ship-agency
turso db show ship-agency --url        # -> libsql://ship-agency-xxxx.turso.io
turso db tokens create ship-agency     # -> token (jaga baik-baik!)

# 2. Lokal: salin .env.example ke .env dan isi dua variabelnya, lalu migrasikan data
cp .env.example .env   # JANGAN commit .env (sudah di .gitignore)
pip install -r requirements.txt
python3 scripts/migrate_to_turso.py        # batal bila Turso sudah berisi data
python3 scripts/migrate_to_turso.py --force  # timpa isi Turso
python3 scripts/migrate_to_turso.py --seed   # isi Turso dengan data contoh saja

# 3. Vercel: isi TURSO_DATABASE_URL + TURSO_AUTH_TOKEN di
#    Settings -> Environment Variables, lalu deploy ulang.
#    Tanpa kedua variabel itu, aplikasi otomatis memakai SQLite lokal.
```

Detail: `docs/adr/0009-persistensi-turso-libsql.md`. Tier gratis Turso
(Starter): 500 database, 9 GB storage, 1 miliar baris dibaca/bulan,
25 juta baris ditulis/bulan. Catatan: perilaku remote (PRAGMA foreign key,
transaksi, latensi) belum terverifikasi terhadap Turso sungguhan — yang
terverifikasi baru mode file lokal libsql (`tests/test_libsql_backend.py`).

## Keamanan & batasan (jujur)

- **Tidak ada autentikasi / RBAC di versi ini.** Siapa pun yang bisa
  mengakses server bisa mengubah data; kolom `user` di audit diambil dari
  payload apa adanya. Jangan expose server ini ke internet. Rencana:
  autentikasi + peran (ops/finance/read-only) dan pencatatan user terverifikasi.
- **Bukan portal resmi** (lihat batasan di atas); tidak ada integrasi otomatis
  ke Inaportnet/INSW/CEISA — data dipindahkan manusia (input ganda terkendali)
  sampai kanal resmi tersedia (ADR-0004).
- **Tidak ada notifikasi** email/Telegram, tidak ada pengingat tenggat
  otomatis (tenggat hanya dihitung & ditampilkan saat checklist dibuka).
- Data master contoh (kapal/principal/cabang) dan nomor dokumen contoh di
  seed **harus diganti** dengan data asli perusahaan sebelum dipakai produksi.
- Akun portal resmi (Inaportnet, INSW, CEISA, Simkapel, SINKARKES) tetap
  dipegang user; sistem ini tidak menyimpan kredensial portal apa pun.
