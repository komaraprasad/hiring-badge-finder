"""Postgres support without a database: storage.py's SQLite SQL must become valid Postgres SQL.

The full suite also runs against a real Postgres when HBF_TEST_PG=postgresql://... is set
(see conftest.py); these checks run always, so a new statement that the translation cannot
handle is caught even without a database.
"""
import re

from app.pgstorage import translate
from app.storage import SCHEMA


def test_placeholders_and_like():
    assert translate("SELECT * FROM people WHERE id=? AND name LIKE ?") == \
        "SELECT * FROM people WHERE id=%s AND name ILIKE %s"


def test_like_escape_backslash_is_kept():
    out = translate(r"SELECT 1 FROM people WHERE name LIKE ? ESCAPE '\'")
    assert out.endswith(r"ESCAPE '\'")


def test_literal_percent_is_escaped():
    assert translate("SELECT '50%' FROM jobs WHERE job_id=?") == "SELECT '50%%' FROM jobs WHERE job_id=%s"


def test_insert_or_ignore():
    assert translate("INSERT OR IGNORE INTO about_claims(job_id, url) VALUES(?,?)") == \
        "INSERT INTO about_claims(job_id, url) VALUES(%s,%s) ON CONFLICT DO NOTHING"


def test_replace_into_settings():
    assert translate("REPLACE INTO settings(key, value) VALUES('daily_cap', ?)") == \
        "INSERT INTO settings(key, value) VALUES('daily_cap', %s) ON CONFLICT(key) DO UPDATE SET value=excluded.value"


def test_upsert_counter_names_the_table():
    out = translate("INSERT INTO reads_by_day(browser_id, day, count) VALUES(?,?,?) "
                    "ON CONFLICT(browser_id, day) DO UPDATE SET count=count+excluded.count")
    assert out.endswith("DO UPDATE SET count=reads_by_day.count+excluded.count")


def test_upsert_with_excluded_value_is_unchanged():
    out = translate("INSERT INTO detail_claims(job_id, person_id, claimed) VALUES(?,?,?) "
                    "ON CONFLICT(job_id, person_id) DO UPDATE SET claimed=excluded.claimed")
    assert out.endswith("DO UPDATE SET claimed=excluded.claimed")


def test_people_insert_returns_id():
    assert translate("INSERT INTO people(name) VALUES(?)").endswith("RETURNING id")


def test_pragma_table_info():
    out = translate("PRAGMA table_info(jobs)")
    assert "information_schema.columns" in out and "'jobs'" in out


def test_schema_splits_into_whole_statements():
    """Postgres runs SCHEMA one statement at a time, split at every ';' (comments included).
    A ';' inside a comment cut a CREATE TABLE in two and every database call failed (HTTP 500)."""
    for stmt in (s for s in SCHEMA.split(";") if s.strip()):
        code = "\n".join(line.split("--")[0] for line in stmt.splitlines()).strip()
        assert code.upper().startswith(("CREATE TABLE", "CREATE INDEX")), stmt[:120]
        assert code.count("(") == code.count(")"), stmt[:120]


def test_schema_translates_without_sqlite_only_words():
    for stmt in (s for s in SCHEMA.split(";") if s.strip()):
        out = translate(stmt)
        assert "AUTOINCREMENT" not in out
        assert not re.search(r"\bINSERT\s+OR\b|\bREPLACE\s+INTO\b|\?", out), out
