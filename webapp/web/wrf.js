"use strict";
// WRF 実験設定: フォーム → JSON → /api/wrf_estimate（検査・見積り）→ /api/run（実行）
const $ = (id) => document.getElementById(id);
let OPT = null;
let doms = [{ dx: 9000, n_we: 121, n_sn: 101, center: [26.0, 127.5] }];
let ops = [];
let phys = {};            // {キー: [領域ごとの値 or ""]}
let estTimer = null;

const api = async (path, body) => {
  const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : undefined);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.status);
  return j;
};
const fmtInit = (s) => `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)} ${s.slice(8)}UTC`;

// ---------- 領域
function renderDoms() {
  $("doms").innerHTML = doms.map((d, i) => {
    const par = doms[i - 1];
    const dxOpts = i === 0 ? [3000, 9000, 18000, 27000] : [par.dx / 3, par.dx / 5];
    const sel = (i === 0 ? `<input type="number" data-k="dx" data-i="${i}" value="${d.dx}" step="500" style="width:90px">` :
      `<select data-k="dx" data-i="${i}">${dxOpts.map((x) => `<option value="${x}" ${Math.abs(x - d.dx) < 1 ? "selected" : ""}>${x}m（親の1/${Math.round(par.dx / x)}）</option>`).join("")}</select>`);
    return `<div class="dom"><b>d${String(i + 1).padStart(2, "0")}</b>${i === 0 ? "（親）" : "（ネスト）"}
      <label><span>格子間隔</span>${sel}</label>
      <label><span>格子数 東西</span><input type="number" data-k="n_we" data-i="${i}" value="${d.n_we}"></label>
      <label><span>南北</span><input type="number" data-k="n_sn" data-i="${i}" value="${d.n_sn}"></label>
      ${i ? `<label title="初期時刻から何時間後にこのネストを開始するか（3の倍数）。親を先に走らせて、細かいネストはあとから始めるとスピンアップを取れる"><span>開始 h</span><input type="number" data-k="start_h" data-i="${i}" value="${d.start_h || 0}" step="3" min="0" style="width:64px"></label>` : ""}
      <label><span>中心 °N</span><input type="number" data-k="lat" data-i="${i}" value="${d.center[0]}" step="0.1"></label>
      <label><span>°E</span><input type="number" data-k="lon" data-i="${i}" value="${d.center[1]}" step="0.1"></label>
      <span class="note">${((d.n_we - 1) * d.dx / 1000).toFixed(0)}×${((d.n_sn - 1) * d.dx / 1000).toFixed(0)} km</span></div>`;
  }).join("");
  $("doms").querySelectorAll("input,select").forEach((e) => (e.onchange = () => {
    const d = doms[+e.dataset.i], k = e.dataset.k, v = +e.value;
    if (k === "lat") d.center[0] = v; else if (k === "lon") d.center[1] = v; else d[k] = v;
    if (k === "dx" || k === "n_we" || k === "n_sn") { renderDoms(); }
    renderPhys(); scheduleEst();
  }));
  $("addDom").disabled = doms.length >= 3; $("delDom").disabled = doms.length <= 1;
}
$("addDom").onclick = () => {
  if (doms.length >= 3) return;
  const par = doms[doms.length - 1];
  const c = doms.length === 1 ? [par.center[0] + 0.4, par.center[1] + 0.4] : [...par.center];
  doms.push({ dx: par.dx / 3, n_we: doms.length === 1 ? 151 : 181, n_sn: doms.length === 1 ? 151 : 181, center: c });
  renderDoms(); renderPhys(); scheduleEst();
};
$("delDom").onclick = () => { if (doms.length > 1) { doms.pop(); renderDoms(); renderPhys(); scheduleEst(); } };

// ---------- 物理設定
function renderPhys() {
  const nd = doms.length;
  $("phys").innerHTML = Object.entries(OPT.phys).map(([k, v]) => {
    const cur = phys[k] || [];
    const cells = [];
    const n = v.per_domain ? nd : 1;
    for (let i = 0; i < n; i++) {
      const val = cur[i] === undefined ? "" : cur[i];
      if (v.choices) {
        cells.push(`<select data-k="${k}" data-i="${i}"><option value="">既定</option>${Object.entries(v.choices).map(([a, b]) => `<option value="${a}" ${String(val) === a ? "selected" : ""}>${a}: ${b}</option>`).join("")}</select>`);
      } else {
        cells.push(`<input type="number" step="any" style="width:90px" data-k="${k}" data-i="${i}" value="${val}" placeholder="既定">`);
      }
    }
    return `<tr><td>${v.label}</td><td>${n > 1 ? cells.map((c, i) => `<span class="note">d${String(i + 1).padStart(2, "0")}</span> ${c}`).join(" &nbsp; ") : cells[0]}</td></tr>`;
  }).join("");
  $("phys").querySelectorAll("select,input").forEach((e) => (e.onchange = () => {
    const k = e.dataset.k, i = +e.dataset.i;
    phys[k] = phys[k] || [];
    phys[k][i] = e.value === "" ? "" : +e.value;
    scheduleEst();
  }));
}

