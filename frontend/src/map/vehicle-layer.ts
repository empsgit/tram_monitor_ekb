/** Manages tram vehicle markers on the map. */

import L from "leaflet";
import type { VehicleData } from "../services/ws-client";

// Route colors (deterministic from route number)
const COLORS = [
  "#e53935", "#d81b60", "#8e24aa", "#5e35b1", "#3949ab",
  "#1e88e5", "#039be5", "#00acc1", "#00897b", "#43a047",
  "#7cb342", "#c0ca33", "#fdd835", "#ffb300", "#fb8c00",
  "#f4511e", "#6d4c41", "#546e7a",
];

function routeColor(routeNum: string): string {
  let hash = 0;
  for (let i = 0; i < routeNum.length; i++) {
    hash = routeNum.charCodeAt(i) + ((hash << 5) - hash);
  }
  return COLORS[Math.abs(hash) % COLORS.length];
}

function createIcon(routeNum: string, course: number): L.DivIcon {
  const color = routeColor(routeNum);
  return L.divIcon({
    className: "",
    iconSize: [32, 32],
    iconAnchor: [16, 16],
    html: `
      <div class="tram-marker" style="background:${color};transform:rotate(${course}deg)">
        <span class="arrow"></span>
        <span style="transform:rotate(-${course}deg)">${routeNum}</span>
      </div>
    `,
  });
}

export class VehicleLayer {
  private markers: Map<string, L.Marker> = new Map();
  private layerGroup: L.LayerGroup;

  constructor(map: L.Map) {
    this.layerGroup = L.layerGroup().addTo(map);
  }

  update(vehicles: VehicleData[]): void {
    const seen = new Set<string>();

    for (const v of vehicles) {
      seen.add(v.id);
      const existing = this.markers.get(v.id);

      if (existing) {
        // Smooth slide to new position
        const marker = existing as any;
        if (typeof marker.slideTo === "function") {
          marker.slideTo([v.lat, v.lon], {
            duration: 9000,
            keepAtCenter: false,
          });
        } else {
          existing.setLatLng([v.lat, v.lon]);
        }
        existing.setIcon(createIcon(v.route, v.course));
        existing.unbindPopup();
        existing.bindPopup(this.popupHtml(v));
      } else {
        const marker = L.marker([v.lat, v.lon], {
          icon: createIcon(v.route, v.course),
        });
        marker.bindPopup(this.popupHtml(v));
        marker.addTo(this.layerGroup);
        this.markers.set(v.id, marker);
      }
    }

    // Remove stale markers
    for (const [id, marker] of this.markers) {
      if (!seen.has(id)) {
        this.layerGroup.removeLayer(marker);
        this.markers.delete(id);
      }
    }
  }

  private popupHtml(v: VehicleData): string {
    const nextStop = v.next_stops[0];
    const eta = nextStop?.eta_seconds
      ? `${Math.ceil(nextStop.eta_seconds / 60)} мин`
      : "";
    return `
      <div style="font-family:sans-serif;font-size:13px;min-width:160px">
        <b>Маршрут ${v.route}</b> (${v.board_num})<br/>
        Скорость: ${v.speed.toFixed(0)} км/ч<br/>
        ${v.prev_stop ? `От: ${v.prev_stop.name}<br/>` : ""}
        ${nextStop ? `До: <b>${nextStop.name}</b> ${eta ? `(~${eta})` : ""}<br/>` : ""}
      </div>
    `;
  }

  clear(): void {
    this.layerGroup.clearLayers();
    this.markers.clear();
  }
}
