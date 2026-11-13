import os

# The website worker looks up company domains over the internet; tests never do.
os.environ.setdefault("HBF_WEBSITE_LOOKUP", "0")

# HBF_TEST_PG=postgresql://... runs the SAME tests against Postgres: every Storage(path) made by a test
# becomes a PostgresStorage in its own fresh schema (one per test database path).
_PG = os.environ.get("HBF_TEST_PG", "")
if _PG:
    import hashlib
    import uuid

    import pytest

    import app.storage
    from app.pgstorage import PostgresStorage

    _RUN = uuid.uuid4().hex[:6]
    _OPEN: dict = {}

    class _PgForTests(PostgresStorage):
        def __init__(self, path: str):
            if path in _OPEN:                       # the same "file" opened again: same data
                self.__dict__ = _OPEN[path].__dict__
                self.init()
                return
            for old in _OPEN.values():              # one test database at a time: free the old pools
                old.pool.close()
            _OPEN.clear()
            schema = "t_" + _RUN + "_" + hashlib.md5(path.encode()).hexdigest()[:10]
            super().__init__(_PG, schema=schema)
            _OPEN[path] = self

    app.storage.Storage = _PgForTests

    def pytest_collection_modifyitems(items):
        # These two check the SQLite file itself (WAL mode, upgrading an old file).
        skip = pytest.mark.skip(reason="reads the SQLite file directly")
        for item in items:
            if item.name in ("test_wal_mode_enabled", "test_website_is_looked_up_once_for_everyone_and_nothing_else_is_saved"):
                item.add_marker(skip)
