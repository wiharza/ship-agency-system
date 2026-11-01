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
os.environ["SESSION_SECRET"] = "test-secret-lokal-jangan-dipakai-di-produksi"

from engine.db import get_db, init_db, seed_sample_data, create_user  # noqa: E402
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

    # 5. Alur server lewat WSGI dengan backend libsql (auth + gate close-ops)
    def call(method, path, body=None, cookie=None):
        env = {}
        setup_testing_defaults(env)
        env["REQUEST_METHOD"] = method
        env["PATH_INFO"] = path
        env["wsgi.input"] = io.BytesIO(body.encode() if body else b"")
        if body:
            env["CONTENT_LENGTH"] = str(len(body))
            env["CONTENT_TYPE"] = "application/json"
        if cookie:
            env["HTTP_COOKIE"] = cookie
        out = {}

        def start_response(status, headers):
            out["status"] = status
            out["headers"] = dict(headers)
        payload = b"".join(server.application(env, start_response))
        return out["status"], payload, out.get("headers", {})

    def login(username, password):
        s, p, h = call("POST", "/api/login",
                       json.dumps({"username": username, "password": password}))
        _check(f"WSGI login {username} 200", s.startswith("200"), f"({s})")
        cookie = (h.get("Set-Cookie") or "").split(";")[0]
        assert cookie.startswith("ship_agency_session="), "Cookie session harus ter-set"
        return cookie

    create_conn = get_db()
    create_user(create_conn, "ops_libsql", "ops-pass-123", "Ops Libsql", "OPS")
    create_user(create_conn, "fin_libsql", "fin-pass-123", "Fin Libsql", "FINANCE")
    create_conn.close()
    ops_cookie = login("ops_libsql", "ops-pass-123")
    fin_cookie = login("fin_libsql", "fin-pass-123")

    s, p, _ = call("GET", "/api/port-calls")
    _check("WSGI GET /api/port-calls tanpa cookie ditolak 401",
           s.startswith("401"), f"({s})")
    s, p, _ = call("GET", "/api/port-calls", cookie=ops_cookie)
    _check("WSGI GET /api/port-calls 200 via libsql", s.startswith("200"),
           f"({s}, port_calls={len(json.loads(p)['port_calls'])})")
    s, p, _ = call("POST", "/api/port-calls/PC-2026-JKT-0001/close-ops",
                   json.dumps({"user": "Tester"}), cookie=ops_cookie)
    _check("WSGI POST close-ops tanpa SPB ditolak 400 via libsql",
           s.startswith("400"), f"({s})")
    s, p, _ = call("POST", "/api/port-calls/PC-2026-JKT-0001/close-ops",
                   json.dumps({"user": "Tester"}), cookie=fin_cookie)
    _check("WSGI POST close-ops role FINANCE ditolak 403 via libsql",
           s.startswith("403"), f"({s})")

    # 6. Cache koneksi remote (hanya bila URL remote + token tersedia di env).
    #    Mode file lokal di atas sengaja TIDAK di-cache, jadi bagian ini butuh
    #    URL remote sungguhan; tanpa itu, SKIP (bukan gagal).
    remote_url = os.environ.get("TURSO_REMOTE_TEST_URL", "")
    remote_token = os.environ.get("TURSO_REMOTE_TEST_TOKEN", "")
    if remote_url and remote_token:
        from engine.db import drop_remote_connection  # noqa
        os.environ["TURSO_DATABASE_URL"] = remote_url
        os.environ["TURSO_AUTH_TOKEN"] = remote_token
        drop_remote_connection()
        c1 = get_db()
        c2 = get_db()
        _check("get_db() remote dipakai ulang (objek sama)", c1 is c2)
        c1.close()  # no-op untuk koneksi shared
        _check("close() tidak memutus koneksi cache",
               get_db().execute("SELECT 1").fetchone()[0] == 1)
        drop_remote_connection()
        c3 = get_db()
        _check("drop_remote_connection() memaksa koneksi baru", c3 is not c1)
        _check("koneksi baru bisa query",
               c3.execute("SELECT COUNT(*) FROM port_calls").fetchone()[0] >= 1)
        drop_remote_connection()
    else:
        print("SKIP tes cache remote: set TURSO_REMOTE_TEST_URL + "
              "TURSO_REMOTE_TEST_TOKEN untuk mengujinya")

    print("\nSEMUA TES BACKEND LIBSQL LULUS!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
