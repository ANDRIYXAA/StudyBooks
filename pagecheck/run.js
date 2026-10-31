"use strict";
/* Runs ONE interactive page in jsdom and uses its controls, then prints what went wrong as one JSON line.
   Started by check.js in a child process (an endless loop in the page must not freeze the checker). Needs no network. */
const fs = require("fs");
const { JSDOM, VirtualConsole } = require("jsdom");

const file = process.argv[2];
const html = fs.readFileSync(file, "utf8");
const lines = html.split("\n");
const errors = [];
const seen = new Set();
let phase = "when it loaded";

/* jsdom counts the line of an error from the start of its <script>: turn that into a line of the file when it is clear which script */
const scripts = [];
{
  const re = /<script\b([^>]*)>([\s\S]*?)<\/script>/gi;
  let m;
  while ((m = re.exec(html))) {
    if (/\bsrc\s*=/i.test(m[1])) continue;
    const start = m.index + m[0].indexOf(">") + 1;
    scripts.push({ off: html.slice(0, start).split("\n").length - 1, n: m[2].split("\n").length });
  }
}
function docLine(line) {
  if (!line) return 0;
  const c = scripts.filter(s => line <= s.n);
  return c.length === 1 ? line + c[0].off : 0;
}
function add(message, line, stack) {
  message = String(message || "error").split("\n")[0].slice(0, 300);
  const key = message.replace(/\d+/g, "N");
  if (seen.has(key) || errors.length >= 8) return;
  seen.add(key);
  const e = { message, phase };
  const fns = [...String(stack || "").matchAll(/\n\s*at (?:async )?([\w$.]+) \(/g)].map(x => x[1]).filter(n => !/^(Object\.<anonymous>|new |Window)|Impl|innerInvoke|callTheUser|invokeTheCallback|_dispatch|dispatchEvent|invokeEventListeners|reportException|^HTML\w+\.(callThe|invoke)/.test(n)).slice(0, 4);
  if (fns.length) e.in = fns.join(" <- ");
  const dl = docLine(line);
  if (dl && lines[dl - 1]) e.line = dl, e.source = lines[dl - 1].trim().slice(0, 160);
  errors.push(e);
}

process.on("unhandledRejection", r => add("unhandled promise rejection: " + (r && (r.stack || r.message) || r)));
process.on("uncaughtException", e => add(e && (e.stack || e.message) || e));

const vc = new VirtualConsole();          // jsdom's own "not implemented" notes and the page's console are not errors of the page
vc.on("jsdomError", () => {});

const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const dom = new JSDOM(html, {
    runScripts: "dangerously", pretendToBeVisual: true, virtualConsole: vc, url: "https://page.invalid/",
    beforeParse(window) {
      window.addEventListener("error", ev => add(ev.error ? (ev.error.name + ": " + ev.error.message) : ev.message, ev.lineno, ev.error && ev.error.stack));
      installStubs(window);
    },
  });
  const { window } = dom;
  const { document } = window;
  await Promise.race([new Promise(r => window.addEventListener("load", r)), sleep(3000)]);
  await sleep(250);                       // timers and animation frames that start the page

  const t0 = Date.now(), budget = 9000;
  const ok = () => Date.now() - t0 < budget && errors.length < 8;
  const fire = (el, type, init) => {
    try { el.dispatchEvent(new window.Event(type, Object.assign({ bubbles: true, cancelable: true }, init))); } catch (e) { add(e); }
  };
  const mouse = (el, type, x, y) => {
    try { el.dispatchEvent(new window.MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0, buttons: type === "mouseup" ? 0 : 1, view: window })); } catch (e) { add(e); }
    try { const ev = new window.Event("pointer" + type.slice(5), { bubbles: true, cancelable: true }); Object.assign(ev, { clientX: x, clientY: y, pointerId: 1, buttons: type === "mouseup" ? 0 : 1, button: 0, pointerType: "mouse", isPrimary: true }); el.dispatchEvent(ev); } catch (e) { add(e); }
  };
  const label = el => el.id ? "#" + el.id : el.tagName.toLowerCase() + (el.name ? `[name=${el.name}]` : "") + (el.type ? `[${el.type}]` : "");
  let steps = 0;

  for (const el of [...document.querySelectorAll("input, select, textarea")]) {
    if (!ok()) break;
    if (el.disabled || el.type === "hidden" || el.type === "file") continue;
    phase = "after using the control " + label(el);
    steps++;
    const tag = el.tagName.toLowerCase(), type = (el.type || "").toLowerCase();
    if (tag === "select") {
      for (const i of [...Array(el.options.length).keys()]) { el.selectedIndex = i; fire(el, "input"); fire(el, "change"); await sleep(5); if (!ok()) break; }
    } else if (type === "range" || type === "number") {
      const min = el.min !== "" ? +el.min : 0, max = el.max !== "" ? +el.max : 100, cur = el.value;
      for (const v of [min, max, (min + max) / 2, cur]) { el.value = String(v); fire(el, "input"); fire(el, "change"); await sleep(5); if (!ok()) break; }
      for (const v of ["", "abc", "-1", "0"]) { if (type === "number") { el.value = v; fire(el, "input"); fire(el, "change"); await sleep(5); } }
    } else if (type === "checkbox" || type === "radio") {
      el.click(); await sleep(5); el.click();
    } else if (type === "color") {
      el.value = "#336699"; fire(el, "input"); fire(el, "change");
    } else if (tag === "textarea" || ["text", "search", "email", "tel", "url", "password", ""].includes(type)) {
      for (const v of ["1", "2*x", "-3.5", "x"]) { el.value = v; fire(el, "input"); fire(el, "change"); await sleep(5); }
    }
  }
  for (const el of [...document.querySelectorAll("button, summary, [role=button], [onclick], a[href^='#']")]) {
    if (!ok()) break;
    if (el.disabled) continue;
    phase = "after pressing " + label(el);
    steps++;
    try { el.click(); } catch (e) { add(e); }
    await sleep(10);
  }
  for (const el of [...document.querySelectorAll("canvas, svg")]) {       // a plot you can drag, click or wheel
    if (!ok()) break;
    phase = "after using the mouse on " + label(el);
    steps++;
    const r = el.getBoundingClientRect(), cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    mouse(el, "mousedown", cx, cy); mouse(el, "mousemove", cx + 20, cy + 10); mouse(el, "mousemove", cx - 30, cy - 15); mouse(el, "mouseup", cx - 30, cy - 15);
    mouse(el, "click", cx, cy);
    try { el.dispatchEvent(new window.WheelEvent("wheel", { bubbles: true, cancelable: true, deltaY: 100, clientX: cx, clientY: cy })); } catch (e) { add(e); }
    await sleep(10);
  }
  phase = "after pressing keys";
  for (const key of ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", " ", "Enter", "r"]) {
    if (!ok()) break;
    for (const type of ["keydown", "keyup"]) {
      try { document.dispatchEvent(new window.KeyboardEvent(type, { key, bubbles: true, cancelable: true })); } catch (e) { add(e); }
    }
  }
  phase = "while it runs";
  await sleep(500);                       // animation loops get a few frames

  const body = document.body ? document.body.cloneNode(true) : null;
  if (body) body.querySelectorAll("script, style, template, noscript").forEach(n => n.remove());       // text you can read, not code
  const text = (body ? body.textContent : "").replace(/\s+/g, " ").trim();
  if (text.length < 15 && !document.querySelector("canvas, svg")) { phase = "after it loaded"; add("the page shows nothing: no text and no drawing"); }
  process.stdout.write(JSON.stringify({ errors, steps }) + "\n");
  process.exit(0);
})().catch(e => { add(e && (e.stack || e.message) || e); process.stdout.write(JSON.stringify({ errors, steps: 0 }) + "\n"); process.exit(0); });

