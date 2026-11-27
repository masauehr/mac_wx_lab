"use strict";
// Mac 数値予報ビューア（WRF・AIFS・IFS）。画面構成は nwp_chart_web に合わせてある。
const $ = (id) => document.getElementById(id);
const STATIC = !!window.STATIC_SITE;   // 公開サイト（閲覧専用・APIなし）
let MODEL_ORDER = ["wrf", "aifs", "aifs_off", "ifs"];     // 実験ケースがあれば、索引の model_order で置き換える
const st = { init: null, region: "wrf", model: "wrf", model2: "aifs", chart: "surf", map: "", ft: 6, cmp: false, playing: false, timer: null };
let INDEX = null;       // 選択中の初期時刻の index.json
let runs = [];

const api = async (path, body) => {
  const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : undefined);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.status);
  return j;
};

const fmtInit = (s) => `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)} ${s.slice(8)}UTC`;
const pad = (n, w = 2) => String(n).padStart(w, "0");
function validTime(init, ft) {
  const d = new Date(Date.UTC(+init.slice(0, 4), +init.slice(4, 6) - 1, +init.slice(6, 8), +init.slice(8)) + ft * 3600e3);
  const j = new Date(d.getTime() + 9 * 3600e3);
  return `${pad(d.getUTCMonth() + 1)}/${pad(d.getUTCDate())} ${pad(d.getUTCHours())}UTC（${pad(j.getUTCDate())}日${pad(j.getUTCHours())}時JST）`;
}

// ---------- ハッシュ（URL）に状態を保存 ----------
function saveHash() {
  const p = new URLSearchParams({ init: st.init || "", map: st.map, region: st.region, model: st.model, chart: st.chart, ft: st.ft });
  if (st.cmp) { p.set("cmp", "1"); p.set("model2", st.model2); }
  history.replaceState(null, "", "#" + p.toString());
}
function loadHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  if (p.get("init")) st.init = p.get("init");
  for (const k of ["region", "model", "chart", "model2", "map"]) if (p.get(k)) st[k] = p.get(k);
  if (p.get("ft")) st.ft = +p.get("ft");
  st.cmp = p.get("cmp") === "1";
}

// ---------- 索引に基づく選択肢 ----------
const regionModels = () => (INDEX && INDEX.regions[st.region] ? INDEX.regions[st.region].models : {});
const hoursOf = (model, chart) => ((regionModels()[model] || {}).hours || {})[chart] || [];
const imgUrl = (model, chart, ft) => {
  const common = (INDEX.common || []).includes(chart);       // 差分・検証の図は背景地図なし（common）
  return `${STATIC ? "data" : "/img"}/${st.init}/${common ? "common" : st.map}/${st.region}/${model}/${chart}/f${pad(ft, 3)}.png`;
};

function fillModelSelects() {
  // モデルの選択肢は、地域によらず、その初期時刻にあるモデル全部。選んだモデルを勝手に変えない
  // （その地域にデータが無いときは、画像なしと表示する）
  const all = MODEL_ORDER.filter((m) => Object.values(INDEX.regions).some((r) => r.models[m]));
  for (const [sel, key] of [[$("modelSel"), "model"], [$("model2Sel"), "model2"]]) {
    sel.innerHTML = all.map((m) => `<option value="${m}">${INDEX.labels[m]}</option>`).join("");
    if (!all.includes(st[key])) st[key] = all[key === "model" ? 0 : Math.min(1, all.length - 1)] || "";
    sel.value = st[key];
  }
}
function fillMaps() {
  const sel = $("mapSel");
  sel.innerHTML = INDEX.maps.map((m) => `<option value="${m.id}">${m.label}</option>`).join("");
  sel.value = st.map; $("mapLbl").hidden = INDEX.maps.length < 2;
}
function fillRegions() {
  const box = $("regionBtns");
  box.innerHTML = "";
  for (const [k, v] of Object.entries(INDEX.regions)) {
    const b = document.createElement("button");
    b.textContent = v.name; b.className = k === st.region ? "on" : "";
    b.onclick = () => { st.region = k; refresh(true); };
    box.appendChild(b);
  }
}
function fillTabs() {
  const box = $("chartTabs");
  box.innerHTML = "";
  for (const c of INDEX.charts) {
    const has = hoursOf(st.model, c.id).length > 0;
    const b = document.createElement("button");
    b.textContent = c.label; b.className = (c.id === st.chart ? "on " : "") + (has ? "" : "dis");
    b.onclick = () => { st.chart = c.id; refresh(); };
    box.appendChild(b);
  }
}

