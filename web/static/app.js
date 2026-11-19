/* 初级工程师：五年 — 运维监控后台风格前端（无框架） */

const el = (sel, root = document) => root.querySelector(sel);
const esc = (s) =>
  String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const money = (v) => "¥" + Number(v || 0).toLocaleString("zh-CN");
const num = (v, d = 0) =>
  Number(v || 0).toLocaleString("zh-CN", { minimumFractionDigits: d, maximumFractionDigits: d });

let S = null;
let panel = "overview";
let busy = false;
let eventFilter = "all";
let actionFilter = "all";
let snapshots = [];
let monitorPage = 0;
const MONITOR_PAGE_SIZE = 5;
// v0.9：启动主界面与阶段性述职
let MENU = null;
let splashView = "home";
let menuDraft = { difficulty: "normal", review_week: 60, name: "" };
let reviewDismissed = false;

/* ---------------- 基础请求 ---------------- */

async function getState() {
  const r = await fetch("/api/state");
  const j = await r.json();
  if (j.ok) S = j.data;
}

async function post(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return r.json();
}

function toast(msg, kind = "") {
  let t = el("#toast");
  if (!t) {
    t = document.createElement("div");
    t.id = "toast";
    t.className = "toast";
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.className = "toast show " + kind;
  clearTimeout(t._timer);
  t._timer = setTimeout(() => (t.className = "toast " + kind), 3600);
}

function setBusy(on) {
  busy = on;
  document.body.classList.toggle("is-busy", !!on);
  const p = el("#progress");
  if (p) p.classList.toggle("on", !!on);
}

/* ---------------- 通用片段 ---------------- */

function kpi(label, value, unit, cls = "") {
  return `<div class="kpi ${cls}"><label>${label}</label><b>${value}</b>${unit ? `<span class="unit">${unit}</span>` : ""}</div>`;
}

function sevClass(pct) {
  if (pct >= 90) return "bad";
  if (pct >= 75) return "warn";
  return "";
}

function mRow(label, value, cls = "") {
  return `<div class="m-row ${cls}"><span class="k">${label}</span><span class="v">${value}</span></div>`;
}

function eventTree(list) {
  const byId = new Map(list.map((e) => [e.id, e]));
  const children = new Map();
  for (const e of list) {
    if (e.cause && byId.has(e.cause)) {
      if (!children.has(e.cause)) children.set(e.cause, []);
      children.get(e.cause).push(e);
    }
  }
  const roots = list.filter((e) => !e.cause || !byId.has(e.cause));
  roots.sort((a, b) => b.week - a.week || b.day - a.day);
  const out = [];
  const walk = (e) => {
    out.push(e);
    (children.get(e.id) || []).sort((a, b) => a.day - b.day).forEach(walk);
  };
  roots.forEach(walk);
  return out;
}

/* ---------------- 常量 ---------------- */

const STAGE_LABEL = { safe: "安全", watch: "观察", warn: "预警", list: "名单" };
const DIFF_LABEL = { easy: "简单", normal: "普通", hard: "困难", veteran: "老手" };
const STAGE_TEXT = {
  watch: "周明远最近找我聊了两次进度，语气比平时客气。我不太喜欢这种客气。",
  warn: "我下班后开始刷招聘网站。不是因为想走，是因为不知道下个月还在不在。",
  list: "HR 约我周四下午三点，会议室 B。我知道那个会议室是干什么用的。",
};
const ENDING_TEXT = {
  funded: ["拿到 A 轮", "王总在全员会上说，我们拿到了 A 轮。我盯着屏幕，想起三个月前那台快满的磁盘。"],
  unfunded: ["融资失败，但系统健康", "系统没崩，用户还在，团队还在。只是投资人说时机不对。没人做错什么，但这一年没有下一轮了。"],
  layoff: ["被裁员", "HR 说这不是针对我个人。我点头，收拾东西，把工牌放在桌上。"],
  bankrupt: ["技术破产", "客户在群里问，数据还能恢复吗。没有人回答。"],
};
const GOAL_LABEL = { stability: "把月可用性拉回 99.5%", cash: "把现金跑道拉到 8 周", growth: "把注册用户再拉高 8%" };
const FLAVOR = { db: "数据库专家", backend: "后端工程师", sre: "SRE", perf: "性能工程师", algo: "算法工程师" };

/* 行动分组（只影响展示，不改后端） */
const ACTION_GROUPS = [
  { key: "db", label: "数据库结构", ids: ["add_index_user_created", "online_ddl", "rewrite_pending", "optimize_index", "archive_orders", "rewrite_dashboard"] },
  { key: "cache", label: "缓存", ids: ["cache_dashboard", "audit_cache"] },
  { key: "capacity", label: "容量与硬件", ids: ["db_upgrade", "expand_disk", "add_server", "consolidate_servers", "pool_60", "add_read_replica"] },
  { key: "ops", label: "运维与治理", ids: ["open_monitoring", "backup_daily", "circuit_breaker", "write_docs"] },
  { key: "auto", label: "自动化", ids: ["automate_ops"] },
];

const ACTION_RISK = {
  add_index_user_created: "大表 DDL 当周会短时锁写；可以改用在线 DDL",
  online_ddl: "多花行动力；临时磁盘膨胀",
  optimize_index: "重建期间占用 IO",
  archive_orders: "归档数据仍可查但变慢；建议先确认报表需求",
  rewrite_pending: "只解决一条查询；需要科技「索引与执行计划」",
  cache_dashboard: "最多 30 秒脏读，并欠下缓存一致性债",
  audit_cache: "要花一周；期间缓存风险仍在",
  add_server: "只缓解应用层排队，对数据库无效；欠服务器蔓延债",
  consolidate_servers: "流量高峰可能扛不住，先看峰值 QPS",
  pool_60: "池子超过数据库 max_connections 会从排队变成直接拒绝",
  add_read_replica: "复制延迟导致轻微不一致；每月 ¥1500",
  db_upgrade: "买的是余量，不是优化：慢 SQL 打满 IO 时升配只能撑一阵",
  expand_disk: "容量和 IOPS 一起加，但改变不了扫描行数",
  open_monitoring: "每月 ¥300，换来查明细的能力与事故抑噪",
  backup_daily: "平时看不出效果，直到真的丢数据",
  circuit_breaker: "需要科技「服务治理」；对已经打满的数据库无效",
  write_docs: "信任加成递减（+2 → +1 → 0），别指望刷绩效",
  rewrite_dashboard: "需要科技「查询改写」；只影响大屏这一条查询",
  automate_ops: "一次性投入 12 AP，两周后才生效",
};

/* ---------------- 结局与处境 ---------------- */

function endingBanner() {
  const e = S.ending;
  if (e.stage === "safe" || e.ended) return "";
  const cls = e.stage === "list" ? "list" : "";
  let extra = "";
  if (e.stage === "warn" && e.rescue_goal) {
    extra = `<span class="goal">挽救窗口：${GOAL_LABEL[e.rescue_goal] || e.rescue_goal}（截止 W${e.rescue_deadline}）</span>`;
  }
  return `<div class="banner ${cls}">${esc(STAGE_TEXT[e.stage] || "")}${extra}</div>`;
}

function endingScreen() {
  const e = S.ending;
  const [title, text] = ENDING_TEXT[e.ended] || ["结局", ""];
  const flavor = e.ended === "funded" && e.flavor ? ` · ${FLAVOR[e.flavor] || e.flavor}` : "";
  const last = S.months.length ? S.months[S.months.length - 1] : null;
  return `<div class="card ending">
    <div class="kicker">结 局 · W${e.ended_week}</div>
    <h2>${esc(title)}${esc(flavor)}</h2>
    <p class="lead">${esc(text)}</p>
    <div class="kpi-grid">
      ${kpi("坚持", S.week, "周", "")}
      ${kpi("现金", money(S.cash), "", S.cash < 0 ? "bad" : "")}
      ${kpi("可用性", last ? `${num(last.availability, 2)}%` : "--", "", last && last.availability < 99 ? "bad" : "")}
      ${kpi("最终绩效", last ? last.grade : "--", "", "")}
    </div>
    <h3>关键节点</h3>
    <ul class="timeline">${e.timeline.map((t) => `<li>${esc(t)}</li>`).join("") || "<li>无</li>"}</ul>
    <button class="primary" id="restart">重新开始</button>
  </div>`;
}

/* =========================================================
   v0.9 启动主界面：新的游戏 / 加载游戏 / 设置 / 沙盒 / 已解锁知识
   ========================================================= */

async function loadMenu() {
  try {
    const r = await fetch("/api/menu");
    const j = await r.json();
    MENU = j.ok ? j.data : null;
  } catch (err) {
    MENU = null;
  }
  const st = (MENU && MENU.profile && MENU.profile.settings) || {};
  if (st.default_difficulty) menuDraft.difficulty = st.default_difficulty;
  if (st.default_review_week) menuDraft.review_week = Number(st.default_review_week) || 60;
  return MENU;
}

function closeSplash() {
  const root = el("#splash-root");
  if (root) {
    root.innerHTML = "";
    root.classList.remove("open");
  }
}

function openSplash(view = "home") {
  stopAuto();
  loadMenu().then(() => renderSplash(view));
}

function menuDiffs() {
  return (MENU && MENU.difficulties) || [
    { id: "easy", label: "简单", desc: "现金更厚、事故更少" },
    { id: "normal", label: "普通", desc: "设计基准难度" },
    { id: "hard", label: "困难", desc: "事故更密、红线更高" },
    { id: "veteran", label: "老手", desc: "开局带技能，考核仍要自己写" },
  ];
}

function menuHead() {
  return `<div class="splash-head">
      <span class="crest">拾</span>
      <div class="crest-text"><b>拾味科技</b><small>SERVE OR PERISH · 初级工程师：五年</small></div>
      <span class="splash-ver">v0.9.0</span>
    </div>`;
}

function splashHome() {
  const slots = (MENU && MENU.slots) || [];
  const kn = (MENU && MENU.knowledge_unlocked) || 0;
  const kt = (MENU && MENU.knowledge_total) || 0;
  const hasSave = !!S && !(S.ending && S.ending.ended);
  return `<h2>今天也是要上线的一天</h2>
    <p class="splash-lead">自己挑一个述职的时间点（W50~W100）。到点系统会给你一份<b>参考评价</b>，
    列出这局跑出来的数据成就——但没人替你按停止键，你可以一直经营到角色失败为止。
    大屏参数会随时间漂移，科技也得一个一个读条、一个一个考过去。</p>
    <div class="menu-grid">
      <button class="menu-card" data-menu-view="new"><i>新</i><b>新的游戏</b><span>选难度与结算周</span></button>
      <button class="menu-card" data-menu-view="load"><i>档</i><b>加载游戏</b><span>${slots.length} 个存档槽</span></button>
      <button class="menu-card" data-menu-view="settings"><i>设</i><b>设置</b><span>默认难度 / 时间轴速度</span></button>
      <button class="menu-card" data-menu-view="sandbox"><i>验</i><b>沙盒模式</b><span>AP 无限，不结算</span></button>
      <button class="menu-card" data-menu-view="knowledge"><i>识</i><b>已解锁知识</b><span>${kn}/${kt} 张卡</span></button>
    </div>
    ${hasSave ? `<button class="primary big" data-menu-continue="1">继续这一局 · W${S.week}</button>` : ""}`;
}

function splashNew() {
  const rw = Number(menuDraft.review_week) || 60;
  return `<h2>新的游戏</h2>
    <p class="splash-lead">「自动评价周」只决定什么时候给你看一次成绩单，不是通关期限。到点之后照样能继续玩。</p>
    <div class="menu-section"><label>难度 · 只改外部条件，核心规则一致</label>
      <div class="diff-grid">${menuDiffs()
        .map((d) => `<button class="diff-btn ${menuDraft.difficulty === d.id ? "sel" : ""}" data-menu-diff="${d.id}">
          <span class="d-name">${esc(d.label)}</span><span class="d-desc">${esc(d.desc)}</span></button>`)
        .join("")}</div>
    </div>
    <div class="menu-section"><label>自动评价周 <b>W${rw}</b></label>
      <div class="review-picks">${[50, 60, 75, 100]
        .map((w) => `<button class="${rw === w ? "sel" : ""}" data-menu-review="${w}">W${w}</button>`)
        .join("")}</div>
      <input type="range" id="menu-review" min="50" max="100" step="1" value="${rw}">
      <small>W50 约一年、W100 约两年。时间拉得越长，索引碎片、缓存命中、磁盘水位越需要反复维护。</small>
    </div>
    <div class="menu-section"><label>存档名</label>
      <input id="menu-name" autocomplete="off" value="${esc(menuDraft.name || "")}" placeholder="新档案"></div>
    <div class="row-actions"><button class="primary" data-menu-start="1">开始上班</button>
      <button data-menu-view="home">返回</button></div>`;
}

function splashLoad() {
  const slots = (MENU && MENU.slots) || [];
  return `<h2>加载游戏</h2>
    <p class="splash-lead">每局一个独立槽位；新游戏不会覆盖旧档。跨局累积的知识图鉴与设置单独保存。</p>
    <div class="slot-list">${
      slots.length
        ? slots
            .map((s) => `<div class="slot-card">
              <div class="slot-main"><b>${esc(s.name || s.slot)}</b>
                <span>${esc(DIFF_LABEL[s.difficulty] || s.difficulty || "?")} · W${s.week} · 掌握 ${s.unlocked} 项${
                  s.sandbox ? " · 沙盘" : ""
                }${s.ended ? " · 已结束" : ""}</span>
                <small>${esc(s.updated_at || "")} · ${esc(s.rules_version || "")}</small></div>
              <div class="slot-actions">
                <button class="primary" data-menu-slot="${esc(s.slot)}">加载</button>
                <button data-menu-rename="${esc(s.slot)}">改名</button>
                ${s.slot !== (MENU && MENU.current_slot)
                  ? `<button data-menu-del="${esc(s.slot)}">删除</button>`
                  : ""}
              </div>
            </div>`)
            .join("")
        : '<div class="muted">还没有存档，先开一局吧。</div>'
    }</div>
    <div class="row-actions"><button data-menu-view="home">返回</button></div>`;
}

function splashSettings() {
  const st = (MENU && MENU.profile && MENU.profile.settings) || {};
  return `<h2>设置</h2>
    <p class="splash-lead">设置跨存档生效，存在本地档案里，不联网。</p>
    <div class="menu-section"><label>默认难度</label>
      <select id="set-diff">${menuDiffs()
        .map((d) => `<option value="${d.id}" ${st.default_difficulty === d.id ? "selected" : ""}>${esc(d.label)}</option>`)
        .join("")}</select></div>
    <div class="menu-section"><label>默认自动评价周（50~100）</label>
      <input id="set-review" type="number" min="50" max="100" value="${Number(st.default_review_week) || 60}"></div>
    <div class="menu-section"><label>自动推进间隔（毫秒，400~5000）</label>
      <input id="set-auto" type="number" min="400" max="5000" step="200" value="${Number(st.auto_ms) || 1600}"></div>
    <div class="menu-section"><label class="chk"><input id="set-tips" type="checkbox" ${
      st.show_tips === false ? "" : "checked"
    }> 显示悬停说明</label></div>
    <div class="row-actions"><button class="primary" data-menu-save-settings="1">保存设置</button>
      <button data-menu-view="home">返回</button></div>`;
}

function splashKnowledge() {
  const items = (MENU && MENU.knowledge) || [];
  const got = items.filter((x) => x.unlocked).length;
  return `<h2>已解锁知识 · ${got}/${items.length}</h2>
    <p class="splash-lead">跨所有存档累积：读过的知识卡会永久留在图鉴里，换一局重开也不会丢。没解锁的只给摘要，不剧透结论。</p>
    <div class="know-grid">${items
      .map((it) => `<div class="know-card ${it.unlocked ? "" : "locked"}">
        <div class="know-top"><span class="know-field">${esc(it.field_label || it.field || "")}</span><b>${esc(it.title)}</b></div>
        ${
          it.unlocked
            ? `<p>${esc(it.body || "")}</p>
               ${it.in_game ? `<div class="know-game">游戏里：${esc(it.in_game)}</div>` : ""}
               ${it.tell ? `<div class="know-tell">${esc(it.tell)}</div>` : ""}`
            : `<p class="muted">${esc(it.summary || "")}</p><small>未解锁 · 去科技树读对应节点</small>`
        }
      </div>`)
      .join("")}</div>
    <div class="row-actions"><button data-menu-view="home">返回</button></div>`;
}

function splashSandbox() {
  return `<h2>沙盒模式</h2>
    <p class="splash-lead">行动力无限、不结算结局、不触发考核，用来验证参数、查询计划与扩容方案。
    会单独建一个存档槽，不覆盖你的正式局。</p>
    <div class="row-actions"><button class="primary" data-menu-sandbox="1">进入沙盒</button>
      <button data-menu-view="home">返回</button></div>`;
}

function renderSplash(view = "home") {
  const root = el("#splash-root");
  if (!root) return;
  splashView = view;
  const body = {
    home: splashHome, new: splashNew, load: splashLoad,
    settings: splashSettings, knowledge: splashKnowledge, sandbox: splashSandbox,
  }[view] || splashHome;
  root.innerHTML = `<div class="splash"><div class="splash-inner">
    ${menuHead()}
    <div class="splash-body">${body()}</div>
    <div class="splash-foot"><span>数据全部跑在本地 · 不联网 · 存档在 saves/game.db</span>
      ${view !== "home" ? '<button class="link" data-menu-view="home">回主菜单</button>' : ""}</div>
  </div></div>`;
  root.classList.add("open");
}

async function startMenuGame(sandbox = false) {
  if (busy) return;
  const nameEl = el("#menu-name");
  const rwEl = el("#menu-review");
  const name = ((nameEl && nameEl.value) || "").trim() || (sandbox ? "沙盘实验" : "新档案");
  const rw = Math.max(50, Math.min(100, Number((rwEl && rwEl.value) || menuDraft.review_week) || 60));
  const slot = (sandbox ? "sand" : "s") + Date.now().toString(36);
  setBusy(true);
  const j = await post("/api/new", {
    slot, difficulty: sandbox ? "veteran" : menuDraft.difficulty,
    sandbox, review_week: rw, seed: 20261118 + Math.floor(Math.random() * 90000),
  });
  setBusy(false);
  if (!j.ok) return toast("新建失败", "bad");
  await post("/api/slot/rename", { slot, name });
  S = j.data;
  menuDraft.name = name;
  menuDraft.review_week = rw;
  selectedNode = null;
  lens = "latency";
  reviewDismissed = false;
  stopAuto();
  panel = "overview";
  history.replaceState(null, "", "#overview");
  closeSplash();
  await loadSnapshots();
  render();
  toast(sandbox ? "沙盒模式：随便试" : `新档案已建立 · 述职定在 W${rw}`);
}

async function loadSlot(slot) {
  if (busy || !slot) return;
  setBusy(true);
  const j = await post("/api/load", { slot });
  setBusy(false);
  if (!j.ok) return toast(j.message || "加载失败", "bad");
  S = j.data;
  selectedNode = null;
  reviewDismissed = false;
  stopAuto();
  panel = "overview";
  history.replaceState(null, "", "#overview");
  closeSplash();
  await loadSnapshots();
  render();
  toast(`已加载 W${S.week}`);
}

async function saveMenuSettings() {
  const g = (id, dflt) => {
    const e2 = el(id);
    return e2 && e2.value ? e2.value : dflt;
  };
  const tips = el("#set-tips");
  const settings = {
    default_difficulty: g("#set-diff", "normal"),
    default_review_week: Number(g("#set-review", 60)) || 60,
    auto_ms: Number(g("#set-auto", 1600)) || 1600,
    show_tips: !!(tips && tips.checked),
  };
  const j = await post("/api/profile", { settings });
  toast(j.ok ? "设置已保存" : "保存失败", j.ok ? "" : "bad");
  await loadMenu();
  renderSplash("home");
}

async function deleteMenuSlot(slot) {
  if (!window.confirm("删除这个存档？对应的动作快照也会一起删掉。")) return;
  const j = await post("/api/slot/delete", { slot });
  toast(j.message || (j.ok ? "已删除" : "删除失败"), j.ok ? "" : "bad");
  await loadMenu();
  renderSplash("load");
}

async function renameMenuSlot(slot) {
  const name = window.prompt("给这个存档起个名字", "");
  if (!name) return;
  await post("/api/slot/rename", { slot, name: String(name).slice(0, 40) });
  await loadMenu();
  renderSplash("load");
}

async function continueMenuGame() {
  closeSplash();
  if (!S) await getState();
  render();
}

async function configReviewWeek() {
  const cur = (S && S.review_week) || 60;
  const v = window.prompt("把自动评价周改到第几周？（50~100）", String(cur));
  if (!v) return;
  const j = await post("/api/review/config", { review_week: Number(v) || cur });
  toast(j.message || (j.ok ? "已调整" : "调整失败"), j.ok ? "" : "bad");
  if (j.data) S = j.data;
  render();
}

/* ---------------- v0.9 阶段性述职面板 ---------------- */

const TIER_LABEL = { gold: "金", silver: "银", bronze: "铜", none: "未达成" };

function openReviewModal(report) {
  const root = el("#modal-root");
  const r = report || (S && S.review_report) || {};
  if (!root || !r.rank) return;
  const ach = r.achievements || [];
  root.innerHTML = `<div class="modal-mask v3-mask"><div class="modal v3 review-modal">
    <button class="modal-x" data-close="1">✕</button>
    <div class="v3-art review-art"><span class="v3-art-glyph">${esc(r.rank)}</span>
      <div class="v3-art-cap">阶段性述职 · W${r.week || S.week} · ${esc(r.difficulty_label || "")}</div></div>
    <div class="v3-body">
      <div class="v3-kicker">参考评价 · 不是结局</div>
      <h3>${esc(r.headline || "")}</h3>
      <p class="v3-narr">${esc(r.rank_text || "")}</p>
      <div class="review-score"><b>${r.score}</b><span>分 · ${esc(r.rank)} 级</span></div>
      <div class="review-kpis">${(r.kpis || [])
        .map((k) => `<div><label>${esc(k.label)}</label><b>${esc(k.value)}</b><small>${esc(k.note || "")}</small></div>`)
        .join("")}</div>
      <h3>数据成就</h3>
      <div class="ach-grid">${ach
        .map((a) => `<div class="ach ${esc(a.tier)}">
          <span class="ach-badge">${TIER_LABEL[a.tier] || ""}</span>
          <div class="ach-main"><b>${esc(a.title)}</b>
            <span>${esc(a.display)} <i>/ 目标 ${esc(String(a.target))}${esc(a.unit || "")}</i></span>
            <div class="ach-bar"><i style="width:${a.pct}%"></i></div></div>
          <small>${esc(a.desc || "")}</small>
        </div>`)
        .join("")}</div>
      ${r.advice ? `<div class="exam-hint"><b>建议</b><span>${esc(r.advice)}</span></div>` : ""}
      ${(r.milestones || []).length
        ? `<div class="review-ms"><b>履历</b>${r.milestones.map((m) => `<div>· ${esc(m)}</div>`).join("")}</div>`
        : ""}
      <div class="v3-actions">
        <button class="ghost" data-close="1">先留着看</button>
        <button class="primary" data-review-continue="1">继续游玩，直到角色失败</button>
      </div>
      <p class="muted" style="font-size:12px">这份评价只是参考：不会结束这一局、也不锁定任何内容。
      之后照样能继续读科技、维护大屏参数、把没拿到的成就补齐。</p>
    </div>
  </div></div>`;
  root.classList.add("open");
}

async function continueReview() {
  if (busy) return;
  setBusy(true);
  const j = await post("/api/review/continue", {});
  setBusy(false);
  if (j.data) S = j.data;
  closeModal();
  render();
  toast(j.message || "继续游玩");
}

/* =========================================================
   全局视图：服务拓扑（"地图"）+ 透镜
   ========================================================= */

const LENSES = [
  { id: "latency", label: "延迟", legend: "时间花在哪一环" },
  { id: "flow", label: "流量", legend: "请求量与回源放大" },
  { id: "risk", label: "风险", legend: "哪一环最先撑不住" },
];

let lens = "latency";
let selectedNode = null;

const NODES = [
  { id: "client", name: "用户", glyph: "客", x: 12, y: 50 },
  { id: "edge", name: "网关", glyph: "关", x: 28.5, y: 50 },
  { id: "app", name: "应用服务", glyph: "应", x: 46, y: 28 },
  { id: "cache", name: "缓存", glyph: "缓", x: 46, y: 74 },
  { id: "db", name: "MySQL", glyph: "库", x: 68.5, y: 50 },
  { id: "disk", name: "磁盘", glyph: "盘", x: 86.5, y: 50 },
];

const LINKS = [
  ["client", "edge"], ["edge", "app"], ["app", "cache"],
  ["cache", "db"], ["app", "db"], ["db", "disk"],
];

/* 每个节点能做的事（对应 content/actions.json 的 id） */
const NODE_ACTIONS = {
  client: ["open_monitoring", "load_test"],
  edge: ["circuit_breaker", "load_test", "open_monitoring"],
  app: ["pool_60", "add_server", "add_read_replica", "consolidate_servers"],
  cache: ["cache_dashboard", "audit_cache"],
  db: ["add_index_user_created", "optimize_index", "archive_orders", "rewrite_pending", "online_ddl"],
  disk: ["archive_orders", "backup_daily"],
};

/* 真实技术知识卡：这是"技术策略"的骨，不是装饰文案 */
const NODE_KNOWLEDGE = {
  client: {
    role: "用户感知层",
    real: "用户只感知端到端 p95/p99，不感知你的内部指标。一次页面加载 = 入口排队 + 应用处理 + 数据库查询 + 回源，任一环的抖动都会被放大成用户的等待。",
    read: "p95 是分位数不是平均值：它告诉你最慢的那 5% 有多慢，而 SLA 通常按最慢的那部分赔钱。",
  },
  edge: {
    role: "入口与保护",
    real: "网关做限流、熔断、连接复用。它的价值不是让请求更快，而是在下游被打满时把损失控制在可预期范围：有损拒绝好过雪崩。",
    read: "错误率是入口视角的 SLO；错误率上升但 p95 没动，往往说明是拒绝而非变慢。",
  },
  app: {
    role: "应用层并发",
    real: "连接池是应用与数据库之间的并发闸门：池子太小，请求排队（ρ 上升）；池子超过数据库 max_connections，会从排队变成直接拒绝。加机器不等于加吞吐——下游容量才是上限。",
    read: "ρ = 平均占用 / 池大小。ρ 长期 > 0.7 说明闸门偏窄；ρ 接近 1 时等待时间会非线性上涨（排队论里 M/M/c 的拐点）。",
  },
  cache: {
    role: "回源放大器",
    real: "缓存的收益 = 命中率 × 回源成本。命中率 90% 意味着数据库只承接 10% 的请求；掉到 80% 就是回源翻倍，数据库压力直接 ×2。而一致性、脏读、失效风暴是它的代价。",
    read: "击穿/穿透/雪崩是三件不同的事：热点 key 过期是击穿，查不存在的数据是穿透，批量同时过期是雪崩。",
  },
  db: {
    role: "查询与索引",
    real: "慢查询的本质是扫描行数 × 单行成本。索引把 O(N) 的全表扫描变成 O(logN) 的定位，但范围查询之后的列会失效（最左前缀），二级索引还要回表。大表加索引会锁写，线上要用 online DDL。",
    read: "rows 扫描行数比耗时更能说明问题：耗时受缓存和 IO 影响，扫描行数只由执行计划决定。",
  },
  disk: {
    role: "容量与 IOPS",
    real: "磁盘有两个独立上限：容量和 IOPS。写入放大、binlog、临时表都会消耗 IOPS；水位超过 90% 时，InnoDB 的写入会明显退化，再满就只读。归档不是删数据，是把冷数据搬到便宜的地方。",
    read: "io_util 是 IOPS 利用率，不是带宽。它长期高位说明单块盘的随机 IO 已经饱和，加 CPU 没用。",
  },
};

function worstQueryMs() {
  const l = (S.queries || []).filter((q) => !q.hidden && q.ms != null);
  return l.length ? Math.max(...l.map((q) => q.ms)) : null;
}
function slowestQuery() {
  const l = (S.queries || []).filter((q) => !q.hidden && q.ms != null);
  if (!l.length) return null;
  return l.reduce((a, b) => (a.ms >= b.ms ? a : b));
}

/* 每个节点在三种透镜下的取值：全部来自真实指标，不编数 */
function nodeView(id) {
  const m = S.metrics;
  const hit = (m.buffer_hit || 0) / 100;
  const cacheOn = !!(S.cache && S.cache.enabled);
  const worst = worstQueryMs();
  const clamp = (v) => Math.max(0, Math.min(1, v));
  const V = (val, unit, sub, load) => ({ val, unit, sub, load: clamp(load) });

  if (lens === "flow") {
    switch (id) {
      case "client": return V(num(S.registered), "人", "累计注册", S.registered / 50000);
      case "edge": return V(num(m.peak_qps, 0), "QPS", "峰值入口", m.peak_qps / 1200);
      case "app": return V(num(S.app.servers), `×${S.app.workers}`, "实例 × worker", S.app.servers / 8);
      case "cache": return V(num(m.peak_qps * (1 - hit), 0), "QPS", cacheOn ? "未命中回源" : "缓存未启用", 1 - hit);
      case "db": return V(num(m.db_qps, 0), "QPS", "数据库读请求", m.db_qps / 900);
      default: return V(num(m.iops, 0), "IOPS", "实际随机 IO", m.io_util / 100);
    }
  }

  if (lens === "risk") {
    switch (id) {
      case "client": return V(num(m.availability, 2), "%", "月可用性", (100 - m.availability) / 1.2);
      case "edge": return V(num(m.error_rate, 2), "%", "请求错误率", m.error_rate / 3);
      case "app": return V(num(m.pool_rho, 2), "ρ", "连接池饱和度", m.pool_rho / 0.85);
      case "cache": return V(cacheOn ? num(m.buffer_hit, 0) : "关", cacheOn ? "%" : "", cacheOn ? "命中率" : "没有缓存层", cacheOn ? (100 - m.buffer_hit) / 100 : 0.55);
      case "db": return V(num(S.debt_pressure, 0), "%", "技术债压力", S.debt_pressure / 100);
      default: return V(num(m.disk_pct, 0), "%", "容量水位", m.disk_pct / 92);
    }
  }

  /* latency */
  switch (id) {
    case "client": return V(num(m.p95, 0), "ms", "端到端 p95", m.p95 / 800);
    case "edge": return V(num(m.error_rate, 2), "%", "失败请求占比", m.error_rate / 3);
    case "app": return V(num(S.app.servers), "实例", `池 ${S.app.pool} · ρ ${num(m.pool_rho, 2)}`, m.p95 / 800);
    case "cache": return V(cacheOn ? num(m.buffer_hit, 0) : "关", cacheOn ? "%" : "", cacheOn ? "缓冲池命中" : "没有缓存层", cacheOn ? (100 - m.buffer_hit) / 100 : 0.55);
    case "db": return V(worst == null ? "—" : num(worst, 0), "ms", worst == null ? "需开启监控" : "最慢单条查询", worst == null ? 0.25 : worst / 900);
    default: return V(num(m.io_util, 0), "%", `IOPS 利用 · ${num(m.disk_gb, 1)}GB`, m.io_util / 100);
  }
}

function toneOf(load) {
  if (load >= 0.85) return "bad";
  if (load >= 0.6) return "warn";
  return "";
}

const DEBT_CAT = { query: "查询", ops: "运维", arch: "架构" };
const BOTTLENECK_NAME = {
  db_scan: "全表扫描", db_write: "写放大", queue_application: "应用排队",
  cache_stampede: "缓存击穿", capacity: "容量不足", disk: "磁盘瓶颈", healthy: "健康",
};

const BOTTLENECK_NODE = {
  db_scan: "db", db_write: "db", queue_application: "app",
  cache_stampede: "cache", capacity: "app", disk: "disk",
};

function renderMap() {
  const pts = {};
  for (const n of NODES) pts[n.id] = { x: n.x * 10, y: n.y * 5.2 };
  const views = {};
  NODES.forEach((n) => (views[n.id] = nodeView(n.id)));

  const paths = LINKS.map(([a, b]) => {
    const A = pts[a], B = pts[b];
    const mx = (A.x + B.x) / 2;
    const d = `M ${A.x} ${A.y} C ${mx} ${A.y}, ${mx} ${B.y}, ${B.x} ${B.y}`;
    const hot = toneOf(views[a].load) === "bad" || toneOf(views[b].load) === "bad";
    const speed = Math.max(0.55, 1.9 - S.metrics.peak_qps / 900);
    return `<path class="link" d="${d}"/>
      <path class="link flow ${hot ? "hot" : ""}" d="${d}" style="--flow:${speed.toFixed(2)}s"/>`;
  }).join("");

  const nodes = NODES.map((n) => {
    const v = views[n.id];
    const tone = toneOf(v.load);
    const sel = selectedNode === n.id ? "sel" : "";
    return `<div class="map-node ${tone} ${sel}" data-node="${n.id}"
        style="left:${n.x}%;top:${n.y}%"
        data-tip="<b>${esc(n.name)}</b>${esc(NODE_KNOWLEDGE[n.id].role)}<br>点开看它真实在做什么">
      <div class="mn-head"><span class="mn-glyph">${n.glyph}</span><span class="mn-name">${esc(n.name)}</span></div>
      <div class="mn-val">${v.val}<small>${v.unit || ""}</small></div>
      <div class="mn-sub">${esc(v.sub)}</div>
      <div class="bar ${tone}"><i style="width:${Math.round(v.load * 100)}%"></i></div>
      ${BOTTLENECK_NODE[S.bottleneck && S.bottleneck.code] === n.id ? '<span class="mn-badge">瓶颈</span>' : ""}
    </div>`;
  }).join("");

  const lg = LENSES.find((l) => l.id === lens) || LENSES[0];
  return `<div class="map">
    <div class="map-title">拾味科技 · 生产环境<b>服务拓扑</b></div>
    <svg class="map-svg" viewBox="0 0 1000 520" preserveAspectRatio="none">${paths}</svg>
    ${nodes}
    <div class="map-legend">
      <span><i style="background:#8fae62"></i>充裕</span>
      <span><i style="background:#cf9d4a"></i>吃紧</span>
      <span><i style="background:#c4645a"></i>越线</span>
      <span>${esc(lg.legend)}</span>
    </div>
  </div>`;
}

/* ---------------- 顶部资源条 ---------------- */

function cnNum(n) {
  const d = ["零", "一", "二", "三", "四", "五", "六", "七", "八", "九"];
  if (n <= 10) return n === 10 ? "十" : d[n];
  if (n < 20) return "十" + d[n - 10];
  return String(n);
}

function resItem(ico, label, valHtml, opts = {}) {
  return `<div class="res" data-tip="${esc(opts.tip || "")}">
    <span class="res-ico">${ico}</span>
    <div class="res-main">
      <span class="res-label">${label}</span>
      <span style="display:flex;align-items:baseline">${valHtml}</span>
    </div>
  </div>`;
}

function renderHud() {
  const m = S.metrics;
  const bar = el("#resbar");
  if (!bar) return;

  const hist = S.history || [];
  const prev = hist.length >= 2 ? hist[hist.length - 2] : null;
  const cur = hist.length ? hist[hist.length - 1] : null;
  const dCash = prev && cur ? cur.cash - prev.cash : 0;
  const dAvail = prev && cur ? cur.avail - prev.avail : 0;

  const delta = (v, unit = "", invert = false) => {
    if (!v) return "";
    const good = invert ? v < 0 : v > 0;
    return `<span class="res-delta ${good ? "up" : "dn"}">${v > 0 ? "+" : ""}${num(v, Math.abs(v) < 10 ? 1 : 0)}${unit}</span>`;
  };

  const apCap = S.ap_cap || 24;
  const apBonus = S.ap_bonus || { tech: 0, event: 0, temp: 0 };
  const bonusBits = [
    apBonus.tech ? `<span class="res-delta up">科技 +${apBonus.tech}</span>` : "",
    apBonus.event ? `<span class="res-delta up">事件 +${apBonus.event}</span>` : "",
    apBonus.temp ? `<span class="res-delta ${apBonus.temp > 0 ? "up" : "dn"}">临时 ${apBonus.temp > 0 ? "+" : ""}${apBonus.temp}</span>` : "",
  ].join("");
  const apPct = Math.max(0, Math.min(100, Math.round((S.ap / Math.max(1, apCap)) * 100)));

  bar.innerHTML = [
    resItem("¥", "现金", `<span class="res-val ${S.cash < 0 ? "bad" : S.cash < 150000 ? "warn" : ""}">${money(S.cash)}</span>${delta(dCash)}`, {
      tip: "<b>现金</b>每月结算一次：收入 - 人力 - 服务器 - SLA 赔偿。现金撑不到下一轮融资就是技术破产。",
    }),
    resItem(
      "◆",
      "行动力",
      `<span class="res-val ${S.ap <= 2 ? "warn" : ""}">${S.ap}<span style="font-size:10px;color:var(--dim)"> / ${apCap}</span></span>${bonusBits}
       <div class="apbar"><i style="width:${apPct}%"></i></div>`,
      {
        tip: `<b>行动力</b>每周刷新，不累积。基数 ${apBonus.base || 24}，科技 ${
          apBonus.tech >= 0 ? "+" : ""
        }${apBonus.tech}，事件 ${apBonus.event >= 0 ? "+" : ""}${apBonus.event}${
          apBonus.temp ? `，临时 ${apBonus.temp > 0 ? "+" : ""}${apBonus.temp}（到 W${apBonus.temp_until}）` : ""
        }。只用来改系统、救火；科技读条走时间，不占这一池子。`,
      }
    ),
    resItem(
      "学",
      "自学精力",
      `<span class="res-val ${S.energy >= 80 ? "ok" : S.energy < 20 ? "warn" : ""}">${S.energy}<span style="font-size:10px;color:var(--dim)"> / ${S.energy_cap || 100}</span></span>
       ${S.energy_penalty ? `<span class="res-delta dn">行动挤占 -${S.energy_penalty}</span>` : ""}
       <div class="apbar ebar"><i style="width:${Math.max(0, Math.min(100, Math.round((S.energy / Math.max(1, S.energy_cap || 100)) * 100)))}%"></i></div>`,
      {
        tip: `<b>自学精力</b>基础回复 +${S.energy_regen || 12}/周（上限 ${S.energy_cap || 100}），但<b>行动越多、下周回得越少</b>：本周前 2 个动作免费，之后每个 -2 点，动作超过 5 个还会直接烧掉存量精力。本周已执行 <b>${
          S.actions_this_week || 0
        }</b> 个动作，下周实际回复 <b>+${S.energy_regen_eff != null ? S.energy_regen_eff : S.energy_regen || 12}</b>${
          S.energy_overload ? `、额外消耗 <b>${S.energy_overload}</b>` : ""
        }。科技读条只走时间，当前精力对应每周 <b>+${S.study_rate || 1}</b> 点学时，每点耗 4 精力。${
          S.studying ? `<br>当前在读：<b>${esc((S.tech.nodes || []).find((x) => x.id === S.studying)?.name || S.studying)}</b>` : "<br>没在读节点时，精力只回不耗——先攒一周再冲大节点是有回报的。"
        }`,
      }
    ),
    resItem("人", "注册用户", `<span class="res-val">${num(S.registered)}</span>`, {
      tip: "<b>注册用户</b>大促和口碑会带来增长，事故与长期慢会把人推走。",
    }),
    resItem("百", "可用性", `<span class="res-val ${m.availability < 99 ? "bad" : m.availability < 99.9 ? "warn" : "ok"}">${num(m.availability, 2)}%</span>${delta(dAvail)}`, {
      tip: "<b>可用性</b>按周错误率折算。低于 99.5% 会触发 SLA 赔偿，连续低于红线会被列进名单。",
      tip2: "",
    }),
    resItem("债", "技术债", `<span class="res-val ${S.debt_pressure > 60 ? "bad" : S.debt_pressure > 40 ? "warn" : ""}">${num(S.debt_pressure, 0)}%</span>`, {
      tip: "<b>技术债压力</b>不是「欠了多少钱」，是「每次改动的摩擦」。它抬高事故概率，也会在评审时被翻出来。",
    }),
    resItem("信", "主管信任", `<span class="res-val ${S.team.trust < 45 ? "bad" : S.team.trust < 60 ? "warn" : ""}">${num(S.team.trust, 0)}</span>`, {
      tip: "<b>主管信任</b>0-100。文档、按时交付、事故复盘会加；反复出事和甩锅会扣。它决定你被裁的优先级。",
    }),
  ].join("");

  /* 右上角告警徽记 */
  const alerts = [];
  const openExams = ((S.tech && S.tech.exams) || []).filter((x) => x.state === "open");
  if (S.review_pending) alerts.push(["评", "warn", `W${S.review_week} 阶段性述职已出：点开看这局的数据成就`, "review"]);
  else if (!S.review_done && !S.sandbox && S.review_week - S.week <= 4)
    alerts.push(["期", "warn", `还有 ${Math.max(0, S.review_week - S.week)} 周到自动评价周`, "career"]);
  if (openExams.length) alerts.push(["考", "warn", `有 ${openExams.length} 个科技考核等交卷：本周内不交算一次未通过`, "tech"]);
  if (!S.team.monitoring) alerts.push(["盲", "warn", "没有开监控：你只能看到聚合 p95，看不到是哪条查询在拖后腿", "actions"]);
  if (m.availability < 99) alerts.push(["可", "bad", "可用性已跌破 99%，继续下去会触发 SLA 赔偿", "monitor"]);
  if (m.p95 > 800) alerts.push(["慢", "bad", "p95 超过 800ms：用户已经在骂了", "monitor"]);
  if (S.debt_pressure > 60) alerts.push(["债", "bad", "技术债压力超过 60%：下一次改动很可能引发事故", "tech"]);
  if (S.cash < 0) alerts.push(["元", "bad", "现金为负：月结时会先扣赔偿再扣工资", "actions"]);
  const ael = el("#alerts");
  if (ael) {
    ael.innerHTML = alerts
      .slice(0, 4)
      .map(([g, c, t, go]) => `<div class="alert-dot ${c}" data-goto="${go}" data-tip="${esc(t)}">${g}</div>`)
      .join("");
  }

  /* 底部时间轴 */
  const y = el("#clock-y"), mo = el("#clock-m"), meta = el("#clock-meta"), status = el("#chrono-status");
  if (y) y.textContent = `第 ${cnNum(S.year)} 年`;
  if (mo) mo.textContent = `${cnNum(S.month)}月 · 第 ${cnNum(S.week_in_month)} 周`;
  if (meta) {
    const sk = S.skills || {};
    meta.textContent = `${DIFF_LABEL[S.difficulty] || S.difficulty} · SQL ${num(sk.sql, 0)} / DB ${num(sk.db, 0)} / OPS ${num(sk.ops, 0)}`;
  }
  if (status) {
    const tone = S.bottleneck && S.bottleneck.code !== "healthy" ? "warn" : "muted";
    status.innerHTML = `<span class="dot ${S.ending.stage === "safe" ? "ok" : "warn"}"></span>
      <span style="margin-left:7px;color:var(--${tone === "warn" ? "warn" : "dim"})">${esc(S.bottleneck ? S.bottleneck.label : "")}</span>
      <span style="color:var(--dim)"> · W${S.week}${S.sandbox ? " · 沙盘" : ` · 述职 W${S.review_week || 60}`}${
        S.review_continued ? " · 已续玩" : ""
      }</span>`;
  }
}

/* ---------------- 右侧检视面板 ---------------- */

function renderInspector() {
  const box = el("#inspect");
  if (!box) return;
  const m = S.metrics;

  if (selectedNode && NODE_KNOWLEDGE[selectedNode]) {
    const k = NODE_KNOWLEDGE[selectedNode];
    const n = NODES.find((x) => x.id === selectedNode);
    const v = nodeView(selectedNode);
    const acts = (NODE_ACTIONS[selectedNode] || [])
      .map((id) => (S.actions || []).find((a) => a.id === id))
      .filter(Boolean);

    box.innerHTML = `
      <div class="ins-block">
        <div class="ins-title"><span class="mn-glyph">${n.glyph}</span>${esc(n.name)}</div>
        <div class="ins-sub">${esc(k.role)}</div>
        <div class="ins-kv"><span>当前读数</span><span>${v.val}${v.unit || ""}</span></div>
        <div class="ins-kv"><span>读数含义</span><span>${esc(v.sub)}</span></div>
        <div class="ins-kv"><span>负载</span><span>${Math.round(v.load * 100)}%</span></div>
        <div class="bar ${toneOf(v.load)}" style="margin-top:8px"><i style="width:${Math.round(v.load * 100)}%"></i></div>
      </div>
      <div class="ins-block">
        <div class="side-head"><span>真实世界对应</span></div>
        <div class="ins-note">${esc(k.real)}</div>
        <div class="side-head" style="margin-top:12px"><span>怎么读这个指标</span></div>
        <div class="ins-note">${esc(k.read)}</div>
      </div>
      ${
        acts.length
          ? `<div class="ins-block"><div class="side-head"><span>这一环能做的事</span></div>
              <div class="ins-actions">${acts
                .map(
                  (a) =>
                    `<button data-action="${esc(a.id)}" ${a.ready ? "" : "disabled"} data-tip="${esc(
                      a.ready ? a.desc : a.reason || "现在做不了"
                    )}">${esc(a.name)}<span class="ia-cost">·${a.ap}AP</span></button>`
                )
                .join("")}</div></div>`
          : ""
      }
      <button class="hud-btn ghost" data-unselect="1" style="width:100%;margin-top:4px">返回全局</button>`;
    return;
  }

  const last = S.months.length ? S.months[S.months.length - 1] : null;
  const debt = S.debt_by_category || {};
  const debtRows = Object.entries(debt)
    .filter(([, v]) => v > 0)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 4);

  box.innerHTML = `
    <div class="ins-block">
      <div class="side-head"><span>公司处境</span><span class="tag ${S.ending.stage === "safe" ? "ok" : "bad"}">${STAGE_LABEL[S.ending.stage]}</span></div>
      ${mRow("现金", money(S.cash), S.cash < 0 ? "bad" : S.cash < 150000 ? "warn" : "")}
      ${mRow("可用性", `${num(m.availability, 2)}%`, m.availability < 99 ? "bad" : m.availability < 99.9 ? "warn" : "")}
      ${mRow("主管信任", `${num(S.team.trust, 0)} / 100`, S.team.trust < 45 ? "bad" : S.team.trust < 60 ? "warn" : "")}
      ${mRow("客服工单", num(S.team.tickets), S.team.tickets > 100 ? "bad" : S.team.tickets > 30 ? "warn" : "")}
      ${mRow("监控", S.team.monitoring ? "已开启" : "未开启", S.team.monitoring ? "" : "warn")}
    </div>
    <div class="ins-block">
      <div class="side-head"><span>主瓶颈</span></div>
      <div class="ins-note"><b>${esc(S.bottleneck.label)}</b><br>${esc(S.bottleneck.hint)}</div>
    </div>
    <div class="ins-block">
      <div class="side-head"><span>技术债构成</span></div>
      ${
        debtRows.length
          ? debtRows.map(([k, v]) => `<div class="ins-kv"><span>${esc(DEBT_CAT[k] || k)}</span><span>${num(v, 0)}</span></div>`).join("")
          : '<div class="muted" style="font-size:11.5px">还没有欠下任何债。这通常说明你还没开始做取舍。</div>'
      }
    </div>
    <div class="ins-block">
      <div class="side-head"><span>上月月结</span></div>
      ${
        last
          ? `${mRow("利润", money(last.profit), last.profit < 0 ? "bad" : "")}
             ${mRow("营收", money(last.revenue))}
             ${mRow("成本", money(last.cost))}
             ${mRow("SLA 赔偿", last.penalty ? money(last.penalty) : "—", last.penalty ? "bad" : "")}
             ${mRow("绩效", last.grade || "—")}`
          : '<div class="muted" style="font-size:11.5px">第一个月还没结算。</div>'
      }
    </div>
    <div class="side-foot">点地图上的节点，看它真实在做什么。</div>`;
}

