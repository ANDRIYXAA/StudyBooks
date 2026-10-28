// Checks the language table of web/index.html: 3 columns per row, no duplicate keys, no Cyrillic in the English column,
// and every t("…") / data-i18n key / pl(n, "…") used by the page exists.   Run: node tests/check_page_dict.js [path]
const fs = require("fs");
const path = process.argv[2] || require("path").join(__dirname, "..", "web", "index.html");
const html = fs.readFileSync(path, "utf8");
const a = html.indexOf("/*D-START*/"), b = html.indexOf("/*D-END*/");
if (a < 0 || b < 0) { console.log("FAIL no D table found"); process.exit(1); }
const D = new Function(html.slice(a, b).replace("/*D-START*/", "") + "; return D;")();
const bad = [], CYR = /[А-Яа-яІіЇїЄєҐґЁё]/;
const seen = new Set();
D.forEach((row, i) => {
  if (!Array.isArray(row) || row.length !== 3) return bad.push(`row ${i}: not 3 columns`);
  const [k, uk, en] = row;
  if (seen.has(k)) bad.push(`duplicate key ${k}`);
  seen.add(k);
  const text = v => typeof v === "function" ? v.toString() : Array.isArray(v) ? v.join(" ") : String(v);
  if (!text(uk).trim()) bad.push(`${k}: empty Ukrainian`);
  if (!text(en).trim()) bad.push(`${k}: empty English`);
  if (CYR.test(text(en))) bad.push(`${k}: Cyrillic in the English column`);
  if (typeof uk !== typeof en) bad.push(`${k}: Ukrainian and English differ in kind (text / function / list)`);
});
const body = html.slice(0, a) + html.slice(b);
const used = new Set();
for (const m of html.matchAll(/\bt\(\s*"([A-Za-z0-9_.]+)"\s*[,)]/g)) used.add(m[1]);
for (const m of html.matchAll(/"((?:auth|acc)\.[A-Za-z0-9_.]+)"/g)) used.add(m[1]);      // keys picked by a ternary, t(x ? "a" : "b")
for (const m of html.matchAll(/data-i18n(?:-html|-ph|-label)?="([^"]+)"/g)) used.add(m[1]);
for (const m of html.matchAll(/\bpl\([^,()]+,\s*"([A-Za-z0-9_.]+)"\)/g)) used.add(m[1]);
for (const k of used) if (!seen.has(k)) bad.push(`used but missing: ${k}`);
const dyn = [...html.matchAll(/"([A-Za-z0-9_.]+\.)"\s*\+/g)].map(m => m[1]);
const unused = [...seen].filter(k => !used.has(k) && !dyn.some(p => k.startsWith(p)) && !/^(act|sug\.s|adm\.s|free\.src)\./.test(k));
console.log(`${D.length} rows, ${used.size} keys used, dynamic prefixes: ${dyn.join(" ") || "-"}`);
if (unused.length) console.log("unused (information only): " + unused.join(", "));
if (bad.length) { console.log("FAIL\n" + bad.join("\n")); process.exit(1); }
console.log("OK");
