/** Manages tram vehicle markers with continuous client-side interpolation. */

import L from "leaflet";
import type { VehicleData } from "../services/ws-client";

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

function createIcon(routeNum: string): L.DivIcon {
  const color = routeColor(routeNum);
  return L.divIcon({
    className: "",
    iconSize: [32, 32],
    iconAnchor: [16, 16],
    html: `
      <div class="tram-marker" style="background:${color}">
        <span class="tram-arrow"></span>
        <span class="tram-label">${routeNum}</span>
      </div>
    `,
  });
}

// --- Dead-reckoning constants ---
const INTERP_DURATION = 10000; // ms — matches server poll interval
const MAX_EXTRAP_MS = 5000;    // ms — extrapolate up to 5s beyond target
const DEG2RAD = Math.PI / 180;
const M_PER_DEG_LAT = 111320;
const MAX_TRAIL_POINTS = 20;

/** Interpolate between two angles via the shortest arc. */
function lerpAngle(from: number, to: number, t: number): number {
  const diff = ((to - from) % 360 + 540) % 360 - 180;
  return (from + diff * t + 360) % 360;
}

interface TrackedVehicle {
  marker: L.Marker;
  route: string;
  // Animated position snapshot when the latest update arrived
  prevLat: number;
  prevLon: number;
  prevCourse: number;
  // Target from the latest server update
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
}

export class VehicleLayer {
  private tracked: Map<string, TrackedVehicle> = new Map();
  private trailPoints: Map<string, [number, number][]> = new Map();
  private trailLines: Map<string, L.Polyline> = new Map();
  private layerGroup: L.LayerGroup;
  private trailGroup: L.LayerGroup;
  private rafId: number = 0;

  constructor(map: L.Map) {
    this.trailGroup = L.layerGroup().addTo(map);
    this.layerGroup = L.layerGroup().addTo(map);
    this.startAnimation();
  }

  /** Kick off the continuous animation loop. */
  private startAnimation(): void {
    const animate = () => {
      this.interpolateAll();
      this.rafId = requestAnimationFrame(animate);
    };
    this.rafId = requestAnimationFrame(animate);
  }

  /** Called on each server update (~10 s). Stores targets, creates/removes markers, updates trails. */
  update(vehicles: VehicleData[]): void {
    const now = performance.now();
    const seen = new Set<string>();

    for (const v of vehicles) {
      seen.add(v.id);
      this.updateTrail(v);

      const tv = this.tracked.get(v.id);
      if (tv) {
        // Snap "previous" to wherever the marker currently is
        tv.prevLat = tv.currentLat;
        tv.prevLon = tv.currentLon;
        tv.prevCourse = tv.currentCourse;
        // Set new target
        tv.targetLat = v.lat;
        tv.targetLon = v.lon;
        tv.targetCourse = v.course;
        tv.targetSpeed = v.speed;
        tv.updateTime = now;

        if (tv.route !== v.route) {
          tv.marker.setIcon(createIcon(v.route));
          tv.route = v.route;
        }
        tv.marker.setPopupContent(this.popupHtml(v));
      } else {
        const marker = L.marker([v.lat, v.lon], {
          icon: createIcon(v.route),
          zIndexOffset: 100,
        });
        marker.bindPopup(this.popupHtml(v));
        marker.addTo(this.layerGroup);
        this.updateHeading(marker, v.course);

        this.tracked.set(v.id, {
          marker,
          route: v.route,
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
        });
      }
    }

    // Remove stale markers and trails
    for (const [id, tv] of this.tracked) {
      if (!seen.has(id)) {
        this.layerGroup.removeLayer(tv.marker);
        this.tracked.delete(id);
        const trail = this.trailLines.get(id);
        if (trail) {
          this.trailGroup.removeLayer(trail);
          this.trailLines.delete(id);
        }
        this.trailPoints.delete(id);
      }
    }
  }

  /**
   * Runs every animation frame (~60 fps).
   *
   * Phase 1 (0 – 10 s): linear interpolation from previous position to server target.
   * Phase 2 (10 – 15 s): dead-reckoning extrapolation using speed + heading.
   *
   * This eliminates the "move-stop-move" pattern completely.
   */
  private interpolateAll(): void {
    const now = performance.now();

    for (const [, tv] of this.tracked) {
      const elapsed = now - tv.updateTime;
      let lat: number, lon: number, course: number;

      if (elapsed < INTERP_DURATION) {
        // Smooth interpolation toward server target
        const t = elapsed / INTERP_DURATION;
        lat = tv.prevLat + (tv.targetLat - tv.prevLat) * t;
        lon = tv.prevLon + (tv.targetLon - tv.prevLon) * t;
        // Heading settles faster (~3 seconds)
        const ht = Math.min(elapsed / 3000, 1);
        course = lerpAngle(tv.prevCourse, tv.targetCourse, ht);
      } else {
        // Dead reckoning: continue moving based on last known speed + heading
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

  private updateTrail(v: VehicleData): void {
    const pts = this.trailPoints.get(v.id) || [];
    const last = pts[pts.length - 1];
    if (!last || Math.abs(last[0] - v.lat) > 0.00002 || Math.abs(last[1] - v.lon) > 0.00002) {
      pts.push([v.lat, v.lon]);
      if (pts.length > MAX_TRAIL_POINTS) pts.shift();
      this.trailPoints.set(v.id, pts);
    }

    if (pts.length < 2) return;

    const color = routeColor(v.route);
    let polyline = this.trailLines.get(v.id);
    if (polyline) {
      polyline.setLatLngs(pts);
      polyline.setStyle({ color });
    } else {
      polyline = L.polyline(pts, {
        color,
        weight: 3,
        opacity: 0.25,
        lineCap: "round",
        lineJoin: "round",
      });
      polyline.addTo(this.trailGroup);
      this.trailLines.set(v.id, polyline);
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

    return `
      <div style="font-family:sans-serif;font-size:13px;min-width:180px">
        <b>Маршрут ${v.route}</b> <span style="color:#6b7280">(${v.board_num})</span><br/>
        <span style="color:#6b7280">Скорость: ${v.speed.toFixed(0)} км/ч</span>
        ${v.prev_stop ? `<div style="margin-top:4px;color:#6b7280">От: ${v.prev_stop.name}</div>` : ""}
        ${stopsHtml ? `<div style="margin-top:4px;border-top:1px solid #e0e0e0;padding-top:4px;font-size:12px">${stopsHtml}</div>` : ""}
      </div>
    `;
  }

  clear(): void {
    cancelAnimationFrame(this.rafId);
    this.layerGroup.clearLayers();
    this.trailGroup.clearLayers();
    this.tracked.clear();
    this.trailPoints.clear();
    this.trailLines.clear();
  }
}
