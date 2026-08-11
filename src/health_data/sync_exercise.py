"""Ingests Hevy's weekly workouts_master.json / routines_master.json into SQLite.

Parent rows (routines, workouts) are upserted by their stable Hevy id.
Their children (routine_sets, workout_sets) are fully deleted and
reinserted from the current pull, since Hevy always hands us the complete
nested structure for anything it returns — this is what makes a corrected
workout (e.g. a mislogged exercise) replace the old data instead of
duplicating it.

After all upserts, a reconcile pass removes any workout or routine that
exists in the DB but is no longer present in the Hevy pull (i.e. was
deleted in the app). The reconcile is skipped entirely if the pull is
empty, which guards against a failed/corrupted sync wiping the table.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from health_data.db import connect

DEFAULT_WORKOUTS_PATH = Path(
    os.environ.get(
        "HEVY_WORKOUTS_PATH",
        "data/raw/exercise/workouts_master.json",
    )
)
DEFAULT_ROUTINES_PATH = Path(
    os.environ.get(
        "HEVY_ROUTINES_PATH",
        "data/raw/exercise/routines_master.json",
    )
)


def load_json(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text())


def extract_exercise(ex: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": ex["exercise_template_id"],
        "title": ex.get("title"),
        "muscle_group": ex.get("muscle_group"),
        "equipment_category": ex.get("equipment_category"),
    }


def routine_to_row(routine: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": routine["id"],
        "parent_routine_id": routine.get("parent_routine_id"),
        "title": routine.get("title"),
        "folder_id": routine.get("folder_id"),
        "notes": routine.get("notes"),
        "created_at": routine.get("created_at"),
        "updated_at": routine.get("updated_at"),
    }


def routine_sets_from(routine: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for position, ex in enumerate(routine.get("exercises", [])):
        for s in ex.get("sets", []):
            rep_range = s.get("rep_range") or {}
            rows.append(
                {
                    "routine_id": routine["id"],
                    "exercise_id": ex["exercise_template_id"],
                    "exercise_position": position,
                    "superset_id": ex.get("superset_id"),
                    "set_index": s.get("index"),
                    "set_type": s.get("indicator"),
                    "rep_range_min": rep_range.get("start"),
                    "rep_range_max": rep_range.get("end"),
                    "weight_lbs": s.get("weight_lbs"),
                }
            )
    return rows


def epoch_to_iso(value: Any) -> str | None:
    """Hevy's workout start_time/end_time are Unix epoch seconds, not ISO strings
    (unlike routine created_at/updated_at, which already are ISO) - normalize so
    date-based SQL queries work directly against the stored value."""
    if value is None:
        return None
    return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat()


def workout_to_row(workout: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": workout["id"],
        "routine_id": workout.get("routine_id"),
        "name": workout.get("name"),
        "description": workout.get("description") or None,
        "start_time": epoch_to_iso(workout.get("start_time")),
        "end_time": epoch_to_iso(workout.get("end_time")),
        "calories_burned": (workout.get("biometrics") or {}).get("total_calories"),
        "estimated_volume_lbs": workout.get("estimated_volume_lbs"),
    }


def workout_sets_from(workout: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for ex in workout.get("exercises", []):
        for s in ex.get("sets", []):
            rows.append(
                {
                    "id": s["id"],
                    "workout_id": workout["id"],
                    "exercise_id": ex["exercise_template_id"],
                    "exercise_notes": ex.get("notes") or None,
                    "superset_id": ex.get("superset_id"),
                    "set_index": s.get("index"),
                    "set_type": s.get("indicator"),
                    "reps": s.get("reps"),
                    "weight_lbs": s.get("weight_lbs"),
                    "duration_seconds": s.get("duration_seconds"),
                    "distance_meters": s.get("distance_meters"),
                    "rpe": s.get("rpe"),
                }
            )
    return rows


def collect_exercises(
    workouts: list[dict[str, Any]], routines: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    exercises: dict[str, dict[str, Any]] = {}
    for routine in routines:
        for ex in routine.get("exercises", []):
            exercises[ex["exercise_template_id"]] = extract_exercise(ex)
    for workout in workouts:
        for ex in workout.get("exercises", []):
            exercises[ex["exercise_template_id"]] = extract_exercise(ex)
    return exercises


def upsert_exercise(conn: sqlite3.Connection, ex: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO exercises (id, title, muscle_group, equipment_category)
        VALUES (:id, :title, :muscle_group, :equipment_category)
        ON CONFLICT(id) DO UPDATE SET
            title = excluded.title,
            muscle_group = excluded.muscle_group,
            equipment_category = excluded.equipment_category
        """,
        ex,
    )