// ---------- 地形編集
const OPS = {
  scale: { label: "地形の倍率", f: [["factor", "倍率", 0.5]], bbox: true },
  set: { label: "標高を指定", f: [["value", "標高 m", 0]], bbox: true },
  remove_land: { label: "陸を海にする", f: [], bbox: true },
  add_hill: { label: "山を追加", f: [["lat", "中心 °N", 26.5], ["lon", "°E", 128.0], ["height", "高さ m", 1000], ["radius_km", "半径 km（標準偏差）", 12]], bbox: false },
};
function renderOps() {
  $("ops").innerHTML = ops.map((o, i) => {
    const sp = OPS[o.type];
    const f = sp.f.map(([k, lab]) => `<label><span>${lab}</span><input type="number" step="any" data-i="${i}" data-k="${k}" value="${o[k] ?? ""}"></label>`).join("");
    const bb = sp.bbox ? ["西端", "東端", "南端", "北端"].map((lab, j) => `<label><span>${lab}</span><input type="number" step="any" data-i="${i}" data-b="${j}" value="${o.bbox ? o.bbox[j] : ""}" placeholder="全域"></label>`).join("") : "";
    return `<div class="op"><b>${sp.label}</b>${f}${bb}<button type="button" data-del="${i}">削除</button></div>`;
  }).join("");
  $("ops").querySelectorAll("input").forEach((e) => (e.onchange = () => {
    const o = ops[+e.dataset.i];
    if (e.dataset.b !== undefined) {
      const arr = (o.bbox || ["", "", "", ""]); arr[+e.dataset.b] = e.value === "" ? "" : +e.value;
      o.bbox = arr.every((x) => x === "") ? undefined : arr;
    } else o[e.dataset.k] = +e.value;
    scheduleEst();
  }));
  $("ops").querySelectorAll("button[data-del]").forEach((b) => (b.onclick = () => { ops.splice(+b.dataset.del, 1); renderOps(); scheduleEst(); }));
}
document.querySelectorAll("button[data-op]").forEach((b) => (b.onclick = () => {
  const t = b.dataset.op, o = { type: t };
  OPS[t].f.forEach(([k, , v]) => (o[k] = v));
  if (OPS[t].bbox) o.bbox = [127.6, 128.4, 26.0, 26.95];
  ops.push(o); renderOps(); scheduleEst();
}));

// ---------- 設定 ⇄ フォーム
function cfgFromForm() {
  const physics = {};
  for (const [k, arr] of Object.entries(phys)) {
    const v = OPT.phys[k];
    const vals = (v.per_domain ? arr.slice(0, doms.length) : arr.slice(0, 1));
    if (vals.every((x) => x === "" || x === undefined)) continue;
    if (v.per_domain) {
      physics[k] = vals.length === 1 ? vals[0] : vals.map((x, i) => (x === "" || x === undefined ? defaultOf(k, doms[i]) : x));
    } else physics[k] = vals[0];
  }
  return {
    id: $("cid").value.trim(), label: $("clabel").value.trim() || $("cid").value, run_hours: +$("hours").value, history_interval: +$("hint").value,
    domains: doms.map((d, i) => ({ dx: d.dx, n_we: d.n_we, n_sn: d.n_sn, center: d.center, ...(i ? { start_h: d.start_h || 0 } : {}) })), physics,
    terrain: ops.map((o) => { const c = { ...o }; if (c.bbox) c.bbox = c.bbox.map((x) => (x === "" ? NaN : x)); if (c.bbox && c.bbox.some((x) => Number.isNaN(x))) delete c.bbox; return c; }),
  };
}
// 領域ごとの空欄は、その領域の既定値で埋める（一部の領域だけ上書きするため）
function defaultOf(k, d) {
  const km = d.dx / 1000;
  return { mp_physics: 6, cu_physics: d.dx >= 8000 ? 1 : 0, bl_pbl_physics: 1, sf_sfclay_physics: 1, sf_surface_physics: 2, ra_lw_physics: 4, ra_sw_physics: 4,
    diff_opt: 1, km_opt: 4, radt: Math.max(1, Math.round(km)), cudt: d.dx >= 8000 ? 5 : 0, qfx_scale: 1, hfx_scale: 1 }[k] ?? 0;
}
function loadCfg(c) {
  $("cid").value = c.id || "exp1"; $("clabel").value = c.label || c.id || "";
  $("hours").value = c.run_hours || 24; $("hint").value = c.history_interval || 60;
  doms = (c.domains || OPT.default.domains).map((d) => ({ dx: d.dx, n_we: d.n_we, n_sn: d.n_sn, center: [...d.center], start_h: d.start_h || 0 }));
  phys = {};
  for (const [k, v] of Object.entries(c.physics || {})) phys[k] = Array.isArray(v) ? v : [v];
  ops = (c.terrain || []).map((o) => ({ ...o }));
  renderDoms(); renderPhys(); renderOps(); scheduleEst();
}

