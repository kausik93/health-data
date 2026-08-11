import sqlite3
from pathlib import Path

import pytest

from health_data.db import SCHEMA_PATH
from health_data.sync_diet import ingest, load_food_csv, parse_food_row

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def food_rows():
    return load_food_csv(FIXTURES / "sample_food.csv")


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.execute("PRAGMA foreign_keys = ON")
    c.executescript(SCHEMA_PATH.read_text())
    yield c
    c.close()


def test_parse_food_row_maps_named_macro_columns():
    row = parse_food_row(
        {
            "Food ID": "1",
            "Date & Time": "2026-08-03 18:00:00",
            "Meal": "Dinner",
            "Name": "Salt",
            "Amount": "0.375 tsp",
            "Calories, cals": "0.0",
            "Total Fat, g": "0.0",
            "Total Carbs, g": "0.0",
            "Dietary Fiber, g": "0.0",
            "Total Sugars, g": "0.0",
            "Protein, g": "0.0",
            "Sodium, mg": "435.4",
        }
    )
    assert row["date"] == "2026-08-03"
    assert row["meal_time"] == "2026-08-03 18:00:00"
    assert row["sodium_mg"] == 435.4


def test_parse_food_row_rejects_unknown_meal():
    with pytest.raises(ValueError):
        parse_food_row(
            {
                "Food ID": "1",
                "Date & Time": "2026-08-03 18:00:00",
                "Meal": "Brunch",
                "Name": "Salt",
                "Amount": "1",
            }
        )


def test_dinner_items_share_the_grouping_timestamp(food_rows):
    dinner = [r for r in food_rows if r["meal"] == "Dinner"]
    assert len(dinner) == 3
    assert len({r["meal_time"] for r in dinner}) == 1


def test_ingest_rolls_up_diet_daily(conn, food_rows):
    ingest(conn, [(2026, food_rows)])
    row = conn.execute(
        "SELECT calories, protein_g FROM diet_daily WHERE date = '2026-08-03'"
    ).fetchone()
    # yogurt (100.0) + vegetable (30.0) + protein (90.0) + seasoning (0.0)
    assert row[0] == pytest.approx(100.0 + 30.0 + 90.0 + 0.0)
    assert row[1] == pytest.approx(20.0 + 1.0 + 15.0 + 0.0)


def test_ingest_preserves_manual_notes_on_diet_daily(conn, food_rows):
    ingest(conn, [(2026, food_rows)])
    conn.execute(
        "UPDATE diet_daily SET notes = 'felt great' WHERE date = '2026-08-03'"
    )
    conn.commit()

    ingest(conn, [(2026, food_rows)])  # simulate the next weekly re-run

    note = conn.execute(
        "SELECT notes FROM diet_daily WHERE date = '2026-08-03'"
    ).fetchone()[0]
    assert note == "felt great"


def test_ingest_replaces_only_the_reingested_year(conn, food_rows):
    ingest(conn, [(2026, food_rows)])
    conn.execute(
        """
        INSERT INTO food_log (food_id, date, meal_time, meal, name, amount, calories)
        VALUES ('x', '2025-01-01', '2025-01-01 08:00:00', 'Breakfast', 'Old item', '1', 100)
        """
    )
    conn.commit()

    ingest(conn, [(2026, food_rows)])  # re-run 2026 only, 2025 row must survive

    count_2025 = conn.execute(
        "SELECT COUNT(*) FROM food_log WHERE date LIKE '2025-%'"
    ).fetchone()[0]
    assert count_2025 == 1
