// 临时 UI 结构校验：在 Node 里用 DOM 桩执行 app.js，逐面板检查渲染结果。
// v1.0：考核弹窗 / 读条 / 解锁全部下线，改验技术档案、工程工作台、三维分离。
import { readFileSync } from "node:fs";
import vm from "node:vm";

const ctxStub = new Proxy(
  {},
  {
    get: (t, k) => {
      if (k === "canvas") return { width: 300, height: 120 };
      return () => {};
    },
    set: () => true,
  }
);

const els = new Map();
function mkEl(sel) {
  return {
    _sel: sel,
    innerHTML: "",
    textContent: "",
    className: "",
    disabled: false,
    value: "",
    scrollTop: 0,
    classList: { toggle() {}, add() {}, remove() {}, contains: () => false },
    addEventListener() {},
    querySelectorAll: () => [],
    querySelector: () => mkEl(sel + " > *"),
    getBoundingClientRect: () => ({ width: 320, height: 120 }),
    getContext: () => ctxStub,
  };
}
function q(sel) {
  if (!els.has(sel)) els.set(sel, mkEl(sel));
  return els.get(sel);
}

const listeners = { click: [], keydown: [] };
globalThis.document = {
  querySelector: q,
  addEventListener(type, fn) {
    (listeners[type] = listeners[type] || []).push(fn);
  },
  createElement: (tag) => mkEl(tag),
  body: { classList: { toggle() {}, add() {}, remove() {} }, appendChild() {} },
};
globalThis.window = {
  devicePixelRatio: 1,
  addEventListener() {},
  confirm: () => true,
  prompt: () => "2",
};
globalThis.location = { hash: "" };
globalThis.history = { replaceState() {} };
globalThis.setTimeout = setTimeout;
globalThis.clearTimeout = clearTimeout;
globalThis.setInterval = () => 0;
globalThis.clearInterval = () => {};

const src = readFileSync(new URL("../web/static/app.js", import.meta.url), "utf8");
const BASE = "http://127.0.0.1:" + (process.env.PORT || "5000");
const realFetch = globalThis.fetch;
const calls = [];
globalThis.fetch = (u, o) => {
  const url = String(u).startsWith("http") ? String(u) : BASE + u;
  if (url.includes("/api/")) calls.push({ url: url.replace(BASE, ""), body: o && o.body ? String(o.body) : "" });
  return realFetch(url, o);
};
vm.runInThisContext(src, { filename: "app.js" });

await new Promise((r) => setTimeout(r, 1200));

const state = await (await fetch(BASE + "/api/state")).json();
if (!state.ok) throw new Error("state api failed");
globalThis.__S = state.data;
vm.runInThisContext("S = __S", { filename: "inject.js" });

function renderPanel(p) {
  vm.runInThisContext(`panel = ${JSON.stringify(p)}; render();`, { filename: "render.js" });
  return q("#center").innerHTML;
}

const problems = [];
function check(name, html, needles) {
  for (const n of needles) if (!html.includes(n)) problems.push(`${name}: missing ${n}`);
  if (html.includes("undefined")) problems.push(`${name}: contains undefined`);
}

const overview = renderPanel("overview");
check("overview", overview, ["服务拓扑", "透镜", "本周诊断", "map-node", "本周数字", "账本"]);
check("hud", q("#resbar").innerHTML, ["现金", "行动力", "调查精力", "注册用户", "可用性", "技术债", "主管信任"]);
check("inspector", q("#inspect").innerHTML, ["公司处境", "主瓶颈", "技术债构成", "上月月结"]);
check("feed", q("#eventfeed").innerHTML, ["最近事件"]);

vm.runInThisContext(`lens = "risk"; render();`, { filename: "lens.js" });
check("lens-risk", q("#center").innerHTML, ["技术债压力", "容量水位"]);
vm.runInThisContext(`lens = "latency";`, { filename: "lens2.js" });

vm.runInThisContext(`selectedNode = "db"; render();`, { filename: "node.js" });
check("node-db", q("#inspect").innerHTML, ["真实世界对应", "需开启监控", "怎么读这个指标", "返回全局"]);
vm.runInThisContext(`selectedNode = null; render();`, { filename: "node2.js" });

const monitor = renderPanel("monitor");
check("monitor", monitor, ["慢查询", "ch-avail", "ch-p95", "ch-profit", "ch-io", "表与索引"]);
if (!monitor.includes("pager") && !monitor.includes("开启监控")) problems.push("monitor: no pager and no locked notice");

