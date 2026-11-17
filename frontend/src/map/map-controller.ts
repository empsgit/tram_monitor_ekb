/** Initialize and control the Leaflet map. */

import L from "leaflet";

// Yekaterinburg center coordinates
const EKB_CENTER: L.LatLngTuple = [56.8389, 60.597];
const DEFAULT_ZOOM = 13;

let map: L.Map;

export function initMap(): L.Map {
  map = L.map("map", {
    center: EKB_CENTER,
    zoom: DEFAULT_ZOOM,
    minZoom: 2,
    maxZoom: 19,
    maxBounds: [[-85, -180], [85, 180]],
    maxBoundsViscosity: 1,
    zoomControl: true,
    attributionControl: false,
  });

  // Keep vehicle/UI startup independent of the vector renderer and tile requests.
  void import("./light-basemap")
    .then(({ addLightBasemap }) => addLightBasemap(map))
    .catch((error) => {
      console.warn("Light basemap unavailable, using OSM fallback:", error);
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      }).addTo(map);
    });

  return map;
}

export function getMap(): L.Map {
  return map;
}

export function flyTo(lat: number, lon: number, zoom = 16): void {
  map.flyTo([lat, lon], zoom, { duration: 0.8 });
}
