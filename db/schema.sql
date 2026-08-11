-- Self-describing registry: what domains and tables this database holds, so
-- something querying generically (an MCP tool, a fresh Claude session) can
-- discover the schema without it being hardcoded elsewhere. Kept in sync with
-- the tables below by hand, in the same commit whenever either changes.
CREATE TABLE IF NOT EXISTS _domains (
    domain      TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    tables      TEXT NOT NULL   -- JSON array of table names belonging to this domain
);

INSERT INTO _domains (domain, description, tables) VALUES
    ('exercise', 'Workouts and routines synced from Hevy',
     '["exercises","routines","routine_sets","workouts","workout_sets"]'),
    ('diet', 'Food and nutrition log synced from MyNetDiary',
     '["food_log","diet_daily"]')
ON CONFLICT(domain) DO UPDATE SET
    description = excluded.description,
    tables = excluded.tables;

-- Exercise domain (source: Hevy export JSON, data/raw/exercise/*_master.json)

CREATE TABLE IF NOT EXISTS exercises (
    id                  TEXT PRIMARY KEY,   -- Hevy exercise_template_id
    title               TEXT NOT NULL,
    muscle_group        TEXT,
    equipment_category  TEXT
);

CREATE TABLE IF NOT EXISTS routines (
    id                  TEXT PRIMARY KEY,   -- Hevy routine UUID
    parent_routine_id   TEXT,   -- self-referential version chain; not FK-enforced since an
                                 -- ancestor could in principle age out of Hevy's own export
    title               TEXT NOT NULL,
    folder_id           TEXT,
    notes               TEXT,
    created_at          TEXT,
    updated_at          TEXT
);

CREATE TABLE IF NOT EXISTS routine_sets (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    routine_id              TEXT NOT NULL REFERENCES routines(id),
    exercise_id             TEXT NOT NULL REFERENCES exercises(id),
    exercise_position       INTEGER NOT NULL,  -- exercise's order within the routine
    superset_id             INTEGER,
    set_index               INTEGER NOT NULL,
    set_type                TEXT,     -- Hevy's "indicator": normal, warmup, dropset, ...
    rep_range_min           INTEGER,
    rep_range_max           INTEGER,
    weight_lbs              REAL,
    UNIQUE (routine_id, exercise_position, set_index)
);

CREATE TABLE IF NOT EXISTS workouts (
    id                      TEXT PRIMARY KEY,  -- Hevy workout UUID
    routine_id              TEXT REFERENCES routines(id),
    name                    TEXT,
    description             TEXT,
    start_time              TEXT,
    end_time                TEXT,
    calories_burned         REAL,
    estimated_volume_lbs    REAL
);

CREATE TABLE IF NOT EXISTS workout_sets (
    id                  TEXT PRIMARY KEY,      -- Hevy's own set id, stable across pulls
    workout_id          TEXT NOT NULL REFERENCES workouts(id),
    exercise_id         TEXT NOT NULL REFERENCES exercises(id),
    exercise_notes      TEXT,
    superset_id         INTEGER,
    set_index           INTEGER NOT NULL,
    set_type            TEXT,     -- Hevy's "indicator": normal, warmup, dropset, ...
    reps                INTEGER,
    weight_lbs          REAL,
    duration_seconds    REAL,
    distance_meters     REAL,
    rpe                 REAL
);

CREATE INDEX IF NOT EXISTS idx_workout_sets_workout_id ON workout_sets(workout_id);
CREATE INDEX IF NOT EXISTS idx_routine_sets_routine_id ON routine_sets(routine_id);
CREATE INDEX IF NOT EXISTS idx_workouts_start_time ON workouts(start_time);

-- Diet domain (source: MyNetDiary export CSV, data/raw/diet/mynetdiary_{year}_food.csv)

CREATE TABLE IF NOT EXISTS food_log (
    log_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    food_id     TEXT NOT NULL,      -- MyNetDiary catalog id, reused across dates
    date        TEXT NOT NULL,
    meal_time   TEXT NOT NULL,      -- shared across a meal's items; the grouping key
    meal        TEXT NOT NULL CHECK (meal IN ('Breakfast', 'Lunch', 'Dinner', 'Snacks')),
    name        TEXT NOT NULL,
    amount      TEXT,
    calories    REAL,
    protein_g   REAL,
    carbs_g     REAL,
    fat_g       REAL,
    fiber_g     REAL,
    sodium_mg   REAL,
    sugar_g     REAL,
    notes       TEXT
);

CREATE INDEX IF NOT EXISTS idx_food_log_date ON food_log(date);

CREATE TABLE IF NOT EXISTS diet_daily (
    date        TEXT PRIMARY KEY,
    calories    REAL,
    protein_g   REAL,
    carbs_g     REAL,
    fat_g       REAL,
    fiber_g     REAL,
    notes       TEXT
);
