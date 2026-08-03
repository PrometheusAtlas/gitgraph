#!/usr/bin/env node
/* One-shot demo capture for gitgraph — fresh Chromium per run.
   Usage: node scripts/capture_demo.js [outdir]
   Saves: 01-graph.png 02-hover.png 03-panel.png 04-rotate.png 05-search.png 06-query.png
*/
const path = require("path");
const fs = require("fs");
const { chromium } = require("/home/genesis-t/gstack/node_modules/playwright");

const OUT = process.argv[2] || path.join(__dirname, "..", "demo");
const URL = process.env.GG_URL || "http://127.0.0.1:8001";

const shot = async (page, name) => {
  await page.screenshot({ path: path.join(OUT, name) });
  console.log("captured", name);
};

const projectNode = (nodes, msg) => {
  // project a node to screen coords using camera matrices
  const cam = Graph.camera();
  const m4 = (m, v) => [
    m[0]*v[0]+m[4]*v[1]+m[8]*v[2]+m[12]*v[3],
    m[1]*v[0]+m[5]*v[1]+m[9]*v[2]+m[13]*v[3],
    m[2]*v[0]+m[6]*v[1]+m[10]*v[2]+m[14]*v[3],
    m[3]*v[0]+m[7]*v[1]+m[11]*v[2]+m[15]*v[3],
  ];
  let target = null;
  const walk = (o) => {
    if (o.__graphObjType && o.__data && !target) {
      if (String(o.__data.msg).startsWith(msg)) target = o;
    }
    if (o.children) for (const c of o.children) walk(c);
  };
  walk(Graph.scene());
  if (!target) {
    const walk2 = (o) => {
      if (o.__graphObjType && o.__data && !target) target = o;
      if (o.children) for (const c of o.children) walk2(c);
    };
    walk2(Graph.scene());
  }
  const p = target.position;
  const vp = m4(cam.matrixWorldInverse.elements, [p.x, p.y, p.z, 1]);
  const np = m4(cam.projectionMatrix.elements, vp);
  const ndc = [np[0] / np[3], np[1] / np[3]];
  const cv = document.querySelector("#cy canvas");
  const r = cv.getBoundingClientRect();
  return {
    x: ((ndc[0] + 1) / 2) * cv.clientWidth + r.left,
    y: ((1 - ndc[1]) / 2) * cv.clientHeight + r.top,
    id: String(target.__data.id),
  };
};

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({
    headless: true,
    args: [
      "--enable-unsafe-swiftshader",
      "--use-angle=swiftshader",
      "--ignore-gpu-blocklist",
      "--disable-gpu-sandbox",
    ],
  });
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  page.on("console", (m) => { if (m.type() === "error") console.log("PAGE ERROR:", m.text().slice(0, 160)); });
  page.on("pageerror", (e) => console.log("PAGE EXCEPTION:", String(e).slice(0, 160)));

  await page.goto(URL, { waitUntil: "networkidle" });
  await page.waitForFunction(() => typeof Graph !== "undefined", { timeout: 15000 });
  await page.selectOption("#repo", "mock-repo");
  await page.waitForFunction(
    () => Graph.graphData().nodes.length > 0,
    { timeout: 15000 });
  await page.waitForTimeout(3800);  // fly-in animation + camera framing

  // frame 1: overview
  await shot(page, "01-graph.png");

  // frame 2: aim camera at a node, hover -> tooltip
  const aim = await page.evaluate(() => {
    const g = Graph;
    let target = null;
    const walk = (o) => {
      if (o.__graphObjType && o.__data && !target) {
        if (String(o.__data.msg) === "Merge feat/payments into main") target = o;
      }
      if (o.children) for (const c of o.children) walk(c);
    };
    walk(g.scene());
    if (!target) target = g.scene().children[3].children[0];
    const p = target.position;
    g.cameraPosition({ x: p.x + 130, y: p.y - 70, z: p.z + 130 }, { x: p.x, y: p.y, z: p.z }, 500);
    return true;
  });
  await page.waitForTimeout(1800);
  const hoverPt = await page.evaluate(projectNode, "Merge feat/payments into main");
  await page.mouse.move(hoverPt.x, hoverPt.y);
  await page.waitForTimeout(900);
  await shot(page, "02-hover.png");

  // frame 3: click -> panel
  await page.mouse.click(hoverPt.x, hoverPt.y);
  await page.waitForSelector("#panel:not([hidden])", { timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(800);
  await shot(page, "03-panel.png");

  // frame 4: rotated view
  await page.evaluate(() => {
    document.getElementById("panel").hidden = true;
    Graph.cameraPosition({ x: -360, y: 210, z: 430 }, { x: 0, y: 0, z: 0 }, 700);
  });
  await page.waitForTimeout(1800);
  await shot(page, "04-rotate.png");

  // frame 5: search
  await page.fill("#search", "stripe");
  await page.waitForTimeout(1200);
  await shot(page, "05-search.png");

  // frame 6: query
  await page.evaluate(() => {
    const p = document.querySelector(".search-pop");
    if (p) p.remove();
    document.getElementById("results").hidden = false;
    document.getElementById("rawq").value =
      "MATCH (c:Commit)-[:AUTHORED_BY]->(a:Author) RETURN a.name AS author, count(c) AS commits ORDER BY commits DESC";
    document.getElementById("runraw").click();
  });
  await page.waitForTimeout(1500);
  await shot(page, "06-query.png");

  await browser.close();
  console.log("done");
})().catch((e) => { console.error("CAPTURE FAILED:", e.message); process.exit(1); });
