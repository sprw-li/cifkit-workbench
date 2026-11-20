# cifkit-workbench

<https://github.com/sprw-li/cifkit-workbench> · MIT

> 把 [cifkit](https://github.com/sprw-li/cifkit)（CIF 处理管线）包成**浏览器里的结构处理工作台**：
> 一句话开管线 → 选型/修复/几何/导出/校验 → 产物下载与溯源。
> 无构建、无 npm、断网能跑；界面复用已有的前端复用包 EduKit，来源与逐文件改动见
> [web/SOURCES.md](web/SOURCES.md)、[web/ADAPTATION.md](web/ADAPTATION.md)。

工作台**不重新实现任何晶体学逻辑**：补氢、对称化、切面、真空、导出、校验全部由 `cifkit` 自己做；
工作台只负责把参数送进去、把进度和产物取回来。接口契约（唯一真源）见 **[CONTRACT.md](CONTRACT.md)**。

## 本仓库独立于 cifkit

- 本仓库（cifkit-workbench）**不包含 cifkit 源码**，也不 vendor 它的代码或数据；
  运行时只通过「进程内 `import cifkit`」或「子进程 `python -m cifkit`」调用它。
- 所以要先给工作台一个可用的 cifkit，二选一：
  1. **pip 安装**：`pip install cifkit`（或 `pip install -e .` 的 editable 检出）；
  2. **CIFKIT_ROOT 指向检出**（推荐，能顺带用到该检出自己的 `.venv`）：
     `CIFKIT_ROOT=C:\path\to\cifkit`。
- 定位顺序（`bridge.resolve_cifkit_root`）：`--cifkit-root` → 环境变量 `CIFKIT_ROOT` →
  同级/同仓检出 → 已安装包所在仓库根。解释器定位同理（`--python` → `CIFKIT_PYTHON` → `<cifkit>/.venv` → 当前解释器）。
- 本仓库自带 MIT [LICENSE](LICENSE)；第三方前端资产的版本/许可证/来源见 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)。

## clone 后三步开始

```powershell
git clone https://github.com/sprw-li/cifkit-workbench
cd cifkit-workbench

# 1) 指向 cifkit（已 pip 安装可跳过；已设置时优先用它）
$env:CIFKIT_ROOT = 'C:\path\to\cifkit'

# 2) 起服务（--allow-exec 才允许跑 op/管线并写 runs-dir；不加即只读浏览）
C:\path\to\cifkit\.venv\Scripts\python.exe server.py --port 8780 --allow-exec

# 3) 打开浏览器
#    http://127.0.0.1:8780/
```

Windows 上也可以直接 `start.cmd`（默认带 `--allow-exec`；`start.cmd --readonly` 为只读浏览，
`start.cmd --selftest` 起服务自检后退出）。`start.cmd` 会优先找同级检出的
`..\.venv\Scripts\python.exe`，找不到就用 PATH 上的 `python`。

