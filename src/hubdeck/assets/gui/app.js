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
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined) el.append(kid);
  return el;
};

const NAMES = { keys: "15-key", dial: "Dial", knobs: "Knobs", rollers: "Rollers", faders: "Faders" };
const SIZE = { keys: 0.17, dial: 0.78, knobs: 0.3, rollers: [0.16, 0.66], faders: [0.11, 0.8] };
let S = null, META = null, page = null, sel = null, cur = null, stamp = Date.now();

async function api(path, body) {
  const opts = { headers: { "X-Hubdeck-Token": window.TOKEN } };
  if (body !== undefined) {
    opts.method = "POST";
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
  if ((r.headers.get("Content-Type") || "").startsWith("image/")) {
    if (!r.ok) throw new Error((await r.json()).error);
    return r.blob();
  }
  const data = await r.json();
  if (!r.ok && data.error) throw new Error(data.error);
  return data;
}
const imgUrl = (path, q) => `${path}?${new URLSearchParams({ ...q, t: window.TOKEN, v: stamp })}`;

function setStatus(text, kind = "") {
  const el = $("#status");
  el.textContent = text;
  el.className = "pill " + kind;
}

// -- state ---------------------------------------------------------------------
async function load() {
  [S, META] = await Promise.all([api("/api/state"), META ? Promise.resolve(META) : api("/api/meta")]);
  if (!page || !(page in S.pages)) page = S.current_page && S.current_page in S.pages ? S.current_page : S.start_page;
  stamp = Date.now();
  render();
  const err = $("#error");
  err.textContent = S.error ? "The config on disk has a problem (hubdeck keeps running the last good one):\n" + S.error : "";
  err.classList.toggle("hidden", !S.error);
  setStatus(S.live ? "live: connected to running hubdeck" : "editing " + S.path.split(/[\\/]/).pop(), S.error ? "bad" : "ok");
}

function sectionOf(section, p = page) {
  // pages without their own dial/knobs/rollers/faders use the start page's
  const own = (S.pages[p] || {})[section];
  if (own || section === "keys") return { items: own || {}, from: p };
  return { items: (S.pages[S.start_page] || {})[section] || {}, from: S.start_page };
}

// -- device drawing -------------------------------------------------------------
function render() {
  const nav = $("#pages");
  nav.replaceChildren(...Object.keys(S.pages).map(p => h("button", {
    class: p === page ? "active" : "", onclick: () => { page = p; closeEditor(); render(); },
  }, p, p === S.start_page ? h("span", { class: "start" }, "start") : null,
     S.current_page === p ? h("span", { class: "live", title: "shown on the device now" }) : null)));
  $("#page-title").textContent = `Page “${page}”`;
  $("#page-start").classList.toggle("hidden", page === S.start_page);
  $("#page-delete").classList.toggle("hidden", page === S.start_page);
  drawBase();
}

function cellPx() {
  const area = $("#device-area").clientWidth - 40;
  return Math.max(70, Math.min(130, Math.floor(area / S.layout.cols)));
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
  $("#unplaced").replaceChildren(...(unplaced.length ? [h("div", { class: "muted" }, "Not on the base (doesn't fit):"), ...unplaced] : []));
  if (!Object.keys(L.modules).length)
    $("#base").append(h("p", { class: "muted", style: "padding:20px" }, "No modules connected or configured."));
}

function moduleBox(kind, m, c, gap) {
  const w = m.w * c - gap, hgt = m.h * c - gap;
  const badge = !m.connected ? "not connected" : (!m.used ? "not used yet" : "");
  const bar = h("div", { class: "bar", draggable: "true" },
    h("span", { class: "name" }, NAMES[kind]),
    m.rotate ? h("span", {}, `${m.rotate}°`) : null,
    badge ? h("span", { class: "badge" }, badge) : null,
    h("span", { class: "spacer" }),
    h("button", { title: "Mounted rotated? Rotate 90° clockwise", onclick: () => rotateModule(kind, m.rotate) }, "↻"));
  bar.addEventListener("dragstart", e => e.dataTransfer.setData("text/kind", kind));
  const body = h("div", { class: "body" });
  const box = h("div", { class: "module" + (m.connected ? "" : " off"), style: `width:${w}px;height:${hgt}px` }, bar, body);
  const bw = w, bh = hgt - 22;
  const { items, from } = sectionOf(kind);
  for (const ctl of m.controls) body.append(controlEl(kind, m, ctl, items, from, bw, bh, c));
  return box;
}

function controlEl(kind, m, ctl, items, from, bw, bh, c) {
  const num = String(ctl.number), summary = items[num];
  const configured = summary !== undefined;
  const side = m.rotate % 180 !== 0;
  let w, hh, inner;
  if (kind === "keys") {
    w = hh = Math.min(bw / (side ? 3 : 5), bh / (side ? 5 : 3)) * 0.84;
    inner = configured ? h("img", { class: "face", src: imgUrl("/api/face.png", { page: from, section: kind, num }) }) : h("div", { class: "face" });
  } else if (kind === "dial") {
    w = hh = Math.min(bw, bh) * 0.82;
    inner = configured ? h("img", { class: "face", src: imgUrl("/api/face.png", { page: from, section: "dial", num: 1 }) }) : h("div", { class: "face" });
  } else if (kind === "knobs") {
    w = hh = c * 0.62; inner = h("div", { class: "knob" });
  } else if (kind === "rollers") {
    [w, hh] = side ? [c * 1.3, c * 0.42] : [c * 0.42, c * 0.78]; inner = h("div", { class: "roller" });
  } else {
    const len = c * 1.45, thick = c * 0.16;
    [w, hh] = side ? [len, thick * 2] : [thick * 2, len];
    const track = h("div", { class: "track", style: side ? `width:${len}px;height:6px` : `width:6px;height:${len}px` });
    const thumb = h("div", { class: "thumb", style: side ? `width:10px;height:${thick * 1.6}px` : `width:${thick * 1.6}px;height:10px` });
    inner = h("div", { class: "fader" }, track, thumb);
  }
  const isSel = sel && sel.section === kind && String(sel.num) === num;
  const el = h("div", {
    class: `ctl ${kind}${configured ? "" : " empty"}${isSel ? " sel" : ""}`,
    style: `left:${ctl.x * bw}px;top:${ctl.y * bh}px;width:${w}px;height:${hh}px`,
    title: configured ? `${NAMES[kind]} ${num}: ${summary}` : `${NAMES[kind]} ${num}: not set (click to add)`,
    onclick: () => openEditor(kind, kind === "dial" ? 1 : Number(num)),
  }, inner, kind !== "dial" ? h("span", { class: "num" }, num) : null);
  if (kind !== "keys" && kind !== "dial" && configured) el.append(h("span", { class: "tip" }, summary));
  return el;
}

async function moduleSettings(change) {
  const r = await api("/api/global?name=modules");
  const mods = r.raw || {};
  change(mods);
  await saveGlobal("modules", mods);
}
const rotateModule = (kind, rot) => moduleSettings(m => { m[kind] = { ...(m[kind] || {}), rotate: (rot + 90) % 360 }; });
function moveModule(kind, x, y) {
  // Dropping onto another module swaps the two; everything ends up with an explicit position so
  // the arrangement stays exactly as placed.
  const mods = S.layout.modules, me = mods[kind];
  if (!kind || !me) return;
  const L = S.layout;
  x = Math.max(0, Math.min(x, L.cols - me.w));
  y = Math.max(0, Math.min(y, L.rows - me.h));
  const overlaps = (a, ax, ay, b) => b.x !== null && b.x !== undefined &&
    ax < b.x + b.w && b.x < ax + a.w && ay < b.y + b.h && b.y < ay + a.h;
  return moduleSettings(m => {
    for (const [k, o] of Object.entries(mods)) {
      if (k !== kind && o.x !== null && o.x !== undefined) m[k] = { ...(m[k] || {}), position: [o.x, o.y] };
    }
    for (const [k, o] of Object.entries(mods)) {
      if (k === kind || !overlaps(me, x, y, o)) continue;
      if (me.x !== null && me.x !== undefined) {
        // put the other module where this one was (clamped so it stays on the base)
        m[k].position = [Math.max(0, Math.min(me.x, L.cols - o.w)), Math.max(0, Math.min(me.y, L.rows - o.h))];
      } else {
        delete m[k].position;     // nowhere to swap to: let it be auto-placed
      }
    }
    m[kind] = { ...(m[kind] || {}), position: [x, y] };
  });
}

async function saveGlobal(name, raw) {
  const r = await api("/api/global", { name, raw });
  if (!r.ok) { alert("Not saved:\n\n" + r.error); return false; }
  setStatus("saved", "ok");
  await load();
  return true;
}

// -- actions / values helpers ---------------------------------------------------
const actionNames = () => Object.entries(META.plugins).flatMap(([p, d]) => Object.keys(d.actions).map(a => `${p}.${a}`));
const stateNames = () => Object.entries(META.plugins).flatMap(([p, d]) => Object.keys(d.states).map(s => `${p}.${s}`));
const valueNames = () => Object.entries(META.plugins).flatMap(([p, d]) => Object.keys(d.values).map(v => `${p}.${v}`));
const shortName = full => Object.entries(META.aliases).find(([, f]) => f === full)?.[0];

function parseAction(a) {
  if (typeof a === "string") return { name: a, arg: undefined };
  const [k, v] = Object.entries(a)[0];
  return { name: META.aliases[k] || k, arg: v };
}
function formatAction({ name, arg }) {
  if (arg === undefined || arg === "") return name;
  return { [shortName(name) || name]: arg };
}
const asList = v => v === undefined || v === null ? [] : Array.isArray(v) ? v : [v];
function argText(v) { return v === undefined ? "" : typeof v === "object" ? JSON.stringify(v) : String(v); }
function parseArg(t) {
  t = t.trim();
  if (t === "") return undefined;
  if (/^-?\d+(\.\d+)?$/.test(t)) return Number(t);
  if (t === "true" || t === "false") return t === "true";
  if (/^[\[{]/.test(t)) { try { return JSON.parse(t); } catch { /* keep as text */ } }
  return t;
}

const getPath = (o, path) => path.reduce((x, k) => (x == null ? undefined : x[k]), o);
function setPath(o, path, value) {
  let x = o;
  for (const k of path.slice(0, -1)) x = x[k] ??= {};
  const last = path[path.length - 1];
  if (value === undefined || value === "" || value === null) delete x[last]; else x[last] = value;
  for (let i = path.length - 1; i > 0; i--) {   // drop emptied parents
    const parent = getPath(o, path.slice(0, i - 1)) ?? o, key = path[i - 1];
    if (parent[key] && typeof parent[key] === "object" && !Object.keys(parent[key]).length) delete parent[key]; else break;
  }
}

// -- editor -------------------------------------------------------------------------
async function openEditor(section, num) {
  sel = { section, num };
  const own = (S.pages[page] || {})[section];
  const inherited = section !== "keys" && !own && sectionOf(section).from !== page;
  const src = inherited ? S.start_page : page;
  const item = await api(`/api/item?${new URLSearchParams({ page: src, section, num })}`);
  cur = { section, num, raw: structuredClone(item.raw || {}), orig: item.raw, yaml: item.yaml, inherited, tab: "form" };
  $("#editor").classList.remove("hidden");
  $("#ed-title").textContent = section === "dial" ? "Dial" : `${NAMES[section]} ${num}`;
  $("#ed-delete").classList.toggle("hidden", !item.raw || inherited);
  showTab("form");
  render();
}
function closeEditor() { sel = cur = null; $("#editor").classList.add("hidden"); }

function showTab(tab) {
  cur.tab = tab;
  document.querySelectorAll("#editor .tabs button").forEach(b => b.classList.toggle("active", b.dataset.tab === tab));
  $("#ed-form").classList.toggle("hidden", tab !== "form");
  $("#ed-yaml").classList.toggle("hidden", tab !== "yaml");
  $("#ed-error").classList.add("hidden");
  if (tab === "yaml") {
    if (cur.yamlDirty !== undefined) $("#ed-yaml").value = cur.yamlDirty;
    else if (cur.formDirty) api("/api/to-yaml", { raw: cur.raw }).then(r => { $("#ed-yaml").value = r.yaml; });
    else $("#ed-yaml").value = cur.yaml ?? "";
  } else buildForm();
}

function row(label, ...inputs) { return h("div", { class: "row" }, h("label", {}, label), ...inputs); }
function textIn(path, placeholder = "") {
  return h("input", { type: "text", value: getPath(cur.raw, path) ?? "", placeholder,
    oninput: e => { setPath(cur.raw, path, e.target.value); changed(); } });
}
function numIn(path, placeholder = "") {
  return h("input", { type: "number", value: getPath(cur.raw, path) ?? "", placeholder, step: "any",
    oninput: e => { setPath(cur.raw, path, e.target.value === "" ? undefined : Number(e.target.value)); changed(); } });
}
function checkIn(path) {
  return h("input", { type: "checkbox", checked: !!getPath(cur.raw, path),
    onchange: e => { setPath(cur.raw, path, e.target.checked || undefined); changed(); } });
}
function selectIn(path, options, blank = "—") {
  const v = getPath(cur.raw, path) ?? "";
  return h("select", { onchange: e => { setPath(cur.raw, path, e.target.value || undefined); changed(); buildForm(); } },
    h("option", { value: "" }, blank), ...options.map(o => h("option", { value: o, selected: o === v }, o)));
}
function colorIn(path) {
  const v = getPath(cur.raw, path) ?? "";
  const text = h("input", { type: "text", value: v, placeholder: "default", style: "flex:1",
    oninput: e => { setPath(cur.raw, path, e.target.value); changed(); } });
  const pick = h("input", { type: "color", value: /^#[0-9a-f]{6}$/i.test(v) ? v : "#000000",
    oninput: e => { text.value = e.target.value; setPath(cur.raw, path, e.target.value); changed(); } });
  return [pick, text];
}

function iconPicker(path) {
  const results = h("div", { class: "icons" });
  const input = textIn(path, "e.g. microphone");
  let timer;
  const search = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const q = input.value.trim();
      if (q.length < 2) { results.replaceChildren(); return; }
      const r = await api(`/api/icons?${new URLSearchParams({ q })}`);
      results.replaceChildren(...r.icons.map(name => h("img", {
        src: imgUrl("/api/icon.png", { name }), title: name, loading: "lazy",
        onclick: () => { input.value = name; setPath(cur.raw, path, name); changed(); results.replaceChildren(); },
      })));
    }, 250);
  };
  input.addEventListener("input", search);
  return [row("icon", input), results];
}

