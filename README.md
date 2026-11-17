# Yekaterinburg Tram Monitor

Real-time tram tracking application for Yekaterinburg, Russia. Shows live tram positions, routes, stops, and estimated arrival times on an interactive map.

## Recovery Deployment

On a clean Linux host with Docker Engine and the Compose plugin installed:

```sh
git clone https://github.com/empsgit/tram_monitor_ekb.git /opt/tram_monitor_ekb
cd /opt/tram_monitor_ekb
sh scripts/init-env.sh
docker compose up -d --build
curl --fail http://127.0.0.1:3000/api/health
```

Only the frontend port 3000 is published. PostgreSQL, Redis, and the backend
are reachable only through the Docker network. Fresh random credentials are
stored in the ignored `.env` file with mode 600. Do not reuse credentials or
images from a compromised host. SSH private keys stay on the operator's machine.
The ETTU key `111` is public; this deployment requires no private third-party API key.

History collection defaults to off. After validation, set
`HISTORY_RECORDING_ENABLED=true` in the server's ignored `.env` and recreate
only the backend (`docker compose up -d --no-deps backend`). New collection uses
`position_observations`, `stop_visits`, and `segment_observations`. Legacy
`vehicle_positions` and `travel_time_segments` are neither read nor written.
See [history and ETA](docs/HISTORY.md) for quality rules, retention and verification.
The health endpoint reports database and Redis connectivity and recording status.
Container logs rotate at 10 MB with three files per service.

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐
│   ETTU API  │────▶│   Backend    │────▶│   Frontend   │
│ map.ettu.ru │     │   FastAPI    │     │  Leaflet.js  │
└─────────────┘     └──────┬───────┘     └──────────────┘
                           │
                    ┌──────┴───────┐
                    │  PostgreSQL  │
                    │  + PostGIS   │
                    │    Redis     │
                    └──────────────┘
```

**Stack**: FastAPI + SQLAlchemy async, PostgreSQL/PostGIS, Redis pub/sub, Vite + TypeScript, Leaflet.js

## Data Pipeline

### 1. Data Sources (ETTU API)

All raw data comes from `map.ettu.ru` with `apiKey=111`:

| Endpoint | Data | Poll Frequency |
|---|---|---|
| `/api/v2/tram/boards/` | Live tram positions (device ID, board number, route, lat/lon, speed, course) | Every 10s |
| `/api/v2/tram/routes/` | Route definitions (route ID, number, name, ordered list of stop IDs per direction via `elements[].path[]`) | At startup |
| `/api/v2/tram/points/` | All tram stops (ID, NAME, LAT, LON, STATUS, DIRECTION) | At startup |

**Important**: The points API returns uppercase field names. Each physical stop may have **two entries** with the same name but different IDs and different `DIRECTION` values (e.g. `"на Пионерскую"` vs `"на Техучилище"`), representing opposite travel directions at that stop.

### 2. Route Geometry (OSRM)

The ETTU routes API only provides an **ordered list of stop IDs** per direction — no street-level geometry. Connecting stops with straight lines gives inaccurate routes.

To get real road-following geometry, the tracker calls the **OSRM public router**:

```
https://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2};...?overview=full&geometries=geojson
```

All forward-direction (direction=0) stop coordinates are sent as waypoints. OSRM returns a GeoJSON LineString that follows actual streets. A 0.3s delay between requests avoids rate limiting.

**Fallback**: If OSRM fails, the route is drawn as straight lines between consecutive stop coordinates.

### 3. Observation-Time Processing

`JourneyTracker` uses ATIME, ordered stop sequences and observed movement,
not the map polyline or API course, to confirm direction. It ignores repeated,
out-of-order, future, stale and implausibly displaced fixes for journey advancement.
A new vehicle needs observations near two distinct stops in forward order.
Ambiguous directions stay unknown. Direction is locked until an observed terminal
departure, a route version change, or a data gap that requires reacquisition.

Arrival and departure observations are separate. Only adjacent confirmed visits
with continuous source timestamps produce a segment sample. Missing stops and
long gaps do not become invented travel times. All timing is approximate to the
sampling interval; raw observations remain available for later recalculation.

### 4. ETA and Stop Arrivals

Real PostgreSQL quantiles are computed from raw segment and dwell observations,
grouped by route version, direction, sequence index, local weekday and hour.
Sparse groups fall back to hourly/all-time history, then a conservative distance
estimate. Responses report `eta_source` and indicative lower/upper bounds.

All stop arrivals are rebuilt in memory each poll. Only confirmed upcoming stops
are included. Repeated IDs in a loop use the nearest future occurrence. Route
filtering happens before the result limit. Stale source data gets `signal_lost`
and no ETA; stationary vehicles do not get a synthetic countdown.

### 5. Frontend

The background uses the free, keyless OpenFreeMap Positron style with only streets,
Russian street labels and water. Buildings, POIs and district labels are omitted.
MapLibre renders this background inside Leaflet's tile pane; tram, stop and route
overlays are unchanged. The renderer is lazy-loaded, uses one worker, and caps
pixel ratio/tile cache size for mobile devices. If loading or WebGL initialization
fails, standard OSM raster tiles are an automatic fallback (no layer switcher).
Attribution remains in the sidebar. See https://openfreemap.org/quick_start/.

Markers show received GPS fixes without interpolation or extrapolation.
Stop requests use cancellation and a generation guard, with a small response
cache and background refresh. Old requests cannot overwrite a newly selected
stop. Filter/selection events no longer rebuild every map layer and hidden list.
Disabling all routes hides all vehicles. Clearing selection restores stop styling.
## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/routes` | List all routes with geometry and stop_ids |
| GET | `/api/routes/{id}` | Route detail with stops |
| GET | `/api/stops` | List all stops with direction |
| GET | `/api/stops/{id}/arrivals` | Upcoming trams at a stop |
| GET | `/api/vehicles` | All active vehicles |
| GET | `/api/vehicles/{id}` | Single vehicle state |
| GET | `/api/diagnostics` | Pipeline diagnostics (route-stop resolution, geometry, matching stats) |
| GET | `/api/diagnostics/routes/{id}` | Diagnostics for a specific route |
| WS  | `/ws/vehicles` | Real-time vehicle position stream |
| GET | `/api/health` | Health check |

