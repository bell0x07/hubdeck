"use strict";
// hubdeck editor. Talks to the local hubdeck API (src/hubdeck/gui/server.py).

const $ = (s, el = document) => el.querySelector(s);
const h = (tag, attrs = {}, ...kids) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) el.append(kid);
  return el;
};

const NAMES = { keys: "Keys", dial: "Dial", knobs: "Knobs", rollers: "Rollers", faders: "Faders" };
const SINGULAR = { keys: "Key", dial: "Dial", knobs: "Knob", rollers: "Roller", faders: "Fader" };
let S = null, META = null, AUDIO = { apps: [], outputs: [], inputs: [] }, SHORTCUTS = null, page = null, sel = null, cur = null, stamp = Date.now();
const IS_MAC = () => META?.platform === "darwin";

// ---------------------------------------------------------------------------------------
// Friendly catalogue: what people want a control to do, mapped to config actions
// ---------------------------------------------------------------------------------------
const MEDIA = { play_pause: "Play / pause", next: "Next track", prev: "Previous track", stop: "Stop",
  vol_up: "Volume up (media key)", vol_down: "Volume down (media key)", mute: "Mute (media key)" };

const ACTIONS = [
  { group: "Media", items: Object.entries(MEDIA).map(([k, label]) => ({ label, make: () => ({ media: k }), is: a => a.name === "keyboard.media" && a.arg === k })) },
  { group: "Sound", items: [
    { label: "Mute / unmute speakers", make: () => "audio.toggle_mute", is: a => a.name === "audio.toggle_mute" && (a.arg === undefined || a.arg === "speakers") },
    { label: "Mute / unmute microphone", make: () => ({ "audio.toggle_mute": "microphone" }), is: a => a.name === "audio.toggle_mute" && a.arg === "microphone" },
    { label: "Volume up", arg: { kind: "number", label: "by %", def: 5 }, make: v => ({ "audio.volume_up": v ?? 5 }), is: a => a.name === "audio.volume_up", get: a => a.arg },
    { label: "Volume down", arg: { kind: "number", label: "by %", def: 5 }, make: v => ({ "audio.volume_down": v ?? 5 }), is: a => a.name === "audio.volume_down", get: a => a.arg },
    { label: "Set volume to", arg: { kind: "number", label: "%", def: 50 }, make: v => ({ "audio.set_volume": v ?? 50 }), is: a => a.name === "audio.set_volume", get: a => a.arg },
    { label: "Switch speakers / headphones to", arg: { kind: "output" }, make: v => ({ "audio.set_output": v || "" }), is: a => a.name === "audio.set_output", get: a => a.arg },
    { label: "Switch microphone to", arg: { kind: "input" }, make: v => ({ "audio.set_input": v || "" }), is: a => a.name === "audio.set_input", get: a => a.arg },
  ] },
  { group: "Discord", items: [
    ["toggle_mute", "Mute / unmute"], ["toggle_deafen", "Deafen / undeafen"], ["mute", "Mute"], ["unmute", "Unmute"],
    ["deafen", "Deafen"], ["undeafen", "Undeafen"],
  ].map(([a, label]) => ({ label, make: () => `discord.${a}`, is: x => x.name === `discord.${a}` })) },
  { group: "Open & run", items: [
    { label: "Open a website", arg: { kind: "text", placeholder: "https://…" }, make: v => ({ open: v || "" }), is: a => a.name === "launch.open" && /^https?:/i.test(a.arg || ""), get: a => a.arg },
    { label: "Open an app, file or folder", arg: { kind: "text", placeholder: () => IS_MAC() ? "Spotify, or /Applications/…" : "C:\\Program Files\\…\\app.exe" }, make: v => ({ open: v || "" }), is: a => a.name === "launch.open", get: a => a.arg },
    { label: "Run a command", arg: { kind: "text", placeholder: () => IS_MAC() ? "e.g. say hello" : "e.g. notepad.exe" }, make: v => ({ run: v || "" }), is: a => a.name === "launch.run" && typeof a.arg === "string", get: a => a.arg },
    { label: "Run a Shortcut", needs: "shortcuts", arg: { kind: "shortcut" }, make: v => ({ "shortcuts.run": v || "" }), is: a => a.name === "shortcuts.run" && typeof a.arg === "string", get: a => a.arg },
    { label: "Flip a value (vars)", arg: { kind: "text", placeholder: "name, e.g. dnd" }, make: v => ({ "vars.toggle": v || "" }), is: a => a.name === "vars.toggle", get: a => a.arg },
  ] },
  { group: "Keyboard", items: [
    { label: "Keyboard shortcut", arg: { kind: "keys" }, make: v => ({ keys: v || "" }), is: a => a.name === "keyboard.keys", get: a => a.arg },
    { label: "Type text", arg: { kind: "text", placeholder: "text to type" }, make: v => ({ type: v || "" }), is: a => a.name === "keyboard.type", get: a => a.arg },
  ] },
  { group: "Pages", items: [
    { label: "Go to page", arg: { kind: "page" }, make: v => ({ page: v || "" }), is: a => a.name === "page.goto", get: a => a.arg },
    { label: "Go back", make: () => "page.back", is: a => a.name === "page.back" },
    { label: "Go to start page", make: () => "page.home", is: a => a.name === "page.home" },
    { label: "Switch pages by app: on / off", make: () => "page.auto_toggle", is: a => a.name === "page.auto_toggle" },
    { label: "Switch pages by app: on", make: () => "page.auto_on", is: a => a.name === "page.auto_on" },
    { label: "Switch pages by app: off", make: () => "page.auto_off", is: a => a.name === "page.auto_off" },
  ] },
];
const ALL_ACTIONS = ACTIONS.flatMap(g => g.items);

const STATES = {
  "discord.muted": "Discord is muted", "discord.deafened": "Discord is deafened", "discord.holding": "hold-to-mute is held",
  "discord.connected": "Discord is connected", "audio.mic_muted": "the microphone is muted",
  "audio.speaker_muted": "the speakers are muted", "page.auto": "pages switch by app",
};
const VALUES = { "audio.volume": "System volume", "audio.mic_volume": "Microphone volume", "audio.app_volume": "Volume of one app" };