function renderFeed() {
  const feed = el("#eventfeed");
  if (!feed) return;
  const items = eventTree(S.events).slice(0, 6);
  feed.innerHTML = `<div class="side-head"><span>最近事件</span><span class="tag">${S.events.length}</span></div>
    <div class="feed">${
      items.length
        ? items
            .map(
              (e) => `<div class="feed-item ${esc(e.tag || "fault")} indent-${Math.min(2, e.depth || 0)}" data-event="${esc(e.id)}">
                <div class="meta">W${e.week} D${e.day}${e.cause ? " · 连锁" : ""}</div>
                <div class="title">${esc(e.title)}</div>
              </div>`
            )
            .join("")
        : '<div class="muted" style="font-size:12px">还没有触发事件</div>'
    }</div>`;
}

function renderChrome() {
  renderHud();
  renderInspector();
  renderFeed();
}

/* ---------------- 全局视图 ---------------- */

function panelStage() {
  const m = S.metrics;
  const last = S.months.length ? S.months[S.months.length - 1] : null;
  const weekEvents = S.events.filter((e) => e.week === S.week).length;
  const slow = slowestQuery();

  const lensBar = `<div class="lens-bar">
    <span class="lens-label">透镜</span>
    ${LENSES.map((l) => `<button data-lens="${l.id}" class="${lens === l.id ? "active" : ""}">${l.label}</button>`).join("")}
  </div>`;

  const head = `<div class="page-head">
      <h2>第 ${S.year} 年 · ${S.month} 月 · 第 ${S.week_in_month} 周</h2>
      <p>拾味科技 · ${DIFF_LABEL[S.difficulty] || S.difficulty}难度${S.sandbox ? " · 沙盘" : ""} · 这周你有 ${S.ap} / ${S.ap_cap} 点行动力</p>
    </div>`;

  const banners = `${(S.blocked || []).map((b) => `<div class="banner info">${esc(b)}</div>`).join("")}
    ${S.sandbox ? '<div class="banner info">沙盘模式：AP 无限、不会破产、不结算 A 轮。用来验证参数。</div>' : ""}`;

  const strip = `<div class="stage-strip">
    <section class="card">
      <h3>本周诊断 <span class="right">${esc(BOTTLENECK_NAME[S.bottleneck.code] || "")}</span></h3>
      <div style="font-family:var(--serif);font-size:15px;color:var(--text-strong);margin-bottom:6px">${esc(S.bottleneck.label)}</div>
      <div class="ins-note" style="margin-top:0">${esc(S.bottleneck.hint)}</div>
      <div class="row-actions" style="margin-top:10px">
        <button data-goto="actions">去处理</button>
        <button data-goto="monitor">看监控</button>
      </div>
    </section>
    <section class="card">
      <h3>本周数字</h3>
      ${mRow("峰值 QPS", num(m.peak_qps, 1))}
      ${mRow("数据库读 QPS", num(m.db_qps, 1))}
      ${mRow("连接池 ρ", num(m.pool_rho, 2), m.pool_rho > 0.7 ? "warn" : "")}
      ${mRow("最慢查询", slow ? `${num(slow.ms, 1)} ms` : "未开监控", slow && slow.ms > 300 ? "bad" : "")}
      ${mRow("本周事件", `${weekEvents} 条`, weekEvents >= 3 ? "warn" : "")}
    </section>
    <section class="card">
      <h3>账本</h3>
      ${mRow("最近月利润", last ? money(last.profit) : "—", last && last.profit < 0 ? "bad" : "")}
      ${mRow("SLA 赔偿", last && last.penalty ? money(last.penalty) : "—", last && last.penalty ? "bad" : "")}
      ${mRow("绩效", last ? last.grade || "—" : "—")}
      ${mRow("文档 / 工单", `${S.team.docs} / ${num(S.team.tickets)}`, S.team.tickets > 100 ? "bad" : "")}
      ${mRow("技术债", `${num(S.debt_pressure, 0)}%`, S.debt_pressure > 60 ? "bad" : S.debt_pressure > 40 ? "warn" : "")}
    </section>
  </div>`;

  return `${head}${banners}${lensBar}${renderMap()}${strip}`;
}

