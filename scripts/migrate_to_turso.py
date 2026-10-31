#!/usr/bin/env python3
"""Migrasikan isi SQLite lokal (agency.db) ke Turso (libSQL remote), sekali jalan.

Cara pakai (dari root repo):
    pip install -r requirements.txt
    export TURSO_DATABASE_URL="libsql://ship-agency-xxxx.turso.io"
    export TURSO_AUTH_TOKEN="eyJhbGciOi..."
    python3 scripts/migrate_to_turso.py [--force] [--seed]

- Tanpa --force: batal bila database Turso sudah berisi port call (mencegah
  duplikasi). Dengan --force: hapus dulu isi Turso (urutan aman terhadap
  foreign key), lalu salin ulang.
- Tanpa --seed: sumbernya agency.db lokal (harus ada). Dengan --seed: bila
  agency.db lokal tidak ada, isi Turso dengan init_db() + seed_sample_data()
  saja (data contoh).
- Token JANGAN ditulis di skrip ini — selalu lewat environment variable.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

TABLES = [
    "master_ports", "master_terminals", "master_branches", "master_principals",
    "master_vessels", "port_calls", "port_call_charter_parties", "clearances",
    "milestones", "sof_stoppages", "disbursements", "fda_disputes", "audit_logs",
]
# Urutan hapus: anak dulu, induk belakangan (foreign key).
DELETE_ORDER = list(reversed(TABLES))


def _counts(conn):
    out = {}
    for t in TABLES:
        try:
            out[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except Exception:
            out[t] = None
    return out


def main():
    force = "--force" in sys.argv
    seed_only = "--seed" in sys.argv
    url = os.environ.get("TURSO_DATABASE_URL", "").strip()
    token = os.environ.get("TURSO_AUTH_TOKEN", "").strip()
    if not url or not token:
        print("GAGAL: isi dulu TURSO_DATABASE_URL dan TURSO_AUTH_TOKEN di environment.")
        print("Lihat .env.example dan README bagian 'Persistensi Turso'.")
        return 1

    from engine.db import get_db, init_db, seed_sample_data

    # 1. Siapkan skema di Turso (CREATE TABLE IF NOT EXISTS — aman diulang).
    print(f"Target Turso: {url}")
    remote = get_db()
    init_db()
    before = _counts(remote)
    if any((v or 0) > 0 for v in before.values()) and not force:
        print("GAGAL: database Turso sudah berisi data:")
        for t, n in before.items():
            if n:
                print(f"  {t}: {n} baris")
        print("Jalankan lagi dengan --force untuk menimpa, atau pakai database Turso kosong.")
        remote.close()
        return 1
    if force and any((v or 0) > 0 for v in before.values()):
        print("Menghapus isi Turso (--force) ...")
        with remote:
            for t in DELETE_ORDER:
                remote.execute(f"DELETE FROM {t}")

    # 2. Sumber: agency.db lokal, atau seed contoh bila --seed.
    local_path = os.path.join(os.path.dirname(__file__), "..", "agency.db")
    if seed_only and not os.path.exists(local_path):
        print("agency.db lokal tidak ada — mengisi Turso dengan data contoh (--seed) ...")
        seed_sample_data()
    else:
        if not os.path.exists(local_path):
            print(f"GAGAL: {local_path} tidak ada. Jalankan dulu 'python3 server.py' "
                  "sekali di lokal, atau pakai --seed.")
            remote.close()
            return 1
        print(f"Sumber lokal: {local_path}")
        local = sqlite3.connect(local_path)
        dump = list(local.iterdump())
        local.close()
        inserts = [s for s in dump
                   if s.startswith("INSERT INTO")
                   and "sqlite_sequence" not in s]
        # iterdump TIDAK mengurutkan berdasarkan foreign key (clearances bisa
        # muncul sebelum port_calls) — urutkan sendiri mengikuti TABLES.
        import re
        def _table_of(stmt):
            m = re.match(r'INSERT INTO "?(\w+)"?', stmt)
            return m.group(1) if m else ""
        inserts.sort(key=lambda s: TABLES.index(_table_of(s))
                     if _table_of(s) in TABLES else len(TABLES))
        print(f"Menyalin {len(inserts)} baris INSERT (diurutkan per tabel) ...")
        with remote:
            for stmt in inserts:
                remote.execute(stmt)

    # 3. Verifikasi: bandingkan jumlah baris per tabel.
    after = _counts(remote)
    print("Verifikasi jumlah baris di Turso:")
    ok = True
    for t in TABLES:
        print(f"  {t}: {after[t]}")
        if after[t] is None:
            ok = False
    remote.close()
    if not ok:
        print("PERINGATAN: ada tabel yang tidak bisa dibaca — periksa manual.")
        return 1
    print("SELESAI: migrasi ke Turso berhasil. Uji dengan TURSO_* aktif lalu "
          "jalankan 'python3 server.py' dan buka http://localhost:8000")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
