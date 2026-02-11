/** Renders the vehicle list in the side panel. */

import type { VehicleData } from "../services/ws-client";
import { flyTo } from "../map/map-controller";
import { routeColor } from "../map/vehicle-layer";

function formatEta(seconds: number | null | undefined): string {
  if (seconds == null) return "";
  if (seconds < 60) return "<1 мин";
  return `${Math.ceil(seconds / 60)} мин`;
}

export function renderVehicleList(
  container: HTMLElement,
  vehicles: VehicleData[]
): void {
  const sorted = [...vehicles].sort((a, b) =>
    a.route.localeCompare(b.route, undefined, { numeric: true })
  );

  container.innerHTML = sorted.length === 0
    ? '<div style="color:var(--text-muted);text-align:center;padding:40px">Нет активных трамваев</div>'
    : "";

  for (const v of sorted) {
    const card = document.createElement("div");
    card.className = "vehicle-card";
    card.addEventListener("click", () => flyTo(v.lat, v.lon));

    const stopsHtml = v.next_stops.slice(0, 3).map((ns, i) => {
      const eta = formatEta(ns.eta_seconds);
      const cls = i === 0 ? "next-stop" : "next-stop-dim";
      return `<div class="stop-row">
        <span class="${cls}">${ns.name}</span>
        ${eta ? `<span class="eta-badge">${eta}</span>` : ""}
      </div>`;
    }).join("");

    card.innerHTML = `
      <div class="vehicle-card-header">
        <span class="route-badge" style="background:${routeColor(v.route)}">${v.route}</span>
        <span class="vehicle-speed">${v.speed.toFixed(0)} км/ч</span>
        <span class="vehicle-board">#${v.board_num}</span>
      </div>
      <div class="vehicle-stops">
        ${v.prev_stop ? `<div class="prev-stop-label">от: ${v.prev_stop.name}</div>` : ""}
        ${stopsHtml || '<span style="color:var(--text-muted)">на маршруте</span>'}
      </div>
    `;
    container.appendChild(card);
  }
}