const actions = renderPanel("actions");
check("actions", actions, ["数据库结构", "缓存", "容量与硬件", "运维与治理", "自动化", "风险：", "可重复"]);
if (actions.includes("研究 SQL")) problems.push("actions: 还有纯刷技能的动作");
check("hud-ap", q("#resbar").innerHTML, ["行动力", "apbar", "/ "]);

// 技术档案：不再有读条 / 考核 / 解锁，只记录接触 / 实验 / 部署
const tech = renderPanel("tech");
check("tech", tech, [
  "技术档案", "tree-canvas", "tlane", "tnode3", "计算机基础", "性能工程", "算法工程",
  "知识不是奖励", "不解锁任何东西", "三个维度", "公司资产", "角色履历", "去工作台",
]);
if (tech.includes("开始读条") || tech.includes("参加考核")) problems.push("tech: 还有读条/考核入口");

// 工程工作台：调查 → 实验 → 部署，没有交卷
const workbench = renderPanel("workbench");
check("workbench", workbench, [
  "工程工作台", "监控 → 看执行计划", "没有读条、没有考核", "慢查询", "调查：慢查询与索引",
  "SQL 实验库", "真实 SQLite", "4,000 行轻量副本", "看执行计划", "在实验库跑", "部署到生产", "重置实验库",
  "查询改写：提交你自己的 SQL", "原始查询（只读）", "在实验库对比验证", "没有预设答案按钮",
  "生产回归用例", "跑回归并部署", "已部署到生产的资产", "公司资产",
]);
if (!workbench.includes("wb-sql") || !workbench.includes("wb-fix") || !workbench.includes("wb-rewrite-sql"))
  problems.push("workbench: SQL / 改写 / 代码编辑器没渲染");
if (workbench.includes("把 DATE() 改成区间条件"))
  problems.push("workbench: 还在用预设答案按钮替代玩家写 SQL");

const know = renderPanel("knowledge");
check("knowledge", know, ["内部知识库", "知识不是奖励", "全文开放", "kgroup", "kcard", "know-search", "记一笔"]);
if (know.includes("解锁全文") || know.includes("未解锁")) problems.push("knowledge: 还在把知识当奖励发");

const career = renderPanel("career");
check("career", career, ["动作快照", "导出存档", "月度记录", "阶段性述职", "只作参考", "调整评价周"]);

// 技术档案的四种状态（只是记录，不 gate 任何操作）
const st2 = JSON.parse(JSON.stringify(state.data));
const n1 = st2.tech.nodes.find((n) => n.id === "db_t1");
const n2 = st2.tech.nodes.find((n) => n.id === "db_t2");
const n3 = st2.tech.nodes.find((n) => n.id === "db_mvcc");
if (n1) Object.assign(n1, { state: "deployed", deployed: true, experimented: true, seen: true });
if (n2) Object.assign(n2, { state: "experimented", experimented: true, seen: true });
if (n3) Object.assign(n3, { state: "seen", seen: true, incident: true });
vm.runInThisContext(`S = ${JSON.stringify(st2)};`, { filename: "archive.js" });
const tech2 = renderPanel("tech");
check("archive-states", tech2, ["已部署", "实验过", "接触过", "未接触", "出过事故"]);
vm.runInThisContext(`openTechModal("db_t2");`, { filename: "archivemodal.js" });
const tmodal = q("#modal-root").innerHTML;
check("archive-modal", tmodal, ["技术档案", "这不是解锁项", "去工作台动手"]);
vm.runInThisContext(`S = __S;`, { filename: "restore.js" });

// 事件报告弹窗
vm.runInThisContext(`S.events = [{id:"T1",week:1,day:1,event:"E01",title:"测试事件",tag:"fault",effects:{tickets:2},depth:0,narrative:"监控红了。",speaker:"值班"}];
S.decisions = [];`, { filename: "ev.js" });
vm.runInThisContext(`openEventModal("T1");`, { filename: "modal.js" });
const modal = q("#modal-root").innerHTML;
check("modal", modal, ["测试事件", "监控红了。", "工程上该怎么读", "知道了", "v3-art"]);

