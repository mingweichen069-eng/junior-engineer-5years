// 临时 UI 结构校验：在 Node 里用 DOM 桩执行 app.js，逐面板检查渲染结果。
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

// 等待 main() 的异步 fetch/render 完成
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
check("hud", q("#resbar").innerHTML, ["现金", "行动力", "自学精力", "注册用户", "可用性", "技术债", "主管信任"]);
check("inspector", q("#inspect").innerHTML, ["公司处境", "主瓶颈", "技术债构成", "上月月结"]);
check("feed", q("#eventfeed").innerHTML, ["最近事件"]);

// 透镜切换：风险透镜下地图应展示技术债/容量类读数
vm.runInThisContext(`lens = "risk"; render();`, { filename: "lens.js" });
check("lens-risk", q("#center").innerHTML, ["技术债压力", "容量水位"]);
vm.runInThisContext(`lens = "latency";`, { filename: "lens2.js" });

// 点选节点：右侧检视面板应给出"真实世界对应"知识卡
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

const tech = renderPanel("tech");
check("tech", tech, ["科技树", "tree-canvas", "tlane", "tnode3", "计算机基础", "性能工程", "算法工程", "六条轨道", "学时", "已掌握", "读条", "不花行动力", "自学精力"]);

const know = renderPanel("knowledge");
check("knowledge", know, ["知识库", "已读", "解锁全文", "kgroup", "kcard"]);

const career = renderPanel("career");
check("career", career, ["动作快照", "导出存档", "月度记录", "阶段性述职", "只作参考", "调整评价周"]);

// 考核弹窗：v0.9 起后端路线入门 be_t1 是编码题，要能看到公开用例与判题结果
const examResp = await (await fetch(BASE + "/api/tech/exam?node=be_t1")).json();
if (!examResp.ok) problems.push("exam api failed");
vm.runInThisContext(
  `examNode = "be_t1"; examTask = ${JSON.stringify(examResp.data.task)}; examLast = null;
   renderExamModal(${JSON.stringify({ node: "be_t1", name: examResp.data.name, week: 3, branch: "backend", exam_set: examResp.data.exam_set })});`,
  { filename: "exam.js" }
);
const examHtml = q("#modal-root").innerHTML;
check("exam-modal", examHtml, ["考核", "要求", "exam-box", "提交答卷", "稍后再写", "已失败", "编码判题", "公开测试用例"]);
if (!examHtml.includes("exam-code")) problems.push("exam-modal: 编码题编辑器没渲染");

// SQL 真机判题也要能渲染（数据库路线入门 db_t1）
const sqlResp = await (await fetch(BASE + "/api/tech/exam?node=db_t1")).json();
if (!sqlResp.ok) problems.push("exam-sql api failed");
vm.runInThisContext(
  `examNode = "db_t1"; examTask = ${JSON.stringify(sqlResp.data.task)}; examLast = null;
   renderExamModal(${JSON.stringify({ node: "db_t1", name: sqlResp.data.name, week: 3, branch: "db", exam_set: sqlResp.data.exam_set })});`,
  { filename: "examsql.js" }
);
const sqlExamHtml = q("#modal-root").innerHTML;
check("exam-sql", sqlExamHtml, ["真机判题", "提交答卷", "执行计划"]);

// 判定失败态：逐用例结果 + 提示 + 下一步
vm.runInThisContext(
  `examNode = "be_t1"; examTask = ${JSON.stringify(examResp.data.task)};
   examLast = {ok:false,message:'第 1 个用例没过',answer:'pass',detail:{kind:'code',passed:0,total:3,cases:[{i:1,args:'[12345,null,20]',expect:'{user_id:12345}',got:'None',ok:false}],fails:1}};
   renderExamModal({node:'be_t1',week:3,branch:'backend'}, examLast);`,
  { filename: "examfail.js" }
);
const examFail = q("#modal-root").innerHTML;
check("exam-fail", examFail, ["未通过", "判题结果", "得到", "提示", "改完再交一次"]);