function faceFields(base, withGauge) {
  return [
    ...iconPicker([...base, "icon"]),
    row("filled icon", checkIn([...base, "filled"])),
    row("label", textIn([...base, "label"], "{value}%, {system.time:%H:%M} …")),
    row("text", textIn([...base, "text"], "big text instead of an icon")),
    row("background", ...colorIn([...base, "bg"])),
    row("icon colour", ...colorIn([...base, "fg"])),
    row("text colour", ...colorIn([...base, "label_color"])),
    row("image / GIF", textIn([...base, "image"], "file next to config, or https://…")),
    row("image fit", selectIn([...base, "image_fit"], ["cover", "contain"], "cover")),
    withGauge ? row("value gauge", checkIn([...base, "gauge"])) : null,
  ];
}

function actionList(key, label) {
  // the press gesture is stored as `action` (or on_press)
  const field = key === "press" ? (cur.raw.on_press !== undefined ? "on_press" : "action") : `on_${key}`;
  const list = asList(cur.raw[field]).map(parseAction);
  const box = h("div");
  const commit = () => {
    const out = list.filter(a => a.name).map(formatAction);
    delete cur.raw[field];
    if (out.length) cur.raw[field === "on_press" ? "on_press" : field] = out.length === 1 ? out[0] : out;
    changed();
  };
  const draw = () => {
    box.replaceChildren(...list.map((a, i) => h("div", { class: "act" },
      h("select", { onchange: e => { a.name = e.target.value; commit(); } },
        h("option", { value: "" }, "— action —"),
        ...actionNames().map(n => h("option", { value: n, selected: n === a.name }, n))),
      h("input", { type: "text", value: argText(a.arg), placeholder: "argument",
        oninput: e => { a.arg = parseArg(e.target.value); commit(); } }),
      h("button", { class: "small", type: "button", onclick: () => { list.splice(i, 1); commit(); draw(); } }, "✕"))),
      h("button", { class: "small", type: "button", onclick: () => { list.push({ name: "", arg: undefined }); draw(); } }, "+ action"));
  };
  draw();
  return h("fieldset", {}, h("legend", {}, label), box);
}

