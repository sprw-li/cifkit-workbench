# cifkit 工作台 · 接口契约 v1（冻结，2026-11-21）

> 本文件是工作台**后端与前端之间的唯一真源**。后端实现端点，前端消费端点；
> 任一方要改契约，先改这里并同时通知另一方。冻结后**允许新增字段**（向后兼容），
> **不允许改字段名 / 改语义**。
> 冻结依据：cifkit CLI/RPC 实测（`python -m cifkit <sub> --help`、`--json` 输出、
> `cifkit/service.py::handle`、`cifkit/schemas/ops.json`），不是文档转述。

## 0. 分层与唯一写者

| 层 | 实体 | 谁写 |
|---|---|---|
| 执行层（唯一写者） | `cifkit`（pipeline / geom / repair / export / service.handle） | 工作台后端**进程内调用** 或 **子进程调用 CLI** |
| 产物层（唯一真源） | runs 目录：`<run>/manifest.json`、`report.md`、`plan.json`、`op.json`、结构文件 | 只由 cifkit 自己写（op.json 由后端写，见 §5.3） |
| 渲染层（无状态） | `web/`（复用 EduKit 骨架，无构建链） | 只读 API |

- 工作台**不自己维护实验状态**：不引入数据库、不复制一份 manifest。
- 写操作全局串行（一把 `threading.Lock`），且**只在 runs-dir 下写**。
- `GET /api/*` **一律无副作用**（不写 runs 目录、不联网）。
- 唯一会出网的路径是显式动作：`op=fetch`、`op=sources_probe`、`kind=pipeline`
  （cifkit 数据源适配器自身联网）；默认浏览路径全部离线。

## 1. 启动与安全

```
py -3 server.py [--host 127.0.0.1] [--port 8780] [--runs-dir state/runs] [--open]
                [--cifkit-root PATH] [--python PATH] [--allow-exec] [--allow-import-root PATH]
                [--max-import-bytes N] [--selftest]
```

- `--host` 只接受 `127.0.0.1` / `localhost`，其它值直接退出（exit 2）。
- 方法：仅 `GET` / `HEAD` / `POST`；其余 → `405 {error,code:"method-not-allowed"}`。
  未知路径 → `404 {error,code:"not-found"}`。静态路径穿越 → `400 {error,code:"bad-path"}`。
- 响应头：JSON/HTML 带 `Cache-Control: no-store`；JSON 一律 UTF-8 + `ensure_ascii=False`。
- 页面**不引 CDN**：全部依赖在 `web/vendor/`（离线可跑）。
- `--runs-dir` 默认 `state/runs`；可指向一个 cifkit 仓库的 `runs/`（只读展示；不可写时
  写动作返回 `403 E_READONLY_RUNS`，不静默失败）。
- 对齐 cifkit 的 Python：定位顺序 `--python` → `CIFKIT_PYTHON` → `<cifkit-root>/.venv/Scripts/python.exe`
  → `<cifkit-root>/.venv/bin/python` → 当前解释器（`sys.executable`）。
- cifkit 根定位顺序 `--cifkit-root` → `CIFKIT_ROOT` → 本仓库同级 `Project5-skill-cif` /
  `.\.`（工作台与 cifkit 同仓时）→ 已安装的 `cifkit` 包所在仓库根 → 失败即启动报错（exit 2），
  **不带半截状态继续**。

## 2. HTTP 端点总表

