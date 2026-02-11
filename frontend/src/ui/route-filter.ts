/** Route filter chips with select/deselect all. */

import type { RouteInfo } from "../services/api-client";
import { store } from "../services/state";
import { routeColor } from "../map/vehicle-layer";

export function renderRouteFilter(
  container: HTMLElement,
  routes: RouteInfo[]
): void {
  container.innerHTML = "";

  // Action buttons row
  const actions = document.createElement("div");
  actions.className = "route-filter-actions";

  const selectAllBtn = document.createElement("button");
  selectAllBtn.className = "filter-btn";
  selectAllBtn.textContent = "Выбрать все";
  selectAllBtn.addEventListener("click", () => {
    store.enableAllRoutes();
    updateAllChips();
  });

  const deselectAllBtn = document.createElement("button");
  deselectAllBtn.className = "filter-btn";
  deselectAllBtn.textContent = "Снять все";
  deselectAllBtn.addEventListener("click", () => {
    store.disableAllRoutes();
    updateAllChips();
  });

  actions.appendChild(selectAllBtn);
  actions.appendChild(deselectAllBtn);
  container.appendChild(actions);

  // Chips container
  const chipsContainer = document.createElement("div");
  chipsContainer.className = "route-chips";
  container.appendChild(chipsContainer);

  const sorted = [...routes].sort((a, b) =>
    a.number.localeCompare(b.number, undefined, { numeric: true })
  );

  const chips: HTMLElement[] = [];

  for (const route of sorted) {
    const chip = document.createElement("div");
    chip.className = "route-chip";
    chip.style.background = routeColor(route.number);
    chip.textContent = route.number;
    chip.title = route.name;

    chip.addEventListener("click", () => {
      store.toggleRoute(route.number);
      updateChip(chip, route.number);
    });

    updateChip(chip, route.number);
    chipsContainer.appendChild(chip);
    chips.push(chip);
  }

  function updateChip(chip: HTMLElement, routeNum: string) {
    chip.classList.toggle(
      "disabled",
      !store.state.enabledRoutes.has(routeNum)
    );
  }

  function updateAllChips() {
    sorted.forEach((route, i) => updateChip(chips[i], route.number));
  }
}
