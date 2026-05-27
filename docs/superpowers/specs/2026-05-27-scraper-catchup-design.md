# Scraper catch-up + UTC scheduler

## Background

Scrapers are registered via `coordinator/services/scraper_registry.py` and
scheduled with APScheduler cron triggers. The alpha-picks scraper
(`packages/alpha-picks-scraper/quilt.yaml`) is configured for `0 14 * * 1-5`
with `jitter_seconds: 3600`, intended to fire daily on weekdays.

In practice the scraper has not fired in over a week. Three problems compound:

1. **Timezone mismatch.** `AsyncIOScheduler()` defaults to local time
   (`America/Los_Angeles`). The manifest comment says "14:00 UTC" but the cron
   actually fires at 14:00 PDT. `coordinator/main.py:341` has the same bug:
   `account_daily_close = "35 20 * * 1-5"` with comment "4:35 PM ET (20:35
   UTC)" — currently runs at 20:35 PDT = 03:35 UTC.
2. **No persistence of last-run.** `ScraperRecord.last_run_at` is in-memory
   only, reset every coordinator restart. The `scrapers` DB table exists with
   `last_success`/`last_error` columns but nothing writes to it.
3. **Cron + jitter + restart pattern misses days entirely.** Empirically
   verified against `apscheduler.triggers.cron.CronTrigger`:
   - Add cron `0 14 * * 1-5` with `jitter=3600` at 13:50 Mon → next fire
     somewhere in 14:00–14:59 Mon. Good.
   - Add the same cron at 14:06 Mon → next fire is **Tuesday** 14:00–14:59,
     even though a legitimate jittered fire could still happen Mon 14:06–14:59.
     Today's base time has passed, so APScheduler rolls to the next valid
     weekday.

   The user develops by restarting the coordinator frequently. Most restarts
   land after 14:00 PDT, so today's window is skipped, the schedule rolls to
   tomorrow, the next restart kills it again before tomorrow's fire, ad
   infinitum. The most recent successful run is from 2026-05-21.

## Goals

- Switch the scheduler to UTC. All server-side timestamps and cron expressions
  are UTC; frontend reformats to local for display (convention established by
  commit `dbd8713`).
- Persist scraper run records so they survive coordinator restarts.
- When the coordinator starts and today's fire window has been missed without a
  successful run, kick the scraper off immediately. Cap at 3 attempts per UTC
  day to bound the damage from repeating failures.

## Non-goals

- Run history beyond "did we run today, how many times did we try." A
  per-attempt history table is deferrable until something actually consumes it.
- Changing the jitter or schedule of any scraper. Manifests stay untouched.
- A general retry/backoff framework. Three flat attempts is enough for v1.

## Design

### 1. Scheduler timezone → UTC

`coordinator/services/scheduler.py`:

```python
from datetime import timezone
...
self._scheduler = AsyncIOScheduler(timezone=timezone.utc)
```

(Standard-library `datetime.timezone.utc` avoids a new pytz dependency.)

All existing cron expressions are reinterpreted as UTC. Audit:

| Job | Cron | Before (local PDT) | After (UTC) | Comment claim |
|---|---|---|---|---|
| `data_goal_processor` | `* * * * *` | every minute | every minute | n/a |
| `account_periodic_sync` | `*/15 * * * 1-5` | every 15 min, Mon–Fri PDT | every 15 min, Mon–Fri UTC | "every 15min, Mon-Fri" |
| `account_daily_close` | `35 20 * * 1-5` | 20:35 PDT = 03:35 UTC | 20:35 UTC | "4:35 PM ET (20:35 UTC)" — matches |
| `scraper:alpha-picks-scraper` | `0 14 * * 1-5` | 14:00 PDT | 14:00 UTC | "14:00 UTC" — matches |

The two annotated jobs (`account_daily_close`, alpha-picks) now do what their
comments say. The `*/15` weekday job shifts its "weekday" boundary by 7h —
Friday 5pm-midnight PDT no longer gets periodic syncs. Acceptable: markets
are closed by then. Update the `account_daily_close` comment to clarify that
20:35 UTC = 4:35 PM EDT / 3:35 PM EST (the cron sits on a DST ambiguity).

### 2. Persistence

Reuse the existing `scrapers` table. Extend the SQLAlchemy `Scraper` model in
`coordinator/database/models.py` with three columns:

```python
last_attempt_at: Mapped[Optional[datetime]] = mapped_column(
    DateTime(timezone=True), nullable=True
)
attempts_today: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
attempts_day: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
```

Add idempotent `ALTER TABLE` shims in `coordinator/main.py` alongside the
existing ones (~lines 33–63).

`ScraperRegistry.discover_and_register` performs an upsert per scraper: if a
row exists, leave its history alone; otherwise insert a fresh row with default
counters. Run state lives in DB, not just in the in-memory `ScraperRecord`.

`run()` updates the row at three points:

- **Before invoking subprocess:** if `attempts_day != today_utc`, reset
  `attempts_today = 0` and `attempts_day = today_utc`. Then increment
  `attempts_today` and set `last_attempt_at = now_utc`. Commit.
- **On success:** set `last_success = now_utc`, clear `last_error`. Commit.
- **On failure:** set `last_error = result.error`. Leave `last_success` alone.
  Commit.