function turnFields() {
  const turn = cur.raw.turn;
  if (typeof turn === "string") cur.raw.turn = { target: turn };
  const mode = cur.raw.turn && (cur.raw.turn.cw || cur.raw.turn.ccw) ? "actions" : "target";
  const modeSel = h("select", { onchange: e => {
    cur.raw.turn = e.target.value === "actions" ? { cw: [], ccw: [] } : {}; changed(); buildForm(); } },
    h("option", { value: "target", selected: mode === "target" }, "adjust a value"),
    h("option", { value: "actions", selected: mode === "actions" }, "run actions per click"));
  const fs = h("fieldset", {}, h("legend", {}, sel.section === "rollers" ? "Rolling" : "Turning"), row("does", modeSel));
  if (mode === "target") {
    fs.append(row("value", selectIn(["turn", "target"], valueNames(), "— nothing —")),
      row("argument", textIn(["turn", "arg"], "app for app_volume, e.g. spotify.exe")),
      row("step", numIn(["turn", "step"], "2")),
      row("invert", checkIn(["turn", "invert"])));
  } else {
    for (const dir of ["cw", "ccw"]) {
      const list = asList(cur.raw.turn[dir]).map(parseAction);
      const box = h("div");
      const commit = () => { const out = list.filter(a => a.name).map(formatAction); cur.raw.turn[dir] = out.length === 1 ? out[0] : out; changed(); };
      const draw = () => box.replaceChildren(...list.map((a, i) => h("div", { class: "act" },
        h("select", { onchange: e => { a.name = e.target.value; commit(); } }, h("option", { value: "" }, "— action —"),
          ...actionNames().map(n => h("option", { value: n, selected: n === a.name }, n))),
        h("input", { type: "text", value: argText(a.arg), placeholder: "argument", oninput: e => { a.arg = parseArg(e.target.value); commit(); } }),
        h("button", { class: "small", type: "button", onclick: () => { list.splice(i, 1); commit(); draw(); } }, "✕"))),
        h("button", { class: "small", type: "button", onclick: () => { list.push({ name: "" }); draw(); } }, "+ action"));
      draw();
      fs.append(h("div", { class: "muted" }, dir === "cw" ? "clockwise / up / right:" : "counter-clockwise / down / left:"), box);
    }
  }
  return fs;
}

