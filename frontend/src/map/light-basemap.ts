import type L from "leaflet";
import { maplibreGL } from "@maplibre/maplibre-gl-leaflet";
import { setWorkerCount, setWorkerUrl, type StyleSpecification } from "maplibre-gl";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import "maplibre-gl/dist/maplibre-gl.css";
import { LIGHT_STYLE_URL, simplifyBasemap } from "./basemap-style";

setWorkerUrl(workerUrl);
setWorkerCount(1);

export async function addLightBasemap(map: L.Map): Promise<void> {
  const response = await fetch(LIGHT_STYLE_URL, { signal: AbortSignal.timeout(10000) });
  if (!response.ok) throw new Error(`Basemap style: HTTP ${response.status}`);
  const style = simplifyBasemap(await response.json() as StyleSpecification);
  const layer = maplibreGL({
    style,
    interactive: false,
    attributionControl: false, // Credits stay visible in the existing sidebar.
    pixelRatio: Math.min(window.devicePixelRatio || 1, 1.5),
    maxTileCacheSize: 96,
    fadeDuration: 0,
  });
  try {
    layer.addTo(map);
    const renderer = layer.getMaplibreMap();
    await new Promise<void>((resolve, reject) => {
      const timer = window.setTimeout(() => reject(new Error("Basemap load timed out")), 20000);
      const onLoad = () => { window.clearTimeout(timer); resolve(); };
      renderer.once("load", onLoad);
      if (renderer.loaded()) onLoad();
    });
  } catch (error) {
    // A failed GL context must not take the route/vehicle layers down with it.
    if (!layer.getMaplibreMap()) {
      layer.onRemove = function () { this.getContainer()?.remove(); return this; };
    }
    layer.remove();
    throw error;
  }
}