// V3 式决策弹窗
vm.runInThisContext(
  `S.decisions = [{id:"W5D3#D0",event_id:"D01",week:5,day:3,title:"编制：一个 HC",tag:"team",speaker:"周明远",narrative:"只批一个 HC。",stakes:"用掉就没有第二次。",choices:[{id:"senior",label:"招资深 DBA",hint:"立刻能上手",effects:["现金 -¥30,000","数据库 +4"],default:false},{id:"none",label:"不招",hint:"留现金",effects:["信任 -2"],default:true}]}];
openDecisionModal(S.decisions[0]);`,
  { filename: "decision.js" }
);
const dmodal = q("#modal-root").innerHTML;
check("decision-modal", dmodal, ["编制：一个 HC", "只批一个 HC", "用掉就没有第二次", "招资深 DBA", "现金 -¥30,000", "默认", "data-choice", "世界不会等你"]);

// 新游戏难度选择弹窗
vm.runInThisContext(`closeModal(); openNewGameModal();`, { filename: "newgame.js" });
const ngModal = q("#modal-root").innerHTML;
check("newgame-modal", ngModal, ["简单", "普通", "困难", "老手", 'data-difficulty="easy"', 'data-difficulty="veteran"']);

// ================= 全按钮点击回归 =================
const indexHtml = readFileSync(new URL("../web/templates/index.html", import.meta.url), "utf8");

function fakeTarget(attrs = {}, id = "") {
  const ds = {};
  for (const [k, v] of Object.entries(attrs)) {
    const camel = k.replace(/^data-/, "").replace(/-([a-z])/g, (_, c) => c.toUpperCase());
    ds[camel] = v;
    ds[k] = v;
  }
  return {
    tagName: "BUTTON",
    id,
    dataset: ds,
    disabled: !!attrs.disabled,
    classList: { contains: () => false, add() {}, remove() {}, toggle() {} },
    closest(sel) {
      if (sel.startsWith("#")) return sel.slice(1) === id ? this : null;
      const m = sel.match(/^(?:button)?\[data-([a-zA-Z0-9-]+)\]$/);
      if (m) return Object.prototype.hasOwnProperty.call(attrs, "data-" + m[1]) ? this : null;
      const m2 = sel.match(/^\[data-([a-zA-Z0-9-]+)\]$/);
      if (m2) return Object.prototype.hasOwnProperty.call(attrs, "data-" + m2[1]) ? this : null;
      return null;
    },
  };
}
function clickOn(attrs, id = "") {
  const t = fakeTarget(attrs, id);
  for (const fn of listeners.click || []) {
    try {
      fn({ target: t, preventDefault() {}, relatedTarget: null });
    } catch (err) {
      problems.push(`click-throw: ${String(err)}`);
    }
  }
}
function expectCall(name, path, bodyNeedle = "") {
  const hit = calls.find((c) => c.url.includes(path));
  if (!hit) problems.push(`click-${name}: 没发出 ${path}`);
  else if (bodyNeedle && !(hit.body || "").includes(bodyNeedle)) problems.push(`click-${name}: ${path} 请求体里没有 ${bodyNeedle}`);
}
const PASSIVE = ["/api/menu", "/api/state", "/api/snapshots", "/api/slots", "/api/profile"];
function expectNoCall(name) {
  const active = calls.filter((c) => !PASSIVE.some((p) => c.url.startsWith(p)) && c.url !== "/api/review");
  if (active.length) problems.push(`click-${name}: 本不该发请求，却发了 ${active.map((c) => c.url).join(",")}`);
}

