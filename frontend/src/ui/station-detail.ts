/** Renders station arrivals detail. */

import { getStopArrivals, type StopInfo } from "../services/api-client";
import { store } from "../services/state";
import { flyTo } from "../map/map-controller";

function formatEta(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  if (seconds < 60) return "<1 мин";
  return `${Math.ceil(seconds / 60)} мин`;
}

export function renderStopSearch(
  searchInput: HTMLInputElement,
  container: HTMLElement
): void {
  searchInput.addEventListener("input", () => {
    const q = searchInput.value.toLowerCase().trim();
    if (q.length < 2) {
      container.innerHTML = "";
      return;
    }

    const matches = store.state.stops.filter((s) =>
      s.name.toLowerCase().includes(q)
    );

    container.innerHTML = "";
    for (const stop of matches.slice(0, 15)) {
      const el = document.createElement("div");
      el.className = "stop-result";
      el.textContent = stop.name;
      el.addEventListener("click", () => {
        store.selectStop(stop.id);
        searchInput.value = stop.name;
        flyTo(stop.lat, stop.lon, 16);
        renderStationDetail(container, stop);
      });
      container.appendChild(el);
    }
  });
}

export async function renderStationDetail(
  container: HTMLElement,
  stop: StopInfo
): Promise<void> {
  container.innerHTML = `
    <div class="station-name">${stop.name}</div>
    <div style="color:var(--text-muted);padding:12px">Загрузка...</div>
  `;

  try {
    const data = await getStopArrivals(stop.id);
    if (data.arrivals.length === 0) {
      container.innerHTML = `
        <div class="station-name">${stop.name}</div>
        <div style="color:var(--text-muted);padding:12px;text-align:center">Нет ближайших трамваев</div>
      `;
      return;
    }

    let html = `<div class="station-name">${stop.name}</div>`;
    for (const a of data.arrivals) {
      html += `
        <div class="arrival-row">
          <div>
            <span class="arrival-route" style="color:var(--accent)">${a.route}</span>
            <span style="color:var(--text-muted);font-size:12px;margin-left:6px">#${a.board_num}</span>
          </div>
          <div class="arrival-eta">${formatEta(a.eta_seconds)}</div>
        </div>
      `;
    }
    container.innerHTML = html;
  } catch {
    container.innerHTML = `
      <div class="station-name">${stop.name}</div>
      <div style="color:var(--accent);padding:12px">Ошибка загрузки</div>
    `;
  }
}

export function setupStationAutoRefresh(container: HTMLElement): void {
  // Auto-refresh arrivals every 15 seconds
  setInterval(async () => {
    const stopId = store.state.selectedStop;
    if (stopId == null) return;
    const stop = store.state.stops.find((s) => s.id === stopId);
    if (stop) {
      await renderStationDetail(container, stop);
    }
  }, 15000);
}
