"use strict";
// ネスト実験ビューア: 1つの実験の d01〜d03 を、同じ時刻・同じ図種で並べる。
// 索引（index.json）の experiments と regions だけで作る（通常版と同じデータ・同じ画像を使う）。
const $ = (id) => document.getElementById(id);
const STATIC = !!window.STATIC_SITE;
const COLORS = ["#1a4fd0", "#d05a1a", "#1a9a4a"];
const st = { init: null, exp: null, map: "", chart: "surf_rr1", ft: 0, playing: false, timer: null, big: -1 };
let RUNS = [];
const INDEXES = {};      // 初期時刻 → index.json（実験を持つ初期時刻だけ）
const pad = (n, w = 2) => String(n).padStart(w, "0");
const fmtInit = (s) => `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)} ${s.slice(8)}UTC`;

async function getJson(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(r.status);
  return r.json();
}
function validTime(init, ft) {
  const d = new Date(Date.UTC(+init.slice(0, 4), +init.slice(4, 6) - 1, +init.slice(6, 8), +init.slice(8)) + ft * 3600e3);
  const j = new Date(d.getTime() + 9 * 3600e3);
  return `${pad(d.getUTCMonth() + 1)}/${pad(d.getUTCDate())} ${pad(d.getUTCHours())}UTC（${pad(j.getUTCDate())}日${pad(j.getUTCHours())}時JST）`;
}
const IDX = () => INDEXES[st.init];
const EXP = () => (IDX().experiments || []).find((e) => e.id === st.exp);

// ---------- 領域ごとの表示先（モデル・地域） ----------
function domains() {
  const e = EXP(), ix = IDX();
  return e.cfg.domains.map((d, i) => {
    const n = String(i + 1).padStart(2, "0");
    const model = `x_${e.id}_d${n}`;
    // 専用の地域 r_<実験>_dNN があればそれ、無ければ（対照と同じ領域）WRF領域
    const rr = ix.regions[`r_${e.id}_d${n}`];
    const region = rr && rr.models[model] ? `r_${e.id}_d${n}` : "wrf";
    const hours = (((ix.regions[region] || {}).models || {})[model] || {}).hours || {};
    return { i, n, model, region, hours, dx: d.dx, nw: d.n_we, ns: d.n_sn, center: d.center, start: d.start_h || 0,
             km: [d.dx * (d.n_we - 1) / 1000, d.dx * (d.n_sn - 1) / 1000], color: COLORS[i % 3] };
  });
}
const hoursOf = (dm, chart) => dm.hours[chart] || [];
const imgUrl = (dm, chart, ft) => {
  const common = (IDX().common || []).includes(chart);
  return `${STATIC ? "data" : "/img"}/${st.init}/${common ? "common" : st.map}/${dm.region}/${dm.model}/${chart}/f${pad(ft, 3)}.png`;
};
// 図種ごとの、全領域の時刻の和集合
const allHours = (doms, chart) => [...new Set(doms.flatMap((d) => hoursOf(d, chart)))].sort((a, b) => a - b);

// ---------- 選択肢 ----------
function fillInit() {
  const sel = $("initSel");
  sel.innerHTML = RUNS.filter((r) => INDEXES[r]).map((r) => `<option value="${r}">${fmtInit(r)}</option>`).join("");
  if (!INDEXES[st.init]) st.init = RUNS.find((r) => INDEXES[r]) || null;
  sel.value = st.init || "";
}
function fillExp() {
  const sel = $("expSel"), ex = IDX().experiments || [];
  sel.innerHTML = ex.map((e) => `<option value="${e.id}">${e.label}</option>`).join("");
  if (!ex.some((e) => e.id === st.exp)) st.exp = (ex[0] || {}).id;
  sel.value = st.exp;
}
function fillMap() {
  const maps = IDX().maps || [];
  $("mapSel").innerHTML = maps.map((m) => `<option value="${m.id}">${m.label}</option>`).join("");
  if (!maps.some((m) => m.id === st.map)) st.map = (maps[0] || {}).id || "pale";
  $("mapSel").value = st.map; $("mapLbl").hidden = maps.length < 2;
}
function fillTabs(doms) {
  const box = $("chartTabs"); box.innerHTML = "";
  const charts = IDX().charts.filter((c) => doms.some((d) => hoursOf(d, c.id).length));
  if (!charts.some((c) => c.id === st.chart)) st.chart = (charts.find((c) => c.id === "surf_rr1") || charts[0] || {}).id;
  for (const c of charts) {
    const b = document.createElement("button");
    b.textContent = c.label; b.className = c.id === st.chart ? "on" : "";
    b.onclick = () => { st.chart = c.id; render(); };
    box.appendChild(b);
  }
}

