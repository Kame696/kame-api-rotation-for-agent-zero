import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WEBUI = path.resolve(HERE, "..", "..", "webui");
const stub = (name) => pathToFileURL(path.join(HERE, "stubs", name)).href;

const MAP = {
  "/js/AlpineStore.js": stub("AlpineStore.js"),
  "/js/api.js": stub("api.js"),
};

export async function resolve(specifier, context, next) {
  if (MAP[specifier]) return { url: MAP[specifier], shortCircuit: true };
  const own = "/plugins/api_rotation_by_kame/webui/";
  if (specifier.startsWith(own)) {
    return { url: pathToFileURL(path.join(WEBUI, specifier.slice(own.length))).href, shortCircuit: true };
  }
  if (specifier === "/plugins/_model_config/webui/model-config-store.js" && process.env.KAME_FAKE_MODEL_CONFIG === "1") {
    return { url: stub("model-config-store.js"), shortCircuit: true };
  }
  return next(specifier, context);
}