| 方法 | 路径 | 用途 | 返回 |
|---|---|---|---|
| GET | `/` `/index.html` | 工作台首页 | `web/index.html` |
| GET | `/assets/*` `/vendor/*` `/components/*` | 静态资源（白名单前缀） | 文件字节 |
| GET | `/api/bootstrap` | 首屏元数据 | `Bootstrap`（§3.1） |
| GET | `/api/runs` | runs 列表 | `{ok, runs:[RunSummary]}` |
| GET | `/api/run?id=<rel|abs>` | 单个 run 快照 | `{ok, run:RunSnapshot}` |
| GET | `/api/artifact?run=&name=[&download=1]` | 产物字节（png/cif/vasp/…） | 文件字节 + `Content-Type` |
| GET | `/api/sources` | 数据源状态（离线，不做 probe） | `{ok, sources:[Source]}` |
| GET | `/api/formats` | 31 格式 + 12 输入包 | `{ok, formats:{}, bundles:{}}` |
| GET | `/api/jobs` | 作业列表（内存，最近 50） | `{ok, jobs:[Job]}` |
| GET | `/api/job?id=` | 单个作业 | `{ok, job:Job}` |
| GET | `/api/job/events?id=` | 作业进度（SSE） | `text/event-stream`（§4.2） |
| POST | `/api/route` | 一句话 → TaskSpec + 计划（不执行） | `{ok, spec, plan, warnings}` |
| POST | `/api/op` | 单步操作（同步，秒级） | `{ok, op, run, result, warnings, elapsed_ms, argv}` |
| POST | `/api/job` | 启动长作业（pipeline/batch） | `{ok, job:Job}` |
| POST | `/api/job/cancel` | 取消长作业（kill 子进程） | `{ok, job:Job}` |
| POST | `/api/run` | EduKit 兼容的场景流（SSE） | `text/event-stream`（§4.1） |

> `/api/op` 的 `op` 取值（白名单，`additionalProperties` 之外的字段一律 422）：
> `fetch | repair | geom | export | convert | validate | features | topology | dataset | sources_probe`。
> 每个 op 只映射到一条 cifkit 调用，不做"聪明补全"。

## 3. 数据形状

### 3.1 `Bootstrap`

```json
{
  "ok": true,
  "cifkit": {"version": "0.1.0", "api_version": "1.0", "root": "<abs>", "python": "<abs>",
             "runs_dir": "<abs>", "cache": {"ready": true, "path": "<abs>"}, "ops": ["validate","features"]},
  "sources": [Source],
  "formats": {"vasp": "VASP POSCAR/CONTCAR …"},
  "bundles": {"vasp": "结构 + INCAR + KPOINTS + README（不附 POTCAR）"},
  "scenarios": [Scenario],
  "limits": {"max_import_bytes": 20971520, "pipeline_timeout_s": 1800, "op_timeout_s": 300,
             "job_keep": 50, "max_upload_bytes": 5242880},
  "caps": {"exec": true, "runs_writable": true, "network": false}
}
```

- `formats` / `bundles` **必须来自 cifkit 自己**（`cifkit.export` 的注册表 + 实测 12 组 bundle），
  不手写；缺失时给 `{}` 并在 `warnings` 里说明。
- `caps.network` 表示"当前动作是否需要出网"，不是探测结果；`sources` 里的 `probe` 只在
  `sources_probe` 之后才有值。

### 3.2 `RunSummary` / `RunSnapshot`

```json
RunSummary = {"id":"demo_sod_zif8","rel":"demo_sod_zif8","abs":"<abs>","mtime":1762000000.0,
              "kind":"pipeline|op|batch|external","ok":true,"failed_step":null,
              "n_steps":10,"n_artifacts":12,"atoms":2208,"formula":"Zn96H960C768N384",
              "topology":"sod","source":"core-mof","has_manifest":true,"has_report":true,
              "has_preview":true,"warnings":[]}
```

```json
RunSnapshot = {
  "id":"demo_sod_zif8","rel":"…","abs":"<abs>","kind":"pipeline",
  "manifest":{"…":"manifest.json 原样（无则 null）"},
  "report_md":"# …（report.md 原文；无则 null）",
  "plan":[{"name":"resolve","optional":false,"description":"…"}],
  "steps":[{"name":"resolve","status":"ok","detail":"…","data":{}}],
  "artifacts":[{"name":"structure.vasp","rel":"structure.vasp","abs":"<abs>","bytes":152719,
                "kind":"text|image|structure|json|archive","downloadable":true}],
  "validation":{"ok":true,"checks":[{"name":"natoms","ok":true,"detail":"…","severity":"error"}]},
  "op":{"…":"op.json 原样（单步 run；无则 null）"},
  "warnings":["…"]
}
```

- `steps` 优先取 `manifest["steps"]`；没有 manifest 的 run（外部目录）给 `[]` 并在
  `warnings` 里写明"这个目录没有 manifest.json"，**绝不臆造**。
