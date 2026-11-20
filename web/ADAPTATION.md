# ADAPTATION.md · cifkit 工作台对上游 EduKit 复用包的改动清单

> 唯一真源：`CONTRACT.md`（v1.1，冻结）。本文件记录**对上游文件改了什么 / 为什么**，
> 以及 cifkit 专属层（`assets/cif.js`）怎么消费契约端点。
>
> 上游：EduKit 复用包（本机 `<condopt-workbench 检出>\web`，无构建 / 无 npm / 断网能跑）。
> 所有依赖仍在 `vendor/`，运行时不访问 CDN，页面加载与浏览路径**零外网请求**。

## 0. 跑起来

```powershell
# 1) 起后端（task-1 的 server.py，只绑 127.0.0.1）
<cifkit 检出>\.venv\Scripts\python.exe server.py --port 8780 --runs-dir state\runs

# 2) 打开工作台：后端在线 → http://127.0.0.1:8780/
#    只想看前端：任意静态服务器托管本目录（没有 /api/*，页面自动降级为离线演示，不白屏）
<cifkit 检出>\.venv\Scripts\python.exe -m http.server 8787 --bind 127.0.0.1 --directory web

# 3) 自检（离线，不发任何外网请求）
node web\_shots\check_page.mjs
<cifkit 检出>\.venv\Scripts\python.exe web\_shots\browser_shots.py
```

## 1. 文件级改动总览

| 文件 | 动作 | 说明 |
|---|---|---|
| `web/index.html` | **改写**（产品壳换成 cifkit） | 保留顶栏 / 场景侧栏 / 输入区 / 结果区 / 复核区骨架、演示视图、深浅色、快捷键；新增工具栏（健康态 / runs 下拉 / 新建 run）、场景表单、结构输入来源、结果卡片、作业日志区 |
| `web/assets/data.js` | **替换内容** | 教育场景 → cifkit 六场景（key 与顺序固定：kickoff / repair / geometry / export / inspect / library）；stages 文案与 wb-backend 定稿一致 |
| `web/assets/cif.js` | **新增** | cifkit 专属层：接后端（只替换 EduKit.EduAPI.run 的路径）、表单、输入来源、结果卡片、作业 SSE 日志、健康态、离线降级 |
| `web/assets/cif-registry.js` | **新增（生成物）** | 离线兜底的 31 格式 / 12 输入包清单，由 `_shots/gen_registry.py` 从 `cifkit.export` 注册表实测导出（不手写） |
| `web/assets/app.css` | **追加覆盖段** | 上游 1–728 行**原样**（23,426 字节，与上游 `app.css` 逐字节一致）+ 追加段（布局微调 + 六场景卡片样式）；未动 `base.css` 色板语义与 `components.css` |
| `web/ADAPTATION.md` | **新增** | 本文件 |
| `web/SOURCES.md` | **新增** | 第三方版本 / 来源 / 许可证 + 生成物说明 |
| `web/README.md` | **新增** | 复用包入口说明（谁改哪里 + 三条复用路径） |
| `web/_shots/gen_registry.py` | **新增** | 生成 `assets/cif-registry.js`；只 import 本机 cifkit，**不发网络请求** |
| `web/_shots/check_page.mjs` | **新增** | 纯本地静态自检；**不发网络请求** |
| `web/_shots/browser_shots.py` | **新增** | Edge headless + CDP：截图 + 外网请求数 + console error / 未捕获异常实测 |
| `web/assets/{ADAPTATION,SOURCES,README}.md` | **新增（可服务副本）** | server.py 的静态白名单只有 `/`、`/assets/*`、`/vendor/*`、`/components/*`（CONTRACT §2），根级 `.md` 必然 404；因此把 canonical 文档复制到 `assets/` 下供浏览器打开（`components/index.html` 的链接指向这里）。`_shots/check_page.mjs` 断言两份**逐字节一致** |

**未改动（逐字节一致）**：`assets/app.js`（哈希与上游相同，见第 2 节）、`assets/base.css`、
`assets/components.css`、`assets/gallery.css`、`assets/gallery.js`、`assets/adapter.example.js`、
`components/index.html`、`vendor/**`。

## 2. assets/app.js 一处都没改（哈希一致）