// ---------- 表示 ----------
function setImg(imgId, nodId, capId, model, ft) {
  const img = $(imgId), nod = $(nodId), cap = $(capId);
  const ok = model && hoursOf(model, st.chart).includes(ft);
  cap.hidden = !st.cmp;
  cap.innerHTML = `<span><b>${INDEX.labels[model] || model}</b></span>`;
  if (ok) {
    img.hidden = false; nod.hidden = true; img.src = imgUrl(model, st.chart, ft);
  } else {
    img.hidden = true; nod.hidden = false; nod.textContent = "このモデルには、この図種・予報時間の画像がありません（←→で時刻を動かしてください）";
  }
  return ok;
}

function render() {
  if (!INDEX) return;
  document.body.classList.toggle("cmp", st.cmp);
  $("view2").hidden = !st.cmp; $("cmpBox").hidden = !st.cmp; $("cmpChk").checked = st.cmp;
  const hrs = hoursOf(st.model, st.chart);
  const sl = $("ftSlider");
  if (hrs.length && !hrs.includes(st.ft)) st.ft = hrs.reduce((a, b) => (Math.abs(b - st.ft) < Math.abs(a - st.ft) ? b : a));
  sl.max = Math.max(0, hrs.length - 1); sl.value = Math.max(0, hrs.indexOf(st.ft));
  sl.disabled = hrs.length <= 1;
  setImg("img1", "nod1", "cap1", st.model, st.ft);
  if (st.cmp) setImg("img2", "nod2", "cap2", st.model2, st.ft);
  const verif = st.chart === "verif";
  $("timeInfo").innerHTML = verif ? "<b>IFS を参照にした RMSE</b>（予報時間によらない検証図）"
    : `初期 <b>${fmtInit(st.init)}</b>　+<b>${pad(st.ft)}h</b>　→　有効 <b>${validTime(st.init, st.ft)}</b>　（${hrs.length ? hrs.indexOf(st.ft) + 1 : 0}/${hrs.length}）`;
  $("dlLink").href = hrs.includes(st.ft) ? imgUrl(st.model, st.chart, st.ft) : "#";
  $("dlLink").download = `${st.init}_${st.region}_${st.model}_${st.chart}_f${pad(st.ft, 3)}.png`;
  document.querySelectorAll("#chartTabs button").forEach((b, i) => b.classList.toggle("on", INDEX.charts[i].id === st.chart));
  renderThumbs();
  fitSize(); saveHash();
}

function renderThumbs() {
  const box = $("thumbs");
  box.innerHTML = "";
  if (st.chart === "verif") return;
  for (const c of INDEX.charts) {
    if (c.id === "verif" || !hoursOf(st.model, c.id).includes(st.ft)) continue;
    const f = document.createElement("figure");
    f.className = c.id === st.chart ? "on" : "";
    f.innerHTML = `<img loading="lazy" src="${imgUrl(st.model, c.id, st.ft)}"><figcaption>${c.label}</figcaption>`;
    f.onclick = () => { st.chart = c.id; render(); };
    box.appendChild(f);
  }
}

function fitSize() {
  const v = $("views"), sz = $("sizeSel").value;
  v.classList.toggle("size-fit", sz === "fit");
  v.classList.toggle("size-pct", /^\d+$/.test(sz));
  if (sz === "fit") v.style.setProperty("--avail", Math.max(300, innerHeight - v.getBoundingClientRect().top - 24) + "px");
  if (/^\d+$/.test(sz)) v.style.setProperty("--imgw", sz + "%");
}

