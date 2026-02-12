/** Manages tram vehicle markers with route-snapped animation. */

import L from "leaflet";
import type { VehicleData } from "../services/ws-client";
import type { RouteInfo } from "../services/api-client";

// Maximally distinct color palette (Kelly's colors + extras)
const COLORS = [
  "#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4",
  "#42d4f4", "#f032e6", "#bfef45", "#fabed4", "#469990",
  "#dcbeff", "#9A6324", "#800000", "#aaffc3", "#808000",
  "#000075", "#a9a9a9", "#ffe119", "#ffd8b1", "#00CED1",
  "#ff6eb4", "#ff4500", "#1abc9c", "#8b0000",
];

export function routeColor(routeNum: string): string {
  const num = parseInt(routeNum, 10);
  if (!isNaN(num)) {
    return COLORS[num % COLORS.length];
  }
  let hash = 0;
  for (let i = 0; i < routeNum.length; i++) {
    hash = routeNum.charCodeAt(i) + ((hash << 5) - hash);
  }
  return COLORS[Math.abs(hash) % COLORS.length];
}

function createIcon(routeNum: string, signalLost = false): L.DivIcon {
  const color = routeColor(routeNum);
  const opacity = signalLost ? "0.4" : "1";
  return L.divIcon({
    className: "",
    iconSize: [32, 32],
    iconAnchor: [16, 16],
    html: `
      <div class="tram-marker" style="background:${color};opacity:${opacity}">
        <span class="tram-arrow"></span>
        <span class="tram-label">${routeNum}</span>
      </div>
    `,
  });
}

// --- Animation constants ---
const INTERP_DURATION = 1800; // ms — converge quickly to latest server point to avoid visual lag
const MAX_EXTRAP_MS = 5000;    // ms — extrapolate up to 5s beyond target
const DEG2RAD = Math.PI / 180;
const M_PER_DEG_LAT = 111320;

// Max progress change that can be animated (~5% of route).
// Anything larger is a GPS glitch or direction change — snap instantly.
const MAX_ANIM_PROGRESS_DELTA = 0.05;


/** Interpolate between two angles via the shortest arc. */
function lerpAngle(from: number, to: number, t: number): number {
  const diff = ((to - from) % 360 + 540) % 360 - 180;
  return (from + diff * t + 360) % 360;
}

// --- Pre-computed route geometry for fast progress → [lat,lon] lookup ---
interface RouteGeometry {
  points: [number, number][];  // [lat, lon]
  cumDist: number[];           // cumulative distance (meters) at each vertex
  totalDist: number;
}

function buildRouteGeometry(coords: number[][]): RouteGeometry {
  const points: [number, number][] = coords.map(c => [c[0], c[1]]);
  const cumDist: number[] = [0];
  let total = 0;
  const cosLat = Math.cos((points[0][0]) * DEG2RAD);
  for (let i = 1; i < points.length; i++) {
    const dlat = (points[i][0] - points[i - 1][0]) * M_PER_DEG_LAT;
    const dlon = (points[i][1] - points[i - 1][1]) * M_PER_DEG_LAT * cosLat;
    total += Math.sqrt(dlat * dlat + dlon * dlon);
    cumDist.push(total);
  }
  return { points, cumDist, totalDist: total };
}

/** Given a progress 0–1, return [lat, lon] on the route polyline. */
function pointAtProgress(geom: RouteGeometry, progress: number): [number, number] {
  const p = Math.max(0, Math.min(1, progress));
  const targetDist = p * geom.totalDist;

  // Binary search for the segment containing targetDist
  let lo = 0, hi = geom.cumDist.length - 1;
  while (lo < hi - 1) {
    const mid = (lo + hi) >> 1;
    if (geom.cumDist[mid] <= targetDist) lo = mid;
    else hi = mid;
  }

  const segStart = geom.cumDist[lo];
  const segEnd = geom.cumDist[hi];
  const segLen = segEnd - segStart;
  const t = segLen > 0 ? (targetDist - segStart) / segLen : 0;

  return [
    geom.points[lo][0] + (geom.points[hi][0] - geom.points[lo][0]) * t,
    geom.points[lo][1] + (geom.points[hi][1] - geom.points[lo][1]) * t,
  ];
}

/** Compute bearing (degrees) at a progress point on the route. */
function bearingAtProgress(geom: RouteGeometry, progress: number): number {
  const eps = 0.001; // ~0.1% of route
  const p1 = pointAtProgress(geom, Math.max(0, progress - eps));
  const p2 = pointAtProgress(geom, Math.min(1, progress + eps));
  const cosLat = Math.cos(p1[0] * DEG2RAD);
  const dx = (p2[1] - p1[1]) * cosLat;
  const dy = p2[0] - p1[0];
  return (Math.atan2(dx, dy) * 180 / Math.PI + 360) % 360;
}

