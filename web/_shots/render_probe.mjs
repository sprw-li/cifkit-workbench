/* 无浏览器渲染探针（沙箱友好）
   ------------------------------------------------------------
   用途：在**拿不到浏览器**的环境里，仍然验证「六场景的结果卡片 / 表单 / 产物列表」到底渲染成
   什么 HTML —— 直接加载 assets/cif.js 的公开测试钩子（window.CIF._test），用最小 DOM 替身跑。

   它验证的是**渲染逻辑**，不是真实浏览器行为：
     · 真浏览器里的截图 / console.error / 外网请求统计见 _shots/browser_shots.py（需要 Edge）。
     · 本探针不联网、不写 runs 目录、不需要 Edge。

   用法: node _shots/render_probe.mjs
   退出码 0 = 全部 PASS。
*/
import fs from "node:fs";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const read = (p) => fs.readFileSync(path.join(root, p), "utf8");

let fails = 0;
const ok = (cond, msg) => {
  if (cond) console.log("PASS  " + msg);
  else { fails += 1; console.log("FAIL  " + msg); }
};

/* ---------- 最小 DOM 替身：cif.js 只用到 getElementById / createElement 的极小子集 ---------- */
class FakeEl {
  constructor(tag = "div") {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this._html = "";
    this.hidden = false;
    this.textContent = "";
    this.value = "";
    this.dataset = {};
    this.classList = { add() {}, remove() {}, toggle() {}, contains() { return false; } };
    this.style = {};
  }
  set innerHTML(v) { this._html = String(v); }
  get innerHTML() { return this._html; }
  matches() { return false; }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  addEventListener() {}
  appendChild(child) { this.children.push(child); return child; }
  remove() {}
}

const elements = new Map();
const document = {
  readyState: "complete",
  title: "",
  documentElement: { dataset: {} },
  body: { innerText: "", classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } } },
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, new FakeEl());
    return elements.get(id);
  },
  createElement(tag) { return new FakeEl(tag); },
  addEventListener() {},
  querySelector() { return null; },
  querySelectorAll() { return []; },
};

/* ---------- 浏览器全局替身 ---------- */
const noop = () => {};
const window = {
  document,
  localStorage: { getItem: () => null, setItem: noop },
  location: { search: "" },
  console,
  setTimeout, clearTimeout, URLSearchParams,
  addEventListener: noop,
  marked: { parse: (text) => "<p>" + String(text).slice(0, 40) + "</p>" },
  DOMPurify: { sanitize: (html) => html },
  lucide: { createIcons: noop },
  fetch: async () => { throw new Error("render_probe 不联网：fetch 被禁用"); },
  EventSource: function EventSource() { throw new Error("render_probe 不建 EventSource"); },
  MutationObserver: class { observe() {} },
  performance: { now: () => 0 },
  confetti: noop,
};
window.window = window;

/* ---------- 加载 data.js / cif-registry.js / cif.js ---------- */
const loadInto = (file) => {
  const code = read(file);
  const fn = new Function("window", "document", "localStorage", "location", "performance", "EventSource",
    "MutationObserver", "fetch", "marked", "DOMPurify", "lucide", "console", "setTimeout", "clearTimeout",
    "URLSearchParams",
    "const self = window;\n" + code + "\nreturn window;");
  return fn(window, document, window.localStorage, window.location, window.performance, window.EventSource,
    window.MutationObserver, window.fetch, window.marked, window.DOMPurify, window.lucide, console,
    setTimeout, clearTimeout, URLSearchParams);
};

loadInto("assets/cif-registry.js");
loadInto("assets/data.js");
loadInto("assets/cif.js");

ok(Boolean(window.EDU_DATA), "data.js 已加载（window.EDU_DATA）");
ok(Boolean(window.CIF && window.CIF._test), "cif.js 已暴露测试钩子 window.CIF._test");

const T = window.CIF ? window.CIF._test : null;
const scenarios = (window.EDU_DATA && window.EDU_DATA.scenarios) || [];