/* ---------------- 监控 ---------------- */

function queryTable(page = 0) {
  const rows = S.queries.filter((q) => !q.hidden);
  if (S.queries.some((q) => q.hidden)) {
    return `<div class="banner">还没开监控：查询明细被遮住了。开监控需要 1 AP + ¥300/月。
      <button data-action="open_monitoring" style="margin-left:8px">开启监控</button></div>`;
  }
  const pages = Math.max(1, Math.ceil(rows.length / MONITOR_PAGE_SIZE));
  const cur = Math.min(page, pages - 1);
  const slice = rows.slice(cur * MONITOR_PAGE_SIZE, cur * MONITOR_PAGE_SIZE + MONITOR_PAGE_SIZE);
  return `<div class="table-wrap"><table>
    <thead><tr><th>查询</th><th class="num">QPS</th><th class="num">耗时</th><th class="num">扫描行数</th><th>访问路径</th></tr></thead>
    <tbody>${slice
      .map((q) => {
        const slow = q.ms > 300;
        const scan = q.path === "full_scan";
        const cls = scan || slow ? "row-bad" : q.ms > 100 ? "row-warn" : "";
        return `<tr class="${cls}">
          <td>${esc(q.name)}</td>
          <td class="num">${num(q.qps, 2)}</td>
          <td class="num" style="color:${slow ? "var(--bad)" : "inherit"}">${num(q.ms, 1)} ms</td>
          <td class="num">${num(q.rows)}</td>
          <td><span class="tag ${scan ? "bad" : q.path === "index_eq" || q.path === "pk" ? "ok" : "warn"}">${esc(q.path)}</span></td>
        </tr>`;
      })
      .join("")}</tbody></table></div>
    <div class="pager">
      <span>共 ${rows.length} 条 · 第 ${cur + 1}/${pages} 页</span>
      <button data-mpage="${cur - 1}" ${cur <= 0 ? "disabled" : ""}>上一页</button>
      <button data-mpage="${cur + 1}" ${cur >= pages - 1 ? "disabled" : ""}>下一页</button>
    </div>`;
}