const RED = "#c81e28";
const KEY_PRESETS = [
  { label: "Discord mute", raw: { action: "discord.toggle_mute", state: "discord.muted",
    face: { icon: "microphone", label: "LIVE" }, face_on: { icon: "microphone-off", label: "MUTED", bg: RED, fg: "white" },
    face_unknown: { icon: "plug-connected-x", label: "NO DC", bg: RED, fg: "white" } } },
  { label: "Discord deafen", raw: { action: "discord.toggle_deafen", state: "discord.deafened",
    face: { icon: "headphones", label: "AUDIO" }, face_on: { icon: "headphones-off", label: "DEAF", bg: RED, fg: "white" },
    face_unknown: { icon: "plug-connected-x", label: "NO DC", bg: RED, fg: "white" } } },
  { label: "Discord hold to mute", raw: { on_down: "discord.hold_mute_start", on_up: "discord.hold_mute_end", state: "discord.holding",
    face: { icon: "hand-stop", label: "HOLD" }, face_on: { icon: "microphone-off", label: "HELD", bg: RED, fg: "white" } } },
  { label: "Mute microphone", raw: { action: { "audio.toggle_mute": "microphone" }, state: "audio.mic_muted",
    face: { icon: "microphone", label: "MIC" }, face_on: { icon: "microphone-off", label: "MUTED", bg: RED, fg: "white" } } },
  { label: "Mute speakers", raw: { action: "audio.toggle_mute", state: "audio.speaker_muted",
    face: { icon: "volume", label: "SOUND" }, face_on: { icon: "volume-off", label: "MUTED", bg: RED, fg: "white" } } },
  { label: "Play / pause", raw: { action: { media: "play_pause" }, face: { icon: "player-play" } } },
  { label: "Next track", raw: { action: { media: "next" }, face: { icon: "player-skip-forward" } } },
  { label: "Previous track", raw: { action: { media: "prev" }, face: { icon: "player-skip-back" } } },
  { label: "Clock", raw: { face: { text: "{system.time:%H:%M}", label: "{system.time:%a %d}" } } },
  { label: "CPU usage", raw: { face: { text: "{system.cpu:.0f}%", label: "CPU" } } },
  { label: "Back", raw: { action: "page.back", face: { icon: "arrow-left", label: "Back" } } },
];
const turnPreset = (label, target, arg, icon) => ({ label, raw: {
  turn: arg ? { target, arg, step: 2 } : { target, step: 2 },
  face: { icon, label: "{value}%", gauge: true } } });
const DIAL_PRESETS = [
  { ...turnPreset("System volume", "audio.volume", null, "volume"),
    extra: { action: "audio.toggle_mute", state: "audio.speaker_muted", face_on: { icon: "volume-off", label: "MUTED", bg: RED, fg: "white" } } },
  { ...turnPreset("Microphone volume", "audio.mic_volume", null, "microphone"),
    extra: { action: { "audio.toggle_mute": "microphone" }, state: "audio.mic_muted", face_on: { icon: "microphone-off", label: "MUTED", bg: RED, fg: "white" } } },
  turnPreset("Spotify volume", "audio.app_volume", "Spotify", "brand-spotify"),
  turnPreset("Discord volume", "audio.app_volume", "Discord", "brand-discord"),
];

