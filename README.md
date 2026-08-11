# health-data

A small personal data pipeline that ingests weekly diet and exercise exports
and normalizes them into a single queryable SQLite database, with automatic
backups and a test suite.

Two sources are ingested from local export files (paths are configurable via
environment variables):

- **Exercise** — Hevy workout/routine export JSON
  (`workouts_master.json`, `routines_master.json`)
- **Diet** — MyNetDiary annual food-log CSV
  (`mynetdiary_{year}_food.csv`)

Everything lands in a local SQLite database (gitignored — the schema and code
are versioned, the personal data is not).

## Schema

See `db/schema.sql`. Tables are grouped into two domains, registered in a small
`_domains` table so the set of real tables stays self-describing:

- **Exercise**: `exercises` (exercise catalog) → `routines` / `routine_sets`
  (the planned program, versioned via `parent_routine_id`) and
  `workouts` / `workout_sets` (what actually happened, one row per set).
- **Diet**: `food_log` (one row per item logged; `meal_time` groups items into
  a single meal) and `diet_daily` (a per-day rollup; its `notes` column is for
  manual annotations and survives re-ingestion).

## Design notes

The two sources have different update semantics, and the sync logic reflects
that:

- **Exercise** rows carry stable upstream ids, so parents are upserted and
  their child sets are replaced on each run. A reconcile pass removes anything
  deleted in the source app, and is skipped when a pull comes back empty so a
  failed export can't wipe a table.
- **Diet** uses a catalog id that is reused every time the same food is logged,
  so it can't be upserted row-by-row. Instead each year's CSV (a full annual
  snapshot) replaces that year's `food_log` rows, and `diet_daily` is recomputed
  from whatever is in `food_log`.

Both syncs are safe to re-run at any time; the module docstrings in
`src/health_data/` describe the exact refresh behavior.

## Configuration

Source paths and backup settings are read from environment variables (with
sensible defaults):

| Variable | Purpose |
|---|---|
| `MYNETDIARY_RAW_DIR` | Directory holding the diet CSV exports |
| `HEVY_WORKOUTS_PATH` | Path to the Hevy workouts export JSON |
| `HEVY_ROUTINES_PATH` | Path to the Hevy routines export JSON |
| `HEALTH_DB_BACKUP_DIR` | Where dated DB snapshots are written |
| `HEALTH_DB_BACKUP_RETENTION` | Number of snapshots to keep (default 12) |

See `.env.example` for a template.

## Running

```bash
uv run backup-db       # dated snapshot of the DB before either sync touches it
uv run sync-exercise   # reads the Hevy JSON, upserts exercise/routine/workout tables
uv run sync-diet       # reads the diet CSVs, replaces food_log per year, rolls up diet_daily
```

`backup-db` uses sqlite3's own backup API (not a raw file copy, so a backup is
never taken mid-write), keeps the last N weekly snapshots, and prunes older
ones automatically.

A typical deployment runs these on a weekly schedule, with `backup-db` first so
there is always a snapshot from immediately before that week's ingestion.

## Testing

```bash
uv run pytest
```

Tests run entirely against synthetic fixtures in `tests/fixtures/` — no real
data or external access needed. They cover macro parsing, the per-year
replace / daily-rollup logic, note preservation across re-ingestion, and a
regression test ensuring a re-ingested corrected workout replaces its sets
rather than duplicating them.
