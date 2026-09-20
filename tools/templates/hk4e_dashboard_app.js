/* hk4e dashboard app — vanilla JS, zero deps */
const $ = (s) => document.querySelector(s);
const fmt = (n) => n.toLocaleString("en-US");
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

/* ---------- tab switching ---------- */
document.querySelectorAll("nav button").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("nav button").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    btn.classList.add("active");
    $("#tab-" + btn.dataset.tab).classList.add("active");
  });
});

/* ---------- header chips + overview ---------- */
$("#chip-nodes").textContent = fmt(DATA.meta.nodes);
$("#chip-edges").textContent = fmt(DATA.meta.edges);
$("#chip-res").textContent = fmt(DATA.stats.resource_total);

function renderCards() {
  const items = [
    ["三元组 (TTL)", DATA.meta.triples], ["图节点", DATA.meta.nodes], ["图边", DATA.meta.edges],
    ["SemanticResource", DATA.stats.resource_total],
    ["实体类", DATA.stats.node_types.EntityClass], ["Avatar 实例", DATA.stats.node_types.Instance],
  ];
  $("#cards").innerHTML = items.map(([k, v]) =>
    `<div class="card"><div class="v">${fmt(v)}</div><div class="k">${k}</div></div>`).join("");
}

const PALETTE = ["#5b8cff", "#37c988", "#d19a66", "#c678dd", "#ffb86c", "#ff7b72", "#4ec9b0", "#9aa4b2", "#e06c9f", "#8fbf5f"];
function renderBars(elId, title, rows, unit) {
  const max = Math.max(...rows.map((r) => r[1]));
  const html = rows.map(([label, v], i) => `
    <div class="brow">
      <div class="lbl" title="${esc(label)}">${esc(label)}</div>
      <div class="track"><div class="fill" style="width:${(v / max * 100).toFixed(1)}%;background:${PALETTE[i % PALETTE.length]}"></div></div>
      <div class="num">${fmt(v)}${unit || ""}</div>
    </div>`).join("");
  return `<div class="barbox"><h3>${title}</h3>${html}</div>`;
}

function renderOverview() {
  const nt = Object.entries(DATA.stats.node_types).sort((a, b) => b[1] - a[1]);
  const et = DATA.stats.edge_groups;
  const rk = Object.entries(DATA.stats.resource_kinds).sort((a, b) => b[1] - a[1]);
  const dm = DATA.stats.domains.map((d) => [d.name, d.classes]);
  $("#bars").innerHTML =
    renderBars("nt", "图节点类型分布", nt) +
    renderBars("et", "边类型分组（断言边取 Top 6）", et) +
    renderBars("rk", "SemanticResource kind 分布", rk) +
    renderBars("dm", "七大域：可归属实体类数量（含传递子类）", dm);
}

/* ---------- tooltip helper ---------- */
function makeTooltip(sel) {
  const tip = $(sel);
  return {
    show(html, x, y) {
      tip.innerHTML = html;
      tip.style.display = "block";
      const w = tip.offsetWidth, h = tip.offsetHeight;
      tip.style.left = Math.min(x + 16, window.innerWidth - w - 10) + "px";
      tip.style.top = Math.min(y + 14, window.innerHeight - h - 10) + "px";
    },
    hide() { tip.style.display = "none"; },
  };
}

function nodeTipHtml(n) {
  let extra = "";
  if (n.facts && Object.keys(n.facts).length) {
    extra = `<div class="t-facts">` + Object.entries(n.facts).slice(0, 8)
      .map(([k, v]) => `${esc(k)} = ${esc(String(v).slice(0, 60))}`).join("<br>") + `</div>`;
  }
  return `<div class="t-name">${esc(n.label || n.name)}</div>
    <div class="t-id">[${esc(n.ntype)}] ${esc(n.id)}</div>
    ${n.desc ? `<div class="t-desc">${esc(n.desc.slice(0, 220))}${n.desc.length > 220 ? "…" : ""}</div>` : ""}
    ${extra}`;
}