interface TrackedVehicle {
  marker: L.Marker;
  route: string;
  routeId: number | null;
  signalLost: boolean;
  // Progress-based animation
  prevProgress: number | null;
  targetProgress: number | null;
  // Fallback: raw lat/lon for vehicles without route geometry
  prevLat: number;
  prevLon: number;
  prevCourse: number;
  targetLat: number;
  targetLon: number;
  targetCourse: number;
  targetSpeed: number; // km/h
  // Timing
  updateTime: number; // performance.now()
  // Current rendered values
  currentLat: number;
  currentLon: number;
  currentCourse: number;
  currentProgress: number | null;
}

export class VehicleLayer {
  private tracked: Map<string, TrackedVehicle> = new Map();
  private layerGroup: L.LayerGroup;
  private rafId: number = 0;

  // Route geometry index for progress → position lookup
  private routeGeometries: Map<number, RouteGeometry> = new Map();

  constructor(map: L.Map) {
    this.layerGroup = L.layerGroup().addTo(map);
    this.startAnimation();
  }

  /** Load route geometries for route-following animation. */
  loadRoutes(routes: RouteInfo[]): void {
    this.routeGeometries.clear();
    for (const route of routes) {
      if (route.geometry && route.geometry.length >= 2) {
        this.routeGeometries.set(route.id, buildRouteGeometry(route.geometry));
      }
    }
  }

  /** Kick off the continuous animation loop. */
  private startAnimation(): void {
    const animate = () => {
      this.interpolateAll();
      this.rafId = requestAnimationFrame(animate);
    };
    this.rafId = requestAnimationFrame(animate);
  }

  /** Called on each server update (~10 s). Stores targets and creates/removes markers. */
  update(vehicles: VehicleData[]): void {
    const now = performance.now();
    const seen = new Set<string>();

    for (const v of vehicles) {
      seen.add(v.id);

      const tv = this.tracked.get(v.id);
      if (tv) {
        // Snap "previous" to wherever the marker currently is
        tv.prevLat = tv.currentLat;
        tv.prevLon = tv.currentLon;
        tv.prevCourse = tv.currentCourse;
        tv.prevProgress = tv.currentProgress;

        // Detect unreasonable progress jumps — snap instead of animating
        if (
          tv.prevProgress != null && v.progress != null &&
          Math.abs(v.progress - tv.prevProgress) > MAX_ANIM_PROGRESS_DELTA
        ) {
          // Large jump: teleport to new position immediately
          tv.prevProgress = v.progress;
          tv.prevLat = v.lat;
          tv.prevLon = v.lon;
          tv.prevCourse = v.course;
        }

        // Set new target
        tv.targetLat = v.lat;
        tv.targetLon = v.lon;
        tv.targetCourse = v.course;
        tv.targetSpeed = v.speed;
        tv.targetProgress = v.progress;
        tv.routeId = v.route_id;
        tv.updateTime = now;

        if (tv.route !== v.route || tv.signalLost !== v.signal_lost) {
          tv.marker.setIcon(createIcon(v.route, v.signal_lost));
          tv.route = v.route;
          tv.signalLost = v.signal_lost;
        }
        tv.marker.setPopupContent(this.popupHtml(v));
      } else {
        const marker = L.marker([v.lat, v.lon], {
          icon: createIcon(v.route, v.signal_lost),
          zIndexOffset: 100,
        });
        marker.bindPopup(this.popupHtml(v));
        marker.addTo(this.layerGroup);
        this.updateHeading(marker, v.course);

        this.tracked.set(v.id, {
          marker,
          route: v.route,
          routeId: v.route_id,
          signalLost: v.signal_lost,
          prevProgress: v.progress,
          targetProgress: v.progress,
          prevLat: v.lat,
          prevLon: v.lon,
          prevCourse: v.course,
          targetLat: v.lat,
          targetLon: v.lon,
          targetCourse: v.course,
          targetSpeed: v.speed,
          updateTime: now,
          currentLat: v.lat,
          currentLon: v.lon,
          currentCourse: v.course,
          currentProgress: v.progress,
        });
      }
    }

    // Remove stale markers
    for (const [id, tv] of this.tracked) {
      if (!seen.has(id)) {
        this.layerGroup.removeLayer(tv.marker);
        this.tracked.delete(id);
      }
    }
  }