function panelMonitor() {
  const m = S.metrics;
  return `
    <div class="page-head"><h2>监控</h2><p>聚合指标每 tick 更新；查询明细需要开启 APM。</p></div>
    <div class="kpi-grid">
      ${kpi("接口 p95", num(m.p95, 0), "ms", m.p95 > 800 ? "bad" : m.p95 > 300 ? "warn" : "ok")}
      ${kpi("可用性", num(m.availability, 2), "%", m.availability < 99 ? "bad" : m.availability < 99.9 ? "warn" : "ok")}
      ${kpi("磁盘 IOPS", num(m.io_util, 0), "%", sevClass(m.io_util))}
      ${kpi("连接池 ρ", num(m.pool_rho, 2), "", m.pool_rho > 0.8 ? "warn" : "")}
    </div>
    <div class="charts">
      <div class="chart-card"><h4>可用性（最近 24 周 %）</h4><canvas id="ch-avail"></canvas></div>
      <div class="chart-card"><h4>接口 p95（ms）</h4><canvas id="ch-p95"></canvas></div>
      <div class="chart-card"><h4>月利润（元）</h4><canvas id="ch-profit"></canvas></div>
      <div class="chart-card"><h4>磁盘利用率（%）</h4><canvas id="ch-io"></canvas></div>
    </div>
    <section class="card" style="margin-bottom:14px">
      <h3>慢查询</h3>
      ${queryTable(monitorPage)}
    </section>
    <section class="card">
      <h3>表与索引</h3>
      <table><thead><tr><th>表</th><th class="num">行数</th><th>索引</th></tr></thead>
      <tbody>${S.tables
        .map(
          (t) => `<tr><td>${esc(t.name)}</td><td class="num">${num(t.rows)}</td>
            <td>${t.indexes.length ? t.indexes.map((i) => `<span class="tag">${esc(i)}</span>`).join(" ") : '<span class="muted">无</span>'}</td></tr>`
        )
        .join("")}</tbody></table>
    </section>`;
}

/* ---------------- 行动 ---------------- */

function actionCard(a) {
  const risk = ACTION_RISK[a.id];
  return `<div class="action ${a.ready ? "" : "off"}">
    <div class="body">
      <div class="name">${esc(a.name)}</div>
      <div class="desc">${esc(a.desc)}</div>
      <div class="cost-row">
        <span class="chip ap">AP ${a.ap}</span>
        ${a.cash ? `<span class="chip">${money(a.cash)}</span>` : ""}
        <span class="chip">${a.weeks ? `${a.weeks} 周后生效` : "立即生效"}</span>
        <span class="tag">${a.repeatable ? "可重复" : "一次性"}</span>
        ${a.times ? `<span class="tag">已做 ${a.times} 次</span>` : ""}
      </div>
      ${risk ? `<div class="risk">风险：${esc(risk)}</div>` : ""}
    </div>
    <button data-action="${esc(a.id)}" ${a.ready ? "" : "disabled"} title="${esc(a.reason || "")}">
      ${a.ready ? "执行" : "不可用"}
    </button>
  </div>`;
}

