/* 静态自检：元素 id / 本地资源 / 场景 stages（不联网、不写 runs）
   用法: node _shots/check_page.mjs
   退出码 0 = 全部通过。 */
import fs from "node:fs";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const read = (p) => fs.readFileSync(path.join(root, p), "utf8");

const html = read("index.html");
const app = read("assets/app.js");
const cif = read("assets/cif.js");
const data = read("assets/data.js");

let fails = 0;
const ok = (cond, msg) => {
  if (cond) console.log("PASS  " + msg);
  else { fails += 1; console.log("FAIL  " + msg); }
};

const idsOf = (source) => {
  const found = new Set();
  const re = new RegExp("\\$\\(\\s*\"([A-Za-z0-9_]+)\"\\s*\\)", "g");
  let m;
  while ((m = re.exec(source))) found.add(m[1]);
  return [...found];
};

const missingOf = (list) => list.filter((id) => !html.includes('id="' + id + '"'));

const appIds = idsOf(app);
const cifIds = idsOf(cif);
const appMissing = missingOf(appIds);
/* 这两个 id 是 cif.js 在运行时注入的（作业日志的 <pre> 与「确认开工」按钮），
   不在 index.html 的静态 DOM 里；这是有意为之，故白名单化并单独断言其注入点存在。 */
const RUNTIME_INJECTED = new Set(["cifLogBody", "cifStartJob"]);
const cifMissing = missingOf(cifIds).filter((id) => !RUNTIME_INJECTED.has(id));
ok(appIds.length > 40, "app.js 需要的元素 id 抓到 " + appIds.length + " 个");
ok(appMissing.length === 0, "app.js 需要的 id 在 index.html 中 0 缺失" + (appMissing.length ? " → " + appMissing.join(", ") : ""));
ok(cifMissing.length === 0, "cif.js 需要的 id 在 index.html 中 0 缺失（运行时注入的 " + [...RUNTIME_INJECTED].join(" / ") + " 除外）" + (cifMissing.length ? " → " + cifMissing.join(", ") : ""));
ok(cif.includes('id="cifLogBody"') && cif.includes('id="cifStartJob"'),
  "cif.js 确实在运行时注入 cifLogBody / cifStartJob");

