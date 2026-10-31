"""Tes backend libsql (Turso) — mode file lokal.

Menguji permukaan API yang dipakai server.py terhadap koneksi libsql, tanpa
akun Turso: TURSO_DATABASE_URL diarahkan ke file sementara sehingga get_db()
memakai _LibSQLConnection. Yang diuji: row_factory (r["kolom"], r[0],
dict(r)), PRAGMA foreign_keys, `with conn:` commit/rollback, lastrowid,
dan satu alur server lewat adapter WSGI (gate close-ops tetap menolak
tanpa SPB).

Jalankan:  python3 tests/test_libsql_backend.py
Bila paket libsql belum terinstal, tes ini SKIP (exit 0) — tes utama tetap
di tests/test_api.py yang memakai SQLite lokal.
"""
import io
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    import libsql  # noqa: F401
except ImportError:
    print("SKIP: paket 'libsql' belum terinstal (pip install -r requirements.txt)")
    raise SystemExit(0)

TMP = tempfile.mkdtemp(prefix="libsql-backend-")
DB_PATH = os.path.join(TMP, "backend.db")
os.environ["TURSO_DATABASE_URL"] = DB_PATH
os.environ.pop("TURSO_AUTH_TOKEN", None)

from engine.db import get_db, init_db, seed_sample_data  # noqa: E402
import server  # noqa: E402
from wsgiref.util import setup_testing_defaults  # noqa: E402


def _check(name, cond, detail=""):
    print(f"[{'OK' if cond else 'GAGAL'}] {name} {detail}")
    assert cond, name


def main():
    init_db()
    seed_sample_data()
    conn = get_db()
    _check("get_db() memakai libsql saat TURSO_DATABASE_URL terisi",
           type(conn).__name__ == "_LibSQLConnection", f"({type(conn).__name__})")

    # 1. Row: akses by nama, by indeks, dict(r), keys()
    cur = conn.cursor()
    cur.execute("SELECT id, agency, doc_type, status FROM clearances "
                "WHERE port_call_id = 'PC-2026-JKT-0001' ORDER BY id LIMIT 1")
    row = cur.fetchone()
    _check("row['doc_type'] by nama", row["doc_type"].startswith("PKKA"))
    _check("row[0] by indeks", isinstance(row[0], int))
    _check("dict(row)", dict(row)["agency"] == "INAPORTNET_SIMPADU")
    _check("row.keys()", "doc_type" in row.keys())
    _check("seed clearances = 10",
           conn.execute("SELECT COUNT(*) FROM clearances").fetchone()[0] == 10)

    # 2. Foreign key ditegakkan (PRAGMA foreign_keys = ON)
    try:
        conn.execute("INSERT INTO clearances (port_call_id, agency, doc_type, status) "
                     "VALUES ('TIDAK-ADA', 'BEA_CUKAI', 'Tes', 'PENDING')")
        conn.commit()
        _check("FK violation ditolak", False)
    except Exception:
        conn.rollback()
        _check("FK violation ditolak", True)

    # 3. `with conn:` commit & rollback
    with conn:
        conn.execute("INSERT INTO milestones (port_call_id, milestone_code, event_time, recorded_by) "
                     "VALUES ('PC-2026-JKT-0001', 'EOSP', '2026-11-01 07:00', 'tes')")
    n1 = conn.execute("SELECT COUNT(*) FROM milestones").fetchone()[0]
    try:
        with conn:
            conn.execute("INSERT INTO milestones (port_call_id, milestone_code, event_time, recorded_by) "
                         "VALUES ('PC-2026-JKT-0001', 'EOSP', '2026-11-01 07:01', 'tes')")
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    n2 = conn.execute("SELECT COUNT(*) FROM milestones").fetchone()[0]
    _check("with conn: commit + rollback", n1 == n2 and n1 == 1, f"({n1} -> {n2})")

    # 4. lastrowid
    cur = conn.execute("INSERT INTO milestones (port_call_id, milestone_code, event_time, recorded_by) "
                       "VALUES ('PC-2026-JKT-0001', 'ANCHORED', '2026-11-01 08:00', 'tes')")
    _check("lastrowid terisi", cur.lastrowid is not None, f"({cur.lastrowid})")
    conn.commit()
    conn.close()

    # 5. Alur server lewat WSGI dengan backend libsql (gate close-ops aktif)
    def call(method, path, body=None):
        env = {}
        setup_testing_defaults(env)
        env["REQUEST_METHOD"] = method
        env["PATH_INFO"] = path
        env["wsgi.input"] = io.BytesIO(body.encode() if body else b"")
        if body:
            env["CONTENT_LENGTH"] = str(len(body))
            env["CONTENT_TYPE"] = "application/json"
        out = {}

        def start_response(status, headers):
            out["status"] = status
        payload = b"".join(server.application(env, start_response))
        return out["status"], payload

    s, p = call("GET", "/api/port-calls")
    _check("WSGI GET /api/port-calls 200 via libsql", s.startswith("200"),
           f"({s}, port_calls={len(json.loads(p)['port_calls'])})")
    s, p = call("POST", "/api/port-calls/PC-2026-JKT-0001/close-ops",
                json.dumps({"user": "Tester"}))
    _check("WSGI POST close-ops tanpa SPB ditolak 400 via libsql",
           s.startswith("400"), f"({s})")

    print("\nSEMUA TES BACKEND LIBSQL LULUS!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
