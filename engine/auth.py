"""Autentikasi pengguna — password hashing + session cookie ter-sign (stdlib).

Desain (ADR-0011, hibrida):
- Identitas user & role di Turso (tabel `users`, lihat engine/db.py).
- Rahasia session di env var SESSION_SECRET — tidak pernah di DB/Git.
- Session = cookie stateless yang di-sign HMAC-SHA256; verifikasi per
  request HANYA di memori (tanpa round trip ke Turso — nyambung dengan
  optimasi cold start ADR-0010). DB hanya disentuh saat login (1x).
- Password di-hash PBKDF2-HMAC-SHA256 (hashlib stdlib, 260.000 iterasi);
  verifikasi selalu timing-safe (hmac.compare_digest).

Keputusan keamanan yang disadari (lihat ADR-0011):
- Cookie stateless: pencabutan user/role baru berlaku setelah cookie
  kedaluwarsa (12 jam) — kecuali SESSION_SECRET di-rotate, yang langsung
  membatalkan SEMUA session.
- Rate limit login best-effort in-memory per instance (serverless bisa
  punya banyak instance); cukup untuk memperlambat brute force, bukan
  pengganti WAF.
"""

import base64
import hashlib
import hmac
import os
import threading
import time

SESSION_COOKIE_NAME = "ship_agency_session"
SESSION_TTL_SECONDS = 12 * 3600  # 12 jam
PBKDF2_ITERATIONS = 260_000
ROLES = ("OPS", "FINANCE", "ADMIN")

LOGIN_MAX_ATTEMPTS = 5          # percobaan gagal per username ...
LOGIN_WINDOW_SECONDS = 15 * 60  # ... dalam 15 menit -> 429

_login_attempts = {}  # username -> [timestamp, ...]
_login_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Session secret
# ---------------------------------------------------------------------------

def get_session_secret():
    """Ambil SESSION_SECRET dari env. Sengaja dibaca tiap panggilan (bukan
    di-cache di import) supaya tes bisa menggantinya lewat os.environ.

    Bila kosong, gagal dengan pesan jelas: aplikasi menolak berjalan tanpa
    rahasia session — session tanpa secret = cookie yang bisa dipalsukan
    siapa pun (fail closed, bukan fail open)."""
    secret = os.environ.get("SESSION_SECRET", "").strip()
    if not secret:
        raise RuntimeError(
            "SESSION_SECRET belum diisi — isi dulu sebelum menjalankan server. "
            "Lokal: export SESSION_SECRET=$(python3 -c \"import os;print(os.urandom(32).hex())\"). "
            "Vercel: Settings -> Environment Variables -> SESSION_SECRET (lalu redeploy)."
        )
    return secret


# ---------------------------------------------------------------------------
# Password hashing (PBKDF2, stdlib)
# ---------------------------------------------------------------------------

def hash_password(password, salt=None):
    """Hash password baru. Format: pbkdf2$260000$salt_hex$hash_hex."""
    if len(password) < 8:
        raise ValueError("Password minimal 8 karakter")
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password, stored_hash):
    """Verifikasi password terhadap hash tersimpan. Selalu timing-safe;
    hash rusak/format lama -> False (bukan exception)."""
    try:
        algo, iterations, salt_hex, hash_hex = stored_hash.split("$")
        if algo != "pbkdf2":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"),
            bytes.fromhex(salt_hex), int(iterations))
        return hmac.compare_digest(digest.hex(), hash_hex)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Session cookie (stateless, HMAC)
# ---------------------------------------------------------------------------

def make_session_cookie(username, role):
    """Buat nilai cookie session untuk user yang baru login.
    Payload: base64url("username|role|expiry_unix") + "." + hmac_hex."""
    expiry = int(time.time()) + SESSION_TTL_SECONDS
    payload = f"{username}|{role}|{expiry}".encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload).decode("ascii")
    sig = hmac.new(get_session_secret().encode("utf-8"),
                   payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


def verify_session_cookie(cookie_header):
    """Verifikasi Cookie header; kembalikan (username, role) atau None.

    Tanpa query DB: signature HMAC + expiry dicek lokal. Bila
    SESSION_SECRET belum diisi, get_session_secret() melempar RuntimeError
    (ditangani pemanggil menjadi 500 dengan pesan jelas)."""
    if not cookie_header:
        return None
    for part in cookie_header.split(";"):
        part = part.strip()
        if not part.startswith(SESSION_COOKIE_NAME + "="):
            continue
        value = part[len(SESSION_COOKIE_NAME) + 1:]
        try:
            payload_b64, sig = value.rsplit(".", 1)
        except ValueError:
            return None
        expected = hmac.new(get_session_secret().encode("utf-8"),
                            payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        try:
            payload = base64.urlsafe_b64decode(payload_b64.encode("ascii")).decode("utf-8")
            username, role, expiry = payload.split("|")
            if role not in ROLES or int(expiry) <= int(time.time()):
                return None
            return username, role
        except Exception:
            return None
    return None


# ---------------------------------------------------------------------------
# Rate limit login (in-memory, per username)
# ---------------------------------------------------------------------------

def is_login_blocked(username):
    """True bila username ini sudah 5x gagal login dalam 15 menit terakhir."""
    now = time.time()
    with _login_lock:
        attempts = [t for t in _login_attempts.get(username, []) if now - t < LOGIN_WINDOW_SECONDS]
        _login_attempts[username] = attempts
        return len(attempts) >= LOGIN_MAX_ATTEMPTS


def record_login_failure(username):
    with _login_lock:
        _login_attempts.setdefault(username, []).append(time.time())


def clear_login_failures(username):
    with _login_lock:
        _login_attempts.pop(username, None)