/* ---- 本地资源：页面加载期不得有外网 URL ---- */
const refs = [];
for (const m of html.matchAll(/(?:src|href)="([^"]+)"/g)) refs.push(m[1]);
const remote = refs.filter((u) => /^(https?:)?\/\//i.test(u));
ok(remote.length === 0, "index.html 的 " + refs.length + " 个 src/href 中 0 个外网 URL" + (remote.length ? " → " + remote.join(", ") : ""));
const dataUri = refs.filter((u) => u.startsWith("data:"));
console.log("      （其中 " + dataUri.length + " 个是内联 data: URI，不发请求；其余全部是 ./ 相对路径）");
const localMissing = refs
  .filter((u) => !/^(data:|#|mailto:|javascript:)/i.test(u) && !/^[a-z]+:/i.test(u))
  .map((u) => u.split("?")[0].replace(/^\.\//, ""))
  .filter((u) => !fs.existsSync(path.join(root, u)));
ok(localMissing.length === 0, "index.html 引用的本地文件全部存在" + (localMissing.length ? " → " + localMissing.join(", ") : ""));

/* ---- 结构：index.html 的块级标签必须配平（手写壳最容易在这里翻车） ---- */
const PAIRS = ["div", "section", "aside", "main", "header", "footer", "details", "dialog", "ol", "ul", "figure"];
const unbalanced = [];
for (const tag of PAIRS) {
  const open = (html.match(new RegExp("<" + tag + "[\\s>]", "g")) || []).length;
  const close = (html.match(new RegExp("</" + tag + ">", "g")) || []).length;
  if (open !== close) unbalanced.push(tag + ": " + open + " open / " + close + " close");
}
ok(unbalanced.length === 0, "index.html 块级标签配平" + (unbalanced.length ? " → " + unbalanced.join(", ") : ""));

/* ---- 文档：canonical 在 web/*.md，而 /assets/* 才是 server.py 白名单内的可服务路径，
        因此 web/assets/ 下有一份副本；这里断言副本与 canonical 逐字节一致（防漂移）。 ---- */
for (const doc of ["ADAPTATION.md", "SOURCES.md", "README.md"]) {
  const canonical = path.join(root, doc);
  const served = path.join(root, "assets", doc);
  const same = fs.existsSync(served) && Buffer.compare(fs.readFileSync(canonical), fs.readFileSync(served)) === 0;
  ok(same, "assets/" + doc + " 与 " + doc + " 逐字节一致（可服务副本不漂移）");
}
const componentsHtml = read("components/index.html");
// 只挑"指向 web 根级 .md"的链接（../X.md，X 里没有 /）；指向 ../assets/… 的是白名单内、可服务。
const badLinks = [...componentsHtml.matchAll(/href="\.\.\/([^"/]+\.md)"/g)].map((m) => m[1]);
ok(badLinks.length === 0, "components/index.html 不链根级 .md（后端白名单不含，会 404）" + (badLinks.length ? " → " + badLinks.join(", ") : ""));

/* ---- 六场景：key / 顺序 / stages / 必填字段 ---- */
const expected = {
  kickoff: ["解析请求", "确认计划", "启动管线", "流式进度", "汇总产物"],
  repair: ["读入结构", "修复", "写出结构", "落 op.json"],
  geometry: ["读入结构", "几何加工", "写出结构", "落 op.json"],
  export: ["读入结构", "渲染格式", "生成输入包", "落 op.json"],
  inspect: ["读入结构", "校验", "特征与拓扑", "落 op.json"],
  library: ["浏览 runs", "查看 run 快照", "构建数据集", "落 op.json"],
};

/* data.js 是给浏览器用的；用最小 shim 在 node 里求值，避免手工解析。 */
const sandbox = { window: {} };
const fn = new Function("window", data + "\nreturn window.EDU_DATA;");
const EDU = fn(sandbox.window);
const keys = (EDU.scenarios || []).map((s) => s.key);
const expectedKeys = Object.keys(expected);
ok(JSON.stringify(keys) === JSON.stringify(expectedKeys), "六场景 key 与顺序 = " + expectedKeys.join(" / "));

for (const scenario of EDU.scenarios) {
  const want = expected[scenario.key] || [];
  ok(JSON.stringify(scenario.stages) === JSON.stringify(want), scenario.key + " stages 与后端定稿一致（" + want.join(" → ") + "）");
  const need = ["no", "title", "tagline", "short", "inputLabel", "placeholder", "accept", "filesHint", "demoLine", "outputs", "example", "mock", "review", "backend", "runLabel", "form", "next", "mockResult"];
  const lack = need.filter((field) => scenario[field] == null || (Array.isArray(scenario[field]) && !scenario[field].length));
  ok(lack.length === 0, scenario.key + " 字段齐全" + (lack.length ? " → 缺 " + lack.join(", ") : ""));
  ok(/离线演示/.test(scenario.mock || ""), scenario.key + " mock 文本标注了「离线演示」");
  ok(scenario.mockResult && /离线演示/.test(scenario.mockResult.note || ""), scenario.key + " mockResult 标注了「离线演示」");
  ok(typeof scenario.needsInput === "boolean" || scenario.key === "kickoff" || scenario.key === "library",
    scenario.key + " needsInput 声明（" + scenario.needsInput + "）");
}

/* ---- off-line 注册表：31 格式 / 12 输入包（与 CONTRACT §2 数量一致） ---- */
const registry = new Function("window", read("assets/cif-registry.js") + "\nreturn window.CIF_REGISTRY;")({});
ok(Object.keys(registry.formats || {}).length === 31, "cif-registry.js formats = 31");
ok(Object.keys(registry.bundles || {}).length === 12, "cif-registry.js bundles = 12");

/* ---- 声明：页面里不得出现 CDN 域名 ---- */
const cdn = [/cdn\.jsdelivr\.net/i, /unpkg\.com/i, /cdnjs\.cloudflare\.com/i, /fonts\.googleapis\.com/i, /google-analytics/i];
const hits = cdn.filter((re) => re.test(html) || re.test(cif) || re.test(data));
ok(hits.length === 0, "index.html / cif.js / data.js 中 0 处 CDN 引用");

console.log(fails === 0 ? "\nALL PASS" : "\n" + fails + " FAILED");
process.exit(fails === 0 ? 0 : 1);