// ---------- 領域の説明（表と概略図） ----------
function drawInfo(doms) {
  const rows = doms.map((d) => `<tr><td><span class="dot" style="background:${d.color}"></span>d${d.n}</td><td>${d.dx / 1000}km</td><td>${d.nw}×${d.ns}</td>` +
    `<td>${Math.round(d.km[0])}×${Math.round(d.km[1])}km</td><td>${d.start ? "+" + d.start + "h" : "初期時刻"}</td></tr>`).join("");
  // 概略図: 中心の緯度経度と大きさ（km）から、親を基準にした等距離の座標へ
  const p = doms[0], lat0 = p.center[0], lon0 = p.center[1], kx = 111.32 * Math.cos(lat0 * Math.PI / 180), ky = 110.57;
  const box = (d) => { const cx = (d.center[1] - lon0) * kx, cy = -(d.center[0] - lat0) * ky; return [cx - d.km[0] / 2, cy - d.km[1] / 2, d.km[0], d.km[1]]; };
  const m = 20, [bx, by, bw, bh] = box(p);
  const rects = doms.map((d) => {
    const [x, y, w, h] = box(d);
    return `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="${d.color}" fill-opacity=".08" stroke="${d.color}" stroke-width="${Math.max(2, bw / 150)}"/>` +
      `<text x="${x + bw / 90}" y="${y + bw / 18}" font-size="${bw / 20}" fill="${d.color}">d${d.n}</text>`;
  }).join("");
  $("nestInfo").innerHTML = `<svg viewBox="${bx - m} ${by - m} ${bw + 2 * m} ${bh + 2 * m}" role="img" aria-label="領域の概略図">${rects}</svg>` +
    `<div><table id="domTable"><tr><th>領域</th><th>格子間隔</th><th>格子数</th><th>大きさ</th><th>開始</th></tr>${rows}</table>` +
    `<p class="note">${EXP().label}　／　予報 ${EXP().cfg.run_hours}時間・${EXP().cfg.history_interval}分ごと出力</p></div>`;
}