function step(d) {
  const hrs = hoursOf(st.model, st.chart);
  if (!hrs.length) return;
  const i = Math.min(hrs.length - 1, Math.max(0, hrs.indexOf(st.ft) + d));
  st.ft = hrs[i]; render();
}
function togglePlay() {
  st.playing = !st.playing;
  $("btnPlay").textContent = st.playing ? "停止" : "再生";
  clearInterval(st.timer);
  if (st.playing) st.timer = setInterval(() => {
    const hrs = hoursOf(st.model, st.chart);
    st.ft = hrs[(hrs.indexOf(st.ft) + 1) % hrs.length]; render();
  }, +$("speedSel").value);
}

// ---------- 初期時刻の読込 ----------
async function loadRuns() {
  runs = STATIC ? (await (await fetch("data/runs.json")).json()).runs : (await api("/api/runs")).runs;
  const sel = $("initSel");
  sel.innerHTML = runs.map((r) => `<option value="${r}">${fmtInit(r)}</option>`).join("") || "<option value=''>（まだ計算結果がありません）</option>";
  if (!runs.includes(st.init)) st.init = runs[0] || null;
  sel.value = st.init || "";
}
async function refresh(keepFt) {
  if (!st.init) { INDEX = null; $("timeInfo").textContent = (STATIC ? "表示できる結果がありません。" : "まだ結果がありません。「計算を実行」から作ってください。"); return; }
  if (!INDEX || INDEX.init !== st.init) INDEX = STATIC ? await (await fetch(`data/${st.init}/index.json`)).json() : await api(`/api/index/${st.init}`);
  if (!INDEX.regions[st.region]) st.region = Object.keys(INDEX.regions)[0];
  if (!(INDEX.maps || []).some((m) => m.id === st.map)) st.map = (INDEX.maps[0] || {}).id || "pale";
  if (INDEX.model_order) MODEL_ORDER = INDEX.model_order;
  fillMaps(); fillRegions(); fillModelSelects();
  if (!INDEX.charts.some((c) => c.id === st.chart && hoursOf(st.model, c.id).length)) {
    const c = INDEX.charts.find((c) => hoursOf(st.model, c.id).length); if (c) st.chart = c.id;
  }
  fillTabs();
  $("btnExp").hidden = !((INDEX.experiments || []).length);
  $("updated").textContent = `作図: ${INDEX.rendered_at ? INDEX.rendered_at.replace("T", " ").replace("+00:00", " UTC") : ""}`;
  render();
}

// ---------- WRF実験の設定（索引の experiments） ----------
const PHYS_NAMES = { mp_physics: "雲物理", cu_physics: "積雲", bl_pbl_physics: "境界層", sf_sfclay_physics: "接地層", sf_surface_physics: "陸面", ra_lw_physics: "長波放射",
  ra_sw_physics: "短波放射", isfflx: "地表フラックス(0=無効)", qfx_scale: "蒸発の係数", hfx_scale: "顕熱の係数", diff_opt: "拡散", km_opt: "乱流混合", radt: "放射間隔", cudt: "積雲間隔" };
const OPNAMES = { scale: "地形の倍率", set: "標高を指定", remove_land: "陸を海にする", add_hill: "山を追加" };
function showExp() {
  const ex = (INDEX && INDEX.experiments) || [];
  $("expBody").innerHTML = ex.map((e) => {
    const c = e.cfg;
    const doms = c.domains.map((d, i) => `d${String(i + 1).padStart(2, "0")}: ${d.dx / 1000}km ${d.n_we}×${d.n_sn}（中心 ${d.center[0]}°N ${d.center[1]}°E）`).join("<br>");
    const ph = Object.entries(c.physics || {}).map(([k, v]) => `${PHYS_NAMES[k] || k} = ${Array.isArray(v) ? v.join(", ") : v}`).join("<br>") || "対照と同じ";
    const tr = (c.terrain || []).map((o) => `${OPNAMES[o.type] || o.type} ${o.factor !== undefined ? "×" + o.factor : o.value !== undefined ? o.value + "m" : ""}${o.bbox ? "（" + o.bbox.join(", ") + "）" : ""}`).join("<br>") || "なし";
    return `<h3>${e.label}（ID: ${e.id}）${e.patched ? "　<small>改造版WRF</small>" : ""}</h3><table class="exp"><tr><th>予報時間</th><td>${c.run_hours}時間</td></tr>
      <tr><th>領域</th><td>${doms}</td></tr><tr><th>物理設定の変更</th><td>${ph}</td></tr><tr><th>地形の編集</th><td>${tr}${(e.terrain_notes || []).length ? "<br><small>" + e.terrain_notes.join(" / ") + "</small>" : ""}</td></tr></table>`;
  }).join("") || "<p>この初期時刻にはWRF実験がありません。「WRF実験」から実行できます。</p>";
  $("expDlg").showModal();
}

