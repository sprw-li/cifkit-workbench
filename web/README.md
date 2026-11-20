# cifkit 工作台 · 前端（复用 EduKit 骨架）

> 无构建、无 npm、断网能跑。六个场景各一条窄闭环：表单 → 执行 → 结果卡片 → 下一步。
> 接口真源是 [../CONTRACT.md](../CONTRACT.md)（v1.1，冻结）；改动清单见 [ADAPTATION.md](ADAPTATION.md)；
> 第三方来源与许可证见 [SOURCES.md](SOURCES.md)。

## 60 秒开始

```powershell
# A. 后端在线（推荐）：server.py 直接托管本目录
<cifkit 检出>\.venv\Scripts\python.exe ..\server.py --port 8780 --runs-dir ..\state\runs
# → http://127.0.0.1:8780/

# B. 只看前端（没有 /api/*，自动降级为「离线演示（无后端）」，不白屏）
<cifkit 检出>\.venv\Scripts\python.exe -m http.server 8787 --bind 127.0.0.1 --directory .
# → http://127.0.0.1:8787/
```

- `?demo=kickoff`：打开后自动跑指定场景（kickoff / repair / geometry / export / inspect / library）。
- `?present=1`：直接进入演示视图；`1`–`6` 切场景；`Ctrl/⌘+Enter` 运行；`Esc` 退出。
- 默认**零外网请求**；后端不可达时用 `assets/data.js` 的预置 mock 渲染，并明确标注「离线演示（无后端）」。

## 三条复用路径（EduKit 原来的三条，本项目都走通了）

| 你手里的时间 | 改什么 | 得到什么 |
| --- | --- | --- |
| 5 分钟 | `assets/data.js` | 换项目名、六个场景、表单字段、离线 mock |
| 15 分钟 | `assets/base.css` 顶部变量 + `assets/app.css` 追加段 | 换一套品牌视觉，布局与交互不动 |
| 30 分钟 | `assets/cif.js` 里的 `EduKit.EduAPI.run` / `scenarioOp()` | 换后端端点与参数映射，保留表单/卡片/日志骨架 |

上游 `assets/app.js` **一个字节都没改**（哈希一致）：它只提供骨架、SSE 解析、Markdown/KaTeX 渲染与复核区；
cifkit 专属层通过上游预留的 `window.CONDOPT` 钩子接入（`assets/cif.js` 末尾把 `window.CIF` 别名给它）。

## 目录

```
web/
  index.html                 工作台（单页；顶栏 + 工具栏 + 场景侧栏 + 输入区 + 结果区）
  components/index.html      12 个组件 + 可复制代码（上游原样）
  assets/
    data.js                  ★ 六场景、表单字段、离线 mock、下一步入口 —— 换题先改这里
    cif.js                   ★ cifkit 专属层：接后端 / 表单 / 结果卡片 / 作业 SSE / 离线降级
    cif-registry.js          ★ 生成物：31 格式 / 12 输入包（离线兜底，来自 cifkit 注册表）
    app.js                   上游 EduKit 骨架（未改动，哈希一致）
    app.css                  上游布局（1–728 行原样）+ cifkit 追加段
    base.css / components.css / gallery.css / gallery.js / adapter.example.js
  vendor/                    离线依赖 + licenses/
  _shots/                    截图与自检脚本（gen_registry.py / check_page.mjs / render_probe.mjs /
                             api_conformance.py / browser_shots.py）
  README.md / ADAPTATION.md / SOURCES.md
```

## 自检

```powershell
node _shots\check_page.mjs                                       # 静态自检（id / 0 外网 / 六场景 stages）
node _shots\render_probe.mjs                                     # 无浏览器渲染探针（六场景卡片 HTML）
<cifkit 检出>\.venv\Scripts\python.exe _shots\api_conformance.py   # 真后端请求体一致性
<cifkit 检出>\.venv\Scripts\python.exe _shots\browser_shots.py --backend real   # Edge 截图（需非沙箱终端）
```

五个脚本都只读本地文件 / 只连 127.0.0.1，**不发外网请求**（用途与实测结果见 ADAPTATION.md 第 5、6、7 节）；
`browser_shots.py` 只连真 `server.py`（找不到就直接 exit 3，不拿替身出假截图）。

> `assets/` 下的 `ADAPTATION.md` / `SOURCES.md` / `README.md` 是这三份文档的**可服务副本**：
> server.py 的静态白名单不含根级 `.md`（CONTRACT §2），所以浏览器能打开的版本放在 `/assets/`。
> 两份逐字节一致由 `_shots/check_page.mjs` 断言；改完 canonical 记得重新复制。