- **默认写哪里**：`state/runs`（`--runs-dir` 可改；也可指向 cifkit 仓库的 `runs\` 直接看历史实验）。
- **只读模式**：不带 `--allow-exec`（或 `start.cmd --readonly`）→ 浏览、看 manifest/报告/预览全部可用，
  任何写动作返回 `403 E_EXEC_DISABLED`（看历史实验用这个）。
- 后端不可达时页面**不白屏**：显示「离线演示（无后端）· 预置 mock」，明确说明不是真实结果。

## 六个场景（一屏一条窄闭环）

| # | 场景 | 干什么 | 背后调用 |
|---|---|---|---|
| 01 | 一句话开管线 `kickoff` | 自然语言 → 识别出的 TaskSpec + 步骤计划（**不执行**）→ 确认后真正开工，实时步骤条 + 日志 + 取消 | `route` → `pipeline`（子进程，行流式） |
| 02 | 结构修复 `repair` | 补氢 / 无序占位 / 过近原子，三开关 + 修复报告 | `repair --add-h …` |
| 03 | 几何加工 `geometry` | 标准胞 / 超胞 / (hkl) 切面 / 真空 / 金属簇 | `geom --standardize --supercell --surface --vacuum …` |
| 04 | 导出 `export` | 31 种结构格式 + 12 种计算软件输入包（结构 + 模板 + README + missing 清单） | `convert --to … --bundle …` |
| 05 | 校验 / 特征 `inspect` | 结构校验清单、ML 特征表、拓扑证据分级 | `validate` / `features` / `topology` |
| 06 | 库与溯源 `library` | runs 列表、manifest 溯源（DOI/sha256/工具版本）、数据集构建 | `GET /api/runs` / `dataset` |

每个场景都是：**表单 → 执行 → 结果卡片（结构数字 / 校验清单 / 产物列表带下载 / PNG 预览 / Markdown 报告）→ 下一步入口**。

## 架构：工作台不引入第二份状态

```
浏览器（web/，复用 EduKit，无构建）
      │  GET /api/*        POST /api/op | /api/job   POST /api/run (SSE)
      ▼
server.py（stdlib http.server，只绑 127.0.0.1）
      │  进程内调 cifkit Python API（service.handle / api）+ 子进程跑 CLI（pipeline/geom/…）
      ▼
cifkit（唯一写者：pipeline / repair / geom / export / validate）
      ▼
runs/<id>/（唯一真源：manifest.json / report.md / plan.json / op.json / 结构产物）
```

- 工作台**不复制** manifest、不建数据库；页面读到的就是 runs 目录里的原始文件。
- 写操作全局串行（一把锁），路径一律围栏在 runs 目录内（`..` 逃逸 400）。
- **`GET` 一律无副作用**（有测试守住：runs 目录树签名前后一致）。
- 单步操作用 `op.json` 留痕（argv、python、cifkit 版本、耗时、stdout/stderr 原文），
  **失败也写**（`ok:false` + stderr），与 cifkit 的「失败也写 manifest」同一口径。

端点、数据形状、错误码、SSE 帧、不变量的**完整定义在 [CONTRACT.md](CONTRACT.md)**（本文不重复）。

## 配置

| 来源 | 键 | 默认 | 说明 |
|---|---|---|---|
| 启动参数 | `--runs-dir` | `state/runs` | 产物目录；可指向 cifkit 仓库的 `runs\` |
| 启动参数 | `--cifkit-root` | 自动定位 | cifkit 检出根 |
| 启动参数 | `--python` | 自动定位 | 与 cifkit 对齐的解释器（`.venv` 优先） |
| 启动参数 | `--allow-exec` | 关 | 打开写动作（`start.cmd` 会传） |
| 启动参数 | `--allow-import-root` | 无 | 允许 `op` 读取的额外目录（可重复） |
| 环境变量 | `CIFKIT_ROOT` | — | 同 `--cifkit-root` |
| 环境变量 | `CIFKIT_PYTHON` | — | 同 `--python` |

## 安全边界

- 只绑 `127.0.0.1`（`--host` 传别的值直接 exit 2）；只有 `GET`/`HEAD`/`POST`。
- 页面**不引 CDN**，依赖全在 `web/vendor/`；浏览路径零外网请求。
- 唯一出网的是用户显式点的动作：`fetch`、`sources_probe`（需 `consent`）、`pipeline`（cifkit 数据源自己联网）。
- artifact 下载只允许 run 目录内、扩展名在白名单里的真实文件。
- 无鉴权：**不要**把服务暴露到局域网或公网。

## 目录

```
cifkit-workbench/
  start.cmd          一键启动（带 --allow-exec）
  server.py          stdlib HTTP 服务：静态资源 + JSON API + SSE
  bridge.py          cifkit 定位 / 进程内 API / CLI 子进程（含流式）
  CONTRACT.md        接口契约（唯一真源，含修订记录 §9 §10）
  README.md          本文件
  VERIFICATION.md    独立验收报告（另一个 agent 产出，逐条命令 + 输出）
  THIRD-PARTY-NOTICES.md  第三方资产版本/许可证/来源
  tests/             工作台自己的契约与不变量测试（pytest）
  web/               复用的 EduKit 工作台（index.html / assets / vendor / components）
    SOURCES.md       第三方资产版本、许可证、来源
    ADAPTATION.md    对上游文件改了哪、为什么
  _verify/           独立验收探针 + 日志 + 截图（运行产物不入库）
  state/runs/        默认产物目录（不入库）
```

## 验收

从本仓库根运行（`<cifkit 检出>` 指上面第 1 步设置的检出；用它的 `.venv` 才能 import cifkit）：

```powershell
$PY = 'C:\path\to\cifkit\.venv\Scripts\python.exe'
$env:CIFKIT_ROOT = 'C:\path\to\cifkit'

# 1) 工作台自己的契约 + 不变量测试（离线，约 65s）
& $PY -m pytest tests -q

# 2) 服务自检（起服务 → 16 项自检 → 退出，不挂住）
& $PY server.py --port 0 --selftest

# 3) 前端静态自检 / 无浏览器渲染探针（只读本地文件，不发外网请求）
node web\_shots\check_page.mjs
node web\_shots\render_probe.mjs

# 4) 独立验收（另一个 agent 的探针；含真跑 cifkit 的 e2e）
& $PY _verify\v01_contract.py
& $PY _verify\v02_adversarial.py
& $PY _verify\v03_e2e.py
& $PY _verify\v04_browser.py     # Edge 像素部分允许 BLOCKED-ENV（见下）

# 5) 真后端请求体一致性 / 浏览器像素截图（后者需要非沙箱终端）
& $PY web\_shots\api_conformance.py
& $PY web\_shots\browser_shots.py --backend real
```

结果与证据见 [VERIFICATION.md](VERIFICATION.md)（含原始命令与逐条输出；文件内路径已做发布化替换）。

### 已知环境限制（诚实）

- **Edge headless 的像素级截图在 DSH 的 workspace-write 沙箱下跑不了**：Chromium 的 mojo 通道需要命名管道，
  受限模式下 `FATAL platform_channel.cc:187 (0x5)`。脚本已就绪（`web/_shots/browser_shots.py`），
  在没有沙箱限制的终端跑即可产出 `web/_shots/*.png` 与断言结果。
  在此之前，页面正确性由 **DOM 级探针**（`render_probe.mjs`）+ **真后端联调**（`api_conformance.py`）
  + **HTTP/静态断言**（零外网、六场景 stages 0 漂移）守住。
- 工作台是**本机单人工具**：没有登录、多用户、数据库、WebSocket。
- 工作台**不提供 POTCAR/UPF/basis/skf/力场**，也不把输入包说成「可直接运行」（沿用 cifkit 的措辞）。
- 不做晶体学逻辑：连「补几个氢、切哪个面」都由 cifkit 决定；工作台只编排与展示。

## 与 DSH 插件的关系

同一份真源的另一个入口是 **DSH 插件 `dsh-cifkit`**（<https://github.com/sprw-li/dsh-cifkit>，
右侧栏面板 + `/cif` 命令 + 只读工具 `cifkit_view`）。两者**互为独立实现**，只共享 `runs/<id>/`
这一个数据契约：工作台是浏览器里的完整闭环，插件是在 DSH 界面内看状态、跑命令的入口。

## 许可证

- 本仓库：MIT，署名 **cifkit contributors**（见 [LICENSE](LICENSE)）。
- 第三方前端资产按各自许可证原样分发，未修改；清单见 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)，
  许可证原文在 `web/vendor/licenses/`。
