/**
 * KAME view logic (1.8.2.9) - what the API Rotation page and the chip above the message box both say.
 *
 * Pure ES module: no Alpine, no DOM, no network. Everything here is a function of the one status
 * view the backend builds (`status_view()` in kame_engine.py, served inside `kame_status` as
 * `view`), so the chip and the page cannot disagree about a count or a time.
 *
 * Ported from the Hermes panel (desktop/plugin.js: KEY_STATE, ringSegments, ringModels,
 * modelState, ringAria, nowSentences, Moves, SpeedCost, quotaShare) with React removed. The rings
 * are returned as SVG text because Alpine cannot repeat an element inside an <svg>.
 *
 * Counts and fingerprints only: the view carries no key, so nothing here can show one.
 */

// -- words -------------------------------------------------------------------------------------

export const RING_COLOUR = {
  bad: "var(--color-error, #ef4444)",
  day: "var(--color-text-quaternary, #8a8f98)",
  ready: "#38bdf8",
  rest: "#f59e0b",
};

/** Per key state: how it is drawn (colour AND shape, so nobody needs to tell colours apart) and
 *  how it reads. `width` is the stroke; `dash` makes a dotted arc. */
export const KEY_STATE = {
  d: { colour: RING_COLOUR.day, dash: "1.2 2.6", glyph: "┈", label: "out for today", width: 6 },
  f: { colour: RING_COLOUR.ready, dash: null, glyph: "◉", label: "answering now", width: 9 },
  o: { colour: RING_COLOUR.bad, dash: "1.2 2.6", glyph: "×", label: "out of rotation — replace it", width: 6 },
  r: { colour: RING_COLOUR.ready, dash: null, glyph: "●", label: "ready", width: 9 },
  s: { colour: RING_COLOUR.rest, dash: null, glyph: "◦", label: "resting", width: 4.5 },
  x: { colour: RING_COLOUR.bad, dash: null, glyph: "×", label: "refused — still being tried", width: 4.5 },
};

export const KEY_ORDER = ["f", "r", "s", "d", "x", "o"];

/** What tripped a key, in two or three words - the long sentences live in the Events tab. */
export const KIND_SHORT = {
  auth: "refused as a credential",
  billing: "out of credit",
  daily: "daily quota spent",
  denied: "refused",
  empty: "empty answer",
  insufficient_quota: "out of credit",
  other: "refused",
  per_minute: "per-minute limit",
  rate_limit: "rate limit",
  revoked: "key revoked",
  server: "provider error",
  timeout: "timed out",
};

/** One character per kind of move, so a line is never told by colour alone. */
export const MOVE_ICON = {
  backup: "◐",
  denied_model: "!",
  invalid_key: "!",
  model_switch: "→",
  quarantine: "■",
  recovery: "✓",
  rotation: "↻",
  storm: "!",
  stream_drop: "↻",
  surfaced: "!",
  switch: "↻",
  wait: "⏸",
};

