import test from "node:test";
import assert from "node:assert/strict";
import { simplifyBasemap, LIGHT_STYLE_URL } from "../src/map/basemap-style.ts";

test("free basemap keeps streets, Russian labels and water, not visual clutter", () => {
  const style = { version: 8,
    sources: { openmaptiles: { type: "vector", url: "https://tiles.openfreemap.org/planet" }, unused: { type: "raster" } },
    layers: [
      { id: "background", type: "background" },
      { id: "water", type: "fill", source: "openmaptiles", "source-layer": "water" },
      { id: "highway_minor", type: "line", source: "openmaptiles", "source-layer": "transportation" },
      { id: "highway-name-minor", type: "symbol", source: "openmaptiles", "source-layer": "transportation_name", layout: { "text-field": "{name_en}" } },
      { id: "building", type: "fill", source: "openmaptiles", "source-layer": "building" },
      { id: "poi", type: "symbol", source: "openmaptiles", "source-layer": "poi" },
      { id: "railway", type: "line", source: "openmaptiles", "source-layer": "transportation" },
      { id: "label_other", type: "symbol", source: "openmaptiles", "source-layer": "place" },
    ],
  };
  const before = JSON.stringify(style);
  const result = simplifyBasemap(style);
  assert.deepEqual(result.layers.map((l) => l.id), ["background", "water", "highway_minor", "highway-name-minor"]);
  assert.deepEqual(Object.keys(result.sources), ["openmaptiles"]);
  assert.deepEqual(result.layers[3].layout["text-field"].slice(0, 2), ["coalesce", ["get", "name:ru"]]);
  assert.equal(result.layers[1].paint["fill-color"], "#c6dfe8");
  assert.equal(result.layers[3].minzoom, 13);
  assert.equal(result.sprite, undefined);
  assert.equal(JSON.stringify(style), before);
  assert.equal(new URL(LIGHT_STYLE_URL).search, "");
});