// ---------------------------------------------------------------------------------------
// API + state
// ---------------------------------------------------------------------------------------
async function api(path, body) {
  const opts = { headers: { "X-Hubdeck-Token": window.TOKEN } };
  if (body !== undefined) {
    opts.method = "POST";
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
  if ((r.headers.get("Content-Type") || "").startsWith("image/")) return r.blob();
  const data = await r.json();
  if (!r.ok && data.error) throw new Error(data.error);
  return data;
}
const imgUrl = (path, q) => `${path}?${new URLSearchParams({ ...q, t: window.TOKEN, v: stamp })}`;
function setStatus(text, kind = "") { const el = $("#status"); el.textContent = text; el.className = "pill " + kind; }

async function load() {
  [S, META] = await Promise.all([api("/api/state"), META ? Promise.resolve(META) : api("/api/meta")]);
  if (!page || !(page in S.pages)) page = S.current_page && S.current_page in S.pages ? S.current_page : S.start_page;
  stamp = Date.now();
  render();
  const err = $("#error");
  err.textContent = S.error ? "Your config file has a problem, so hubdeck keeps using the last working version:\n\n" + S.error : "";
  err.classList.toggle("hidden", !S.error);
  setStatus(S.live ? "● connected to hubdeck" : "editing " + S.path.split(/[\\/]/).pop(), S.error ? "bad" : "ok");
}
async function loadShortcuts() { try { SHORTCUTS = (await api("/api/shortcuts")).names; } catch { SHORTCUTS = []; } }
async function loadAudio() { try { AUDIO = await api("/api/audio"); } catch { /* no audio support on this OS */ } }

function sectionOf(section, p = page) {
  const own = (S.pages[p] || {})[section];
  if (own || section === "keys") return { items: own || {}, from: p };
  return { items: (S.pages[S.start_page] || {})[section] || {}, from: S.start_page };
}

// ---------------------------------------------------------------------------------------
// Device drawing
// ---------------------------------------------------------------------------------------
function render() {
  $("#pages").replaceChildren(...Object.keys(S.pages).map(p => {
    const flags = S.page_flags[p] || {};
    return h("button", {
      class: p === page ? "active" : "", onclick: () => { page = p; closeEditor(); render(); },
      title: p === S.start_page ? "Start page" : "",
    }, p === S.start_page ? "★ " : "", p,
    flags.match?.length ? h("span", { class: "flag", title: "shown when " + flags.match.map(windowText).join(", ") + " has focus" }, "⇄") : null,
    flags.sticky ? h("span", { class: "flag", title: "sticky: stays while it's shown, whatever has focus" }, "📌") : null,
    S.current_page === p ? h("span", { class: "live", title: "on the device right now" }) : null);
  }));
  $("#page-title").textContent = page;
  drawFocus();
  $("#page-start").classList.toggle("hidden", page === S.start_page);
  $("#page-delete").classList.toggle("hidden", page === S.start_page);
  drawBase();
}

function cellPx() {
  const area = $("#device-area").clientWidth - 40;
  return Math.max(80, Math.min(140, Math.floor(area / S.layout.cols)));
}

function drawBase() {
  const L = S.layout, c = cellPx(), gap = 6;
  const grid = h("div", { class: "grid", style: `width:${L.cols * c}px;height:${L.rows * c}px` });
  for (let y = 0; y < L.rows; y++) for (let x = 0; x < L.cols; x++) {
    const cell = h("div", { class: "cell", style: `left:${x * c + 2}px;top:${y * c + 2}px;width:${c - 4}px;height:${c - 4}px` });
    cell.addEventListener("dragover", e => { e.preventDefault(); cell.classList.add("drop"); });
    cell.addEventListener("dragleave", () => cell.classList.remove("drop"));
    cell.addEventListener("drop", e => { e.preventDefault(); moveModule(e.dataTransfer.getData("text/kind"), x, y); });
    grid.append(cell);
  }
  const unplaced = [];
  for (const [kind, m] of Object.entries(L.modules)) {
    const box = moduleBox(kind, m, c, gap);
    if (m.x === null || m.x === undefined) { box.style.position = "relative"; unplaced.push(box); }
    else { box.style.left = `${m.x * c + gap / 2}px`; box.style.top = `${m.y * c + gap / 2}px`; grid.append(box); }
  }
  $("#base").replaceChildren(grid);
  $("#unplaced").replaceChildren(...(unplaced.length ? [h("div", { class: "muted" }, "Not on the base:"), ...unplaced] : []));
  if (!Object.keys(L.modules).length)
    $("#base").append(h("p", { class: "muted", style: "padding:20px" }, "No modules found. Plug them in and close MasterHUB."));
}

function moduleBox(kind, m, c, gap) {
  const w = m.w * c - gap, hgt = m.h * c - gap;
  const badge = !m.connected ? "not plugged in" : "";
  const bar = h("div", { class: "bar", draggable: "true", title: "Drag to move it to where it sits on your base" },
    h("span", { class: "grip" }, "⠿"), h("span", { class: "name" }, NAMES[kind]),
    badge ? h("span", { class: "badge" }, badge) : null, h("span", { class: "spacer" }),
    h("button", { title: "Is this module mounted turned? Rotate it 90° here to match", onclick: () => rotateModule(kind, m.rotate) }, "↻"));
  bar.addEventListener("dragstart", e => e.dataTransfer.setData("text/kind", kind));
  const body = h("div", { class: "body" });
  const box = h("div", { class: "module" + (m.connected ? "" : " off"), style: `width:${w}px;height:${hgt}px` }, bar, body);
  const { items, from } = sectionOf(kind);
  for (const ctl of m.controls) body.append(controlEl(kind, m, ctl, items, from, w, hgt - 26, c));
  return box;
}

function controlEl(kind, m, ctl, items, from, bw, bh, c) {
  const num = String(ctl.number), summary = items[num];
  const configured = summary !== undefined;
  const side = m.rotate % 180 !== 0;
  let w, hh, inner;
  if (kind === "keys") {
    w = hh = Math.min(bw / (side ? 3 : 5), bh / (side ? 5 : 3)) * 0.84;
    inner = configured ? h("img", { class: "face", src: imgUrl("/api/face.png", { page: from, section: kind, num }) })
      : h("div", { class: "face add" }, "+");
  } else if (kind === "dial") {
    w = hh = Math.min(bw, bh) * 0.82;
    inner = configured ? h("img", { class: "face", src: imgUrl("/api/face.png", { page: from, section: "dial", num: 1 }) })
      : h("div", { class: "face add" }, "+");
  } else if (kind === "knobs") {
    w = hh = c * 0.6; inner = h("div", { class: "knob" });
  } else if (kind === "rollers") {
    [w, hh] = side ? [c * 1.3, c * 0.42] : [c * 0.42, c * 0.78]; inner = h("div", { class: "roller" });
  } else {
    const len = c * 1.45, thick = c * 0.16;
    [w, hh] = side ? [len, thick * 2] : [thick * 2, len];
    inner = h("div", { class: "fader" },
      h("div", { class: "track", style: side ? `width:${len}px;height:6px` : `width:6px;height:${len}px` }),
      h("div", { class: "thumb", style: side ? `width:10px;height:${thick * 1.6}px` : `width:${thick * 1.6}px;height:10px` }));
  }
  const isSel = sel && sel.section === kind && String(sel.num) === num;
  const el = h("div", {
    class: `ctl ${kind}${configured ? "" : " empty"}${isSel ? " sel" : ""}`,
    style: `left:${ctl.x * bw}px;top:${ctl.y * bh}px;width:${w}px;height:${hh}px`,
    title: configured ? `${SINGULAR[kind]} ${num}: ${friendlySummary(summary)}` : `${SINGULAR[kind]} ${num}: click to set up`,
    onclick: () => openEditor(kind, kind === "dial" ? 1 : Number(num)),
  }, inner, kind !== "dial" ? h("span", { class: "num" }, num) : null);
  if ((kind === "knobs" || kind === "rollers" || kind === "faders") && configured)
    el.append(h("span", { class: "tip" }, friendlySummary(summary)));
  return el;
}

function friendlySummary(s) {
  if (!s) return "";
  const app = s.match(/^audio\.app_volume\s+(.+)$/);
  if (app) { const n = app[1].replace(/\.exe$/i, ""); return `${n[0].toUpperCase()}${n.slice(1)} volume`; }
  if (s.startsWith("audio.mic_volume")) return "Mic volume";
  if (s.startsWith("audio.volume")) return "System volume";
  if (s === "turn: actions") return "per-click actions";
  return s;
}

async function moduleSettings(change) {
  const r = await api("/api/global?name=modules");
  const mods = r.raw || {};
  change(mods);
  const res = await api("/api/global", { name: "modules", raw: mods });
  if (!res.ok) { alert("Couldn't save:\n\n" + res.error); return; }
  setStatus("saved", "ok");
  await load();
}
const rotateModule = (kind, rot) => moduleSettings(m => { m[kind] = { ...(m[kind] || {}), rotate: (rot + 90) % 360 }; });

function moveModule(kind, x, y) {
  // dropping onto another module swaps the two; everything keeps an explicit position afterwards
  const mods = S.layout.modules, me = mods[kind];
  if (!kind || !me) return;
  const L = S.layout;
  x = Math.max(0, Math.min(x, L.cols - me.w));
  y = Math.max(0, Math.min(y, L.rows - me.h));
  const overlaps = (a, ax, ay, b) => b.x !== null && b.x !== undefined &&
    ax < b.x + b.w && b.x < ax + a.w && ay < b.y + b.h && b.y < ay + a.h;
  return moduleSettings(m => {
    for (const [k, o] of Object.entries(mods))
      if (k !== kind && o.x !== null && o.x !== undefined) m[k] = { ...(m[k] || {}), position: [o.x, o.y] };
    for (const [k, o] of Object.entries(mods)) {
      if (k === kind || !overlaps(me, x, y, o)) continue;
      if (me.x !== null && me.x !== undefined)
        m[k].position = [Math.max(0, Math.min(me.x, L.cols - o.w)), Math.max(0, Math.min(me.y, L.rows - o.h))];
      else delete m[k].position;
    }
    m[kind] = { ...(m[kind] || {}), position: [x, y] };
  });
}

// ---------------------------------------------------------------------------------------
// Small form helpers (bound to paths inside cur.raw)
// ---------------------------------------------------------------------------------------
const getPath = (o, path) => path.reduce((x, k) => (x == null ? undefined : x[k]), o);
function setPath(o, path, value) {
  let x = o;
  for (const k of path.slice(0, -1)) x = x[k] ??= {};
  const last = path[path.length - 1];
  if (value === undefined || value === "" || value === null) delete x[last]; else x[last] = value;
  for (let i = path.length - 1; i > 0; i--) {
    const parent = i > 1 ? getPath(o, path.slice(0, i - 1)) : o, key = path[i - 1];
    if (parent && parent[key] && typeof parent[key] === "object" && !Object.keys(parent[key]).length) delete parent[key]; else break;
  }
}
const field = (label, ...inputs) => h("div", { class: "row" }, h("label", {}, label), ...inputs);
function textIn(path, placeholder = "") {
  return h("input", { type: "text", value: getPath(cur.raw, path) ?? "", placeholder,
    oninput: e => { setPath(cur.raw, path, e.target.value); changed(); } });
}
function checkIn(path, label) {
  return h("label", { class: "check" }, h("input", { type: "checkbox", checked: !!getPath(cur.raw, path),
    onchange: e => { setPath(cur.raw, path, e.target.checked || undefined); changed(); } }), label);
}
function colorIn(path, fallback) {
  const v = getPath(cur.raw, path) ?? "";
  const pick = h("input", { type: "color", value: /^#[0-9a-f]{6}$/i.test(v) ? v : fallback,
    oninput: e => { setPath(cur.raw, path, e.target.value); clear.classList.remove("hidden"); changed(); } });
  const clear = h("button", { class: "small" + (v ? "" : " hidden"), type: "button", title: "Back to the default colour",
    onclick: () => { setPath(cur.raw, path, undefined); pick.value = fallback; clear.classList.add("hidden"); changed(); } }, "default");
  return h("span", { class: "color" }, pick, clear);
}
const asList = v => v === undefined || v === null ? [] : Array.isArray(v) ? v : [v];
function parseAction(a) {
  if (typeof a === "string") return { name: a, arg: undefined };
  const [k, v] = Object.entries(a)[0];
  return { name: META.aliases[k] || k, arg: v };
}

// keyboard shortcut recorder: press the keys, we write "ctrl+shift+m"
const KEYMAP = { " ": "space", Escape: "esc", Enter: "enter", Tab: "tab", Backspace: "backspace", Delete: "delete",
  Insert: "insert", Home: "home", End: "end", PageUp: "pgup", PageDown: "pgdn", ArrowUp: "up", ArrowDown: "down",
  ArrowLeft: "left", ArrowRight: "right", PrintScreen: "printscreen", Pause: "pause", CapsLock: "capslock",
  "+": "plus", "-": "minus", "=": "equals", ",": "comma", ".": "period", "/": "slash", "\\": "backslash",
  ";": "semicolon", "'": "quote", "`": "backtick", "[": "lbracket", "]": "rbracket" };
function keyRecorder(value, onChange) {
  const input = h("input", { type: "text", value: value || "", placeholder: "click here, then press the keys", readonly: true, class: "keyrec" });
  input.addEventListener("keydown", e => {
    e.preventDefault();
    if (["Control", "Shift", "Alt", "Meta"].includes(e.key)) return;
    const parts = [];
    if (e.ctrlKey) parts.push("ctrl");
    if (e.shiftKey) parts.push("shift");
    if (e.altKey) parts.push("alt");
    if (e.metaKey) parts.push(IS_MAC() ? "cmd" : "win");
    let k = KEYMAP[e.key] ?? (/^F\d{1,2}$/.test(e.key) ? e.key.toLowerCase() : /^[a-z0-9]$/i.test(e.key) ? e.key.toLowerCase() : null);
    // Shift or Option changed the character (!, å): name the key that was pressed instead
    if (!k && /^(Key[A-Z]|Digit\d)$/.test(e.code)) k = e.code.replace(/^Key|^Digit/, "").toLowerCase();
    if (!k && e.key.length === 1) k = e.key.toLowerCase();
    if (!k) return;
    parts.push(k);
    input.value = parts.join("+");
    onChange(input.value);
  });
  return input;
}

// ---------------------------------------------------------------------------------------
// Friendly action picker
// ---------------------------------------------------------------------------------------
// items for a plugin this OS doesn't have (Shortcuts off macOS) are never offered; such actions show as Custom
const offered = it => !it.needs || !!META?.plugins[it.needs];
function findItem(a) { return ALL_ACTIONS.find(it => offered(it) && it.is(a)); }

function argInput(item, value, onChange) {
  const spec = item.arg;
  if (!spec) return null;
  if (spec.kind === "number") return h("span", { class: "argwrap" }, h("input", { type: "number", value: value ?? spec.def, style: "width:80px",
    oninput: e => onChange(e.target.value === "" ? undefined : Number(e.target.value)) }), h("span", { class: "muted" }, spec.label));
  if (spec.kind === "page") return h("select", { onchange: e => onChange(e.target.value) },
    h("option", { value: "" }, "choose a page"), ...Object.keys(S.pages).map(p => h("option", { value: p, selected: p === value }, p)));
  if (spec.kind === "output" || spec.kind === "input") {
    const opts = [...new Set([...(AUDIO[spec.kind + "s"] || []), ...(value ? [value] : [])])];
    return h("select", { onchange: e => onChange(e.target.value) },
      h("option", { value: "" }, opts.length ? "choose a device" : "no devices found"),
      ...opts.map(o => h("option", { value: o, selected: o === value }, o)));
  }
  if (spec.kind === "keys") return keyRecorder(value, onChange);
  if (spec.kind === "shortcut") {
    const list = h("datalist", { id: "shortcut-names" });
    const fill = () => list.replaceChildren(...(SHORTCUTS || []).map(n => h("option", { value: n })));
    if (SHORTCUTS === null) loadShortcuts().then(fill); else fill();
    return h("span", { class: "argwrap" }, h("input", { type: "text", value: value ?? "", list: "shortcut-names",
      placeholder: "Shortcut name", oninput: e => onChange(e.target.value) }), list);
  }
  return h("input", { type: "text", value: value ?? "", placeholder: (typeof spec.placeholder === "function" ? spec.placeholder() : spec.placeholder) || "", oninput: e => onChange(e.target.value) });
}

function actionSelect(selected, onPick) {
  return h("select", { class: "actsel", onchange: e => onPick(e.target.value) },
    h("option", { value: "" }, "— nothing —"),
    ...ACTIONS.map(g => h("optgroup", { label: g.group },
      ...g.items.filter(offered).map(it => h("option", { value: ALL_ACTIONS.indexOf(it), selected: it === selected }, it.label)))),
    selected === "custom" ? h("option", { value: "custom", selected: true }, "Custom (see Advanced)") : null);
}

// edits one gesture field (action / on_long_press / turn.cw …) as a list of friendly actions
function actionEditor(field, target = () => cur.raw, onChange = changed) {
  const box = h("div", { class: "actions" });
  const raw = () => asList(target()[field]);
  const write = list => {
    const obj = target();
    delete obj[field];
    if (list.length) obj[field] = list.length === 1 ? list[0] : list;
    onChange();
  };
  const draw = () => {
    const list = raw();
    const rows = list.map((a, i) => {
      const parsed = parseAction(a), item = findItem(parsed);
      const rowEl = h("div", { class: "act" });
      const update = (it, v) => { const l = raw(); l[i] = it.make(v); write(l); };
      rowEl.append(actionSelect(item || "custom", v => {
        if (v === "") { const l = raw(); l.splice(i, 1); write(l); draw(); return; }
        if (v === "custom") return;
        const it = ALL_ACTIONS[Number(v)]; update(it, it.arg?.def); draw();
      }));
      if (item) { const ai = argInput(item, item.get ? item.get(parsed) : undefined, v => update(item, v)); if (ai) rowEl.append(ai); }
      else rowEl.append(h("code", { class: "muted" }, JSON.stringify(a)));
      return rowEl;
    });
    const add = list.length ? h("button", { class: "small link", type: "button", onclick: () => {
      const l = raw(); l.push(ALL_ACTIONS[0].make()); write(l); draw(); } }, "+ and then also…")
      : h("div", { class: "act" }, actionSelect(null, v => { if (v === "") return; const it = ALL_ACTIONS[Number(v)];
          write([it.make(it.arg?.def)]); draw(); }));
    box.replaceChildren(...rows, add);
  };
  draw();
  return box;
}

// ---------------------------------------------------------------------------------------
// Editor panel
// ---------------------------------------------------------------------------------------
async function openEditor(section, num) {
  sel = { section, num };
  const own = (S.pages[page] || {})[section];
  const inherited = section !== "keys" && !own && sectionOf(section).from !== page;
  const src = inherited ? S.start_page : page;
  const item = await api(`/api/item?${new URLSearchParams({ page: src, section, num })}`);
  cur = { section, num, raw: structuredClone(item.raw || {}), yaml: item.yaml, inherited, tab: "form", isNew: !item.raw };
  if (!AUDIO.apps.length && section !== "keys") await loadAudio();
  $("#editor").classList.remove("hidden");
  $("#ed-title").textContent = section === "dial" ? "Dial" : `${SINGULAR[section]} ${num}`;
  $("#ed-sub").textContent = inherited ? `Shared from page “${S.start_page}”` : `Page “${page}”`;
  $("#ed-delete").classList.toggle("hidden", !item.raw || inherited);
  showTab("form");
  render();
}
function closeEditor() { sel = cur = null; $("#editor").classList.add("hidden"); }

function showTab(tab) {
  cur.tab = tab;
  document.querySelectorAll("#editor .tabs button").forEach(b => b.classList.toggle("active", b.dataset.tab === tab));
  $("#ed-form").classList.toggle("hidden", tab !== "form");
  $("#ed-yaml-wrap").classList.toggle("hidden", tab !== "yaml");
  $("#ed-error").classList.add("hidden");
  if (tab === "yaml") {
    if (cur.yamlDirty !== undefined) $("#ed-yaml").value = cur.yamlDirty;
    else if (cur.formDirty) api("/api/to-yaml", { raw: cur.raw }).then(r => { $("#ed-yaml").value = r.yaml; });
    else $("#ed-yaml").value = cur.yaml ?? "";
  } else buildForm();
}

const section = (title, ...kids) => h("section", { class: "fs" }, h("h3", {}, title), ...kids);
const more = (title, ...kids) => h("details", { class: "more" }, h("summary", {}, title), ...kids);

function presetBar(presets) {
  return h("div", { class: "presets" }, ...presets.map(p => h("button", { type: "button", class: "chip", onclick: () => {
    cur.raw = structuredClone({ ...p.raw, ...(p.extra || {}) });
    if (cur.section === "knobs" || cur.section === "rollers") delete cur.raw.face, delete cur.raw.face_on;
    changed(); buildForm(); } }, p.label)));
}

function iconPicker(path) {
  const results = h("div", { class: "icons" });
  const current = getPath(cur.raw, path);
  const preview = h("img", { class: "icon-now" + (current ? "" : " hidden"), src: current ? imgUrl("/api/icon.png", { name: current }) : "" });
  const input = h("input", { type: "text", value: current ?? "", placeholder: "search: mic, volume, music, play…" });
  let timer;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const q = input.value.trim();
      if (q.length < 2) { results.replaceChildren(); return; }
      const r = await api(`/api/icons?${new URLSearchParams({ q })}`);
      results.replaceChildren(...r.icons.slice(0, 60).map(name => h("img", {
        src: imgUrl("/api/icon.png", { name }), title: name, loading: "lazy",
        onclick: () => { input.value = name; setPath(cur.raw, path, name); preview.src = imgUrl("/api/icon.png", { name });
          preview.classList.remove("hidden"); results.replaceChildren(); changed(); },
      })));
    }, 250);
  });
  const none = h("button", { class: "small", type: "button", onclick: () => { input.value = ""; setPath(cur.raw, path, undefined);
    preview.classList.add("hidden"); changed(); } }, "no icon");
  return [field("Icon", preview, input, none), results];
}

