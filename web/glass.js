/* The glass of a menu button flows while the pointer (or the keyboard focus) is on it. On leaving, the loop is not cut: it runs on to its
   end, where the glass is at rest. No movement on a phone or any touch screen (a tap would start it and nothing would stop it), and none
   when the person asked for less motion. This file is shared by the book page and the combined page (web.py puts it in both). */
const GLASS_MS = 3600;
const glassStill = () => matchMedia("(prefers-reduced-motion: reduce), (hover: none), (pointer: coarse), (max-width: 700px)").matches;
function glassIn(el) {
  if (glassStill() || !el.animate) return;
  let a = el._ga;
  if (a) { a.playbackRate = 1; a.effect.updateTiming({iterations: Infinity}); return; }
  const cs = getComputedStyle(el), b0 = cs.getPropertyValue("--b0").trim() || "85%", p0 = parseFloat(cs.getPropertyValue("--p0")) || 0, e = "ease-in-out";
  try {
    a = el.animate([{offset: 0, "--b": b0, "--q": b0, "--p": p0 + "px", easing: e}, {offset: .33, "--b": "8%", "--q": "92%", "--p": p0 + 6 + "px", easing: e},
      {offset: .66, "--b": "92%", "--q": "8%", "--p": p0 + 12 + "px", easing: e}, {offset: 1, "--b": b0, "--q": b0, "--p": p0 + "px"}],
      {duration: GLASS_MS, iterations: Infinity});
  } catch (err) { return; }
  a.onfinish = a.oncancel = () => { if (el._ga === a) el._ga = null; };
  el._ga = a;
}
function glassOut(el) {
  const a = el._ga;
  if (!a) return;
  a.effect.updateTiming({iterations: Math.floor((a.currentTime || 0) / GLASS_MS) + 1});     // finish the round that is running
  a.playbackRate = 2;
}
addEventListener("mouseover", e => { const g = e.target.closest?.(".gbtn"); if (g) glassIn(g); });
addEventListener("mouseout", e => { const g = e.target.closest?.(".gbtn"); if (g && !g.contains(e.relatedTarget)) glassOut(g); });
addEventListener("focusin", e => { const g = e.target.closest?.(".gbtn"); if (g) glassIn(g); });
addEventListener("focusout", e => { const g = e.target.closest?.(".gbtn"); if (g && !g.contains(e.relatedTarget)) glassOut(g); });