`ScraperRecord` keeps its in-memory mirror of these fields for the API; the DB
is authoritative.

### 3. Catch-up on startup

A new method `ScraperRegistry._maybe_catch_up(name: str)` is called once per
scraper at the end of `discover_and_register`, after the cron job is added.

```
row = read scrapers row for `name`
if row.attempts_day != today_utc:
    row.attempts_today = 0
    row.attempts_day = today_utc
    commit

if row.attempts_today >= 3:
    return                                # bounded retry

base_fire = base_cron_fire_today_utc(name)   # see below; None if not a
                                              # weekday for this cron
if base_fire is None:
    return                                # today isn't a scheduled day
if now_utc < base_fire:
    return                                # cron will fire on its own

if row.last_success is not None and row.last_success >= base_fire:
    return                                # already ran successfully today

asyncio.create_task(self.run(name))
logger.info("scraper %s catch-up fired (missed today's %s base fire)",
            name, base_fire.isoformat())
```

**`base_cron_fire_today_utc(name)`** — compute the cron's un-jittered base fire
time for today in UTC, or `None` if today is not a scheduled day:

```python
trigger = CronTrigger.from_crontab(record.schedule, timezone=timezone.utc)
# Drop jitter for the base-time calculation; we want the floor, not the
# randomized fire.
trigger.jitter = None
midnight_utc = datetime.combine(today_utc, time.min, timezone.utc)
fire = trigger.get_next_fire_time(None, midnight_utc - timedelta(seconds=1))
if fire is None or fire.date() != today_utc:
    return None
return fire
```

For `0 14 * * 1-5`, on a weekday this returns `today 14:00:00 UTC`; on a
weekend it returns next Monday 14:00:00, whose date differs from today → we
return `None` and skip catch-up.

### 4. Jitter on subsequent fires

No change needed. APScheduler's `CronTrigger(jitter=N)` re-randomizes on every
call to `get_next_fire_time`, so once a fire completes the next one already
picks a fresh jittered time. The catch-up itself is a one-shot via
`asyncio.create_task` and doesn't touch the scheduled cron, so the cron's own
re-jittering continues unaffected.

### 6. Concurrency guard

The existing cron lambda is `asyncio.create_task(self.run(name))`. With
catch-up also using `create_task`, a stale cron could (in theory) overlap with
a catch-up run for the same scraper. Add a guard at the top of `run()`:

```python
record = self._scrapers.get(name)
if record is None:
    return ScraperResult(success=False, error=f"scraper {name!r} not registered")
if record.last_status == "running":
    logger.info("scraper %s is already running; skipping duplicate invocation",
                name)
    return ScraperResult(success=False, error="already running")
```

`record.last_status` is set to `"running"` immediately on entry to `run()`
(already exists in the code) and reset on completion. The single-process,
single-event-loop coordinator means this guard is race-free.

### 7. API

`/api/scrapers` currently returns `next_run_at` (from the scheduler) but
`last_run_at`/`last_status`/`last_error` are in-memory and reset on restart.
After this change, the route reads these from the DB row so they survive
restarts. New field: `attempts_today` (so the UI can show "tried 2/3 today").

All timestamps remain ISO-8601 with UTC offset; frontend continues to format
to local for display.

## Testing

Unit tests in `tests/coordinator/services/test_scraper_registry_catchup.py`:

- `test_catchup_eligible_on_weekday_after_base_time`: simulates 16:00 UTC on a
  Tuesday with no last_success → catch-up fires.
- `test_catchup_skipped_before_base_time`: 13:30 UTC → catch-up skipped, cron
  handles it.
- `test_catchup_skipped_when_already_ran_today`: last_success = today 14:30
  UTC → catch-up skipped.
- `test_catchup_skipped_on_weekend`: Saturday 16:00 UTC → catch-up skipped
  (cron is `* * * * 1-5`).
- `test_catchup_skipped_when_three_attempts_today`: attempts_today = 3 →
  catch-up skipped.
- `test_attempts_today_resets_on_new_utc_day`: attempts_day = yesterday →
  attempts_today reset to 0 before the count check.

DB roundtrip tests in the same file:

- `test_run_success_persists_last_success_to_db`: invoke `run()` with a stub
  engine that returns success → DB row has `last_success` populated.
- `test_run_failure_increments_attempts_only`: invoke `run()` with a stub
  engine that returns failure → `attempts_today` increments, `last_success`
  stays None.
- `test_three_failures_then_catchup_returns_false`: simulate three failed
  invocations → fourth catch-up is a no-op.

Scheduler timezone test in `tests/coordinator/services/test_scheduler_tz.py`:

- `test_scheduler_uses_utc`: `SchedulerService()._scheduler.timezone ==
  timezone.utc`.
- `test_cron_0_14_fires_at_14_utc`: with the scheduler set to UTC, cron `0 14
  * * 1-5` resolves to 14:00 UTC, not 14:00 local.

## Migration

The added columns are nullable / have defaults, so the idempotent
`ALTER TABLE` shims handle existing DBs without a destructive migration. On
the first startup after deploy, every scraper's row is upserted with default
counters; the catch-up runs once if eligible.

## Rollback

Revert the commit. The added DB columns are harmless if left in place.
