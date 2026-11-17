import type { StyleSpecification } from "maplibre-gl";

export const LIGHT_STYLE_URL = "https://tiles.openfreemap.org/styles/positron";

/** Only the base map changes. Leaflet's tram, stop and route overlays stay intact. */
export function simplifyBasemap(style: StyleSpecification): StyleSpecification {
  const allowed = new Set(["water", "waterway", "water_name", "transportation", "transportation_name"]);
  const layers = style.layers.filter((layer) => {
    if (layer.type === "background") return true;
    if (!("source-layer" in layer) || !allowed.has(layer["source-layer"] ?? "")) return false;
    return !/^(railway|highway_path|highway-name-path|highway-shield|road_shield)/.test(layer.id);
  }).map((layer) => {
    if (layer.type === "symbol" && layer.layout?.["text-field"]) {
      const minzoom = layer.id === "highway-name-major" ? 11
        : layer.id === "highway-name-minor" ? 13 : layer.minzoom;
      return { ...layer, minzoom, layout: { ...layer.layout,
        "text-field": ["coalesce", ["get", "name:ru"], ["get", "name"], ["get", "name:latin"]] } };
    }
    if (layer.type === "fill" && layer.id === "water") {
      return { ...layer, paint: { ...layer.paint, "fill-color": "#c6dfe8" } };
    }
    if (layer.type === "line" && layer.id === "waterway") {
      return { ...layer, paint: { ...layer.paint, "line-color": "#b4d3df" } };
    }
    return layer;
  }) as StyleSpecification["layers"];
  const usedSources = new Set(layers.flatMap((layer) => "source" in layer ? [layer.source] : []));
  return { ...style, layers,
    sprite: layers.some((layer) => layer.type === "symbol" && layer.layout?.["icon-image"]) ? style.sprite : undefined,
    sources: Object.fromEntries(Object.entries(style.sources).filter(([id]) => usedSources.has(id))) };
}
