/**
 * KAME rotation chip — the store behind the indicator beside the composer.
 *
 * v1.6.0.1. Until this release ordinary rotation was invisible: the only thing
 * the plugin ever put on screen was the wait notice, and that needs the WHOLE
 * pool cold for ninety seconds before it says anything. A key being swapped, a
 * key resting twenty seconds, a key leaving rotation — all of it happened in a
 * console the person watching the chat is not reading.
 *
 * Agent Zero v2.11 added WebUI plugin slots beside the model/context strip.
 * This store feeds the chip that lives in one.
 *
 * Two rules it does not break:
 *   1. Counts and fingerprints only. The endpoint cannot return a key, so this
 *      cannot render one.
 *   2. Nothing here reaches the model. It is a screen, not a message.
 */
import { createStore } from "/js/AlpineStore.js";
import { callJsonApi } from "/js/api.js";

const API_PATH = "/plugins/api_rotation_by_kame/kame_status";

// Registering the visibility toggle is a nicety, not a dependency. Agent Zero
// may move this store between versions, and a chip that disappears because a
// preferences module was renamed is a worse outcome than a chip that cannot be
// switched off from Settings. Dynamic import so a failure is catchable.
let preferencesStore = null;
import("/components/sidebar/bottom/preferences/preferences-store.js")
  .then((mod) => {
    preferencesStore = mod.store;
    preferencesStore?.registerUiControlVisibility?.("kameRotation", {
      mobile: true,
      desktop: true,
    });
  })
  .catch(() => {
    /* older or restructured Agent Zero — the chip simply always shows */
  });

const EMPTY = {
  available: false,
  active: false,
  layer: 3,
  version: "",
  pools: [],
  totals: { keys: 0, ready: 0, resting: 0, retired: 0 },
  stats: {},
};

function formatSeconds(value) {
  const s = Number(value);
  if (!Number.isFinite(s) || s <= 0) return "";
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  return `${(s / 3600).toFixed(1).replace(/\.0$/, "")}h`;
}

/**
 * The one number worth putting in a chip.
 *
 * Ready over total, because that is the answer to "can it answer me right now".
 * Not "healthy over total" — a key resting twenty seconds off a throttle is
 * perfectly healthy and cannot take this turn, and conflating the two is how a
 * panel ends up reassuring somebody whose agent is stuck.
 */
function buildView(data) {
  const d = data && typeof data === "object" ? data : EMPTY;
  const totals = d.totals || EMPTY.totals;
  const keys = Math.max(Number(totals.keys) || 0, 0);
  const ready = Math.max(Number(totals.ready) || 0, 0);
  const resting = Math.max(Number(totals.resting) || 0, 0);
  const retired = Math.max(Number(totals.retired) || 0, 0);

  // Soonest recovery across every pool, so the chip can answer "how long".
  let eta = null;
  for (const pool of d.pools || []) {
    const value = Number(pool?.eta);
    if (Number.isFinite(value) && value > 0 && (eta === null || value < eta)) eta = value;
  }

  let tone = "ok";
  let headline = "All keys ready";
  if (!d.available || !d.active) {
    tone = "idle";
    // Layer 3 is a SAFE end state — KAME binds nothing and Agent Zero runs
    // exactly as it would with no plugin — but it is also the single most
    // useful thing to see when somebody reports "the plugin does nothing", so
    // it is said out loud rather than rendered as a healthy zero.
    headline = d.available && d.layer === 3
      ? "Not attached — Agent Zero is running natively"
      : "Waiting for the first call";
  } else if (retired > 0) {
    tone = "bad";
    headline = `${retired} credential${retired === 1 ? "" : "s"} left rotation`;
  } else if (keys > 0 && ready === 0) {
    tone = "bad";
    headline = eta ? `Every key resting — earliest back in ${formatSeconds(eta)}` : "Every key resting";
  } else if (resting > 0) {
    tone = "warn";
    headline = `${resting} of ${keys} resting${eta ? ` — next back in ${formatSeconds(eta)}` : ""}`;
  } else if (keys === 0) {
    tone = "idle";
    headline = "No pool seen yet — KAME learns one on the first call";
  }

  const pools = (d.pools || []).map((pool) => ({
    identity: String(pool?.identity || ""),
    total: Number(pool?.total) || 0,
    ready: Number(pool?.ready) || 0,
    resting: Number(pool?.resting) || 0,
    retired: Number(pool?.retired) || 0,
    etaLabel: formatSeconds(pool?.eta),
    keys: (pool?.keys || []).map((row) => ({
      id: String(row?.id || ""),
      state: String(row?.state || "ready"),
      leftLabel: formatSeconds(row?.seconds_left),
      // "refusal 2 of 3" — so a reader can tell a key being tried from a key
      // given up on without having to know the threshold.
      strikesLabel:
        Number(row?.strikes) > 0 ? `refusal ${row.strikes} of ${row.limit}` : "",
    })),
  }));

  return {
    available: !!d.available,
    active: !!d.active,
    version: String(d.version || ""),
    tone,
    headline,
    keys,
    ready,
    resting,
    retired,
    ringLabel: keys ? `${ready}/${keys}` : "–",
    // 0-100 of the ring that is filled. An empty pool draws an empty ring
    // rather than a full one, because "nothing known" must not look like
    // "everything fine".
    ringPercent: keys ? Math.round((ready / keys) * 100) : 0,
    ariaLabel: keys
      ? `KAME rotation: ${ready} of ${keys} API keys ready`
      : "KAME rotation: no key pool seen yet",
    pools,
    hasPools: pools.length > 0,
  };
}

const model = {
  view: buildView(null),
  open: false,
  loadSeq: 0,
  _timer: null,

  async onMount(watch) {
    await this.refresh();
    // A turn that just ended is the moment the pool most likely changed.
    try {
      watch("$store.chats.selectedContext?.running", (running, previous) => {
        if (previous !== running) void this.refresh();
      });
    } catch {
      /* an Agent Zero without that store still gets the snapshot refresh */
    }
    // A slow safety net only. The live path is `apply_snapshot_before`, which
    // fires on every WebSocket push; this exists so a pool that changes while
    // nothing else is happening — every key resting out a daily cap, with no
    // traffic to trigger a snapshot — still ticks down on screen.
    this._timer = setInterval(() => void this.refresh(), 15000);
  },

  cleanup() {
    this.open = false;
    this.loadSeq += 1;
    if (this._timer) clearInterval(this._timer);
    this._timer = null;
  },

  toggle() {
    this.open = !this.open;
    if (this.open) void this.refresh();
  },

  async refresh() {
    const seq = ++this.loadSeq;
    try {
      const data = await callJsonApi(API_PATH, {});
      if (seq === this.loadSeq) this.view = buildView(data);
    } catch (error) {
      // A failed poll must not blank a panel that was showing something true a
      // second ago. The previous view stands until a good answer replaces it.
      if (seq === this.loadSeq) console.debug("KAME status unavailable:", error);
    }
    return this.view;
  },
};

export const store = createStore("kameRotation", model);