function buildForm() {
  const f = $("#ed-form"), s = cur.section;
  const parts = [];
  if (cur.inherited) parts.push(h("p", { class: "muted" },
    `Inherited from page “${S.start_page}”. Saving here creates an override just for page “${page}”.`));
  if (s === "faders") {
    parts.push(h("fieldset", {}, h("legend", {}, "Fader"),
      row("value", selectIn(["target"], valueNames(), "— nothing —")),
      row("argument", textIn(["arg"], "app for app_volume, e.g. chrome.exe")),
      row("invert", checkIn(["invert"])),
      h("p", { class: "muted" }, "Soft takeover: the fader takes control once it reaches the current value.")));
  } else {
    if (s === "keys" || s === "dial") parts.push(h("fieldset", {}, h("legend", {}, "Face"), ...faceFields(["face"], s === "dial")));
    if (s !== "keys") parts.push(turnFields());
    if (s !== "rollers") {
      parts.push(actionList("press", "Press"), actionList("long_press", "Long press"),
        actionList("double_press", "Double press"), actionList("down", "Key down"), actionList("up", "Key up"));
    }
    if (s === "keys" || s === "dial") {
      const st = h("fieldset", {}, h("legend", {}, "State-driven face"),
        row("state", selectIn(["state"], stateNames(), "— none —")));
      if (cur.raw.state) st.append(h("div", { class: "muted" }, "Face while the state is on (empty = same as above):"),
        row("icon", textIn(["face_on", "icon"])), row("label", textIn(["face_on", "label"])),
        row("background", ...colorIn(["face_on", "bg"])), row("icon colour", ...colorIn(["face_on", "fg"])),
        h("div", { class: "muted" }, "Unknown state (e.g. app offline), face_unknown and other fields: use the YAML tab."));
      parts.push(st);
    }
  }
  f.replaceChildren(...parts);
  preview();
}