// ---------- 見積り・検査
function scheduleEst() { clearTimeout(estTimer); estTimer = setTimeout(estimate, 300); }
async function estimate() {
  const e = $("est");
  try {
    const r = await api("/api/wrf_estimate", cfgFromForm());
    e.className = "";
    const dom = r.domains.map((d, i) => `d${String(i + 1).padStart(2, "0")}: ${d.dx / 1000}km ${d.n_we}×${d.n_sn}、時間刻み ${d.dt}秒` + (i ? `（親の ${d.ips},${d.jps} から、開始 +${d.start_h || 0}h）` : "")).join("<br>");
    e.innerHTML = `<b>計算時間の目安: 約 ${r.minutes} 分</b>（6並列）　メモリ 約 ${r.memory_gb} GB<br>${dom}` +
      (r.patched ? `<br><span class="warn">改造版WRFを使います${OPT.patched_build ? "" : "（未ビルド）"}</span>` : "") +
      (r.needs_high_geog ? `<br><span class="warn">高解像度の地形データ（約2.8GB）が必要です${OPT.high_geog ? "（取得済み）" : "（初回に自動で取得）"}</span>` : "") +
      `<br><span class="note">※実測（121×101・24時間＝約7分）からの見積りです。他のジョブと同時だと遅くなります。</span>`;
    return r;
  } catch (err) { e.className = "err"; e.textContent = err.message; return null; }
}

// ---------- 実行
let pollT = null;
async function pollJob() {
  clearTimeout(pollT);
  const j = await api("/api/job").catch(() => ({ status: "none" }));
  const run = j.status === "running" || j.status === "queued";
  $("run").disabled = run; $("cancel").hidden = !run;
  $("stages").innerHTML = (j.stages || []).map((s) => `<li class="${s.status}">${s.name} — ${{ waiting: "待機", running: "実行中…", done: "完了", skipped: "済み（スキップ）", error: "エラー" }[s.status]}</li>`).join("");
  $("jobBox").hidden = !(j.log && j.log.length); $("jobBox").textContent = (j.log || []).join("\n"); $("jobBox").scrollTop = $("jobBox").scrollHeight;
  $("runMsg").textContent = j.init ? `${fmtInit(j.init)}：${{ done: "完了（天気図ビューアで結果を見られます）", error: "エラー", cancelled: "中止", running: "実行中", queued: "待機中" }[j.status] || ""}` : "";
  if (run) pollT = setTimeout(pollJob, 3000);
}
$("run").onclick = async () => {
  const est = await estimate();
  if (!est) return;
  if (est.minutes > 120 && !confirm(`計算時間の目安が約 ${est.minutes} 分です。実行しますか？`)) return;
  const maps = [...document.querySelectorAll(".mp:checked")].map((x) => x.value);
  try {
    await api("/api/run", { init: $("init").value, models: [], maps: maps.length ? maps : ["terrain"], wrf_cfg: cfgFromForm() });
    pollJob();
  } catch (e) { $("runMsg").textContent = "開始できません: " + e.message; }
};
$("cancel").onclick = async () => { await api("/api/cancel", {}); pollJob(); };
$("showJson").onclick = () => { $("json").hidden = false; $("json").value = JSON.stringify(cfgFromForm(), null, 1); };
$("loadJson").onclick = () => { try { $("json").hidden = false; loadCfg(JSON.parse($("json").value)); } catch (e) { alert("JSONを読めません: " + e.message); } };
["cid", "clabel", "hours", "hint"].forEach((i) => ($(i).oninput = scheduleEst));

async function main() {
  OPT = await api("/api/wrf_options");
  $("patchState").textContent = OPT.patched_build ? "ビルド済み" : "未ビルド";
  const c = await api("/api/candidates");
  const have = (await api("/api/runs")).runs;
  $("init").innerHTML = [...new Set([...have, ...c.list])].sort().reverse().map((x) => `<option value="${x}">${fmtInit(x)}${have.includes(x) ? "（作図済み）" : ""}</option>`).join("");
  $("preset").innerHTML = `<option value="">（選択して読み込む）</option>` + OPT.presets.map((p, i) => `<option value="${i}">${p.label}</option>`).join("");
  $("preset").onchange = () => {
    const v = $("preset").value; if (v === "") return; const p = OPT.presets[+v];
    loadCfg({ ...OPT.default, ...p.cfg, id: p.id === "ctl" ? "ctl2" : p.id });
    if (p.init_hint && [...$("init").options].some((o) => o.value === p.init_hint)) $("init").value = p.init_hint;   // 推奨の初期時刻
  };
  loadCfg({ ...OPT.default, id: "exp1", label: "実験1" });
  pollJob();
}
main().catch((e) => ($("est").textContent = e.message));