function panelActions() {
  const byId = new Map(S.actions.map((a) => [a.id, a]));
  const used = new Set();
  const groups = ACTION_GROUPS.map((g) => {
    const list = g.ids.map((id) => byId.get(id)).filter(Boolean);
    list.forEach((a) => used.add(a.id));
    return { ...g, list };
  }).filter((g) => g.list.length);
  const rest = S.actions.filter((a) => !used.has(a.id));
  if (rest.length) groups.push({ key: "other", label: "其他", list: rest });

  const filtered = (list) =>
    list.filter((a) => actionFilter === "all" || (actionFilter === "repeat" ? a.repeatable : !a.repeatable));

  return `
    <div class="page-head"><h2>行动</h2><p>本周剩余 <b>${S.ap}</b> / ${S.ap_cap} 点行动力。每个动作都直接改系统；可重复动作成本递增。</p></div>
    <div class="row-actions" style="margin-bottom:12px">
      <button data-afilter="all" class="${actionFilter === "all" ? "primary" : ""}">全部</button>
      <button data-afilter="once" class="${actionFilter === "once" ? "primary" : ""}">一次性</button>
      <button data-afilter="repeat" class="${actionFilter === "repeat" ? "primary" : ""}">可重复</button>
    </div>
    ${groups
      .map((g) => {
        const list = filtered(g.list);
        if (!list.length) return "";
        return `<div class="action-group">
          <div class="group-head">${esc(g.label)} <span class="tag">${list.length}</span></div>
          <div class="action-list">${list.map(actionCard).join("")}</div>
        </div>`;
      })
      .join("")}
    ${
      S.pending.length
        ? `<section class="card"><h3>进行中</h3>
            ${S.pending.map((p) => `<div class="muted">${esc(p.action)} → W${p.due} 生效</div>`).join("")}</section>`
        : ""
    }`;
}

/* ---------------- 代码编辑器（考核弹窗用） ---------------- */

const SQL_KEYWORDS = /^(SELECT|FROM|WHERE|AND|OR|NOT|ORDER|BY|LIMIT|OFFSET|GROUP|HAVING|JOIN|LEFT|RIGHT|INNER|OUTER|ON|AS|COUNT|SUM|AVG|MIN|MAX|DESC|ASC|CREATE|INDEX|UNIQUE|DATE|BETWEEN|IN|IS|NULL|LIKE|CASE|WHEN|THEN|ELSE|END|EXPLAIN|QUERY|PLAN|INSERT|INTO|VALUES|UPDATE|SET|DELETE|WITH|UNION|ALL|ALTER|TABLE|ADD|ALGORITHM|LOCK|NONE|INPLACE|KEY|HAVING|ROUND|IF)$/i;

function highlightSql(sql) {
  let out = "";
  let i = 0;
  const re = /('(?:[^']|'')*')|(--[^\n]*)|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][A-Za-z0-9_]*)/g;
  let m;
  while ((m = re.exec(sql))) {
    if (m.index > i) out += esc(sql.slice(i, m.index));
    const token = m[0];
    if (m[1]) out += `<span class="s">${esc(token)}</span>`;
    else if (m[2]) out += `<span class="c">${esc(token)}</span>`;
    else if (m[3]) out += `<span class="n">${esc(token)}</span>`;
    else if (SQL_KEYWORDS.test(token)) out += `<span class="k">${esc(token)}</span>`;
    else if (sql[re.lastIndex] === "(") out += `<span class="f">${esc(token)}</span>`;
    else out += esc(token);
    i = re.lastIndex;
  }
  out += esc(sql.slice(i));
  return out;
}

function planClass(detail) {
  const u = String(detail || "").toUpperCase();
  if (u.includes("COVERING INDEX")) return { cls: "ok", label: "覆盖索引" };
  if (u.includes("TEMP B-TREE")) return { cls: "bad", label: "额外排序" };
  if (u.includes("USING INDEX")) return { cls: "warn", label: "索引扫描" };
  if (u.startsWith("SCAN")) return { cls: "bad", label: "全表扫描" };
  return { cls: "", label: "其他" };
}

function paintEditor(box, hl) {
  if (!box || !hl) return;
  hl.innerHTML = highlightSql(box.value) + "\n";
  hl.scrollTop = box.scrollTop;
  hl.scrollLeft = box.scrollLeft;
}

function bindEditor(box, hl) {
  if (!box || !hl) return;
  const paint = () => paintEditor(box, hl);
  paint();
  box.addEventListener("input", paint);
  box.addEventListener("scroll", () => {
    hl.scrollTop = box.scrollTop;
    hl.scrollLeft = box.scrollLeft;
  });
  box.addEventListener("keydown", (e) => {
    if (e.key === "Tab") {
      e.preventDefault();
      const a = box.selectionStart;
      const b = box.selectionEnd;
      box.value = box.value.slice(0, a) + "  " + box.value.slice(b);
      box.selectionStart = box.selectionEnd = a + 2;
      paint();
    }
  });
}

/* ---------------- 科技树（HOI4 式网格） ---------------- */

const TECH_GLYPH = { cs: "基", db: "库", backend: "服", sre: "稳", perf: "性", algo: "算" };
const TREE = { colW: 210, rowH: 224, nodeW: 190, nodeH: 112, pad: 28, laneW: 88, subGap: 10 };
const COL_NAME = { 1: "入门", 2: "上手", 3: "进阶", 4: "深水区", 5: "封顶", 6: "终局" };

function techPos(n, minCol) {
  const sub = n.sub || 0;
  return {
    x: TREE.pad + TREE.laneW + (n.col - minCol) * TREE.colW,
    y: TREE.pad + n.row * TREE.rowH + sub * (TREE.nodeH + TREE.subGap),
  };
}

function panelTech() {
  const tech = S.tech || { branches: {}, nodes: [], layout: {} };
  const branches = Object.entries(tech.branches || {}).sort((a, b) => a[1].row - b[1].row);
  const minCol = (tech.layout && tech.layout.min_col) || 1;
  const maxCol = (tech.layout && tech.layout.max_col) || 6;
  const byId = {};
  tech.nodes.forEach((n) => (byId[n.id] = n));
  const W = TREE.pad * 2 + TREE.laneW + (maxCol - minCol) * TREE.colW + TREE.nodeW;
  const H = TREE.pad * 2 + Math.max(1, branches.length - 1) * TREE.rowH + TREE.nodeH * 2 + TREE.subGap;

  const unlocked = tech.nodes.filter((n) => n.unlocked).length;
  const masteryName = tech.mastery ? (tech.branches[tech.mastery] || {}).name || tech.mastery : "";

  const paths = [];
  for (const n of tech.nodes) {
    const p = techPos(n, minCol);
    const parents = [...(n.requires_all || [])];
    (n.requires_any || []).forEach((r) => parents.push(r));
    for (const rid of parents) {
      const parent = byId[rid];
      if (!parent) continue;
      const q = techPos(parent, minCol);
      const x1 = q.x + TREE.nodeW;
      const y1 = q.y + TREE.nodeH / 2;
      const x2 = p.x;
      const y2 = p.y + TREE.nodeH / 2;
      const mx = x1 + Math.max(24, (x2 - x1) * 0.45);
      const cls = n.unlocked && parent.unlocked ? "done" : parent.unlocked ? "live" : "off";
      const any = (n.requires_any || []).includes(rid) ? " any" : "";
      paths.push(
        `<path class="tl ${cls}${any}" d="M${x1} ${y1} C ${mx} ${y1} ${mx} ${y2} ${x2} ${y2}"/>`
      );
    }
  }

  const lanes = branches
    .map(([id, b]) => {
      const y = TREE.pad + b.row * TREE.rowH;
      const total = tech.nodes.filter((n) => n.branch === id).length;
      const done = tech.nodes.filter((n) => n.branch === id && n.unlocked).length;
      return `<div class="tlane" style="top:${y}px;height:${TREE.rowH}px;--bc:${b.color}">
        <div class="tlane-label"><b>${esc(b.name)}</b><small>${done}/${total}</small></div>
      </div>`;
    })
    .join("");

  const heads = [];
  for (let c = minCol; c <= maxCol; c++) {
    const x = TREE.pad + TREE.laneW + (c - minCol) * TREE.colW;
    heads.push(
      `<div class="tcol-head" style="left:${x}px;width:${TREE.nodeW}px">${c} · ${COL_NAME[c] || ""}</div>`
    );
  }

  const nodeEls = tech.nodes
    .map((n) => {
      const p = techPos(n, minCol);
      const st = n.unlocked
        ? "done"
        : n.excluded_by
        ? "excluded"
        : !n.req_ok
        ? "locked"
        : n.exam_state === "open"
        ? "ready"
        : n.cooling
        ? "cooling"
        : n.studying
        ? "studying"
        : n.progress
        ? "paused"
        : "open";
      const pct = n.ap ? Math.min(100, Math.round((n.progress / n.ap) * 100)) : 0;
      const color = (tech.branches[n.branch] || {}).color || "#c9a961";
      const mark = n.unlocked ? "✓" : n.exam_state === "open" ? "考" : n.cooling ? "冷" : "";
      const foot = n.unlocked
        ? "已掌握"
        : n.exam_state === "open"
        ? "待考核 · 本周交卷"
        : n.cooling
        ? `冷却至 W${n.exam_until}`
        : n.excluded_by
        ? "路线互斥"
        : !n.req_ok
        ? "前置未满足"
        : n.studying
        ? `读条 ${n.progress}/${n.ap} · +${n.rate || 1}/周`
        : n.progress
        ? `暂停 ${n.progress}/${n.ap}`
        : n.examined
        ? "未开始 · 需考核"
        : "未开始";
      return `<button class="tnode3 ${st}" style="left:${p.x}px;top:${p.y}px;width:${TREE.nodeW}px;height:${TREE.nodeH}px;--bc:${color}" data-tech-node="${esc(
        n.id
      )}">
        <span class="tn3-top">
          <span class="tn3-ico">${TECH_GLYPH[n.branch] || "技"}</span>
          <span class="tn3-name">${esc(n.name)}</span>
          ${mark ? `<span class="tn3-badge">${mark}</span>` : ""}
        </span>
        <span class="tn3-meta">${n.is_mastery ? "专精" : "T" + n.col} · ${n.ap} 学时${
        n.examined && !tech.exam_enabled ? ' · <b class="pen">免考核</b>' : ""
      }${n.switch_penalty ? ' · <b class="pen">跨线</b>' : ""}</span>
        <span class="tn3-bar"><i style="width:${pct}%"></i></span>
        <span class="tn3-foot">${foot}</span>
      </button>`;
    })
    .join("");

  const exams = (tech.exams || []).filter((x) => x.state === "open");
  const eset = tech.exam_set || {};
  return `<div class="page-head"><h2>科技树</h2>
      <p>六条轨道、${tech.nodes.length} 个节点（新增<b>算法工程</b>）。读条<b>不花行动力</b>：点「开始读条」后每周按自学精力自动推进，精力越高读得越快；读满之后${
    tech.exam_enabled
      ? `要交一次真作业才能掌握——<b>${esc(eset.label || "基础题库")}</b>以编码题与真库 SQL 为主，每条专精路线都躲不掉编码考核`
      : "直接掌握（本难度免考核）"
  }。${
    eset.note ? `<span class="muted">${esc(eset.note)}</span>` : ""
  }专精之间互斥，一局只能选一条。</p></div>
    ${
      exams.length
        ? `<div class="banner info">你有 ${exams.length} 个考核机会待交卷：${exams
            .map((x) => `<b>${esc(x.name)}</b>`)
            .join("、")}。本周内不交，算一次未通过。<button data-exam-open="${exams[0].node}">去考核</button></div>`
        : ""
    }
    <div class="tech-toolbar">
      <span class="tt-item">已掌握 <b>${unlocked}</b><i>/${tech.nodes.length}</i></span>
      <span class="tt-item">专精 <b>${esc(masteryName || "未选择")}</b></span>
      <span class="tt-item">在读 <b>${esc(((tech.nodes || []).find((x) => x.id === tech.active) || {}).name || "无")}</b></span>
      <span class="tt-item">自学精力 <b>${S.energy}</b><i> / ${S.energy_cap || 100}</i> · 每周 +${S.study_rate || 1} 学时</span>
      <span class="tt-legend">
        <i class="lg done"></i>已掌握<i class="lg ready"></i>待考核<i class="lg studying"></i>读条中<i class="lg cooling"></i>冷却<i class="lg locked"></i>锁定
      </span>
    </div>
    <div class="tree-viewport"><div class="tree-canvas" style="width:${W}px;height:${H}px">
      ${lanes}
      ${heads.join("")}
      <svg class="tree-links" width="${W}" height="${H}">${paths.join("")}</svg>
      ${nodeEls}
    </div></div>
    <div class="tree-foot">点节点看详情：先「开始读条」，然后推进周数等它自己走满（同时只能读一个，进度会保留）。攒精力能加速，但没法花钱买进度；读满后先交作业，作业写不出来，知识不算你的。</div>`;
}

function techActionBlock(n) {
  if (n.unlocked) return `<div class="tm-done">已掌握 · 读条与${n.examined ? "考核" : "时间"}都已完成</div>`;
  if (n.excluded_by) return `<div class="tm-blocked">与已选路线「${esc(n.excluded_by)}」互斥</div>`;
  if (!n.req_ok) return `<div class="tm-blocked">前置未满足</div>`;
  if (n.exam_state === "open") {
    return `<button class="primary" data-exam-open="${esc(n.id)}">参加考核（本周内交卷）</button>`;
  }
  if (n.cooling) {
    return `<button disabled>考核冷却中 · W${n.exam_until} 可重考</button>`;
  }
  if (n.full) {
    return `<button class="primary" data-exam-open="${esc(n.id)}">领取考核</button>`;
  }
  const rate = n.rate || S.study_rate || 1;
  const eta = Math.ceil((n.remaining || 0) / rate);
  return `<div class="tm-study">
    <button class="primary" data-tech-study="${esc(n.id)}">${n.studying ? "暂停读条" : "开始读条"}</button>
    <span class="tm-study-note">读条不花行动力：按当前<b>${S.energy}</b> 精力每周 <b>+${rate}</b> 点，还差 <b>${n.remaining}</b> 点、约 <b>${eta}</b> 周读满。${
      n.studying ? "推进周数才会走条。" : "同时只能读一个；暂停后进度保留，精力会继续回复。"
    }</span>
  </div>`;
}