function lookFields(base, isDial, isOverlay) {
  const fields = [
    ...iconPicker([...base, "icon"]),
    field("Label", textIn([...base, "label"], isDial ? "e.g. {value}% for the current value" : "short text under the icon")),
    field("Background", colorIn([...base, "bg"], "#19191e")),
    field("Icon colour", colorIn([...base, "fg"], META.accent || "#3cc86e")),
  ];
  if (isOverlay) return fields;
  if (isDial) fields.push(field("", checkIn([...base, "gauge"], "show a ring with the current value")));
  fields.push(more("More look options",
    field("Big text", textIn([...base, "text"], "instead of an icon, e.g. {system.time:%H:%M}")),
    field("Text colour", colorIn([...base, "label_color"], "#ffffff")),
    field("Picture / GIF", textIn([...base, "image"], "file next to your config, or a https:// link")),
    field("", checkIn([...base, "filled"], "filled icon style")),
    h("p", { class: "muted" }, "Live values you can use in labels: {system.time:%H:%M}, {system.cpu:.0f}%, {audio.volume}%, and on the dial or knobs {value}.")));
  return fields;
}

// A state set in YAML that the pickers don't list, e.g. {audio.output: DAC5}: kept as one option.
function ownState(state) {
  if (!state || typeof state !== "object") return state ? { key: state, label: STATES[state] || state } : null;
  const [[name, want]] = Object.entries(state);
  return { key: JSON.stringify(state), label: `${STATES[name] || name}: ${want}` };
}

