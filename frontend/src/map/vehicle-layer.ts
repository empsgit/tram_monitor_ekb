/** Manages tram vehicle markers on the map with smooth animation and trails. */

import L from "leaflet";
import "leaflet.marker.slideto";
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

interface MarkerMeta {
  route: string;
  course: number;
}

const SLIDE_DURATION = 9500;
const MAX_TRAIL_POINTS = 20; // ~3 minutes of history

export class VehicleLayer {
  private markers: Map<string, L.Marker> = new Map();
  private meta: Map<string, MarkerMeta> = new Map();
  private trailPoints: Map<string, [number, number][]> = new Map();
  private trailLines: Map<string, L.Polyline> = new Map();
  private layerGroup: L.LayerGroup;
  private trailGroup: L.LayerGroup;

  constructor(map: L.Map) {
    // Trail layer behind markers
    this.trailGroup = L.layerGroup().addTo(map);
    this.layerGroup = L.layerGroup().addTo(map);
  }

  update(vehicles: VehicleData[]): void {
    const seen = new Set<string>();

    for (const v of vehicles) {
      seen.add(v.id);
      const existing = this.markers.get(v.id);

      // Update trail
      this.updateTrail(v);

      if (existing) {
        const marker = existing as any;
        if (typeof marker.slideTo === "function") {
          marker.slideTo([v.lat, v.lon], {
            duration: SLIDE_DURATION,
            keepAtCenter: false,
          });
        } else {
          existing.setLatLng([v.lat, v.lon]);
        }

        const prev = this.meta.get(v.id);
        if (!prev || prev.route !== v.route) {
          existing.setIcon(createIcon(v.route));
        }

        this.updateHeading(existing, v.course);
        this.meta.set(v.id, { route: v.route, course: v.course });
        existing.setPopupContent(this.popupHtml(v));
      } else {
        const marker = L.marker([v.lat, v.lon], {
          icon: createIcon(v.route),
          zIndexOffset: 100,
        });
        marker.bindPopup(this.popupHtml(v));
        marker.addTo(this.layerGroup);
        this.markers.set(v.id, marker);
        this.meta.set(v.id, { route: v.route, course: v.course });
        this.updateHeading(marker, v.course);
      }
    }

    // Remove stale markers and trails
    for (const [id, marker] of this.markers) {
      if (!seen.has(id)) {
        this.layerGroup.removeLayer(marker);
        this.markers.delete(id);
        this.meta.delete(id);
        const trail = this.trailLines.get(id);
        if (trail) {
          this.trailGroup.removeLayer(trail);
          this.trailLines.delete(id);
        }
        this.trailPoints.delete(id);
      }
    }
  }

  private updateTrail(v: VehicleData): void {
    const pts = this.trailPoints.get(v.id) || [];
    const last = pts[pts.length - 1];
    // Only add if position changed noticeably
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
    this.layerGroup.clearLayers();
    this.trailGroup.clearLayers();
    this.markers.clear();
    this.meta.clear();
    this.trailPoints.clear();
    this.trailLines.clear();
  }
}
