"""Postgres storage (e.g. a free Neon database), chosen when DATABASE_URL starts with postgres://.

It is the SAME Storage class: every method and every rule is shared. Only the plumbing differs:
a connection pool instead of one SQLite file, and the few SQLite-only bits of SQL are translated
here (placeholders, INSERT OR IGNORE, LIKE case, rowid, AUTOINCREMENT, PRAGMA table_info).

Writes are serialized with one transaction-level advisory lock, exactly like SQLite's
BEGIN IMMEDIATE, so the counting rules (daily limits, 5 parallel searches) stay exact.
"""
from __future__ import annotations

import re
import time
import weakref
from contextlib import contextmanager

from .storage import Storage

_WRITE_LOCK = 774411          # any fixed number: the one advisory lock all writes share


class Row(dict):
    """Rows readable by name (r["id"]), by position (r[0]) and as dict(r), like sqlite3.Row."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


def _row_factory(cursor):
    names = [d.name for d in cursor.description] if cursor.description else []
    return lambda values: Row(zip(names, values))


_PRAGMA_RE = re.compile(r"^\s*PRAGMA\s+table_info\((\w+)\)\s*$", re.IGNORECASE)
_INSERT_PEOPLE_RE = re.compile(r"^\s*INSERT\s+INTO\s+people\s*\(", re.IGNORECASE)


def translate(sql: str) -> str:
    """SQLite SQL as written in storage.py -> the same statement for Postgres."""
    m = _PRAGMA_RE.match(sql)
    if m:
        return ("SELECT ordinal_position, column_name FROM information_schema.columns"
                f" WHERE table_schema = current_schema() AND table_name = '{m.group(1)}'"
                " ORDER BY ordinal_position")
    out = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
    out = out.replace(", rowid DESC", ", job_id DESC")
    if re.search(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", out, re.IGNORECASE):
        out = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", out, flags=re.IGNORECASE)
        out = out.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    # REPLACE INTO settings(key, value) ...: settings is the only table it is used on (key = primary key).
    m = re.match(r"\s*REPLACE\s+INTO\s+settings\s*\(", out, re.IGNORECASE)
    if m:
        out = re.sub(r"^\s*REPLACE\s+INTO", "INSERT INTO", out, flags=re.IGNORECASE)
        out = out.rstrip().rstrip(";") + " ON CONFLICT(key) DO UPDATE SET value=excluded.value"
    # "DO UPDATE SET count=count+1": Postgres needs the table named on the right-hand side.
    m = re.match(r"\s*INSERT\s+INTO\s+(\w+)", out, re.IGNORECASE)
    if m and "DO UPDATE SET" in out:
        table = m.group(1)
        head, tail = out.split("DO UPDATE SET", 1)
        tail = re.sub(r"\b(\w+)\s*=\s*\1\b(?!\s*\()", lambda x: f"{x.group(1)}={table}.{x.group(1)}", tail)
        out = head + "DO UPDATE SET" + tail
    out = re.sub(r"\bLIKE\b", "ILIKE", out)            # SQLite's LIKE ignores case
    out = out.replace("%", "%%").replace("?", "%s")    # literal % kept, ? placeholders -> %s
    if _INSERT_PEOPLE_RE.match(out):
        out = out.rstrip().rstrip(";") + " RETURNING id"
    return out


def _params(params):
    return tuple(int(p) if isinstance(p, bool) else p for p in (params or ()))


class _Cursor:
    """psycopg cursor + sqlite's `lastrowid` (filled by INSERT ... RETURNING id)."""

    def __init__(self, cur, lastrowid=None):
        self._cur, self.lastrowid = cur, lastrowid

    def __getattr__(self, name):
        return getattr(self._cur, name)

    def __iter__(self):
        return iter(self._cur)


class _Conn:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        q = translate(sql)
        cur = self._conn.execute(q, _params(params))
        if q.endswith("RETURNING id"):
            row = cur.fetchone()
            return _Cursor(cur, row[0] if row else None)
        return _Cursor(cur)

    def executescript(self, script):
        for stmt in script.split(";"):
            if stmt.strip() and not all(l.strip().startswith("--") or not l.strip() for l in stmt.splitlines()):
                self.execute(stmt)

    def close(self):
        pass                                           # the pool owns the connection


class PostgresStorage(Storage):
    def __init__(self, url: str, schema: str | None = None):
        from psycopg_pool import ConnectionPool        # imported only when Postgres is used

        self.path = url
        kwargs = {"row_factory": _row_factory, "prepare_threshold": None}   # None: works behind PgBouncer
        if schema:
            kwargs["options"] = f"-c search_path={schema}"
        # A ping before EVERY use cost one extra round trip per call (the database may be far away).
        # Only a connection that sat unused for 30 s is pinged first (a free database closes idle ones);
        # one in steady use during a run is trusted. Idle connections are replaced after 2 minutes.
        last_used = weakref.WeakKeyDictionary()

        def check(conn):
            if time.monotonic() - last_used.get(conn, 0) > 30:
                ConnectionPool.check_connection(conn)

        def mark(conn):
            last_used[conn] = time.monotonic()

        self.pool = ConnectionPool(url, min_size=2, max_size=10, kwargs=kwargs, open=True, check=check,
                                   reset=mark, max_idle=120, max_lifetime=1800, timeout=30)
        if schema:
            with self.pool.connection() as conn:
                conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        self.init()

    @contextmanager
    def _tx(self):
        with self.pool.connection() as conn:           # commits on success, rolls back on error
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (_WRITE_LOCK,))
            yield _Conn(conn)

    @contextmanager
    def _read(self):
        with self.pool.connection() as conn:
            yield _Conn(conn)

    def init(self) -> None:
        # The shared init() opens self._connect(); give it a pooled, locked connection instead.
        with self._tx() as c:
            self._connect = lambda: c                 # noqa: E731 - used only during init()
            try:
                Storage.init(self)
            finally:
                del self._connect

    def close(self) -> None:
        self.pool.close()
