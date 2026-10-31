/**
 * API Rotation page (1.8.2.7, redrawn in 1.8.2.9) - Agent Zero's central menu -> API Rotation.
 *
 * The chip beside the composer says "can it answer me now"; this page says everything else: three
 * sentences first, one ring per model (a segment per key), what KAME just did, the three controls
 * everybody reaches for, and the older cards folded under "Details". Settings are grouped the way
 * the Hermes version groups them; the fallback list is edited in rows.
 *
 * Every number comes from the one status view (`kame-view.js` over `status_view()` in the engine),
 * the same one the chip reads. Counts and fingerprints only: the endpoints cannot return a key.
 */
import { createStore } from "/js/AlpineStore.js";
import { callJsonApi } from "/js/api.js";
import {
  KEY_STATE,
  KIND_SHORT,
  KIND_WORDS,
  REASON_WORDS,
  backIn,
  backgroundUseLines,
  STALE_READING_S,
  clockOf,
  durationWords,
  fmtLeft,
  groupMoves,
  keyCounts,
  keyRows,
  legendItems,
  modelState,
  nowSentences,
  quotaShare,
  ringAria,
  ringModels,
  replyFromRow,
  replyLineText,
  ringSvg,
  sideJobsLine,
  speedCostLine,
  untilEpoch,
  ago,
} from "/plugins/api_rotation_by_kame/webui/kame-view.js";

const STATUS = "/plugins/api_rotation_by_kame/kame_status";
const SET = "/plugins/api_rotation_by_kame/kame_settings_set";
const ACTION = "/plugins/api_rotation_by_kame/kame_pool_action";
const FALLBACK_SET = "/plugins/api_rotation_by_kame/kame_fallback_set";
const MODEL_CONFIG = "/plugins/_model_config/webui/model-config-store.js";
const REFRESH_MS = 1000;       // 1.8.3.1 (D4): as often as the Hermes panel (a status read costs ~2 ms)
const SEARCH_DEBOUNCE_MS = 250;
const IMPORT_RETRY_MS = 5000;

// what KAME did next, after an arrow, so each Events row reads "what happened -> what happened next"
const KIND_NEXT = {
  rotation: "the next request uses another key",
  quarantine: "the key sits out, the others carry on",
  invalid_key: "out of rotation until you replace it (/kame-keys)",
  denied_model: "this key skips this model and keeps working on your others",
  storm: "keys keep rotating; repeats are counted, not listed",
  stream_drop: "continuing on another key from where it stopped",
  wait: "no request is sent until a key is back",
  surfaced: "Agent Zero gets the error",
  model_switch: "the next model of your list answers this reply",
};

// one sentence per kind, for the legend under the list
const KIND_MEANING = {
  rotation: "A key was refused (a rate limit, a busy server, a timeout) and rests for as long as the evidence says.",
  switch: "The previous key did not answer, so KAME asked another one.",
  backup: "A reply was slower than usual, so a backup key raced it; the first to start answering won.",
  quarantine: "A key rests for a long time (a spent daily quota, no credit).",
  invalid_key: "The provider says this key is not valid. Waiting will not repair it: replace it.",
  denied_model: "This key may not use this model (plan, region, an API not switched on). It still works for others.",
  storm: "Many keys failed at once: almost always the provider, not your keys.",
  stream_drop: "The provider stopped in the middle of an answer.",
  wait: "Every key was resting, so KAME waited instead of spending requests it knew would be refused.",
  recovery: "A key answered after KAME had already rotated: the rotation worked.",
  surfaced: "Something no other key or wait can fix; Agent Zero gets the error, with a line telling the agent whether retrying helps.",
  setting: "You changed a KAME setting.",
  model_switch: "Your model could not answer now (its provider busy, far slower than usual, every key resting, or no answer in time), so this reply fell back to the next model of your list. The next reply starts on your best model again.",
};

const JOB_WORDS = {
  utility: "Background (memory, titles, summaries)",
  compress: "Compressing the chat",
  chat: "Conversation",
  "": "Conversation",
};

export function seconds(value) {
  const s = Number(value);
  if (!Number.isFinite(s) || s <= 0) return "";
  return fmtLeft(s);
}

export function clock(at) {
  const t = Number(at);
  if (!Number.isFinite(t) || t <= 0) return "";
  return new Date(t * 1000).toLocaleTimeString();
}

// Agent Zero's own model search (the plugin that draws its model picker). v2.12 and later have it;
// without it the fallback rows are plain text inputs and nothing else changes.
//
// 1.8.3.0 (A5): the search is looked up when it is needed, not once at open. On the live v2.14 server the
// suggestions never came: the store was fetched once, inside the same try as the provider list, so a
// store that registered after the page opened, or a provider list that was slow or failed, left the
// editor on plain inputs for the whole session. Now `Alpine.store('modelConfig')` is asked at typing
// time (it is the store Agent Zero's own model field reads), the module import is only a second way to
// reach it and is retried, and the provider list can fail without taking the search down.
let modelConfig = null;
let importing = null;
let importedAt = 0;

/** The model search store, or ``null``: Agent Zero's live Alpine store first, else the module's own export. */
function searchStore() {
  try {
    const live = globalThis.Alpine?.store?.("modelConfig");
    if (live && typeof live.searchModelsDetailed === "function") return live;
  } catch {
    // an Alpine without that store: look at the module below
  }
  return modelConfig && typeof modelConfig.searchModelsDetailed === "function" ? modelConfig : null;
}

async function loadModelConfig() {
  const now = searchStore();
  if (now) return now;
  if (!importing) {
    if (Date.now() - importedAt < IMPORT_RETRY_MS) return null;
    importedAt = Date.now();
    importing = import(MODEL_CONFIG)
      .then((mod) => { modelConfig = mod.store || null; })
      .catch(() => { modelConfig = null; })
      .finally(() => { importing = null; })
      .then(() => searchStore());
  }
  return importing;
}

let rowSeq = 0;
function newRow(provider = "", name = "", apiBase = "") {
  return { id: ++rowSeq, provider, name, api_base: apiBase, results: [], source: "", open: false, searching: false, timer: null, active: -1, error: false };
}

/** 1.8.3.0 (A6): Hermes' power tag and the order of the rows inside a shelf. */
const IMPACT_RANK = { high: 0, medium: 1, low: 2, escape: 3, retired: 4 };
const IMPACT_TAG = {
  high: "●●● High",
  medium: "●●○ Medium",
  low: "●○○ Low",
  escape: "Off switch",
};
const CONSEQUENTIAL_TAIL =
  " This is the switch people reach for when they suspect KAME of breaking something — it is reversible, and nothing is uninstalled.";

