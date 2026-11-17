/** Simple reactive state store. */

import type { VehicleData } from "./ws-client";
import type { RouteInfo, StopInfo } from "./api-client";

export type StateChange = "vehicles" | "routes" | "stops" | "filter" | "selection" | "highlight" | "connection";
export type StateListener = (change: StateChange) => void;

export interface AppState {
  vehicles: Map<string, VehicleData>;
  routes: RouteInfo[];
  stops: StopInfo[];
  enabledRoutes: Set<string>; // route numbers
  selectedStop: number | null;
  highlightedVehicleIds: Set<string>;
  connected: boolean;
}

export class Store {
  state: AppState = {
    vehicles: new Map(),
    routes: [],
    stops: [],
    enabledRoutes: new Set(),
    selectedStop: null,
    highlightedVehicleIds: new Set(),
    connected: false,
  };

  private listeners: Set<StateListener> = new Set();

  subscribe(listener: StateListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private notify(change: StateChange): void {
    for (const l of this.listeners) l(change);
  }

  setConnected(connected: boolean): void {
    this.state.connected = connected;
    this.notify("connection");
  }

  updateVehicles(vehicles: VehicleData[]): void {
    const fresh = new Map<string, VehicleData>();
    for (const v of vehicles) {
      fresh.set(v.id, v);
    }
    this.state.vehicles = fresh;
    this.notify("vehicles");
  }

  setRoutes(routes: RouteInfo[]): void {
    const firstLoad = this.state.routes.length === 0;
    const previous = this.state.enabledRoutes;
    this.state.routes = routes;
    this.state.enabledRoutes = new Set(routes.filter((r) => firstLoad || previous.has(r.number)).map((r) => r.number));
    this.notify("routes");
  }

  setStops(stops: StopInfo[]): void {
    this.state.stops = stops;
    this.notify("stops");
  }

  toggleRoute(routeNum: string): void {
    if (this.state.enabledRoutes.has(routeNum)) {
      this.state.enabledRoutes.delete(routeNum);
    } else {
      this.state.enabledRoutes.add(routeNum);
    }
    this.notify("filter");
  }

  enableAllRoutes(): void {
    this.state.enabledRoutes = new Set(this.state.routes.map((r) => r.number));
    this.notify("filter");
  }

  disableAllRoutes(): void {
    this.state.enabledRoutes.clear();
    this.notify("filter");
  }

  selectStop(stopId: number | null): void {
    if (this.state.selectedStop === stopId) return;
    this.state.selectedStop = stopId;
    this.state.highlightedVehicleIds = new Set();
    this.notify("selection");
  }

  setHighlightedVehicles(vehicleIds: Iterable<string> | null): void {
    this.state.highlightedVehicleIds = vehicleIds
      ? new Set(vehicleIds)
      : new Set();
    this.notify("highlight");
  }

  getVisibleVehicles(): VehicleData[] {
    const result: VehicleData[] = [];
    for (const v of this.state.vehicles.values()) {
      if (this.state.enabledRoutes.has(v.route)) {
        result.push(v);
      }
    }
    return result;
  }

  /** Returns stop IDs that belong to at least one enabled route, or null if all routes are enabled. */
  getActiveStopIds(): Set<number> | null {
    const allEnabled = this.state.routes.every((r) =>
      this.state.enabledRoutes.has(r.number)
    );
    if (allEnabled) return null;

    const ids = new Set<number>();
    for (const route of this.state.routes) {
      if (this.state.enabledRoutes.has(route.number)) {
        for (const id of route.stop_ids) {
          ids.add(id);
        }
      }
    }
    return ids;
  }
}

export const store = new Store();
