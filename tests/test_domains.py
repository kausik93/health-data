import json
import sqlite3

from health_data.db import SCHEMA_PATH


def test_domains_registry_lists_every_real_table():
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA_PATH.read_text())

    real_tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE '\\_%' ESCAPE '\\' AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\'"
        ).fetchall()
    }

    registered_tables = set()
    for domain, tables_json in conn.execute("SELECT domain, tables FROM _domains"):
        registered_tables.update(json.loads(tables_json))

    assert registered_tables == real_tables


def test_domains_registry_upserts_on_reapply():
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA_PATH.read_text())
    conn.executescript(SCHEMA_PATH.read_text())  # applied again, e.g. on every script run

    count = conn.execute("SELECT COUNT(*) FROM _domains").fetchone()[0]
    assert count == 2  # exercise, diet - no duplicate rows from reapplying