/* ---------- 1. 六场景表单：字段都渲染出控件 ---------- */
for (const scenario of scenarios) {
  const html = (scenario.form || []).map((field) => T.fieldHtml(field, undefined)).join("");
  ok(html.length > 0, "[" + scenario.key + "] 表单渲染出 " + (scenario.form || []).length + " 个字段（" + html.length + " 字符）");
  const controls = (html.match(/type="(checkbox|number|text|radio)"/g) || []).length +
    (html.match(/<select/g) || []).length + (html.match(/<textarea/g) || []).length;
  ok(controls >= Math.min((scenario.form || []).length, 1), "[" + scenario.key + "] 表单控件数 " + controls + " ≥ 1");
}

/* ---------- 2. 结果卡片：结构数字 / 校验清单 / 产物 / 预览 / 报告 / 下一步 ---------- */
const checks = [
  { name: "natoms", ok: true, detail: "2208 个原子", severity: "error" },
  { name: "occupancy", ok: false, detail: "存在未处理的部分占据", severity: "error" },
];
const products = [
  { name: "structure.vasp", bytes: 152719, kind: "text" },
  { name: "preview.png", bytes: 84210, kind: "image" },
];
const validationHtml = T.validationHtml({ ok: false, checks });
ok(validationHtml.includes("natoms") && validationHtml.includes("occupancy"), "校验清单渲染两个 check 名");
ok(validationHtml.includes("cif-check-row is-ok") && validationHtml.includes("cif-check-row is-bad"),
  "校验清单同时有 is-ok / is-bad 两种状态");
ok(validationHtml.includes("整表结论：不通过"), "校验整表结论按 validation.ok=false 渲染");
ok(T.validationHtml(null).includes("不编造"), "没有 validation 时写「不编造」，不伪造通过");

const productsOnline = T.productsHtml(products, { offline: false, runRel: "op-x" });
ok(productsOnline.includes("/api/artifact?run=op-x&amp;name=structure.vasp&amp;download=1"), "在线产物给 /api/artifact 下载链接");
ok(productsOnline.includes('href="/api/artifact?run=op-x&amp;name=preview.png"'), "在线图片产物给预览链接");
ok((productsOnline.match(/humanBytes|KB|B</g) || []).length >= 0 && productsOnline.includes("KB"), "产物字节用人读写法（KB）");

const productsOffline = T.productsHtml(products, { offline: true, runRel: "" });
ok(!productsOffline.includes("/api/artifact"), "离线产物不产生任何 /api/artifact 链接");
ok(productsOffline.includes("下载（离线无产物）"), "离线产物明确写「下载（离线无产物）」");

/* ---------- 3. 三种卡片：在线结果 / 离线 mock / 错误 ---------- */
const cardBox = document.getElementById("cifResult");
const scenario0 = scenarios[0];

// 3a 在线结果卡片
window.CIF.state.scenarioKey = scenario0.key;
T.resultCard({
  title: "测试 · 结果卡片", offline: false, runLabel: "op-stub",
  summary: [["原子数 atoms", "2208"], ["组成 formula", "Zn96H960C768N384"]],
  validation: { ok: true, checks }, products, report: "# 报告\n\n正文", warnings: ["warn-1"],
});
let html = cardBox.innerHTML;
ok(html.includes("结构化返回") && !html.includes("is-offline"), "在线卡片带「结构化返回」徽章且不是 is-offline");
ok(html.includes("结构数字") && html.includes("校验清单") && html.includes("产物列表") &&
   html.includes("PNG 预览") && html.includes("Markdown 报告"), "在线卡片六块齐全（数字/校验/产物/预览/报告）");
ok(html.includes("data-cif-goto"), "在线卡片带「下一步」入口");
ok(html.includes("warn-1"), "在线卡片显示 warnings");