let previewTimer;
function changed() { cur.yamlDirty = undefined; cur.formDirty = true; setStatus("unsaved changes", ""); preview(); }
function preview() {
  const img = $("#ed-preview"), s = cur.section;
  $("#ed-preview-wrap").classList.toggle("hidden", !(s === "keys" || s === "dial"));
  img.classList.toggle("round", s === "dial");
  if (!(s === "keys" || s === "dial")) return;
  clearTimeout(previewTimer);
  previewTimer = setTimeout(async () => {
    try {
      const blob = await api("/api/preview.png", { page, section: s, num: cur.num, raw: cur.raw });
      img.src = URL.createObjectURL(blob);
      $("#ed-error").classList.add("hidden");
    } catch (e) { showEdError(e.message); }
  }, 250);
}
function showEdError(msg) { const el = $("#ed-error"); el.textContent = msg; el.classList.toggle("hidden", !msg); }

async function saveItem() {
  const body = { page, section: cur.section, num: cur.num };
  if (cur.tab === "yaml") body.yaml = $("#ed-yaml").value; else body.raw = cur.raw;
  const r = await api("/api/item", body);
  if (!r.ok) { showEdError("Not saved (the config would be invalid):\n" + r.error); setStatus("not saved", "bad"); return; }
  setStatus("saved", "ok");
  const keep = { ...sel };
  await load();
  await openEditor(keep.section, keep.num);
}
async function removeItem() {
  if (!confirm("Remove this control's settings from this page?")) return;
  const r = await api("/api/item", { page, section: cur.section, num: cur.num, raw: null });
  if (!r.ok) { showEdError(r.error); return; }
  closeEditor(); await load();
}

