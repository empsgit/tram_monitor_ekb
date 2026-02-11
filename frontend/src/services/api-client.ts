/** REST API client for routes, stops, etc. */

export interface RouteInfo {
  id: number;
  number: string;
  name: string;
  color: string;
}

export interface StopInfo {
  id: number;
  name: string;
  lat: number;
  lon: number;
}

export interface StopArrivals {
  stop_id: number;
  stop_name: string;
  arrivals: Array<{
    vehicle_id: string;
    board_num: string;
    route: string;
    route_id: number;
    eta_seconds: number | null;
  }>;
}

const BASE = "/api";

async function fetchJson<T>(path: string): Promise<T> {
  const resp = await fetch(`${BASE}${path}`);
  if (!resp.ok) throw new Error(`API error ${resp.status}: ${path}`);
  return resp.json();
}

export async function getRoutes(): Promise<RouteInfo[]> {
  return fetchJson("/routes");
}

export async function getStops(): Promise<StopInfo[]> {
  return fetchJson("/stops");
}

export async function getStopArrivals(
  stopId: number,
  route?: number
): Promise<StopArrivals> {
  const params = route ? `?route=${route}` : "";
  return fetchJson(`/stops/${stopId}/arrivals${params}`);
}