上游 EduKit 的 app.js 已经留了 6 个受 `window.CONDOPT` 保护的可选钩子。**为了保持「原样复制」，
app.js 一个字节都没动**；cifkit 专属层用 `window.CIF` 作自己的命名空间，并在 `assets/cif.js` 末尾
把同一对象别名给 `window.CONDOPT`（app.js 里写死的钩子名是上游冻结的）：

```js
window.CIF = { hasPayload, buildPayload, modeBadge, onReview, state };
window.CONDOPT = window.CIF;   // app.js 的钩子名是上游冻结的，这里只做同一对象别名
```

证据（命令原文）：

```powershell
node -e "const fs=require('fs');const a=fs.readFileSync('web/assets/app.js');const b=fs.readFileSync('<condopt-workbench 检出>/web/assets/app.js');console.log('identical:',a.length===b.length&&a.equals(b));"
# identical: true
```

被用到的钩子语义（与上游注释一致）：

| 钩子 | cifkit 层怎么用 |
|---|---|
| `hasPayload(key)` | 恒 true：六场景的输入都在表单里，不该被上游「输入框必须非空」拦住 |
| `buildPayload(ctx)` | 返回 `{op, params, input}`（kickoff 返回 `{text}`），供 JSON 抽屉展示与排查 |
| `modeBadge()` | 顶栏徽章：bootstrap 通过 → 「后端已连接 · 待运行」；失败 → 「离线演示（无后端）」 |
| `onReview(record)` | **不伪造后端写**：契约没有「复核入库」端点，只写本机 localStorage，并在界面写明不上传 |

另外 `EduKit.EduAPI.run` 被替换（这是上游文档指定的**唯一**接后端点）：

- 后端不可达 → `mockFlow()`：按场景跑阶段 + 把 `data.js` 的 `mock` 流式吐出，再渲染 mock 结果卡片；
- `scenario.key === "kickoff"` → `POST /api/route`（预览，不执行）；
- 其它五个场景 → `POST /api/op`（结构化，权威结果）。

## 3. 六场景 ↔ 契约端点 ↔ 页面元素

| 场景 id | 背后调用（CONTRACT 3.3 / 5 / 9.2） | 表单字段（params 键） | 结果卡片来源 |
|---|---|---|---|
| `kickoff` | `POST /api/route` →（人确认）`POST /api/job {kind:"pipeline", payload:{spec 或 query}}` → `GET /api/job/events?id=`（EventSource） | text（一句话） | `GET /api/run?id=<run_dir>` 快照 + 作业日志 |
| `repair` | `POST /api/op {op:"repair"}` | add_h, disorder, contacts | result + products + run 快照 |
| `geometry` | `POST /api/op {op:"geom"}` | standardize, supercell, surface, surface_frame, vacuum, thickness, cluster | 同上 |
| `export` | `POST /api/op {op:"convert"}` | formats, bundles, stem, title | 同上 |
| `inspect` | `POST /api/op {op:"validate" 或 "features" 或 "topology"}` | op + (rdf, rdf_bins, rdf_max, topology, rings) 或 net | 同上 |
| `library` | `GET /api/runs` + `GET /api/sources` + `POST /api/op {op:"dataset"}` | from_runs, from_library, label_key, no_features, no_structures, dedupe, jobs | 同上 |

每屏都走同一条窄闭环：**表单 → 执行 → 结果卡片（结构数字 / 校验清单 / 产物列表+下载 / PNG 预览 / Markdown 报告）→ 下一步入口**。

几个刻意的取舍（都为了「不伪造」）：

- op 请求体只发**该 op 白名单内**的键（未知字段后端 422）；空值 / 默认值不塞进去。
- input 三选一（CONTRACT 5）：已有 run 的产物 `{run,name}` / `{path}` / `{cif_text}`；
  前端在**发请求之前**自查，缺输入就打一张本地失败卡，不发注定被拒的请求。
- 校验清单与报告取 **run 快照**（`manifest.validation` / `report.md`）；单步 op run 没有 report.md 时，
  前端按 `POST /api/op` 的结构化返回渲染一份**标注了来源**的摘要，不冒充「流式报告」。
- 产物下载 / 预览一律走 `GET /api/artifact?run=&name=[&download=1]`（契约白名单扩展名）；
  校验清单缺失时写「没有 validation 记录（不编造校验结论）」。
- 「新建 run」不是一个端点：契约里**没有**创建空 run 的 API（run 由 op / pipeline 创建，5.1）。
  按钮的行为是「切到 01 场景 + 明确告诉你 run 从哪来」，不伪造一个创建端点的语义。
