/** Main entry point: initialize map, connect WebSocket, wire up UI. */

import { initMap } from "./map/map-controller";
import { VehicleLayer } from "./map/vehicle-layer";
import { StopLayer } from "./map/stop-layer";
import { RouteLayer } from "./map/route-layer";
import { WsClient } from "./services/ws-client";
import { getRoutes, getStops } from "./services/api-client";
import { store } from "./services/state";
import { renderVehicleList } from "./ui/vehicle-list";
import {
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
      document.getElementById(`tab-${target}`)!.classList.add("active");
    });
  });

  // Mobile panel handle
  const panel = document.getElementById("panel")!;
  const panelHandle = document.getElementById("panel-handle")!;
  panelHandle.addEventListener("click", () => {
    panel.classList.toggle("expanded");
  });

  // Load routes and stops independently (one failure shouldn't block the other)
  try {
    const routes = await getRoutes();
    store.setRoutes(routes);
    routeLayer.loadRoutes(routes);
    vehicleLayer.loadRoutes(routes);
    renderRouteFilter(routeFilterEl, routes);
  } catch (e) {
    console.error("Failed to load routes:", e);
  }
  try {
    const stops = await getStops();
    store.setStops(stops);
    stopLayer.loadStops(stops);
  } catch (e) {
    console.error("Failed to load stops:", e);
  }

  // Set up stop search
  renderStopSearch(stopSearchEl, stationDetailEl);
  setupStationAutoRefresh(stationDetailEl);

  // WebSocket connection
  const wsClient = new WsClient();
  // Do not render potentially stale Redis snapshot on (re)connect.
  // We show vehicles only after receiving a live `update` frame.
  let hasFreshVehicleUpdate = false;

  const SNAPSHOT_MAX_AGE_MS = 20_000;

  const isFreshSnapshot = (ts: string | null): boolean => {
    if (!ts) return false;
    const parsed = Date.parse(ts);
    if (Number.isNaN(parsed)) return false;
    return Date.now() - parsed <= SNAPSHOT_MAX_AGE_MS;
  };

  wsClient.onStatusChange = (connected) => {
    store.setConnected(connected);
    statusDot.classList.toggle("connected", connected);
    statusText.textContent = connected ? "Онлайн" : "Подключение...";

    if (!connected) {
      hasFreshVehicleUpdate = false;
      store.updateVehicles([]);
    }
  };

  wsClient.subscribe((msg) => {
    if (msg.type === "update") {
      hasFreshVehicleUpdate = true;
      store.updateVehicles(msg.vehicles);
      return;
    }

    // Accept startup snapshot only if it is fresh by API vehicle timestamps.
    if (hasFreshVehicleUpdate) {
      store.updateVehicles(msg.vehicles);
      return;
    }

    const hasVehicles = msg.vehicles.length > 0;
    const snapshotIsFresh = hasVehicles && msg.vehicles.every((v) => isFreshSnapshot(v.timestamp));
    if (snapshotIsFresh) {
      store.updateVehicles(msg.vehicles);
    }
  });

  wsClient.connect();

  // Re-render when route filter changes
  store.subscribe(() => {
    const visible = store.getVisibleVehicles();
    vehicleLayer.update(visible);
    renderVehicleList(vehicleListEl, visible);
    vehicleCount.textContent = `${visible.length} трамваев`;

    // Update route line visibility
    routeLayer.setVisibility(store.state.routes, store.state.enabledRoutes);

    // Dim stops not on selected routes
    stopLayer.setDimming(store.getActiveStopIds());

    // Highlight selected stop
    if (store.state.selectedStop) {
      stopLayer.highlightStop(store.state.selectedStop);
    }
  });
}

main().catch(console.error);
