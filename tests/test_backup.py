import sqlite3

from health_data.backup import backup, prune


def _make_db(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()


def test_backup_returns_none_when_no_db_yet(tmp_path):
    result = backup(tmp_path / "missing.db", tmp_path / "backups")
    assert result is None


def test_backup_creates_a_restorable_copy(tmp_path):
    db_path = tmp_path / "health.db"
    _make_db(db_path)

    dest = backup(db_path, tmp_path / "backups")

    assert dest.exists()
    restored = sqlite3.connect(dest)
    assert restored.execute("SELECT x FROM t").fetchone() == (1,)


def test_prune_keeps_only_the_most_recent_n(tmp_path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    names = [f"health_2026080{i}T000000Z.db" for i in range(1, 6)]
    for name in names:
        (backup_dir / name).write_text("x")

    prune(backup_dir, retention=2)

    remaining = sorted(p.name for p in backup_dir.glob("health_*.db"))
    assert remaining == names[-2:]
