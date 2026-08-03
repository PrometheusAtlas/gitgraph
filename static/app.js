/* gitgraph frontend — DAG + force views over the Neo4j commit graph. */
"use strict";

const $ = (id) => document.getElementById(id);
let cy = null;
let current = { repo: "", branch: "" };
const PALETTE = [
  "#5fd0a0", "#7ab8ff", "#f2b06b", "#d98fd9", "#ff8f8f",
  "#8fd9d9", "#e0d27a", "#a0e07a", "#d9a05f", "#9fa8ff",
];
const branchColor = (() => { const m = new Map(); let i = 0;
  return (b) => { if (!m.has(b)) m.set(b, PALETTE[i++ % PALETTE.length]); return m.get(b); }; })();

async function jget(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error((await r.text()).slice(0, 200));
  return r.json();
}
async function jpost(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const t = await r.text();
  if (!r.ok) throw new Error(t.slice(0, 300));
  return JSON.parse(t);
}

/* ---------------- repo / branch loading ---------------- */

async function loadRepos() {
  const repos = await jget("/api/repos");
  const sel = $("repo");
  sel.innerHTML = "";
  for (const r of repos) {
    const o = document.createElement("option");
    o.value = r.name;
    o.textContent = `${r.name}  (${r.commits} commits)`;
    sel.appendChild(o);
  }
  if (repos.length) {
    sel.value = repos[0].name;
    current.repo = repos[0].name;
    loadBranches();
    loadGraph();
  }
}

async function loadBranches() {
  const repos = await jget("/api/repos");
  const r = repos.find((x) => x.name === current.repo);
  const sel = $("branch");
  sel.innerHTML = "";
  const all = document.createElement("option");
  all.value = ""; all.textContent = "all branches";
  sel.appendChild(all);
  for (const b of (r ? r.branches : [])) {
    const o = document.createElement("option");
    o.value = b; o.textContent = b;
    sel.appendChild(o);
  }
}

/* ---------------- layout helpers ---------------- */

function dagLayout(nodes, edges) {
  // y: level = distance from the tip set (parents are deeper)
  const child = new Map();
  for (const e of edges) (child.get(e.target) ?? child.set(e.target, []).get(e.target)).push(e.source);
  const level = new Map();
  const ids = new Set(nodes.map((n) => n.id));
  const tips = nodes.filter((n) => !child.get(n.id)?.length);
  const stack = tips.map((t) => t.id);
  for (const t of stack) level.set(t, 0);
  while (stack.length) {
    const id = stack.pop();
    const l = level.get(id) ?? 0;
    for (const e of edges) {
      if (e.source !== id) continue;
      if (!ids.has(e.target)) continue;
      if (!level.has(e.target) || level.get(e.target) < l + 1) {
        level.set(e.target, l + 1);
        stack.push(e.target);
      }
    }
  }
  const byLevel = new Map();
  for (const n of nodes) {
    const l = level.get(n.id) ?? 0;
    (byLevel.get(l) ?? byLevel.set(l, []).get(l)).push(n);
  }
  const xs = {};
  for (const [l, group] of byLevel) {
    group.sort((a, b) => (a.branch === b.branch ? (a.date < b.date ? 1 : -1) : (a.branch < b.branch ? -1 : 1)));
    group.forEach((n, i) => { xs[n.id] = (i - (group.length - 1) / 2) * 120; });
  }
  return nodes.map((n) => ({ id: n.id, x: xs[n.id], y: -(level.get(n.id) ?? 0) * 70 }));
}

/* ---------------- graph rendering ---------------- */