/* ---------- force engine (shared) ---------- */
class ForceGraph {
  constructor(canvas, opts) {
    this.cv = canvas; this.cx = canvas.getContext("2d");
    this.opts = Object.assign({ onHover: null, onClickNode: null, edgePanel: null }, opts);
    this.nodes = []; this.edges = []; this.alpha = 1;
    this.scale = 1; this.ox = 0; this.oy = 0;
    this.hover = null; this.drag = null; this.panning = false;
    this._bind(); this._loop();
  }
  setData(nodes, edges, opts2 = {}) {
    Object.assign(this, opts2);
    this.nodes = nodes; this.edges = edges; this.alpha = 1;
    const n = nodes.length, R = Math.min(this.cv.width, this.cv.height) * 0.36 / Math.max(this.scale, 0.4);
    nodes.forEach((nd, i) => {
      if (nd.x === undefined) {
        const a = (i / Math.max(n, 1)) * Math.PI * 2;
        nd.x = Math.cos(a) * R * (0.4 + 0.6 * Math.random());
        nd.y = Math.sin(a) * R * (0.4 + 0.6 * Math.random());
        nd.vx = 0; nd.vy = 0;
      }
    });
    this.idx = new Map(nodes.map((nd, i) => [nd.key, i]));
  }
  _bind() {
    const cv = this.cv;
    const pos = (e) => {
      const r = cv.getBoundingClientRect();
      return [((e.clientX - r.left) - this.ox) / this.scale, ((e.clientY - r.top) - this.oy) / this.scale];
    };
    const hit = (x, y) => {
      for (let i = this.nodes.length - 1; i >= 0; i--) {
        const nd = this.nodes[i];
        const dx = nd.x - x, dy = nd.y - y;
        if (dx * dx + dy * dy < (nd.r + 4) * (nd.r + 4)) return nd;
      }
      return null;
    };
    cv.addEventListener("mousedown", (e) => {
      const [x, y] = pos(e); const nd = hit(x, y);
      this._moved = false;
      if (nd) { this.drag = nd; nd.fx = nd.x; nd.fy = nd.y; this.alpha = Math.max(this.alpha, 0.4); }
      else this.panning = true;
      cv.classList.add("dragging");
      this._px = e.clientX; this._py = e.clientY;
    });
    window.addEventListener("mousemove", (e) => {
      if (this.drag || this.panning) {
        if (Math.abs(e.clientX - this._px) + Math.abs(e.clientY - this._py) > 3) this._moved = true;
      }
      if (this.drag) {
        const [x, y] = pos(e);
        this.drag.fx = x; this.drag.fy = y; this.drag.x = x; this.drag.y = y;
      } else if (this.panning) {
        this.ox += e.clientX - this._px; this.oy += e.clientY - this._py;
        this._px = e.clientX; this._py = e.clientY;
      }
    });
    window.addEventListener("mouseup", () => {
      if (this.drag) { delete this.drag.fx; delete this.drag.fy; this.drag = null; }
      this.panning = false; cv.classList.remove("dragging");
    });
    cv.addEventListener("mousemove", (e) => {
      const [x, y] = pos(e); const nd = hit(x, y);
      this.hover = nd;
      cv.style.cursor = nd ? "pointer" : "grab";
      if (this.opts.onHover) this.opts.onHover(nd, e.clientX, e.clientY);
    });
    cv.addEventListener("mouseleave", () => { this.hover = null; if (this.opts.onHover) this.opts.onHover(null, 0, 0); });
    cv.addEventListener("click", (e) => {
      if (this._moved) return;
      const [x, y] = pos(e); const nd = hit(x, y);
      if (nd && this.opts.onClickNode) this.opts.onClickNode(nd);
    });
    cv.addEventListener("wheel", (e) => {
      e.preventDefault();
      const r = cv.getBoundingClientRect();
      const mx = e.clientX - r.left, my = e.clientY - r.top;
      const f = e.deltaY < 0 ? 1.15 : 1 / 1.15;
      this.ox = mx - (mx - this.ox) * f; this.oy = my - (my - this.oy) * f;
      this.scale *= f;
    }, { passive: false });
  }
  _step() {
    if (this.alpha < 0.005 || !this.nodes.length) return;
    const N = this.nodes, E = this.edges, a = this.alpha;
    for (let i = 0; i < N.length; i++) {
      const A = N[i];
      for (let j = i + 1; j < N.length; j++) {
        const B = N[j];
        let dx = A.x - B.x, dy = A.y - B.y;
        let d2 = dx * dx + dy * dy; if (d2 < 1) { d2 = 1; dx = 1; }
        if (d2 > 90000) continue;
        const f = 2400 / d2 * a;
        const d = Math.sqrt(d2), ux = dx / d, uy = dy / d;
        A.vx += ux * f; A.vy += uy * f; B.vx -= ux * f; B.vy -= uy * f;
      }
      A.vx += -A.x * 0.003 * a; A.vy += -A.y * 0.003 * a;
    }
    for (const e of E) {
      const A = N[e.s], B = N[e.t]; if (!A || !B) continue;
      const dx = B.x - A.x, dy = B.y - A.y;
      const d = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
      const rest = e.rest || (A.r + B.r + 55);
      const f = (d - rest) * 0.02 * a;
      const ux = dx / d, uy = dy / d;
      A.vx += ux * f; A.vy += uy * f; B.vx -= ux * f; B.vy -= uy * f;
    }
    for (const nd of N) {
      if (nd.fx !== undefined) { nd.x = nd.fx; nd.y = nd.fy; nd.vx = 0; nd.vy = 0; continue; }
      nd.vx *= 0.82; nd.vy *= 0.82;
      nd.x += Math.max(-12, Math.min(12, nd.vx));
      nd.y += Math.max(-12, Math.min(12, nd.vy));
    }
    this.alpha *= 0.985;
  }
  _loop() {
    const cv = this.cv, cx = this.cx;
    const dpr = window.devicePixelRatio || 1;
    const w = cv.clientWidth, h = cv.clientHeight;
    if (cv.width !== w * dpr) { cv.width = w * dpr; cv.height = h * dpr; }
    this._step();
    cx.setTransform(dpr, 0, 0, dpr, 0, 0);
    cx.clearRect(0, 0, w, h);
    cx.translate(this.ox, this.oy); cx.scale(this.scale, this.scale);
    for (const e of this.edges) {
      const A = this.nodes[e.s], B = this.nodes[e.t]; if (!A || !B) continue;
      cx.strokeStyle = e.color || "#2d3748"; cx.lineWidth = e.w || 1;
      cx.globalAlpha = e.alpha || 0.75;
      cx.beginPath(); cx.moveTo(A.x, A.y); cx.lineTo(B.x, B.y); cx.stroke();
    }
    cx.globalAlpha = 1;
    const showLabel = this.nodes.length <= 50 || this.scale > 1.3;
    for (const nd of this.nodes) {
      cx.fillStyle = nd.color; cx.globalAlpha = this.hover === nd ? 1 : 0.92;
      cx.beginPath(); cx.arc(nd.x, nd.y, nd.r, 0, Math.PI * 2); cx.fill();
      if (this.hover === nd) { cx.strokeStyle = "#fff"; cx.lineWidth = 2; cx.stroke(); }
      if (showLabel || nd.big) {
        cx.fillStyle = "#e6edf3"; cx.globalAlpha = 0.95;
        cx.font = `${nd.big ? 13 : 11}px -apple-system, "PingFang SC", sans-serif`;
        cx.textAlign = "center";
        cx.fillText(nd.label, nd.x, nd.y + nd.r + 13);
      }
    }
    cx.globalAlpha = 1;
    cx.setTransform(1, 0, 0, 1, 0, 0);
    requestAnimationFrame(() => this._loop());
  }
}