// ---------- 表示 ----------
function render() {
  if (!st.init || !st.exp) return;
  const doms = domains();
  fillTabs(doms); drawInfo(doms);
  const hrs = allHours(doms, st.chart);
  if (hrs.length && !hrs.includes(st.ft)) st.ft = hrs.reduce((a, b) => (Math.abs(b - st.ft) < Math.abs(a - st.ft) ? b : a));
  const sl = $("ftSlider"); sl.max = Math.max(0, hrs.length - 1); sl.value = Math.max(0, hrs.indexOf(st.ft)); sl.disabled = hrs.length <= 1;
  $("timeInfo").innerHTML = `初期 <b>${fmtInit(st.init)}</b>　+<b>${pad(st.ft)}h</b>　→　有効 <b>${validTime(st.init, st.ft)}</b>　（${hrs.indexOf(st.ft) + 1}/${hrs.length}）`;
  const box = $("nestViews"); box.innerHTML = "";
  for (const d of doms) {
    const ok = hoursOf(d, st.chart).includes(st.ft);
    const v = document.createElement("div"); v.className = "viewbox" + (st.big === d.i ? " big" : "");
    const why = st.ft < d.start ? `未開始（d${d.n} は +${d.start}h から）` : "この時刻の画像がありません（降水の図は、開始の1時間後から）";
    v.innerHTML = `<div class="cap"><span><span class="dot" style="background:${d.color}"></span><b>d${d.n}</b>　${d.dx / 1000}km　${Math.round(d.km[0])}×${Math.round(d.km[1])}km</span><span>${d.start ? "+" + d.start + "h開始" : ""}</span></div>` +
      (ok ? `<img alt="d${d.n}" src="${imgUrl(d, st.chart, st.ft)}">` : `<div class="nodata">${why}</div>`);
    const img = v.querySelector("img");
    if (img) {
      img.onclick = () => { st.big = st.big === d.i ? -1 : d.i; render(); };
      // 選んだ背景地図で作図していない（画像がない）とき
      img.onerror = () => { const n = document.createElement("div"); n.className = "nodata"; n.textContent = "この背景地図の画像がありません（別の地図を選んでください）"; img.replaceWith(n); };
    }
    box.appendChild(v);
  }
  saveHash();
}
function step(dir) {
  const hrs = allHours(domains(), st.chart); if (!hrs.length) return;
  st.ft = hrs[Math.min(hrs.length - 1, Math.max(0, hrs.indexOf(st.ft) + dir))]; render();
}
function togglePlay() {
  st.playing = !st.playing; $("btnPlay").textContent = st.playing ? "停止" : "再生";
  clearInterval(st.timer);
  if (st.playing) st.timer = setInterval(() => { const h = allHours(domains(), st.chart); st.ft = h[(h.indexOf(st.ft) + 1) % h.length]; render(); }, +$("speedSel").value);
}

// ---------- ハッシュ・初期化 ----------
function saveHash() { history.replaceState(null, "", "#" + new URLSearchParams({ init: st.init, exp: st.exp, map: st.map, chart: st.chart, ft: st.ft })); }
function loadHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  for (const k of ["init", "exp", "map", "chart"]) if (p.get(k)) st[k] = p.get(k);
  if (p.get("ft")) st.ft = +p.get("ft");
}
async function load() {
  loadHash();
  RUNS = STATIC ? (await getJson("data/runs.json")).runs : (await getJson("/api/runs")).runs;
  await Promise.all(RUNS.map(async (r) => {                   // 実験を持つ初期時刻だけ使う
    try { const ix = await getJson(STATIC ? `data/${r}/index.json` : `/api/index/${r}`); if ((ix.experiments || []).length) INDEXES[r] = ix; } catch (e) { /* 読めない初期時刻は飛ばす */ }
  }));
  if (!Object.keys(INDEXES).length) { $("timeInfo").textContent = "ネスト実験の結果がありません。"; return; }
  fillInit(); fillExp(); fillMap(); render();
}
$("initSel").onchange = () => { st.init = $("initSel").value; fillExp(); fillMap(); render(); };
$("expSel").onchange = () => { st.exp = $("expSel").value; st.big = -1; render(); };
$("mapSel").onchange = () => { st.map = $("mapSel").value; render(); };
$("btnPrev").onclick = () => step(-1); $("btnNext").onclick = () => step(1); $("btnPlay").onclick = togglePlay;
$("ftSlider").oninput = () => { st.ft = allHours(domains(), st.chart)[+$("ftSlider").value]; render(); };
document.addEventListener("keydown", (e) => { if (e.key === "ArrowLeft") step(-1); else if (e.key === "ArrowRight") step(1); else if (e.key === " ") { e.preventDefault(); togglePlay(); } });
window.addEventListener("hashchange", () => {            // 共有されたURL（ハッシュ）を貼り替えたとき
  const p = new URLSearchParams(location.hash.slice(1));
  if (p.get("init") === String(st.init) && p.get("exp") === st.exp && p.get("map") === st.map && p.get("chart") === st.chart && +p.get("ft") === st.ft) return;
  loadHash(); if (!INDEXES[st.init]) return; fillInit(); fillExp(); fillMap(); render();
});
load().catch((e) => { $("timeInfo").textContent = "読み込みに失敗しました: " + e.message; });