function stateOptions(own) {
  const keys = [...new Set([...Object.keys(STATES), ...(own ? [own.key] : [])])];
  return keys.map(k => h("option", { value: k, selected: k === own?.key }, k === own?.key ? own.label : STATES[k]));
}

function stateFields() {
  const own = ownState(cur.raw.state);
  const pick = h("select", { onchange: e => { setPath(cur.raw, ["state"], e.target.value === own?.key ? cur.raw.state : e.target.value || undefined);
    if (!e.target.value) { delete cur.raw.face_on; delete cur.raw.face_unknown; }
    else if (!cur.raw.face_on) cur.raw.face_on = { bg: RED, fg: "white" };
    changed(); buildForm(); } },
    h("option", { value: "" }, "never"), ...stateOptions(own));
  const out = [field("Change look when", pick)];
  if (cur.raw.state) out.push(h("div", { class: "overlay" }, h("div", { class: "muted" }, "…then look like this (empty = unchanged):"),
    ...lookFields(["face_on"], false, true)));
  return out;
}

function turnFields() {
  if (typeof cur.raw.turn === "string") cur.raw.turn = { target: cur.raw.turn };
  const t = cur.raw.turn || {};
  const mode = t.cw || t.ccw ? "actions" : t.target ? "value" : "";
  const verb = cur.section === "rollers" ? "Rolling" : "Turning";
  const modeSel = h("select", { onchange: e => {
    const v = e.target.value;
    if (v === "") delete cur.raw.turn, delete cur.raw.on_turn;     // on_turn needs a turn
    else if (v === "actions") cur.raw.turn = { cw: { media: "next" }, ccw: { media: "prev" } };
    else cur.raw.turn = { target: v, step: 2, ...(v === "audio.app_volume" ? { arg: AUDIO.apps[0] || "Spotify" } : {}) };
    changed(); buildForm(); } },
    h("option", { value: "" }, "does nothing"),
    ...Object.entries(VALUES).map(([k, label]) => h("option", { value: k, selected: t.target === k }, `changes ${label.toLowerCase()}`)),
    h("option", { value: "actions", selected: mode === "actions" }, "does something per click…"));
  const out = [field(verb, modeSel)];
  if (t.target === "audio.app_volume") out.push(field("App", appPicker(["turn", "arg"])));
  if (mode === "value") out.push(field("Step", h("input", { type: "number", value: t.step ?? 2, min: 1, max: 25, style: "width:80px",
    oninput: e => { setPath(cur.raw, ["turn", "step"], e.target.value === "" ? undefined : Number(e.target.value)); changed(); } }),
    h("span", { class: "muted" }, "% per click")),
    field("Expo", h("input", { type: "number", value: t.expo ?? 0, min: 0, max: 1, step: 0.1, style: "width:80px",
      oninput: e => { setPath(cur.raw, ["turn", "expo"], Number(e.target.value) || undefined); changed(); } }),
      h("span", { class: "muted" }, "0 = even; up to 1 = finer near 0, coarser near 100")),
    field("", checkIn(["turn", "invert"], "reverse the direction")));
  if (mode === "actions") {
    const turnObj = () => (cur.raw.turn ??= {});
    out.push(field(cur.section === "rollers" ? "Up / right" : "Clockwise", actionEditor("cw", turnObj)),
      field(cur.section === "rollers" ? "Down / left" : "Counter-clockwise", actionEditor("ccw", turnObj)));
  }
  if (mode) out.push(field("After each turn", actionEditor("on_turn")),
    h("p", { class: "muted" }, "For example Python that shows the value on the Dial: deck.dial.set_face(…, value_of=deck.control, seconds=1.5)."));
  return out;
}