function openTechModal(id) {
  const n = (S.tech.nodes || []).find((x) => x.id === id);
  if (!n) return;
  const root = el("#modal-root");
  if (!root) return;
  const kmap = {};
  (S.knowledge || []).forEach((k) => (kmap[k.id] = k));
  const knows = (n.knowledge || []).map((kid) => kmap[kid]).filter(Boolean);
  const branch = (S.tech.branches || {})[n.branch] || {};
  const reqIds = [...(n.requires_all || []), ...(n.requires_any || [])];
  const reqs = reqIds.map((rid) => {
    const p = (S.tech.nodes || []).find((x) => x.id === rid);
    return `<span class="treq ${p && p.unlocked ? "ok" : ""}">${esc(p ? p.name : rid)}</span>`;
  });
  const isAny = (n.requires_any || []).length > 0;
  const pct = n.ap ? Math.min(100, Math.round((n.progress / n.ap) * 100)) : 0;
  const stateText = n.unlocked
    ? "已掌握"
    : n.exam_state === "open"
    ? "待考核"
    : n.cooling
    ? `冷却至 W${n.exam_until}`
    : n.studying
    ? `读条中 · +${n.rate || 1}/周`
    : n.progress
    ? "暂停（进度保留）"
    : "未开始";

  const knowHtml = knows.length
    ? knows
        .map(
          (k) => `<div class="tm-know ${k.read ? "read" : ""}">
            <div class="tm-know-t"><span>${esc(k.field_label)}</span>${k.read ? "已读" : "未解锁全文"}</div>
            <b>${esc(k.title)}</b>
            <p>${esc(k.summary)}</p>
            ${k.read ? `<p class="tm-know-body">${esc(k.body)}</p>` : ""}
          </div>`
        )
        .join("")
    : '<div class="muted">这个节点没有绑定知识卡。</div>';

  root.innerHTML = `<div class="modal-mask">
    <div class="modal tech-modal">
      <button class="modal-x" data-close="1">✕</button>
      <div class="tm-head" style="--bc:${branch.color || "#c9a961"}">
        <span class="tm-ico">${TECH_GLYPH[n.branch] || "技"}</span>
        <div>
          <div class="tm-branch">${esc(branch.name || n.branch)} · ${n.is_mastery ? "专精" : "第 " + n.col + " 层"}</div>
          <h3>${esc(n.name)}</h3>
        </div>
      </div>
      <p class="tm-desc">${esc(n.desc)}</p>
      <div class="tm-stats">
        <div><label>读条总量</label><b>${n.ap} 学时</b></div>
        <div><label>已完成</label><b>${n.progress}/${n.ap}</b></div>
        <div><label>状态</label><b>${stateText}</b></div>
      </div>
      <div class="tm-bar"><i style="width:${pct}%"></i></div>
      ${
        n.examined
          ? `<div class="tm-exam-note">${
              S.tech.exam_enabled
                ? `读满后需要交一次 <b>${examKindLabel(n.id)}</b> 考核。第一次失败冷却 1 周，第二次失败读条清零。`
                : "本难度免考核：读满即掌握。"
            }</div>`
          : ""
      }
      ${n.switch_penalty ? `<div class="tm-note">跨线成本：本方向第一个节点 ×1.4~2.0。学「计算机基础」可以把这个倍数压下来。</div>` : ""}
      <div class="tm-sec"><div class="tm-sec-t">前置${isAny ? "（任一）" : ""}</div><div class="tm-reqs">${reqs.join("") || '<span class="muted">无</span>'}</div></div>
      <div class="tm-sec"><div class="tm-sec-t">这个节点教什么</div>${knowHtml}</div>
      ${n.unlocks_note ? `<div class="tm-unlock">${esc(n.unlocks_note)}</div>` : ""}
      <div class="tm-actions">${techActionBlock(n)}</div>
    </div>
  </div>`;
  root.classList.add("open");
}

function examKindLabel(nodeId) {
  const key = (S.tech.nodes || []).find((x) => x.id === nodeId);
  return key && key.exam_kind === "number" ? "计算题" : "动手题";
}

async function techStudy(id) {
  if (busy) return;
  setBusy(true);
  const j = await post("/api/tech/study", { node: id });
  setBusy(false);
  toast(j.message, j.ok ? "" : "bad");
  if (j.data) {
    S = j.data;
    closeModal();
    render();
    openTechModal(id);
  }
}

/* ---------------- 考核弹窗（维多利亚式事件卡 + 代码编辑器） ---------------- */

let examTask = null;
let examNode = null;
let examLast = null;

async function openExamModal(nodeId) {
  if (!nodeId) return;
  const root = el("#modal-root");
  if (!root) return;
  examNode = nodeId;
  examTask = null;
  examLast = null;
  root.innerHTML = `<div class="modal-mask v3-mask"><div class="modal v3 exam-modal"><div class="exam-loading">调取考核题目…</div></div></div>`;
  root.classList.add("open");
  let j = null;
  try {
    const r = await fetch("/api/tech/exam?node=" + encodeURIComponent(nodeId));
    j = await r.json();
  } catch (err) {
    toast("题目加载失败", "bad");
    return closeModal();
  }
  if (!j || !j.ok) {
    toast((j && j.message) || "没有这道题", "bad");
    return closeModal();
  }
  examTask = j.data.task || {};
  renderExamModal(j.data);
}

function renderExamModal(meta, result) {
  const root = el("#modal-root");
  if (!root) return;
  const t = examTask || {};
  const n = (S.tech.nodes || []).find((x) => x.id === (meta.node || examNode));
  const branch = (S.tech.branches || {})[(n && n.branch) || meta.branch] || {};
  const tag = n && n.branch === "sre" ? "fault" : n && n.branch === "perf" ? "business" : "team";
  const res = result || examLast;
  const fails = (n && n.exam_fails) || 0;
  const kindHint =
    t.kind === "code"
      ? "写完整的 Python 函数：判题会真的调用它，把下面每个用例都跑一遍。不能 import，函数执行有步数上限。"
      : t.kind === "sql"
      ? "这题会真的在你本地那份 20 万订单的数据库副本上跑一遍，执行计划说了算。"
      : t.kind === "number"
      ? "填数值就行，允许一点误差。"
      : "写出关键语句，判分会检查必须出现的子句。";
  const setLabel = (meta && meta.exam_set && meta.exam_set.label) || "";

  const verdict = res
    ? `<div class="exam-verdict ${res.ok ? "ok" : "bad"}">${res.ok ? "✓ " : "✗ "}${esc(
        res.message || ""
      )}</div>`
    : "";
  const plan = res && res.detail && res.detail.plan && res.detail.plan.length
    ? `<div class="exam-plan">${res.detail.plan
        .map((p) => {
          const c = planClass(p.detail);
          return `<div class="plan-line ${c.cls}"><span class="badge ${c.cls}">${c.label}</span><code>${esc(
            p.detail
          )}</code></div>`;
        })
        .join("")}</div>`
    : "";
  const warns = res && res.detail && res.detail.warnings && res.detail.warnings.length
    ? `<div class="exam-warns">${res.detail.warnings
        .map((w) => `<div>· ${esc(w)}</div>`)
        .join("")}</div>`
    : "";
  const hint = (!res || !res.ok) && t.hint
    ? `<div class="exam-hint"><b>提示</b><span>${esc(t.hint)}</span></div>`
    : "";
  const cooling = !!(n && n.cooling);
  const afterFail =
    res && !res.ok
      ? `<div class="exam-hint"><b>下一步</b><span>${esc(
          res.detail && res.detail.fails >= 2
            ? "这是第二次未通过，读条已经清零：回科技树重新研究一遍再来。"
            : cooling
            ? `冷却 1 周：第 ${(n && n.exam_until) || S.week + 1} 周可以再来一次，再失败读条清零。`
            : "改完再交一次；跨周没交卷也算一次未通过。"
        )}</span></div>`
      : "";
  const solution = res && res.ok && res.detail && res.detail.solution
    ? `<div class="exam-solution"><b>参考解</b><pre>${esc(res.detail.solution)}</pre></div>`
    : "";

  // 编码题：公开测试用例（输入 → 期望），交卷后逐条展示实际输出
  const caseRows = (rows) => (rows || [])
    .map((c) => `<div class="case-row ${c.ok === false ? "bad" : c.ok === true ? "ok" : ""}">
        <span class="case-i">#${c.i}</span>
        <code>${esc(c.args != null ? c.args : "")}</code>
        <span class="case-arrow">→</span>
        <code>${esc(c.expect != null ? c.expect : "")}</code>
        ${c.got != null ? `<span class="case-got">得到 ${esc(c.got)}</span>` : ""}
        ${c.ok === true ? '<span class="case-mark ok">✓</span>' : c.ok === false ? '<span class="case-mark bad">✗</span>' : ""}
      </div>`)
    .join("");
  const publicCases = t.kind === "code" && (t.cases || []).length
    ? `<div class="exam-cases"><b>公开测试用例</b>${caseRows(
        (t.cases || []).map((c, i) => ({
          i: i + 1, args: JSON.stringify(c.args || []), expect: JSON.stringify(c.expect),
        }))
      )}</div>`
    : "";
  const resultCases = res && res.detail && res.detail.kind === "code" && (res.detail.cases || []).length
    ? `<div class="exam-cases results"><b>判题结果 ${res.detail.passed || 0}/${res.detail.total || 0}</b>${caseRows(
        res.detail.cases
      )}</div>`
    : "";

  const editor =
    t.kind === "number"
      ? `<input id="exam-input" class="exam-number" autocomplete="off" placeholder="填一个数值${t.unit ? "（" + esc(t.unit) + "）" : ""}" value="${esc(
          (res && res.detail && res.detail.got != null && res.detail.got) || ""
        )}">`
      : t.kind === "code"
      ? `<textarea id="exam-box" class="exam-code" spellcheck="false" autocomplete="off" wrap="off">${esc(
          (res && res.answer) || t.starter || ""
        )}</textarea>`
      : `<div class="editor exam-editor">
          <pre class="hl" id="exam-hl" aria-hidden="true"></pre>
          <textarea id="exam-box" spellcheck="false" autocomplete="off">${esc(
            (res && res.answer) || t.starter || ""
          )}</textarea>
        </div>`;

  root.innerHTML = `<div class="modal-mask v3-mask">
    <div class="modal v3 exam-modal">
      <button class="modal-x" data-close="1">✕</button>
      <div class="v3-art ev-art ${tag}">
        <span class="v3-art-glyph">考</span>
        <div class="v3-art-cap">${esc(branch.name || "考核")} · 第 ${meta.week || S.week} 周 · ${
    t.kind === "code" ? "编码判题" : t.kind === "sql" ? "真机判题" : t.kind === "number" ? "计算题" : "配置题"
  }${setLabel ? " · " + esc(setLabel) : ""}</div>
      </div>
      <div class="v3-body">
        <div class="v3-kicker">考核 · ${esc(t.role || "现场")}</div>
        <h3>${esc(t.title || meta.name || "考核")}</h3>
        <p class="v3-narr">${esc(t.brief || "")}</p>
        <div class="v3-stakes"><b>要求</b> ${esc(t.goal || "")}</div>
        <div class="exam-meta"><span>已失败 ${fails} 次</span><span>${kindHint}</span></div>
        ${publicCases}
        ${editor}
        <div class="v3-actions">
          <button class="ghost" data-close="1">稍后再写</button>
          <button class="primary" id="exam-submit">提交答卷</button>
        </div>
        ${verdict}${plan}${warns}${resultCases}${hint}${afterFail}${solution}
      </div>
    </div>
  </div>`;
  root.classList.add("open");
  const box = el("#exam-box");
  const hl = el("#exam-hl");
  if (box && hl && t.kind !== "code") bindEditor(box, hl);
}

async function submitExam() {
  if (!examNode || busy) return;
  const box = el("#exam-box");
  const num = el("#exam-input");
  const answer = box ? box.value : num ? num.value : "";
  if (!String(answer).trim()) return toast("答卷是空的", "bad");
  setBusy(true);
  const j = await post("/api/tech/exam/submit", { node: examNode, answer });
  setBusy(false);
  const res = {
    ok: !!j.ok,
    message: j.message || "",
    detail: j.detail || {},
    answer,
  };
  examLast = res;
  if (j.data) S = j.data;
  if (j.ok) {
    toast(j.message, "ok");
    closeModal();
    render();
    return openTechModal(examNode);
  }
  toast(j.message, "bad");
  const meta = { node: examNode, week: S.week };
  renderExamModal(meta, res);
}

/* ---------------- 知识库 ---------------- */

let knowledgeFilter = "all";

function panelKnowledge() {
  const rows = S.knowledge || [];
  const readN = rows.filter((r) => r.read).length;
  const masteredN = rows.filter((r) => r.mastered).length;
  const list = rows.filter((r) =>
    knowledgeFilter === "all" ? true : knowledgeFilter === "read" ? r.read : !r.read
  );
  const groups = {};
  list.forEach((r) => {
    const key = r.field_label || "其它";
    (groups[key] = groups[key] || []).push(r);
  });

  const card = (r) => `<article class="kcard ${r.read ? "read" : "locked"}">
      <div class="kcard-top">
        <span class="kfield">${esc(r.field_label)}</span>
        ${r.mastered ? '<span class="ktag done">已掌握</span>' : r.read ? '<span class="ktag">已读</span>' : '<span class="ktag off">未解锁</span>'}
      </div>
      <h4>${esc(r.title)}</h4>
      <p class="ksum">${esc(r.summary)}</p>
      ${
        r.read
          ? `<div class="kbody">${esc(r.body)}</div>
             <div class="krow"><b>在游戏里</b><span>${esc(r.in_game)}</span></div>
             <div class="krow tell"><b>怎么判断</b><span>${esc(r.tell)}</span></div>`
          : `<div class="klock">去科技树研究「${esc(nodeName(r.node))}」解锁全文</div>`
      }
    </article>`;

  return `<div class="page-head"><h2>知识库</h2>
      <p>你读过的每一条，都会在科技树上变成一次真实的机制修正。没读过的只给标题和摘要——先自己去学。</p></div>
    <div class="know-bar">
      <div class="know-stats"><span>已读 <b>${readN}</b> / ${rows.length}</span><span>已掌握 <b>${masteredN}</b></span></div>
      <div class="know-filter">
        ${[
          ["all", "全部"],
          ["read", "已读"],
          ["unread", "未读"],
        ]
          .map(
            ([k, label]) =>
              `<button data-knowledge-filter="${k}" class="${knowledgeFilter === k ? "active" : ""}">${label}</button>`
          )
          .join("")}
      </div>
    </div>
    ${
      Object.keys(groups).length
        ? Object.entries(groups)
            .map(
              ([field, items]) => `<section class="kgroup">
                <div class="kgroup-head"><b>${esc(field)}</b><span>${items.filter((x) => x.read).length}/${items.length}</span></div>
                <div class="kgrid">${items.map(card).join("")}</div>
              </section>`
            )
            .join("")
        : '<div class="banner">这里还没有内容。去科技树研究节点，读过资料后知识会自动进库。</div>'
    }`;
}

function nodeName(id) {
  const n = (S.tech.nodes || []).find((x) => x.id === id);
  return n ? n.name : "对应节点";
}

/* ---------------- 文档 ---------------- */

