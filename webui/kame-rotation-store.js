/**
 * KAME rotation chip - the store behind the indicator beside the composer.
 *
 * v1.6.0.1. Until this release ordinary rotation was invisible: the only thing
 * the plugin ever put on screen was the wait notice, and that needs the WHOLE
 * pool cold for ninety seconds before it says anything. A key being swapped, a
 * key resting twenty seconds, a key leaving rotation - all of it happened in a
 * console the person watching the chat is not reading.
 *
 * 1.8.2.9: the chip and the API Rotation page read the same view (`kame-view.js`
 * over `status_view()` in the engine), so they say the same count and the same
 * time. The ring has one segment per key; the popover groups keys by provider and
 * by key, with the full model names that use each one.
 *
 * Two rules it does not break:
 *   1. Counts and fingerprints only. The endpoint cannot return a key, so this
 *      cannot render one.
 *   2. Nothing here reaches the model. It is a screen, not a message.
 */
import { createStore } from "/js/AlpineStore.js";
import { callJsonApi } from "/js/api.js";
import { chipPopover, chipSummary } from "/plugins/api_rotation_by_kame/webui/kame-view.js";

const API_PATH = "/plugins/api_rotation_by_kame/kame_status";

/** A chip with nothing open and no chat running reads the status every this many seconds. */
const IDLE_EVERY_TICKS = 5;

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
    /* older or restructured Agent Zero - the chip simply always shows */
  });

/** The chat the person is looking at, so "what is it doing now" is that chat's. */
function selectedContextId() {
  try {
    const chats = globalThis.Alpine?.store?.("chats");
    return String(chats?.selectedContext?.id || chats?.selected || "") || "";
  } catch {
    return "";
  }
}

const model = {
  data: null,
  now: Date.now(),
  open: false,
  loadSeq: 0,
  _tick: null,
  _ticks: 0,

  /** Whether the chat in view has a turn running (Agent Zero's own flag). */
  get running() {
    try {
      return Boolean(globalThis.Alpine?.store?.("chats")?.selectedContext?.running);
    } catch {
      return false;
    }
  },

  // The view is derived, not stored: the clock moves it (a time left counts down) without a request.
  get view() {
    return chipSummary(this.data, this.now);
  },

  // 1.8.3.1 (D3): what the popover shows - one sentence, the models, the last replies - as plain data.
  get popover() {
    return chipPopover(this.data, this.now);
  },

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
    // 1.8.3.1 (D4): one clock, a second long. The live path is `apply_snapshot_before`, which fires on every
    // WebSocket push; this clock exists so a pool that changes while nothing else is happening - every key
    // resting out a daily cap, with no traffic to trigger a snapshot - still ticks down on screen. The data
    // is re-read every second while the popover is open or a chat is running (the countdowns and the live
    // line move), and every fifth second otherwise. A read costs about 2 ms, so a second is cheap.
    this._tick = setInterval(() => {
      this.now = Date.now();
      this._ticks += 1;
      if (this.open || this.running || this._ticks % IDLE_EVERY_TICKS === 0) void this.refresh();
    }, 1000);
  },

  cleanup() {
    this.open = false;
    this.loadSeq += 1;
    if (this._tick) clearInterval(this._tick);
    this._tick = null;
  },

  toggle() {
    this.open = !this.open;
    if (this.open) void this.refresh();
  },

  async refresh() {
    const seq = ++this.loadSeq;
    try {
      const context = selectedContextId();
      const data = await callJsonApi(API_PATH, context ? { context_id: context } : {});
      if (seq === this.loadSeq) {
        this.data = data;
        this.now = Date.now();
      }
    } catch (error) {
      // A failed poll must not blank a panel that was showing something true a
      // second ago. The previous view stands until a good answer replaces it.
      if (seq === this.loadSeq) console.debug("KAME status unavailable:", error);
    }
    return this.view;
  },
};

export const store = createStore("kameRotation", model);