- `atoms`/`formula` 取 manifest 里最后一步的原子数/组成；取不到给 `null`。
- `validation` 取 `manifest["validation"]`（cifkit 已在 pipeline 里写入）。

### 3.3 `Source` / `Scenario`

```json
Source = {"id":"core-mof","label":"CoRE MOF 2024","needs_key":false,"key_env":null,
          "ready":true,"status":"ready|missing-key|missing-mirror|unknown",
          "topology_metadata":true,"detail":"…","hint":null,"probe":null}
```

```json
Scenario = {"id":"kickoff","title":"一句话开管线","subtitle":"…","icon":"sparkles",
            "stages":[{"index":0,"label":"解析请求","description":"…"}],
            "inputs":[{"name":"text","kind":"text|file|select|number|checkbox","label":"…","required":true}],
            "action":"route+pipeline"}
```

**六个场景 id 固定**（前端路由、SSE 的 `scenario` 字段、`/api/run` 都按它匹配）：

| id | 标题 | 背后调用 |
|---|---|---|
| `kickoff` | 一句话开管线 | `route` →（用户确认）`POST /api/job {kind:"pipeline"}` |
| `repair` | 结构修复（补氢/无序/近邻） | `POST /api/op {op:"repair"}` |
| `geometry` | 几何加工（标准/超胞/切面/真空/簇） | `POST /api/op {op:"geom"}` |
| `export` | 导出与计算输入包 | `POST /api/op {op:"convert"}` |
| `inspect` | 校验 / 特征 / 拓扑证据 | `POST /api/op {op:"validate"\|"features"\|"topology"}` |
| `library` | 库与溯源 / 数据集 | `GET /api/runs` + `POST /api/op {op:"dataset"}` |

### 3.4 `Job`

```json
Job = {"id":"job-20261121-001","kind":"pipeline|batch","status":"running|done|failed|cancelled",
       "started_at":"2026-11-21T00:00:00+08:00","ended_at":null,"elapsed_s":3.2,
       "argv":["-m","cifkit","pipeline","…","--outdir","…","--json"],
       "cwd":"<abs>","run_dir":"<abs>","returncode":null,"error":null,
       "steps":[{"index":0,"name":"resolve","status":"running|ok|failed|skipped","detail":"…"}],
       "log_tail":["…最近 200 行原文…"],"result":{"…":"最终 --json 解析结果（无则 null）"}}
```

- 作业在**内存**里保留最近 50 条（`limits.job_keep`）；进程重启即丢（产物在 runs 目录里不丢）。
- `status` 只由子进程退出码与是否被取消决定：`0→done`、非 0→`failed`、被 cancel→`cancelled`。
- `log_tail` 是**子进程 stdout+stderr 原文**（不加工）；解析出的 `steps` 是**附加**信息。

## 4. SSE 帧

### 4.1 `POST /api/run`（EduKit 兼容）

请求：`{"scenario":"kickoff","text":"…","files":[],"payload":{…}}`

```
event: meta
data: {"run_id":"…","mode":"live","model":"cifkit-0.1.0"}

event: stage
data: {"index":0,"label":"解析请求","status":"running"}

event: delta
data: {"text":"## …\n"}

event: notice
data: {"message":"…"}

event: error
data: {"message":"…","hint":"…"}

event: done
data: {"run_id":"…","elapsed_ms":812,"mode":"live"}
```

- `stage.index` 必须对应 `Bootstrap.scenarios[i].stages[index]`。
- `delta.text` 是 Markdown（可含 KaTeX `$…$`），前端用 marked + DOMPurify + KaTeX 渲染。
- 流式只负责观感；**权威结果以 `/api/op`、`/api/job`、`/api/run` 的结构化返回为准**。
- 任一帧失败 → 发 `error`（页面不崩），最后仍发 `done`。

### 4.2 `GET /api/job/events?id=`（作业进度）

```
event: meta
data: {"job_id":"…","kind":"pipeline","argv":[…]}

event: log
data: {"line":"[resolve] ok …","stream":"stdout"}

event: step
data: {"index":0,"name":"resolve","status":"ok","detail":"…"}

event: notice
data: {"message":"…"}

event: error
data: {"message":"…"}

event: done
data: {"job_id":"…","status":"done","returncode":0,"elapsed_s":10.4,"run_dir":"<abs>"}
```