/* What a browser has and jsdom lacks. The page must not fail here for a reason that is only the checker's. */
function installStubs(window) {
  const noop = () => {};
  const ctxBase = {
    measureText: () => ({ width: 8, actualBoundingBoxAscent: 8, actualBoundingBoxDescent: 2, actualBoundingBoxLeft: 0, actualBoundingBoxRight: 8 }),
    getImageData: (x, y, w, h) => ({ data: new window.Uint8ClampedArray(Math.max(4, (w | 0) * (h | 0) * 4)), width: w | 0, height: h | 0 }),
    createImageData: (w, h) => ({ data: new window.Uint8ClampedArray(Math.max(4, (w | 0) * (h | 0) * 4)), width: w | 0, height: h | 0 }),
    getParameter: () => 0, getExtension: () => null, getShaderParameter: () => true, getProgramParameter: () => true,
    getShaderInfoLog: () => "", getProgramInfoLog: () => "", getError: () => 0, isContextLost: () => false,
    getContextAttributes: () => ({}), getSupportedExtensions: () => [], getLineDash: () => [],
    isPointInPath: () => false, isPointInStroke: () => false,
  };
  const makeCtx = canvas => {
    const props = { canvas };
    const proxy = new Proxy(function () { return proxy; }, {
      get(t, k) {
        if (k === Symbol.toPrimitive) return () => 0;
        if (k in props) return props[k];
        if (k in ctxBase) return ctxBase[k];
        if (/^[A-Z0-9_]+$/.test(String(k))) return 1;       // WebGL constants
        return (...a) => proxy;
      },
      set(t, k, v) { props[k] = v; return true; },
      apply: () => proxy,
    });
    return proxy;
  };
  window.HTMLCanvasElement.prototype.getContext = function () { return this.__ctx || (this.__ctx = makeCtx(this)); };
  window.HTMLCanvasElement.prototype.toDataURL = () => "data:image/png;base64,iVBORw0KGgo=";
  window.HTMLCanvasElement.prototype.toBlob = function (cb) { setTimeout(() => cb(new window.Blob([""], { type: "image/png" })), 0); };
  window.HTMLCanvasElement.prototype.captureStream = () => ({ getTracks: () => [] });
  window.matchMedia = window.matchMedia || (q => ({ matches: false, media: q, onchange: null, addListener: noop, removeListener: noop, addEventListener: noop, removeEventListener: noop, dispatchEvent: () => false }));
  class Obs { constructor() {} observe() {} unobserve() {} disconnect() {} takeRecords() { return []; } }
  window.ResizeObserver = window.ResizeObserver || Obs;
  window.IntersectionObserver = window.IntersectionObserver || Obs;
  window.scrollTo = noop; window.scroll = noop; window.scrollBy = noop;
  window.Element.prototype.scrollIntoView = noop;
  window.Element.prototype.setPointerCapture = noop; window.Element.prototype.releasePointerCapture = noop;
  window.HTMLElement.prototype.focus = window.HTMLElement.prototype.focus || noop;
  window.URL.createObjectURL = window.URL.createObjectURL || (() => "blob:stub");
  window.URL.revokeObjectURL = window.URL.revokeObjectURL || noop;
  const rect = () => ({ left: 0, top: 0, right: 600, bottom: 300, x: 0, y: 0, width: 600, height: 300 });
  window.Element.prototype.getBoundingClientRect = rect;
  for (const [name, v] of [["clientWidth", 600], ["clientHeight", 300], ["offsetWidth", 600], ["offsetHeight", 300], ["scrollWidth", 600], ["scrollHeight", 300]]) {
    Object.defineProperty(window.Element.prototype, name, { configurable: true, get() { return v; } });
  }
  const svg = window.SVGElement && window.SVGElement.prototype;
  if (svg) {
    svg.getBBox = () => ({ x: 0, y: 0, width: 100, height: 20 });
    svg.getComputedTextLength = () => 50;
    svg.getTotalLength = () => 100;
    svg.getPointAtLength = () => ({ x: 0, y: 0 });
    svg.getScreenCTM = () => ({ a: 1, b: 0, c: 0, d: 1, e: 0, f: 0, inverse() { return this; } });
    svg.createSVGPoint = () => ({ x: 0, y: 0, matrixTransform() { return { x: 0, y: 0 }; } });
  }
  window.SVGSVGElement && (window.SVGSVGElement.prototype.createSVGPoint = () => ({ x: 0, y: 0, matrixTransform() { return { x: 0, y: 0 }; } }));
  class Path2D { addPath() {} closePath() {} moveTo() {} lineTo() {} bezierCurveTo() {} quadraticCurveTo() {} arc() {} arcTo() {} ellipse() {} rect() {} }
  window.Path2D = window.Path2D || Path2D;
  window.OffscreenCanvas = window.OffscreenCanvas || class { constructor(w, h) { this.width = w; this.height = h; } getContext() { return makeCtx(this); } };
  window.ImageData = window.ImageData || class { constructor(a, b, c) { const w = typeof a === "number" ? a : b, h = typeof a === "number" ? b : c; this.width = w; this.height = h; this.data = typeof a === "number" ? new window.Uint8ClampedArray(w * h * 4) : a; } };
  const audio = function () { return new Proxy({}, { get: (t, k) => k === "destination" ? {} : k === "currentTime" ? 0 : k === "state" ? "running" : (() => audio()) }); };
  window.AudioContext = window.AudioContext || audio; window.webkitAudioContext = window.AudioContext;
  window.speechSynthesis = window.speechSynthesis || { speak: noop, cancel: noop, getVoices: () => [] };
  window.HTMLMediaElement.prototype.play = () => Promise.resolve(); window.HTMLMediaElement.prototype.pause = noop; window.HTMLMediaElement.prototype.load = noop;
  window.CSS = window.CSS || { supports: () => true, escape: s => s };
  window.DOMMatrix = window.DOMMatrix || class { constructor() { this.a = this.d = 1; this.b = this.c = this.e = this.f = 0; } translate() { return this; } scale() { return this; } rotate() { return this; } multiply() { return this; } inverse() { return this; } };
  window.PointerEvent = window.PointerEvent || window.MouseEvent;
  window.TouchEvent = window.TouchEvent || window.Event;
  window.devicePixelRatio = 1;
  window.getSelection = window.getSelection || (() => ({ removeAllRanges: noop, addRange: noop, toString: () => "" }));
}
