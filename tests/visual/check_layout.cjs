#!/usr/bin/env node
/*
 * Visual layout check for the dashboard's landing page, in a real browser.
 *
 * Replaces playwright_validate.js / .ps1, which pointed at a LAN address,
 * opened a headed window, used a selector Playwright does not support and
 * wrote screenshots into the repository root.
 *
 * What it checks, at each viewport, in a fresh browser context (no stored
 * sidebar state -- Streamlit remembers it in localStorage, and a reused profile
 * shows a phone the desktop's open sidebar):
 *
 *   page     no horizontal overflow; the hero's first line is not covered by
 *            the sidebar's reopen control; a collapsed sidebar reserves no
 *            width (the 214px strip a tablet used to get);
 *   scene    the "Apresentação ao Vivo" frame is visible and as tall as its
 *            content -- sampled for a full scenario and more, because the bug
 *            this exists for was intermittent: the frame fitted at most moments
 *            and overflowed when the verdict box opened; its height does not
 *            change while the loop runs; the animation advances (nodes change
 *            state and the scenario title changes); no two node cards overlap;
 *            nothing is drawn past the frame's right edge; and no visible text
 *            is microscopic, measured as rendered size, SVG scale included.
 *
 * Usage:
 *   npm run visual                       # against http://localhost:8501
 *   node tests/visual/check_layout.cjs http://127.0.0.1:8501
 *
 * Exit status is non-zero when any check fails. Screenshots and a JSON report
 * go to tests/visual/artifacts/, which is git-ignored.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const BASE = process.argv[2] || process.env.DASHBOARD_URL || "http://localhost:8501";
const OUT = path.join(__dirname, "artifacts");
const VIEWPORTS = [
  { name: "phone-375", width: 375, height: 812, mobile: true },
  { name: "phone-390", width: 390, height: 844, mobile: true },
  { name: "phone-430", width: 430, height: 932, mobile: true },
  { name: "tablet-768", width: 768, height: 1024, mobile: true },
  { name: "tablet-1024", width: 1024, height: 768, mobile: false },
  { name: "desktop-1440", width: 1440, height: 900, mobile: false },
];
// Rendered text below this is a failure. Phones and tablets read the HTML
// flow, which has no scaling; the SVG diagram is only drawn where the frame
// is at least 820px wide, and its secondary labels are allowed to be smaller.
const MIN_FONT_FLOW = 11;
const MIN_FONT_SVG = 9;
const SAMPLES = 64;          // x 250ms = 16s: more than one whole scenario
const SAMPLE_MS = 250;

function sceneFrame(page) {
  return page.$$eval("iframe", (frames) =>
    frames.findIndex((f) => (f.srcdoc || "").includes("__SCENE__"))
  );
}

async function inspect(page, vp) {
  const failures = [];
  const fail = (msg) => failures.push(msg);

  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await page.waitForSelector("h1", { timeout: 60000 });
  await page.waitForFunction(
    () => [...document.querySelectorAll("iframe")].some(
      (f) => (f.srcdoc || "").includes("__SCENE__") &&
             f.contentDocument && f.contentDocument.getElementById("rows")
    ),
    null,
    { timeout: 60000 }
  );
  await page.waitForTimeout(1500);

  // ------------------------------------------------------------------ page
  const pageFacts = await page.evaluate(() => {
    const rect = (el) => (el ? el.getBoundingClientRect().toJSON() : null);
    const sidebar = document.querySelector('section[data-testid="stSidebar"]');
    const expand = document.querySelector('[data-testid="stExpandSidebarButton"]');
    const main = document.querySelector('[data-testid="stMain"]');
    return {
      vw: window.innerWidth,
      docScrollW: document.documentElement.scrollWidth,
      mainScrollW: main ? main.scrollWidth : 0,
      mainClientW: main ? main.clientWidth : 0,
      sidebarExpanded: sidebar ? sidebar.getAttribute("aria-expanded") : null,
      sidebarW: sidebar ? sidebar.getBoundingClientRect().width : 0,
      mainLeft: main ? main.getBoundingClientRect().left : 0,
      expand: rect(expand),
      eyebrow: rect(document.querySelector(".ap-eyebrow")),
      h1: rect(document.querySelector("h1")),
    };
  });
  if (pageFacts.docScrollW > pageFacts.vw + 1) {
    fail(`page scrolls sideways: ${pageFacts.docScrollW} > ${pageFacts.vw}`);
  }
  if (pageFacts.mainScrollW > pageFacts.mainClientW + 1) {
    fail(`main column scrolls sideways: ${pageFacts.mainScrollW} > ${pageFacts.mainClientW}`);
  }
  const overlaps = (a, b) => a && b &&
    a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
  if (pageFacts.expand && pageFacts.expand.width > 0 &&
      overlaps(pageFacts.expand, pageFacts.eyebrow)) {
    fail("the sidebar's reopen control covers the hero eyebrow");
  }
  if (pageFacts.h1 && (pageFacts.h1.left < 0 || pageFacts.h1.right > pageFacts.vw + 1)) {
    fail("the hero headline leaves the viewport");
  }
  if (pageFacts.sidebarExpanded === "false" && pageFacts.sidebarW > 1) {
    fail(`a collapsed sidebar still takes ${pageFacts.sidebarW}px`);
  }

  // ----------------------------------------------------------------- scene
  const frameIndex = await sceneFrame(page);
  const handle = (await page.$$("iframe"))[frameIndex];
  await handle.scrollIntoViewIfNeeded();
  const frame = await handle.contentFrame();

  const samples = [];
  for (let i = 0; i < SAMPLES; i++) {
    const outer = await handle.boundingBox();
    const inner = await frame.evaluate(() => {
      const d = document.documentElement;
      const lit = [...document.querySelectorAll(".node")]
        .filter((n) => n.offsetParent !== null || n.closest("svg"))
        .filter((n) => /is-(running|success|blocked|waiting)/.test(n.className))
        .map((n) => n.id)
        .sort()
        .join(",");
      const title = document.querySelector("#nowName .on");
      return {
        contentH: Math.max(d.scrollHeight, document.body.scrollHeight),
        contentW: d.scrollWidth,
        clientW: d.clientWidth,
        lit,
        title: title ? title.textContent : "",
      };
    });
    samples.push({ frameH: outer.height, frameW: outer.width, ...inner });
    await page.waitForTimeout(SAMPLE_MS);
  }

  const heights = samples.map((s) => s.frameH);
  const over = samples.filter((s) => s.contentH > s.frameH + 2);
  if (over.length) {
    fail(`scene content taller than its frame in ${over.length}/${SAMPLES} samples ` +
         `(worst ${Math.max(...over.map((s) => s.contentH - s.frameH)).toFixed(0)}px)`);
  }
  if (Math.max(...heights) - Math.min(...heights) > 2) {
    fail(`scene frame height moved during the loop: ${Math.min(...heights)}-${Math.max(...heights)}`);
  }
  if (samples.some((s) => s.contentW > s.clientW + 1)) {
    fail("scene content wider than its frame");
  }
  if (new Set(samples.map((s) => s.lit)).size < 3) {
    fail("the animation did not advance (node states never changed)");
  }
  if (new Set(samples.map((s) => s.title)).size < 2) {
    fail("the scenario never changed during sampling");
  }

  const layout = await frame.evaluate(({ minFlow, minSvg }) => {
    const svg = document.querySelector("svg.scene");
    const svgShown = svg && getComputedStyle(svg).display !== "none";
    const scale = svgShown ? svg.getBoundingClientRect().width / 900 : 1;
    const frameW = document.documentElement.clientWidth;

    const visible = (el) => {
      const cs = getComputedStyle(el);
      if (cs.display === "none" || cs.visibility === "hidden") return false;
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    };
    // Text that a reader is meant to read: every element with its own text.
    const smallest = [];
    for (const el of document.querySelectorAll("body *")) {
      if (!visible(el)) continue;
      const own = [...el.childNodes].some(
        (n) => n.nodeType === 3 && n.textContent.trim().length > 1
      );
      if (!own) continue;
      const inSvg = !!el.closest("svg");
      const px = parseFloat(getComputedStyle(el).fontSize) * (inSvg ? scale : 1);
      const limit = inSvg ? minSvg : minFlow;
      if (px + 0.05 < limit) {
        smallest.push(`${el.tagName.toLowerCase()}.${el.className}: ` +
                      `${px.toFixed(1)}px "${el.textContent.trim().slice(0, 30)}"`);
      }
    }

    const cards = [...document.querySelectorAll(svgShown ? "svg .node" : ".flow .node")]
      .filter(visible)
      .map((n) => ({ id: n.id, r: n.getBoundingClientRect() }));
    const clashes = [];
    for (let i = 0; i < cards.length; i++) {
      for (let j = i + 1; j < cards.length; j++) {
        const a = cards[i].r, b = cards[j].r;
        const w = Math.min(a.right, b.right) - Math.max(a.left, b.left);
        const h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
        if (w > 1 && h > 1) clashes.push(`${cards[i].id} x ${cards[j].id}`);
      }
    }
    const outside = cards.filter((c) => c.r.right > frameW + 1 || c.r.left < -1)
      .map((c) => c.id);

    let minFont = Infinity;
    for (const el of document.querySelectorAll(svgShown ? "svg .node b" : ".flow .node b")) {
      const px = parseFloat(getComputedStyle(el).fontSize) * (svgShown ? scale : 1);
      minFont = Math.min(minFont, px);
    }
    return {
      mode: svgShown ? "diagram" : "flow",
      scale: Number(scale.toFixed(3)),
      nodeNameMinPx: Number(minFont.toFixed(1)),
      tooSmall: smallest.slice(0, 12),
      tooSmallCount: smallest.length,
      clashes,
      outside,
    };
  }, { minFlow: MIN_FONT_FLOW, minSvg: MIN_FONT_SVG });

  if (layout.tooSmallCount) {
    fail(`${layout.tooSmallCount} text element(s) below the minimum: ` +
         layout.tooSmall.join("; "));
  }
  if (layout.clashes.length) fail(`overlapping nodes: ${layout.clashes.join(", ")}`);
  if (layout.outside.length) fail(`nodes past the frame edge: ${layout.outside.join(", ")}`);

  // Screenshots. Streamlit scrolls an inner element, not the window, and an
  // element screenshot leaves the off-screen part of an iframe unpainted -- so
  // the scene is captured as the viewer sees it: one screen at a time.
  await page.evaluate(() => {
    document.querySelector('[data-testid="stMain"]').scrollTop = 0;
  });
  await page.waitForTimeout(300);
  await page.screenshot({ path: path.join(OUT, `${vp.name}-top.png`) });
  const box = await page.evaluate(() => {
    const main = document.querySelector('[data-testid="stMain"]');
    const f = [...document.querySelectorAll("iframe")].find(
      (x) => (x.srcdoc || "").includes("__SCENE__")
    );
    const r = f.getBoundingClientRect();
    return { top: r.top + main.scrollTop, height: r.height };
  });
  const step = Math.floor(vp.height * 0.85);
  for (let y = 0, part = 1; y < box.height; y += step, part++) {
    await page.evaluate((to) => {
      document.querySelector('[data-testid="stMain"]').scrollTop = to;
    }, box.top + y - 50);
    await page.waitForTimeout(350);
    await page.screenshot({ path: path.join(OUT, `${vp.name}-scene-${part}.png`) });
  }

  // While the sidebar is closed its reopen control floats in a fixed header;
  // that header must be opaque, or the page scrolls under the button.
  const header = await page.evaluate(() => {
    const h = document.querySelector('[data-testid="stHeader"]');
    return h ? getComputedStyle(h).backgroundColor : "";
  });
  if (pageFacts.sidebarExpanded === "false" &&
      /rgba\(0, 0, 0, 0\)|transparent/.test(header)) {
    fail("the header is transparent while the sidebar's reopen control floats in it");
  }

  return {
    viewport: vp.name,
    sidebar: { expanded: pageFacts.sidebarExpanded, width: pageFacts.sidebarW },
    scene: {
      mode: layout.mode,
      scale: layout.scale,
      nodeNameMinPx: layout.nodeNameMinPx,
      frameHeight: { min: Math.min(...heights), max: Math.max(...heights) },
      contentHeightMax: Math.max(...samples.map((s) => s.contentH)),
      scenariosSeen: [...new Set(samples.map((s) => s.title))],
    },
    failures,
  };
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const results = [];
  try {
    for (const vp of VIEWPORTS) {
      const context = await browser.newContext({
        viewport: { width: vp.width, height: vp.height },
        deviceScaleFactor: vp.mobile ? 2 : 1,
        isMobile: vp.mobile && vp.width < 768,
        hasTouch: vp.mobile,
      });
      const page = await context.newPage();
      const errors = [];
      page.on("pageerror", (e) => errors.push(String(e)));
      let result;
      try {
        result = await inspect(page, vp);
      } catch (e) {
        result = { viewport: vp.name, failures: [`check crashed: ${e.message}`] };
      }
      if (errors.length) result.failures.push(`page errors: ${errors.join(" | ")}`);
      results.push(result);
      const mark = result.failures.length ? "FAIL" : "ok  ";
      console.log(`${mark} ${vp.name.padEnd(13)} ` +
        (result.scene ? `${result.scene.mode} scale=${result.scene.scale} ` +
          `names>=${result.scene.nodeNameMinPx}px frame=${result.scene.frameHeight.min}` +
          `-${result.scene.frameHeight.max} scenarios=${result.scene.scenariosSeen.length}` : ""));
      for (const f of result.failures) console.log(`       - ${f}`);
      await context.close();
    }
  } finally {
    await browser.close();
  }
  fs.writeFileSync(path.join(OUT, "report.json"), JSON.stringify(results, null, 2));
  const failed = results.filter((r) => r.failures.length).length;
  console.log(`\n${results.length - failed}/${results.length} viewports passed`);
  process.exit(failed ? 1 : 0);
})();