/** 1.8.3.0 (A7, A8): how Hermes' Events page says each kind, how loudly, and what it means. */
const EVENT_LABELS = {
  answered: ["Answered", "good"],
  denied_model: ["Not for this model", "warn"],
  gave_up: ["Stopped waiting", "bad"],
  invalid_key: ["Bad key", "bad"],
  quarantine: ["Key benched", "bad"],
  recovery: ["Answered on retry", "good"],
  rotation: ["Key rests", "plain"],
  sent: ["Asked", "plain"],
  setting: ["Setting changed", "good"],
  stitch: ["Answer continued", "good"],
  storm: ["Provider outage", "warn"],
  stream_drop: ["Answer cut", "warn"],
  handover: ["Fallback", "warn"],
  surfaced: ["Passed to Agent Zero", "bad"],
  switch: ["Next key", "good"],
  wait: ["All keys resting", "warn"],
};

const EVENT_MEANING = {
  answered: "The key this call went out on answered, first time. The time is the whole call; the reason says how long the first word took.",
  gave_up: "Every key stayed resting past your “Stop waiting” time, so KAME stopped waiting and handed Agent Zero the original refusal.",
  sent: "A request went out on this key. Every request leaves one of these, so a slow answer is visible while it is still coming.",
  denied_model: "The provider refused this key for THIS model only — a plan that does not include it, or an API not switched on. The key is fine everywhere else, and replacing it would change nothing.",
  invalid_key: "The provider said this is not a valid credential. Replace it — waiting will not repair one.",
  quarantine: "This key was rested for a minute or more before it will be offered again.",
  recovery: "A key answered after KAME had already rotated at least once. The rotation worked.",
  rotation: "This key was rested briefly and the next call went somewhere else.",
  setting: "You changed one of KAME’s settings. Recorded here so a later question — “did that setting help?” — can be answered against what the pool did before and after it.",
  stitch: "An answer that was cut short was continued on another key and joined back up.",
  storm: "A lot of keys refused at once, which is usually the provider rather than your keys.",
  stream_drop: "The provider stopped in the middle of an answer.",
  handover: "Your model could not answer now — its provider said it is busy (overloaded, or timing out on several keys), it was far slower than usual, or every key was resting — so this reply fell back to the next model of your fallback list. The next reply starts on your best model again.",
  surfaced: "KAME could not fix this one, so Agent Zero got the error and decides what to do next.",
  switch: "The previous key did not answer, so KAME asked this one instead — or a slow reply got a backup key racing it.",
  wait: "Every key was resting, so KAME waited instead of spending a request it knew would be refused.",
};

/** Hermes' words for the classifier's reason. */
const EVENT_REASON_WORDS = {
  auth: "the provider refused this credential",
  auth_permanent: "the provider says this is not a key",
  billing: "the account is out of credit",
  daily: "the provider says today's quota is spent",
  denied: "this key may not use this model",
  host_breaker: "Agent Zero stopped the call itself",
  insufficient_quota: "the account is out of credit",
  other: "an error nothing recognised",
  per_minute: "a per-minute rate limit",
  rate_limit: "a rate limit",
  refused: "the provider refused the call",
  server: "the provider had a server error",
  timeout: "the provider did not answer in time",
};

/** Agent Zero's engine writes its reasons as short phrases; Hermes writes classifier ids. Same facts. */
const REASON_ID = {
  throttled: "per_minute",
  "daily quota": "daily",
  "out of credit": "insufficient_quota",
  "provider busy": "server",
  "no answer in time": "timeout",
  "credential refused": "auth",
  "not a valid key": "auth_permanent",
  "not allowed on this model": "denied",
  "unrecognised error": "other",
};

/** What KAME did next, after an arrow, so every row reads "what happened → what happened next". */
const EVENT_NEXT = {
  answered: "",
  denied_model: "this key skips this model and keeps working on your others",
  gave_up: "Agent Zero got the original error",
  invalid_key: "out of rotation until you replace it (/kame-keys)",
  quarantine: "the key sits out, the others carry on",
  recovery: "",
  rotation: "the next request uses another key",
  sent: "waiting for the first word",
  setting: "",
  stitch: "shown to you as one answer",
  storm: "keys keep rotating; repeats are counted, not listed",
  stream_drop: "continuing on another key from where it stopped",
  handover: "the next model of your list answers this reply",
  surfaced: "Agent Zero decides what to do next",
  switch: "",
  wait: "no request is sent until a key is back",
};

/** The kinds that are KAME working, rather than a provider failing. */
const GOOD_KINDS = new Set(["switch", "recovery", "stitch", "wait", "setting", "sent", "answered", "handover"]);

/** Where a rest came from, in one word (Hermes' SIZED_BY_LABELS). */
const SIZED_BY_LABELS = {
  catalog: ["field", "good"],
  header: ["header", "good"],
  body: ["payload", "good"],
  retryinfo: ["retryDelay", "good"],
  anchor: ["reset time", "good"],
  exception: ["sdk", "good"],
  type: ["field", "good"],
  verdict: ["evidence", "good"],
  pattern: ["text", "weak"],
  table: ["table", "weak"],
  dropped: ["guess", "weak"],
  reprobe: ["re-probe", "weak"],
  retained: ["existing hold", "weak"],
  backoff: ["bare-429 backoff", "weak"],
  window: ["our default", "weak"],
  text: ["message", "weak"],
  kame: ["ours", "weak"],
  host: ["host", "weak"],
};

/** Agent Zero's background jobs (the names kame_aux reads from the call stack) in a person's words. */
const TASK_WORDS = {
  title_generation: "Naming this chat",
  compression: "Shrinking a long conversation",
  memory_recall: "Looking up memories",
  memory_consolidation: "Tidying memories",
  memorize_fragments: "Saving memories",
  memorize_solutions: "Saving solutions",
  document_query: "Reading a document",
  infection_check: "Checking content for hidden instructions",
  background_review: "Reviewing a result",
  profile_describer: "Describing a profile",
};

const STILL_WAITING_AFTER_S = 60;
const STALE_AFTER_S = STALE_READING_S;
const duration = durationWords;

