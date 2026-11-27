"use strict";
// 任意作図: 高度（気圧面・海抜高度・地上）と表示範囲を指定して1枚の図を作る（ローカル専用）
const $ = (id) => document.getElementById(id);
let OPT = null;

const api = async (path, body) => {
  const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : undefined);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.status);
  return j;
};
const fmtInit = (s) => `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)} ${s.slice(8)}UTC`;
const opt = (v, t) => `<option value="${v}">${t}</option>`;

function runInfo() { return OPT.runs.find((r) => r.init === $("init").value); }

function fillModels() {
  const r = runInfo();
  const keep = $("model").value;
  $("model").innerHTML = Object.entries(r.models).map(([k, m]) => opt(k, m.label)).join("");
  if (r.models[keep]) $("model").value = keep;
  fillSteps();
}
function fillSteps() {
  const m = runInfo().models[$("model").value];
  const keep = $("ft").value;
  $("ft").innerHTML = m.steps.map((s) => opt(s, `+${String(s).padStart(2, "0")}h`)).join("");
  if (m.steps.map(String).includes(keep)) $("ft").value = keep; else $("ft").value = m.steps.includes(12) ? 12 : m.steps[0];
  updateUnit();
}
function fillVars() {
  const sfc = $("unit").value === "sfc";
  const vars = sfc ? OPT.sfc_vars : OPT.vars;
  const withNone = (cur) => opt("", "なし") + Object.entries(vars).map(([k, v]) => opt(k, `${v.label} [${v.unit}]`)).join("");
  const keepF = $("fill").value, keepC = $("contour").value;
  $("fill").innerHTML = withNone();
  $("contour").innerHTML = withNone();
  const defF = sfc ? "t2" : "t", defC = sfc ? "msl" : "gh";
  $("fill").value = vars[keepF] ? keepF : defF;
  $("contour").value = vars[keepC] ? keepC : defC;
}
function updateUnit() {
  const u = $("unit").value, model = $("model").value;
  $("levelRow").hidden = u === "sfc";
  const q = $("quick");
  const lv = u === "m" ? [100, 500, 1000, 1500, 2000, 3000, 5000] : [1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100];
  q.innerHTML = u === "sfc" ? "" : lv.map((v) => `<button type="button" data-v="${v}">${v}</button>`).join("");
  q.querySelectorAll("button").forEach((b) => (b.onclick = () => { $("level").value = b.dataset.v; }));
  if (u === "m" && model !== "wrf") $("levelHelp").textContent = "海抜高度はWRFだけで作れます（モデルをWRFにしてください）。";
  else if (u === "hPa") $("levelHelp").textContent = model === "wrf" ? "WRFのモデル面から任意の気圧面へ補間します（50〜1000hPa）。" : "提供される13面（1000〜50hPa）の間は log p で線形補間します（図に注記が出ます）。";
  else if (u === "m") $("levelHelp").textContent = "モデル面の高さで補間します。地面がその高さより高い所は空白になります。";
  else $("levelHelp").textContent = "2m気温・海面気圧・10m風・降水強度・雲量など。";
  if (u === "m" && $("level").value < 50) $("level").value = 1500;
  fillVars();
}
function setBBox(b) { [$("lon0").value, $("lon1").value, $("lat0").value, $("lat1").value] = b; }
function params() {
  const u = $("unit").value;
  return {
    init: $("init").value, model: $("model").value, ft: +$("ft").value,
    unit: u === "sfc" ? "hPa" : u, level: u === "sfc" ? "sfc" : $("level").value,
    fill: $("fill").value, fill_step: $("fill_step").value, cmap: $("cmap").value,
    contour: $("contour").value, interval: $("interval").value, wind: $("wind").value,
    bbox: [$("lon0").value, $("lon1").value, $("lat0").value, $("lat1").value].map(Number), basemap: $("basemap").value,
  };
}
function restore(p) {
  if (!OPT.runs.find((r) => r.init === p.init)) return;
  $("init").value = p.init; fillModels();
  if (runInfo().models[p.model]) { $("model").value = p.model; fillSteps(); }
  $("ft").value = p.ft;
  $("unit").value = p.level === "sfc" ? "sfc" : p.unit; updateUnit();
  if (p.level !== "sfc") $("level").value = p.level;
  $("fill").value = p.fill || ""; $("contour").value = p.contour || ""; $("cmap").value = p.cmap || "";
  $("fill_step").value = p.fill_step || ""; $("interval").value = p.interval || ""; $("wind").value = p.wind || "none";
  setBBox(p.bbox); $("basemap").value = p.basemap || "pale";
}

async function go() {
  const st = $("status"), btn = $("go");
  st.className = ""; st.textContent = "作図中…（初回はデータの読み込みで10秒ほどかかります）"; btn.disabled = true;
  const t0 = performance.now();
  try {
    const p = params();
    const r = await api("/api/custom", p);
    $("result").innerHTML = `<img alt="図" src="${r.url}?t=${Date.now()}">`;
    $("note").textContent = r.note || "";
    $("save").hidden = false; $("save").href = r.url; $("save").download = r.url.split("/").pop();
    st.textContent = `完了（${((performance.now() - t0) / 1000).toFixed(1)}秒）`;
    loadHistory();
  } catch (e) { st.className = "err"; st.textContent = e.message; }
  btn.disabled = false;
}
async function loadHistory() {
  const h = (await api("/api/custom_history")).items;
  $("hist").innerHTML = h.map((it, i) => {
    const p = it.params;
    const lv = p.level === "sfc" ? "地上" : p.unit === "m" ? p.level + "m" : p.level + "hPa";
    return `<figure data-i="${i}"><img loading="lazy" src="${it.url}"><figcaption>${p.model} +${p.ft}h ${lv}</figcaption></figure>`;
  }).join("");
  $("hist").querySelectorAll("figure").forEach((f) => (f.onclick = () => {
    const it = h[+f.dataset.i]; restore(it.params);
    $("result").innerHTML = `<img alt="図" src="${it.url}">`; $("note").textContent = it.note || "";
    $("save").hidden = false; $("save").href = it.url;
  }));
}

async function main() {
  OPT = await api("/api/custom_options");
  if (!OPT.runs.length) { $("status").textContent = "計算済みのデータがありません。先に天気図ビューアで「計算を実行」してください。"; $("status").className = "err"; return; }
  $("init").innerHTML = OPT.runs.map((r) => opt(r.init, fmtInit(r.init))).join("");
  $("preset").innerHTML = opt("", "（選択）") + OPT.presets.map((p, i) => opt(i, p.name)).join("");
  $("basemap").innerHTML = Object.entries(OPT.basemaps).map(([k, v]) => opt(k, v)).join("");
  $("basemap").value = "pale";
  $("init").onchange = fillModels; $("model").onchange = () => { fillSteps(); };
  $("unit").onchange = updateUnit;
  $("preset").onchange = () => { if ($("preset").value !== "") setBBox(OPT.presets[+$("preset").value].bbox); };
  setBBox([118, 138, 20, 34]);
  fillModels();
  $("go").onclick = go;
  addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target.tagName !== "BUTTON" && e.target.tagName !== "SELECT") go(); });
  loadHistory();
}
main().catch((e) => { $("status").className = "err"; $("status").textContent = e.message; });