function appPicker(path) {
  const v = getPath(cur.raw, path) ?? "";
  const same = (a, b) => a.toLowerCase().replace(/\.exe$/, "") === b.toLowerCase().replace(/\.exe$/, "");
  const apps = [...(AUDIO.apps || []).filter(a => !v || !same(a, v)), ...(v ? [v] : [])]
    .filter(a => a.toLowerCase() !== "focused")
    .sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" }));
  const sel = h("select", { onchange: e => {
    if (e.target.value === "__other") { other.classList.remove("hidden"); other.focus(); return; }
    other.classList.add("hidden"); setPath(cur.raw, path, e.target.value); changed(); } },
    h("option", { value: "focused", selected: v.toLowerCase() === "focused" }, "the focused app"),
    ...apps.map(a => h("option", { value: a, selected: a === v }, a.replace(/\.exe$/i, ""))),
    h("option", { value: "__other" }, "another app…"));
  const other = h("input", { type: "text", class: "hidden", placeholder: IS_MAC() ? "app name, e.g. Music" : "program name, e.g. chrome.exe",
    oninput: e => { setPath(cur.raw, path, e.target.value); changed(); } });
  return h("span", { class: "argwrap" }, sel, other,
    h("button", { class: "small", type: "button", title: "Refresh the list of apps playing audio",
      onclick: async () => { await loadAudio(); buildForm(); } }, "↻"));
}

function faderFields() {
  const t = cur.raw.target;
  const pick = h("select", { onchange: e => {
    if (!e.target.value) { delete cur.raw.target; delete cur.raw.arg; }
    else { cur.raw.target = e.target.value; if (e.target.value === "audio.app_volume") cur.raw.arg ??= AUDIO.apps[0] || "Spotify"; else delete cur.raw.arg; }
    changed(); buildForm(); } },
    h("option", { value: "" }, "does nothing"),
    ...Object.entries(VALUES).map(([k, label]) => h("option", { value: k, selected: t === k }, `controls ${label.toLowerCase()}`)));
  const out = [field("This fader", pick)];
  if (t === "audio.app_volume") out.push(field("App", appPicker(["arg"])));
  out.push(field("", checkIn(["invert"], "reverse the direction")),
    h("p", { class: "muted" }, "It only takes over once you move it to the current level, so the volume never jumps."));
  return out;
}

function buildForm() {
  const f = $("#ed-form"), s = cur.section;
  const parts = [];
  if (cur.inherited) parts.push(h("p", { class: "note" },
    `This is set on page “${S.start_page}” and shared by every page that doesn't have its own. Saving here gives page “${page}” its own version.`));
  if (s === "keys") parts.push(section("Quick setup", presetBar(KEY_PRESETS)));
  if (s === "dial" || s === "knobs") parts.push(section("Quick setup", presetBar(DIAL_PRESETS)));

  if (s === "faders") parts.push(section("What it does", ...faderFields()));
  else {
    if (s !== "keys") parts.push(section(s === "rollers" ? "When you roll it" : "When you turn it", ...turnFields()));
    if (s !== "rollers") parts.push(section(s === "keys" ? "When you press it" : "When you press it", actionEditor(cur.raw.on_press !== undefined ? "on_press" : "action")));
    if (s === "keys" || s === "dial") parts.push(section("How it looks", ...lookFields(["face"], s === "dial")), section("Changing look", ...stateFields()));
    if (s !== "rollers") parts.push(more("More: long press, double press, hold",
      field("Long press", actionEditor("on_long_press")),
      field("Double press", actionEditor("on_double_press")),
      field("When pressed down", actionEditor("on_down")),
      field("When released", actionEditor("on_up")),
      h("p", { class: "muted" }, "Hold-style controls (like hold to mute) use “pressed down” and “released”.")));
  }
  f.replaceChildren(...parts);
  preview();
}

let previewTimer;
function changed() { cur.yamlDirty = undefined; cur.formDirty = true; setStatus("unsaved changes", "warn"); preview(); }
function preview() {
  const img = $("#ed-preview"), s = cur.section;
  const show = s === "keys" || s === "dial";
  $("#ed-preview-wrap").classList.toggle("hidden", !show);
  img.classList.toggle("round", s === "dial");
  if (!show) return;
  clearTimeout(previewTimer);
  previewTimer = setTimeout(async () => {
    try {
      const blob = await api("/api/preview.png", { page, section: s, num: cur.num, raw: cur.raw });
      if (blob instanceof Blob) { img.src = URL.createObjectURL(blob); showEdError(""); }
    } catch (e) { showEdError(e.message); }
  }, 250);
}
function showEdError(msg) { const el = $("#ed-error"); el.textContent = msg; el.classList.toggle("hidden", !msg); }

async function saveItem() {
  const body = { page, section: cur.section, num: cur.num };
  if (cur.tab === "yaml") body.yaml = $("#ed-yaml").value; else body.raw = cur.raw;
  const r = await api("/api/item", body);
  if (!r.ok) { showEdError("Not saved, something isn't right yet:\n\n" + r.error); setStatus("not saved", "bad"); return; }
  setStatus("saved ✓", "ok");
  const keep = { ...sel };
  await load();
  await openEditor(keep.section, keep.num);
}
async function removeItem() {
  if (!confirm("Clear this control on this page?")) return;
  const r = await api("/api/item", { page, section: cur.section, num: cur.num, raw: null });
  if (!r.ok) { showEdError(r.error); return; }
  closeEditor(); await load();
}

// ---------------------------------------------------------------------------------------
// Pages
// ---------------------------------------------------------------------------------------
async function pageAction(action, extra = {}) {
  const r = await api("/api/page", { action, name: page, ...extra });
  if (!r.ok) { alert(r.error); return; }
  if (action === "rename") page = extra.new;
  if (action === "delete") page = null;
  await load();
}
$("#add-page").onclick = async () => {
  const name = prompt("Name of the new page:");
  if (!name) return;
  const r = await api("/api/page", { action: "add", name });
  if (!r.ok) { alert(r.error); return; }
  page = name.trim(); await load();
};
$("#page-rename").onclick = () => { const n = prompt("New name for this page:", page); if (n && n !== page) pageAction("rename", { new: n.trim() }); };
$("#page-delete").onclick = () => confirm(`Delete page “${page}” and everything on it?`) && pageAction("delete");
$("#page-start").onclick = () => pageAction("start");

