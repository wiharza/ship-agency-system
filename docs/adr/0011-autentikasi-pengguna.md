# ADR-0011: Autentikasi Pengguna (Users + Roles + Session Cookie Ter-Sign)

Status: Diterima (1 Nov 2026)
Konteks: PRD §9, Putusan Q41

## Masalah

Aplikasi tidak punya autentikasi sama sekali: siapa pun yang bisa mencapai
server (lokal maupun Vercel) bisa mengubah data — menerbitkan SPB, menutup
ops/finance, mengubah clearance. Lebih dalam lagi, dua fitur yang sudah
dibangun **butuh identitas nyata** untuk menjadi benar:

1. `audit_logs` (jejak audit 10 tahun) — `user_id` diambil dari
   `payload.get("user", ...)`; siapa pun bisa POST sebagai `"Finance Mgr"`
   dan jejak audit percaya begitu saja. Jejak audit yang bisa dipalsukan
   client bukan jejak audit.
2. Dual authorization (Otorisasi 1 Ops → Otorisasi 2 Finance) — dengan satu
   password bersama, "dua otorisasi" jadi ilusi: orang yang sama memasukkan
   keduanya. Domain ini (SPB, clearance, PM 28/2022) justru tentang *siapa*
   mengesahkan apa.

## Keputusan: hibrida — identitas di Turso, rahasia di env var

| Kebutuhan | Taruh di mana | Kenapa |
|---|---|---|
| User, role, status aktif | Turso (`users`) | Bisa dicabut per-orang, ikut persisten |
| Hash password | Turso (kolom `users.password_hash`) | PBKDF2 stdlib, bukan plaintext |
| Session secret (penanda cookie) | Env var `SESSION_SECRET` | Rahasia, bukan data — tidak boleh di DB/Git |
| Verifikasi session per request | Lokal (HMAC cookie) | TANPA round trip ke Turso |

Poin terakhir disengaja dan nyambung dengan ADR-0010: **session tidak
pernah dicek ke database di tiap request** — itu menambah 1 round trip
jaringan per request padahal verifikasi HMAC cookie hanya butuh mikrodetik
di memori. DB hanya disentuh saat login (1× SELECT user).

## Desain

### Skema (`engine/db.py:init_db()`)

```sql
CREATE TABLE IF NOT EXISTS users (
    username      TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,   -- pbkdf2$260000$salt_hex$hash_hex
    full_name     TEXT NOT NULL,
    role          TEXT NOT NULL CHECK(role IN ('OPS','FINANCE','ADMIN')),
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL
);
```

Tidak ada user bawaan di-seed (tidak ada kredensial default yang bisa
ditebak). User pertama dibuat lewat `scripts/create_user.py`.

### Password (`engine/auth.py`)

- `hashlib.pbkdf2_hmac("sha256", pw, salt_16_byte, 260_000)`; verifikasi
  selalu `hmac.compare_digest` (timing-safe); hash rusak -> False.
- Password minimal 8 karakter (ditegakkan di `hash_password` dan di script).

### Session cookie (stateless, `engine/auth.py`)

- Nilai cookie: `base64url("username|role|expiry_unix") + "." +
  hmac_sha256_hex(SESSION_SECRET, payload_b64)`.
- TTL 12 jam. Atribut: `HttpOnly; Path=/; SameSite=Lax; Max-Age=43200`,
  plus `Secure` bila request datang lewat HTTPS (`X-Forwarded-Proto:
  https` — Vercel meneruskannya; lokal HTTP tanpa Secure supaya login
  tetap jalan di `http://localhost`).
- Verifikasi: split, hitung ulang HMAC, `compare_digest`, cek expiry dan
  role. Tanpa query DB.
- Cookie mengalir otomatis ke semua `fetch()` same-origin, jadi UI hampir
  tidak berubah (hanya tombol Keluar + cek `/api/me`).

### Enforcement (`server.py`)

- `do_GET`: `/` menampilkan halaman login bila belum login, UI bila sudah;
  `/api/me` mengembalikan user session; semua `/api/*` lain menolak 401
  tanpa session valid.
- `do_POST`: `/api/login` publik (rate-limited), `/api/logout` publik
  (hapus cookie); semua POST lain butuh session valid.
- Gate role: `close-ops` hanya `OPS`/`ADMIN`, `close-finance` hanya
  `FINANCE`/`ADMIN` — dicek SEBELUM gate SPB/status supaya penolakan role
  jelas (403). Route lain boleh semua role yang sudah login (peran lebih
  halus = pekerjaan lanjutan, lihat Konsekuensi).