// ---------- 公開サイトへ反映（ローカルのみ） ----------
let pubTimer = null;
async function openPublish() {
  const info = await api("/api/publish_info");
  $("pubBody").innerHTML = `<table class="exp"><tr><th></th><th>初期時刻</th><th>容量</th><th>感度実験</th></tr>` + info.runs.map((r, i) =>
    `<tr><td><input type="checkbox" class="pubInit" value="${r.init}" ${i < 3 && r.terrain ? "checked" : ""} ${r.terrain ? "" : "disabled"}></td><td>${fmtInit(r.init)}</td>
      <td>${r.terrain ? r.mb + "MB" : "（標高陰影の図なし）"}</td><td>${r.experiments.join("、") || "－"}</td></tr>`).join("") +
    `</table><p class="note">ギャラリー（任意作図）: <b>${info.gallery}枚</b>。目安として全体 ${info.limit_mb}MB 以下にしてください（GitHub Pages は約1GBまで）。</p><p id="pubSum" class="note"></p>`;
  const upd = () => {
    const mb = [...document.querySelectorAll(".pubInit:checked")].reduce((a, c) => a + (info.runs.find((r) => r.init === c.value).mb || 0), 0);
    $("pubSum").innerHTML = `選択中の合計: 約 <b>${mb}MB</b>（感度実験の広域の図は載せないため、実際はこれより小さくなります）` + (mb > info.limit_mb ? " <b style='color:#c0392b'>— 多すぎます。初期時刻を減らしてください</b>" : "");
  };
  document.querySelectorAll(".pubInit").forEach((c) => (c.onchange = upd)); upd();
  $("pubDlg").showModal();
  pollPublish();
}
async function pollPublish() {
  clearTimeout(pubTimer);
  const s = await api("/api/publish_status").catch(() => ({ status: "none", log: [] }));
  $("pubLog").hidden = !s.log.length; $("pubLog").textContent = s.log.join("\n");
  $("pubMsg").textContent = { running: "反映中…", done: "反映しました（数分後にサイトへ反映されます）", error: "失敗しました（ログを確認）", none: "" }[s.status];
  $("pubGo").disabled = s.status === "running";
  if (s.status === "running") pubTimer = setTimeout(pollPublish, 2500);
}

// ---------- 検証表 ----------
function showStats() {
  const s = (INDEX && INDEX.stats) || {};
  const names = { msl: "海面気圧 [hPa]", t2: "2m気温 [℃]", wspd: "10m風速 [m/s]", rain6: "6時間降水 [mm]" };
  const ms = ["aifs", "aifs_off", "wrf"];
  let h = "";
  for (const [v, lab] of Object.entries(names)) {
    if (!s[v]) continue;
    h += `<h3>${lab}</h3><table><tr><th>予報時間</th>${ms.map((m) => `<th>${INDEX.labels[m]}</th>`).join("")}</tr>`;
    for (const ft of [6, 12, 18, 24]) {
      h += `<tr><td>+${ft}h</td>` + ms.map((m) => {
        const r = (s[v][m] || {})[ft];
        return `<td>${r ? `${r.bias >= 0 ? "+" : ""}${r.bias.toFixed(2)} / ${r.rmse.toFixed(2)} / ${r.corr.toFixed(2)}` : "－"}</td>`;
      }).join("") + "</tr>";
    }
    h += "</table>";
  }
  if (s.precip24) {
    h += `<h3>24時間積算降水（領域内）</h3><table><tr><th>モデル</th><th>領域平均 [mm]</th><th>最大 [mm]</th></tr>`;
    for (const m of ["ifs", "aifs", "aifs_off", "wrf"]) if (s.precip24[m]) h += `<tr><td>${INDEX.labels[m]}</td><td>${s.precip24[m].mean.toFixed(2)}</td><td>${s.precip24[m].max.toFixed(1)}</td></tr>`;
    h += "</table>";
  }
  $("statsBody").innerHTML = h || "<p>この初期時刻には検証結果がありません（IFSデータが必要です）。</p>";
  $("statsDlg").showModal();
}