export const KIND_WORDS = {
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

/** The engine's short reason in a person's words. */
export const REASON_WORDS = {
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

// -- time ---------------------------------------------------------------------------------------

/** Seconds from now to an epoch deadline, never negative; null when there is no deadline. */
export function untilEpoch(epoch, nowMs) {
  return typeof epoch === "number" && Number.isFinite(epoch) ? Math.max(0, epoch - nowMs / 1000) : null;
}

const two = (n) => String(n).padStart(2, "0");

/**
 * The ONE formatter of a time LEFT (the twin of `_kame_fmt_left` in the engine): `45 s`, `12 min`,
 * `1 h 05 min`, and past three hours the clock time it ends, `back at 15:40`. Never decimals of hours.
 */
export function fmtLeft(seconds, nowMs = Date.now()) {
  const s = Number(seconds);
  if (!Number.isFinite(s)) return "a while";
  const left = Math.max(0, s);
  if (left < 59.5) return `${Math.max(1, Math.round(left))} s`;
  if (left < 3570) return `${Math.round(left / 60)} min`;
  if (left < 3 * 3600) {
    const minutes = Math.round(left / 60);
    return `${Math.floor(minutes / 60)} h ${two(minutes % 60)} min`;
  }
  const at = new Date(nowMs + left * 1000);
  return `back at ${two(at.getHours())}:${two(at.getMinutes())}`;
}

/** `back in 12 min` / `back at 15:40`: a time left, ready to follow a name. */
export function backIn(seconds, nowMs = Date.now()) {
  const text = fmtLeft(seconds, nowMs);
  return text.startsWith("back at") ? text : `back in ${text}`;
}

/** `8 min ago`, `just now`. */
export function ago(seconds) {
  const s = Number(seconds);
  if (!Number.isFinite(s) || s < 5) return "just now";
  if (s < 60) return `${Math.round(s)} s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

/** `08:10` from epoch seconds, in the viewer's own clock. */
export function clockOf(epochSeconds) {
  const t = Number(epochSeconds);
  if (!Number.isFinite(t) || t <= 0) return "";
  const at = new Date(t * 1000);
  return `${two(at.getHours())}:${two(at.getMinutes())}`;
}

/** `2 keys` / `1 key`. */
export function count(n, one, many) {
  return `${n} ${n === 1 ? one : many}`;
}

// -- models and keys ---------------------------------------------------------------------------

/** The same model whether the list wrote it ("google:gemini-3.7-flash") or LiteLLM named it
 *  ("google:gemini/gemini-3.7-flash"): the twin of `_kame_names_match`. */
export function namesMatch(a, b) {
  const x = String(a || "").toLowerCase();
  const y = String(b || "").toLowerCase();
  if (x === y) return true;
  if (!x.includes(":") || !y.includes(":")) return false;
  const [px, mx] = [x.slice(0, x.indexOf(":")), x.slice(x.indexOf(":") + 1)];
  const [py, my] = [y.slice(0, y.indexOf(":")), y.slice(y.indexOf(":") + 1)];
  return px === py && mx.split("/").pop() === my.split("/").pop();
}

/** The pool a list entry (or an activity identity) belongs to. */
export function poolOf(view, identity) {
  return (view?.pools || []).find((pool) => namesMatch(pool.identity, identity));
}

/** The model's full name as the person wrote it, never cut. */
export function labelOf(view, identity) {
  const pool = poolOf(view, identity);
  if (pool?.label) return pool.label;
  const entry = (view?.fallback?.entries || []).find((e) => namesMatch(e.identity, identity));
  if (entry?.label) return entry.label;
  const text = String(identity || "");
  return text.includes(":") ? text.slice(text.indexOf(":") + 1) : text;
}

/** How many keys of a pool are in each state: `{ r, f, s, d, x, o }`. */
export function keyCounts(pool) {
  const out = { d: 0, f: 0, o: 0, r: 0, s: 0, x: 0 };
  for (const row of pool?.key_states || []) out[row[1]] = (out[row[1]] ?? 0) + 1;
  return out;
}

/** The models to draw, in the order of the user's list; pools KAME served that are not in the list
 *  go in `also`. Without a list: the pools seen, the freshest first. */
export function ringModels(view) {
  const entries = [];
  for (const entry of view?.fallback?.entries || []) {
    if (!entries.some((e) => namesMatch(e.identity, entry.identity))) entries.push(entry);
  }
  const pools = view?.pools || [];
  const used = new Set();
  const main = entries.map((entry, index) => {
    const pool = poolOf(view, entry.identity);
    if (pool) used.add(pool.identity);
    return { entry, identity: entry.identity, label: labelOf(view, entry.identity), pool, rank: index + 1 };
  });
  const rest = pools.filter((pool) => !used.has(pool.identity));
  if (!entries.length) {
    return {
      also: [],
      main: [...rest]
        .sort((a, b) => (a.idle_for_s ?? 1e12) - (b.idle_for_s ?? 1e12))
        .map((pool, index) => ({ entry: null, identity: pool.identity, label: pool.label, pool, rank: index + 1 })),
    };
  }
  return {
    also: rest.map((pool) => ({ entry: null, identity: pool.identity, label: pool.label, pool, rank: 0 })),
    main,
  };
}

/** What a model is doing, in a few words: `{ word, tone }`. */
export function modelState(model, view, nowMs) {
  const { entry, pool } = model;
  // 1.8.3.0 (D4): keys Agent Zero holds for a model that has not been called yet are known, not used
  if (!pool || !pool.keys || pool.unused) return { tone: "quiet", word: "not used yet" };
  const busy = untilEpoch(entry?.busy_until, nowMs);
  if (busy) {
    const again = untilEpoch(entry?.probe_at, nowMs);
    return entry?.busy_kind === "slow"
      ? { tone: "warn", word: `slow today — skipped for ${fmtLeft(busy, nowMs)}` }
      : { tone: "warn", word: again ? `busy — asked again in ${fmtLeft(again, nowMs)}` : "busy — asked again soon" };
  }
  const counts = keyCounts(pool);
  const ready = counts.r + counts.f;
  const total = pool.keys ?? 0;
  if (total && counts.d >= total - counts.o - counts.x && !ready && counts.d) return { tone: "bad", word: "out for today" };
  if (!ready && total) {
    const soonest = Math.min(
      ...(pool.key_states || []).filter((row) => row[2]).map((row) => untilEpoch(row[2], nowMs) ?? Infinity),
      Infinity,
    );
    return {
      tone: "bad",
      word: Number.isFinite(soonest) ? `resting — first key back in ${fmtLeft(soonest, nowMs)}` : "resting",
    };
  }
  return { tone: "good", word: "ready" };
}

function isAnswering(model, view) {
  const id = view?.activity?.identity;
  return Boolean(id) && Boolean(model.pool) && namesMatch(model.pool.identity, id);
}

/** The sentence a screen reader gets for a ring (the SVG itself is hidden from it). */
export function ringAria(model, view, nowMs) {
  const counts = keyCounts(model.pool);
  const state = modelState(model, view, nowMs);
  const parts = [
    `${model.label}${model.rank === 1 ? ", your model" : model.rank ? `, number ${model.rank} in your list` : ""}`,
    model.pool && model.pool.keys ? `${counts.r + counts.f} of ${model.pool.keys} keys ready` : "no key used yet",
    counts.s ? `${counts.s} resting` : "",
    counts.d ? `${counts.d} out for today` : "",
    counts.x + counts.o ? `${counts.x + counts.o} to replace` : "",
    state.word,
    isAnswering(model, view) ? "Answering now" : "",
  ];
  return parts.filter(Boolean).join(", ");
}

// -- the rings ---------------------------------------------------------------------------------

/** The arcs of one ring: one per key (in the stable order the backend gives), or - past 40 keys,
 *  where a ring of slivers is noise - one proportional arc per state. Degrees from 12 o'clock. */
export function ringSegments(keyStates) {
  const states = Array.isArray(keyStates) ? keyStates : [];
  const total = states.length;
  if (!total) return [];
  if (total > 40) {
    const counts = {};
    for (const row of states) counts[row[1]] = (counts[row[1]] ?? 0) + 1;
    let at = 0;
    return KEY_ORDER.filter((code) => counts[code]).map((code) => {
      const span = (counts[code] / total) * 360;
      const out = { code, count: counts[code], end: at + span - 1, start: at + 1 };
      at += span;
      return out;
    });
  }
  const slot = 360 / total;
  const gap = total === 1 ? 0 : Math.min(2, slot / 3);
  return states.map((row, index) => ({
    code: row[1],
    count: 1,
    end: (index + 1) * slot - gap / 2,
    fingerprint: row[0],
    start: index * slot + gap / 2,
    until: row[2],
  }));
}

/** SVG path of an arc on the ring (radius `r`, centre 50,50). */
export function arcPath(r, from, to) {
  const point = (angle) => {
    const a = (angle * Math.PI) / 180;
    return [50 + r * Math.sin(a), 50 - r * Math.cos(a)].map((n) => n.toFixed(2)).join(" ");
  };
  return `M ${point(from)} A ${r} ${r} 0 ${to - from > 180 ? 1 : 0} 1 ${point(to)}`;
}

const attr = (name, value) => (value === null || value === undefined ? "" : ` ${name}="${value}"`);

/** One model as a ring, as SVG text: a segment per key, the halo (solid answering, dashed busy,
 *  dotted slow), the number of ready keys in the middle. `small` is the model outside the list. */
export function ringSvg(model, view, nowMs) {
  const pool = model.pool && model.pool.keys ? model.pool : null;
  const counts = keyCounts(pool);
  const segments = ringSegments(pool?.key_states || []);
  const entry = model.entry;
  const busy = untilEpoch(entry?.busy_until, nowMs);
  const answering = isAnswering(model, view);
  const parts = [];
  if (!pool) parts.push(`<circle cx="50" cy="50" r="40" fill="none" stroke="${RING_COLOUR.day}" stroke-width="3"/>`);
  if (answering) {
    parts.push(`<circle cx="50" cy="50" r="47" fill="none" stroke="${busy ? RING_COLOUR.rest : RING_COLOUR.ready}" stroke-width="1.4"/>`);
  } else if (busy) {
    parts.push(
      `<circle cx="50" cy="50" r="47" fill="none" stroke="${RING_COLOUR.rest}" stroke-width="1.4"` +
        `${attr("stroke-dasharray", entry?.busy_kind === "slow" ? "1 3" : "5 3")}/>`,
    );
  }
  const marks = [];
  segments.forEach((segment) => {
    const style = KEY_STATE[segment.code] || KEY_STATE.r;
    if (segments.length === 1 && segment.count === 1) {
      parts.push(
        `<circle cx="50" cy="50" r="40" fill="none" stroke="${style.colour}" stroke-width="${style.width}"` +
          `${attr("stroke-dasharray", style.dash)}/>`,
      );
    } else {
      parts.push(
        `<path d="${arcPath(40, segment.start, segment.end)}" fill="none" stroke="${style.colour}" ` +
          `stroke-width="${style.width}"${attr("stroke-dasharray", style.dash)}/>`,
      );
    }
    if (segment.code === "f" || segment.code === "x" || segment.code === "o") {
      const a = (((segment.start + segment.end) / 2) * Math.PI) / 180;
      const x = (50 + 47 * Math.sin(a)).toFixed(2);
      marks.push(
        segment.code === "f"
          ? `<circle cx="${x}" cy="${(50 - 47 * Math.cos(a)).toFixed(2)}" r="2.2" fill="${RING_COLOUR.ready}"/>`
          : `<text x="${x}" y="${(53 - 47 * Math.cos(a)).toFixed(2)}" font-size="9" text-anchor="middle" fill="${RING_COLOUR.bad}">×</text>`,
      );
    }
  });
  parts.push(...marks);
  parts.push(
    `<text x="50" y="56" font-size="24" font-weight="600" text-anchor="middle" fill="currentColor">${pool ? counts.r + counts.f : "–"}</text>`,
  );
  if (pool) {
    parts.push(`<text x="50" y="69" font-size="9" opacity="0.6" text-anchor="middle" fill="currentColor">/${pool.keys}</text>`);
  }
  return `<svg viewBox="0 0 100 100" aria-hidden="true" class="kame-ring-svg${pool ? "" : " kame-ring-hollow"}">${parts.join("")}</svg>`;
}

/** The key shapes, drawn small, so the colours are never the only code. */
export function legendItems() {
  return [
    { code: "r", text: "ready" },
    { code: "s", text: "resting (thin)" },
    { code: "d", text: "out for today (dotted)" },
    { code: "x", text: "refused / replace (×)" },
  ].map((item) => ({
    ...item,
    svg:
      `<svg viewBox="0 0 20 8" aria-hidden="true" class="kame-legend-svg"><line x1="1" x2="19" y1="4" y2="4" ` +
      `stroke="${KEY_STATE[item.code].colour}" stroke-width="${Math.min(7, KEY_STATE[item.code].width * 0.7)}"` +
      `${attr("stroke-dasharray", KEY_STATE[item.code].dash ? "1.2 2.2" : null)}/></svg>`,
  }));
}

// -- one count, over physical keys ------------------------------------------------------------

/** `1 of 3 keys ready`: the one count every surface says. */
export function totalsPhrase(totals) {
  if (totals && typeof totals.phrase === "string" && totals.phrase) return totals.phrase;
  return `${Number(totals?.ready) || 0} of ${Number(totals?.keys) || 0} keys ready`;
}

/** The physical keys behind `key_groups`: a key shared by several models is ONE entry. A key is
 *  ready when it is ready for any model (the rule `totals` counts by). `[{provider, fingerprint,
 *  api_base, code, until}]`, in the order of the groups. */
export function physicalKeys(view) {
  const slots = new Map();
  for (const group of view?.key_groups || []) {
    const slot = `${group.provider}\u0000${group.fingerprint}\u0000${group.api_base || ""}`;
    const row = slots.get(slot) || {
      provider: group.provider, fingerprint: group.fingerprint, api_base: group.api_base || "", codes: [], until: null,
    };
    row.codes.push(group.code);
    if (typeof group.until_epoch === "number" && (row.until === null || group.until_epoch < row.until)) {
      row.until = group.until_epoch;
    }
    slots.set(slot, row);
  }
  return [...slots.values()].map((row) => {
    const ready = row.codes.includes("r") || row.codes.includes("f");
    const code = ready ? "r" : KEY_ORDER.find((c) => row.codes.includes(c)) || "s";
    return { provider: row.provider, fingerprint: row.fingerprint, api_base: row.api_base, code, until: ready ? null : row.until };
  });
}

/** Keys grouped for the lists: by provider, then by key, with the full names of the models that use
 *  each key (wrapping, never cut). A key whose models all share a state is ONE row. */
export function keyRows(view, nowMs) {
  const byProvider = new Map();
  for (const group of view?.key_groups || []) {
    const style = KEY_STATE[group.code] || KEY_STATE.r;
    const left = untilEpoch(group.until_epoch, nowMs);
    const why = group.kind && group.code !== "r" ? KIND_SHORT[group.kind] || group.kind : "";
    const words = [why || style.label];
    // a refused credential does not come back by waiting: no countdown for it
    if (left && group.code !== "o" && group.code !== "x") words.push(backIn(left, nowMs));
    const rows = byProvider.get(group.provider) || [];
    rows.push({
      fingerprint: String(group.fingerprint).replace(/^key:/, ""),
      api_base: group.api_base || "",
      code: group.code,
      glyph: style.glyph,
      colour: style.colour,
      words: words.join(" · "),
      models: [...(group.models || [])],
      modelsLine: `${(group.models || []).length} model${(group.models || []).length === 1 ? "" : "s"}: ${(group.models || []).join(", ")}`,
      shared: (group.models || []).length > 1,
    });
    byProvider.set(group.provider, rows);
  }
  // a key that is in two states on two models is two rows but one key
  return [...byProvider.entries()].map(([provider, rows]) => ({
    provider,
    rows,
    keyCount: new Set(rows.map((r) => r.fingerprint)).size,
  }));
}

// -- the three sentences -----------------------------------------------------------------------

/** A reading older than this is called old (the header says so, the second sentence says so). */
export const STALE_READING_S = 30;

/** Hermes' duration: `45s`, `1m 15s`, `2h 05m`. */
export function durationWords(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return `${minutes}m ${String(total % 60).padStart(2, "0")}s`;
  return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, "0")}m`;
}

/** The three lines at the top of the Overview: what is happening, anything wrong, what is next.
 *  `view` is `status_view()` (the page adds `active` and `build` from the status report).
 *  `ageS` is how long ago the page last read the status (Hermes: the age of the snapshot): an old reading
 *  says so in the second sentence instead of describing a state that may be gone.
 *  `icon` is a character, never colour alone. */
export function nowSentences(view, nowMs, ageS = 0) {
  const nowSec = nowMs / 1000;
  const activity = view?.activity;
  const entries = view?.fallback?.entries || [];
  const models = ringModels(view).main;
  const usable = models.find((model) => model.pool && (model.pool.unused || modelState(model, view, nowMs).tone === "good"));
  const first = models[0];

  // -- line 1: what is happening
  let icon = "●";
  const untouched = models.every((model) => !model.pool || !model.pool.keys || model.pool.unused);
  let line1 = usable
    ? `Ready — your next reply goes to ${usable.label} (${keyCounts(usable.pool).r + keyCounts(usable.pool).f} of ${usable.pool.keys} keys ready).`
    : untouched
      ? `Ready — nothing has been asked yet.${first ? ` Your first reply goes to ${first.label}.` : ""}`
      : "Nothing is ready right now — see the models below.";
  const lastReply = (view?.replies || [])[0];
  const started = typeof activity?.started_at === "number" ? Math.max(0, nowSec - activity.started_at) : null;

  if (activity?.kind === "calling" && Array.isArray(activity.racing) && activity.racing.length) {
    icon = "◐";
    const joined = activity.racing;
    const last = joined[joined.length - 1];
    const earlier = joined.slice(0, -1);
    line1 =
      (earlier.length
        ? `${activity.model} and ${earlier.join(" and ")} are slow today — ${last} joined`
        : `${activity.model} is late — ${last} races it`) + (started !== null ? ` — ${started.toFixed(1)} s` : "");
  } else if (activity?.kind === "calling" && typeof activity.probe === "string" && activity.probe) {
    icon = "◐";
    line1 = `Answering with ${activity.model} — asking ${activity.probe} again beside it` + (started !== null ? ` — ${started.toFixed(1)} s` : "");
  } else if (activity?.kind === "calling") {
    icon = "◐";
    line1 =
      `Answering with ${activity.model}` +
      (activity.key ? ` — key ${String(activity.key).replace(/^key:/, "")}` : "") +
      (started !== null ? ` — ${started.toFixed(1)} s` : "") +
      (activity.attempt > 1 ? ` (try ${activity.attempt})` : "");
  } else if (activity?.kind === "waiting") {
    icon = "⏸";
    const eta = typeof activity.eta_s === "number" ? untilEpoch((view.generated_at || nowSec) + activity.eta_s, nowMs) : null;
    line1 = `Every key is resting — back in ${eta === null ? "a moment" : fmtLeft(eta, nowMs)}. Nothing is spent while waiting. Esc stops.`;
  } else if (lastReply && lastReply.for && nowSec - lastReply.at < 120) {
    icon = "→";
    line1 = `${lastReply.model} answered for ${lastReply.for} — ${(lastReply.total_s ?? 0).toFixed(1)} s.`;
  }

  // -- line 2: the worst thing wrong, only one
  const sums = (view?.pools || []).reduce(
    (sum, pool) => {
      const c = keyCounts(pool);
      sum.replace += c.x + c.o;
      sum.today += c.d;
      return sum;
    },
    { replace: 0, today: 0 },
  );
  const busyModel = models.find((model) => {
    const word = modelState(model, view, nowMs).word;
    return word.startsWith("busy") || word.startsWith("slow");
  });
  const paused = Object.entries(view?.speed?.paused_until || {}).find(([, until]) => until > nowSec);
  let line2 = "Nothing wrong.";
  let tone = "good";
  if (view?.build?.complete === false) {
    line2 = "This install is incomplete — KAME is not rotating.";
    tone = "bad";
  } else if (ageS > STALE_READING_S) {
    line2 = `This reading is ${durationWords(ageS)} old — Agent Zero may be restarting.`;
    tone = "warn";
  } else if (view?.active === false) {
    line2 = "KAME is loaded but not rotating — see the note below.";
    tone = "warn";
  } else if (models.length && !usable && !untouched) {
    const everyoneBusy = models.every((model) => model.pool && untilEpoch(model.entry?.busy_until, nowMs));
    line2 = everyoneBusy
      ? "Every model is busy — KAME keeps asking, your best first, until one answers."
      : "No model can answer right now — KAME waits for the first key to come back.";
    tone = "bad";
  } else if (sums.replace > 0) {
    line2 = `${count(sums.replace, "key", "keys")} to replace — the provider refused ${sums.replace === 1 ? "it" : "them"} as credentials.`;
    tone = "bad";
  } else if (busyModel) {
    line2 = `${busyModel.label} is ${modelState(busyModel, view, nowMs).word.split(" —")[0]} — your next model answers meanwhile.`;
    tone = "warn";
  } else if (sums.today > 0) {
    line2 = `${count(sums.today, "key is", "keys are")} out of quota for today — the others carry on.`;
    tone = "warn";
  } else if (paused) {
    line2 = `Faster is paused on ${labelOf(view, paused[0])} for ${fmtLeft(paused[1] - nowSec, nowMs)} — a key hit a rate limit.`;
    tone = "warn";
  }

  // -- line 3: what happens next
  let line3 = usable ? `Next reply: ${usable.label}.` : "";
  if (view?.fallback && view.fallback.state !== "on" && view.fallback.why) {
    line3 = view.fallback.why;
  } else if (first && entries.length > 1 && modelState(first, view, nowMs).tone === "warn") {
    const again = untilEpoch(first.entry?.probe_at, nowMs);
    const next = models.find((model) => model !== first && model.pool && (model.pool.unused || modelState(model, view, nowMs).tone === "good"));
    line3 =
      `If ${first.label} stays busy, the next reply starts on ${next ? next.label : "the next model"}` +
      (again ? `; ${first.label} is asked again in ${fmtLeft(again, nowMs)}.` : ".");
  }
  const note = typeof view?.fallback?.warning === "string" ? view.fallback.warning : "";
  return { icon, line1, line2, line3, note, tone };
}

// -- what KAME did -----------------------------------------------------------------------------

/** One move as a sentence: who, what, why, and how often when it repeated. */
export function moveSentence(move) {
  const who = move.key || move.identity || "";
  const word = KIND_WORDS[move.kind] || move.kind;
  const reason = REASON_WORDS[move.reason] || move.reason || "";
  const parts = [who, word.toLowerCase() === who.toLowerCase() ? "" : word.toLowerCase(), reason].filter(Boolean);
  const text = parts.join(" · ");
  return move.repeat ? `${text} — ${move.repeat}` : text;
}

/** Repeated decisions as one row each: the backend's `moves` (consecutive events with the same kind,
 *  key and reason), newest first, without the settings you changed yourself. An older engine without
 *  `moves` gets the same grouping done here from `events`. */
export function groupMoves(view, limit = 6) {
  let moves = Array.isArray(view?.moves) ? view.moves : null;
  if (!moves) {
    moves = [];
    for (const ev of view?.events || []) {
      const who = ev.key || ev.identity || "";
      const last = moves[moves.length - 1];
      if (last && last.kind === ev.kind && (last.key || last.identity) === who && last.reason === (ev.reason || "")) {
        last.count += 1;
        last.first_at = ev.at;
        continue;
      }
      moves.push({ kind: ev.kind, key: ev.key || "", identity: ev.identity || "", reason: ev.reason || "", count: 1, first_at: ev.at, last_at: ev.at });
    }
    for (const row of moves) {
      if (row.count > 1 && row.last_at > row.first_at) {
        const every = (row.last_at - row.first_at) / (row.count - 1);
        row.repeat = `${row.count} times, every ${every >= 3300 ? "hour" : fmtLeft(every)}`;
      } else if (row.count > 1) {
        row.repeat = `${row.count} times`;
      }
    }
  }
  return moves
    .filter((move) => move.kind !== "setting")
    .slice(0, limit)
    .map((move) => ({
      icon: MOVE_ICON[move.kind] || "·",
      text: moveSentence(move),
      repeat: move.repeat || "",
      count: move.count || 1,
      last_at: move.last_at,
      kind: move.kind,
    }));
}

// -- speed and quota ---------------------------------------------------------------------------

/** What the speed mode has really cost, against what it may. `{text, over}` or null. */
export function speedCostLine(speed) {
  if (!speed || !speed.cap_pct || !speed.hour) return null;
  const hour = speed.hour;
  const over = hour.extra_pct !== null && hour.extra_pct !== undefined && speed.cap_pct > 0 && hour.extra_pct > speed.cap_pct * 1.5;
  const sinceStart = speed.since_start;
  const text =
    (hour.replies
      ? `Extra requests this hour: +${hour.extra_pct ?? 0}% (${hour.replies} replies). Your setting allows about +${speed.cap_pct}%.`
      : `No replies this hour yet. Your setting allows about +${speed.cap_pct}% extra requests.`) +
    (sinceStart?.replies && sinceStart.extra_pct !== null && sinceStart.extra_pct !== undefined
      ? ` Since Agent Zero started: +${sinceStart.extra_pct}%.`
      : "");
  return { text, over };
}

/** Requests per model, conversation versus background jobs (titles, summaries...), from `quota_use`. */
export function quotaShare(view) {
  const use = view?.quota_use && typeof view.quota_use === "object" ? view.quota_use : {};
  const models = new Map();
  for (const [identity, row] of Object.entries(use.models || {})) {
    models.set(identity, { main: Number(row?.main) || 0, side: Number(row?.side) || 0, tasks: { ...(row?.tasks || {}) } });
  }
  const forEntry = (identity) => {
    const sum = { main: 0, side: 0, tasks: {} };
    for (const [name, row] of models) {
      if (namesMatch(name, identity)) {
        sum.main += row.main;
        sum.side += row.side;
        for (const [task, n] of Object.entries(row.tasks)) sum.tasks[task] = (sum.tasks[task] ?? 0) + n;
      }
    }
    const total = sum.main + sum.side;
    return { ...sum, pct: total ? Math.round((100 * sum.side) / total) : 0, total };
  };
  return {
    forEntry,
    models,
    since: Number(use.since) || 0,
    stopped: { ...(use.stopped || {}) },
    suggestion: typeof use.suggestion === "string" && use.suggestion ? use.suggestion : null,
  };
}

/** A model's side-job line under its ring: only when background jobs are a real share (10%+). */
export function sideJobsLine(share, identity) {
  const row = share.forEntry(identity);
  return row.side >= 10 && row.pct >= 10 ? `side jobs: ${row.pct}% of requests since ${clockOf(share.since)}` : null;
}

/** The lines of "Background jobs and your quota"; `[]` when there is nothing to say. */
export function backgroundUseLines(view) {
  const share = quotaShare(view);
  const clock = clockOf(share.since);
  const rows = [...share.models.entries()]
    .map(([identity, row]) => ({ identity, ...row, total: row.main + row.side }))
    .filter((row) => row.side > 0)
    .sort((a, b) => b.side - a.side);
  const tasks = (row) =>
    Object.entries(row.tasks)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 3)
      .map(([task, n]) => `${task} ${n}`)
      .join(", ");
  const lines = rows.map(
    (row) =>
      `${labelOf(view, row.identity)}: ${row.total} requests since ${clock}; ${row.side} (${Math.round((100 * row.side) / row.total)}%) were background jobs - ${tasks(row)}`,
  );
  const stopped = Object.entries(share.stopped).filter(([, n]) => n > 0);
  if (stopped.length) lines.push(`Stopped early to save your keys: ${stopped.map(([task, n]) => `${task} ${n}`).join(", ")}`);
  if (share.suggestion) lines.push(share.suggestion);
  return lines;
}

// -- the chip ----------------------------------------------------------------------------------

/** The little ring in the chip: one segment per PHYSICAL key (not per model), `ready/keys` inside. */
export function chipRingSvg(view) {
  const keys = physicalKeys(view);
  const segments = ringSegments(keys.map((k) => [k.fingerprint, k.code, k.until]));
  const parts = [];
  if (!keys.length) {
    parts.push(`<circle cx="50" cy="50" r="40" fill="none" stroke="${RING_COLOUR.day}" stroke-width="9" opacity="0.5"/>`);
  } else if (segments.length === 1 && segments[0].count === 1) {
    const style = KEY_STATE[segments[0].code] || KEY_STATE.r;
    parts.push(`<circle cx="50" cy="50" r="40" fill="none" stroke="${style.colour}" stroke-width="12"${attr("stroke-dasharray", style.dash)}/>`);
  } else {
    for (const segment of segments) {
      const style = KEY_STATE[segment.code] || KEY_STATE.r;
      parts.push(
        `<path d="${arcPath(40, segment.start, segment.end)}" fill="none" stroke="${style.colour}" stroke-width="${Math.max(7, style.width + 3)}"${attr("stroke-dasharray", style.dash)}/>`,
      );
    }
  }
  return `<svg viewBox="0 0 100 100" aria-hidden="true">${parts.join("")}</svg>`;
}

/** Everything the chip and its popover show, from the status report (`data.view` plus `active`). */
export function chipSummary(data, nowMs) {
  const view = data && typeof data === "object" && data.view ? { ...data.view, active: data.active, build: data.build } : null;
  if (!view || !data.available) {
    return { tone: "idle", label: "–", tip: "KAME has nothing to show yet.", ring: chipRingSvg(null), aria: "KAME API Rotation: no reading yet",
             said: null, headline: "Waiting for the first call", counts: "", providers: [], moves: [], version: "" };
  }
  const said = nowSentences(view, nowMs);
  const totals = view.totals || {};
  const keys = Number(totals.keys) || 0;
  const ready = Number(totals.ready) || 0;
  let soonest = null;
  for (const group of view.key_groups || []) {
    const left = untilEpoch(group.until_epoch, nowMs);
    if (left && group.code !== "o" && group.code !== "x" && (soonest === null || left < soonest)) soonest = left;
  }
  let tone = said.tone === "good" ? "ok" : said.tone;
  let headline = said.line1;
  if (!data.active) {
    tone = "idle";
    headline = data.layer === 3 ? "Not attached — Agent Zero is running natively" : "Waiting for the first call";
  } else if (!keys) {
    tone = "idle";
    headline = "No key seen yet — KAME learns them on the first call";
  } else if (ready === 0) {
    tone = "bad";
  }
  const counts = keys
    ? `${totalsPhrase(totals)}${ready < keys && soonest ? ` · next key ${backIn(soonest, nowMs)}` : ""}`
    : "";
  return {
    tone,
    label: keys ? `${ready}/${keys}` : "–",
    tip: said.line1,
    ring: chipRingSvg(view),
    aria: keys ? `KAME API Rotation: ${totalsPhrase(totals)}` : "KAME API Rotation: no key seen yet",
    said,
    headline,
    counts,
    providers: keyRows(view, nowMs),
    moves: groupMoves(view, 6),
    version: String(data.version || ""),
    retired: Number(totals.retired) || 0,
  };
}

// -- the live line and the chip popover (1.8.3.1, D2 / D3) ---------------------------------------
//
// The words below are the shared table `tests/fixtures/live_words_1831.json`, byte-identical in the Hermes
// port: the node check runs every row through these functions.

/** The one live sentence for an activity (the twin of `_kame_live_line` in the engine). Seven states, icons
 *  included: calling, retrying, racing, standin, waiting, stitching, recovered. `phase` (`racing` /
 *  `standin`) rides beside `kind: "calling"`. `etaSeconds` is the time left as the page counts it (null =
 *  no estimate). `""` when there is nothing to say. */
export function liveLineText(activity, etaSeconds) {
  if (!activity || typeof activity !== "object") return "";
  const kind = String(activity.phase || activity.kind || "");
  const model = activity.name ?? activity.model ?? "";
  const ready = activity.ready ?? activity.healthy;
  const keys = activity.keys;
  const counted = `${ready ?? "?"} of ${keys ?? "?"} keys ready`;
  if (kind === "calling") {
    const resting = (activity.attempt ?? 1) > 1 || (ready != null && keys != null && Number(ready) < Number(keys));
    return resting ? `↻ ${model}: trying another key · ${counted}` : `⏳ Asking ${model} · ${counted}`;
  }
  if (kind === "racing") return `⚡ ${model} is slow to start - a backup key is asking too`;
  if (kind === "standin") return `↪ ${model} can't answer now - ${activity.other ?? ""} is answering instead`;
  if (kind === "waiting") {
    return etaSeconds == null
      ? `⏳ Every key of ${model} is resting - waiting for one to come back`
      : `⏳ Every key of ${model} is resting · next key in ${durationWords(etaSeconds)}`;
  }
  if (kind === "stitching") {
    const done = activity.n ?? activity.resume;
    const limit = activity.max ?? activity.budget;
    return `↻ The answer was cut off - continuing it on another key${done != null && limit != null ? ` (${done} of ${limit})` : ""}`;
  }
  if (kind === "recovered") return `↻ ${model} is back after ${durationWords(activity.duration_s ?? activity.waited_s ?? 0)}`;
  return "";
}

/** "2nd", "3rd", "11th", "22nd". */
export function ordinal(n) {
  const tens = n % 100;
  const unit = n % 10;
  const suffix = tens >= 11 && tens <= 13 ? "th" : unit === 1 ? "st" : unit === 2 ? "nd" : unit === 3 ? "rd" : "th";
  return `${n}${suffix}`;
}

/** One sentence for a reply (`reply` is the generic shape: `kind`, `model`, `other`, `seconds`, `key_index`,
 *  `waited_s`, `why`, `reason`). `""` for nothing or an unknown kind. */
export function replyLineText(reply) {
  if (!reply || typeof reply !== "object") return "";
  const model = reply.model ?? "";
  const seconds = reply.seconds == null ? null : Number(reply.seconds);
  const took = seconds === null ? "" : ` in ${seconds.toFixed(1)}s`;
  const kind = String(reply.kind ?? "");
  if (kind === "answered") return `✓ ${model} answered${took}`;
  if (kind === "retried") return `↻ ${model} answered on the ${ordinal(Number(reply.key_index) || 2)} key${took}`;
  if (kind === "backup") return `⚡ a backup key of ${model} answered first${seconds === null ? "" : ` (${seconds.toFixed(1)}s)`}`;
  if (kind === "standin") return `↪ ${model} answered for ${reply.other ?? ""}${reply.why ? ` (${reply.why})` : ""}${took}`;
  if (kind === "waited") return `⏳ waited ${durationWords(reply.waited_s ?? 0)} for a key, then ${model} answered${took}`;
  if (kind === "cut") return `↻ ${model}'s answer was cut off and finished on another key`;
  if (kind === "failed") return `✕ no answer: ${reply.reason ?? "the request failed"}`;
  return "";
}

/** A row of the engine's `replies` in the generic shape `replyLineText` reads. The most telling thing wins:
 *  no answer, a stand-in, a cut answer, a wait, a backup key, a retry. Old rows have only `model`. */
export function replyFromRow(row) {
  const seconds = row?.total_s ?? null;
  const model = row?.name ?? row?.model ?? "";
  if (row?.failed) return { kind: "failed", model, reason: row.reason };
  if (row?.for) return { kind: "standin", model, other: row.for_name ?? row.for, seconds, why: row.why };
  if (row?.cut) return { kind: "cut", model };
  if (row?.waited_s) return { kind: "waited", model, seconds, waited_s: row.waited_s };
  if (row?.backup) return { kind: "backup", model, seconds };
  if ((row?.attempts ?? 1) > 1) return { kind: "retried", model, key_index: row.attempts, seconds };
  return { kind: "answered", model, seconds };
}

const SPEED_LABEL = { adaptive: "Balanced", max: "Faster", off: "Saver" };

/** The route LiteLLM puts in front of a model name when the provider's own name is not the route's
 *  (`google` -> `gemini`); the twin of `_KAME_ROUTE_ALIASES` in the engine. */
const ROUTE_ALIASES = { google: "gemini" };

/** A model's name as the user wrote it, without the provider or LiteLLM's route (1.8.3.1, review R10): the
 *  twin of `_kame_live_name`. `google:gemini/gemini-3.8-flash` -> `gemini-3.8-flash`; a vendor path that
 *  belongs to the model (`openrouter:meta-llama/llama-4`) stays. Falls back to the pool's own label. */
export function modelName(model) {
  const identity = String(model?.identity ?? "");
  const colon = identity.indexOf(":");
  if (colon < 0) return String(model?.label ?? identity);
  const provider = identity.slice(0, colon).toLowerCase();
  const rest = identity.slice(colon + 1);
  const slash = rest.indexOf("/");
  if (slash > 0) {
    const first = rest.slice(0, slash).toLowerCase();
    if (first === provider || first === (ROUTE_ALIASES[provider] ?? "")) return rest.slice(slash + 1);
  }
  return rest;
}

/** How many models and replies the popover lists. */
export const CHIP_MODELS_MAX = 6;
export const CHIP_REPLIES_MAX = 5;

const PRODUCT = "KAME API Rotation";

/** One model's status, on the right of its row: `{ text, tone }`. */
export function chipRowStatus(model, view, nowMs) {
  const { entry, pool } = model;
  if (!pool || !pool.keys || pool.unused) return { text: "not used yet", tone: "quiet" };
  const busy = untilEpoch(entry?.busy_until, nowMs);
  if (busy) {
    const again = untilEpoch(entry?.probe_at, nowMs);
    return entry?.busy_kind === "slow"
      ? { text: `slow today · skipped for ${durationWords(busy)}`, tone: "warn" }
      : { text: again ? `busy · asked again in ${durationWords(again)}` : "busy · asked again soon", tone: "warn" };
  }
  const counts = keyCounts(pool);
  const ready = counts.r + counts.f;
  const total = pool.keys ?? 0;
  const refused = counts.x + counts.o;
  if (!ready && total) {
    if (refused) {
      return { text: `${count(refused, "key", "keys")} refused - replace ${refused === 1 ? "it" : "them"}`, tone: "bad" };
    }
    const soonest = Math.min(
      ...(pool.key_states || []).filter((row) => row[2]).map((row) => untilEpoch(row[2], nowMs) ?? Infinity),
      Infinity,
    );
    if (counts.d && !counts.s) return { text: "out for today", tone: "bad" };
    return { text: Number.isFinite(soonest) ? `resting · back in ${durationWords(soonest)}` : "resting", tone: "bad" };
  }
  return { text: `${ready} of ${total} ready`, tone: ready === total ? "good" : "warn" };
}

/** A small ring for a popover row, as SVG text (Alpine cannot repeat an element inside an <svg>): one arc
 *  per key in the page's colours and shapes. */
export function miniRingSvg(segments) {
  const parts = [];
  if (!segments.length) {
    parts.push(`<circle cx="50" cy="50" r="38" fill="none" stroke="${RING_COLOUR.day}" stroke-width="12"/>`);
  }
  for (const segment of segments) {
    const style = KEY_STATE[segment.code] || KEY_STATE.r;
    if (segments.length === 1 && segment.count === 1) {
      parts.push(`<circle cx="50" cy="50" r="38" fill="none" stroke="${style.colour}" stroke-width="12"${attr("stroke-dasharray", style.dash)}/>`);
    } else {
      parts.push(
        `<path d="${arcPath(38, segment.start, segment.end)}" fill="none" stroke="${style.colour}" stroke-width="12"${attr("stroke-dasharray", style.dash)}/>`,
      );
    }
  }
  return `<svg viewBox="0 0 100 100" aria-hidden="true">${parts.join("")}</svg>`;
}

function popoverTone(view, active, stale) {
  if (!view || !active || stale) return "muted";
  const totals = view.totals || {};
  const keys = Number(totals.keys) || 0;
  const ready = Number(totals.ready) || 0;
  if (!keys) return "muted";
  if (!ready) return "bad";
  return ready < keys || Number(totals.retired) > 0 ? "warn" : "good";
}

/** Everything the chip's popover shows, as plain data (D3): a header, one sentence in the state colour, the
 *  keys line, the models of the ladder (up to six), the last replies (up to five), the retired-key sentence
 *  and the button. A pure function of the status report (`data.view` plus `active`, `build`, `version`). */
export function chipPopover(data, nowMs) {
  const has = data && typeof data === "object";
  const view = has && data.view ? { ...data.view, active: data.active, build: data.build } : null;
  const read = Boolean(view && data.available);
  const active = Boolean(read && data.active);
  const nowSec = nowMs / 1000;
  const generated = Number(view?.generated_at) || 0;
  const ageS = generated ? Math.max(0, nowSec - generated) : 0;
  const stale = ageS > STALE_READING_S;
  const said = active ? nowSentences(view, nowMs, ageS) : null;
  const all = active ? ringModels(view).main : [];
  const hasList = (view?.fallback?.entries || []).some((entry) => entry.role === "fallback");
  const usable = all.find((model) => model.pool && (model.pool.unused || modelState(model, view, nowMs).tone === "good"));
  const activity = view?.activity && view.activity.kind && view.activity.kind !== "idle" ? view.activity : null;
  const inUse = activity?.identity ? poolOf(view, activity.identity)?.identity : null;
  let sentence;
  if (!read) {
    sentence = `${PRODUCT} has not been read yet.`;
  } else if (!active) {
    sentence = `${PRODUCT} is loaded but not rotating: ${data.reason ?? (data.layer === 3 ? "Agent Zero is running natively" : "unknown")}`;
  } else if (said.tone !== "good") {
    sentence = said.line2;
  } else if (activity) {
    const eta = typeof activity.eta_s === "number" ? untilEpoch((generated || nowSec) + activity.eta_s, nowMs) : null;
    sentence = liveLineText(activity, activity.kind === "waiting" ? eta : null);
  } else if (usable) {
    sentence = `Ready - next reply: ${modelName(usable)}`;
  } else {
    sentence = "Ready - nothing has been asked yet";
  }
  const totals = view?.totals || {};
  const speed = SPEED_LABEL[view?.speed?.mode];
  const keysLine = Number(totals.keys) ? `${totalsPhrase(totals)}${speed ? ` · Speed: ${speed}` : ""}` : "No pooled key has been used yet";
  const models = all.slice(0, CHIP_MODELS_MAX).map((model) => {
    const status = chipRowStatus(model, view, nowMs);
    const isMain = hasList && model.rank === 1;
    const answering = Boolean(inUse) && model.pool?.identity === inUse;
    const segments = ringSegments(model.pool?.key_states || []);
    return {
      identity: model.identity,
      name: modelName(model),
      segments,
      ring: miniRingSvg(segments),
      status: status.text,
      tags: [...(isMain ? ["main"] : []), ...(answering && !isMain ? ["in use"] : [])],
      tone: status.tone,
    };
  });
  const replies = (view?.replies || []).slice(0, CHIP_REPLIES_MAX).map((row) => ({
    at: row.at,
    line: replyLineText(replyFromRow(row)),
    time: clockOf(row.at),
  }));
  const retired = Number(totals.retired) || (view?.pools || []).reduce((sum, pool) => sum + (pool.retired || 0), 0);
  return {
    button: "Open API Rotation",
    emptyReplies: "No reply yet in this session.",
    keysLine,
    models,
    more: Math.max(0, all.length - CHIP_MODELS_MAX),
    replies,
    retired: retired
      ? `${count(retired, "key is", "keys are")} out of rotation - the provider refused ${retired === 1 ? "it as a credential" : "them as credentials"}. ` +
        "Nothing was deleted: put a working key over the retired one in your .env and it returns by itself."
      : "",
    sentence,
    stale,
    title: "API Rotation",
    tone: popoverTone(view, active, stale),
    version: has && data.version ? `KAME ${data.version}` : "KAME",
  };
}