- **Actor dari session**: semua `log_audit()`, `ops_closed_by` /
  `finance_closed_by`, `recorded_by` milestone, dan `submitted_by`
  clearance mengambil username dari session — nilai `"user"` dari payload
  client diabaikan sepenuhnya. Lubang pemalsuan audit tertutup.

### Rate limit login (`engine/auth.py`)

In-memory per instance: 5 percobaan gagal per username dalam 15 menit
-> 429. Pesan gagal login disamakan untuk semua kasus (user tidak ada /
nonaktif / password salah) supaya tidak membocorkan user mana yang
terdaftar. Login berhasil menghapus counter.

### SESSION_SECRET kosong = fail closed

- `run_server()` gagal cepat (RuntimeError dengan pesan jelas + cara
  mengisi) bila secret belum diisi — server tidak jalan tanpa autentikasi.
- Di Vercel (WSGI), tiap request dijawab 500 JSON dengan pesan yang sama.
- Tanpa secret, tidak ada mode "jalan tanpa login" — itu fail open.

## Alternatif yang ditolak

- **Satu password bersama di env (`APP_PASSWORD`)**: cukup untuk demo
  solo, tapi membuat audit + dual authorization bohong (lihat Masalah).
  Ditolak untuk app ini; desain hibrida di atas menggantikannya.
- **Session di database (cek per request)**: +1 round trip Turso per
  request (~0,4 dtk hangat) — ditolak karena optimasi ADR-0010.
- **Auth terkelola (Clerk/Auth0/Supabase)**: benar untuk produksi dengan
  data principal asli, tapi melanggar ADR-0003 (stdlib-only) dan butuh
  akun/dependensi luar. DIY auth di sini layak untuk demo/internal tool
  dengan blast radius kecil; bila app naik ke produksi nyata, ADR ini
  layak direvisi ke auth terkelola.

## Konsekuensi

- **Pencabutan tidak instan**: cookie stateless — user yang dinonaktifkan
  atau role-nya diubah tetap bisa memakai cookie lama sampai kedaluwarsa
  (maks 12 jam). Satu-satunya pencabutan instan: rotate `SESSION_SECRET`,
  yang membatalkan SEMUA session sekaligus. Diterima untuk scope ini.
- **Rate limit best-effort**: per instance; serverless Vercel bisa punya
  banyak instance, jadi limit tidak global. Cukup memperlambat brute
  force, bukan pengganti WAF.
- **Role kasar**: hanya dua endpoint yang di-gate per role. SPB,
  clearance, disbursement, dan master data boleh semua role yang login.
  Peran lebih halus (mis. SPB hanya OPS) adalah pekerjaan lanjutan.
- **Tidak ada reset password mandiri**: reset hanya lewat
  `scripts/create_user.py --update` oleh admin.
- **Vercel**: wajib tambah env var `SESSION_SECRET` (Production) sebelum
  deploy — tanpa itu aplikasi menjawab 500. Nilai: sekali generate
  `os.urandom(32).hex()`; jangan dipakai ulang antar environment.

## Addendum (1 Nov 2026): user default di-seed (Q42)

`seed_sample_data()` kini membuat dua user default saat DB pertama kali
dibuat: `ops_jakarta` (OPS) dan `fin_jakarta` (FINANCE). Password dari env
`SEED_OPS_PASSWORD` / `SEED_FIN_PASSWORD` bila diisi; bila tidak, password
default yang terdokumentasi: `OpsJakarta2026` / `FinJakarta2026`.

Ini sengaja dibuat nyaman, bukan aman: siapa pun yang membaca kode tahu
password defaultnya. Maka aturannya: **wajib diganti setelah login
pertama** (`scripts/create_user.py <user> --update`), dan seed mencetak
peringatan keras ke log saat user default dibuat. DB yang sudah ada
(seperti Turso `agency-yansen`) tidak ikut di-seed ulang — user default
di sana dibuat sekali lewat `scripts/create_user.py` dengan env Turso.

## Verifikasi (1 Nov 2026)

- `tests/test_api.py`: 21 tes lulus — termasuk Tes 1 (halaman login tanpa
  session), 17 (401 tanpa cookie, 401 password salah, login OK + cookie
  HttpOnly), 18 (403 gate role), 19 (audit user_id dari session, payload
  `"Capt. Hadi"` tidak pernah tercatat), 20 (rate limit 5×401 lalu 429),
  21 (logout -> 401).
- `tests/test_libsql_backend.py`: lulus — login WSGI, 401 tanpa cookie,
  400 close-ops tanpa SPB, 403 role FINANCE, semua lewat backend libsql.
- E2E Turso remote (`agency-yansen`): login + alur close-ops/finance
  penuh lewat WSGI dengan user tes sementara (dihapus sesudahnya).