// 科技树：读满待考核 + 冷却中两种状态
const st2 = JSON.parse(JSON.stringify(state.data));
// 上一次自检点过「沙盒」后，服务器会变成预解锁全科技的局；把 db_t1 固定成
// 「未开始读条」，保证 tech-timebar 断言与脚本运行次数无关。
const db1 = st2.tech.nodes.find((n) => n.id === "db_t1");
Object.assign(db1, {
  unlocked: false, studying: false, examined: false, exam_state: "", exam_fails: 0,
  ready: false, full: false, cooling: false, progress: 0, remaining: db1.ap,
  excluded_by: "", req_ok: true,
});
const db2 = st2.tech.nodes.find((n) => n.id === "db_t2");
db2.req_ok = true;
db2.unlocked = false;
db2.progress = db2.ap;
db2.ready = true;
db2.exam_state = "open";
db2.full = true;
db2.remaining = 0;
const db3 = st2.tech.nodes.find((n) => n.id === "db_mvcc");
db3.req_ok = true;
db3.cooling = true;
db3.exam_until = 9;
db3.exam_state = "cool";
vm.runInThisContext(`S = ${JSON.stringify(st2)};`, { filename: "techstate.js" });
const tech2 = renderPanel("tech");
check("tech-ready", tech2, ["待考核", "冷却至 W9"]);
vm.runInThisContext(`openTechModal("db_t2");`, { filename: "techmodal.js" });
const tmodal = q("#modal-root").innerHTML;
check("tech-modal", tmodal, ["参加考核", "这个节点教什么", "前置", "读条总量", "第一次失败冷却"]);
vm.runInThisContext(`openTechModal("db_mvcc");`, { filename: "techmodal2.js" });
check("tech-cooldown", q("#modal-root").innerHTML, ["考核冷却中"]);
// 未开始的节点：按钮改成「开始读条」，不再有 +1/+5/投满 AP
vm.runInThisContext(`openTechModal("db_t1");`, { filename: "techmodal3.js" });
const tstart = q("#modal-root").innerHTML;
check("tech-timebar", tstart, ["开始读条", "学时", "每周", "读条不花行动力"]);
if (tstart.includes("一次投满") || tstart.includes("读条 +1 AP")) problems.push("tech-timebar: 还在用 AP 加速读条");

// 事件报告弹窗
vm.runInThisContext(`S.events = [{id:"T1",week:1,day:1,event:"E01",title:"测试事件",tag:"fault",effects:{tickets:2},depth:0,narrative:"监控红了。",speaker:"值班"}];
S.decisions = [];`, {
  filename: "ev.js",
});
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
check("newgame-modal", ngModal, [
  "简单",
  "普通",
  "困难",
  "老手",
  'data-difficulty="easy"',
  'data-difficulty="veteran"',
]);

// ================= 全按钮点击回归 =================
// 上一轮事故：监听只挂在 #center 上，检视栏/弹窗按钮全点不动；
// 这一轮又发现底部时间轴（data-speed）和右上 HUD（#newgame/#sandbox）根本没接处理。
// 这里用一个「假按钮」逐个派发到 document 的真实 click 监听，断言请求真的发出去。
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
const PASSIVE = ["/api/menu", "/api/state", "/api/snapshots", "/api/slots", "/api/profile", "/api/tech/exam?"];
function expectNoCall(name) {
  // 主界面/快照这类「被动读取」不算按钮副作用，但 /api/review/continue 与 config 算
  const active = calls.filter((c) => !PASSIVE.some((p) => c.url.startsWith(p)) && c.url !== "/api/review");
  if (active.length) problems.push(`click-${name}: 本不该发请求，却发了 ${active.map((c) => c.url).join(",")}`);
}