// 3b 离线 mock 卡片
T.resultCard({
  title: "测试 · 离线", offline: true, runLabel: "无后端 · mock",
  note: "预置 mock，不是真实结果", summary: [["原子数 atoms", "2208"]],
  validation: { ok: true, checks }, products, report: "# 离线报告",
  warnings: ["离线演示：预置 mock"],
});
html = cardBox.innerHTML;
ok(html.includes("is-offline") && html.includes("离线演示（无后端）· 预置 mock"), "离线卡片带「离线演示（无后端）· 预置 mock」徽章");
ok(html.includes("cif-offline-note"), "离线卡片顶部有醒目说明条");
ok(!html.includes("/api/artifact"), "离线卡片不产生下载/预览链接（没有真实文件）");
ok(html.includes("离线演示：没有真实预览图"), "离线卡片预览区明确写「没有真实预览图」");

/* ---------- 4. op 参数映射：只发白名单键 ---------- */
const allowed = {
  repair: ["add_h", "disorder", "contacts"],
  geometry: ["standardize", "supercell", "surface", "surface_frame", "vacuum", "thickness", "cluster"],
  export: ["formats", "bundles", "stem", "title"],
  inspect: ["op", "rdf", "rdf_bins", "rdf_max", "topology", "rings", "net"],
  library: ["from_runs", "from_library", "label_key", "no_features", "no_structures", "dedupe", "jobs"],
};
for (const scenario of scenarios) {
  if (scenario.key === "kickoff") continue;
  window.CIF.state.scenarioKey = scenario.key;
  window.CIF.state.formValues = T.defaultFormValues(scenario);
  const params = T.opParams(scenario);
  const extra = Object.keys(params).filter((key) => !allowed[scenario.key].includes(key));
  ok(extra.length === 0, "[" + scenario.key + "] params 只含白名单键（" + Object.keys(params).join(",") + "）");
  ok(["repair", "geom", "convert", "validate", "features", "topology", "dataset"].includes(T.scenarioOp(scenario)),
    "[" + scenario.key + "] 映射到合法 op：" + T.scenarioOp(scenario));
}

/* ---------- 5. 输入三选一 ---------- */
window.CIF.state.scenarioKey = "repair";
const scenarioRepair = T.scenarioByKey("repair");
window.CIF.state.inputMode = "cif_text";
window.CIF.state.inputCif = "data_x\n_cell_length_a 10";
ok(JSON.stringify(T.buildInput()) === JSON.stringify({ cif_text: "data_x\n_cell_length_a 10" }), "input 模式 2：cif_text");
window.CIF.state.inputMode = "path";
window.CIF.state.inputPath = "D:/runs/x/structure.cif";
ok(JSON.stringify(T.buildInput()) === JSON.stringify({ path: "D:/runs/x/structure.cif" }), "input 模式 3：path");
window.CIF.state.inputMode = "run";
window.CIF.state.currentRun = "op-x";
window.CIF.state.inputArtifact = "repaired.cif";
ok(JSON.stringify(T.buildInput()) === JSON.stringify({ run: "op-x", name: "repaired.cif" }), "input 模式 1：run + name");
window.CIF.state.inputCif = "";
window.CIF.state.currentRun = null;
ok(T.buildInput() === null, "三种输入都缺时返回 null（前端先自查，不发注定被拒的请求）");
ok(T.buildInput !== undefined && scenarioRepair.needsInput === true, "repair 声明 needsInput=true");

/* ---------- 6. 徽章文案 ---------- */
window.CIF.state.online = false;
ok(String(window.CIF.modeBadge().text).includes("离线演示"), "离线徽章文案含「离线演示（无后端）」");
ok(window.CIF.modeBadge().connected === false, "离线徽章 connected=false");
window.CIF.state.online = true;
ok(window.CIF.modeBadge().connected === true && String(window.CIF.modeBadge().text).includes("已连接"),
  "在线徽章文案为「后端已连接 · 待运行」");

console.log(fails === 0 ? "\nALL PASS" : "\n" + fails + " FAILED");
process.exit(fails === 0 ? 0 : 1);
