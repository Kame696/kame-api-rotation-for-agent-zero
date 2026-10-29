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
  clockOf,
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
const REFRESH_MS = 3000;
const SEARCH_DEBOUNCE_MS = 300;

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
let modelConfig = null;
async function loadModelConfig() {
  try {
    const mod = await import(MODEL_CONFIG);
    const found = mod.store || globalThis.Alpine?.store?.("modelConfig");
    if (!found || typeof found.searchModelsDetailed !== "function") return null;
    await found.ensureLoaded?.();
    modelConfig = found;
  } catch {
    modelConfig = null;
  }
  return modelConfig;
}

let rowSeq = 0;
function newRow(provider = "", name = "", apiBase = "") {
  return { id: ++rowSeq, provider, name, api_base: apiBase, results: [], source: "", open: false, searching: false, timer: null };
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
    const found = await loadModelConfig();
    this.hasSearch = !!found;
    this.providers = found ? (found.chatProviders || []).map((p) => ({ value: p.value, label: p.label })) : [];
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
    return nowSentences(this.view, this.now);
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
  isChanged(m) {
    const v = this.current[m.name];
    return v !== undefined && v !== null && String(v) !== String(m.default);
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
  /** The shelves with the rows to show: advanced ones only when asked (a changed one never hides),
   *  a number that belongs to one answer of a choice only under that answer. */
  get settingCards() {
    const groups = this.data?.setting_groups || [];
    const hidden = (m) =>
      (m.advanced && !this.isChanged(m) && !this.showAdvanced) ||
      (m.depends_on && String(this.value(m.depends_on[0])) !== m.depends_on[1]);
    return groups
      .map((g) => {
        const all = this.settingsMeta.filter((m) => m.group === g.id);
        const rows = all.filter((m) => !hidden(m));
        const changed = rows.filter((m) => this.isChanged(m)).length;
        return {
          id: g.id,
          title: changed ? `${g.title} · ${changed} changed` : g.title,
          note: g.note,
          folded: !!g.folded,
          rows,
          total: all.length,
          shows_fallback: !!g.shows_fallback,
          shows_keys: !!g.shows_keys,
        };
      })
      .filter((c) => c.rows.length || c.shows_fallback || c.shows_keys);
  },
  quickRow(name) {
    return this.settingsMeta.find((m) => m.name === name) || null;
  },
  /** The two choices on the first page (speed, fallback), and the reply line: Hermes' three controls. */
  get quickChoices() {
    return ["hedge_mode", "model_switch"].map((n) => this.quickRow(n)).filter(Boolean);
  },
  get quickNotice() {
    return ["reply_notice"].map((n) => this.quickRow(n)).filter(Boolean);
  },
  get quickTimed() {
    return this.value("model_switch") === "timed" ? this.quickRow("failover_after_seconds") : null;
  },
  choicesOf(m) {
    return m.choice_order || m.choices || [];
  },
  choiceLabel(m, c) {
    return (m.labels && m.labels[c]) || c;
  },
  /** The sentence under a choice's buttons: what the chosen one means (and the footnote). */
  choiceSentence(m) {
    const chosen = String(this.value(m.name));
    const helpText = (m.choice_help && m.choice_help[chosen]) || "";
    // the footnote is not shown for the cheapest answer (the first one)
    const foot = m.choice_footnote && chosen !== (m.choice_order || [])[0] ? m.choice_footnote : "";
    return [helpText, foot].filter(Boolean).join(" ");
  },
  /** The reading beside the page title: the one count, over keys, and the keys to replace. */
  get headerStatus() {
    const v = this.view;
    if (!v) return { text: "", tone: "idle", replace: 0 };
    const t = v.totals || {};
    const active = this.data?.active;
    return {
      text: active ? (v.totals?.phrase || `${t.ready || 0} of ${t.keys || 0} keys ready`) : "not rotating",
      tone: this.said.tone,
      replace: (t.rejected || 0) + (t.retired || 0),
    };
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
  },
  async save(name, raw) {
    const meta = this.settingsMeta.find((m) => m.name === name);
    let value = raw;
    if (meta?.type === "flag") value = raw ? "true" : "false";
    try {
      const res = await callJsonApi(SET, { name, value: String(value ?? "") });
      this.message = res?.ok ? `${meta?.title || name}: ${res.message}` : (res?.message || "not saved");
      // Saved or refused, the field shows what is in force again.
      delete this.drafts[name];
      await this.refresh();
    } catch (e) {
      this.message = `not saved (${e?.message || e})`;
    }
  },
  async reset(name) {
    try {
      const res = await callJsonApi(SET, { name, reset: true });
      this.message = res?.message || "";
      delete this.drafts[name];
      await this.refresh();
    } catch (e) {
      this.message = `not reset (${e?.message || e})`;
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
    if (!modelConfig || !row.provider) return;
    if (row.timer) clearTimeout(row.timer);
    const run = async () => {
      const provider = row.provider;
      const query = row.name;
      row.searching = true;
      try {
        const r = await modelConfig.searchModelsDetailed(provider, query, "chat", row.api_base || "");
        // an answer for a model box that has moved on is not shown
        if (provider !== row.provider || query !== row.name) return;
        row.results = r.models || [];
        row.source = r.source || "";
        row.open = true;
      } catch {
        row.results = [];
      } finally {
        row.searching = false;
      }
    };
    if (now) void run();
    else row.timer = setTimeout(run, SEARCH_DEBOUNCE_MS);
  },
  grouped(row) {
    if (modelConfig?.groupResults) return modelConfig.groupResults(row.results, row.name);
    return { matched: [], rest: row.results };
  },
  pick(row, name) {
    row.name = name;
    row.open = false;
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