def upsert_routine(conn: sqlite3.Connection, routine: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO routines (id, parent_routine_id, title, folder_id, notes, created_at, updated_at)
        VALUES (:id, :parent_routine_id, :title, :folder_id, :notes, :created_at, :updated_at)
        ON CONFLICT(id) DO UPDATE SET
            parent_routine_id = excluded.parent_routine_id,
            title = excluded.title,
            folder_id = excluded.folder_id,
            notes = excluded.notes,
            created_at = excluded.created_at,
            updated_at = excluded.updated_at
        """,
        routine,
    )


def replace_routine_sets(
    conn: sqlite3.Connection, routine_id: str, rows: list[dict[str, Any]]
) -> None:
    conn.execute("DELETE FROM routine_sets WHERE routine_id = ?", (routine_id,))
    conn.executemany(
        """
        INSERT INTO routine_sets
            (routine_id, exercise_id, exercise_position, superset_id, set_index,
             set_type, rep_range_min, rep_range_max, weight_lbs)
        VALUES
            (:routine_id, :exercise_id, :exercise_position, :superset_id, :set_index,
             :set_type, :rep_range_min, :rep_range_max, :weight_lbs)
        """,
        rows,
    )


def upsert_workout(conn: sqlite3.Connection, workout: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO workouts (id, routine_id, name, description, start_time, end_time,
                               calories_burned, estimated_volume_lbs)
        VALUES (:id, :routine_id, :name, :description, :start_time, :end_time,
                :calories_burned, :estimated_volume_lbs)
        ON CONFLICT(id) DO UPDATE SET
            routine_id = excluded.routine_id,
            name = excluded.name,
            description = excluded.description,
            start_time = excluded.start_time,
            end_time = excluded.end_time,
            calories_burned = excluded.calories_burned,
            estimated_volume_lbs = excluded.estimated_volume_lbs
        """,
        workout,
    )


def replace_workout_sets(
    conn: sqlite3.Connection, workout_id: str, rows: list[dict[str, Any]]
) -> None:
    conn.execute("DELETE FROM workout_sets WHERE workout_id = ?", (workout_id,))
    conn.executemany(
        """
        INSERT INTO workout_sets
            (id, workout_id, exercise_id, exercise_notes, superset_id, set_index,
             set_type, reps, weight_lbs, duration_seconds, distance_meters, rpe)
        VALUES
            (:id, :workout_id, :exercise_id, :exercise_notes, :superset_id, :set_index,
             :set_type, :reps, :weight_lbs, :duration_seconds, :distance_meters, :rpe)
        """,
        rows,
    )


def _reconcile_workouts(conn: sqlite3.Connection, hevy_ids: list[str]) -> int:
    placeholders = ",".join("?" * len(hevy_ids))
    conn.execute(f"DELETE FROM workout_sets WHERE workout_id NOT IN ({placeholders})", hevy_ids)
    cur = conn.execute(f"DELETE FROM workouts WHERE id NOT IN ({placeholders})", hevy_ids)
    return cur.rowcount


def _reconcile_routines(conn: sqlite3.Connection, hevy_ids: list[str]) -> int:
    placeholders = ",".join("?" * len(hevy_ids))
    conn.execute(f"DELETE FROM routine_sets WHERE routine_id NOT IN ({placeholders})", hevy_ids)
    cur = conn.execute(f"DELETE FROM routines WHERE id NOT IN ({placeholders})", hevy_ids)
    return cur.rowcount


def ingest(
    conn: sqlite3.Connection,
    workouts: list[dict[str, Any]],
    routines: list[dict[str, Any]],
) -> dict[str, int]:
    with conn:
        for ex in collect_exercises(workouts, routines).values():
            upsert_exercise(conn, ex)

        for routine in routines:
            upsert_routine(conn, routine_to_row(routine))
            replace_routine_sets(conn, routine["id"], routine_sets_from(routine))

        for workout in workouts:
            upsert_workout(conn, workout_to_row(workout))
            replace_workout_sets(conn, workout["id"], workout_sets_from(workout))

        hevy_workout_ids = [w["id"] for w in workouts]
        deleted_workouts = _reconcile_workouts(conn, hevy_workout_ids) if hevy_workout_ids else 0

        hevy_routine_ids = [r["id"] for r in routines]
        deleted_routines = _reconcile_routines(conn, hevy_routine_ids) if hevy_routine_ids else 0

    return {
        "routines": len(routines),
        "workouts": len(workouts),
        "deleted_workouts": deleted_workouts,
        "deleted_routines": deleted_routines,
    }


def main() -> None:
    workouts = load_json(DEFAULT_WORKOUTS_PATH)
    routines = load_json(DEFAULT_ROUTINES_PATH)
    conn = connect()
    try:
        summary = ingest(conn, workouts, routines)
    finally:
        conn.close()
    deleted_msg = (
        f" | deleted: {summary['deleted_workouts']} workouts, {summary['deleted_routines']} routines"
        if summary["deleted_workouts"] or summary["deleted_routines"]
        else ""
    )
    print(f"sync-exercise: {summary['routines']} routines, {summary['workouts']} workouts{deleted_msg}")


if __name__ == "__main__":
    main()