- 事件流在作业结束后**仍可重放**（已结束的作业把 `log_tail` 全部回放一遍再发 `done`），
  这样刷新页面不会丢日志。
- SSE 每 15s 发一次 `: keepalive` 注释帧（防代理超时）；客户端断开即停止。

## 5. `POST /api/op` 语义

请求：`{"op":"geom","params":{…},"run":null,"input":{…}}`

`input` 三选一（**低优先级到高**）：
1. `{"run":"<rel>","name":"repaired.cif"}` —— 取某个已有 run 里的产物；
2. `{"path":"<abs>"}` —— 必须在 `runs-dir` 或 `--allow-import-root` 内（否则 `400 E_PATH_OUTSIDE`）；
3. `{"cif_text":"…"}` —— 粘贴的 CIF（≤ `limits.max_upload_bytes`）。

`params` 按 op 白名单（键名与 CLI 一一对应，**不接受 argv**）：

| op | params |
|---|---|
| `fetch` | `source,topology,name,structure_id,raw,ready,limit,outdir?` |
| `repair` | `add_h,disorder,contacts`（bool；默认 `add_h=true`） |
| `geom` | `standardize,supercell:[a,b,c],surface:[h,k,l],surface_frame,vacuum,thickness,cluster` |
| `export` | `formats:[…]`（用 `convert` 同名实现，多格式） |
| `convert` | `formats:[…],bundles:[…],stem,title` |
| `validate` | `{}` |
| `features` | `rdf,rdf_bins,rdf_max,topology,rings` |
| `topology` | `net?`（缺省=只报证据，不比对） |
| `dataset` | `from_runs?,from_library?,label_key,no_features,no_structures,dedupe,jobs` |
| `sources_probe` | `{}`（联网探测，慢；前端必须二次确认） |

### 5.1 op run 的目录与命名

每次 `POST /api/op` 新建一个 run 目录（**不覆盖已有目录**）：

```
<runs-dir>/op-<yyyyMMdd-HHmmss>-<op>-<4位随机>/
  input.cif          仅当 input.cif_text 或 input.path 不在 run 内（拷贝进来）
  op.json            见 §5.3
  <产物>              repair→repaired.cif；geom→geom-<op>.cif；convert→ structure.* / <stem>.*;
                     validate→validation.json；features→features.csv；topology→topology.json；
                     dataset→dataset/…
```

同名目录已存在 → `409 E_CONFLICT`（不静默复用）。

### 5.2 返回值

```json
{"ok":true,"op":"geom","run":RunSummary,"result":{"natoms":552,"formula":"…","abc":[…],"info":{}},
 "argv":["-m","cifkit","geom","…","--json"],"elapsed_ms":2120.5,
 "products":[{"name":"geom-surface.cif","rel":"…","bytes":123,"kind":"structure"}],
 "warnings":["…"]}
```

- 失败：`{"ok":false,"error":"E_…","message":"…","hint":"…","argv":[…]}` + 对应 HTTP 状态；
  **失败也要落 `op.json`**（`ok:false` + 原文 stderr），便于复盘（对齐 cifkit 的"失败也写 manifest"）。

### 5.3 `op.json`（后端写，唯一由工作台写的文件）

```json
{"schema":"cifkit-workbench/op/v1","op":"geom","ok":true,"argv":[…],"cwd":"…","python":"…",
 "cifkit_version":"0.1.0","started_at":"…","elapsed_ms":2120.5,"input":{"from":"run","ref":"…"},
 "params":{…},"returncode":0,"stdout":"…","stderr":"…","result":{…},"products":[…],
 "workbench_version":"1.0"}
```

## 6. 错误模型

```json
{"ok": false, "error": "E_NOT_FOUND", "message": "run 不存在: demo2", "hint": "先用 GET /api/runs 看可用 run"}
```

