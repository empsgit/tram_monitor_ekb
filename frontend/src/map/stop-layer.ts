/** Manages stop markers on the map with visibility dimming. */

import L from "leaflet";
import type { StopInfo } from "../services/api-client";
import { store } from "../services/state";

const STOP_PANE = "stopPane";

export class StopLayer {
  private markers: Map<number, L.CircleMarker> = new Map();
  private layerGroup: L.LayerGroup;

  constructor(map: L.Map) {
    // Create a custom pane above overlayPane (z-index 400) so stops sit above route lines
    const pane = map.createPane(STOP_PANE);
    pane.style.zIndex = "450";
    this.layerGroup = L.layerGroup().addTo(map);
  }

  loadStops(stops: StopInfo[]): void {
    this.layerGroup.clearLayers();
    this.markers.clear();

    for (const stop of stops) {
      const marker = L.circleMarker([stop.lat, stop.lon], {
        pane: STOP_PANE,
        radius: 4,
        fillColor: "#60a5fa",
        fillOpacity: 0.9,
        color: "white",
        weight: 1.2,
      });

      const label = stop.direction
        ? `${stop.name} (${stop.direction})`
        : stop.name;
      marker.bindTooltip(label, {
        direction: "top",
        offset: [0, -8],
        className: "stop-tooltip",
      });

      marker.on("click", () => {
        store.selectStop(stop.id);
        document.querySelector('.tab[data-tab="station"]')?.dispatchEvent(
          new Event("click")
        );
      });

      marker.addTo(this.layerGroup);
      this.markers.set(stop.id, marker);
    }
  }

  /** Dim stops not in the active set. Pass null to show all normally. */
  setDimming(activeStopIds: Set<number> | null): void {
    for (const [id, m] of this.markers) {
      if (activeStopIds === null || activeStopIds.has(id)) {
        m.setStyle({ fillOpacity: 0.9, radius: 4, fillColor: "#60a5fa" });
      } else {
        m.setStyle({ fillOpacity: 0.15, radius: 3, fillColor: "#94a3b8" });
      }
    }
  }

  highlightStop(stopId: number): void {
    // Reset all to default (dimming will be re-applied by caller)
    for (const [, m] of this.markers) {
      m.setStyle({ radius: 4, fillColor: "#60a5fa", weight: 1.5 });
    }
    const selected = this.markers.get(stopId);
    if (selected) {
      selected.setStyle({
        radius: 7,
        fillColor: "#e94560",
        fillOpacity: 1,
        weight: 3,
      });
      selected.bringToFront();
    }
  }
}