// -- pages ---------------------------------------------------------------------------
async function pageAction(action, extra = {}) {
  const r = await api("/api/page", { action, name: page, ...extra });
  if (!r.ok) { alert(r.error); return; }
  if (action === "rename") page = extra.new;
  if (action === "delete") page = null;
  await load();
}
$("#add-page").onclick = async () => {
  const name = prompt("Name of the new page (letters, digits, - or _):");
  if (!name) return;
  const r = await api("/api/page", { action: "add", name });
  if (!r.ok) { alert(r.error); return; }
  page = name.trim(); await load();
};
$("#page-rename").onclick = () => { const n = prompt("New name:", page); if (n && n !== page) pageAction("rename", { new: n.trim() }); };
$("#page-delete").onclick = () => confirm(`Delete page “${page}” and everything on it?`) && pageAction("delete");
$("#page-start").onclick = () => pageAction("start");

// -- dialogs: lighting, device & modules, YAML -----------------------------------------
const dlg = $("#dlg");
let dlgSave = null;
function openDialog(title, tabs, onSave) {
  $("#dlg-title").textContent = title;
  $("#dlg-error").classList.add("hidden");
  $("#dlg-tabs").replaceChildren(...tabs.map((t, i) => h("button", { type: "button", class: i ? "" : "active",
    onclick: e => { document.querySelectorAll("#dlg-tabs button").forEach(b => b.classList.remove("active"));
      e.target.classList.add("active"); t.show(); } }, t.name)));
  dlgSave = onSave;
  tabs[0].show();
  dlg.showModal();
}
function dlgError(msg) { const el = $("#dlg-error"); el.textContent = msg; el.classList.toggle("hidden", !msg); }
$("#dlg-save").onclick = async e => {
  e.preventDefault();
  try { if (await dlgSave()) dlg.close(); } catch (err) { dlgError(err.message); }
};
const showBody = (yaml) => { $("#dlg-body").classList.toggle("hidden", yaml); $("#dlg-yaml").classList.toggle("hidden", !yaml); };