| error | HTTP | 触发 |
|---|---|---|
| `E_BAD_REQUEST` | 400 | 缺字段 / 类型错 |
| `E_PATH_OUTSIDE` | 400 | 输入路径逃逸 runs-dir 与 allow-import-root |
| `E_CONSENT_REQUIRED` | 400 | `sources_probe` 未带 `consent:true` |
| `E_NOT_FOUND` | 404 | run / artifact / job 不存在 |
| `E_CONFLICT` | 409 | 目标 run 目录已存在；同名作业在跑 |
| `E_VALIDATION` | 422 | 参数不在白名单 / cifkit 参数校验失败（原样带 cifkit 的 `violations`） |
| `E_NO_STRUCTURE` | 422 | `route` 退出码 2（没解析出 topology/structure_name/structure_id） |
| `E_READONLY_RUNS` | 403 | runs-dir 不可写 |
| `E_EXEC_DISABLED` | 403 | `--allow-exec` 未开（只读模式） |
| `E_TIMEOUT` | 504 | op 超时（`limits.op_timeout_s`） |
| `E_INTERNAL` | 500 | 其它 |
| `E_CANCELLED` | 409 | 作业/操作被取消 |

## 7. 不变量（测试必须覆盖，缺一即 bug）

1. **`GET` 无副作用**：`/api/bootstrap|runs|run|artifact|sources|formats|jobs|job` 前后
   runs 目录 mtime 集合不变。
2. **同名 run 不覆盖**：第二次 `op` 用同名目录 → `409`。
3. **路径围栏**：`?run=../../etc` / `name=..\\..\\x` → `400 E_PATH_OUTSIDE`（不是 404 之外的成功）。
4. **artifact 白名单**：`name` 必须是该 run 目录内真实存在的文件，且扩展名在
   `{.png,.jpg,.cif,.vasp,.poscar,.xyz,.extxyz,.json,.jsonl,.csv,.md,.txt,.lammps,.data,.qe,.in,.cp2k,.gjf,.orca,.xsf,.gro,.top,.cfg,.gen,.sdf,.mol,.pdb,.zip,.incar,.kpoints}`。
5. **零写入浏览**：打开工作台首页、切换场景、看 run 快照全程不写 runs 目录。
6. **作业可取消**：`cancel` 后子进程在 5s 内结束，`status="cancelled"`，产物目录保留（不删）。
7. **失败留痕**：任何 `op` 失败也写 `op.json`（`ok:false` + stderr 原文）。
8. **不伪造**：拿不到的数据给 `null`/`[]` + `warnings`，不许编造原子数、真空值、拓扑。
9. **离线**：页面加载与浏览路径**零外网请求**（浏览器验收断言 external requests = 0）。
10. **契约一致**：`docs/API.md` / `cifkit/service.py` 的 ops 与本文 §2 表不冲突；
    测试从服务端实际返回里读 `Bootstrap.formats`，不硬编码格式数量。

## 8. 与 cifkit 的边界

- 工作台**不实现任何晶体学逻辑**：不补氢、不算对称、不切面——一律调 cifkit。
- 工作台**不下载 POTCAR/UPF/力场**，也不把 bundle 说成"可直接运行"（沿用 cifkit 的措辞）。
- 工作台**不解析 CLI 文本做决策**：能用 `--json` 就用 `--json`；只有 pipeline 的进度行
  是文本（`[step] status detail`），且只用于展示，权威结果取最终 `--json`。

## 9. 修订记录（v1.1，2026-11-21，Lead 裁定）

v1 冻结后由独立验收方（verifier）提出 7 处空缺/笔误，逐条裁定如下，**均为向后兼容的补充**：