/** "4m ago", the form that is useful on a list that updates itself. */
function eventAgo(secondsAgo) {
  const s = Number(secondsAgo);
  if (!Number.isFinite(s) || s < 0) return "";
  if (s < 45) return "just now";
  if (s < 90) return "a minute ago";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

function modelOnly(identity) {
  const text = String(identity ?? "");
  const at = text.indexOf(":");
  return at === -1 ? text : text.slice(at + 1);
}

function plural(n, one, many) {
  return `${n} ${n === 1 ? one : many}`;
}

const model = {
  tab: "overview",
  data: null,
  now: Date.now(),
  error: "",
  loading: false,
  showAdvanced: false,
  showSwitches: false,
  detailsOpen: false,
  openRing: "",
  confirming: "",
  askingFlag: "",
  eventView: "all",
  bgTask: "all",
  inspecting: null,
  fetchedAt: 0,
  errors: {},
  saving: false,
  busy: false,
  message: "",
  drafts: {},
  // the fallback editor
  rows: [],
  rowsDirty: false,
  rowsNote: "",
  hasSearch: false,
  providers: [],
  _timer: null,
  _tick: null,

  async onOpen() {
    this.cleanup();
    this._open = true;
    // The timers first: a panel closed while the first fetch is in flight must
    // not leave a poll running (cleanup clears it).
    this._timer = setInterval(() => this.refresh(), REFRESH_MS);
    this._tick = setInterval(() => { this.now = Date.now(); }, 1000);
    await this.refresh();
    await this.prepareSearch();
  },

  /** Find Agent Zero's model search and, if it offers one, its provider list. A provider list that
   *  cannot be loaded never disables the search. */
  async prepareSearch() {
    const found = await loadModelConfig();
    this.hasSearch = !!found;
    if (found) await this.readProviders(found);
    return found;
  },
  async readProviders(found) {
    try {
      if (!(found.chatProviders || []).length) await found.ensureLoaded?.();
    } catch {
      // the list stays as it is: the search does not depend on it
    }
    this.providers = (found.chatProviders || []).map((p) => ({ value: p.value, label: p.label }));
  },
  /** The providers for the select: Agent Zero's list, else the ones KAME already knows (its pools and
   *  the saved list). Empty = the provider is typed. */
  get providerChoices() {
    if (this.providers.length) return this.providers;
    const seen = new Set();
    const add = (id) => {
      const value = String(id || "").trim();
      if (value) seen.add(value);
    };
    for (const pool of this.data?.view?.pools || []) add(String(pool.identity || "").split(":")[0]);
    for (const entry of this.data?.speed?.fallback_models || []) add(entry.provider);
    return [...seen].sort().map((value) => ({ value, label: value }));
  },

  cleanup() {
    this._open = false;
    this.drafts = {};
    for (const row of this.rows) if (row.timer) clearTimeout(row.timer);
    if (this._timer) clearInterval(this._timer);
    if (this._tick) clearInterval(this._tick);
    this._timer = null;
    this._tick = null;
  },

  async refresh() {
    if (this.loading) return;
    this.loading = true;
    try {
      this.data = await callJsonApi(STATUS, { panel: true });
      this.now = Date.now();
      this.fetchedAt = this.now;
      this.error = "";
      this.syncRows();
    } catch (e) {
      this.error = `KAME's status did not load (${e?.message || e}).`;
    } finally {
      this.loading = false;
    }
  },

  // -- the one view ---------------------------------------------------------------------------

  /** `status_view()` plus the two facts about the install the page also needs. */
  get view() {
    const d = this.data;
    return d && d.view ? { ...d.view, active: d.active, build: d.build } : null;
  },
  get said() {
    return nowSentences(this.view, this.now, this.readAgeS);
  },
  /** How long ago the page last read the status, in seconds (0 before the first reading). */
  get readAgeS() {
    return this.fetchedAt ? Math.max(0, (this.now - this.fetchedAt) / 1000) : 0;
  },
  /** The build fingerprint: computed from the bytes on disk, so it cannot describe a file that is not there. */
  get buildFingerprint() {
    return this.data?.build?.fingerprint || "";
  },
  get pid() {
    return this.data?.pid || "";
  },
  /** How often the page reads the status again, in words (the footer says it). */
  get refreshEvery() {
    const n = REFRESH_MS / 1000;
    return n === 1 ? "second" : `${n} seconds`;
  },
  get version() {
    return this.data?.version || "";
  },
  get first() {
    return !!this.data && !(this.view?.totals?.keys > 0) && !(this.view?.pools || []).some((p) => p.keys > 0);
  },

  // -- the rings ------------------------------------------------------------------------------

  get ringGroups() {
    const v = this.view;
    if (!v) return { main: [], also: [], chain: false };
    const { main, also } = ringModels(v);
    const share = quotaShare(v);
    const card = (m) => {
      const state = modelState(m, v, this.now);
      const answering = !!v.activity?.identity && !!m.pool && m.pool.identity === v.activity.identity;
      return {
        identity: m.identity,
        label: m.label,
        rank: m.rank,
        title: `${m.rank ? `${m.rank} · ` : ""}${m.label}`,
        small: !m.rank,
        svg: ringSvg(m, v, this.now),
        aria: ringAria(m, v, this.now),
        word: answering && state.tone === "good" ? "answering now" : state.word,
        tone: state.tone,
        side: sideJobsLine(share, m.identity),
        open: this.openRing === m.identity,
        model: m,
      };
    };
    return { main: main.map(card), also: also.map(card), chain: (v.fallback?.entries || []).length > 1 };
  },
  get ringsNote() {
    return this.ringGroups.chain
      ? "In the order of your fallback list: KAME starts every reply on 1, and goes down the list when a model is busy, slow or out of quota."
      : "One ring per provider and model. A key spent on one model keeps its allowance on another.";
  },
  get legend() {
    return legendItems();
  },
  toggleRing(identity) {
    this.openRing = this.openRing === identity ? "" : identity;
    this.confirming = "";
  },
  /** The open model's keys, one line each, and what its buttons may do. */
  get keyTable() {
    const v = this.view;
    if (!v || !this.openRing) return null;
    const m = [...this.ringGroups.main, ...this.ringGroups.also].find((c) => c.identity === this.openRing);
    if (!m) return null;
    const pool = m.model.pool;
    if (!pool || !pool.keys) {
      return { identity: m.identity, empty: `${m.label} has not been used yet — its keys show here after the first call.`, keys: [], busy: false, summary: "" };
    }
    const counts = keyCounts(pool);
    const summary = [`${counts.r + counts.f} of ${pool.keys} keys ready`];
    if (pool.unused) summary.push("not used yet");
    if (counts.s) summary.push(`${counts.s} resting${pool.soonest_s ? `, next back in ${fmtLeft(pool.soonest_s, this.now)}` : ""}`);
    if (counts.d) summary.push(`${counts.d} out for today`);
    if (counts.x + counts.o) summary.push(`${counts.x + counts.o} to replace`);
    const keys = (pool.key_states || []).map(([fingerprint, code, until, kind]) => {
      const style = KEY_STATE[code] || KEY_STATE.r;
      const left = untilEpoch(until, this.now);
      let words = style.label;
      if (left && code !== "o" && code !== "x") words += ` · ${backIn(left, this.now)}`;
      if (kind && code !== "r" && code !== "f") words += ` · ${KIND_SHORT[kind] || kind}`;
      return { fingerprint: String(fingerprint).replace(/^key:/, ""), glyph: style.glyph, colour: style.colour, words };
    });
    return {
      identity: m.identity,
      label: m.label,
      empty: "",
      summary: summary.join(" · "),
      unused: !!pool.unused,
      keys,
      busy: !!untilEpoch(m.model.entry?.busy_until, this.now),
    };
  },
  async act(action, identity) {
    this.busy = true;
    try {
      const res = await callJsonApi(ACTION, { action, identity: identity || "" });
      this.message = res?.message || (res?.ok ? "Done." : "That did not work.");
      this.confirming = "";
      await this.refresh();
    } catch (e) {
      this.message = `That did not work (${e?.message || e}).`;
    } finally {
      this.busy = false;
    }
  },

  // -- what KAME did, the quick controls, the details --------------------------------------------

  get moves() {
    return groupMoves(this.view, 6).map((m) => ({ ...m, ago: m.last_at ? ago(this.now / 1000 - m.last_at) : "" }));
  },
  get speedCost() {
    return speedCostLine(this.view?.speed);
  },
  get backgroundLines() {
    return backgroundUseLines(this.view);
  },
  get keyGroups() {
    return keyRows(this.view, this.now);
  },
  get activity() {
    return this.view?.activity || {};
  },
  /** "Right now": the long form of the first sentence (Hermes' words). */
  get rightNow() {
    const a = this.activity;
    const left = (value) => (value === null || value === undefined ? null : Math.max(0, value - this.readAgeS));
    if (!a.kind || a.kind === "idle") {
      const soonest = (this.view?.pools || []).map((p) => p.soonest_s).filter((n) => typeof n === "number");
      const eta = (this.view?.totals?.ready || 0) > 0 || !soonest.length ? null : left(Math.min(...soonest));
      return eta === null
        ? "Idle. A key is ready, so the next call goes straight out."
        : `Idle, and every key is resting. The first one comes back in ${duration(eta)}.`;
    }
    if (a.kind === "calling") {
      return a.attempt > 1
        ? `Rotated. Attempt ${a.attempt} on ${a.model}, with ${a.healthy} of ${a.keys} keys still ready.`
        : `Calling ${a.model}, with ${a.healthy} of ${a.keys} keys ready.`;
    }
    if (a.kind === "waiting") {
      const eta = left(a.eta_s);
      return `Every key is resting. Waiting ${eta === null ? "for one to come back" : duration(eta)} rather than failing the turn — press Esc to stop.`;
    }
    return "Working.";
  },
  get waitingSince() {
    const at = this.activity.waiting_since;
    return this.activity.kind === "waiting" && at ? clockOf(at) : "";
  },

  get pools() {
    return this.data?.pools || [];
  },
  get speed() {
    return this.data?.speed || {};
  },
  get inFlight() {
    return this.data?.in_flight || [];
  },
  get replies() {
    return this.data?.replies || [];
  },
  get seams() {
    const host = this.data?.host || {};
    const rows = Object.entries(host.entry_points || {}).map(([name, v]) => ({ name, ...v }));
    return { rows, factory: host.fallback_factory, layer: host.layer };
  },
  get process() {
    const d = this.data || {};
    const fp = d.build?.fingerprint ? ` · build ${d.build.fingerprint}` : "";
    return `KAME ${d.version || ""}${fp} · ${d.active ? "rotating" : "not rotating (Agent Zero runs natively)"}`;
  },
  poolLine(pool) {
    const eta = seconds(pool.eta);
    const parts = [`${pool.ready} of ${pool.total} ready`];
    if (pool.resting) parts.push(`${pool.resting} resting${eta ? `, next back in ${eta}` : ""}`);
    if (pool.retired) parts.push(`${pool.retired} out of rotation`);
    return parts.join(" · ");
  },

  // -- events -----------------------------------------------------------------------------------

  events(background) {
    const rows = this.data?.events || [];
    return rows.filter((r) => {
      const bg = r.job === "utility" || r.job === "compress";
      return background ? bg : !bg;
    });
  },

  // -- the Events tab (1.8.3.0, A7): Hermes' Events page ------------------------------------------

  /** The report's own clock, kept moving between two readings (a skewed clock cannot make a row old). */
  get serverNowS() {
    const at = Number(this.data?.generated_at);
    if (!Number.isFinite(at)) return this.now / 1000;
    return at + Math.max(0, this.now - (this.fetchedAt || this.now)) / 1000;
  },
  /** Agent Zero's kinds under Hermes' names: a model switch is a "Fallback", a backup key is a "Next key". */
  eventKind(e) {
    const kind = e?.kind;
    return kind === "model_switch" ? "handover" : kind === "backup" ? "switch" : kind;
  },
  eventLabel(e) {
    const [word, tone] = EVENT_LABELS[this.eventKind(e)] || [e?.kind, "plain"];
    return { word, tone };
  },
  eventMeaning(e) {
    return EVENT_MEANING[this.eventKind(e)] || "";
  },
  isGoodEvent(e) {
    return GOOD_KINDS.has(this.eventKind(e));
  },
  reasonWords(reason) {
    const id = REASON_ID[reason] || reason;
    return EVENT_REASON_WORDS[id] || reason;
  },
  eventReason(e) {
    const reason = REASON_ID[e?.reason] || e?.reason || "";
    const kind = this.eventKind(e);
    if (kind === "surfaced" && reason === "timeout") {
      return "the provider timed out twice in a row — Agent Zero takes over the retry";
    }
    if (e?.code === 400 && (!reason || reason === "other")) {
      return "the provider rejected the request itself — another key would get the same answer";
    }
    if (String(e?.sized_by ?? "").startsWith("backoff") && (reason === "rate_limit" || reason === "per_minute")) {
      return "a rate limit with no quota number — usually the provider is busy, not your quota";
    }
    return EVENT_REASON_WORDS[reason] ?? e?.reason ?? EVENT_LABELS[kind]?.[0] ?? "";
  },
  eventSentence(e) {
    const reason = this.eventReason(e);
    const next = EVENT_NEXT[this.eventKind(e)] ?? "";
    if (!next) return reason;
    return reason ? `${reason} → ${next}` : next;
  },
  taskWord(task) {
    const id = String(task || "other");
    return TASK_WORDS[id] ?? id.replace(/_/g, " ");
  },
  /** The sentence of a row; a background call is led by the job that made it. */
  eventLine(e) {
    return e?.task ? `${this.taskWord(e.task)} · ${this.eventSentence(e)}` : this.eventSentence(e);
  },
  /** The count beside a tab's name (Hermes: Events (N), Background jobs (N)). */
  tabCount(id) {
    return id === "events" ? this.chatEvents.length : id === "background" ? this.bgEvents.length : 0;
  },
  replyTime(value) {
    if (value === null || value === undefined) return "-";
    return value < 60 ? `${Number(value).toFixed(1)} s` : duration(value);
  },
  replyAgo(r) {
    return eventAgo(this.serverNowS - Number(r?.at ?? 0));
  },
  /** One reply in the words of the chip's popover (1.8.3.1, review R16): `✓ gemini-3.8-flash answered in 2.3s`,
   *  `↪ ... answered for ... (busy)`, `✕ no answer: it timed out` - the same renderer. */
  replyLine(r) {
    return replyLineText(replyFromRow(r));
  },
  replyClock(r) {
    return clockOf(r?.at);
  },
  eventAgo(e) {
    return eventAgo(this.serverNowS - Number(e?.at ?? 0));
  },
  eventClock(e) {
    return clock(e?.at);
  },
  eventTail(e) {
    const rest = Number(e?.seconds);
    return {
      model: modelOnly(e?.identity),
      key: e?.key || "",
      code: e?.code ? `HTTP ${e.code}` : "",
      rest: rest > 0
        ? e.kind === "recovery"
          ? `after ${duration(rest)}`
          : e.kind === "wait"
            ? `for ${duration(rest)}`
            : `rested ${duration(rest)}`
        : "",
    };
  },
  sizedChip(e) {
    const source = e?.sized_by;
    if (!source) return null;
    const family = String(source).split(".")[0];
    const [word, tone] = Object.prototype.hasOwnProperty.call(SIZED_BY_LABELS, family)
      ? SIZED_BY_LABELS[family]
      : ["unknown source", "weak"];
    const good = tone === "good";
    return {
      word,
      good,
      title: good
        ? "The provider said how long. This wait is its number, not ours."
        : "Nothing in the response said how long. This wait is a fallback.",
    };
  },
  rowTitle(e) {
    const meaning = this.eventMeaning(e);
    return this.canInspect(e) ? `${meaning}\n\nClick to see what the provider actually said.` : meaning;
  },
  canInspect(e) {
    return !!e?.detail && this.eventLabel(e).tone !== "good";
  },
  inspect(e) {
    if (this.canInspect(e)) this.inspecting = e;
  },
  closeInspect() {
    this.inspecting = null;
  },
  get inspectorHead() {
    const e = this.inspecting;
    if (!e) return null;
    return { label: this.eventLabel(e).word, identity: e.identity || "", clock: this.eventClock(e) };
  },
  get chatEvents() {
    return this.events(false);
  },
  get tallies() {
    const rows = this.chatEvents;
    const good = rows.filter((e) => this.isGoodEvent(e)).length;
    const oldest = rows.length ? rows[rows.length - 1].at : null;
    const covers = oldest ? eventAgo(this.serverNowS - oldest) : "";
    return {
      all: { count: rows.length, label: covers ? `in the last ${covers.replace(" ago", "")}` : "recorded",
             title: "Everything in the buffer, newest first." },
      did: { count: good, label: "KAME rotating",
             title: "Rotations, recoveries, continued answers and waits — the plugin doing its job." },
      wrong: { count: rows.length - good, label: "providers refusing",
               title: "What the providers said. A refusal here is not a fault in KAME; it is what KAME is for." },
      background: { count: this.events(true).length, label: "background jobs →",
                    title: "Titles, summaries, compression and the other jobs Agent Zero runs on its own. Opens the Background jobs tab, where you can pick one job." },
    };
  },
  setEventView(view) {
    this.eventView = view;
  },
  get shownEvents() {
    const rows = this.chatEvents;
    if (this.eventView === "did") return rows.filter((e) => this.isGoodEvent(e));
    if (this.eventView === "wrong") return rows.filter((e) => !this.isGoodEvent(e));
    return rows;
  },
  get eventsEmpty() {
    if (this.shownEvents.length) return "";
    return this.chatEvents.length
      ? "Nothing of that kind yet."
      : "Nothing recorded yet. Every rotation, refusal, cut answer and continuation appears here as it happens.";
  },
  /** One line saying the plugin is alive, under a list that is allowed to be empty. */
  get heartbeat() {
    const counters = this.data?.counters || {};
    const calls = Number(counters.calls ?? 0);
    const lastCall = Number(counters.last_call_at ?? 0);
    const readAge = this.fetchedAt ? (this.now - this.fetchedAt) / 1000 : null;
    if (!this.data?.active) return "KAME is not installed in this Agent Zero process, so nothing will be recorded here.";
    if (readAge !== null && readAge > STALE_AFTER_S) {
      return `This reading is ${eventAgo(readAge)} — the Agent Zero process that writes it may have stopped. ` +
        "Everything below is what it last knew, not what is happening now.";
    }
    if (!calls) return "KAME is running and has not been asked to route a call yet in this process.";
    if (lastCall) {
      return `KAME is running. ${plural(calls, "call", "calls")} routed, the last one ` +
        `${eventAgo(this.serverNowS - lastCall)}. An empty list above means nothing failed.`;
    }
    return `KAME is running. ${plural(calls, "call", "calls")} routed. An empty list above means nothing failed.`;
  },
  get heartbeatStale() {
    return !!this.fetchedAt && (this.now - this.fetchedAt) / 1000 > STALE_AFTER_S && !!this.data?.active;
  },
  /** One sentence per call that has been running for a while ("Still waiting"). */
  stillWaiting(channel) {
    return (this.data?.in_flight || [])
      .filter((f) => ((f.job === "utility" || f.job === "compress") ? "secondary" : "main") === channel &&
        Number(f.seconds ?? 0) >= STILL_WAITING_AFTER_S)
      .map((f) => {
        const who = channel === "main" ? "chat" : this.taskWord(f.task || (f.job === "compress" ? "compression" : "")) ;
        return `${who} · ${modelOnly(f.identity)} — no answer for ${duration(f.seconds)}` +
          (f.refusals ? `, ${plural(f.refusals, "refusal", "refusals")} so far` : "") +
          (f.last ? `; last: ${this.reasonWords(f.last)}` : "") +
          ". KAME keeps trying, as configured.";
      });
  },
  /** Every kind Agent Zero records, in Hermes' order, with its sentence. */
  get legendKinds() {
    const recorded = new Set(["denied_model", "gave_up", "invalid_key", "quarantine", "recovery", "rotation", "setting",
      "stitch", "storm", "stream_drop", "handover", "surfaced", "switch", "wait"]);
    return Object.entries(EVENT_LABELS)
      .filter(([kind]) => recorded.has(kind))
      .map(([kind, [word, tone]]) => ({ kind, word, tone, text: EVENT_MEANING[kind] || "" }));
  },

  // -- the Background jobs tab (1.8.3.0, A8): Hermes' SecondaryPage -------------------------------

  get bgEvents() {
    return this.events(true);
  },
  get bgTasks() {
    const by = {};
    for (const e of this.bgEvents) {
      const name = e.task || "other";
      by[name] = (by[name] || 0) + 1;
    }
    return Object.entries(by).sort((x, y) => y[1] - x[1]).map(([name, count]) => ({ name, count, label: this.taskWord(name) }));
  },
  get bgActive() {
    return this.bgTask !== "all" && this.bgTasks.some((t) => t.name === this.bgTask) ? this.bgTask : "all";
  },
  setBgTask(name) {
    this.bgTask = name;
  },
  get bgShown() {
    const active = this.bgActive;
    return active === "all" ? this.bgEvents : this.bgEvents.filter((e) => (e.task || "other") === active);
  },
  reasonText(e) {
    // a throttle that names no wait is usually the provider being busy, not your quota
    const r = String(e?.reason || "");
    let said = REASON_WORDS[r] || r;
    if (r === "throttled" && !["provider", "window"].includes(String(e?.sized_by || ""))) {
      said = "throttled - usually the provider is busy, not your quota";
    }
    const next = KIND_NEXT[e?.kind] || "";
    return next ? (said ? `${said} → ${next}` : next) : said;
  },
  get meanings() {
    return Object.entries(KIND_MEANING).map(([kind, text]) => ({ kind, word: KIND_WORDS[kind] || kind, text }));
  },
  kindWord(kind) {
    return KIND_WORDS[kind] || kind;
  },
  jobWord(job) {
    return JOB_WORDS[job || ""] || job;
  },
  good(kind) {
    return ["switch", "recovery", "wait", "setting", "model_switch", "backup"].includes(kind);
  },
  seconds,
  clock,

  // -- settings ---------------------------------------------------------------------------------

  get settingsMeta() {
    return this.data?.settings_meta || [];
  },
  get current() {
    return this.data?.settings || {};
  },
  /** Hermes' Source badge: where the value in force comes from. */
  sourceOf(m) {
    if (m.env_override) return "environment";
    const v = this.current[m.name];
    return v !== undefined && v !== null && String(v) !== String(m.default) ? "config" : "default";
  },
  sourceLabel(m) {
    const source = this.sourceOf(m);
    return source === "environment" ? "set in the environment" : source === "config" ? "set in the settings" : "default";
  },
  sourceTip(m) {
    const source = this.sourceOf(m);
    return source === "environment"
      ? `${m.env_override} is set in the environment, which outranks everything else.`
      : source === "config"
        ? "Set in this plugin's settings. The environment would outrank it."
        : "Nothing anywhere mentions this one, so the built-in default is in force.";
  },
  /** A row is "changed" when its value does not come from the default. */
  isChanged(m) {
    return this.sourceOf(m) !== "default";
  },
  impactTag(m) {
    return IMPACT_TAG[m?.impact] || "";
  },
  get changedCount() {
    return this.settingsMeta.filter((m) => m.group !== "fallback" && this.isChanged(m)).length;
  },
  get advancedCount() {
    return this.settingsMeta.filter((m) => m.advanced && m.group !== "fallback" && !this.isChanged(m)).length;
  },
  get toolbarTitle() {
    return this.changedCount ? `KAME settings · ${this.changedCount} changed` : "KAME settings · all at their defaults";
  },
  get noSettings() {
    return !!this.data && !this.settingsMeta.length;
  },
  /** One shelf's rows: by power (high first, ties keep the table's order), advanced ones only when asked
   *  (a changed one never hides), a number that belongs to one answer of a choice only under that answer. */
  _shelfRows(all) {
    const byWeight = (a, b) => (IMPACT_RANK[a.impact] ?? 9) - (IMPACT_RANK[b.impact] ?? 9);
    const hidden = (m) =>
      (m.advanced && !this.isChanged(m) && !this.showAdvanced) ||
      (m.depends_on && String(this.value(m.depends_on[0])) !== m.depends_on[1]);
    const sorted = all.map((m, i) => [m, i]).sort((x, y) => byWeight(x[0], y[0]) || x[1] - y[1]).map(([m]) => m);
    return { sorted, rows: sorted.filter((m) => !hidden(m)) };
  },
  get settingCards() {
    if (this.noSettings) return [];
    const groups = this.data?.setting_groups || [];
    const claimed = new Set();
    const cards = groups
      .map((g) => {
        const { sorted, rows } = this._shelfRows(this.settingsMeta.filter((m) => m.group === g.id && !claimed.has(m.name)));
        sorted.forEach((m) => claimed.add(m.name));
        const changed = rows.filter((m) => this.isChanged(m)).length;
        return {
          id: g.id,
          title: changed ? `${g.title} · ${changed} changed` : g.title,
          note: g.note,
          folded: !!g.folded,
          // a troubleshooting shelf with one switch already on opens by itself
          open: !g.folded || changed > 0,
          rows,
          total: rows.length,
          shows_fallback: !!g.shows_fallback,
          shows_keys: !!g.shows_keys,
        };
      })
      .filter((c) => c.rows.length || c.shows_fallback || c.shows_keys);
    // a setting no shelf claims still shows, on a card of its own at the end
    const rest = this.settingsMeta.filter((m) => m.group !== "fallback" && !groups.some((g) => g.id === m.group));
    if (rest.length) {
      cards.push({
        id: "_rest",
        title: "Other",
        note: "Not sorted into any of the groups above.",
        folded: false,
        open: true,
        rows: rest,
        total: rest.length,
        shows_fallback: false,
        shows_keys: false,
      });
    }
    return cards;
  },
  /** 1.8.3.0 (A4): the shelf "If your model cannot answer", drawn on the Fallback models tab too. */
  get fallbackCards() {
    return this.settingCards.filter((c) => c.id === "extra");
  },
  get extraAdvancedCount() {
    return this.settingsMeta.filter((m) => m.group === "extra" && m.advanced && !this.isChanged(m)).length;
  },
  quickRow(name) {
    return this.settingsMeta.find((m) => m.name === name) || null;
  },
  /** 1.8.3.0 (A9): "Quick controls" is built from the same rows as the Settings tab, in Hermes' order:
   *  speed, fallback, the seconds only when fallback is "Set time", then the reply notice. */
  get quickCards() {
    if (this.noSettings) return [];
    const names = ["hedge_mode", "model_switch"];
    if (this.value("model_switch") === "timed") names.push("failover_after_seconds");
    names.push("reply_notice");
    const rows = names.map((n) => this.quickRow(n)).filter(Boolean);
    return rows.length ? [{ id: "quick", rows }] : [];
  },
  choicesOf(m) {
    return m.choice_order || m.choices || [];
  },
  choiceLabel(m, c) {
    return (m.labels && m.labels[c]) || c;
  },
  /** The sentence under a choice's buttons: what the chosen one means. */
  choiceNote(m) {
    const chosen = String(this.value(m.name));
    return (m.choice_help && m.choice_help[chosen]) || "";
  },
  /** What the percentages leave out; not shown for the cheapest answer (the first one). */
  choiceFoot(m) {
    const chosen = String(this.value(m.name));
    return m.choice_footnote && chosen !== (m.choice_order || [])[0] ? m.choice_footnote : "";
  },
  choiceSentence(m) {
    return [this.choiceNote(m), this.choiceFoot(m)].filter(Boolean).join(" ");
  },
  /** A number, checked here for a readable message and again on the other side. */
  validateNumber(m, raw) {
    const text = String(raw ?? "").trim();
    const units = m.units || "seconds";
    if (!text) return `Enter a number of ${units}.`;
    const value = Number(text);
    if (!Number.isFinite(value)) return `${m.title} takes a number of ${units}; "${text}" is not one.`;
    if (m.low !== null && m.low !== undefined && m.high !== null && m.high !== undefined && (value < m.low || value > m.high)) {
      return `Accepts ${m.low} to ${m.high}.`;
    }
    if (m.integral && !Number.isInteger(value)) return "This one counts whole things, so it takes a whole number.";
    if (m.off_or_at_least && value > 0 && value < m.off_or_at_least) {
      return `Either 0 (off) or at least ${m.off_or_at_least} — anything shorter fires while a healthy provider is still connecting.`;
    }
    return "";
  },
  rangeText(m) {
    if (m.low === null || m.low === undefined || m.high === null || m.high === undefined) return "";
    return `${m.low}–${m.high} ${m.units || ""}${m.off_or_at_least ? `, 0 or ${m.off_or_at_least}+` : ""}`.replace(/ +$/, "");
  },
  errorOf(name) {
    return this.errors[name] || "";
  },
  isDirty(name) {
    return Object.prototype.hasOwnProperty.call(this.drafts, name);
  },
  async saveNumber(m) {
    const raw = this.value(m.name);
    const problem = this.validateNumber(m, raw);
    if (problem) {
      this.errors[m.name] = problem;
      return;
    }
    delete this.errors[m.name];
    await this.save(m.name, String(raw).trim());
  },
  /** A free-text setting: an empty field puts the default back. */
  async saveText(m) {
    const text = String(this.value(m.name) ?? "").trim();
    if (text === "") await this.reset(m.name);
    else await this.save(m.name, text);
  },
  /** Only turning a consequential switch ON asks first: that is the direction that stops KAME doing its job. */
  async flagChanged(m, on) {
    if (on && m.consequential) {
      this.askingFlag = m.name;
      return;
    }
    await this.save(m.name, on);
  },
  flagQuestion(m) {
    return `${m.title}?`;
  },
  flagDescription(m) {
    return `${m.sentence}${CONSEQUENTIAL_TAIL}`;
  },
  async confirmFlag(m) {
    this.askingFlag = "";
    await this.save(m.name, true);
  },
  /** The reading beside the page title: the one count, over keys, and the keys to replace. */
  get headerStatus() {
    const v = this.view;
    if (!v) return { text: "", tone: "idle", replace: 0, age: "", stale: false };
    const t = v.totals || {};
    const active = this.data?.active;
    const stale = !!this.fetchedAt && this.readAgeS > STALE_AFTER_S;
    return {
      text: active ? (v.totals?.phrase || `${t.ready || 0} of ${t.keys || 0} keys ready`) : "not rotating",
      tone: this.said.tone,
      replace: (t.rejected || 0) + (t.retired || 0),
      age: stale ? `reading ${duration(this.readAgeS)} old` : "live",
      stale,
    };
  },
  /** The notes above the page, loudest first (Hermes' banners, in Agent Zero's words). */
  get notes() {
    const d = this.data;
    if (!d) return [];
    const out = [];
    const build = d.build || {};
    if (build.complete === false) {
      out.push({
        id: "incomplete",
        tone: "bad",
        text: "This install is incomplete — KAME is not rotating. " +
          `Missing: ${(build.missing || []).join(", ") || "unknown"}. ` +
          "Re-deploy the whole plugin directory. A copy that does not recurse drops core/ " +
          "and leaves a plugin that registers, reports itself active, and does nothing.",
      });
    }
    if (d.active === false) {
      out.push({
        id: "not-rotating",
        tone: "plain",
        text: `KAME is loaded but every call keeps the key Agent Zero resolved: ${d.reason || "unknown"}`,
      });
    }
    if (this.headerStatus.stale) {
      out.push({
        id: "stale",
        tone: "plain",
        text: `Nothing has been read for ${duration(this.readAgeS)}. The numbers below are the last reading, not the current one — ` +
          "the backend is probably restarting.",
      });
    }
    const totals = this.view?.totals || {};
    const retired = Number(totals.retired) || 0;
    const invalid = (Number(totals.rejected) || 0) + retired;
    const them = (n, one, many) => (n === 1 ? one : many);
    if (retired > 0) {
      out.push({
        id: "retired",
        tone: "bad",
        text: `${retired} ${them(retired, "key has", "keys have")} left rotation — the provider refused ` +
          `${them(retired, "it", "them")} as ${them(retired, "a credential", "credentials")}, ` +
          `so KAME no longer offers ${them(retired, "it", "them")} and nothing is being spent on ` +
          `${them(retired, "it", "them")}. Nothing was deleted: paste the replacement over ` +
          `${them(retired, "it", "them")} in Agent Zero’s Settings > API keys and ${them(retired, "it comes", "they come")} ` +
          "back by itself. Your other keys are carrying every turn meanwhile.",
      });
    }
    if (invalid > retired) {
      const fresh = invalid - retired;
      out.push({
        id: "invalid",
        tone: "plain",
        text: `${fresh} ${them(fresh, "key was", "keys were")} just refused and ${them(fresh, "is", "are")} still being tried. ` +
          "One refusal is not proof — an expired token a second from refreshing sends the same thing — " +
          `so it takes ${d.refusals_before_retiring || 3} in a row, with nothing working in between.`,
      });
    }
    return out;
  },
  /** The fallback list as Hermes shows it inside "If your model cannot answer": the sentence about switching,
   *  then one line per model - how its keys are doing, or that KAME has not served it yet. */
  get fallbackList() {
    const fb = this.view?.fallback;
    if (!fb || fb.state === "unknown") return null;
    const listed = (fb.entries || []).length ? ringModels(this.view).main : [];
    const entries = listed.map((model, index) => {
      const pool = model.pool && model.pool.keys > 0 ? model.pool : null;
      let state;
      if (!pool) state = "KAME has not served this model yet";
      else if (pool.healthy === pool.keys) state = `${pool.keys} keys ready`;
      else if (pool.healthy > 0) state = `${pool.healthy} of ${pool.keys} keys ready`;
      else {
        const back = pool.soonest_s != null ? Math.max(0, pool.soonest_s - this.readAgeS) : null;
        state = `all ${pool.keys} keys resting${back !== null ? `, back in ${duration(back)}` : ""}`;
      }
      return { n: index + 1, identity: model.identity, yours: model.entry?.role === "primary", state };
    });
    return {
      why: fb.why,
      warning: typeof fb.warning === "string" ? fb.warning : "",
      entries,
      how: "Change this list in the Fallback models tab. Background jobs are never raced and never switch on their own account; a stopped important job goes down your list once.",
    };
  },
  /** "This Agent Zero process": the counters of this process, the ones that are not zero shown with what they mean. */
  get processFields() {
    const c = this.data?.counters || {};
    const field = (label, value, hint = "") => ({ label, value, hint });
    const out = [
      field("Calls", c.calls ?? 0),
      field("Rotations", c.rotations ?? 0),
      field("Recovered", c.recovered ?? 0, "answered after a rotation"),
      field("Surfaced", c.surfaced ?? 0, "errors KAME let through"),
    ];
    if (c.handed_over) {
      out.push(field("Fallbacks", c.handed_over, "replies your fallback list answered: the model was busy, resting or too slow"));
    }
    if (c.probe_bursts) {
      out.push(field("Keys asked together", c.probe_bursts, "quota ran out, so several keys were tried at once"));
    }
    if (c.waits) out.push(field("Waited", duration(c.waited_s), `across ${c.waits} pause(s)`));
    if (c.stream_drops) out.push(field("Answers cut", c.stream_drops, "the provider stopped mid-sentence"));
    if (c.stitched) out.push(field("Continued", c.stitched, "finished on another key, as one reply"));
    const backups = Number(this.data?.speed?.backups) || 0;
    if (backups) {
      out.push(field("Backup keys started", backups, `${Number(this.data?.speed?.backup_wins) || 0} won`));
    }
    return out;
  },
  value(name) {
    if (Object.prototype.hasOwnProperty.call(this.drafts, name)) return this.drafts[name];
    const v = this.current[name];
    if (v !== undefined && v !== null) return v;
    const meta = this.settingsMeta.find((m) => m.name === name);
    return meta && meta.default !== undefined && meta.default !== null ? meta.default : "";
  },
  draft(name, v) {
    this.drafts[name] = v;
    delete this.errors[name];
  },
  async save(name, raw) {
    const meta = this.settingsMeta.find((m) => m.name === name);
    let value = raw;
    if (meta?.type === "flag") value = raw ? "true" : "false";
    this.saving = true;
    try {
      const res = await callJsonApi(SET, { name, value: String(value ?? "") });
      this.message = res?.ok ? `${meta?.title || name}: ${res.message}` : (res?.message || "not saved");
      // Saved or refused, the field shows what is in force again.
      delete this.drafts[name];
      await this.refresh();
    } catch (e) {
      this.message = `not saved (${e?.message || e})`;
    } finally {
      this.saving = false;
    }
  },
  async reset(name) {
    this.saving = true;
    try {
      const res = await callJsonApi(SET, { name, reset: true });
      this.message = res?.message || "";
      delete this.drafts[name];
      await this.refresh();
    } catch (e) {
      this.message = `not reset (${e?.message || e})`;
    } finally {
      this.saving = false;
    }
  },

  // -- the fallback editor ----------------------------------------------------------------------

  /** Until the person edits, the rows are the saved list; after that the rows are theirs. */
  syncRows() {
    if (this.rowsDirty) return;
    const saved = this.data?.speed?.fallback_models || [];
    this.rows = saved.map((e) => newRow(e.provider, e.name, e.api_base || ""));
  },
  get fallbackIncomplete() {
    return this.rows.filter((r) => !(String(r.provider).trim() && String(r.name).trim())).length;
  },
  isDraft(row) {
    return !(String(row.provider).trim() && String(row.name).trim());
  },
  addRow() {
    this.rows.push(newRow());
    this.rowsDirty = true;
  },
  removeRow(index) {
    const [gone] = this.rows.splice(index, 1);
    if (gone?.timer) clearTimeout(gone.timer);
    this.rowsDirty = true;
  },
  moveRow(index, by) {
    const to = index + by;
    if (to < 0 || to >= this.rows.length) return;
    const [row] = this.rows.splice(index, 1);
    this.rows.splice(to, 0, row);
    this.rowsDirty = true;
  },
  setProvider(row, value) {
    // a hand-written endpoint belongs to the provider it was written for (review L4)
    if (row.provider !== value) row.api_base = "";
    row.provider = value;
    row.results = [];
    row.open = false;
    this.rowsDirty = true;
  },
  /** Typing in the model box: the name is the person's; the search only offers names. */
  typeModel(row, value) {
    row.name = value;
    this.rowsDirty = true;
    this.search(row, false);
  },
  search(row, now) {
    if (!row.provider) return;
    if (row.timer) clearTimeout(row.timer);
    const run = async () => {
      const found = searchStore() || (await loadModelConfig());
      if (!found) return;
      if (!this.hasSearch) {
        this.hasSearch = true;
        void this.readProviders(found);
      }
      const provider = row.provider;
      const query = row.name;
      row.searching = true;
      try {
        const r = await found.searchModelsDetailed(provider, query, "chat", row.api_base || "");
        // an answer for a model box that has moved on is not shown
        if (provider !== row.provider || query !== row.name) return;
        row.results = r.models || [];
        row.source = r.source || "";
        row.error = !!r.error;
        row.active = -1;
        row.open = true;
      } catch {
        row.results = [];
        row.error = true;
      } finally {
        row.searching = false;
      }
    };
    if (now) void run();
    else row.timer = setTimeout(run, SEARCH_DEBOUNCE_MS);
  },
  grouped(row) {
    const found = searchStore();
    if (found?.groupResults) return found.groupResults(row.results, row.name);
    return { matched: [], rest: row.results };
  },
  /** The suggestions as one list (the matches first, as Agent Zero's field shows them), with their place. */
  items(row) {
    const g = this.grouped(row);
    return [...g.matched.map((name) => ({ name, matched: true })), ...g.rest.map((name) => ({ name, matched: false }))]
      .map((item, index) => ({ ...item, index }));
  },
  /** Keys in the model box: up and down move, Enter picks, Escape closes. */
  key(row, event) {
    const k = event?.key;
    if (k === "Escape") {
      row.open = false;
      row.active = -1;
      return;
    }
    if (k !== "ArrowDown" && k !== "ArrowUp" && k !== "Enter") return;
    const items = this.items(row);
    if (k === "Enter") {
      const item = row.open ? items[row.active] : null;
      if (item) {
        event.preventDefault?.();
        this.pick(row, item.name);
      }
      return;
    }
    event.preventDefault?.();
    if (!items.length) {
      this.search(row, true);
      return;
    }
    row.open = true;
    row.active = k === "ArrowDown" ? (row.active + 1) % items.length : row.active <= 0 ? items.length - 1 : row.active - 1;
  },
  pick(row, name) {
    row.name = name;
    row.open = false;
    row.active = -1;
    this.rowsDirty = true;
  },
  /** The provider choices for one row: Agent Zero's list, plus the row's own when it is not in it. */
  providerOptions(row) {
    if (!row.provider || this.providers.some((p) => p.value === row.provider)) return this.providers;
    return [...this.providers, { value: row.provider, label: row.provider }];
  },
  async saveRows() {
    const rows = this.rows.map((r) => ({ provider: r.provider, name: r.name, api_base: r.api_base }));
    try {
      const res = await callJsonApi(FALLBACK_SET, { rows });
      this.message = res?.message || (res?.ok ? "Saved." : "not saved");
      if (res?.ok) {
        this.rowsNote = res.message || "";
        // the saved rows keep their place; drafts stay on screen, marked
        const entries = res.entries || [];
        for (const row of this.rows) {
          const hit = entries.find((e) => e.provider === String(row.provider).trim().toLowerCase() && e.name.toLowerCase() === String(row.name).trim().toLowerCase());
          if (hit) row.api_base = hit.api_base || "";
        }
        this.rowsDirty = this.rows.some((r) => this.isDraft(r));
      }
      await this.refresh();
    } catch (e) {
      this.message = `not saved (${e?.message || e})`;
    }
  },
};

export const store = createStore("kamePanel", model);