const cases = [
  ["推进一周（data-speed=1）", { "data-speed": "1" }, "/api/advance", '"weeks":1'],
  ["推进四周（data-speed=4）", { "data-speed": "4" }, "/api/advance", '"weeks":4'],
  ["自动推进", { "data-speed": "auto" }, null, ""],
  ["暂停", { "data-speed": "pause" }, null, ""],
  ["左侧导航", { "data-panel": "tech" }, null, ""],
  ["工作台导航", { "data-panel": "workbench" }, null, ""],
  ["技术档案节点", { "data-tech-node": "db_t1" }, null, ""],
  ["执行动作", { "data-action": "add_index" }, "/api/action", ""],
  ["禁用的动作", { "data-action": "add_index", disabled: true }, null, ""],
  ["透镜切换", { "data-lens": "risk" }, null, ""],
  ["知识筛选", { "data-knowledge-filter": "read" }, null, ""],
  ["知识搜索", { "data-know-search": "1" }, null, ""],
  ["记一笔看过", { "data-wb-read": "k_arch_hierarchy" }, "/api/workbench/read-card", ""],
  ["动作筛选", { "data-afilter": "all" }, null, ""],
  ["监控翻页", { "data-mpage": "1" }, null, ""],
  ["跳转", { "data-goto": "workbench" }, null, ""],
  ["关闭弹窗", { "data-close": "1" }, null, ""],
  ["难度选择", { "data-difficulty": "normal" }, "/api/new", ""],
  ["决策选项", { "data-choice": "senior", "data-decision": "D01" }, "/api/event/choose", ""],
  ["回退快照", { "data-rollback": "1" }, "/api/rollback", ""],
  ["看执行计划", { "data-wb-run": "inspect" }, "/api/workbench/inspect", ""],
  ["在实验库跑", { "data-wb-run": "experiment" }, "/api/workbench/experiment", ""],
  ["部署索引", { "data-wb-run": "deploy" }, "/api/workbench/deploy-index", ""],
  ["重置实验库", { "data-wb-run": "reset" }, "/api/workbench/reset-lab", ""],
  ["改写对比验证", { "data-wb-check-rewrite": "q_dashboard" }, "/api/workbench/check-rewrite", ""],
  ["改写部署", { "data-wb-deploy-rewrite": "q_dashboard" }, "/api/workbench/deploy-rewrite", ""],
  ["跑回归并部署", { "data-wb-fix": "1" }, "/api/workbench/deploy-fix", ""],
  ["选修复用例", { "data-wb-case": "ex_fn_idempo" }, null, ""],
  ["找同事咨询", { "data-wb-consult": "M01_dashboard_degrade" }, "/api/events/consult", ""],
  ["主界面页签", { "data-menu-view": "new" }, null, ""],
  ["述职继续游玩", { "data-review-continue": "1" }, "/api/review/continue", ""],
];
for (const [name, attrs, path, needle] of cases) {
  calls.length = 0;
  clickOn(attrs);
  if (path) expectCall(name, path, needle);
  else expectNoCall(name);
  await new Promise((r) => setTimeout(r, 320));
}
calls.length = 0;
clickOn({}, "newgame");
await new Promise((r) => setTimeout(r, 250));
if (!q("#splash-root").innerHTML.includes("新的游戏")) problems.push("click-新档案: 没打开主界面的新游戏页");
expectNoCall("新档案");
await new Promise((r) => setTimeout(r, 320));
calls.length = 0;
clickOn({}, "sandbox");
expectCall("沙盘", "/api/new");
await new Promise((r) => setTimeout(r, 320));
calls.length = 0;
clickOn({}, "restart");
await new Promise((r) => setTimeout(r, 250));
if (!q("#splash-root").innerHTML.includes("新的游戏")) problems.push("click-restart: 没回到主界面");
expectNoCall("restart");

// 覆盖完整性
const HANDLED = new Set([
  "data-panel", "data-speed", "data-tech-node", "data-choice", "data-action",
  "data-lens", "data-unselect", "data-knowledge-filter", "data-know-search",
  "data-afilter", "data-mpage", "data-rollback", "data-node", "data-event", "data-goto",
  "data-difficulty", "data-close", "data-tip", "data-decision",
  "data-review-config", "data-review-open", "data-review-continue",
  "data-wb-run", "data-wb-check-rewrite", "data-wb-deploy-rewrite", "data-wb-fix", "data-wb-case",
  "data-wb-consult", "data-wb-read",
  "id-newgame", "id-sandbox", "id-restart", "id-menu",
  "data-menu-view", "data-menu-diff", "data-menu-review", "data-menu-start",
  "data-menu-sandbox", "data-menu-slot", "data-menu-rename", "data-menu-del",
  "data-menu-save-settings", "data-menu-continue", "data-menu-close",
]);
const panelsHtml = [
  indexHtml, overview, monitor, actions, tech, workbench, know, career,
  tech2, tmodal, modal, dmodal, ngModal,
  q("#inspect").innerHTML, q("#eventfeed").innerHTML, q("#splash-root").innerHTML,
].join("\n");
const seen = new Set();
for (const tag of panelsHtml.match(/<button[^>]*>/g) || []) {
  for (const a of tag.match(/data-[a-z0-9-]+/g) || []) seen.add(a);
  for (const m of tag.matchAll(/id="([a-z0-9-]+)"/g)) seen.add("id-" + m[1]);
}
for (const a of seen) if (!HANDLED.has(a)) problems.push(`click-coverage: 按钮属性 ${a} 没有点击处理`);

console.log(problems.length ? "PROBLEMS:\n" + problems.join("\n") : "ALL UI CHECKS PASSED");
process.exit(problems.length ? 1 : 0);