/* ---------- tab: semantic bridge ---------- */
function legend(el, items) {
  $(el).innerHTML = items.map(([swatch, isLine, label]) =>
    `<div class="li">${isLine ? `<span class="ln" style="background:${swatch}"></span>` : `<span class="dot" style="background:${swatch}"></span>`}${label}</div>`).join("");
}
let semGraph;
function initSemantic() {
  const tip = makeTooltip("#tooltip");
  semGraph = new ForceGraph($("#cv-semantic"), {
    onHover: (nd, x, y) => nd ? tip.show(nodeTipHtml(nd), x, y) : tip.hide(),
  });
  const TYPE_COLOR = { EntityClass: "#c678dd", ObjectProperty: "#d19a66", Instance: "#37c988", DatatypeProperty: "#6b7686" };
  const nodes = DATA.semantic.nodes.map((n, i) => ({
    key: n.id, id: n.id, label: n.name, ntype: n.type, desc: n.desc, facts: n.facts,
    color: TYPE_COLOR[n.type] || "#5b8cff",
    r: n.type === "Instance" ? 4.5 : n.type === "ObjectProperty" ? 6 : 8,
  }));
  const idx = new Map(nodes.map((n, i) => [n.key, i]));
  const BRIDGE = new Set(["wieldsElement", "belongsToRegion", "isMemberOf", "triggersReaction", "interactsWith", "rulesOver"]);
  const edges = [];
  for (const [a, rel, b] of DATA.semantic.edges) {
    const s = idx.get(a), t = idx.get(b); if (s === undefined || t === undefined) continue;
    const isBridge = BRIDGE.has(rel);
    edges.push({
      s, t, rest: isBridge ? 150 : 110,
      color: isBridge ? "#d19a66" : rel === "instance_of" ? "#3a4a5a" : "#41506b",
      w: isBridge ? 2.4 : 1, alpha: isBridge ? 0.95 : 0.5,
    });
  }
  semGraph.setData(nodes, edges);
  legend("#lg-semantic", [
    ["#c678dd", false, "语义层类 (genshin#)"], ["#37c988", false, "Avatar 实例 (131)"],
    ["#d19a66", false, "属性节点"], ["#d19a66", true, "桥接关系 (wieldsElement 等)"],
    ["#3a4a5a", true, "instance_of"], ["#41506b", true, "其他结构边"],
  ]);
}