$("#btn-yaml").onclick = async () => {
  const r = await api("/api/yaml");
  $("#dlg-yaml").value = r.yaml;
  openDialog("config.yaml", [{ name: "Whole file", show: () => showBody(true) }], async () => {
    const res = await api("/api/yaml", { yaml: $("#dlg-yaml").value });
    if (!res.ok) { dlgError("Not saved (invalid):\n" + res.error); return false; }
    setStatus("saved", "ok"); await load(); return true;
  });
};

$("#btn-settings").onclick = async () => {
  const [dev, mods] = await Promise.all([api("/api/global?name=device"), api("/api/global?name=modules")]);
  const d = dev.raw || {}, m = mods.raw || {};
  const sel2 = (obj, key, opts, def) => h("select", { onchange: e => { obj[key] = e.target.value; } },
    ...opts.map(o => h("option", { value: o, selected: String(obj[key] ?? def) === String(o) }, o)));
  const body = h("div", {},
    h("div", { class: "zone" }, h("h3", {}, "Device"),
      row("base", sel2(d, "orientation", ["horizontal", "vertical"], "horizontal")),
      row("ownership", sel2(d, "ownership", ["shared", "exclusive"], "shared")),
      h("p", { class: "muted" }, "shared: hubdeck only touches controls in the config. exclusive: it owns everything.")),
    ...["keys", "dial", "knobs", "rollers", "faders"].map(k => {
      m[k] ??= {};
      const rot = h("select", { onchange: e => { m[k].rotate = Number(e.target.value); } },
        ...[0, 90, 180, 270].map(r => h("option", { value: r, selected: (m[k].rotate ?? 0) === r }, `${r}°`)));
      const z = h("div", { class: "zone" }, h("h3", {}, NAMES[k]), row("mounted at", rot));
      if (k === "keys" || k === "dial") z.append(row("backlight", h("input", { type: "number", min: 0, max: 100,
        value: m[k].brightness ?? "", placeholder: "leave as is",
        oninput: e => { m[k].brightness = e.target.value === "" ? undefined : Number(e.target.value); } })));
      return z;
    }));
  $("#dlg-body").replaceChildren(body);
  openDialog("Device & modules", [{ name: "Settings", show: () => showBody(false) }], async () => {
    for (const k of Object.keys(m)) { if (m[k].rotate === 0) delete m[k].rotate; if (m[k].brightness == null) delete m[k].brightness;
      if (!Object.keys(m[k]).length) delete m[k]; }
    let r = await api("/api/global", { name: "device", raw: d });
    if (r.ok) r = await api("/api/global", { name: "modules", raw: m });
    if (!r.ok) { dlgError(r.error); return false; }
    setStatus("saved", "ok"); await load(); return true;
  });
};

