/** Manages stop markers on the map. */

import L from "leaflet";
import type { StopInfo } from "../services/api-client";
import { store } from "../services/state";

export class StopLayer {
  private markers: Map<number, L.CircleMarker> = new Map();
  private layerGroup: L.LayerGroup;

  constructor(map: L.Map) {
    this.layerGroup = L.layerGroup().addTo(map);
  }

  loadStops(stops: StopInfo[]): void {
    this.layerGroup.clearLayers();
    this.markers.clear();

    for (const stop of stops) {
      const marker = L.circleMarker([stop.lat, stop.lon], {
        radius: 4,
        fillColor: "#60a5fa",
        fillOpacity: 0.8,
        color: "white",
        weight: 1.5,
      });

      marker.bindTooltip(stop.name, {
        direction: "top",
        offset: [0, -6],
        className: "stop-tooltip",
      });

      marker.on("click", () => {
        store.selectStop(stop.id);
        // Switch to station tab
        document.querySelector('.tab[data-tab="station"]')?.dispatchEvent(
          new Event("click")
        );
      });

      marker.addTo(this.layerGroup);
      this.markers.set(stop.id, marker);
    }
  }

  highlightStop(stopId: number): void {
    // Reset all
    for (const [id, m] of this.markers) {
      m.setStyle({
        radius: 4,
        fillColor: "#60a5fa",
        weight: 1.5,
      });
    }
    // Highlight selected
    const selected = this.markers.get(stopId);
    if (selected) {
      selected.setStyle({
        radius: 8,
        fillColor: "#e94560",
        weight: 3,
      });
      selected.bringToFront();
    }
  }
}