  /**
   * Runs every animation frame (~60 fps).
   *
   * If the vehicle has progress + route geometry, it follows the route polyline.
   * Otherwise falls back to linear lat/lon interpolation + dead-reckoning.
   */
  private interpolateAll(): void {
    const now = performance.now();

    for (const [, tv] of this.tracked) {
      const elapsed = now - tv.updateTime;
      let lat: number, lon: number, course: number;

      // Try route-following mode
      const geom = tv.routeId != null ? this.routeGeometries.get(tv.routeId) : undefined;
      const hasRoute = geom && tv.targetProgress != null && tv.prevProgress != null;

      if (hasRoute) {
        // --- Route-following animation ---
        let progress: number;
        if (elapsed < INTERP_DURATION) {
          const t = elapsed / INTERP_DURATION;
          progress = tv.prevProgress! + (tv.targetProgress! - tv.prevProgress!) * t;
        } else {
          // Dead reckoning: advance progress based on speed
          const extraMs = Math.min(elapsed - INTERP_DURATION, MAX_EXTRAP_MS);
          const extraS = extraMs / 1000;
          const speedMs = tv.targetSpeed / 3.6;
          const dMeters = speedMs * extraS;
          const dProgress = geom!.totalDist > 0 ? dMeters / geom!.totalDist : 0;
          // Direction: if targetProgress >= prevProgress, moving forward
          const dir = tv.targetProgress! >= tv.prevProgress! ? 1 : -1;
          progress = tv.targetProgress! + dProgress * dir;
        }
        progress = Math.max(0, Math.min(1, progress));

        const pos = pointAtProgress(geom!, progress);
        lat = pos[0];
        lon = pos[1];
        course = bearingAtProgress(geom!, progress);
        // Reverse direction: flip bearing 180°
        if (tv.targetProgress! < tv.prevProgress!) {
          course = (course + 180) % 360;
        }
        tv.currentProgress = progress;
      } else {
        // --- Fallback: linear lat/lon interpolation ---
        if (elapsed < INTERP_DURATION) {
          const t = elapsed / INTERP_DURATION;
          lat = tv.prevLat + (tv.targetLat - tv.prevLat) * t;
          lon = tv.prevLon + (tv.targetLon - tv.prevLon) * t;
          const ht = Math.min(elapsed / 3000, 1);
          course = lerpAngle(tv.prevCourse, tv.targetCourse, ht);
        } else {
          const extraMs = Math.min(elapsed - INTERP_DURATION, MAX_EXTRAP_MS);
          const extraS = extraMs / 1000;
          const speedMs = tv.targetSpeed / 3.6;
          const bearing = tv.targetCourse * DEG2RAD;
          const dMeters = speedMs * extraS;
          const cosLat = Math.cos(tv.targetLat * DEG2RAD);

          lat = tv.targetLat + (dMeters * Math.cos(bearing)) / M_PER_DEG_LAT;
          lon = tv.targetLon + (dMeters * Math.sin(bearing)) / (M_PER_DEG_LAT * cosLat);
          course = tv.targetCourse;
        }
        tv.currentProgress = tv.targetProgress;
      }

      // Only touch the DOM when the value actually changed
      if (
        Math.abs(lat - tv.currentLat) > 0.0000005 ||
        Math.abs(lon - tv.currentLon) > 0.0000005
      ) {
        tv.marker.setLatLng([lat, lon]);
        tv.currentLat = lat;
        tv.currentLon = lon;
      }

      if (Math.abs(course - tv.currentCourse) > 0.3) {
        this.updateHeading(tv.marker, course);
        tv.currentCourse = course;
      }
    }
  }

  private updateHeading(marker: L.Marker, course: number): void {
    const el = (marker as any)._icon as HTMLElement | undefined;
    if (!el) return;
    const inner = el.querySelector(".tram-marker") as HTMLElement | null;
    if (inner) inner.style.transform = `rotate(${course}deg)`;
    const label = el.querySelector(".tram-label") as HTMLElement | null;
    if (label) label.style.transform = `rotate(-${course}deg)`;
  }

  private popupHtml(v: VehicleData): string {
    const stopsHtml = v.next_stops
      .slice(0, 3)
      .map((ns, i) => {
        const eta = ns.eta_seconds
          ? ns.eta_seconds < 60
            ? "<1 мин"
            : `~${Math.ceil(ns.eta_seconds / 60)} мин`
          : "";
        const style = i === 0 ? "font-weight:600;color:#16a34a" : "color:#6b7280";
        return `<div style="${style}">${ns.name} ${eta ? `<span class="eta-badge">${eta}</span>` : ""}</div>`;
      })
      .join("");

    const signalBadge = v.signal_lost
      ? `<div style="margin-top:4px;color:#dc2626;font-weight:600;font-size:11px">Нет сигнала</div>`
      : "";

    return `
      <div style="font-family:sans-serif;font-size:13px;min-width:180px">
        <b>Маршрут ${v.route}</b> <span style="color:#6b7280">(${v.board_num})</span><br/>
        <span style="color:#6b7280">Скорость: ${v.speed.toFixed(0)} км/ч</span>
        ${signalBadge}
        ${v.prev_stop ? `<div style="margin-top:4px;color:#6b7280">От: ${v.prev_stop.name}</div>` : ""}
        ${stopsHtml ? `<div style="margin-top:4px;border-top:1px solid #e0e0e0;padding-top:4px;font-size:12px">${stopsHtml}</div>` : ""}
      </div>
    `;
  }

  clear(): void {
    cancelAnimationFrame(this.rafId);
    this.layerGroup.clearLayers();
    this.tracked.clear();
  }
}
