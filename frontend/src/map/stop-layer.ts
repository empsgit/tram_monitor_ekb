/** Manages stop markers on the map with visibility dimming. */

import L from "leaflet";
import type { StopInfo } from "../services/api-client";
import { store } from "../services/state";

const STOP_PANE = "stopPane";

export class StopLayer {
  private markers: Map<number, L.CircleMarker> = new Map();
  private layerGroup: L.LayerGroup;
  private activeIds: Set<number> | null = null;
  private selectedId: number | null = null;

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
        if (store.state.selectedStop === stop.id) {
          store.selectStop(null);
        } else {
          store.selectStop(stop.id);
          document.querySelector('.tab[data-tab="station"]')?.dispatchEvent(
            new Event("click")
          );
        }
      });

      marker.addTo(this.layerGroup);
      this.markers.set(stop.id, marker);
    }
  }

  /** Dim stops not in the active set. Pass null to show all normally. */
  setDimming(activeStopIds: Set<number> | null): void {
    const previous = this.activeIds;
    this.activeIds = activeStopIds;
    for (const id of this.markers.keys()) {
      if ((previous === null || previous.has(id)) !== (activeStopIds === null || activeStopIds.has(id))) {
        this.styleStop(id);
      }
    }
  }

  highlightStop(stopId: number | null): void {
    const previous = this.selectedId;
    this.selectedId = stopId;
    if (previous !== null) this.styleStop(previous);
    if (stopId !== null) this.styleStop(stopId);
  }

  private styleStop(id: number): void {
    const marker = this.markers.get(id);
    if (!marker) return;
    const selected = id === this.selectedId;
    const active = this.activeIds === null || this.activeIds.has(id);
    marker.setStyle({
      radius: selected ? 7 : active ? 4 : 3,
      fillColor: selected ? "#e94560" : active ? "#60a5fa" : "#94a3b8",
      fillOpacity: selected ? 1 : active ? 0.9 : 0.15,
      weight: selected ? 3 : 1.2,
    });
    if (selected) marker.bringToFront();
  }
}
