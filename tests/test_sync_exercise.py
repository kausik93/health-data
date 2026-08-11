import json
import sqlite3
from pathlib import Path

import pytest

from health_data.db import SCHEMA_PATH
from health_data.sync_exercise import (
    collect_exercises,
    ingest,
    routine_sets_from,
    routine_to_row,
    workout_sets_from,
    workout_to_row,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def routines():
    return json.loads((FIXTURES / "sample_routines.json").read_text())


@pytest.fixture
def workouts():
    return json.loads((FIXTURES / "sample_workouts.json").read_text())


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.execute("PRAGMA foreign_keys = ON")
    c.executescript(SCHEMA_PATH.read_text())
    yield c
    c.close()


def test_collect_exercises_dedupes_across_routines_and_workouts(routines, workouts):
    exercises = collect_exercises(workouts, routines)
    assert exercises["55E6546F"]["title"] == "Incline Bench Press (Barbell)"
    assert exercises["TREADMILL01"]["muscle_group"] == "cardio"
    assert exercises["CYCLING01"]["muscle_group"] == "cardio"


def test_routine_to_row_carries_version_chain(routines):
    row = routine_to_row(routines[0])
    assert row["id"] == "routine-push-day-2"
    assert row["parent_routine_id"] == "routine-upper-day-2-old"


def test_routine_sets_from_expands_every_set(routines):
    rows = routine_sets_from(routines[0])
    # 2 sets on exercise 0, 1 set on exercise 1 (superset)
    assert len(rows) == 3
    assert rows[0]["exercise_id"] == "55E6546F"
    assert rows[0]["rep_range_min"] == 8
    assert rows[0]["rep_range_max"] == 10
    assert rows[2]["exercise_id"] == "88AA1122"
    assert rows[2]["superset_id"] == 1


def test_workout_to_row_pulls_calories_from_biometrics(workouts):
    row = workout_to_row(workouts[0])
    assert row["calories_burned"] == 568.49
    assert row["routine_id"] == "routine-push-day-2"


def test_workout_to_row_converts_epoch_start_time_to_queryable_iso_date(workouts):
    # Hevy's start_time/end_time are Unix epoch seconds, not ISO strings -
    # this must convert so a plain date-prefix SQL query still works.
    row = workout_to_row(workouts[1])
    assert row["start_time"].startswith("2026-08-04")


def test_workout_sets_from_preserves_varying_reps_per_set(workouts):
    rows = workout_sets_from(workouts[0])
    assert [r["reps"] for r in rows] == [10, 9, 8]
    assert rows[0]["id"] == "set-1001"


def test_ingest_is_idempotent(conn, workouts, routines):
    ingest(conn, workouts, routines)
    counts_first = _table_counts(conn)

    ingest(conn, workouts, routines)
    counts_second = _table_counts(conn)

    assert counts_first == counts_second
    assert counts_first["workout_sets"] == 5  # 3 + 1 + 1 across both workouts


def test_ingest_replaces_workout_sets_when_a_workout_is_corrected(conn, workouts):
    single_exercise_treadmill = {
        **workouts[1],
        "exercises": [workouts[1]["exercises"][0]],  # only Treadmill, no Cycling
    }
    ingest(conn, [single_exercise_treadmill], [])
    rows = conn.execute(
        "SELECT exercise_id FROM workout_sets WHERE workout_id = ?",
        (single_exercise_treadmill["id"],),
    ).fetchall()
    assert [r[0] for r in rows] == ["TREADMILL01"]

    # corrected pull now includes the Cycling segment too
    ingest(conn, [workouts[1]], [])
    rows = conn.execute(
        "SELECT exercise_id FROM workout_sets WHERE workout_id = ? ORDER BY exercise_id",
        (workouts[1]["id"],),
    ).fetchall()
    assert [r[0] for r in rows] == ["CYCLING01", "TREADMILL01"]


def _table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    tables = ["exercises", "routines", "routine_sets", "workouts", "workout_sets"]
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}


def test_ingest_removes_workouts_deleted_in_hevy(conn, workouts, routines):
    ingest(conn, workouts, routines)
    assert conn.execute("SELECT COUNT(*) FROM workouts").fetchone()[0] == 2

    # Second sync: only first workout remains in Hevy (second was deleted)
    ingest(conn, [workouts[0]], routines)
    assert conn.execute("SELECT COUNT(*) FROM workouts").fetchone()[0] == 1
    ids = [r[0] for r in conn.execute("SELECT id FROM workouts").fetchall()]
    assert ids == [workouts[0]["id"]]
    # Sets for the deleted workout must also be gone
    sets = conn.execute(
        "SELECT COUNT(*) FROM workout_sets WHERE workout_id = ?", (workouts[1]["id"],)
    ).fetchone()[0]
    assert sets == 0


def test_ingest_reconcile_skipped_when_pull_is_empty(conn, workouts, routines):
    ingest(conn, workouts, routines)
    count_before = conn.execute("SELECT COUNT(*) FROM workouts").fetchone()[0]

    # Empty pull (e.g. failed sync) — guard must prevent wiping the table
    ingest(conn, [], routines)
    count_after = conn.execute("SELECT COUNT(*) FROM workouts").fetchone()[0]
    assert count_after == count_before