$("#btn-lighting").onclick = async () => {
  const res = await api("/api/global?name=lighting");
  const L = res.raw || {};
  $("#dlg-yaml").value = res.yaml || "";
  const zoneForm = (title, obj, key) => {
    const z = obj[key] ?? {};
    const set = (k, v) => { if (v === "" || v === undefined) delete z[k]; else z[k] = v; obj[key] = z; if (!Object.keys(z).length) delete obj[key]; };
    const on = z.on ?? {};
    const setOn = (k, v) => { if (v === "" || v === undefined) delete on[k]; else on[k] = v; if (Object.keys(on).length) set("on", on); else set("on", undefined); };
    const mode = h("select", { onchange: e => set("mode", e.target.value) }, h("option", { value: "" }, "—"),
      ...META.led_modes.map(mo => h("option", { value: mo, selected: z.mode === mo }, mo)));
    const color = h("input", { type: "text", value: z.color ?? "", placeholder: "#3cc86e", oninput: e => set("color", e.target.value) });
    const pick = h("input", { type: "color", value: /^#[0-9a-f]{6}$/i.test(z.color || "") ? z.color : "#ffffff",
      oninput: e => { color.value = e.target.value; set("color", e.target.value); } });
    const bright = h("input", { type: "text", value: z.brightness ?? "", placeholder: "0-100 or value",
      oninput: e => set("brightness", parseArg(e.target.value)) });
    const state = h("select", { onchange: e => set("state", e.target.value) }, h("option", { value: "" }, "— none —"),
      ...stateNames().map(sn => h("option", { value: sn, selected: z.state === sn }, sn)));
    const onColor = h("input", { type: "text", value: on.color ?? "", placeholder: "colour while on", oninput: e => setOn("color", e.target.value) });
    const onMode = h("select", { onchange: e => setOn("mode", e.target.value) }, h("option", { value: "" }, "—"),
      ...META.led_modes.map(mo => h("option", { value: mo, selected: on.mode === mo }, mo)));
    return h("div", { class: "zone" }, h("h3", {}, title),
      h("div", { class: "grid2" }, row("mode", mode), row("colour", pick, color), row("brightness", bright),
        row("state", state), row("on: colour", onColor), row("on: mode", onMode)));
  };
  L.knobs ??= {}; L.faders ??= {};
  const holder = { base: L.base };
  const body = h("div", {},
    h("p", { class: "muted" }, "brightness “value” follows the control's value. Animations (pulse/blink/cycle) and min_brightness: use the YAML tab."),
    zoneForm("Base", holder, "base"),
    zoneForm("All knobs", L.knobs, "all"), ...[1, 2, 3].map(i => zoneForm(`Knob ${i}`, L.knobs, String(i))),
    zoneForm("All faders", L.faders, "all"), ...[1, 2, 3, 4, 5].map(i => zoneForm(`Fader ${i}`, L.faders, String(i))));
  $("#dlg-body").replaceChildren(body);
  let yamlTab = false;
  openDialog("Lighting", [
    { name: "Form", show: () => { yamlTab = false; showBody(false); } },
    { name: "YAML", show: () => { yamlTab = true; showBody(true); } },
  ], async () => {
    let r;
    if (yamlTab) r = await api("/api/global", { name: "lighting", yaml: $("#dlg-yaml").value });
    else {
      const out = {};
      if (holder.base) out.base = holder.base;
      if (Object.keys(L.knobs).length) out.knobs = L.knobs;
      if (Object.keys(L.faders).length) out.faders = L.faders;
      r = await api("/api/global", { name: "lighting", raw: out });
    }
    if (!r.ok) { dlgError("Not saved (invalid):\n" + r.error); return false; }
    setStatus("saved", "ok"); await load(); return true;
  });
};

// -- wiring ----------------------------------------------------------------------------
document.querySelectorAll("#editor .tabs button").forEach(b => b.onclick = () => {
  if (cur.tab === "yaml") cur.yamlDirty = $("#ed-yaml").value;
  showTab(b.dataset.tab);
});
$("#ed-yaml").addEventListener("input", () => setStatus("unsaved changes"));
$("#ed-close").onclick = () => { closeEditor(); render(); };
$("#ed-save").onclick = saveItem;
$("#ed-revert").onclick = () => openEditor(cur.section, cur.num);
$("#ed-delete").onclick = removeItem;
window.addEventListener("resize", () => S && drawBase());

// keep live faces and the current page fresh while hubdeck runs
setInterval(async () => {
  if (!S || !S.live || document.hidden || dlg.open) return;
  try {
    const fresh = await api("/api/state");
    const pageChanged = fresh.current_page !== S.current_page;
    S = fresh; stamp = Date.now();
    if (pageChanged) render(); else document.querySelectorAll("#base img.face").forEach(img => {
      img.src = img.src.replace(/v=\d+/, "v=" + stamp);
    });
  } catch { /* hubdeck stopped */ }
}, 3000);

load().catch(e => { setStatus("error", "bad"); $("#error").textContent = e.message; $("#error").classList.remove("hidden"); });