/* ---------- tab: explorer ---------- */
let exGraph, HOPS = 1;
const EX_REL_COLOR = (rel) => {
  if (rel === "subClassOf") return "#41506b";
  if (rel === "instance_of") return "#37c988";
  if (["wieldsElement", "belongsToRegion", "isMemberOf", "triggersReaction", "interactsWith", "rulesOver"].includes(rel)) return "#d19a66";
  return "#5b8cff";
};
function initExplorer() {
  const tip = makeTooltip("#tooltip2");
  exGraph = new ForceGraph($("#cv-explorer"), {
    onHover: (nd, x, y) => nd ? tip.show(nodeTipHtml(nd), x, y) : tip.hide(),
    onClickNode: (nd) => explore(nd.key, nd.label),
  });
  legend("#lg-explorer", [
    ["#5b8cff", false, "实体类"], ["#37c988", false, "实例"], ["#ffb86c", false, "当前种子"],
    ["#5b8cff", true, "关系断言"], ["#41506b", true, "subClassOf"], ["#37c988", true, "instance_of"],
  ]);
  const q = $("#q"), mlist = $("#mlist");
  const doSearch = () => {
    const tokens = q.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    if (!tokens.length) return;
    const hits = DATA.explorer.nodes.filter((n) => {
      const hay = (n.name + "\n" + n.local).toLowerCase();
      return tokens.every((t) => hay.includes(t));
    });
    if (!hits.length) {
      mlist.innerHTML = `<div class="mi">无命中（语料边界，见 USAGE.md）</div>`;
      mlist.style.display = "block"; return;
    }
    mlist.innerHTML = hits.slice(0, 12).map((n) =>
      `<div class="mi" data-key="${esc(n.id)}"><div>${esc(n.name)}</div><div class="mt">${esc(n.type)} · ${esc(n.local)}</div></div>`).join("");
    mlist.style.display = "block";
    mlist.querySelectorAll(".mi").forEach((mi) =>
      mi.addEventListener("click", () => { mlist.style.display = "none"; explore(mi.dataset.key); }));
    if (hits.length === 1 || hits[0].name.length >= 4) { mlist.style.display = "none"; explore(hits[0].id); }
  };
  $("#go").addEventListener("click", doSearch);
  q.addEventListener("keydown", (e) => { if (e.key === "Enter") doSearch(); });
  document.querySelectorAll(".hopctl button").forEach((b) =>
    b.addEventListener("click", () => {
      document.querySelectorAll(".hopctl button").forEach((x) => x.classList.remove("on"));
      b.classList.add("on"); HOPS = +b.dataset.hop;
    }));
}
function explore(seedKey, seedLabel) {
  const g = DATA.explorer;
  const idxOf = new Map(g.nodes.map((n, i) => [n.id, i]));
  const adj = g.adj;
  const si = idxOf.get(seedKey); if (si === undefined) return;
  const keep = new Set([si]);
  let frontier = new Set([si]);
  for (let h = 0; h < HOPS; h++) {
    const next = new Set();
    for (const i of frontier) {
      for (const [j] of adj[i] || []) if (!keep.has(j)) next.add(j);
    }
    for (const j of next) { if (keep.size < 90) keep.add(j); }
    frontier = next;
  }
  const subIdx = [...keep];
  const remap = new Map(subIdx.map((old, ni) => [old, ni]));
  const nodes = subIdx.map((old) => {
    const n = g.nodes[old];
    return { key: n.id, id: n.id, label: n.name, ntype: n.type, desc: n.desc, facts: n.facts,
      color: old === si ? "#ffb86c" : n.type === "Instance" ? "#37c988" : "#5b8cff",
      r: old === si ? 11 : n.type === "Instance" ? 4.5 : 7 };
  });
  const edges = [];
  const seen = new Set();
  for (const old of subIdx) {
    for (const [j, ei] of (adj[old] || [])) {
      if (!keep.has(j)) continue;
      const a = remap.get(old), b = remap.get(j);
      const k1 = a + "|" + b + "|" + ei, k2 = b + "|" + a + "|" + ei;
      if (seen.has(k1) || seen.has(k2)) continue; seen.add(k1);
      const rel = g.rels[ei];
      edges.push({ s: a, t: b, rel, color: EX_REL_COLOR(rel), w: 1.2, alpha: 0.8, rest: 120 });
    }
  }
  exGraph.setData(nodes, edges);
  exGraph.scale = 1; exGraph.ox = 0; exGraph.oy = 0;
  const seedNode = g.nodes[si];
  $("#epanel").style.display = "block";
  $("#ebody").innerHTML = `<div class="erow"><b>种子</b> ${esc(seedNode.name)} <span style="color:var(--muted)">${esc(seedNode.local)}</span></div>` +
    edges.slice(0, 40).map((e) =>
      `<div class="erow">${esc(nodes[e.s].label)} <b>--${esc(e.rel)}--&gt;</b> ${esc(nodes[e.t].label)}</div>`).join("");
}

