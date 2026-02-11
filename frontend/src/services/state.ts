/** Simple reactive state store. */

import type { VehicleData } from "./ws-client";
import type { RouteInfo, StopInfo } from "./api-client";

export type StateListener = () => void;

export interface AppState {
  vehicles: Map<string, VehicleData>;
  routes: RouteInfo[];
  stops: StopInfo[];
  enabledRoutes: Set<string>; // route numbers
  selectedStop: number | null;
  connected: boolean;
}

class Store {
  state: AppState = {
    vehicles: new Map(),
    routes: [],
    stops: [],
    enabledRoutes: new Set(),
    selectedStop: null,
    connected: false,
  };

  private listeners: Set<StateListener> = new Set();

  subscribe(listener: StateListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private notify(): void {
    for (const l of this.listeners) l();
  }

  setConnected(connected: boolean): void {
    this.state.connected = connected;
    this.notify();
  }

  updateVehicles(vehicles: VehicleData[]): void {
    for (const v of vehicles) {
      this.state.vehicles.set(v.id, v);
    }
    this.notify();
  }

  setRoutes(routes: RouteInfo[]): void {
    this.state.routes = routes;
    this.state.enabledRoutes = new Set(routes.map((r) => r.number));
    this.notify();
  }

  setStops(stops: StopInfo[]): void {
    this.state.stops = stops;
    this.notify();
  }

  toggleRoute(routeNum: string): void {
    if (this.state.enabledRoutes.has(routeNum)) {
      this.state.enabledRoutes.delete(routeNum);
    } else {
      this.state.enabledRoutes.add(routeNum);
    }
    this.notify();
  }

  enableAllRoutes(): void {
    this.state.enabledRoutes = new Set(this.state.routes.map((r) => r.number));
    this.notify();
  }

  disableAllRoutes(): void {
    this.state.enabledRoutes.clear();
    this.notify();
  }

  selectStop(stopId: number | null): void {
    this.state.selectedStop = stopId;
    this.notify();
  }

  getVisibleVehicles(): VehicleData[] {
    const result: VehicleData[] = [];
    const hasFilter = this.state.enabledRoutes.size > 0;
    for (const v of this.state.vehicles.values()) {
      if (!hasFilter || this.state.enabledRoutes.has(v.route)) {
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