// ---------------------------------------------------------------------------------------
// Dialogs: lighting, device & modules, whole file
// ---------------------------------------------------------------------------------------
const dlg = $("#dlg");
let dlgSave = null;
function openDialog(title, tabs, onSave) {
  $("#dlg-title").textContent = title;
  $("#dlg-error").classList.add("hidden");
  $("#dlg-tabs").replaceChildren(...tabs.map((t, i) => h("button", { type: "button", class: i ? "" : "active",
    onclick: e => { document.querySelectorAll("#dlg-tabs button").forEach(b => b.classList.remove("active"));
      e.target.classList.add("active"); t.show(); } }, t.name)));
  $("#dlg-tabs").classList.toggle("hidden", tabs.length < 2);
  dlgSave = onSave;
  tabs[0].show();
  dlg.showModal();
}
function dlgError(msg) { const el = $("#dlg-error"); el.textContent = msg; el.classList.toggle("hidden", !msg); }
$("#dlg-save").onclick = async e => { e.preventDefault(); try { if (await dlgSave()) dlg.close(); } catch (err) { dlgError(err.message); } };
// Enter in a text field saves: the form's first button is ✕, which Enter would press, closing it unsaved
dlg.addEventListener("keydown", e => {
  if (e.key === "Enter" && e.target.matches("input[type=text]")) { e.preventDefault(); $("#dlg-save").click(); }
});
const showBody = yaml => { $("#dlg-body").classList.toggle("hidden", yaml); $("#dlg-yaml").classList.toggle("hidden", !yaml); };
async function saveGlobalRaw(name, raw) {
  const r = await api("/api/global", { name, raw });
  if (!r.ok) { dlgError("Not saved:\n\n" + r.error); return false; }
  return true;
}

$("#btn-yaml").onclick = async () => {
  const r = await api("/api/yaml");
  $("#dlg-yaml").value = r.yaml;
  openDialog("Advanced: the whole config file", [{ name: "config.yaml", show: () => showBody(true) }], async () => {
    const res = await api("/api/yaml", { yaml: $("#dlg-yaml").value });
    if (!res.ok) { dlgError("Not saved, the file would be invalid:\n\n" + res.error); return false; }
    setStatus("saved ✓", "ok"); await load(); return true;
  });
};

$("#btn-settings").onclick = async () => {
  const [dev, mods] = await Promise.all([api("/api/global?name=device"), api("/api/global?name=modules")]);
  const d = dev.raw || {}, m = mods.raw || {};
  const radio = (obj, key, options, def) => h("span", { class: "seg" }, ...options.map(([v, label]) =>
    h("label", {}, h("input", { type: "radio", name: key, checked: String(obj[key] ?? def) === String(v),
      onchange: () => { obj[key] = v; } }), label)));
  const body = h("div", {},
    section("Your base",
      field("Base", radio(d, "orientation", [["horizontal", "lying wide (6×4)"], ["vertical", "standing tall (4×6)"]], "horizontal")),
      field("Other apps", radio(d, "ownership", [["shared", "may use controls hubdeck doesn't"], ["exclusive", "hubdeck uses everything"]], "shared"))),
    section("Modules",
      h("p", { class: "muted" }, "If a module is mounted turned, say how far (clockwise) so numbers and pictures match."),
      ...["keys", "dial", "knobs", "rollers", "faders"].map(k => {
        m[k] ??= {};
        const rot = h("select", { onchange: e => { m[k].rotate = Number(e.target.value); } },
          ...[[0, "normal"], [90, "turned right (90°)"], [180, "upside down"], [270, "turned left (270°)"]]
            .map(([r, label]) => h("option", { value: r, selected: (m[k].rotate ?? 0) === r }, label)));
        const row = field(NAMES[k], rot);
        if (k === "keys" || k === "dial") row.append(h("span", { class: "muted" }, "brightness"),
          h("input", { type: "range", min: 0, max: 100, value: m[k].brightness ?? 100,
            oninput: e => { m[k].brightness = Number(e.target.value); } }));
        return row;
      })));
  $("#dlg-body").replaceChildren(body);
  openDialog("Device & modules", [{ name: "Settings", show: () => showBody(false) }], async () => {
    for (const k of Object.keys(m)) {
      if (m[k].rotate === 0) delete m[k].rotate;
      if (!Object.keys(m[k]).length) delete m[k];
    }
    if (!(await saveGlobalRaw("device", d)) || !(await saveGlobalRaw("modules", m))) return false;
    setStatus("saved ✓", "ok"); await load(); return true;
  });
};

// ---------------------------------------------------------------------------------------
// Switching pages by app: a page's windows (match, sticky) and auto_switch
// ---------------------------------------------------------------------------------------
const reEscape = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const windowText = w => typeof w === "string" ? w : [w.app, w.title && `“${w.title}”`].filter(Boolean).join(" ");

function drawFocus() {
  const el = $("#focus-now"), f = S.focus;
  el.classList.toggle("hidden", !S.live || !f);
  if (!S.live || !f) return;
  el.textContent = `Focused now: ${f.app}${f.title ? " · " + f.title : ""} → ` +
    (f.page ? `page “${f.page}”` : "no page") + (S.auto_switch ? "" : " (switching by app is off)");
}

function focusNote() {
  if (!META.follows_focus) return h("p", { class: "note" }, "This computer can't tell hubdeck which app has focus: these settings only work on Windows and macOS.");
  return S.live ? null : h("p", { class: "muted" }, "Open the editor from the tray icon to see the focused window and pick from recent ones.");
}

function recentPicker(onPick) {
  const recent = S.recent_windows || [];
  if (!S.live) return null;
  return h("select", { onchange: e => {
    const w = recent[Number(e.target.value)];
    e.target.value = "";
    if (w) onPick({ app: w.app, title: w.title ? reEscape(w.title) : undefined });
  } }, h("option", { value: "" }, recent.length ? "pick a recent window…" : "no windows seen yet"),
    ...recent.map((w, i) => h("option", { value: i }, w.app + (w.title ? " — " + w.title : ""))));
}

// a list of windows (match, ignore): rows of app + title regex; writes "app", {title} or {app, title}
function windowList(list, onChange) {
  const box = h("div", { class: "windows" });
  const rows = asList(list).map(w => typeof w === "string" ? { app: w } : { ...w });
  const emit = () => onChange(rows.filter(r => r.app || r.title).map(r =>
    r.title ? (r.app ? { app: r.app, title: r.title } : { title: r.title }) : r.app));
  const draw = () => box.replaceChildren(
    ...rows.map((r, i) => h("div", { class: "act" },
      h("input", { type: "text", value: r.app ?? "", placeholder: IS_MAC() ? "app, e.g. Safari" : "program, e.g. X2.exe",
        oninput: e => { r.app = e.target.value || undefined; emit(); } }),
      h("input", { type: "text", value: r.title ?? "", placeholder: "and a title containing… (optional)",
        oninput: e => { r.title = e.target.value || undefined; emit(); } }),
      h("button", { class: "small ghost", type: "button", title: "Remove", onclick: () => { rows.splice(i, 1); emit(); draw(); } }, "✕"))),
    h("div", { class: "act" },
      h("button", { class: "small link", type: "button", onclick: () => { rows.push({}); draw(); } }, "+ add a window"),
      recentPicker(w => { rows.push(w); emit(); draw(); })));
  draw();
  return box;
}