async function loadGraph() {
  const url = `/api/graph?repo=${encodeURIComponent(current.repo)}` +
    (current.branch ? `&branch=${encodeURIComponent(current.branch)}` : "") + "&limit=2500";
  const data = await jget(url);
  const positions = dagLayout(data.nodes, data.edges);
  const pos = new Map(positions.map((p) => [p.id, p]));
  const nodes = data.nodes.map((n) => ({
    data: {
      id: n.id, msg: n.msg, author: n.author, date: n.date,
      branch: n.branch, merged: n.merged, tags: n.tags,
      add: n.add, del: n.del, files: n.files,
      color: n.branch ? branchColor(n.branch) : "#6a6a7a",
    },
    position: { x: pos.get(n.id)?.x ?? 0, y: pos.get(n.id)?.y ?? 0 },
    classes: `${n.merged ? "merged" : ""} ${n.tags?.length ? "tagged" : ""}`,
  }));
  const edges = data.edges.map((e) => ({
    data: { id: `${e.source}->${e.target}`, source: e.source, target: e.target },
  }));
  const styles = [
    {
      selector: "node",
      style: {
        width: 26, height: 26, "background-color": "data(color)",
        "border-width": 1.5, "border-color": "#0a0a10",
        label: "data(msg)", "font-size": 10, "text-valign": "top",
        "text-margin-y": 6, color: "#9a9aaa", "text-wrap": "ellipsis",
        "text-max-width": 150,
      },
    },
    { selector: "node.merged", style: { shape: "diamond", width: 20, height: 20 } },
    { selector: "node.tagged", style: { shape: "hexagon", "border-width": 3, "border-color": "#e0d27a" } },
    { selector: "node:selected", style: { "border-width": 3, "border-color": "#ffffff" } },
    { selector: "edge", style: { width: 1.6, "line-color": "#4a4a5c", "curve-style": "bezier", "target-arrow-shape": "triangle", "target-arrow-color": "#4a4a5c", "arrow-scale": 0.7 } },
  ];
  if (cy) cy.destroy();
  cy = cytoscape({ container: $("cy"), elements: { nodes, edges }, style: styles, minZoom: 0.2, maxZoom: 3 });
  wireEvents();
  cy.fit(undefined, 40);
}

function wireEvents() {
  const tip = $("tooltip");
  cy.on("mouseover", "node", (ev) => {
    const n = ev.target;
    const d = n.data();
    tip.innerHTML =
      `<div class="tt-msg">${esc(d.msg)}</div>` +
      `<div class="tt-row">${d.author} · ${shortDate(d.date)}</div>` +
      `<div class="tt-row">+${d.add} −${d.del} · ${d.files} files${d.merged ? " · MERGE" : ""}${d.tags?.length ? " · " + d.tags.join(",") : ""}</div>` +
      (d.branch ? `<div class="tt-row" style="color:${d.color}">◆ ${d.branch}</div>` : "") +
      `<div class="tt-row hash">${d.id.slice(0, 12)}…</div>`;
    tip.hidden = false;
  });
  cy.on("mouseover", "edge", (ev) => {
    const src = cy.getElementById(ev.target.data("source")).data();
    tip.innerHTML = `<div class="tt-edge">↑ ${esc(src.msg)}</div>` +
      `<div class="tt-row">${src.author} · ${shortDate(src.date)}</div>` +
      `<div class="tt-row">${src.id.slice(0, 12)}…</div>`;
    tip.hidden = false;
  });
  cy.on("mouseout", "node edge", () => { tip.hidden = true; });
  cy.on("mousemove", (ev) => {
    const r = $("cy").getBoundingClientRect();
    tip.style.left = ev.originalEvent.clientX - r.left + 14 + "px";
    tip.style.top = ev.originalEvent.clientY - r.top + 14 + "px";
  });
  cy.on("tap", "node", (ev) => showPanel(ev.target.id()));
  cy.on("tap", (ev) => { if (ev.target === cy) $("panel").hidden = true; });
}

async function showPanel(hash) {
  const d = await jget(`/api/node/${hash}`);
  $("panel-body").innerHTML = `
    <h2>${esc(d.msg)}</h2>
    <div class="plabel">commit</div><div class="pval hash">${d.hash}</div>
    <div class="plabel">author</div><div class="pval">${esc(d.author_name)} &lt;${esc(d.author_email)}&gt;<br>${d.authored_at}</div>
    <div class="plabel">committed</div><div class="pval">${esc(d.committer_name)}<br>${d.committed_at}</div>
    <div class="plabel">stats</div><div class="pval">+${d.add} −${d.del} · ${d.files_changed} files ${d.merged ? "· merge commit" : ""}</div>
    ${d.branches?.length ? `<div class="plabel">branches</div><div class="pval">${d.branches.map(esc).map((b) => `<span class="chip">${b}</span>`).join("")}</div>` : ""}
    ${d.tags?.length ? `<div class="plabel">tags</div><div class="pval">${d.tags.map(esc).map((t) => `<span class="chip">${t}</span>`).join("")}</div>` : ""}
    ${d.body ? `<div class="plabel">body</div><div class="pval">${esc(d.body)}</div>` : ""}
    <div class="plabel">parents</div><div class="pval">${(d.parents || []).map((p) => `<a class="hash" href="#" data-nav="${p}">${p.slice(0, 12)}…</a><br>`).join("") || "—"}</div>
    <div class="plabel">children</div><div class="pval">${(d.children || []).map((c) => `<a class="hash" href="#" data-nav="${c}">${c.slice(0, 12)}…</a><br>`).join("") || "—"}</div>
    ${d.files?.length ? `<div class="plabel">files changed</div><div class="pval">${d.files.map((f) => `<div class="fpath">${esc(f)}</div>`).join("")}</div>` : ""}`;
  $("panel").hidden = false;
  $("panel-body").querySelectorAll("a[data-nav]").forEach((a) =>
    a.addEventListener("click", (ev) => { ev.preventDefault(); showPanel(a.dataset.nav); }));
}

