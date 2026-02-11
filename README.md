# Yekaterinburg Tram Monitor

Real-time tram tracking application for Yekaterinburg, Russia. Shows live tram positions, routes, stops, and estimated arrival times on an interactive map.

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

### 3. Vehicle Processing Pipeline

Every 10 seconds, each raw vehicle goes through 3 stages:

#### Stage 1: Route Matching (`route_matcher.py`)

Uses **Shapely linear referencing** to snap each tram's GPS coordinate onto its route geometry:

- Creates a `LineString` from the route's `[[lat, lon], ...]` geometry
- Projects the vehicle's point onto the line → **progress** (0.0 to 1.0, position along the route)
- Calculates perpendicular distance — must be within **300 meters** (`MAX_SNAP_DISTANCE_M`) or matching fails
- **Direction inference**: compares vehicle's `course` (heading) with route bearing at that point. If they differ by >90°, the tram is traveling in reverse (direction=1)

#### Stage 2: Stop Detection (`stop_detector.py`)

Given the vehicle's `distance_along` (progress × route length) and direction:

- Stops are pre-sorted by `distance_along` per direction
- **Binary search** (`bisect`) finds the vehicle's position among sorted stops
- Returns the **previous stop** (last one passed) and **next 5 stops** ahead

#### Stage 3: ETA Calculation (`eta_calculator.py`)

For each upcoming stop:

```
remaining_m = stop.distance_along - vehicle.distance_along
speed_ms   = max(vehicle_speed_kmh, 5.0) / 3.6
eta_seconds = remaining_m / speed_ms
```

- Uses **minimum 5 km/h** to avoid division by zero when a tram is stopped
- **Maximum ETA capped at 3600 seconds** (1 hour) — anything beyond is discarded

### 4. Stop Arrivals (Остановка tab)

When you search for a stop, `get_vehicles_for_stop()` uses **two tiers**:

**Tier 1 — Pipeline-based**: Scans all tracked vehicles' `next_stops`. If any vehicle has the queried stop in its upcoming stops, returns that vehicle with the pre-calculated ETA.

**Tier 2 — Distance-based fallback**: For routes that serve this stop but whose vehicles weren't matched by the pipeline:
1. Looks up which routes serve this stop (from `_stop_to_routes` mapping)
2. For each active vehicle on those routes, calculates **haversine distance** to the stop
3. Estimates ETA as `distance_m / (max(speed, 5.0) / 3.6)`
4. Discards if ETA > 1 hour

### 5. Data Flow Summary

```
ETTU API (every 10s)
    │
    ▼
EttuClient.fetch_vehicles()
    │
    ▼
VehicleTracker._process_vehicle()  ×N vehicles
    │
    ├── RouteMatcher.match()       → progress (0.0–1.0), direction (0 or 1)
    ├── StopDetector.detect()      → prev_stop, next 5 stops
    └── EtaCalculator.calculate()  → ETAs per stop
    │
    ▼
VehicleState
    │
    ├── Broadcaster → Redis pub/sub → WebSocket → Browser
    ├── REST API /api/vehicles
    └── Database (vehicle_positions table)
```

### 6. Frontend Rendering

- **Route polylines**: Drawn from OSRM geometry, colored with Kelly's 24-color maximally distinct palette
- **Tram markers**: Positioned with `leaflet.marker.slideto` for smooth animation between 10s polling intervals
- **Trails**: Last 20 positions per tram, drawn as semi-transparent polylines (opacity 0.25)
- **Stop markers**: CircleMarkers with direction in tooltip. Stops not on selected routes get dimmed
- **ETAs**: Displayed as minutes in vehicle list and popups, showing next 3 stops

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

The app will be available at `http://localhost` (nginx on port 80).

To rebuild after code changes:
```bash
docker compose down && docker compose up -d --build
```

## Configuration

Environment variables (set in `docker-compose.yml`):

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://tram:tram_secret@db:5432/tram_monitor` | PostgreSQL connection |
| `REDIS_URL` | `redis://redis:6379/0` | Redis connection |
| `ETTU_BASE_URL` | `https://map.ettu.ru` | ETTU API base URL |
| `POLL_INTERVAL_SECONDS` | `10` | Vehicle polling interval |
| `ROUTE_REFRESH_HOURS` | `6` | Route/stop refresh interval |
