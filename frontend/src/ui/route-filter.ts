/** Route filter chips. */

import type { RouteInfo } from "../services/api-client";
import { store } from "../services/state";

const COLORS = [
  "#e53935", "#d81b60", "#8e24aa", "#5e35b1", "#3949ab",
  "#1e88e5", "#039be5", "#00acc1", "#00897b", "#43a047",
  "#7cb342", "#c0ca33", "#fdd835", "#ffb300", "#fb8c00",
  "#f4511e", "#6d4c41", "#546e7a",
];

function routeColor(routeNum: string): string {
  let hash = 0;
  for (let i = 0; i < routeNum.length; i++) {
    hash = routeNum.charCodeAt(i) + ((hash << 5) - hash);
  }
  return COLORS[Math.abs(hash) % COLORS.length];
}

export function renderRouteFilter(
  container: HTMLElement,
  routes: RouteInfo[]
): void {
  container.innerHTML = "";

  const sorted = [...routes].sort((a, b) =>
    a.number.localeCompare(b.number, undefined, { numeric: true })
  );

  for (const route of sorted) {
    const chip = document.createElement("div");
    chip.className = "route-chip";
    chip.style.background = routeColor(route.number);
    chip.textContent = route.number;
    chip.title = route.name;

    const updateState = () => {
      chip.classList.toggle(
        "disabled",
        !store.state.enabledRoutes.has(route.number)
      );
    };

    chip.addEventListener("click", () => {
      store.toggleRoute(route.number);
      updateState();
    });

    updateState();
    container.appendChild(chip);
  }
}