$("#page-switch").onclick = async () => {
  const P = (await api("/api/page-settings?" + new URLSearchParams({ page }))).raw || {};
  let match = asList(P.match), sticky = !!P.sticky;
  $("#dlg-body").replaceChildren(h("div", {}, focusNote(),
    section("Show this page when one of these has focus",
      h("p", { class: "muted" }, "The app's program name (any case, .exe optional) and/or text in its window title " +
        "(a regular expression). When several pages fit, app + title wins over title, which wins over app alone."),
      windowList(match, l => { match = l; })),
    section("Sticky",
      h("label", { class: "check" }, h("input", { type: "checkbox", checked: sticky, onchange: e => { sticky = e.target.checked; } }),
        "stay on this page while it's shown, whatever has focus"))));
  openDialog(`Page “${page}”: switch by app`, [{ name: "Settings", show: () => showBody(false) }], async () => {
    const r = await api("/api/page-settings", { page, raw: { match: match.length ? match : undefined, sticky: sticky || undefined } });
    if (!r.ok) { dlgError("Not saved:\n\n" + r.error); return false; }
    setStatus("saved ✓", "ok"); await load(); return true;
  });
};

$("#btn-auto").onclick = async () => {
  const res = await api("/api/global?name=auto_switch");
  const A = res.raw || {};
  $("#dlg-yaml").value = res.yaml || "";
  $("#dlg-body").replaceChildren(h("div", {}, focusNote(),
    section("Switching pages by app",
      h("label", { class: "check" }, h("input", { type: "checkbox", checked: A.enabled !== false,
        onchange: e => { if (e.target.checked) delete A.enabled; else A.enabled = false; } }), "on when hubdeck starts"),
      h("p", { class: "muted" }, "A page's “Switch by app…” says which windows show it. An app with no page puts back the page you " +
        "picked yourself. A key can turn switching off and on until hubdeck restarts.")),
    section("Ignore these windows",
      h("p", { class: "muted" }, "They count as if focus never moved, like the taskbar or Alt+Tab."),
      windowList(A.ignore, l => { if (l.length) A.ignore = l; else delete A.ignore; })),
    section("Whenever the focused window changes", actionEditor("on_focus", () => A, () => {}))));
  let yamlTab = false;
  openDialog("Switching pages by app", [
    { name: "Simple", show: () => { yamlTab = false; showBody(false); } },
    { name: "Advanced (YAML)", show: () => { yamlTab = true; showBody(true); } },
  ], async () => {
    const r = await api("/api/global", yamlTab ? { name: "auto_switch", yaml: $("#dlg-yaml").value } : { name: "auto_switch", raw: A });
    if (!r.ok) { dlgError("Not saved:\n\n" + r.error); return false; }
    setStatus("saved ✓", "ok"); await load(); return true;
  });
};

const LIGHT_LOOKS = [["", "leave as it is"], ["static", "steady"], ["breathing", "breathing"], ["strobe", "flashing"],
  ["rainbow", "rainbow"], ["cycling", "colour cycle"], ["wave", "wave"], ["off", "off"]];
$("#btn-lighting").onclick = async () => {
  const res = await api("/api/global?name=lighting");
  const L = res.raw || {};
  $("#dlg-yaml").value = res.yaml || "";
  L.knobs ??= {}; L.faders ??= {};
  const holder = { base: L.base };
  const zone = (title, obj, key, hasValue) => {
    const z = obj[key] ?? {};
    const commit = () => { if (Object.keys(z).length) obj[key] = z; else delete obj[key]; };
    const set = (k, v) => { if (v === "" || v === undefined) delete z[k]; else z[k] = v; commit(); };
    const look = h("select", { onchange: e => set("mode", e.target.value) },
      ...LIGHT_LOOKS.map(([v, label]) => h("option", { value: v, selected: (z.mode ?? "") === v }, label)));
    const color = h("input", { type: "color", value: /^#[0-9a-f]{6}$/i.test(z.color || "") ? z.color : META.accent || "#3cc86e",
      oninput: e => set("color", e.target.value) });
    const rows = [field("Colour", color, look)];
    if (hasValue) rows.push(field("", h("label", { class: "check" }, h("input", { type: "checkbox", checked: z.brightness === "value",
      onchange: e => set("brightness", e.target.checked ? "value" : undefined) }), "brighter when the value is higher")));
    const on = z.on ?? {};
    const own = ownState(z.state), kept = z.state;
    const stateSel = h("select", { onchange: e => { set("state", e.target.value === own?.key ? kept : e.target.value); if (!e.target.value) set("on", undefined); else { on.color ??= RED; set("on", on); } draw(); } },
      h("option", { value: "" }, "never"), ...stateOptions(own));
    const onBox = h("span", { class: "argwrap" });
    const draw = () => onBox.replaceChildren(...(z.state ? [h("span", { class: "muted" }, "then"),
      h("input", { type: "color", value: /^#[0-9a-f]{6}$/i.test(on.color || "") ? on.color : RED, oninput: e => { on.color = e.target.value; set("on", on); } }),
      h("select", { onchange: e => { if (e.target.value) on.mode = e.target.value; else delete on.mode; set("on", on); } },
        ...LIGHT_LOOKS.map(([v, label]) => h("option", { value: v, selected: (on.mode ?? "") === v }, label)))] : []));
    draw();
    rows.push(field("Change when", stateSel, onBox));
    return h("div", { class: "zone" }, h("h3", {}, title), ...rows);
  };
  const body = h("div", {},
    zone("Base", holder, "base", false),
    zone("All knobs", L.knobs, "all", true),
    zone("All faders", L.faders, "all", true),
    h("p", { class: "muted" }, "Per-knob or per-fader colours and animations: use the Advanced tab."));
  $("#dlg-body").replaceChildren(body);
  let yamlTab = false;
  openDialog("Lighting", [
    { name: "Simple", show: () => { yamlTab = false; showBody(false); } },
    { name: "Advanced (YAML)", show: () => { yamlTab = true; showBody(true); } },
  ], async () => {
    let r;
    if (yamlTab) r = await api("/api/global", { name: "lighting", yaml: $("#dlg-yaml").value });
    else {
      const out = { ...L };
      if (holder.base) out.base = holder.base; else delete out.base;
      if (!Object.keys(out.knobs).length) delete out.knobs;
      if (!Object.keys(out.faders).length) delete out.faders;
      r = await api("/api/global", { name: "lighting", raw: out });
    }
    if (!r.ok) { dlgError("Not saved:\n\n" + r.error); return false; }
    setStatus("saved ✓", "ok"); await load(); return true;
  });
};

// ---------------------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------------------
document.querySelectorAll("#editor .tabs button").forEach(b => b.onclick = () => {
  if (cur.tab === "yaml") cur.yamlDirty = $("#ed-yaml").value;
  showTab(b.dataset.tab);
});
$("#ed-yaml").addEventListener("input", () => setStatus("unsaved changes", "warn"));
$("#ed-close").onclick = () => { closeEditor(); render(); };
$("#ed-save").onclick = saveItem;
$("#ed-revert").onclick = () => openEditor(cur.section, cur.num);
$("#ed-delete").onclick = removeItem;
window.addEventListener("resize", () => S && drawBase());

setInterval(async () => {   // keep live faces and the current page fresh while hubdeck runs
  if (!S || !S.live || document.hidden || dlg.open) return;
  try {
    const fresh = await api("/api/state");
    const pageChanged = fresh.current_page !== S.current_page;
    S = fresh; stamp = Date.now();
    drawFocus();
    if (pageChanged) render();
    else document.querySelectorAll("#base img.face").forEach(img => { img.src = img.src.replace(/v=\d+/, "v=" + stamp); });
  } catch { /* hubdeck stopped */ }
}, 3000);

load().catch(e => { setStatus("error", "bad"); $("#error").textContent = e.message; $("#error").classList.remove("hidden"); });
