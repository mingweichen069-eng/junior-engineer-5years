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
  const t = el("#toast");
  if (!t) return;
  t.textContent = msg;
  t.className = "toast show " + kind;
  clearTimeout(t._timer);
  t._timer = setTimeout(() => (t.className = "toast " + kind), 4200);
}

function setBusy(on) {
  busy = on;
  document.body.classList.toggle("is-busy", !!on);
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
const FLAVOR = { db: "数据库专家", backend: "后端工程师", sre: "SRE" };

/* 科技树考核目标（与 content/tech.json 的 assess 规则对应） */
const TECH_GOAL = {
  db_t1: "sql 技能 ≥ 20（研究 SQL 两次）",
  db_t2: "在实验台写出一条走索引的区间查询（created_at >= … AND < …）",
  db_t3: "接口 p95 ≤ 400ms",
  be_t1: "backend 技能 ≥ 25",
  be_t2: "在第 12~13 周大促中把可用性保持在 98% 以上",
  be_t3: "周错误率 ≤ 1%",
  sre_t1: "先开启监控（1 AP + ¥300/月）",
  sre_t2: "提前做过扩容/限流准备，并在大促中存活",
  sre_t3: "接口 p95 ≤ 500ms",
};

/* 行动分组（只影响展示，不改后端） */
const ACTION_GROUPS = [
  { key: "db", label: "数据库", ids: ["add_index_user_created", "online_ddl", "optimize_index", "archive_orders", "rewrite_pending"] },
  { key: "cache", label: "缓存", ids: ["cache_dashboard", "audit_cache"] },
  { key: "server", label: "服务器与容量", ids: ["add_server", "consolidate_servers", "pool_60", "add_read_replica"] },
  { key: "ops", label: "运维与文档", ids: ["open_monitoring", "backup_daily", "circuit_breaker", "load_test", "write_docs"] },
  { key: "study", label: "学习与研究", ids: ["study_sql", "study_db", "study_ops"] },
  { key: "change", label: "线上变更", ids: ["rewrite_dashboard"] },
];

const ACTION_RISK = {
  add_index_user_created: "大表 DDL 当周会短时锁写；可用在线 DDL 规避",
  online_ddl: "多花 2 AP；临时磁盘膨胀",
  optimize_index: "重建期间占用 IO",
  archive_orders: "归档数据仍可查但变慢；建议先确认报表需求",
  rewrite_pending: "需要科技树 db_t1；只解决一条查询",
  cache_dashboard: "最多 30 秒脏读，并欠下缓存一致性债",
  audit_cache: "要花一周；期间缓存风险仍在",
  add_server: "只缓解应用层排队，对数据库无效；欠服务器蔓延债",
  consolidate_servers: "流量高峰可能扛不住，先看峰值 QPS",
  pool_60: "池子超过数据库上限会直接拒绝连接",
  add_read_replica: "复制延迟导致轻微不一致；每月 ¥1500",
  open_monitoring: "每月 ¥300，当期没有绩效收益",
  backup_daily: "平时看不出效果，直到真的丢数据",
  circuit_breaker: "需要 ops ≥ 12；对已经打满的数据库无效",
  load_test: "花 ¥100 + 2 AP，换来大促准备标记",
  write_docs: "信任加成递减（+2 → +1 → 0），别指望刷绩效",
  study_sql: "只涨技能，不改变系统",
  study_db: "只涨技能，不改变系统",
  study_ops: "只涨技能，不改变系统",
  rewrite_dashboard: "需在实验台通过编码考核（db_t2）",
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

/* ---------------- 侧栏 ---------------- */

function renderSidebar() {
  const m = S.metrics;
  const s = el("#side-metrics");
  if (s) {
    s.innerHTML = `
      <div class="side-head"><span>实时状态</span><span class="dot ${S.ending.stage === "safe" ? "ok" : "warn"}"></span></div>
      <div class="m-list">
        ${mRow("行动力", `${S.ap} / 5`, S.ap <= 1 ? "warn" : "")}
        ${mRow("现金", money(S.cash), S.cash < 0 ? "bad" : S.cash < 150000 ? "warn" : "")}
        ${mRow("可用性", `${num(m.availability, 2)}%`, m.availability < 99 ? "bad" : m.availability < 99.9 ? "warn" : "")}
        ${mRow("接口 p95", `${num(m.p95, 0)} ms`, m.p95 > 800 ? "bad" : m.p95 > 300 ? "warn" : "")}
        ${mRow("技术债", `${num(S.debt_pressure, 0)}%`, S.debt_pressure > 60 ? "bad" : S.debt_pressure > 40 ? "warn" : "")}
        ${mRow("主管信任", num(S.team.trust, 0), S.team.trust < 45 ? "bad" : S.team.trust < 60 ? "warn" : "")}
      </div>
      <div class="side-foot">${STAGE_LABEL[S.ending.stage]} · 瓶颈：${esc(S.bottleneck.label)}</div>`;
  }

  const st = el("#sysstatus");
  if (st) {
    const rows = [
      ["峰值 QPS", num(m.peak_qps, 1), ""],
      ["磁盘 IOPS", `${num(m.io_util, 0)}%`, sevClass(m.io_util)],
      ["磁盘占用", `${num(m.disk_pct, 1)}%`, sevClass(m.disk_pct)],
      ["连接池 ρ", num(m.pool_rho, 2), m.pool_rho > 0.8 ? "warn" : ""],
      ["客服工单", num(S.team.tickets), S.team.tickets > 100 ? "bad" : S.team.tickets > 30 ? "warn" : ""],
      ["监控", S.team.monitoring ? "已开启" : "未开启", S.team.monitoring ? "" : "warn"],
    ];
    st.innerHTML = `<div class="side-head"><span>系统</span></div>
      ${rows.map(([k, v, c]) => mRow(k, v, c)).join("")}`;
  }

  const feed = el("#eventfeed");
  if (feed) {
    const items = eventTree(S.events).slice(0, 6);
    feed.innerHTML = `<div class="side-head"><span>最近事件</span></div>
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
}

function renderChrome() {
  const m = S.metrics;
  const set = (id, text) => { const n = el("#" + id); if (n) n.textContent = text; };
  set("cash", money(S.cash));
  set("users", num(S.registered));
  set("ap", `${S.ap} / 5`);
  set("avail", `${num(m.availability, 2)}%`);
  const clock = el("#clock");
  if (clock) {
    const skill = S.skills || {};
    clock.textContent =
      `第 ${S.year} 年 ${S.month} 月 · 第 ${S.week_in_month} 周（W${S.week}）` +
      ` · ${DIFF_LABEL[S.difficulty] || S.difficulty}` +
      ` · SQL ${num(skill.sql, 0)} / DB ${num(skill.db, 0)}` +
      ` · v${S.rules_version || "0.5.0"}`;
  }
  renderSidebar();
}

/* ---------------- 主页 ---------------- */

function panelOverview() {
  const m = S.metrics;
  const last = S.months.length ? S.months[S.months.length - 1] : null;
  const weekEvents = S.events.filter((e) => e.week === S.week).length;
  return `
    <div class="page-head">
      <h2>第 ${S.year} 年 ${S.month} 月 · 第 ${S.week_in_month} 周</h2>
      <p>注册 ${num(S.registered)} · 现金 ${money(S.cash)} · ${DIFF_LABEL[S.difficulty] || S.difficulty}难度${S.sandbox ? " · 沙盘模式" : ""}</p>
    </div>
    ${(S.blocked || []).map((b) => `<div class="banner">${esc(b)}</div>`).join("")}
    ${S.sandbox ? '<div class="banner info">沙盘模式：AP 无限、不会破产、不结算 A 轮。用来验证参数。</div>' : ""}
    <div class="banner ${S.bottleneck.code === "healthy" ? "" : "list"}">
      当前主瓶颈：<b>${esc(S.bottleneck.label)}</b> —— ${esc(S.bottleneck.hint)}
    </div>
    <div class="kpi-grid">
      ${kpi("现金", money(S.cash), "", S.cash < 0 ? "bad" : S.cash < 150000 ? "warn" : "")}
      ${kpi("可用性", num(m.availability, 2), "%", m.availability < 99 ? "bad" : m.availability < 99.9 ? "warn" : "ok")}
      ${kpi("接口 p95", num(m.p95, 0), "ms", m.p95 > 800 ? "bad" : m.p95 > 300 ? "warn" : "ok")}
      ${kpi("技术债压力", num(S.debt_pressure, 0), "%", S.debt_pressure > 60 ? "bad" : S.debt_pressure > 40 ? "warn" : "ok")}
    </div>
    <div class="grid-2">
      <section class="card">
        <h3>本周概览</h3>
        <div class="kv">
          ${mRow("注册用户", num(S.registered))}
          ${mRow("峰值 QPS", num(m.peak_qps, 1))}
          ${mRow("磁盘占用", `${num(m.disk_gb, 2)} GB / ${num(m.disk_pct, 1)}%`)}
          ${mRow("连接池 ρ", num(m.pool_rho, 2))}
          ${mRow("主管信任", num(S.team.trust, 0) + " / 100")}
          ${mRow("本周事件", `${weekEvents} 条`)}
        </div>
      </section>
      <section class="card">
        <h3>财务与团队</h3>
        <div class="kv">
          ${mRow("最近月利润", last ? money(last.profit) : "--", last && last.profit < 0 ? "bad" : "")}
          ${mRow("最近月可用性", last ? `${num(last.availability, 2)}%` : "--")}
          ${mRow("SLA 赔偿", last && last.penalty ? money(last.penalty) : "--")}
          ${mRow("客服工单", num(S.team.tickets), S.team.tickets > 100 ? "bad" : "")}
          ${mRow("文档", `${S.team.docs} 份`)}
        </div>
      </section>
    </div>
    <section class="card">
      <h3>事件流（点击查看详情）</h3>
      <div class="feed">
        ${
          S.events.length
            ? eventTree(S.events)
                .slice(0, 10)
                .map(
                  (e) => `<div class="feed-item ${esc(e.tag || "fault")} indent-${Math.min(3, e.depth || 0)}" data-event="${esc(e.id)}">
                    <div class="meta">W${e.week} D${e.day} · ${esc(e.event)}${e.cause ? ` · ← ${esc(e.cause)}` : ""}</div>
                    <div class="title">${esc(e.title)}</div>
                  </div>`
                )
                .join("")
            : '<div class="muted">还没有触发事件</div>'
        }
      </div>
    </section>`;
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
  return `<div class="action">
    <div class="body">
      <div class="name">${esc(a.name)}
        <span class="tag">${a.repeatable ? "可重复" : "一次性"}</span>
        ${a.times ? `<span class="tag">已做 ${a.times} 次</span>` : ""}
      </div>
      <div class="desc">${esc(a.desc)}</div>
      <div class="cost">AP ${a.ap}${a.cash ? ` · ${money(a.cash)}` : ""}${a.weeks ? ` · ${a.weeks} 周后生效` : " · 立即生效"}</div>
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
    <div class="page-head"><h2>行动</h2><p>本周剩余 ${S.ap} 点行动力。一次性动作做过就没了；可重复动作成本递增。</p></div>
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

/* ---------------- 实验台 ---------------- */

const DEFAULT_SQL = `SELECT * FROM orders
WHERE user_id = 12345
ORDER BY created_at DESC
LIMIT 20`;

let labSql = DEFAULT_SQL;
let labResult = null;
let labAutoRan = false;

const SQL_KEYWORDS = /^(SELECT|FROM|WHERE|AND|OR|NOT|ORDER|BY|LIMIT|OFFSET|GROUP|HAVING|JOIN|LEFT|RIGHT|INNER|OUTER|ON|AS|COUNT|SUM|AVG|MIN|MAX|DESC|ASC|CREATE|INDEX|UNIQUE|DATE|BETWEEN|IN|IS|NULL|LIKE|CASE|WHEN|THEN|ELSE|END|EXPLAIN|QUERY|PLAN|INSERT|INTO|VALUES|UPDATE|SET|DELETE|WITH|UNION|ALL)$/i;

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
  if (u.includes("USING INDEX")) return { cls: "warn", label: "索引扫描" };
  if (u.startsWith("SCAN")) return { cls: "bad", label: "全表扫描" };
  return { cls: "", label: "其他" };
}

function panelLab() {
  const r = labResult;
  const idx = S.actions.find((a) => a.id === "add_index_user_created");
  const rw = S.actions.find((a) => a.id === "rewrite_dashboard");
  const plan = r && r.plan ? r.plan : [];
  const warnings = r && r.warnings ? r.warnings : [];
  const rows = r && r.rows ? r.rows : [];
  const cols = r && r.columns ? r.columns : [];
  const proofs = S.proofs || {};
  const proved = !!proofs.lab_proof_rewrite;
  const db2 = ((S.tech || {}).nodes || []).find((n) => n.id === "db_t2");
  const db2done = !!(db2 && db2.unlocked);
  return `
    <div class="page-head"><h2>实验台 · 编码区</h2>
      <p>真实 SQLite（20 万订单副本）；执行计划是真的。生产环境仍是模型：结论可迁移，毫秒数不可迁移。</p></div>
    <div class="grid-2">
      <section class="card">
        <h3>SQL 编辑器</h3>
        <div class="editor">
          <pre class="hl" id="sqlhl" aria-hidden="true"></pre>
          <textarea id="sqlbox" spellcheck="false" autocomplete="off">${esc(labSql)}</textarea>
        </div>
        <div class="row-actions">
          <button id="run-sql" class="primary">运行</button>
          <button id="reset-lab">重置实验环境</button>
          ${proved ? '<button data-goto="tech" class="primary">前往科技树提交考核 →</button>' : ""}
        </div>
      </section>
      <section class="card assess ${proved ? "ok" : ""}">
        <h3>考核目标 · db_t2</h3>
        <div class="assess-goal">
          写出一条走索引的区间查询：<br>
          <code>WHERE created_at &gt;= '2024-01-31' AND created_at &lt; '2024-02-01'</code><br>
          通过条件：执行计划出现 <code>SEARCH … USING INDEX</code> 或 <code>COVERING INDEX</code>。
        </div>
        <div class="assess-status ${db2done || proved ? "ok" : ""}">${
          db2done
            ? "✓ db_t2 已解锁"
            : proved
            ? "✓ 凭证已记录，前往科技树提交考核"
            : "○ 尚未记录凭证"
        }</div>
        ${db2done
          ? '<div class="muted" style="font-size:11.5px;margin-top:6px">该科技节点已完成本局考核。</div>'
          : proved
          ? '<div class="muted" style="font-size:11.5px;margin-top:6px">科技树 → 数据库专家 → db_t2 → 点「考核」</div>'
          : ""}
      </section>
    </div>
    <div class="grid-2">
      <section class="card">
        <h3>执行计划</h3>
        ${
          plan.length
            ? plan
                .map((p) => {
                  const c = planClass(p.detail);
                  return `<div class="plan-line ${c.cls}"><span class="badge ${c.cls}">${c.label}</span><code>${esc(p.detail)}</code></div>`;
                })
                .join("")
            : '<div class="muted" style="font-size:12px">运行后显示 EXPLAIN QUERY PLAN</div>'
        }
      </section>
      <section class="card">
        <h3>判定结果</h3>
        ${
          !r
            ? '<div class="verdict idle">运行 SQL 后给出判定。</div>'
            : !r.ok
            ? `<div class="verdict bad">${esc(r.error)}</div>`
            : r.used_index
            ? '<div class="verdict ok">✓ 通过：本次执行用上了索引</div>'
            : '<div class="verdict bad">✗ 未通过：执行计划没有用上索引</div>'
        }
        ${
          warnings.length
            ? warnings
                .map((w) => `<div class="warn" style="font-size:11.5px;margin-top:6px;color:${/没有|失效|全表/.test(w) ? "var(--bad)" : "var(--muted)"}">· ${esc(w)}</div>`)
                .join("")
            : ""
        }
        ${r && r.ok ? `<div class="muted" style="margin-top:10px;font-size:11px">${r.row_count} 行 · ${r.elapsed_ms} ms${r.truncated ? "（已截断）" : ""}</div>` : ""}
      </section>
    </div>
    ${
      rows.length
        ? `<section class="card" style="margin-bottom:14px"><h3>结果</h3>
            <div class="table-wrap"><table><thead><tr>${cols.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead>
            <tbody>${rows
              .map((row) => `<tr>${row.map((v) => `<td class="num">${esc(v == null ? "NULL" : String(v))}</td>`).join("")}</tr>`)
              .join("")}</tbody></table></div></section>`
        : ""
    }
    <section class="card">
      <h3>发布到生产</h3>
      <p class="muted" style="font-size:12px;margin:0 0 8px">实验台验证通过后，再把结构化变更发布到生产。发布要付行动力，并按周生效。</p>
      <div class="deploy-row">
        <button data-deploy="index_user_created" ${idx && idx.ready ? "" : "disabled"}>发布复合索引</button>
        <span class="muted" style="font-size:11.5px">${idx ? (idx.ready ? `AP ${idx.ap} · ${idx.weeks} 周后生效` : esc(idx.reason)) : ""}</span>
      </div>
      <div class="deploy-row">
        <button data-deploy="rewrite_dashboard" ${rw && rw.ready ? "" : "disabled"}>发布大屏 SQL 改写</button>
        <span class="muted" style="font-size:11.5px">${rw ? (rw.ready ? `AP ${rw.ap} · 立即生效` : esc(rw.reason)) : ""}</span>
      </div>
    </section>`;
}

function syncEditor() {
  const box = el("#sqlbox");
  const hl = el("#sqlhl");
  if (!box || !hl) return;
  const paint = () => {
    hl.innerHTML = highlightSql(box.value) + "\n";
    hl.scrollTop = box.scrollTop;
    hl.scrollLeft = box.scrollLeft;
  };
  paint();
  box.addEventListener("input", () => {
    labSql = box.value;
    paint();
  });
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

async function runLab() {
  const box = el("#sqlbox");
  labSql = box ? box.value : labSql;
  setBusy(true);
  const j = await post("/api/lab/query", { sql: labSql });
  setBusy(false);
  labResult = j;
  if (j.proof_granted && j.proof_granted.length) {
    toast("✓ 凭证已记录：前往「科技树」提交考核", "ok");
    const st = await fetch("/api/state").then((x) => x.json());
    if (st.ok) S = st.data;
  }
  render();
  syncEditor();
}

async function resetLab() {
  await post("/api/lab/reset", {});
  labResult = null;
  toast("实验环境已重置");
  render();
  syncEditor();
}

async function deployLab(kind) {
  const j = await post("/api/lab/deploy", { kind });
  toast(j.message || "已发布");
  if (j.data) {
    S = j.data;
    render();
  }
}

/* ---------------- 科技树 ---------------- */

function panelTech() {
  const tech = S.tech || { branches: {}, nodes: [], direction: "" };
  const branches = Object.keys(tech.branches);
  const byBranch = {};
  branches.forEach((b) => (byBranch[b] = tech.nodes.filter((n) => n.branch === b)));
  const readyNow = tech.nodes.filter((n) => n.ready && n.req_ok && !n.unlocked);
  const nextUp = tech.nodes.filter((n) => !n.unlocked && !n.ready && n.req_ok);
  const allDone = tech.nodes.every((n) => n.unlocked);
  const prog = S.proofs || {};

  const hint =
    readyNow.length
      ? `<div class="line">当前可考核：<b>${readyNow.map((n) => esc(n.name)).join("、")}</b></div>
         ${readyNow
           .map((n) => {
             const g = TECH_GOAL[n.id] || n.unlocks_note || "按节点要求完成考核";
             const extra =
               n.id === "db_t2"
                 ? `<div class="line">凭证：<b style="color:${prog.lab_proof_rewrite ? "var(--ok)" : "var(--warn)"}">${prog.lab_proof_rewrite ? "已记录" : "未记录"}</b>${prog.lab_proof_rewrite ? "，可以直接提交考核" : "，先去实验台写一条走索引的区间查询"}</div>`
                 : "";
             return `<div class="line">· 目标：${esc(g)}</div>${extra}`;
           })
           .join("")}`
      : allDone
      ? '<div class="line">本方向已全部解锁。转向其他方向时，第一个节点的学习成本翻倍。</div>'
      : nextUp.length
      ? `<div class="line">下一步：继续读条 <b>${esc(nextUp[0].name)}</b>（${nextUp[0].progress}/${nextUp[0].ap}）· 目标：${esc(TECH_GOAL[nextUp[0].id] || "完成考核")}</div>`
      : '<div class="line">暂无可推进节点：先满足技能或前置条件。</div>';

  const nodeHtml = (n) => {
    const state = n.unlocked ? "done" : !n.req_ok ? "locked" : n.ready ? "ready" : "";
    const pct = Math.min(100, Math.round((n.progress / n.ap) * 100));
    const badge = n.unlocked ? '<span class="tag ok">已通过</span>' : n.ready ? '<span class="tag warn">可考核</span>' : '<span class="tag">未解锁</span>';
    return `<article class="tnode ${state}">
      <div class="tnode-head"><span class="tier">T${n.tier}</span><b>${esc(n.name)}</b>${badge}</div>
      <p class="tnode-desc">${esc(n.desc)}</p>
      <div class="bar ${n.unlocked ? "ok" : ""}"><i style="width:${n.unlocked ? 100 : pct}%"></i></div>
      <div class="tnode-goal">考核目标：${esc(TECH_GOAL[n.id] || n.unlocks_note || "完成节点要求")}</div>
      ${n.unlocks_note ? `<div class="muted" style="font-size:10.5px">${esc(n.unlocks_note)}</div>` : ""}
      <div class="tnode-actions">
        ${
          !n.unlocked && n.req_ok
            ? n.ready
              ? `<button class="primary" data-tech-assess="${esc(n.id)}">考核</button>`
              : `<button data-tech-study="${esc(n.id)}">研究（1 AP）</button>`
            : ""
        }
        ${!n.req_ok ? `<span class="muted" style="font-size:10.5px">需先解锁：${n.requires.join("、")}</span>` : ""}
      </div>
    </article>`;
  };

  return `
    <div class="page-head"><h2>科技树</h2>
      <p>读条只攒「考核机会」，必须通过考核才真正掌握。当前方向：<b>${esc(tech.direction_label || "未定")}</b>。转向其他方向时第一个节点成本翻倍。</p></div>
    <section class="card tech-hint">${hint}</section>
    <div class="tech-graph">
      ${branches
        .map(
          (b) => `<section class="tech-branch">
            <div class="branch-head"><b>${esc(tech.branches[b].name)}</b><span class="tag">${byBranch[b].filter((n) => n.unlocked).length}/${byBranch[b].length}</span></div>
            <p class="branch-desc">${esc(tech.branches[b].desc)}</p>
            <div class="tflow">${byBranch[b].map(nodeHtml).join("")}</div>
          </section>`
        )
        .join("")}
    </div>`;
}

async function techStudy(id) {
  if (busy) return;
  setBusy(true);
  const j = await post("/api/tech/study", { node: id });
  setBusy(false);
  toast(j.message, j.ok ? "" : "bad");
  if (j.data) {
    S = j.data;
    render();
  }
}

async function techAssess(id) {
  if (busy) return;
  setBusy(true);
  const j = await post("/api/tech/assess", { node: id });
  setBusy(false);
  toast(j.message, j.ok ? "ok" : "bad");
  if (j.data) {
    S = j.data;
    render();
  }
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
  overview: panelOverview,
  monitor: panelMonitor,
  actions: panelActions,
  lab: panelLab,
  tech: panelTech,
  docs: panelDocs,
  career: panelCareer,
};

function render() {
  if (!S) return;
  renderChrome();
  const center = el("#center");
  if (!center) return;
  if (S.ending.ended) {
    center.innerHTML = endingScreen();
    const a1 = el("#adv1"), a4 = el("#adv4");
    if (a1) a1.disabled = true;
    if (a4) a4.disabled = true;
    return;
  }
  const a1 = el("#adv1"), a4 = el("#adv4");
  if (a1) a1.disabled = false;
  if (a4) a4.disabled = false;
  center.innerHTML = endingBanner() + (PANELS[panel] || panelOverview)();
  center.scrollTop = 0;
  if (panel === "monitor") drawCharts();
  if (panel === "lab") {
    syncEditor();
    if (!labResult && !labAutoRan) {
      labAutoRan = true;
      runLab();
    }
  }
  const nav = el("#nav");
  if (nav) {
    nav.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.panel === panel));
  }
}

/* ---------------- 事件弹窗（分支 UI 预留） ---------------- */

function openEventModal(id) {
  const e = (S.events || []).find((x) => x.id === id);
  if (!e) return;
  const root = el("#modal-root");
  const opts = [
    "接受需求，按现有架构硬扛（性能承压）",
    "拒绝需求（产品满意度下降，融资扣分）",
    "折中：异步处理 / 排期到下一迭代（消耗人力 AP）",
  ];
  root.innerHTML = `<div class="modal-mask">
    <div class="modal">
      <h3>${esc(e.title)}</h3>
      <div class="meta">W${e.week} D${e.day} · ${esc(e.event)} · ${esc(e.tag || "fault")}${e.cause ? ` · 来自 ${esc(e.cause)}` : ""}</div>
      <div class="body">${Object.entries(e.effects || {})
        .map(([k, v]) => `${esc(k)} = ${esc(typeof v === "number" ? num(v, Number.isInteger(v) ? 0 : 2) : v)}`)
        .join(" · ") || "本周没有直接数值影响。"}</div>
      ${opts
        .map(
          (o) => `<label class="opt"><input type="checkbox" disabled><span>${esc(o)}</span></label>`
        )
        .join("")}
      <div class="reserved">分支选择将在后续版本开放；当前事件结果由世界自动结算。</div>
      <div class="row-actions" style="justify-content:flex-end">
        <button data-close="1" class="primary">关闭</button>
      </div>
    </div>
  </div>`;
  root.classList.add("open");
}

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
  if (!j.ok) return toast("推进失败", "bad");
  S = j.data;
  render();
  toast(`推进 ${S.week - before} 周 → W${S.week}`);
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
  labResult = null;
  labAutoRan = false;
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
  const j = await post("/api/new", { difficulty: diff });
  setBusy(false);
  if (!j.ok) return toast("新建失败", "bad");
  S = j.data;
  labResult = null;
  labAutoRan = false;
  panel = "overview";
  history.replaceState(null, "", "#overview");
  render();
  toast(`难度：${DIFF_LABEL[diff] || diff}`);
}

async function newGame() {
  if (busy) return;
  if (!window.confirm("放弃当前进度，开始新游戏？")) return;
  openNewGameModal();
}

function bind() {
  const nav = el("#nav");
  if (nav) {
    nav.addEventListener("click", (e) => {
      const b = e.target.closest("button[data-panel]");
      if (!b) return;
      panel = b.dataset.panel;
      history.replaceState(null, "", "#" + panel);
      render();
    });
  }

  const center = el("#center");
  if (center) {
    center.addEventListener("click", (e) => {
      if (e.target.id === "restart") return newGame();
      if (e.target.id === "run-sql") return runLab();
      if (e.target.id === "reset-lab") return resetLab();

      const tag = e.target.closest("button[data-tag]");
      if (tag) {
        eventFilter = tag.dataset.tag;
        render();
        return;
      }
      const af = e.target.closest("button[data-afilter]");
      if (af) {
        actionFilter = af.dataset.afilter;
        render();
        return;
      }
      const mp = e.target.closest("button[data-mpage]");
      if (mp) {
        monitorPage = Math.max(0, Number(mp.dataset.mpage));
        render();
        drawCharts();
        return;
      }
      const ts = e.target.closest("button[data-tech-study]");
      if (ts) return techStudy(ts.dataset.techStudy);
      const ta = e.target.closest("button[data-tech-assess]");
      if (ta) return techAssess(ta.dataset.techAssess);
      const go = e.target.closest("button[data-goto]");
      if (go) {
        panel = go.dataset.goto;
        history.replaceState(null, "", "#" + panel);
        render();
        return;
      }
      const rb = e.target.closest("button[data-rollback]");
      if (rb) return rollback(rb.dataset.rollback);
      const deploy = e.target.closest("button[data-deploy]");
      if (deploy) return deployLab(deploy.dataset.deploy);
      const b = e.target.closest("button[data-action]");
      if (b) return doAction(b.dataset.action);
      const ev = e.target.closest("[data-event]");
      if (ev && ev.dataset.event) return openEventModal(ev.dataset.event);
    });
  }

  const adv1 = el("#adv1");
  const adv4 = el("#adv4");
  if (adv1) adv1.addEventListener("click", () => advance(1));
  if (adv4) adv4.addEventListener("click", () => advance(4));
  const ng = el("#newgame");
  if (ng) ng.addEventListener("click", newGame);
  const sb = el("#sandbox");
  if (sb) sb.addEventListener("click", startSandbox);

  const root = el("#modal-root");
  if (root) {
    root.addEventListener("click", (e) => {
      if (e.target.classList.contains("modal-mask") || e.target.closest("button[data-close]")) {
        closeModal();
        return;
      }
      const diff = e.target.closest("button[data-difficulty]");
      if (diff) startNewGame(diff.dataset.difficulty);
    });
  }

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
  const fromHash = location.hash.replace("#", "");
  if (PANELS[fromHash]) panel = fromHash;
  await getState();
  await loadSnapshots();
  render();
})();
