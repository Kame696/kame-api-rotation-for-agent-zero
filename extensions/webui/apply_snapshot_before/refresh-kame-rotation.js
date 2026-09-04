/**
 * Keep the KAME rotation chip live — v1.6.0.1.
 *
 * Agent Zero pushes a state snapshot over the WebSocket whenever anything in
 * the chat moves. This runs on each of those, which is what makes the chip a
 * live reading rather than something that was true when the page loaded.
 *
 * It deliberately does NOT refresh on every push. Snapshots arrive many times a
 * second while a response streams, and the pool does not change per token. It
 * refreshes when something happened that could plausibly have changed the pool
 * — a new generation started, a turn finished, the chat changed — and otherwise
 * lets a slow timer in the store handle the ticking-down case.
 *
 * The plugin's own snapshot could not have been used for this: Agent Zero's
 * `SnapshotV1` is schema-strict (`validate_snapshot_schema_v1` raises on any
 * unexpected key), so a plugin cannot add a field to it. Reading the plugin's
 * own endpoint on the snapshot's cadence is the supported shape.
 */
import { store as kameStore } from "/plugins/api_rotation_by_kame/webui/kame-rotation-store.js";

let lastContextId = "";
let lastGenerationKey = "";
let lastRunning = null;
let lastRefreshAt = 0;

// Never more than this often, whatever the snapshot says. A rotation panel that
// can be made to hammer its own backend by a fast-streaming answer is a panel
// that costs the thing it is describing.
const MIN_INTERVAL_MS = 1500;

function latestGenerationKey(logs) {
  if (!Array.isArray(logs)) return "";
  for (let index = logs.length - 1; index >= 0; index--) {
    const item = logs[index];
    if (item?.type !== "agent" || Number(item.agentno || 0) !== 0) continue;
    return `${item.no ?? ""}:${item.id ?? ""}`;
  }
  return "";
}

export default async function refreshKameRotation(ctx) {
  const snapshot = ctx?.snapshot;
  if (!snapshot) return;

  const contextId = String(snapshot.context || "");
  const generationKey = latestGenerationKey(snapshot.logs);
  const active = (Array.isArray(snapshot.contexts) ? snapshot.contexts : [])
    .find((item) => item?.id === contextId) || null;
  const running = active ? !!active.running : null;

  const contextChanged = contextId !== lastContextId;
  const generationChanged = !!generationKey && generationKey !== lastGenerationKey;
  // A turn ENDING is the moment a bench most often exists that did not a second
  // ago, so it is worth a refresh in its own right.
  const turnEnded = lastRunning === true && running === false;

  if (!contextChanged && !generationChanged && !turnEnded) return;

  lastContextId = contextId;
  if (generationKey) lastGenerationKey = generationKey;
  lastRunning = running;

  const now = Date.now();
  if (now - lastRefreshAt < MIN_INTERVAL_MS) return;
  lastRefreshAt = now;

  try {
    await kameStore.refresh();
  } catch {
    /* the store already keeps the last good view; a failed poll blanks nothing */
  }
}