/* ---------- tab: domain overview ---------- */
let domGraph;
function initDomain() {
  const tip = makeTooltip("#tooltip3");
  domGraph = new ForceGraph($("#cv-domain"), {
    onHover: (nd, x, y) => {
      if (!nd) { tip.hide(); return; }
      const d = DATA.stats.domains.find((x2) => x2.name === nd.label);
      const rels = (d && d.top_rels || []).map(([r, c]) => `${esc(r)} ×${c}`).join(" · ");
      tip.show(`<div class="t-name">${esc(nd.label)}</div>
        <div class="t-id">实体类 ${d ? d.classes : "?"} · 实例 ${d ? d.instances : 0} · 断言边 ${d ? d.relations : 0}</div>
        ${rels ? `<div class="t-desc">高频关系：${rels}</div>` : ""}`, x, y);
    },
  });
  const dm = DATA.stats.domains;
  const nodes = dm.map((d) => ({
    key: "dom:" + d.name, label: d.name, ntype: "Domain", id: "domain",
    desc: `实体类 ${d.classes} · 实例 ${d.instances} · 断言边 ${d.relations}`,
    color: "#ffb86c", r: 12 + Math.min(26, d.classes / 14), big: true,
  }));
  const idx = new Map(nodes.map((n, i) => [n.key, i]));
  const edges = DATA.stats.domain_edges.map((e) => ({
    s: idx.get("dom:" + e.s), t: idx.get("dom:" + e.t),
    color: "#5b8cff", w: Math.min(8, 1 + e.w / 60), alpha: 0.55, rest: 230,
  })).filter((e) => e.s !== undefined && e.t !== undefined);
  domGraph.setData(nodes, edges);
  legend("#lg-domain", [["#ffb86c", false, "域节点（大小 ∝ 实体类数）"], ["#5b8cff", true, "跨域断言关系（宽 ∝ 数量）"]]);
  const tb = $("#dtable");
  tb.innerHTML = `<tr><th>域</th><th style="text-align:right">类</th><th style="text-align:right">实例</th><th style="text-align:right">断言边</th></tr>` +
    dm.map((d) => `<tr><td>${esc(d.name)}</td><td class="num">${fmt(d.classes)}</td><td class="num">${fmt(d.instances)}</td><td class="num">${fmt(d.relations)}</td></tr>`).join("");
}

/* ---------- init ---------- */
renderCards();
renderOverview();
initSemantic();
initExplorer();
initDomain();