const DOCS = [
  {
    title: "索引与最左前缀",
    body: `复合索引 (a, b) 只能被 a、或 a+b 的查询用上；单独查 b 用不上。
      「我的订单」是 WHERE user_id=? ORDER BY created_at DESC，
      (user_id, created_at) 既定位又免排序，(created_at) 只能反向扫全表。`,
  },
  {
    title: "为什么 DATE(col) 用不上索引",
    body: `索引里存的是 created_at 的原值，不是 DATE(created_at)。
      WHERE DATE(created_at)=CURDATE() 对每一行都要先算函数，只能全表扫描。
      改成 created_at >= 今天 AND created_at < 明天，才能走范围索引。`,
  },
  {
    title: "连接池与排队",
    body: `并发需求 = QPS × 单次耗时。池子小于需求就开始排队，超过超时阈值就 500。
      池子也不是越大越好：超过数据库上限会直接拒绝连接，上下文切换还会拖慢吞吐。`,
  },
  {
    title: "缓存与一致性",
    body: `缓存不是加速，是复制数据。复制就有不一致：
      写路径只要有一条绕过了失效逻辑，用户就会看到旧状态。
      TTL 越短越一致、命中越低；这不是 bug，是取舍。`,
  },
  {
    title: "只读副本不是免费扩容",
    body: `副本能分走读流量，但复制有延迟：刚写入的数据在副本上可能还看不见。
      读多写少的报表、大屏适合副本；支付回调、订单详情这类强一致读要留在主库。`,
  },
];

function panelDocs() {
  return `
    <div class="page-head"><h2>文档</h2>
      <p>${S.team.monitoring ? "已开启 APM：查询明细可见。" : "还没开监控：只能看到聚合指标，看不到具体是哪条查询在拖后腿。"}</p></div>
    ${DOCS.map(
      (d, i) => `<details class="card" style="margin-bottom:10px" ${i === 0 ? "open" : ""}>
        <summary style="cursor:pointer;font-weight:600;color:var(--text-strong)">${esc(d.title)}</summary>
        <p class="muted" style="line-height:1.85;margin:10px 0 0">${esc(d.body)}</p>
      </details>`
    ).join("")}`;
}

/* ---------------- 存档 ---------------- */

function panelCareer() {
  const months = S.months.slice().reverse();
  const resolved = S.debt.filter((d) => d.resolved).length;
  return `
    <div class="page-head"><h2>存档与履历</h2><p>P4 初级后端 · 第 ${S.year} 年 · 已完成 ${S.months.length} 个月</p></div>
    <div class="kpi-grid">
      ${kpi("当前绩效", months.length ? months[0].grade : "--", "", "")}
      ${kpi("修复的技术债", resolved, "/ " + S.debt.length, "")}
      ${kpi("主导事件", S.events.length, "条", "")}
      ${kpi("文档", S.team.docs, "份", "")}
    </div>
    <section class="card" style="margin-bottom:14px">
      <h3>阶段性述职 · W${S.review_week || 60}</h3>
      <p class="muted" style="font-size:12px">到点自动结算一次评价，<b>只作参考，不结束游戏</b>：看完还能继续经营，
      一直玩到角色失败（裁员 / 技术破产）。评价里列的是这局真实跑出来的数据成就。</p>
      ${
        S.review_report && S.review_report.rank
          ? `<div class="review-mini">
              <span class="rank-big">${esc(S.review_report.rank)}</span>
              <div><b>${S.review_report.score} 分</b><span>${esc(S.review_report.headline || "")}</span></div>
              <button class="primary" data-review-open="1">查看完整报告</button>
            </div>
            <div class="muted" style="font-size:12px">已结算 ${S.review_count || 1} 次 ·
              真机考核通过 ${(S.tech && S.tech.exams_passed) || 0} 次 / 未通过 ${(S.tech && S.tech.exams_failed) || 0} 次${
                S.review_continued ? " · 已选择继续游玩" : ""
              }</div>`
          : `<div class="muted" style="font-size:12px">还没到自动评价周，还有 ${Math.max(
              0, (S.review_week || 60) - S.week
            )} 周。这段时间里把大屏参数养住、把科技读条排好，成就才会好看。</div>`
      }
      <div class="row-actions" style="margin-top:8px"><button data-review-config="1">调整评价周（50~100）</button></div>
      ${
        (S.review_milestones || []).length
          ? `<div class="review-ms"><b>履历</b>${S.review_milestones
              .map((m) => `<div>· ${esc(m)}</div>`)
              .join("")}</div>`
          : ""
      }
    </section>
    <section class="card" style="margin-bottom:14px">
      <h3>月度记录</h3>
      ${
        months.length
          ? `<table><thead><tr><th>月</th><th>绩效</th><th class="num">可用性</th><th class="num">利润</th></tr></thead>
             <tbody>${months
               .map(
                 (m) => `<tr><td>M${m.month}</td><td><span class="tag ${m.grade === "D" ? "bad" : m.grade === "A" || m.grade === "S" ? "ok" : "warn"}">${m.grade}</span></td>
                 <td class="num">${num(m.availability, 2)}%</td><td class="num">${money(m.profit)}</td></tr>`
               )
               .join("")}</tbody></table>`
          : '<div class="muted">还没有月度记录</div>'
      }
    </section>
    <section class="card" style="margin-bottom:14px">
      <h3>动作快照（可回退）</h3>
      ${
        snapshots.length
          ? `<table><thead><tr><th>时间</th><th>标签</th><th></th></tr></thead><tbody>${snapshots
              .map(
                (s) => `<tr><td class="num">${esc(s.created_at.slice(5, 16))}</td>
                  <td>${esc(s.label)}</td>
                  <td class="right"><button data-rollback="${s.id}">回退</button></td></tr>`
              )
              .join("")}</tbody></table>`
          : '<div class="muted">执行动作后会自动生成快照</div>'
      }
    </section>
    <section class="card">
      <h3>导出</h3>
      <p class="muted" style="font-size:12px">存档在 <code>saves/game.db</code>；也可以导出 JSON 备份。</p>
      <a class="btn" href="/api/export">导出存档 JSON</a>
    </section>`;
}

/* ---------------- 图表 ---------------- */

function drawLine(canvas, values, color, opts = {}) {
  if (!canvas) return;
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  const w = Math.max(1, rect.width);
  const h = Math.max(1, rect.height);
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);
  if (!values.length) return;

  const pad = 8;
  const min = opts.min != null ? opts.min : Math.min(...values);
  const max = opts.max != null ? opts.max : Math.max(...values);
  const span = max - min || 1;
  const x = (i) => pad + (i / Math.max(1, values.length - 1)) * (w - pad * 2);
  const y = (v) => h - pad - ((Math.min(max, Math.max(min, v)) - min) / span) * (h - pad * 2);

  ctx.strokeStyle = "#232c38";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 3; i += 1) {
    const yy = pad + (i / 3) * (h - pad * 2);
    ctx.beginPath();
    ctx.moveTo(pad, yy);
    ctx.lineTo(w - pad, yy);
    ctx.stroke();
  }

  ctx.beginPath();
  values.forEach((v, i) => (i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v))));
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.8;
  ctx.stroke();

  ctx.lineTo(x(values.length - 1), h - pad);
  ctx.lineTo(x(0), h - pad);
  ctx.closePath();
  ctx.fillStyle = color + "1f";
  ctx.fill();

  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.arc(x(values.length - 1), y(values[values.length - 1]), 2.6, 0, Math.PI * 2);
  ctx.fill();
}

function drawEmpty(canvas, text) {
  if (!canvas) return;
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, rect.width) * dpr;
  canvas.height = Math.max(1, rect.height) * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, rect.width, rect.height);
  ctx.fillStyle = "#5a6573";
  ctx.font = '11px "Microsoft YaHei", sans-serif';
  ctx.textAlign = "center";
  ctx.fillText(text, rect.width / 2, rect.height / 2);
}

function drawCharts() {
  if (panel !== "monitor" || !S) return;
  const hist = S.history || [];
  const canvases = ["#ch-avail", "#ch-p95", "#ch-profit", "#ch-io"].map((s) => el(s));
  if (!canvases.every(Boolean)) return;
  if (hist.length < 2) {
    canvases.forEach((c) => drawEmpty(c, "推进几周后显示趋势"));
    return;
  }
  drawLine(canvases[0], hist.map((h) => h.avail), "#4f9d6b", { min: 90, max: 100 });
  drawLine(canvases[1], hist.map((h) => h.p95), "#b8933f", { min: 0 });
  const profit = (S.months || []).slice(-8).map((m) => m.profit);
  if (profit.length >= 2) drawLine(canvases[2], profit, "#5b8def");
  else drawEmpty(canvases[2], "月结后显示利润");
  drawLine(canvases[3], hist.map((h) => h.io), "#b85c5c", { min: 0, max: 100 });
}

/* ---------------- 渲染 ---------------- */

const PANELS = {
  overview: panelStage,
  monitor: panelMonitor,
  actions: panelActions,
  tech: panelTech,
  knowledge: panelKnowledge,
  docs: panelDocs,
  career: panelCareer,
};

let lastPanel = null;

function render() {
  if (!S) return;
  renderChrome();
  const center = el("#center");
  if (!center) return;

  const nav = el("#nav");
  if (nav) nav.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.panel === panel));
  if (document.body && document.body.setAttribute) {
    document.body.setAttribute("data-lens", lens);
    document.body.setAttribute("data-panel", panel);
  }

  if (S.ending.ended) {
    stopAuto();
    center.innerHTML = endingScreen();
    setSpeedUI("pause");
    return;
  }

  center.innerHTML = endingBanner() + (PANELS[panel] || panelStage)();
  if (lastPanel !== panel) {
    center.scrollTop = 0;
    lastPanel = panel;
  }
  if (panel === "monitor") drawCharts();
  maybeOpenDecision();
  // 到评价周自动给一次参考述职；关掉后本次会话不再自动弹，档案页仍可手动打开
  if (S.review_pending && !reviewDismissed) {
    reviewDismissed = true;
    openReviewModal(S.review_report || {});
  }
}

/* ---------------- 时间控制 ---------------- */

let autoTimer = null;
let speed = "pause";

function setSpeedUI(mode) {
  speed = mode;
  const wrap = el("#speed");
  if (!wrap) return;
  wrap.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.speed === mode));
}

function stopAuto() {
  if (autoTimer) {
    clearInterval(autoTimer);
    autoTimer = null;
  }
  if (speed === "auto") setSpeedUI("pause");
}

function startAuto() {
  stopAuto();
  setSpeedUI("auto");
  const st = (MENU && MENU.profile && MENU.profile.settings) || {};
  const gap = Math.max(400, Math.min(5000, Number(st.auto_ms) || 1600));
  autoTimer = setInterval(() => {
    if (!S || S.ending.ended || busy) return stopAuto();
    advance(1);
  }, gap);
}

/* ---------------- 事件报告 ---------------- */

const TAG_GLYPH = { fault: "故", business: "务", team: "人", finance: "账" };
const FX_LABEL = {
  trust: "主管信任", tickets: "客服工单", p95: "接口 p95", p95_add: "接口 p95",
  error: "错误率", error_add: "错误率", error_rate: "错误率",
  availability: "可用性", cash: "现金", satisfaction: "产品满意度",
  registered: "注册用户", users: "注册用户", debt: "技术债", spawn_debt: "新增债务",
  io: "磁盘 IOPS", io_util: "磁盘 IOPS", disk: "磁盘占用", score: "评分",
};
const TAG_NAME = { fault: "故障", business: "业务", team: "团队", finance: "财务" };

function effectChips(effects) {
  const list = Object.entries(effects || {});
  if (!list.length) return '<span class="fx">本周没有直接数值影响</span>';
  return list
    .map(([k, v]) => {
      const isNum = typeof v === "number";
      const txt = isNum ? (v > 0 ? "+" : "") + num(v, Number.isInteger(v) ? 0 : 2) : String(v);
      return `<span class="fx ${isNum ? (v > 0 ? "up" : "dn") : ""}"><span>${esc(FX_LABEL[k] || k)}</span><b>${esc(txt)}</b></span>`;
    })
    .join("");
}

/* 从事件类型推一条"工程上该怎么办"的建议 */
function incidentAdvice(e) {
  const t = e.tag || "fault";
  if (t === "fault") {
    if (S.metrics.p95 > 300) return "先确认是扫描行数还是 IO 打满：去监控页看哪条查询在拖后腿，再决定加索引还是加缓存。";
    return "先看本周诊断定位瓶颈，再动结构。故障期不要同时改两件事，否则无法归因。";
  }
  if (t === "business") return "业务压力不是靠加班解决的。算清楚这一次需求会带来多少 QPS 增量，再决定扩容还是排期。";
  if (t === "team") return "信任是可以经营的资源：一份文档、一次复盘，比通宵改 bug 更能保住下个季度。";
  return "钱的问题最后都会变成容量问题。先看现金跑道还剩几周。";
}

function eventArtClass(tag) {
  return ["fault", "business", "team", "finance"].includes(tag) ? tag : "fault";
}

function openEventModal(id) {
  const d = (S.decisions || []).find((x) => x.id === id);
  if (d) return openDecisionModal(d);
  const e = (S.events || []).find((x) => x.id === id);
  if (!e) return;
  const root = el("#modal-root");
  if (!root) return;
  const tag = eventArtClass(e.tag);
  root.innerHTML = `<div class="modal-mask">
    <div class="modal v3">
      <button class="modal-x" data-close="1">✕</button>
      <div class="v3-art ev-art ${tag}">
        <span class="v3-art-glyph">${TAG_GLYPH[tag] || "事"}</span>
        <div class="v3-art-cap">${TAG_NAME[tag] || "事件"} · W${e.week} D${e.day}</div>
      </div>
      <div class="v3-body">
        <div class="v3-kicker">${esc(e.speaker || TAG_NAME[tag] || "现场")}</div>
        <h3>${esc(e.title)}</h3>
        <p class="v3-narr">${esc(e.narrative || "这件事没有留下更多描述。")}</p>
        ${e.choice_label ? `<div class="v3-chosen">你的决定：<b>${esc(e.choice_label)}</b></div>` : ""}
        <div class="v3-fx">${effectChips(e.effects)}</div>
        <div class="v3-sec"><div class="v3-sec-t">工程上该怎么读</div><p>${esc(incidentAdvice(e))}</p></div>
        <div class="v3-actions">
          <button class="ghost" data-goto="monitor">去监控页归因</button>
          <button class="primary" data-close="1">知道了</button>
        </div>
      </div>
    </div>
  </div>`;
  root.classList.add("open");
}

/* ---------------- 决策事件（V3 式弹窗） ---------------- */

