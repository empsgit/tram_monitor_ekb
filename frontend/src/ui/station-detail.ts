/** Renders station arrivals detail. */

import { getStopArrivals, type StopInfo, type StopArrivals } from "../services/api-client";
import { LatestRequest } from "../services/latest-request";
import { store } from "../services/state";
import { flyTo } from "../map/map-controller";

function formatEta(seconds: number | null | undefined, signalLost = false): string {
  if (signalLost) return "Нет сигнала";
  if (seconds == null) return "—";
  if (seconds < 60) return "<1 мин";
  return `${Math.ceil(seconds / 60)} мин`;
}

function stopDisplayName(stop: StopInfo): string {
  return stop.direction ? `${stop.name} (${stop.direction})` : stop.name;
}

function stationHeader(stop: StopInfo): string {
  return `
    <div class="station-header-row">
      <div class="station-name">${escapeHtml(stopDisplayName(stop))}</div>
      <button class="station-clear-btn" type="button" data-action="clear-stop">Сбросить</button>
    </div>
  `;
}

function bindClearSelection(container: HTMLElement): void {
  const clearBtn = container.querySelector<HTMLButtonElement>('[data-action="clear-stop"]');
  if (!clearBtn) return;
  clearBtn.addEventListener("click", () => {
    store.selectStop(null);
  });
}

export function renderStopSearch(
  searchInput: HTMLInputElement,
  container: HTMLElement
): void {
  searchInput.addEventListener("input", () => {
    store.selectStop(null);
    cancelStationDetail();
    const q = searchInput.value.toLowerCase().trim();
    if (q.length < 2) {
      container.innerHTML = "";
      return;
    }

    const matches = store.state.stops.filter((s) =>
      s.name.toLowerCase().includes(q) ||
      (s.direction && s.direction.toLowerCase().includes(q))
    );

    container.innerHTML = "";
    for (const stop of matches.slice(0, 15)) {
      const displayName = stopDisplayName(stop);
      const el = document.createElement("div");
      el.className = "stop-result";
      el.textContent = displayName;
      el.addEventListener("click", () => {
        store.selectStop(stop.id);
        searchInput.value = displayName;
        flyTo(stop.lat, stop.lon, 16);
      });
      container.appendChild(el);
    }
  });
}

const requests = new LatestRequest();
const cache = new Map<number, { data: StopArrivals; receivedAt: number }>();
let pendingStop: number | null = null;
let displayedStop: number | null = null;

export function cancelStationDetail(): void {
  requests.cancel();
  pendingStop = null;
  displayedStop = null;
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));
}

function paint(container: HTMLElement, stop: StopInfo, data: StopArrivals): void {
  store.setHighlightedVehicles(data.arrivals.map((a) => a.vehicle_id));
  let html = stationHeader(stop);
  if (!data.arrivals.length) {
    html += '<div style="color:var(--text-muted);padding:12px">Нет ближайших трамваев</div>';
  }
  for (const a of data.arrivals) {
    const source = a.eta_source === "history" ? "По истории" : a.eta_source === "mixed" ? "Частично по истории" : "Оценка, мало истории";
    const range = a.eta_min_seconds != null && a.eta_max_seconds != null && !a.signal_lost
      ? `, ${Math.floor(a.eta_min_seconds / 60)}-${Math.ceil(a.eta_max_seconds / 60)} мин` : "";
    html += `<div class="arrival-row">
      <div><span class="arrival-route" style="color:var(--accent)">${escapeHtml(a.route)}</span>
      <span style="color:var(--text-muted);font-size:12px;margin-left:6px">#${escapeHtml(a.board_num)}</span></div>
      <div class="arrival-eta" title="${source}${range}">${formatEta(a.eta_seconds, a.signal_lost)}
      <div style="font-size:10px;color:var(--text-muted)">${a.signal_lost ? "" : source}</div></div>
    </div>`;
  }
  container.innerHTML = html;
  bindClearSelection(container);
  displayedStop = stop.id;
}

export async function renderStationDetail(container: HTMLElement, stop: StopInfo, refresh = false): Promise<void> {
  if (store.state.selectedStop !== stop.id || pendingStop === stop.id) return;
  const cached = cache.get(stop.id);
  if (displayedStop !== stop.id) {
    if (cached) paint(container, stop, cached.data);
    else {
      container.innerHTML = `${stationHeader(stop)}<div style="color:var(--text-muted);padding:12px">Загрузка...</div>`;
      bindClearSelection(container);
      displayedStop = stop.id;
    }
  }
  if (!refresh && cached && Date.now() - cached.receivedAt < 10000) return;
  const request = requests.start();
  pendingStop = stop.id;
  try {
    const data = await getStopArrivals(stop.id, undefined, request.signal);
    if (!request.isCurrent() || store.state.selectedStop !== stop.id) return;
    cache.delete(stop.id);
    cache.set(stop.id, { data, receivedAt: Date.now() });
    if (cache.size > 100) cache.delete(cache.keys().next().value!);
    paint(container, stop, data);
  } catch {
    if (!request.isCurrent() || store.state.selectedStop !== stop.id) return;
    if (cached) paint(container, stop, cached.data);
    else {
      store.setHighlightedVehicles(null);
      container.innerHTML = stationHeader(stop);
      bindClearSelection(container);
    }
    container.insertAdjacentHTML("beforeend", '<div style="color:var(--accent);padding:12px">Не удалось обновить данные. Повторим запрос.</div>');
  } finally {
    if (request.isCurrent()) pendingStop = null;
  }
}

export function setupStationAutoRefresh(container: HTMLElement): void {
  setInterval(() => {
    if (document.hidden) return;
    const stop = store.state.stops.find((s) => s.id === store.state.selectedStop);
    if (stop) void renderStationDetail(container, stop, true);
  }, 15000);
}
