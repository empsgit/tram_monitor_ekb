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
    zoomControl: true,
    attributionControl: false,
  });

  // Light-themed tiles (CartoDB Positron)
  L.tileLayer(
    "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
    {
      attribution:
        '',
      subdomains: "abcd",
      maxZoom: 19,
    }
  ).addTo(map);

  return map;
}

export function getMap(): L.Map {
  return map;
}

export function flyTo(lat: number, lon: number, zoom = 16): void {
  map.flyTo([lat, lon], zoom, { duration: 0.8 });
}
