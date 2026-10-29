// the host's createStore: the model object itself, kept where the test can reach it
globalThis.__stores = globalThis.__stores || {};
export function createStore(name, model) {
  globalThis.__stores[name] = model;
  return model;
}