### Diagnostics Endpoint

`GET /api/diagnostics` returns detailed pipeline verification data:

```json
{
  "total_stops_in_points_api": 250,
  "total_routes": 30,
  "total_vehicles": 85,
  "vehicles_matched_to_route": 72,
  "vehicles_unmatched": 13,
  "routes": [
    {
      "route_id": 1,
      "route_number": "1",
      "path_stop_count": 48,
      "resolved_count": 46,
      "named_count": 40,
      "unresolved_ids": [99901, 99902],
      "has_osrm_geometry": true,
      "geometry_points": 1250,
      "route_length_m": 18500.0,
      "stops_by_direction": {
        "0": [{"id": 1168, "name": "1-й км (на Пионерскую)", "order": 0, "distance_along_m": 0.0}],
        "1": [{"id": 1169, "name": "1-й км (на Техучилище)", "order": 0, "distance_along_m": 50.3}]
      }
    }
  ]
}
```

Use this to identify:
- **Unresolved stops**: IDs in route paths that don't exist in the points API
- **Unnamed stops**: Stops with coordinates but no name (STATUS=0 or empty NAME)
- **Vehicle matching rate**: How many trams are successfully snapped to their routes
- **Stop ordering**: Verify stops are in the correct sequence along each route direction

## Running

```bash
docker compose up -d --build
```

The app will be available at `http://localhost:3000`.

To rebuild after code changes:
```bash
docker compose down && docker compose up -d --build
```

## Configuration

Environment variables (set in `docker-compose.yml`):

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | Generated by Compose from `.env` | PostgreSQL connection |
| `REDIS_URL` | Generated by Compose from `.env` | Redis connection |
| `ETTU_BASE_URL` | `https://map.ettu.ru` | ETTU API base URL |
| `POLL_INTERVAL_SECONDS` | `10` | Vehicle polling interval |
| `ROUTE_REFRESH_HOURS` | `1` | Route/stop refresh interval |