- topology 不带 net 时只报证据、不比对（CONTRACT 5），前端把 net 的「（只报证据）」作为默认选中项。

## 4. 离线态（CONTRACT 9.7，lead 裁定）

- 判据只有一条：`GET /api/bootstrap` 是否 200。失败 → `state.online=false`。
- 页面**不白屏**：六场景照常可切换；表单照常用；点「执行」走 `mockFlow()`，
  渲染 `data.js` 的 `mock` + `mockResult`，卡片顶部写死
  「**离线演示（无后端）· 预置 mock**」，横幅与侧栏各还有一次说明；产物下载被禁用（没有真实文件）。
- **不产生 console.error / 未捕获异常**：唯一失败路径是 `fetch` → `getJson` 抛 `CifError` → 被
  `try/catch` 接住；日志只用 `console.warn` / `console.info`（上游 app.js 也是）。
- 状态裁剪：`state.snapshot=null`、`state.runs=[]`；runs 下拉显示「（离线演示 · 无后端）」，
  数据源区写「离线：GET /api/sources 需要后端」，**不把上一轮的真实数据当成本轮结果**。

## 5. 生成物与脚本的用途（lead 要求写清）

| 脚本 | 用途 | 是否会出网 |
|---|---|---|
| `_shots/gen_registry.py` | 把 `cifkit.export.FORMAT_INFO / BUNDLE_INFO`（31 / 12）导出成 `assets/cif-registry.js`，供离线态渲染导出清单 | 否（只 import 本机 cifkit） |
| `_shots/check_page.mjs` | 纯本地静态自检：元素 id 是否齐全、`index.html` 的 src/href 是否 0 外网 URL、本地资源是否存在、六场景 key/stages/字段、registry 数量、CDN 引用 0 | 否（只读文件） |
| `_shots/browser_shots.py` | Edge headless + CDP：逐场景截图到 `_shots/*.png`，统计外网请求数、console error、未捕获异常、DOM 断言。**需要不被沙箱限制的终端**（见第 7 节） | 否（只连 127.0.0.1） |
| `_shots/render_probe.mjs` | **无浏览器**渲染探针：用最小 DOM 替身加载 `cif.js` 的测试钩子 `window.CIF._test`，断言六场景表单控件、结果卡片六块、离线卡片无下载链接、op 参数只含白名单键、input 三选一 | 否（`fetch` 被替身禁用） |
| `_shots/api_conformance.py` | 把 cif.js **实际会发的请求体**打到真 `server.py`（`--allow-exec`）：首页与本地资源 200、六场景 stages 与后端 0 漂移、route/op×8/job/SSE/cancel/runs/run/artifact、GET 无副作用 | 否（只连 127.0.0.1） |


> `_shots/` 里只保留有长期价值的脚本：`gen_registry.py`（生成离线注册表）、`check_page.mjs`（静态自检）、
> `render_probe.mjs`（无浏览器渲染探针）、`api_conformance.py`（真后端请求体一致性）、`browser_shots.py`（Edge 截图）。
> 早期为「后端未就绪」写的 `stub_server.py` 已删除：它只是测试替身，真 `server.py` 到位后没有长期价值，
> 留着反而可能让人拿假后端出「看起来像真的」截图；`browser_shots.py` 现在只连真后端，找不到 `server.py` 直接 exit 3。

## 6. 已跑验收（命令原文与关键输出见 task-2 汇报）

| # | 命令 | 结果 |
|---|---|---|
| 1 | `node --check assets/data.js assets/cif.js assets/cif-registry.js` | exit 0 |
| 2 | `node _shots/check_page.mjs` | **exit 0 / ALL PASS**（app.js 54 个 id 0 缺失；首页 19 个 src/href 0 外网；六场景 key/stages 逐字一致；registry 31/12；0 处 CDN） |
| 3 | `node _shots/render_probe.mjs` | **exit 0 / ALL PASS**（无浏览器：六场景表单 + 在线/离线卡片 HTML + 下载链接 + op 白名单键 + input 三选一） |
| 4 | `python _shots/api_conformance.py` | **exit 0 / ALL PASS**（真 `server.py --allow-exec`：首页与 17 个本地资源 200、六场景 stages 0 漂移、route/op×8/job+SSE/cancel/runs/run/artifact、GET 不写 runs；另有 3 条后端侧 NOTE 见第 9 节） |
| 5 | `node -e` 比对 `app.js` 与上游 | `identical: true`（逐字节相同） |
| 6 | `python _shots/browser_shots.py --backend real` | **待跑**：本会话沙箱禁止命名管道，Edge 起不来（见第 7 节）；脚本已就绪 |