1. **笔误修正**：v1 全文把 Markdown 反引号写成了转义形式 \`（生成时手误）；已全部改为普通反引号。
   语义未变，纯解析友好性修复。
2. **冻结 `POST /api/job` 请求体**（v1 §2 只登记了 kind，未定义 body）：
   - `{"kind":"pipeline","payload":{"query"?:str,"spec"?:object,"source"?:str,"topology"?:str,
     "formats"?:[str],"surface_frame"?:"slab"|"cell","bundles"?:[str],"ingest"?:bool,"outdir"?:str}}`；
     `query` 与 `spec` 互斥（同时给 → `422 E_VALIDATION`），两者都缺 → `422`；
   - `{"kind":"batch","payload":{"specs":[str,…] 或 "specs_file":str,"jobs"?:int,"timeout"?:int,
     "ingest"?:bool,"outdir"?:str}}`；
   - `outdir` 缺省 `<runs-dir>/job-<yyyyMMdd-HHmmss>-<kind>-<4位随机>`；解析后必须仍在 runs-dir 内，
     逃逸 → `400 E_PATH_OUTSIDE`；解析后的目录**已存在且非空 → 409 E_CONFLICT**；
     同一 outdir 已有 running 作业 → `409 E_CONFLICT`；
   - `payload` 里的未知键 → `422 E_VALIDATION`（不静默忽略）。
3. **不变量 7 的验收口径**：失败的 op **也必须先建目录、再写 `op.json`**（`ok:false` + 原始 stderr 原文），
   因此失败响应**仍返回 `run` 字段**（否则验收方无法定位是哪个目录）。
4. **不变量 6 的口径**：`POST /api/job/cancel` 后子进程 5s 内结束、`status="cancelled"`、
   `returncode=null 或 -9/-15` 之一、**产物目录保留不删**。
5. **runs-dir 不可写**：启动时探测 `caps.runs_writable`；为 false 时所有写动作返回
   `403 E_READONLY_RUNS`（浏览路径照常可用），`Bootstrap.caps.runs_writable=false`。
6. **`POST /api/op` 失败响应形状**（§5.2 的补充）：
   `{"ok":false,"error":"E_…","message":"…","hint":"…","run":RunSummary|null,"argv":[…]}`；
   `run` 指向失败后仍写好 `op.json` 的那个目录。
7. **前端离线态**（§7 不变量 5 的延伸）：后端不可达时页面必须优雅降级（明确标注"离线演示"），
   **不允许未捕获异常或 `console.error`**；浏览器验收会断这两条 + 零外网请求。

## 10. 修订记录（v1.2，2026-11-21，Lead 裁定）

1. **`RunSummary.atoms/formula` 的取值来源补一条回落**（§3.2 补充）：
   有 `manifest.json` → 取 manifest 最后一步的原子数/组成；
   **没有 manifest 的 op run → 回落 `op.json.result.natoms` / `op.json.result.formula`**
   （值来自 cifkit 自己的 `--json`，不是工作台推算）；两处都没有 → `null` + `warnings`。
   理由：单步 run 的原子数是 UI 最基本的数字，且来源仍是 cifkit；不回落会白丢信息。
2. **`--allow-exec` 默认关这一安全默认保留**（后端实现口径，符合 §6），但**双击可用的入口必须能干活**：
   `start.cmd` 显式传 `--allow-exec`，并在文件里注释只读用法
   （`start.cmd --port 8790` 不带该参数即只读浏览）。文档要写清"只读模式看历史、`--allow-exec` 才能跑计算"。
3. **静态资源白名单保持 §2 字面**（`/`、`/index.html`、`/assets/*`、`/vendor/*`、`/components/*`）：
   后端拒绝把 web 根变成可枚举文件服务是对的；前端已把指向 `*.md` 的链接改成弹窗/去掉。
4. **`sources_probe` 的 `consent`**：`params.consent` 与顶层 `consent` 等价（§6 未指定位置，两者都接受）。
5. **job 的 `--spec` 临时文件**落在 `<runs-dir>/._jobspecs/`（点开头，`list_runs` 不计为 run，作业结束即删）——
   本机沙箱下系统 TEMP 子目录不可写；这是实现细节，不改变 `/api/jobs` 的语义。
6. **浏览器像素级截图（Edge headless）在本机 workspace-write 沙箱下不可完成**：
   Edge 的 mojo 通道需要命名管道，受限模式下 `FATAL platform_channel.cc:187 (0x5)`。
   因此 `VERIFICATION.md` 里该条必须记为 **BLOCKED-ENV（未验证）**，不得记 PASS；
   非沙箱终端复现命令：
   `<cifkit 检出>\.venv\Scripts\python.exe <本仓库>\web\_shots\browser_shots.py --backend real`。
   DOM 级/HTTP 级证据（`render_probe.mjs`、`api_conformance.py`、`_verify/v04` 的非像素断言）照常算证据。