// ---------- 計算の実行 ----------
// 日付を指定していればそれを、なければ候補の選択を使う
function pickInit() {
  const d = $("runDate").value;
  return d ? d.replaceAll("-", "") + $("runHour").value : $("runInit").value;
}
let pollTimer = null;
async function openRun() {
  const c = await api("/api/candidates");
  const sel = $("runInit");
  sel.innerHTML = c.list.map((x) => `<option value="${x}">${fmtInit(x)}${runs.includes(x) ? "（作図済み）" : ""}${x === c.latest ? "（最新）" : ""}</option>`).join("");
  sel.value = runs.includes(st.init) && c.list.includes(st.init) ? st.init : c.latest;
  const mn = c.min || {};
  const f = (x) => (x ? `${x.slice(0, 4)}-${x.slice(4, 6)}-${x.slice(6, 8)}` : "");
  $("runRange").textContent = `過去の初期時刻も指定できます（00・06・12・18UTC）。取得できる期間: IFS・WRFの比較参照は ${f(mn.ifs)} 以降、公式AIFSは ${f(mn.aifs_off)} 以降、WRFのGFS初期値は ${f(mn.gfs)} 以降。AIFS（手元）は、入力に土壌の変数が要るため ${f(mn.aifs_local)} 以降。ECMWFの本家は直近約3日分のみで、それより前は自動でAWSのミラーから取得します。`;
  $("runDate").min = f(mn.gfs); $("runDate").max = new Date().toISOString().slice(0, 10); $("runDate").value = "";
  $("runDlg").showModal();
  pollJob();
}
function drawJob(j) {
  const run = j.status === "running" || j.status === "queued";
  $("runGo").disabled = run; $("runRender").disabled = run; $("runCancel").hidden = !run;
  const label = { done: "完了", error: "エラー", cancelled: "中止", running: "実行中", queued: "待機中", none: "" }[j.status] || "";
  $("runMsg").textContent = j.init ? `${fmtInit(j.init)}：${label}` : "";
  $("stageList").innerHTML = (j.stages || []).map((s) => `<li class="${s.status}">${s.name} — ${{ waiting: "待機", running: "実行中…", done: "完了", skipped: "済み（スキップ）", error: "エラー" }[s.status]}</li>`).join("");
  const box = $("logBox"), atEnd = box.scrollTop + box.clientHeight >= box.scrollHeight - 20;
  box.textContent = (j.log || []).join("\n");
  if (atEnd) box.scrollTop = box.scrollHeight;
  const m = $("jobMsg");
  m.className = run ? "run" : "";
  const cur = (j.stages || []).find((s) => s.status === "running");
  m.textContent = run ? `計算中: ${cur ? cur.name : ""}` : j.status === "done" ? "" : j.status === "error" ? "計算エラー（「計算を実行」で詳細）" : "";
}
async function pollJob() {
  clearTimeout(pollTimer);
  const j = await api("/api/job").catch(() => ({ status: "none" }));
  drawJob(j);
  if (j.status === "running" || j.status === "queued") pollTimer = setTimeout(pollJob, 2000);
  else if (j.status === "done" && j.init) {
    const was = runs.includes(j.init) && INDEX && INDEX.init === j.init && INDEX.rendered_at;
    await loadRuns(); st.init = j.init; $("initSel").value = j.init; INDEX = null; await refresh();
    if (was) $("updated").textContent += "（更新）";
  }
}

