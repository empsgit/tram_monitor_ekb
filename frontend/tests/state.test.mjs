import test from "node:test";
import assert from "node:assert/strict";
import { Store } from "../src/services/state.ts";
import { LatestRequest } from "../src/services/latest-request.ts";

test("disable all means no vehicles, catalog refresh preserves it", () => {
  const store = new Store();
  const routes = [{ number: "1", stop_ids: [1] }, { number: "2", stop_ids: [2] }];
  store.setRoutes(routes);
  store.updateVehicles([{ id: "a", route: "1" }, { id: "b", route: "2" }]);
  assert.equal(store.getVisibleVehicles().length, 2);
  store.disableAllRoutes();
  assert.equal(store.getVisibleVehicles().length, 0);
  store.setRoutes(routes);
  assert.equal(store.getVisibleVehicles().length, 0);
  store.toggleRoute("2");
  assert.equal(store.getVisibleVehicles()[0].id, "b");
});

test("selection and highlighting do not emit vehicle updates", () => {
  const store = new Store();
  const events = [];
  store.subscribe((event) => events.push(event));
  store.selectStop(1);
  store.setHighlightedVehicles(["a"]);
  store.selectStop(null);
  assert.deepEqual(events, ["selection", "highlight", "selection"]);
  assert.equal(store.state.highlightedVehicleIds.size, 0);
});

test("late A response cannot replace B or cleared selection", () => {
  const requests = new LatestRequest();
  const a = requests.start();
  const b = requests.start();
  assert.equal(a.signal.aborted, true);
  assert.equal(a.isCurrent(), false);
  assert.equal(b.isCurrent(), true);
  requests.cancel();
  assert.equal(b.signal.aborted, true);
  assert.equal(b.isCurrent(), false);
});
