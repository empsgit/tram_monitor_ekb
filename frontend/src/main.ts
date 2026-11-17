/** Main entry point: initialize map, connect WebSocket, wire up UI. */

import "leaflet/dist/leaflet.css";
import "./styles/main.css";

import { initMap } from "./map/map-controller";
import { VehicleLayer } from "./map/vehicle-layer";
import { StopLayer } from "./map/stop-layer";
import { RouteLayer } from "./map/route-layer";
import { WsClient } from "./services/ws-client";
import { getRoutes, getStops } from "./services/api-client";
import { store } from "./services/state";
import { renderVehicleList } from "./ui/vehicle-list";
import {
  cancelStationDetail,
  renderStopSearch,
  renderStationDetail,
  setupStationAutoRefresh,
} from "./ui/station-detail";
import { renderRouteFilter } from "./ui/route-filter";

async function main() {
  const map = initMap();
  const vehicleLayer = new VehicleLayer(map);
  const stopLayer = new StopLayer(map);
  const routeLayer = new RouteLayer(map);

  const statusDot = document.getElementById("status-dot")!;
  const statusText = document.getElementById("status-text")!;
  const vehicleCount = document.getElementById("vehicle-count")!;
  const vehicleListEl = document.getElementById("vehicle-list")!;
  const stationDetailEl = document.getElementById("station-detail")!;
  const stopSearchEl = document.getElementById("stop-search") as HTMLInputElement;
  const routeFilterEl = document.getElementById("route-filter")!;

  // Tabs
  const tabs = document.querySelectorAll<HTMLElement>(".tab");
  const tabContents = document.querySelectorAll<HTMLElement>(".tab-content");
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      tabs.forEach((t) => t.classList.remove("active"));
      tabContents.forEach((tc) => tc.classList.remove("active"));
      tab.classList.add("active");
      const target = tab.dataset.tab!;
      if (target === "list") renderVehicleList(vehicleListEl, store.getVisibleVehicles());
      document.getElementById(`tab-${target}`)!.classList.add("active");
    });
  });

  // Mobile panel handle
  const panel = document.getElementById("panel")!;
  const panelHandle = document.getElementById("panel-handle")!;
  panelHandle.addEventListener("click", () => {
    panel.classList.toggle("expanded");
  });

  // Set up stop search
  renderStopSearch(stopSearchEl, stationDetailEl);
  setupStationAutoRefresh(stationDetailEl);

  // WebSocket connection
  const wsClient = new WsClient();
  wsClient.onStatusChange = (connected) => {
    store.setConnected(connected);
    statusDot.classList.toggle("connected", connected);
    statusText.textContent = connected ? "Онлайн" : "Подключение...";

    if (!connected) {
      store.updateVehicles([]);
    }
  };

  wsClient.subscribe((msg) => store.updateVehicles(msg.vehicles));

  let lastSelectedStop: number | null = null;

  const listIsVisible = () => document.getElementById("tab-list")?.classList.contains("active") && !document.hidden;
  store.subscribe((change) => {
    if (change === "vehicles" || change === "filter" || change === "routes") {
      const visible = store.getVisibleVehicles();
      vehicleLayer.update(visible);
      vehicleLayer.setHighlightedVehicles(store.state.highlightedVehicleIds);
      if (listIsVisible()) renderVehicleList(vehicleListEl, visible);
      vehicleCount.textContent = `${visible.length} трамваев`;
    }
    if (change === "selection" || change === "highlight") {
      vehicleLayer.setHighlightedVehicles(store.state.highlightedVehicleIds);
    }
    if (change === "filter" || change === "routes" || change === "stops") {
      routeLayer.setVisibility(store.state.routes, store.state.enabledRoutes);
      stopLayer.setDimming(store.getActiveStopIds());
    }
    if (change === "selection") {
      stopLayer.highlightStop(store.state.selectedStop);
      cancelStationDetail();
      lastSelectedStop = store.state.selectedStop;
      const stop = store.state.stops.find((s) => s.id === lastSelectedStop);
      if (stop) void renderStationDetail(stationDetailEl, stop);
      else stationDetailEl.innerHTML = '<div style="color:var(--text-muted);padding:12px;text-align:center">Остановка не выбрана</div>';
    }
  });
  document.addEventListener("visibilitychange", () => {
    if (listIsVisible()) renderVehicleList(vehicleListEl, store.getVisibleVehicles());
  });
  wsClient.connect();

  // Fetch independently and concurrently; start live updates without waiting on REST.
  await Promise.allSettled([
    getRoutes().then((routes) => {
      routeLayer.loadRoutes(routes);
      vehicleLayer.loadRoutes(routes);
      store.setRoutes(routes);
      renderRouteFilter(routeFilterEl, routes);
    }).catch((e) => console.error("Failed to load routes:", e)),
    getStops().then((stops) => {
      stopLayer.loadStops(stops);
      store.setStops(stops);
    }).catch((e) => console.error("Failed to load stops:", e)),
  ]);
}

main().catch(console.error);