const cases = [
  ["推进一周（data-speed=1）", { "data-speed": "1" }, "/api/advance", '"weeks":1'],
  ["推进四周（data-speed=4）", { "data-speed": "4" }, "/api/advance", '"weeks":4'],
  ["自动推进", { "data-speed": "auto" }, null, ""],
  ["暂停", { "data-speed": "pause" }, null, ""],
  ["左侧导航", { "data-panel": "tech" }, null, ""],
  ["科技节点详情", { "data-tech-node": "db_t1" }, null, ""],
  ["开始读条", { "data-tech-study": "db_t1" }, "/api/tech/study", ""],
  ["执行动作", { "data-action": "add_index" }, "/api/action", ""],
  ["禁用的动作", { "data-action": "add_index", disabled: true }, null, ""],
  ["打开考核", { "data-exam-open": "db_t1" }, "/api/tech/exam", ""],
  ["透镜切换", { "data-lens": "risk" }, null, ""],
  ["知识筛选", { "data-knowledge-filter": "read" }, null, ""],
  ["动作筛选", { "data-afilter": "all" }, null, ""],
  ["监控翻页", { "data-mpage": "1" }, null, ""],
  ["跳转", { "data-goto": "tech" }, null, ""],
  ["关闭弹窗", { "data-close": "1" }, null, ""],
  ["难度选择", { "data-difficulty": "normal" }, "/api/new", ""],
  ["决策选项", { "data-choice": "senior", "data-decision": "D01" }, "/api/event/choose", ""],
  ["回退快照", { "data-rollback": "1" }, "/api/rollback", ""],
];
for (const [name, attrs, path, needle] of cases) {
  calls.length = 0;
  clickOn(attrs);
  if (path) expectCall(name, path, needle);
  else expectNoCall(name);
  // 请求是异步的，busy 会在响应回来后才解锁；等一小会儿再点下一个按钮
  await new Promise((r) => setTimeout(r, 200));
}
calls.length = 0;
clickOn({}, "newgame");
await new Promise((r) => setTimeout(r, 250));
if (!q("#splash-root").innerHTML.includes("新的游戏")) problems.push("click-新档案: 没打开主界面的新游戏页");
expectNoCall("新档案");
await new Promise((r) => setTimeout(r, 200));
calls.length = 0;
clickOn({}, "sandbox");
expectCall("沙盘", "/api/new");
await new Promise((r) => setTimeout(r, 200));
calls.length = 0;
clickOn({}, "restart");
await new Promise((r) => setTimeout(r, 250));
if (!q("#splash-root").innerHTML.includes("新的游戏")) problems.push("click-restart: 没回到主界面");
expectNoCall("restart");

// 覆盖完整性：模板 + 各面板 + 各类弹窗里出现的按钮，其 data-/id 必须在委托里有处理
const HANDLED = new Set([
  "data-panel", "data-speed", "data-exam-open", "data-tech-study", "data-tech-node",
  "data-choice", "data-action", "data-lens", "data-unselect", "data-knowledge-filter",
  "data-afilter", "data-mpage", "data-rollback", "data-node", "data-event", "data-goto",
  "data-difficulty", "data-close", "data-tip", "data-decision", "data-review-config",
  "id-newgame", "id-sandbox", "id-restart", "id-exam-submit", "id-menu",
  // v0.9 启动主界面（renderSplash 生成的按钮，委托在 app.js 的 menu 分支）
  "data-menu-view", "data-menu-diff", "data-menu-review", "data-menu-start",
  "data-menu-sandbox", "data-menu-slot", "data-menu-rename", "data-menu-del",
  "data-menu-save-settings", "data-menu-continue", "data-menu-close",
]);
const panelsHtml = [indexHtml, overview, monitor, actions, tech, know, career, tstart, examHtml, sqlExamHtml, examFail, modal, dmodal, ngModal, q("#inspect").innerHTML, q("#eventfeed").innerHTML, q("#splash-root").innerHTML].join("\n");
const seen = new Set();
for (const tag of panelsHtml.match(/<button[^>]*>/g) || []) {
  for (const a of tag.match(/data-[a-z0-9-]+/g) || []) seen.add(a);
  for (const m of tag.matchAll(/id="([a-z0-9-]+)"/g)) seen.add("id-" + m[1]);
}
for (const a of seen) if (!HANDLED.has(a)) problems.push(`click-coverage: 按钮属性 ${a} 没有点击处理`);

console.log(problems.length ? "PROBLEMS:\n" + problems.join("\n") : "ALL UI CHECKS PASSED");
process.exit(problems.length ? 1 : 0);
