/** Manages route polylines on the map. */

import L from "leaflet";
import type { RouteInfo } from "../services/api-client";
import { routeColor } from "./vehicle-layer";

export class RouteLayer {
  private polylines: Map<number, L.Polyline> = new Map();
  private layerGroup: L.LayerGroup;

  constructor(map: L.Map) {
    this.layerGroup = L.layerGroup().addTo(map);
  }

  loadRoutes(routes: RouteInfo[]): void {
    this.layerGroup.clearLayers();
    this.polylines.clear();

    for (const route of routes) {
      if (!route.geometry || route.geometry.length < 2) continue;

      const latlngs: L.LatLngTuple[] = route.geometry.map(
        (p) => [p[0], p[1]] as L.LatLngTuple
      );

      const color = routeColor(route.number);
      const polyline = L.polyline(latlngs, {
        color,
        weight: 3,
        opacity: 0.5,
        smoothFactor: 1,
      });

      polyline.bindTooltip(`Маршрут ${route.number}`, {
        sticky: true,
        className: "route-tooltip",
      });

      polyline.addTo(this.layerGroup);
      this.polylines.set(route.id, polyline);
    }
  }

  /** Show/hide routes based on the enabled set of route numbers. */
  setVisibility(routes: RouteInfo[], enabledRoutes: Set<string>): void {
    for (const route of routes) {
      const polyline = this.polylines.get(route.id);
      if (!polyline) continue;

      if (enabledRoutes.has(route.number)) {
        if (!this.layerGroup.hasLayer(polyline)) {
          this.layerGroup.addLayer(polyline);
        }
      } else {
        if (this.layerGroup.hasLayer(polyline)) {
          this.layerGroup.removeLayer(polyline);
        }
      }
    }
  }
}