/* ---------------- search ---------------- */

let searchTimer = null;
$("search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(async () => {
    const q = $("search").value.trim();
    const old = document.querySelector(".search-pop");
    if (old) old.remove();
    if (q.length < 2) return;
    const res = await jget(`/api/search?q=${encodeURIComponent(q)}`);
    const pop = document.createElement("div");
    pop.className = "search-pop";
    const r = $("search").getBoundingClientRect();
    pop.style.left = r.left + "px";
    pop.style.top = r.bottom + 6 + "px";
    for (const s of res.slice(0, 12)) {
      const d = document.createElement("div");
      d.innerHTML = `<div class="s-msg">${esc(s.msg.slice(0, 60))}</div><div class="s-meta">${s.hash.slice(0, 10)}… · ${s.author} · ${shortDate(s.date)}</div>`;
      d.addEventListener("click", () => { pop.remove(); $("search").value = ""; focusNode(s.hash); });
      pop.appendChild(d);
    }
    if (!res.length) pop.innerHTML = `<div style="padding:8px 10px;color:var(--dim)">no matches</div>`;
    document.body.appendChild(pop);
  }, 250);
});
document.addEventListener("click", (e) => {
  if (!e.target.closest(".search-pop") && !e.target.closest("#search")) {
    const pop = document.querySelector(".search-pop");
    if (pop) pop.remove();
  }
});

function focusNode(hash) {
  const n = cy.getElementById(hash);
  if (n.length) { cy.animate({ center: { eles: n }, zoom: 0.9, duration: 400 }); n.animate({ style: { "border-width": 4, "border-color": "#ffffff" }, duration: 1500 }); }
  else showPanel(hash);
}

/* ---------------- views ---------------- */

$("view-dag").addEventListener("click", () => { setView("dag"); });
$("view-force").addEventListener("click", () => { setView("force"); });
function setView(v) {
  $("view-dag").classList.toggle("on", v === "dag");
  $("view-force").classList.toggle("on", v === "force");
  if (v === "dag") loadGraph();
  else cy.layout({ name: "cose", animate: true, nodeRepulsion: 8000, idealEdgeLength: 90 }).run();
}

$("repo").addEventListener("change", () => {
  current.repo = $("repo").value;
  current.branch = "";
  loadBranches();
  loadGraph();
});
$("branch").addEventListener("change", () => {
  current.branch = $("branch").value;
  loadGraph();
});

/* ---------------- queries ---------------- */

async function loadQueries() {
  const qs = await jget("/api/queries");
  const sel = $("namedq");
  sel.innerHTML = "";
  for (const q of qs) {
    const o = document.createElement("option");
    o.value = q.name;
    o.textContent = `${q.name} — ${q.description}`;
    sel.appendChild(o);
  }
  $("runq").addEventListener("click", async () => {
    const name = sel.value;
    const branch = $("branchq").value.trim();
    await runQuery({ name, repo: current.repo, branch: branch || undefined });
  });
  $("runraw").addEventListener("click", async () => {
    const cypher = $("rawq").value.trim();
    if (!cypher) return;
    await runQuery({ cypher, repo: current.repo });
  });
}

async function runQuery(payload) {
  const box = $("results");
  box.hidden = false;
  box.innerHTML = "running…";
  try {
    const rows = await jpost("/api/query", payload);
    if (!rows.length) { box.innerHTML = "<div style='color:var(--dim)'>no rows</div>"; return; }
    const cols = Object.keys(rows[0]);
    let html = "<table><thead><tr>" + cols.map((c) => `<th>${esc(c)}</th>`).join("") + "</tr></thead><tbody>";
    for (const r of rows.slice(0, 100)) {
      html += "<tr>" + cols.map((c) => `<td>${esc(fmt(r[c]))}</td>`).join("") + "</tr>";
    }
    html += "</tbody></table>";
    if (rows.length > 100) html += `<div style='color:var(--dim);margin-top:6px'>…and ${rows.length - 100} more</div>`;
    box.innerHTML = html;
  } catch (e) {
    box.innerHTML = `<div class="err">${esc(e.message)}</div>`;
  }
}

$("statsbtn").addEventListener("click", () => runQuery({ name: "author-stats", repo: current.repo }));

/* ---------------- helpers ---------------- */

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function shortDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleDateString();
}
function fmt(v) {
  if (v === null || v === undefined) return "";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

loadRepos();
loadQueries();
