#!/usr/bin/env python3
"""Buat user aplikasi (ADR-0011).

User disimpan di tabel `users` (Turso bila TURSO_* terisi, SQLite lokal bila
tidak). Password di-hash PBKDF2, tidak pernah disimpan plaintext. Tidak ada
pendaftaran publik di UI — akun hanya dibuat lewat script ini oleh admin.

Pemakaian:
    python3 scripts/create_user.py ops_jakarta --full-name "Ops Jakarta" --role OPS
    python3 scripts/create_user.py fin_jakarta --full-name "Finance Jakarta" --role FINANCE
    python3 scripts/create_user.py admin --full-name "Administrator" --role ADMIN --password 'RahasiaKuat123'
    python3 scripts/create_user.py ops_jakarta --update   # reset password user yang sudah ada

Bila --password tidak diisi, password diminta dua kali lewat getpass
(tidak tampil di layar, tidak masuk riwayat shell).
"""
import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.db import get_db, init_db, create_user  # noqa: E402
from engine.auth import ROLES  # noqa: E402


def _read_password_twice():
    while True:
        pw = getpass.getpass("Password (min 8 karakter): ")
        pw2 = getpass.getpass("Ulangi password: ")
        if pw != pw2:
            print("Password tidak sama, coba lagi.")
            continue
        if len(pw) < 8:
            print("Password minimal 8 karakter.")
            continue
        return pw


def main():
    ap = argparse.ArgumentParser(description="Buat user aplikasi Sistem Keagenan Kapal")
    ap.add_argument("username", help="Username login (unik)")
    ap.add_argument("--full-name", required=True, help="Nama lengkap (tampil di UI/audit)")
    ap.add_argument("--role", required=True, choices=ROLES,
                    help="OPS = operasional (close-ops), FINANCE = keuangan (close-finance), "
                         "ADMIN = keduanya")
    ap.add_argument("--password", help="Password (bila kosong, diminta lewat getpass)")
    ap.add_argument("--update", action="store_true",
                    help="Bila user sudah ada, reset password/nama/role-nya")
    args = ap.parse_args()

    password = args.password or _read_password_twice()
    init_db()
    conn = get_db()
    try:
        exists = conn.execute(
            "SELECT 1 FROM users WHERE username = ?", (args.username,)).fetchone()
        if exists and not args.update:
            print(f"User '{args.username}' sudah ada. Pakai --update untuk meresetnya.")
            return 1
        if exists:
            from engine.auth import hash_password
            with conn:
                conn.execute(
                    "UPDATE users SET password_hash = ?, full_name = ?, role = ? "
                    "WHERE username = ?",
                    (hash_password(password), args.full_name, args.role, args.username))
            print(f"User '{args.username}' diperbarui (role {args.role}).")
        else:
            create_user(conn, args.username, password, args.full_name, args.role)
            print(f"User '{args.username}' dibuat (role {args.role}).")
    finally:
        conn.close()

    print("Langkah berikut: pastikan SESSION_SECRET terisi (lokal: export, "
          "Vercel: Environment Variables), lalu login di halaman utama.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
