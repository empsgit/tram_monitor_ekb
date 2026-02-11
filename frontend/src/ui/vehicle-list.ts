/** Renders the vehicle list in the side panel. */

import type { VehicleData } from "../services/ws-client";
import { flyTo } from "../map/map-controller";

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

function formatEta(seconds: number | null | undefined): string {
  if (seconds == null) return "";
  if (seconds < 60) return "<1 мин";
  return `${Math.ceil(seconds / 60)} мин`;
}

export function renderVehicleList(
  container: HTMLElement,
  vehicles: VehicleData[]
): void {
  // Sort by route number
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

    const nextStop = v.next_stops[0];
    const eta = formatEta(nextStop?.eta_seconds);

    card.innerHTML = `
      <div class="vehicle-card-header">
        <span class="route-badge" style="background:${routeColor(v.route)}">${v.route}</span>
        <span class="vehicle-speed">${v.speed.toFixed(0)} км/ч</span>
        <span class="vehicle-board">#${v.board_num}</span>
      </div>
      <div class="vehicle-stops">
        ${v.prev_stop ? `<span style="color:var(--text-muted)">${v.prev_stop.name}</span> → ` : ""}
        ${nextStop ? `<span class="next-stop">${nextStop.name}</span>${eta ? `<span class="eta-badge">${eta}</span>` : ""}` : '<span style="color:var(--text-muted)">на маршруте</span>'}
      </div>
    `;
    container.appendChild(card);
  }
}
