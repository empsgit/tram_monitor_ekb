# Observation History and ETA

## Collection

- `position_observations`: one row per `(vehicle_id, observed_at)`. `observed_at`
  is ATIME converted from Ekaterinburg time to UTC; `received_at` is server time.
  The original coordinates, speed/course, route version, confirmed direction and
  quality status are retained. Duplicate polls do not create new rows.
- `stop_visits`: first observation within 60 m, departure beyond 90 m with forward
  movement. Arrival and departure are separate source timestamps. The key is
  `(trip_id, stop_index)`, so repeated stop IDs in loops remain distinct.
- `segment_observations`: departure-to-arrival time between adjacent visits in
  the same trip/version/direction. No samples across skipped stops, GPS jumps,
  route changes, or source-time gaps above 120 seconds. Accepted durations are
  10-1800 seconds, with a 35 m/s plausibility ceiling.

These are sampled events, not exact door-opening times. Sampling interval,
GPS radius and curved track geometry introduce uncertainty. Retaining raw fixes
and visits allows a later detector to be evaluated/replayed without trusting the
current aggregate. Legacy history tables are untouched and excluded from ETA.

Direction starts unknown and requires a unique forward sequence near two
different stops, at least 100 m apart. Course alone cannot lock or flip direction.
A terminal must be observed before reversal; departure uses distance from the
closest approach, not distance moved in one poll. Long gaps require reacquisition,
except a stationary tram still at the same fix. Catalog changes reset affected
journeys. These conservative rules can temporarily omit uncertain arrivals.

Source time more than 30 seconds in the future or 120 seconds old cannot advance
the journey. Old valid journeys remain visible with no ETA for up to 6 hours;
this bounds vehicles that have left service and never report again. Missing or
malformed ATIME is not replaced with server time to manufacture training samples.

## Statistics and ETA

Every five minutes PostgreSQL calculates true `percentile_cont` p25/median/p75
from raw samples. Exact weekday/hour groups require 5 samples, hour-only groups
8, all-time groups 10. Day/hour use Asia/Yekaterinburg, including UTC day rollover.
Fallback groups are independently computed from raw rows, not averaged medians.
Directions, stop occurrences and route versions never mix.

ETA adds run medians and intermediate dwell medians, scaling the first segment by
remaining straight-line distance. It does not apply instantaneous speed to the
entire remaining route. With insufficient history, fallback uses 18 km/h and
20 seconds per intermediate stop. This is explicitly labeled `fallback` or
`mixed`; `history` means the contributing run/dwell estimates are historical.
Bounds are sums of component quantiles, not calibrated confidence intervals.
Curved-segment remaining-distance estimation is still approximate.

Countdown is capped at 30 seconds per actual observation and disabled at speed 0.
After 120 seconds without a valid new fix, ETA is null, not an ever-decreasing
fiction. The full arrival snapshot is retained; filtering precedes the 15-row cap.

## Retention and Operations

Set these in the ignored server `.env`, then recreate the backend:

```dotenv
HISTORY_RECORDING_ENABLED=true
POSITION_RETENTION_DAYS=30
SAMPLE_RETENTION_DAYS=180
```

```sh
docker compose up -d --no-deps backend
curl --fail http://127.0.0.1:3000/api/health
curl --fail http://127.0.0.1:3000/api/diagnostics
```

Maintenance deletes expired new-table records in indexed batches of 5,000,
at most 20 batches per table per run. There is no startup wipe. Failed writes
remain queued for retry; each queue is capped at 10,000 rows, with explicit
overflow/error counters. A process crash can lose an uncommitted in-memory batch.
Database uniqueness protects against duplicate inserts after retries/restarts.

Diagnostics include `history` counters, queue sizes, last statistics refresh,
and `journeys` with direction status, passed index and route version. Collection
can be paused by setting the flag false; this does not delete stored data.

## Verification

```sh
cd backend
python -m pytest -q
cd ../frontend
node --test tests/*.test.mjs  # Node 24, native TypeScript stripping
npm run build
```

`python -m tests.integration_history` additionally checks PostgreSQL deduplication,
visit upserts, real quantiles, catalog rollback, stop-cache freshness and retention.
It requires `DATABASE_URL` plus `TRAM_TEST_DATABASE` naming an EMPTY disposable
database starting with `tram_test_`, with PostGIS installed. Never point it at
the application database. The test refuses other database names.

`python -m tests.shadow_live` reads live ETTU data for about three minutes without
publishing or writing to any database. It reports confirmed journeys, accepted
observations, rejected fixes, visits and candidate segment samples.
