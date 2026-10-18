/**
 * KAME panel (1.8.2.7) - Agent Zero's central menu -> KAME.
 *
 * The chip beside the composer says "can it answer me now"; this panel says
 * everything else: each pool, what KAME decided and why (the conversation and
 * the background jobs apart), the fallback models, and the settings everybody
 * needs, editable in place.
 *
 * Counts and fingerprints only: the endpoints cannot return a key.
 */
import { createStore } from "/js/AlpineStore.js";
import { callJsonApi } from "/js/api.js";

const STATUS = "/plugins/api_rotation_by_kame/kame_status";
const SET = "/plugins/api_rotation_by_kame/kame_settings_set";
const REFRESH_MS = 3000;

const KIND_WORDS = {
  rotation: "Key rests",
  switch: "Next key",
  quarantine: "Key benched",
  invalid_key: "Bad key",
  denied_model: "Not for this model",
  storm: "Provider outage",
  stream_drop: "Answer cut",
  wait: "All keys resting",
  recovery: "Answered on retry",
  surfaced: "Passed to Agent Zero",
  setting: "Setting changed",
  model_switch: "Fallback",
  backup: "Backup key",
};

// 1.8.2.8: the engine's short reason in a person's words (Hermes' wording).
const REASON_WORDS = {
  throttled: "a per-minute rate limit",
  "daily quota": "today's quota is spent",
  "out of credit": "the account is out of credit",
  "provider busy": "the provider is busy (overloaded)",
  "no answer in time": "the provider did not answer in time",
  "credential refused": "the provider refused this key",
  "not a valid key": "the provider says this is not a valid key",
  "not allowed on this model": "this key may not use this model",
  "unrecognised error": "an error nothing recognised",
};

// 1.8.2.8: what KAME did next, after an arrow, so each row reads
// "what happened -> what happened next" without a hover.
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

// 1.8.2.8: one sentence per kind, for the legend under the list.
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
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  return `${(s / 3600).toFixed(1).replace(/\.0$/, "")}h`;
}

export function clock(at) {
  const t = Number(at);
  if (!Number.isFinite(t) || t <= 0) return "";
  return new Date(t * 1000).toLocaleTimeString();
}

const model = {
  tab: "overview",
  data: null,
  error: "",
  loading: false,
  showAdvanced: false,
  message: "",
  drafts: {},
  _timer: null,

  async onOpen() {
    this.cleanup();
    this._open = true;
    // The timer first: a panel closed while the first fetch is in flight must
    // not leave a poll running (cleanup clears it).
    this._timer = setInterval(() => this.refresh(), REFRESH_MS);
    await this.refresh();
  },

  cleanup() {
    this._open = false;
    this.drafts = {};
    if (this._timer) clearInterval(this._timer);
    this._timer = null;
  },

  async refresh() {
    if (this.loading) return;
    this.loading = true;
    try {
      this.data = await callJsonApi(STATUS, { panel: true });
      this.error = "";
    } catch (e) {
      this.error = `KAME's status did not load (${e?.message || e}).`;
    } finally {
      this.loading = false;
    }
  },

  get pools() {
    return this.data?.pools || [];
  },
  get speed() {
    return this.data?.speed || {};
  },
  get fallbackModels() {
    return this.speed.fallback_models || [];
  },
  get inFlight() {
    return this.data?.in_flight || [];
  },
  get settingsMeta() {
    return this.data?.settings_meta || [];
  },
  get current() {
    return this.data?.settings || {};
  },

  events(background) {
    const rows = this.data?.events || [];
    return rows.filter((r) => {
      const bg = r.job === "utility" || r.job === "compress";
      return background ? bg : !bg;
    });
  },

  reasonText(e) {
    // Hermes 1.8.2.6 words: a throttle that names no wait is usually the
    // provider being busy, not your quota.
    const r = String(e?.reason || "");
    let said = REASON_WORDS[r] || r;
    if (r === "throttled" && !["provider", "window"].includes(String(e?.sized_by || ""))) {
      said = "throttled - usually the provider is busy, not your quota";
    }
    const next = KIND_NEXT[e?.kind] || "";
    return next ? (said ? `${said} \u2192 ${next}` : next) : said;
  },

  get meanings() {
    return Object.entries(KIND_MEANING).map(([kind, text]) => ({ kind, word: KIND_WORDS[kind] || kind, text }));
  },

  get seams() {
    const host = this.data?.host || {};
    const rows = Object.entries(host.entry_points || {}).map(([name, v]) => ({ name, ...v }));
    return { rows, factory: host.fallback_factory, layer: host.layer };
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

  settingsIn(group) {
    // 1.8.2.8: a number that belongs to one answer of a choice ("After my
    // time") is shown only under that answer.
    return this.settingsMeta.filter((m) => m.group === group &&
      !(m.depends_on && String(this.value(m.depends_on[0])) !== m.depends_on[1]));
  },

  choicesOf(m) {
    return m.choice_order || m.choices || [];
  },

  choiceLabel(m, c) {
    return (m.labels && m.labels[c]) || c;
  },

  get replies() {
    return this.data?.replies || [];
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

  poolLine(pool) {
    const eta = seconds(pool.eta);
    const parts = [`${pool.ready} of ${pool.total} ready`];
    if (pool.resting) parts.push(`${pool.resting} resting${eta ? `, next back in ${eta}` : ""}`);
    if (pool.retired) parts.push(`${pool.retired} out of rotation`);
    return parts.join(" · ");
  },
};

export const store = createStore("kamePanel", model);