## 7. Edge 截图在本会话被沙箱挡住（如实记录）

现象（stderr 原文）：

```
[68648:64868:FATAL:mojo\public\cpp\platform\platform_channel.cc:187] Check failed: . : 拒绝访问。 (0x5)
[1121/010901.658:ERROR:third_party\crashpad\crashpad\client\crashpad_client_win.cc:457] OpenProcess: 拒绝访问。 (0x5)
```

原因：本会话的 DSH 沙箱（workspace-write）**禁止进程打开命名管道**，而 Chromium/Edge 的 mojo IPC 必须用命名管道。
已试过且全部失败的组合（都无输出、无 PNG）：`--headless=new`、`--headless=new --no-sandbox`、
`--single-process`、`--in-process-gpu`、`--screenshot=<file>`；profile 与输出目录都放在工作区内（排除写权限因素）。

处置（**不再换法子重试**）：

- `_shots/browser_shots.py` 保持可用，请在**非沙箱终端**（或授予完整访问的会话）执行：
  `python web\_shots\browser_shots.py --backend real`（`--backend auto` 在 server.py 存在时会自动选 real）；
- 同一命令产出 `_shots/01_offline_home.png`、`02_offline_kickoff.png`、`03_online_home_kickoff.png`、
  `04_online_kickoff_job.png`、`05..09_online_<场景>.png`，并断言
  「加载期外网请求 = 0 / console.error = 0 / 未捕获异常 = 0 / 六场景卡片元素齐全」；
- 在此之前，**无浏览器**的等价证据是第 6 节的 2/3/4 三条（静态 0 外网 + 渲染谓词 + 真后端请求体一致性）。

## 8. 后端侧发现（不是前端问题，已上报 wb-backend）

| 现象 | 判据 | 结论 |
|---|---|---|
| `POST /api/op {op:"dataset", params:{from_runs:true}}` → `500 E_INTERNAL`，原文 `FileNotFoundError: runs/library 目录不存在：True` | CONTRACT §5 的 `from_runs` / `from_library` 是布尔，而 cifkit CLI 的 `--from-runs/--from-library` 收的是**路径** | 后端 payload→CLI 映射疑似 bug（把 `True` 当路径）；前端形状不变，等后端修 |
| `POST /api/op {op:"geom", params:{cluster:"sbu"}}` → 500，原文 `extract_cluster(mode='sbu'): only extracted 1 atom(s)` | 示例 CIF 只有 2 个原子，切不出金属簇 | 形状正确（枚举值已到达 cifkit），是测试输入太小 |
| `geom` 同时给 `supercell=[2,2,2]` 与 `surface_frame="slab"` → 500 `make_slab: reps[2] must be 1` | cifkit 规则：slab 模式面内重复、c 方向必须为 1 | 前端已在 `data.js` 的 surface_frame 帮助文案里写明该约束 |

## 9. 只读模式的措辞（CONTRACT §10.2）

`start.cmd` 会默认带 `--allow-exec`「双击能干活」，所以前端**不写死「默认只读」**。现行措辞：

- 顶栏能力行：`caps.exec` 为假时显示「exec 关闭（未加 --allow-exec）」；
- 点执行时的自查卡：标题「只读模式（未加 --allow-exec）· 前端先自查，不发注定 403 的请求」，
  正文 = 后端口径 `E_EXEC_DISABLED` 的 message + hint（`用 --allow-exec 重启 server.py`），
  同时 `GET` 浏览路径（runs / run 快照 / artifact / sources）不受影响。

## 10. 未接的端点（有意为之）

- `POST /api/run`（EduKit 兼容 SSE 观感通道）：**本前端不调用**。六场景都有更具体、更权威的结构化通道
  （CONTRACT 3.3），再走一遍 SSE 只会产生第二份「看起来像结果」的文本；契约也写了「流式只负责观感」。
  需要观感时用的是 `/api/job/events`（真实子进程日志）与结构化返回渲染。
- `POST /api/op {op:"fetch"}` / `{op:"sources_probe"}`：会出网的显式动作，需要二次确认；
  本版六场景窄闭环不包含它们（数据源状态只做只读展示 `GET /api/sources`）。