function openDecisionModal(d) {
  const root = el("#modal-root");
  if (!root) return;
  const tag = eventArtClass(d.art || d.tag);
  const choices = d.choices || [];
  root.innerHTML = `<div class="modal-mask v3-mask">
    <div class="modal v3 decision" data-decision="${esc(d.id)}">
      <div class="v3-art ev-art ${tag}">
        <span class="v3-art-glyph">${TAG_GLYPH[tag] || "事"}</span>
        <div class="v3-art-cap">${TAG_NAME[tag] || "决策"} · W${d.week} D${d.day}</div>
      </div>
      <div class="v3-body">
        <div class="v3-kicker">${esc(d.speaker || "现场")}</div>
        <h3>${esc(d.title)}</h3>
        <p class="v3-narr">${esc(d.narrative || "")}</p>
        ${d.stakes ? `<div class="v3-stakes">${esc(d.stakes)}</div>` : ""}
        <div class="v3-choices">
          ${choices
            .map(
              (c, i) => `<button class="v3-cbtn" data-choice="${esc(c.id)}">
                <span class="v3-cidx">${i + 1}</span>
                <span class="v3-cmain">
                  <b>${esc(c.label)}</b>
                  ${c.hint ? `<small>${esc(c.hint)}</small>` : ""}
                  <span class="v3-cfx">${(c.effects || [])
                    .map((x) => `<i>${esc(x)}</i>`)
                    .join("")}</span>
                </span>
                ${c.default ? '<span class="v3-cdef">默认</span>' : ""}
              </button>`
            )
            .join("")}
        </div>
        <div class="v3-hint">不选就推进，会按「默认」处理。世界不会等你。</div>
      </div>
    </div>
  </div>`;
  root.classList.add("open");
}

async function chooseDecision(id, choice) {
  if (busy) return;
  setBusy(true);
  const j = await post("/api/event/choose", { id, choice });
  setBusy(false);
  if (!j.ok) return toast(j.message || "处理失败", "bad");
  S = j.data;
  closeModal();
  toast(j.message || "已处理");
  render();
}

function maybeOpenDecision() {
  if (!S || S.ending.ended) return;
  const list = S.decisions || [];
  if (!list.length) return;
  const root = el("#modal-root");
  if (!root || root.classList.contains("open")) return;
  openDecisionModal(list[0]);
}

/* ---------------- 事件弹窗（已在上方重构为"事件报告"） ---------------- */

function closeModal() {
  const root = el("#modal-root");
  if (root) {
    root.classList.remove("open");
    root.innerHTML = "";
  }
}

/* ---------------- 交互 ---------------- */

async function advance(weeks) {
  if (busy || (S && S.ending.ended)) return;
  setBusy(true);
  const before = S.week;
  const j = await post("/api/advance", { weeks });
  setBusy(false);
  if (!j.ok) {
    stopAuto();
    return toast("推进失败", "bad");
  }
  S = j.data;
  render();
  if (S.ending.ended) {
    stopAuto();
    toast("这一局结束了", "bad");
    return;
  }
  toast(`第 ${S.week} 周 · 剩余行动力 ${S.ap}`);
}

async function loadSnapshots() {
  try {
    const r = await fetch("/api/snapshots");
    const j = await r.json();
    if (j.ok) snapshots = j.data;
  } catch (err) {
    snapshots = [];
  }
}

async function rollback(id) {
  if (!window.confirm("回退到这个快照？当前进度会被覆盖。")) return;
  const j = await post("/api/rollback", { id: Number(id) });
  toast(j.message || (j.ok ? "已回退" : "回退失败"), j.ok ? "" : "bad");
  if (j.ok) {
    S = j.data;
    await loadSnapshots();
    render();
  }
}

async function startSandbox() {
  if (busy) return;
  if (!window.confirm("进入沙盘模式？AP 无限、不会破产，用来验证参数。")) return;
  setBusy(true);
  const j = await post("/api/new", { sandbox: true, difficulty: "veteran" });
  setBusy(false);
  if (!j.ok) return toast("进入沙盘失败", "bad");
  S = j.data;
  selectedNode = null;
  lens = "latency";
  stopAuto();
  panel = "overview";
  history.replaceState(null, "", "#overview");
  render();
  toast("沙盘模式：随便试");
}

async function doAction(id) {
  if (busy) return;
  setBusy(true);
  const j = await post("/api/action", { id });
  setBusy(false);
  toast(j.message, j.ok ? "" : "bad");
  if (j.data) {
    S = j.data;
    await loadSnapshots();
    render();
  }
}

function openNewGameModal() {
  const root = el("#modal-root");
  if (!root) return;
  const opts = [
    ["easy", "简单", "现金 75 万 · 事故更少 · 评审线更低", "第一次玩"],
    ["normal", "普通", "现金 50 万 · 设计基准", "推荐"],
    ["hard", "困难", "现金 35 万 · 事故更密 · 红线更高", "每步都要算成本"],
    ["veteran", "老手", "SQL 55 起步 + 直接解锁 db_t1", "考核仍要自己过"],
  ];
  root.innerHTML = `<div class="modal-mask">
    <div class="modal">
      <h3>开始新游戏</h3>
      <div class="meta">选择难度 · 只改变外部条件，核心规则一致</div>
      <div class="diff-grid">
        ${opts
          .map(
            ([id, name, desc, note]) => `<button class="diff-btn" data-difficulty="${id}">
              <span class="d-name">${name}</span>
              <span class="d-desc">${desc}</span>
              <span class="d-note">${note}</span>
            </button>`
          )
          .join("")}
      </div>
      <div class="row-actions" style="justify-content:flex-end">
        <button data-close="1">取消</button>
      </div>
    </div>
  </div>`;
  root.classList.add("open");
}

async function startNewGame(diff) {
  closeModal();
  setBusy(true);
  // 旧入口也用独立槽位，避免把玩家正在玩的存档直接覆盖掉
  const slot = "s" + Date.now().toString(36);
  const j = await post("/api/new", { slot, difficulty: diff, review_week: menuDraft.review_week });
  setBusy(false);
  if (!j.ok) return toast("新建失败", "bad");
  S = j.data;
  selectedNode = null;
  lens = "latency";
  stopAuto();
  panel = "overview";
  history.replaceState(null, "", "#overview");
  render();
  toast(`难度：${DIFF_LABEL[diff] || diff}`);
}

async function newGame() {
  if (busy) return;
  return openSplash("new");
}

/* ---------------- 悬停提示 ---------------- */

let tipEl = null;

function showTip(anchor, html) {
  if (!html) return;
  if (!tipEl) tipEl = el("#tooltip");
  if (!tipEl) return;
  tipEl.innerHTML = html;
  tipEl.classList.add("on");
  const r = anchor.getBoundingClientRect();
  const w = tipEl.offsetWidth, h = tipEl.offsetHeight;
  let x = r.left + r.width / 2 - w / 2;
  let y = r.top - h - 10;
  if (y < 8) y = r.bottom + 10;
  x = Math.max(10, Math.min(window.innerWidth - w - 10, x));
  tipEl.style.left = x + "px";
  tipEl.style.top = y + "px";
}

function hideTip() {
  if (tipEl) tipEl.classList.remove("on");
}

function bindTips(root) {
  if (!root) return;
  root.addEventListener("mouseover", (e) => {
    const t = e.target.closest("[data-tip]");
    if (t) showTip(t, t.dataset.tip);
  });
  root.addEventListener("mouseout", (e) => {
    const t = e.target.closest("[data-tip]");
    if (!t) return;
    if (e.relatedTarget && t.contains(e.relatedTarget)) return;
    hideTip();
  });
}

function bind() {
  /* 统一事件委托：中间舞台、右侧检视栏、顶部 HUD、底部时间轴、弹窗都在 document 上处理。
     上一版把监听挂在 #center 上，于是检视栏里的「这一环能做的事」和弹窗里的
     研究/考核按钮全部点不动——因为它们的 DOM 不在 #center 里。 */
  document.addEventListener("click", (e) => {
    const pick = (sel) => e.target.closest(sel);

    /* --- 启动主界面（覆盖层，优先处理） --- */
    const mview = pick("button[data-menu-view]");
    if (mview) return renderSplash(mview.dataset.menuView);
    const mdiff = pick("button[data-menu-diff]");
    if (mdiff) {
      menuDraft.difficulty = mdiff.dataset.menuDiff;
      return renderSplash("new");
    }
    const mrev = pick("button[data-menu-review]");
    if (mrev) {
      menuDraft.review_week = Number(mrev.dataset.menuReview);
      const ri = el("#menu-review");
      if (ri) ri.value = String(menuDraft.review_week);
      return renderSplash("new");
    }
    if (pick("button[data-menu-start]")) return startMenuGame(false);
    if (pick("button[data-menu-sandbox]")) return startMenuGame(true);
    const mslot = pick("button[data-menu-slot]");
    if (mslot) return loadSlot(mslot.dataset.menuSlot);
    const mdel = pick("button[data-menu-del]");
    if (mdel) return deleteMenuSlot(mdel.dataset.menuDel);
    const mren = pick("button[data-menu-rename]");
    if (mren) return renameMenuSlot(mren.dataset.menuRename);
    if (pick("button[data-menu-save-settings]")) return saveMenuSettings();
    if (pick("button[data-menu-continue]") || pick("button[data-menu-close]")) return continueMenuGame();

    /* --- 阶段性述职 --- */
    if (pick("button[data-review-continue]")) return continueReview();
    if (pick("button[data-review-open]")) return openReviewModal();
    if (pick("button[data-review-config]")) return configReviewWeek();

    /* --- 左侧导航 --- */
    const navBtn = pick("button[data-panel]");
    if (navBtn) {
      panel = navBtn.dataset.panel;
      history.replaceState(null, "", "#" + panel);
      closeModal();
      return render();
    }

    /* --- 顶部 HUD：主菜单 / 新档案 / 沙盘 --- */
    if (pick("#menu")) return openSplash("home");
    if (pick("#newgame")) return newGame();
    if (pick("#sandbox")) return startSandbox();

    /* --- 底部时间轴：暂停 / 推进一周 / 推进四周 / 自动 --- */
    const sp = pick("button[data-speed]");
    if (sp) {
      if (busy) return;
      const mode = sp.dataset.speed;
      if (mode === "pause") { stopAuto(); setSpeedUI("pause"); return; }
      if (mode === "auto") return startAuto();
      stopAuto();
      setSpeedUI("pause");
      return advance(mode === "4" ? 4 : 1);
    }

    /* --- 科技与考核 --- */
    const examOpen = pick("[data-exam-open]");
    if (examOpen) return openExamModal(examOpen.dataset.examOpen);
    if (e.target.id === "exam-submit") return submitExam();
    const ts = pick("button[data-tech-study]");
    if (ts) return techStudy(ts.dataset.techStudy);
    const tnode = pick("button[data-tech-node]");
    if (tnode) return openTechModal(tnode.dataset.techNode);

    /* --- 决策事件 --- */
    const ch = pick("button[data-choice]");
    if (ch) {
      const box = ch.closest("[data-decision]");
      if (box) chooseDecision(box.dataset.decision, ch.dataset.choice);
      return;
    }

    /* --- 动作 --- */
    const act = pick("button[data-action]");
    if (act) {
      if (act.disabled) return;
      return doAction(act.dataset.action);
    }

    /* --- 面板内控件 --- */
    const l = pick("button[data-lens]");
    if (l) {
      lens = l.dataset.lens;
      return render();
    }
    if (pick("[data-unselect]")) {
      selectedNode = null;
      return render();
    }
    if (e.target.id === "restart") return newGame();
    const kf = pick("button[data-knowledge-filter]");
    if (kf) {
      knowledgeFilter = kf.dataset.knowledgeFilter;
      return render();
    }
    const af = pick("button[data-afilter]");
    if (af) {
      actionFilter = af.dataset.afilter;
      return render();
    }
    const mp = pick("button[data-mpage]");
    if (mp) {
      monitorPage = Math.max(0, Number(mp.dataset.mpage));
      render();
      return drawCharts();
    }
    const rb = pick("button[data-rollback]");
    if (rb) return rollback(rb.dataset.rollback);

    /* --- 地图节点 --- */
    const node = pick("[data-node]");
    if (node && node.dataset.node) {
      selectedNode = selectedNode === node.dataset.node ? null : node.dataset.node;
      return render();
    }
    const ev = pick("[data-event]");
    if (ev && ev.dataset.event) return openEventModal(ev.dataset.event);

    /* --- 跳转（放最后：别的按钮上也可能带 data-goto） --- */
    const go = pick("[data-goto]");
    if (go) {
      if (go.dataset.goto === "review") return openReviewModal();
      panel = go.dataset.goto;
      history.replaceState(null, "", "#" + panel);
      closeModal();
      return render();
    }

    /* --- 难度选择 --- */
    const diff = pick("button[data-difficulty]");
    if (diff) return startNewGame(diff.dataset.difficulty);

    /* --- 关闭弹窗 --- */
    if (pick("button[data-close]")) return closeModal();
    if (e.target.classList.contains("modal-mask")) return closeModal();
  });

  /* 悬停说明：统一挂载在 document 上，避免子元素间移动时闪烁 */
  bindTips(document);

  /* 键盘：空格推进 / 数字切页 / Esc 关闭 */
  document.addEventListener("keydown", (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target.tagName || "").toUpperCase());
    if (e.key === "Escape") {
      if (el(".modal[data-decision]")) return;
      closeModal();
      return;
    }
    if (typing) return;
    if (e.code === "Space") {
      e.preventDefault();
      advance(1);
      return;
    }
    if (el(".modal[data-decision]")) {
      const pick = Number(e.key);
      const box = el(".modal[data-decision]");
      const btns = box ? box.querySelectorAll("button[data-choice]") : [];
      if (pick >= 1 && pick <= btns.length) {
        chooseDecision(box.dataset.decision, btns[pick - 1].dataset.choice);
        return;
      }
    }
    const idx = "1234567".indexOf(e.key);
    if (idx >= 0) {
      const order = ["overview", "monitor", "actions", "tech", "knowledge", "docs", "career"];
      panel = order[idx];
      history.replaceState(null, "", "#" + panel);
      render();
    }
  });

  window.addEventListener("hashchange", () => {
    const p = location.hash.replace("#", "");
    if (PANELS[p]) {
      panel = p;
      render();
    }
  });
  window.addEventListener("resize", () => {
    if (panel === "monitor") drawCharts();
  });
}

(async function main() {
  bind();
  const raw = location.hash.replace("#", "");
  // 深链：#tech/exam/<node> 直接打开考核（复盘、分享、截图都用得上）
  const deep = raw.match(/^tech\/exam\/([A-Za-z0-9_]+)$/);
  const fromHash = deep ? "tech" : raw;
  if (PANELS[fromHash]) panel = fromHash;
  await getState();
  await loadSnapshots();
  await loadMenu();
  render();
  if (deep) {
    openExamModal(deep[1]);
    return;
  }
  // v0.9：默认先给启动主界面，玩家自己选新游戏 / 加载 / 沙盒 / 继续
  renderSplash("home");
})();
