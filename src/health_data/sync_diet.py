"""Ingests MyNetDiary's weekly mynetdiary_{year}_food.csv snapshots into SQLite.

MyNetDiary's "Food ID" is a catalog id reused every time the same food is
logged again, not a stable per-log-entry id, so food_log can't be upserted
row-by-row. Instead each year's CSV (always a full annual snapshot) fully
replaces that year's food_log rows. diet_daily is then recomputed from
whatever is in food_log and upserted by date, so any manually-added notes on
diet_daily survive the recompute untouched.
"""

import csv
import logging
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from health_data.db import connect

logger = logging.getLogger(__name__)

DEFAULT_RAW_DIR = Path(
    os.environ.get(
        "MYNETDIARY_RAW_DIR",
        "data/raw/diet",
    )
)

FOOD_CSV_RE = re.compile(r"mynetdiary_(\d{4})_food\.csv$")

FIELD_MAP = {
    "calories": "Calories, cals",
    "protein_g": "Protein, g",
    "carbs_g": "Total Carbs, g",
    "fat_g": "Total Fat, g",
    "fiber_g": "Dietary Fiber, g",
    "sodium_mg": "Sodium, mg",
    "sugar_g": "Total Sugars, g",
}

VALID_MEALS = {"Breakfast", "Lunch", "Dinner", "Snacks"}


def find_food_csvs(raw_dir: Path) -> list[tuple[int, Path]]:
    found = []
    for path in raw_dir.glob("mynetdiary_*_food.csv"):
        m = FOOD_CSV_RE.search(path.name)
        if m:
            found.append((int(m.group(1)), path))
    return sorted(found)


def _float(value: str) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def parse_food_row(row: dict[str, str]) -> dict[str, Any]:
    date_time = row["Date & Time"]
    meal = row["Meal"]
    if meal not in VALID_MEALS:
        raise ValueError(f"unexpected meal value: {meal!r}")
    parsed = {
        "food_id": row["Food ID"],
        "date": date_time[:10],
        "meal_time": date_time,
        "meal": meal,
        "name": row["Name"],
        "amount": row["Amount"],
        "notes": None,
    }
    for field, column in FIELD_MAP.items():
        parsed[field] = _float(row.get(column, ""))
    return parsed


def load_food_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as f:
        return [parse_food_row(row) for row in csv.DictReader(f)]


def replace_food_log_year(
    conn: sqlite3.Connection, year: int, rows: list[dict[str, Any]]
) -> None:
    conn.execute("DELETE FROM food_log WHERE date LIKE ?", (f"{year}-%",))
    conn.executemany(
        """
        INSERT INTO food_log
            (food_id, date, meal_time, meal, name, amount,
             calories, protein_g, carbs_g, fat_g, fiber_g, sodium_mg, sugar_g, notes)
        VALUES
            (:food_id, :date, :meal_time, :meal, :name, :amount,
             :calories, :protein_g, :carbs_g, :fat_g, :fiber_g, :sodium_mg, :sugar_g, :notes)
        """,
        rows,
    )


def recompute_diet_daily(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO diet_daily (date, calories, protein_g, carbs_g, fat_g, fiber_g)
        SELECT date, SUM(calories), SUM(protein_g), SUM(carbs_g), SUM(fat_g), SUM(fiber_g)
        FROM food_log
        GROUP BY date
        ON CONFLICT(date) DO UPDATE SET
            calories = excluded.calories,
            protein_g = excluded.protein_g,
            carbs_g = excluded.carbs_g,
            fat_g = excluded.fat_g,
            fiber_g = excluded.fiber_g
        """
    )


def ingest(conn: sqlite3.Connection, year_rows: list[tuple[int, list[dict[str, Any]]]]) -> dict[str, int]:
    skipped = []
    with conn:
        for year, rows in year_rows:
            if not rows:
                # Guard: never delete a year's data when the CSV came back empty —
                # a failed/truncated export would wipe the table and lose all history.
                logger.warning("sync-diet: skipping %d — CSV produced 0 rows (possible failed export)", year)
                skipped.append(year)
                continue
            replace_food_log_year(conn, year, rows)
        recompute_diet_daily(conn)
    ingested = [(y, r) for y, r in year_rows if y not in skipped]
    return {
        "years": len(ingested),
        "rows": sum(len(rows) for _, rows in ingested),
        "skipped_years": skipped,
    }


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    csvs = find_food_csvs(DEFAULT_RAW_DIR)
    year_rows = [(year, load_food_csv(path)) for year, path in csvs]
    conn = connect()
    try:
        summary = ingest(conn, year_rows)
    finally:
        conn.close()
    skipped_msg = f" | SKIPPED years (empty CSV): {summary['skipped_years']}" if summary["skipped_years"] else ""
    print(f"sync-diet: {summary['years']} year(s), {summary['rows']} food_log rows{skipped_msg}")


if __name__ == "__main__":
    main()
