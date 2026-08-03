/* gitgraph frontend — 3D graph over the Neo4j commit graph. */
"use strict";

const $ = (id) => document.getElementById(id);
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

/* ---------------- 3D graph ---------------- */

const Graph = ForceGraph3D()(document.getElementById("cy"))
  .backgroundColor("#12121a")
  .nodeRelSize(6)
  .nodeVal((n) => (n.merged ? 1.6 : 1))
  .nodeColor((n) => n.color)
  .nodeLabel((n) =>
    `<b>${esc(n.msg)}</b><br>${esc(n.author)} · ${shortDate(n.date)}<br>` +
    `+${n.add ?? "?"} −${n.del ?? "?"} · ${n.files ?? "?"} files` +
    (n.merged ? " · MERGE" : "") + (n.branch ? ` · ${esc(n.branch)}` : "") +
    `<br>${n.id.slice(0, 12)}…`)
  .linkColor(() => "#4a4a5c")
  .linkWidth(1.2)
  .linkDirectionalParticles(2)
  .linkDirectionalParticleWidth(2)
  .linkDirectionalParticleColor((l) => (l.source && l.source.color) || "#7ab8ff")
  .linkLabel((l) =>
    `<i>${esc(l.source.msg)}</i><br>${esc(l.source.author)} · ${shortDate(l.source.date)}`)
  .onNodeClick((n) => showPanel(n.id))
  .onNodeHover((n) => {
    document.body.style.cursor = n ? "pointer" : "default";
  })
  .nodeThreeObject((n) => {
    const THREE = window.THREE;
    const group = new THREE.Group();
    const sphere = new THREE.Mesh(
      new THREE.SphereGeometry(n.merged ? 4.5 : 3.4, 16, 16),
      new THREE.MeshLambertMaterial({ color: n.color }));
    group.add(sphere);
    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d");
    const text = n.msg.slice(0, 26);
    ctx.font = "600 13px 'DejaVu Sans Mono', monospace";
    const w = Math.max(90, ctx.measureText(text).width + 14);
    canvas.width = w * 2; canvas.height = 44;
    ctx.scale(2, 2);
    ctx.font = "600 13px 'DejaVu Sans Mono', monospace";
    ctx.fillStyle = "rgba(18,18,26,0.88)";
    ctx.fillRect(0, 0, w, 22);
    ctx.strokeStyle = n.color;
    ctx.strokeRect(0.5, 0.5, w - 1, 21);
    ctx.fillStyle = "#e8e8ee";
    ctx.fillText(text, 7, 15);
    const tex = new THREE.CanvasTexture(canvas);
    tex.minFilter = THREE.LinearFilter;
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false }));
    sprite.scale.set(w * 0.015, 22 * 0.015, 1);
    sprite.position.y = 6;
    group.add(sprite);
    return group;
  });

async function loadGraph() {
  const url = `/api/graph?repo=${encodeURIComponent(current.repo)}` +
    (current.branch ? `&branch=${encodeURIComponent(current.branch)}` : "") + "&limit=2500";
  const data = await jget(url);
  const nodes = data.nodes.map((n) => ({
    id: n.id,
    msg: n.msg,
    author: n.author,
    date: n.date,
    branch: n.branch,
    merged: n.merged,
    tags: n.tags,
    add: n.add, del: n.del, files: n.files,
    color: n.branch ? branchColor(n.branch) : "#6a6a7a",
  }));
  const links = data.edges.map((e) => ({ source: e.target, target: e.source }));
  Graph.graphData({ nodes, links });
  Graph.onEngineStop(() => Graph.zoomToFit(400, 60));
}

/* ---------------- commit panel ---------------- */

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
  const n = Graph.graphData().nodes.find((x) => x.id === hash);
  if (n) {
    Graph.cameraPosition({ x: n.x * 1.4, y: n.y * 1.4, z: n.z * 1.4 + 120 }, n, 1500);
  }
  showPanel(hash);
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