// ---------- 起動 ----------
async function main() {
  loadHash();
  await loadRuns();
  if (runs.length && !runs.includes(st.init)) st.init = runs[0];
  $("mapSel").onchange = (e) => { st.map = e.target.value; render(); };
  $("modelSel").onchange = (e) => { st.model = e.target.value; fillTabs(); render(); };
  $("model2Sel").onchange = (e) => { st.model2 = e.target.value; render(); };
  $("cmpChk").onchange = (e) => { st.cmp = e.target.checked; render(); };
  $("btnSwap").onclick = () => { [st.model, st.model2] = [st.model2, st.model]; $("modelSel").value = st.model; $("model2Sel").value = st.model2; fillTabs(); render(); };
  $("initSel").onchange = (e) => { st.init = e.target.value; INDEX = null; refresh(); };
  $("ftSlider").oninput = (e) => { st.ft = hoursOf(st.model, st.chart)[+e.target.value]; render(); };
  $("btnFirst").onclick = () => step(-999); $("btnLast").onclick = () => step(999);
  $("btnPrev").onclick = () => step(-1); $("btnNext").onclick = () => step(1);
  $("btnPlay").onclick = togglePlay; $("sizeSel").onchange = fitSize; addEventListener("resize", fitSize);
  $("speedSel").onchange = () => { if (st.playing) { togglePlay(); togglePlay(); } };
  $("btnRun").onclick = openRun; $("runClose").onclick = () => $("runDlg").close();
  $("btnExp").onclick = showExp; $("expClose").onclick = () => $("expDlg").close();
  $("btnPublish").onclick = openPublish; $("pubClose").onclick = () => $("pubDlg").close();
  $("pubGo").onclick = async () => {
    const inits = [...document.querySelectorAll(".pubInit:checked")].map((c) => c.value);
    if (!inits.length) { $("pubMsg").textContent = "初期時刻を選んでください"; return; }
    if (!confirm(`公開サイト（GitHub Pages）を更新します。\n初期時刻: ${inits.join(", ")}\nインターネットに公開されます。よろしいですか？`)) return;
    try { await api("/api/publish", { inits }); pollPublish(); } catch (e) { $("pubMsg").textContent = "開始できません: " + e.message; }
  };
  $("btnStats").onclick = showStats; $("statsClose").onclick = () => $("statsDlg").close();
  $("runGo").onclick = async () => {
    const models = [$("runAifs").checked && "aifs", $("runWrf").checked && "wrf"].filter(Boolean);
    const maps = [...document.querySelectorAll(".runMap:checked")].map((x) => x.value);
    try { drawJob(await api("/api/run", { init: pickInit(), models, maps })); pollJob(); } catch (e) { $("runMsg").textContent = "開始できません: " + e.message; }
  };
  $("runRender").onclick = async () => {
    try { drawJob(await api("/api/render", { init: pickInit(), maps: [...document.querySelectorAll(".runMap:checked")].map((x) => x.value) })); pollJob(); } catch (e) { $("runMsg").textContent = "開始できません: " + e.message; }
  };
  $("runCancel").onclick = async () => { await api("/api/cancel", {}); pollJob(); };
  addEventListener("keydown", (e) => {
    if (e.target.matches("input,select,textarea") || document.querySelector("dialog[open]")) return;
    const ci = INDEX ? INDEX.charts.findIndex((c) => c.id === st.chart) : 0;
    if (e.key === "ArrowRight") step(1); else if (e.key === "ArrowLeft") step(-1);
    else if (e.key === "ArrowDown" && INDEX) { st.chart = INDEX.charts[Math.min(INDEX.charts.length - 1, ci + 1)].id; render(); }
    else if (e.key === "ArrowUp" && INDEX) { st.chart = INDEX.charts[Math.max(0, ci - 1)].id; render(); }
    else if (e.key === " ") { e.preventDefault(); togglePlay(); }
    else if (e.key === "Home") step(-999); else if (e.key === "End") step(999);
    else if (e.key === "c" || e.key === "C") { st.cmp = !st.cmp; render(); }
    else return;
    e.preventDefault();
  });
  addEventListener("hashchange", () => location.reload());   // URLを貼り替えたときに状態を読み直す
  if (STATIC) { $("btnPublish").hidden = true; $("lnkCustom").hidden = true; $("lnkWrf").hidden = true; $("btnRun").hidden = true; $("jobMsg").hidden = true; document.body.classList.add("static"); }
  await refresh();
  if (!STATIC) pollJob();
  if (!STATIC && new URLSearchParams(location.search).get("publish") === "1") openPublish();   // 任意作図からの「公開サイトへ反映する…」   // 実行中のジョブがあれば状態を表示
}
main();
