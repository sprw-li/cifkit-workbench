# workbench 独立验收报告（VERIFICATION.md）

> 路径已做发布化替换，命令可按占位符复跑；结论与数字一个都不许改。

- 验收人：**verifier**（独立验收角色；只读 CONTRACT.md 与 cifkit 自己的文档/API，黑盒驱动 HTTP/SSE/浏览器）
- 日期：2026-11-21（Asia/Shanghai）
- 唯一接口真源：`CONTRACT.md`（v1.2，含 §9/§10 修订）
- 独立性声明：**未读 server.py / bridge.py / web/** 的实现来迁就判据**；唯一"读实现"的地方是 cifkit 自身公共 API（`cifkit.api.capabilities/validate`）与 cifkit CLI `--help`，用作权威对照。发现的问题只报给 lead，**未改实现**。
- runs 目录：全部使用系统临时目录（`%TEMP%\cifwb-verify-runs`），**未写** `<cifkit 检出>\runs\`。

## 0. 结论总表

| 探针 | 命令原文 | 结果 | exit |
|---|---|---|---|
| v01 契约一致性 | `"<cifkit 检出>\.venv\Scripts\python.exe" _verify\v01_contract.py` | 66/66 PASS，3 BLOCKED（判据不可构造，见 §6） | 0 |
| v02 对抗/边界/并发/取消/SSE | `"<cifkit 检出>\.venv\Scripts\python.exe" _verify\v02_adversarial.py` | 33/33 PASS，1 BLOCKED-ENV（symlink 无特权） | 0 |
| v03 端到端（真跑 cifkit） | `"<cifkit 检出>\.venv\Scripts\python.exe" _verify\v03_e2e.py` | 19/19 PASS | 0 |
| v04 浏览器（Edge headless） | `"<cifkit 检出>\.venv\Scripts\python.exe" _verify\v04_browser.py` | 4/4 PASS（非像素），1 BLOCKED-ENV（Edge 沙箱起不来） | 0 |
| v05 插件独立复核 | `"<cifkit 检出>\.venv\Scripts\python.exe" _verify\v05_plugin.py` | 5/5 PASS（host harness 235/0/0，client 113/0） | 0 |
| 工具自检 | `"<cifkit 检出>\.venv\Scripts\python.exe" _verify\selftest_plumbing.py` | 7/7 PASS | 0 |
| SSE keepalive 专项 | `"<cifkit 检出>\.venv\Scripts\python.exe" _verify\probe_sse_keepalive.py` | 空闲 >15s 收到 1 个 `: keepalive` | 0 |
| SSE 时序专项 | `"<cifkit 检出>\.venv\Scripts\python.exe" _verify\probe_sse_timing.py` | 24.8s 处 1 个 keepalive；普通管线 14.5s 内结束无空闲 | 0 |
| 只读 runs-dir 专项 | `"<cifkit 检出>\.venv\Scripts\python.exe" _verify\probe_ro_strategies.py` | `--runs-dir` 指向已存在文件 → caps.runs_writable=false + 403 | 0 |

**没有实现级 FAIL。** 唯一"未验证"的是浏览器像素/DOM/console（Edge 在 workspace-write 沙箱下无法启动，见 §6）。

## 1. v01 契约一致性

命令原文：`"<cifkit 检出>\.venv\Scripts\python.exe" _verify\v01_contract.py`
（覆盖 CONTRACT §1 启动与安全、§2 全部 18 个 (方法,路径)、§3 数据形状、§5 op.json、§6 错误码、§7 不变量 1/3/4/5/10）

| 判据 | 命令/证据 | 观察到的输出摘要 | 结果 |
|---|---|---|---|
| §1 --host 非本机地址 → exit 2 | 见节首命令 | exit=2 stderr="错误：--host 只接受 127.0.0.1 / localhost（工作台无鉴权，不得对外监听），收到 '0.0.0.0'" | PASS |
| §1 --selftest → exit 0 | 见节首命令 | exit=0 stdout="ge', 'stage', 'stage', 'delta', 'notice', 'stage', 'stage', 'stage', 'stage', 'stage', 'done'] indices=[0, 1, 2, 3, 4]\n[PASS] GET /api/job/events 未知 id -> 404 status=404\n[PASS] GET /（web/index.html） status=200\n[PASS] 不变量1 GET 无副作用（runs mtime 集合不变） before=1 after=1\n[selftest] 完成：16/16 项通过，用时 32.1s" st | PASS |
| §1 cifkit 根定位：显式 --cifkit-root 无效时回落已安装包根（根必须真实存在），否则 exit 2 | 见节首命令 | 回落启动 status=200 resolved_root='<cifkit 检出>' | PASS |
| §2 GET / → 200 text/html + Cache-Control: no-store | 见节首命令 | status=200 ct='text/html; charset=utf-8' cache='no-store' bytes=16457 | PASS |
| §2 GET /index.html → 200 且与 / 同源 | 见节首命令 | status=200 bytes=16457 | PASS |
| §2 index.html 引用的 16 个本地资源全部 200 | 见节首命令 | 失败=无；样例=['./assets/app.css', './assets/app.js', './assets/base.css', './assets/cif-registry.js', './assets/cif.js', './assets/components.css'] | PASS |
| §2 GET /api/bootstrap → 200 JSON + no-store | 见节首命令 | status=200 cache='no-store' | PASS |
| §3.1 Bootstrap 顶层键齐全 | 见节首命令 | 缺=无 | PASS |
| §3.1 Bootstrap.cifkit 键齐全 + runs_dir 指向本次 runs-dir | 见节首命令 | 缺=无 root='<cifkit 检出>' python='<cifkit 检出>\\.venv\\Scripts\\python.exe' runs_dir='<临时目录>\\cifwb-verify-runs\\v01' | PASS |
| §3.1 cifkit.cache 含 ready/path | 见节首命令 | cache={'ready': True, 'path': '<cifkit 检出>\\_cache'} | PASS |
| §3.1 caps 含 exec/runs_writable/network（network 非探测结果） | 见节首命令 | caps={'exec': True, 'runs_writable': True, 'network': False} | PASS |
| §3.1 limits 键齐全 | 见节首命令 | limits={'max_import_bytes': 20971520, 'pipeline_timeout_s': 1800.0, 'op_timeout_s': 300.0, 'job_keep': 50, 'max_upload_bytes': 5242880} | PASS |
| §3.3 六个场景 id 固定且有序 | 见节首命令 | 实际=['kickoff', 'repair', 'geometry', 'export', 'inspect', 'library'] | PASS |
| §3.3 每个 Scenario 键齐全 + stages 序号 0..n-1 连续 | 见节首命令 | 键缺=无 阶段错=无 | PASS |
| §3.3 Scenario.inputs 每项含 name/kind/label | 见节首命令 | 异常=无 | PASS |
| §3.1 bootstrap.formats == cifkit.api.capabilities().formats（31 项，未硬编码） | 见节首命令 | 服务端=31 项缺=[] 多=[] | PASS |
| §3.1 bootstrap.bundles == cifkit.api.capabilities().bundles（12 项） | 见节首命令 | 服务端=12 项缺=[] 多=[] | PASS |
| §3.1 bootstrap.cifkit.api_version == cifkit capabilities.api_version | 见节首命令 | 服务端='1.0' cifkit='1.0' | PASS |
| §3.1/不变量 10 bootstrap.cifkit.ops 来自 cifkit 自己（== capabilities.operations） | 见节首命令 | 服务端=['capabilities', 'convert', 'dataset', 'dataset_rows', 'features', 'features_many', 'fetch', 'topology', 'validate'] cifkit.operations=['capabilities', 'convert', 'dataset', 'dataset_rows', 'features', 'features_many', 'fetch', 'topology', 'validate'] | PASS |
| 不变量 10：每个 op 可追溯到 cifkit 执行层（CLI 子命令 或 api/capabilities/schemas 里存在） | 见节首命令 | ['fetch:cli=0, api=True, schema=True', 'repair:cli=0, api=False, schema=False', 'geom:cli=0, api=False, schema=False', 'export:cli=0, api=False, schema=False', 'convert:cli=0, api=True, schema=True', 'validate:cli=0, api=True, schema=True', 'features:cli=0, api=True, schema=True', 'topology:cli=2, api=True, schema=True | PASS |
| §2/§3.3 GET /api/sources → 200 + Source 键齐全 + probe 未探测时为 null | 见节首命令 | status=200 keys缺=无 n=6 | PASS |
| §3.3 GET /api/sources 不做 probe（离线，probe 全为 null） | 见节首命令 | 已有 probe 的源=无 | PASS |
| §2 GET /api/formats → 200 {ok,formats,bundles} | 见节首命令 | status=200 formats=31 bundles=12 | PASS |
| §2 GET /api/jobs → 200 {ok,jobs:[]} | 见节首命令 | status=200 jobs=[] | PASS |
| §2 GET /api/runs（空 runs）→ 200 {ok,runs:[]} | 见节首命令 | status=200 runs=[] | PASS |
| §6 E_NOT_FOUND：GET /api/run?id=不存在 → 404 | 见节首命令 | status=404 body='{"ok": false, "error": "E_NOT_FOUND", "message": "run 不存在: __no_such_run__", "hint": "先用 GET /api/runs 看可用 run", "run": null}' | PASS |
| §6 E_NOT_FOUND：GET /api/artifact?run=不存在 → 404 | 见节首命令 | status=404 body='{"ok": false, "error": "E_NOT_FOUND", "message": "run 不存在: __no_such_run__", "hint": "先用 GET /api/runs 看可用 run", "run": null}' | PASS |
| §6 E_NOT_FOUND：GET /api/job?id=不存在 → 404 | 见节首命令 | status=404 body='{"ok": false, "error": "E_NOT_FOUND", "message": "作业不存在：__no_such_job__", "hint": "作业只保留在内存里（最多 50 条），重启即丢；用 GET /api/jobs 看当前列表", "run": null}' | PASS |
| §1 PUT → 405 {code:'method-not-allowed'} | 见节首命令 | status=405 body='{"ok": false, "error": "E_METHOD_NOT_ALLOWED", "code": "method-not-allowed", "message": "405 method not allowed：PUT（只接受 ' | PASS |
| §1 DELETE → 405 {code:'method-not-allowed'} | 见节首命令 | status=405 body='{"ok": false, "error": "E_METHOD_NOT_ALLOWED", "code": "method-not-allowed", "message": "405 method not allowed：DELETE（只' | PASS |
| §1 PATCH → 405 {code:'method-not-allowed'} | 见节首命令 | status=405 body='{"ok": false, "error": "E_METHOD_NOT_ALLOWED", "code": "method-not-allowed", "message": "405 method not allowed：PATCH（只接' | PASS |
| §1 未知路径 → 404 {code:'not-found'} | 见节首命令 | status=404 body='{"ok": false, "error": "E_NOT_FOUND", "code": "not-found", "message": "未知路径: /api/__no_such_path__", "hint": "可用端点见 CONT' | PASS |
| §2 静态前缀内不存在文件 → 404 且不返回文件字节 | 见节首命令 | status=404 ct='text/plain; charset=utf-8' body='404 not found：__no_such_asset__.js\n'（契约未规定该情形 body 必须 JSON；实现为 text/plain，已记入已知限制） | PASS |
| §1 静态路径穿越 400 bad-path：GET /assets/../CONTRACT.md HTTP/1.1 | 见节首命令 | status='HTTP/1.1 400 Bad Request' code='bad-path' | PASS |
| §1 静态路径穿越 400 bad-path：GET /assets/%2e%2e/CONTRACT.md HTTP/1.1 | 见节首命令 | status='HTTP/1.1 400 Bad Request' code='bad-path' | PASS |
| §1 静态路径穿越 400 bad-path：GET /vendor/..%2f..%2fserver.py HTTP/1.1 | 见节首命令 | status='HTTP/1.1 400 Bad Request' code='bad-path' | PASS |
| §6 E_BAD_REQUEST 400：POST /api/op 缺 op | 见节首命令 | status=400 error='E_BAD_REQUEST' body='{"ok": false, "error": "E_BAD_REQUEST", "message": "缺少 op", "hint": "可用: fetch, repair, geom, export, convert, validate, features, topology, dataset, sources_pr' | PASS |
| §6 E_CONSENT_REQUIRED 400：sources_probe 未带 consent:true | 见节首命令 | status=400 error='E_CONSENT_REQUIRED' body='{"ok": false, "error": "E_CONSENT_REQUIRED", "message": "sources_probe 会联网探测数据源，需要显式同意", "hint": "请求带 consent: true（或 params.consent: true）", "run": null}' | PASS |
| §6 E_VALIDATION 422：cifkit 参数越界（features.rdf_bins=0） | 见节首命令 | status=422 error='E_VALIDATION' body='{"ok": false, "error": "E_VALIDATION", "message": "cifkit 返回错误（invalid-request）：request does not match the API contract", "hint": "原始错误见 detail", "detail": {"code": "invalid-request", "message": "requ' | PASS |
| §5/§6 未知 op → 422 E_VALIDATION（不是 200/500） | 见节首命令 | status=422 error='E_VALIDATION' body='{"ok": false, "error": "E_VALIDATION", "message": "不支持的 op: __not_an_op__", "hint": "白名单: fetch, repair, geom, export, convert, validate, features, topology, da' | PASS |
| §5/§6 E_PATH_OUTSIDE 400：input.path 在 runs-dir 与 allow-import-root 之外 | 见节首命令 | status=400 error='E_PATH_OUTSIDE' body='{"ok": false, "error": "E_PATH_OUTSIDE", "message": "输入路径越界：C:\\\\Windows\\\\win.ini", "hint": "只允许 runs-dir（<临时目录>\\\\ci' | PASS |
| §6 E_NO_STRUCTURE 422：route 没解析出结构 | 见节首命令 | status=422 error='E_NO_STRUCTURE' body='{"ok": false, "error": "E_NO_STRUCTURE", "message": "没有解析出拓扑/结构名/结构 id，无法规划", "hint": "在文本里补充要什么结构：拓扑（sod）、名字（ZIF-8/GaN）、id（mp-149）或本地文件路径", "detail": {"spec": ' | PASS |
| §2 POST /api/route → 200 {ok,spec,plan,warnings} | 见节首命令 | status=200 spec.keys=['query', 'source', 'topology', 'structure_name', 'structure_id', 'typical', 'repair_h', 'repair_disorder'] plan=8 | PASS |
| §2/§6 GET /api/job/events 未知 id → 404 E_NOT_FOUND | 见节首命令 | status=404 body='{"ok": false, "error": "E_NOT_FOUND", "message": "作业不存在：__no_such_job__", "hint": "作业只保留在内存里（最多 50 条），重启即丢；用 GET /api/jo' | PASS |
| §2/§9.2 POST /api/job 缺 kind → 400/422（不启动作业） | 见节首命令 | status=422 error='E_VALIDATION' body='{"ok": false, "error": "E_VALIDATION", "message": "不支持的 kind: ", "hint": "可用: pipeline \| batch", "run": null}' | PASS |
| §2/§6 POST /api/job/cancel 缺 id → 400 E_BAD_REQUEST | 见节首命令 | status=400 body='{"ok": false, "error": "E_BAD_REQUEST", "message": "缺少 job id", "run": null}' | PASS |
| §5.2 POST /api/op → 200 {ok,op,run,result,warnings,elapsed_ms,argv} | 见节首命令 | status=200 缺=无 body='{"ok": true, "op": "validate", "run": {"id": "op-20261121-022753-validate-f73a", "rel": "op-20261121-022753-validate-f73a", "abs": "<临时目录>\\\\cifwb-verify-ru' | PASS |
| §5.2 argv 是数组（允许 []：§0 允许后端进程内调用 cifkit） | 见节首命令 | argv=[]（进程内调用；op.json.stdout 为 cifkit --json 原文） | PASS |
| §5.1 op run 目录落在 runs-dir 下且已创建 | 见节首命令 | run='op-20261121-022753-validate-f73a' abs=<临时目录>\cifwb-verify-runs\v01\op-20261121-022753-validate-f73a | PASS |
| §5.1 run 目录名符合 op-<yyyyMMdd-HHmmss>-<op>-<4位随机> | 见节首命令 | name=op-20261121-022753-validate-f73a | PASS |
| §3.2 RunSummary 键齐全 + kind='op' | 见节首命令 | 缺=无 kind='op' | PASS |
| §5.3 op.json 落盘且键齐全（schema=cifkit-op/v1） | 见节首命令 | 文件存在=True 缺=无 | PASS |
| §3.2 GET /api/run → {ok,run:RunSnapshot} 键齐全 | 见节首命令 | status=200 缺=无 | PASS |
| §3.2 artifacts 每项键齐全 | 见节首命令 | n=3 缺=无 | PASS |
| §2/§3.2 GET /api/runs 含刚创建的 run 且每行键齐全（n=3） | 见节首命令 | rows=['op-20261121-022753-validate-f73a', 'op-20261121-022742-validate-7091', 'op-20261121-022742-features-9993'] 缺键=无 | PASS |
| 不变量 3：5 个逃逸请求全部 400 E_PATH_OUTSIDE | 见节首命令 | 异常=无 | PASS |
| 不变量 4 正向：白名单内 artifact input.cif → 200 字节 | 见节首命令 | status=200 ct='chemical/x-cif; charset=utf-8' bytes=9983 | PASS |
| 不变量 4 反向：真实存在但扩展名不在白名单 → 非 200（不泄漏字节） | 见节首命令 | status=422 body='{"ok": false, "error": "E_VALIDATION", "message": "不支持下载的扩展名：.exe", "hint": "白名单：[\'.cfg\', \'.cif\', \'.' | PASS |
| 不变量 1/5：25 个 GET（首页/静态/快照/artifact）后 runs 目录签名零变化 | 见节首命令 | 变化=无 非200=[] | PASS |
| §4.1 POST /api/run → meta + stage + done 帧齐全 | 见节首命令 | status=200 events=['meta', 'stage', 'stage', 'stage', 'stage', 'stage', 'delta', 'notice', 'stage', 'stage', 'stage', 'stage'] err=None | PASS |
| §4.1 stage.index 对应 Bootstrap.scenarios[kickoff].stages[index] | 见节首命令 | stages=[{'index': 0, 'label': '解析请求', 'status': 'running'}, {'index': 1, 'label': '确认计划', 'status': 'running'}, {'index': 2, 'label': '启动管线', 'status': 'running'}, {'index': 3, 'label': '流式进度', 'status': 'running'}] 契约阶段数=5 | PASS |
| §6 E_EXEC_DISABLED 403：未开 --allow-exec 时 POST /api/op | 见节首命令 | status=403 error='E_EXEC_DISABLED' body='{"ok": false, "error": "E_EXEC_DISABLED", "message": "服务以只读模式运行（未开 --allow-exec），拒绝执行 op", "hint": "用 --allow-exec 启动；或只使用 GET 浏览端点", "run": null}' | PASS |
| §3.1/§9.5 runs-dir 不可写：Bootstrap.caps.runs_writable=false | 见节首命令 | status=200 caps={'exec': True, 'runs_writable': False, 'network': False} | PASS |
| §6/§9.5 E_READONLY_RUNS 403：runs-dir 不可写时写动作不静默失败 | 见节首命令 | status=403 error='E_READONLY_RUNS' body='{"ok": false, "error": "E_READONLY_RUNS", "message": "runs-dir 不可写：<cifkit 检出>\\\\\\\_verify\\\\_ro\\\\runs_is_a_file", "hint": "改用可写的 --runs-dir，或只用 GET 浏览端点", "run": null}' | PASS |
| §9.5 runs-dir 不可写时浏览路径照常可用 | 见节首命令 | GET /api/runs status=200 | PASS |
| §2 端点覆盖：18 个 (方法,路径) 全部被测到（自检覆盖矩阵） | 见节首命令 | 未覆盖=无 | PASS |
| §6 E_CONFLICT 409 不在 v01 构造（需并发长作业）；由 v02_adversarial 的「同名作业在跑 → 409 E_CONFLICT」覆盖并 PASS | 见节首命令/下方复现 |  | NOTE |
| §6 E_CANCELLED 409 | 见节首命令/下方复现 | 契约未定义可观测触发路径（取消后作业返回 cancelled 而非 409）；未构造 | BLOCKED |
| §6 E_TIMEOUT 504 | 见节首命令/下方复现 | 需要 >op_timeout_s 的 op / 不可控内部异常；本轮未构造 | BLOCKED |
| §6 E_INTERNAL 500 | 见节首命令/下方复现 | 需要 >op_timeout_s 的 op / 不可控内部异常；本轮未构造 | BLOCKED |

未覆盖/旁证：
- §6 `E_CONFLICT` 不在 v01 构造（需并发长作业），由 v02「同名作业在跑 → 409 E_CONFLICT」覆盖并 PASS。
- §6 `E_CANCELLED` / `E_TIMEOUT` / `E_INTERNAL` 未构造，见 §6 未验证清单。

## 2. v02 对抗/边界/并发/取消/SSE

命令原文：`"<cifkit 检出>\.venv\Scripts\python.exe" _verify\v02_adversarial.py`
（覆盖坏 JSON、NaN/Infinity、未知 op、额外字段、`..` 逃逸、超大上传、artifact 穿越、并发 op×6、§9.2 job 形状/围栏、同名作业 409、取消语义、未知 job SSE、失败留痕、SSE keepalive）

| 判据 | 命令/证据 | 观察到的输出摘要 | 结果 |
|---|---|---|---|
| 坏 JSON → 400 E_BAD_REQUEST（500 即失败） | 见节首命令 | status=400 body='{"ok": false, "error": "E_BAD_REQUEST", "message": "JSON 解析失败: Expecting value: line 1 column 30 (char 29)", "hint": "请求体必须是 UTF-8 JSON", "run": null}' | PASS |
| 非有限数字字面量 NaN → 拒绝（非 200/500，RFC 8259 不容） | 见节首命令 | status=400 body='{"ok": false, "error": "E_BAD_REQUEST", "message": "缺少 input（三选一：run+name / path / cif_text）", "hint": "例如 {\\"cif_text\\": \\"...\\"} 或 {\\"run\\": \\"op-...\\", \\"name\\": \\"repaired.cif\\"}", "run": {"id": "' | PASS |
| 非有限数字字面量 Infinity → 拒绝（非 200/500，RFC 8259 不容） | 见节首命令 | status=400 body='{"ok": false, "error": "E_BAD_REQUEST", "message": "缺少 input（三选一：run+name / path / cif_text）", "hint": "例如 {\\"cif_text\\": \\"...\\"} 或 {\\"run\\": \\"op-...\\", \\"name\\": \\"repaired.cif\\"}", "run": {"id": "' | PASS |
| 非有限数字字面量 -Infinity → 拒绝（非 200/500，RFC 8259 不容） | 见节首命令 | status=400 body='{"ok": false, "error": "E_BAD_REQUEST", "message": "缺少 input（三选一：run+name / path / cif_text）", "hint": "例如 {\\"cif_text\\": \\"...\\"} 或 {\\"run\\": \\"op-...\\", \\"name\\": \\"repaired.cif\\"}", "run": {"id": "' | PASS |
| params 额外字段 → 422 E_VALIDATION（§5 白名单） | 见节首命令 | status=422 error='E_VALIDATION' body='{"ok": false, "error": "E_VALIDATION", "message": "op=validate 的参数不在白名单：[\'bogus_key\']", "hint": "允许的参数: (无)；不接受 argv", "detail": {"violations": ["params.bogus_key"]}, "run": null}' | PASS |
| 顶层 argv 注入 → 不执行外部命令（无注入标记 / 非 500） | 见节首命令 | status=422 含标记=False body='{"ok": false, "error": "E_VALIDATION", "message": "请求体未知字段：[\'argv\']", "hint": "允许字段: [\'consent\', \'input\', \'op\', \'params\', \'run\']", "detail": {"violations": ["argv"]}, "run": null}' | PASS |
| input 路径逃逸 4 例全部 400 E_PATH_OUTSIDE | 见节首命令 | 异常=无 | PASS |
| artifact name 逃逸 5 例全部非 200（不穿越） | 见节首命令 | 异常=无 | PASS |
| cif_text 超 max_upload_bytes(5242880) → 4xx（非 200），耗时 0.3s | 见节首命令 | status=422 error='E_VALIDATION' body='{"ok": false, "error": "E_VALIDATION", "message": "cif_text 超过上限 5242880 字节", "run": {"id": "op-20261121-022938-validate-918a", "rel": "op-20261121-022938-valid' | PASS |
| 16MB 请求体 → 4xx 且服务不崩（随后 /api/bootstrap 仍 200） | 见节首命令 | status=422 elapsed=0.1s body='{"ok": false, "error": "E_VALIDATION", "message": "请求体未知字段：[\'x\']", "hint": "允许字段: [\'consent\', \'input\', \'op\', \'params\', \''；存活 bootstrap status=200 | PASS |
| 符号链接逃逸 | 见节首命令/下方复现 | 本机无法创建 symlink：OSError: [WinError 1314] 客户端没有所需的特权。: '<cifkit 检出>\\tests\\data\\zif8_cr_sod.cif' -> '<临时目录>\\cifwb-verify-runs\\v02\\verify_link.cif' | BLOCKED |
| 并发 6x validate：全部 200、互不 500，run 目录两两不同且数量=6 | 见节首命令 | 状态=[200] 唯一rel=6 新建目录=6 样例=['op-20261121-022939-validate-07cf', 'op-20261121-022939-validate-96fd', 'op-20261121-022940-validate-9cec'] | PASS |
| POST /api/job kind=pipeline(source=local) → 200 {ok,job} | 见节首命令 | status=200 采用body={'kind': 'pipeline', 'payload': {'spec': {'query': '<cifkit 检出>\\tests\\data\\zif8_cr_sod.cif', 'source': 'local', 'structure_id': '<cifkit 检出>\\tests\\data\\zif8_cr_sod.cif'}, 'outdir': '<临时目录>\\cifwb-verify-runs\\v02\\job_o | PASS |
| §3.4 Job 键齐全 + kind='pipeline' + argv 是数组 | 见节首命令 | 缺=无 kind='pipeline' argv=['<cifkit 检出>\\.venv\\Scripts\\python.exe', '-m', 'cifkit', 'pipeline', '--spec', '<临时目录>\\cifwb-verify-runs\\v02\\._jobspecs\\job_out_a.json', '--outdir', '<临时目录>\\cifwb-verify-runs\\v02\\ | PASS |
| §9.2 pipeline 缺 payload → 422 E_VALIDATION | 见节首命令 | status=422 body='{"ok": false, "error": "E_VALIDATION", "message": "pipeline 需要 payload.query 或 payload.spec", "hint": "如 {\\"kind\\":\\"pipeline\\",\\"payload\\":{\\"query\\":\\"...\\"}}' | PASS |
| §9.2 payload.query 与 payload.spec 互斥 → 422 E_VALIDATION | 见节首命令 | status=422 body='{"ok": false, "error": "E_VALIDATION", "message": "payload.query 与 payload.spec 互斥", "hint": "二选一：给自然语言 query，或给现成的 TaskSpec 对象", "run": null}' | PASS |
| §9.2 payload 未知键 → 422 E_VALIDATION（不静默忽略） | 见节首命令 | status=422 body='{"ok": false, "error": "E_VALIDATION", "message": "pipeline 的 payload 未知键：[\'bogus_key\']", "hint": "允许: [\'bundles\', \'formats\', \'ingest\', \'outdir\', \'query\', \'sour' | PASS |
| §9.2 outdir 逃逸 runs-dir → 400 E_PATH_OUTSIDE | 见节首命令 | status=400 body='{"ok": false, "error": "E_PATH_OUTSIDE", "message": "outdir 必须落在 runs-dir 内：<cifkit 检出>\\\\..\\\\cifwb-escaped-outdir", "hint": "runs-dir = C:\\\\User' | PASS |
| §9.2 outdir 已存在且非空 → 409 E_CONFLICT | 见节首命令 | status=409 body='{"ok": false, "error": "E_CONFLICT", "message": "outdir 已存在且非空：pre_existing_nonempty", "hint": "换一个 outdir；工作台不覆盖已有目录", "run": null}' | PASS |
| §6/不变量 2：同名作业在跑 → 第二个 409 E_CONFLICT（不静默并发写同一目录） | 见节首命令 | status=409 error='E_CONFLICT' body='{"ok": false, "error": "E_CONFLICT", "message": "同一 outdir 已有 running 作业：job_out_a", "hint": "先 cancel 或等它结束", "run": null}' | PASS |
| §4.2 运行中作业 SSE：meta 帧 + 至少 1 条 log/step | 见节首命令 | status=200 events=['meta', 'log', 'step', 'log', 'step', 'log', 'step', 'log', 'step', 'log'] keepalive=0 | PASS |
| §4.2 运行中作业 SSE 的 keepalive：首个作业在观察窗内已结束，改由下方专用长作业验证 | 见节首命令/下方复现 |  | NOTE |
| §4.2 作业 SSE 在空闲 >15s 时发送 keepalive 注释帧（专用长作业，45s 窗口） | 见节首命令 | keepalive=[': keepalive', ': keepalive', ': keepalive'] 共 3 次；窗口=54.2s done_in_window=False events=['log', 'meta', 'step'] | PASS（口径差异已记录，见 §6） |
| 长作业最终 status='done' 且 returncode=0 | 见节首命令 | status=done rc=0 elapsed=19.2 | PASS |
| §3.4 作业产物目录存在（run_dir 落盘） | 见节首命令 | run_dir=<临时目录>\cifwb-verify-runs\v02\job_out_a | PASS |
| §4.2 已结束作业 SSE 可重放：log 帧 + done | 见节首命令 | status=200 events=['meta', 'log', 'step', 'log', 'step', 'log', 'step', 'log', 'step', 'log', 'step', 'log'] | PASS |
| §2 POST /api/job/cancel → 200 {ok,job} | 见节首命令 | status=200 body='{"ok": true, "job": {"id": "job_out_b", "kind": "pipeline", "status": "running", "started_at": "2026-11-21T02:31:12+0800", "ended_at": null, "elapsed_s": 17.1, "argv": ["<cifkit 检出>\\\\.' | PASS |
| 不变量 6：cancel 后 5s 内 status='cancelled' | 见节首命令 | status=cancelled 用时=2.03s | PASS |
| §9.4 cancel 后 returncode 为 null 或 -9/-15 | 见节首命令 | returncode=None | PASS |
| §6 E_CANCELLED 409 未构造：重复 cancel 返回 status=200 error='' | 见节首命令/下方复现 |  | NOTE |
| 不变量 6：取消后产物目录保留（不删） | 见节首命令 | run_dir=<临时目录>\cifwb-verify-runs\v02\job_out_b 仍存在 | PASS |
| 未知 job 的 SSE → 404 E_NOT_FOUND 或 error/done 帧（不悬挂） | 见节首命令 | status=404 events=[] timed_out=False raw=['{"ok": false, "error": "E_NOT_FOUND", "message": "作业不存在：__no_such_job__", "hint": "作业只保留在内存里（最多 50 条），重启即丢；用 GET /api/jobs 看当前列表", "run": null}'] | PASS |
| GET /api/op（写端点用 GET）→ 非 200（404/405，不产生 run） | 见节首命令 | status=405 body='{"ok": false, "error": "E_METHOD_NOT_ALLOWED", "code": "method-not-allowed", "message": "405 method not allowed：GET（只接受 ' | PASS |
| POST /api/job/cancel 缺 id → 400 E_BAD_REQUEST（非 500） | 见节首命令 | status=400 body='{"ok": false, "error": "E_BAD_REQUEST", "message": "缺少 job id", "run": null}' | PASS |
| §1 HEAD /api/bootstrap → 200 且无 body | 见节首命令 | status=200 body_len=0 | PASS |
| §9.6/不变量 7：失败的 op 返回 run 字段 + 该目录落 op.json（ok:false + stderr 原文） | 见节首命令 | status=422 error='E_VALIDATION' run字段=True dir=op-20261121-023130-validate-19bf 新目录=['op-20261121-023130-validate-19bf'] op.json.ok=False | PASS |

## 3. v03 端到端（真跑 cifkit，离线数据）

命令原文：`"<cifkit 检出>\.venv\Scripts\python.exe" _verify\v03_e2e.py`
（fixture：`tests/data/zif8_cr_sod.cif`（138 原子）与 `tests/data/zif8_raw_hfree.cif`；runs-dir 在系统临时目录）

| 判据 | 命令/证据 | 观察到的输出摘要 | 结果 |
|---|---|---|---|
| validate 与 cifkit.api.validate 逐检查一致（9 项） | 见节首命令 | 一致=True cifkit=[('natoms', True), ('composition', True), ('finite_coordinates', True), ('lattice', True), ('duplicate_atoms', True), ('min_distance', True), ('hydrogen', True), ('carbon_coordination', True), ('charge', True)] op=[('natoms', True), ('composition', True), ('finite_coordinates', True), ('lattice', True),  | PASS |
| validate 结论 ok / metrics.natoms 与 cifkit 一致 | 见节首命令 | op.ok=True cifkit.ok=True op.natoms=138 cifkit.natoms=138 | PASS |
| validate run 目录 + op.json 落盘 | 见节首命令 | run=<临时目录>\cifwb-verify-runs\v03\op-20261121-022053-validate-d9b4 op.json=True | PASS |
| geom 超胞产物落盘且原子数 =138*2=276（实测 276） | 见节首命令 | status=200 product=<临时目录>\cifwb-verify-runs\v03\op-20261121-022058-geom-4f3b\geom-supercell.cif natoms=276 run=<临时目录>\cifwb-verify-runs\v03\op-20261121-022058-geom-4f3b | PASS |
| geom run 目录 + op.json 落盘 | 见节首命令 | run=<临时目录>\cifwb-verify-runs\v03\op-20261121-022058-geom-4f3b | PASS |
| convert 产出 vasp/xyz 文件真落盘且非空 | 见节首命令 | status=200 files={'input.vasp': 9790, 'input.xyz': 8524} run=<临时目录>\cifwb-verify-runs\v03\op-20261121-022102-convert-7e62 | PASS |
| convert 的 vasp 结构可被 pymatgen 读回且原子数=138 | 见节首命令 | vasp=<临时目录>\cifwb-verify-runs\v03\op-20261121-022102-convert-7e62\input.vasp natoms=138 | PASS |
| convert run 目录 + op.json 落盘 | 见节首命令 | run=<临时目录>\cifwb-verify-runs\v03\op-20261121-022102-convert-7e62 | PASS |
| repair(zif8_cr_sod) 产物 repaired.cif 落盘 | 见节首命令 | status=200 product=<临时目录>\cifwb-verify-runs\v03\op-20261121-022105-repair-a6e8\repaired.cif run=<临时目录>\cifwb-verify-runs\v03\op-20261121-022105-repair-a6e8 body='{"ok": true, "op": "repair", "run": {"id": "op-20261121-022105-repair-a6e8", "re | PASS |
| repair run 目录 + op.json 落盘 | 见节首命令 | run=<临时目录>\cifwb-verify-runs\v03\op-20261121-022105-repair-a6e8 | PASS |
| repair(缺氢 zif8_raw_hfree.cif) 补氢后原子数增加（156 → 276） | 见节首命令 | status=200 before=156 after=276 product=<临时目录>\cifwb-verify-runs\v03\op-20261121-022107-repair-9897\repaired.cif | PASS |
| pipeline 作业启动 → 200 {ok,job} | 见节首命令 | 采用body={'kind': 'pipeline', 'payload': {'spec': {'query': '<cifkit 检出>\\tests\\data\\zif8_cr_sod.cif', 'source': 'local', 'structure_id': '<cifkit 检出>\\tests\\data\\zif8_cr_sod.cif'}}} 尝试=[(200, '{"ok": true, "job": {"id": "job-20261121-022112-pi')] | PASS |
| pipeline 作业 status='done' 且 returncode=0 | 见节首命令 | status=done rc=0 elapsed_s=6.2 argv=['<cifkit 检出>\\.venv\\Scripts\\python.exe', '-m', 'cifkit', 'pipeline', '--spec', '<临时目录>\\cifwb-verify-runs\\v03\\._jobspecs\\job-20261121-022112-pipeline-42c6.json', '--outdir', '<用户主目录>\\AppData\\Local\\Temp\\d | PASS |
| §9.2 outdir 缺省 → job-<yyyyMMdd-HHmmss>-pipeline-<4随机> 且落在 runs-dir 内 | 见节首命令 | run_dir=<临时目录>\cifwb-verify-runs\v03\job-20261121-022112-pipeline-42c6 parent=<临时目录>\cifwb-verify-runs\v03 | PASS |
| pipeline 产物 manifest.json + report.md 真落盘 | 见节首命令 | run_dir=<临时目录>\cifwb-verify-runs\v03\job-20261121-022112-pipeline-42c6 manifest=True report.md=True report_bytes=3856 | PASS |
| manifest.json 可解析且含 steps（非空） | 见节首命令 | keys=['artifacts', 'created_at', 'environment', 'failed_step', 'library', 'ok', 'query', 'schema', 'source', 'spec', 'steps', 'tool_version'] n_steps=7 | PASS |
| §3.4 作业 log_tail 保留子进程原文（非空） | 见节首命令 | n_lines=200 tail=[' "bundles": {}', '}'] | PASS |
| §3.2 pipeline RunSnapshot：manifest/report_md/steps 非空 | 见节首命令 | status=200 manifest=dict report_md_len=3576 steps=7 | PASS |
| 耗时记录完整（6 个动作） | 见节首命令 | validate:wall=5.30s/ms=5117；geom:wall=3.33s/ms=3297；convert:wall=1.94s/ms=1876；repair(cr_sod):wall=2.20s/ms=2166；repair(hfree):wall=3.96s/ms=3893；pipeline:wall=6.63s/ms=6200 | PASS |

耗时记录（wall / 服务端 elapsed_ms）：

```
validate wall= 5.30s server_elapsed_ms= 5117.0 status=200
geom wall= 3.33s server_elapsed_ms= 3297.4 status=200
convert wall= 1.94s server_elapsed_ms= 1875.9 status=200
repair(cr_sod) wall= 2.20s server_elapsed_ms= 2165.8 status=200
repair(hfree) wall= 3.96s server_elapsed_ms= 3892.7 status=200
pipeline wall= 6.63s server_elapsed_ms= 6200.0 status=done
[PASS] 耗时记录完整（6 个动作） validate:wall=5.30s/ms=5117；geom:wall=3.33s/ms=3297；convert:wall=1.94s/ms=1876；repair(cr_sod):wall=2.20s/ms=2166；repair(hfree):wall=3.96s/m
```

关键对照：`validate` 的 9 项 checks 名称与 ok 值、metrics.natoms=138 与 `cifkit.api.validate(path=...)` 完全一致；geom supercell=[2,1,1] 后 pymatgen 读回 276 原子 = 138×2；convert 的 `input.vasp` 可被 pymatgen 读回 138 原子；repair 缺氢样例 156 → 276 原子；pipeline 的 `manifest.json`（7 steps）与 `report.md`（3856 字节）真落盘。

## 4. v04 浏览器（Edge headless：在线 + 离线）

命令原文：`"<cifkit 检出>\.venv\Scripts\python.exe" _verify\v04_browser.py`

| 判据 | 命令/证据 | 观察到的输出摘要 | 结果 |
|---|---|---|---|
| v04[静态] 页面/资源零 CDN 外链（扫描 web/ 非 vendor 的 0 处命中应为 0） | 见节首命令 | 命中=无 | PASS |
| v04[HTTP] GET / → 200 text/html + no-store | 见节首命令 | status=200 ct='text/html; charset=utf-8' bytes=16457 | PASS |
| v04[HTTP] GET /api/bootstrap → 200 且 scenarios/caps 有值 | 见节首命令 | status=200 scenarios=6 caps={'exec': True, 'runs_writable': True, 'network': False} | PASS |
| v04[HTTP] index.html 引用的 16 个本地静态资源全部 200 | 见节首命令 | 失败=无 样例=['assets/app.css', 'assets/app.js', 'assets/base.css', 'assets/cif-registry.js', 'assets/cif.js', 'assets/components.css'] | PASS |
| v04 浏览器像素/DOM/console/网络（Edge headless） | 见节首命令/下方复现 | BLOCKED-ENV：RuntimeError: Edge 进程已退出 exit=21（见 edge-63558.log）；Edge 原始日志尾部='[1121/022101.938:ERROR:third_party\\crashpad\\crashpad\\client\\crash_report_database_win.cc:686] CreateDirectory <临时目录>\\cifwb-edge-i380jsxl\\Crashpad: 拒绝访问。 (0x5)\n[1121/022101.938:ERROR:third_part | BLOCKED-ENV |

Edge FATAL 原文（BLOCKED-ENV 证据，来自 `_verify/logs/edge-<port>.log`）：

```
FATAL:mojo\public\cpp\platform\platform_channel.cc:187] Check failed: . : 拒绝访问。 (0x5)
ERROR:crashpad_client_win.cc:457] OpenProcess: 拒绝访问。 (0x5)
ERROR:chrome\browser\process_singleton_win.cc:942] Lock file can not be created! Error code: 5
ERROR:chrome\app\chrome_main_delegate.cc:840] Failed to create a ProcessSingleton for your profile directory.
（Edge 进程 exit=21；workspace 本地 profile 与 TEMP profile 均同样失败）
```

复现（非沙箱终端）：
```
"<cifkit 检出>\.venv\Scripts\python.exe" _verify\v04_browser.py
"<cifkit 检出>\.venv\Scripts\python.exe" web\_shots\browser_shots.py --backend real
```

## 5. v05 插件独立复核（dsh-cifkit）

命令原文：`"<cifkit 检出>\.venv\Scripts\python.exe" _verify\v05_plugin.py`（内部执行 `node --check index.js/client.js`、`node dev/verify-host.mjs`、`node dev/verify-client.mjs`；**未修改插件文件**）

| 判据 | 命令/证据 | 观察到的输出摘要 | 结果 |
|---|---|---|---|
| package.json 可解析且含 name/version | 见节首命令 | name='dsh-cifkit' version='0.1.0' | PASS |
| node --check index.js exit 0 | 见节首命令 | exit=0 stderr='' | PASS |
| node --check client.js exit 0 | 见节首命令 | exit=0 stderr='' | PASS |
| node dev/verify-host.mjs exit 0（PASS 标记 242 / FAIL 标记 0） | 见节首命令 | exit=0 PASS=242 FAIL=0 | PASS |
| node dev/verify-client.mjs exit 0（PASS 标记 114 / FAIL 标记 0） | 见节首命令 | exit=0 PASS=114 FAIL=0 | PASS |

harness 原始汇总（独立重跑，以本次为准）：

- host：通过 235 / 失败 0 / 跳过 0 / 合计 235
- client：通过 113 / 失败 0 / 合计 113
- 实现者自报 client 111，本次独立重跑为 **113**（两次均 0 失败）。

## 6. 未验证 / 已知限制（诚实清单）

| 项 | 状态 | 说明与复现 |
|---|---|---|
| Edge headless 像素截图 / DOM 文本 / console / 页面加载期外网请求 | **BLOCKED-ENV（未验证）** | workspace-write 沙箱下 Edge mojo 命名管道被拒（FATAL platform_channel.cc:187 0x5，exit=21）。非像素断言（HTTP 200、静态资源 200、零 CDN 外链扫描）已 PASS。复现：`"<cifkit 检出>\.venv\Scripts\python.exe" _verify\v04_browser.py`（非沙箱终端） |
| §4.2 job SSE keepalive 的**字面**"每 15s" | PASS（口径差异已记录） | v02 最终判据（先等前序作业结束 + 45s 窗）**PASS**：3 个 `: keepalive` / 窗口 54.2s / 作业未结束；专项探针亦在 25.6s 窗收到 1 个、时序探针在 24.8s 处 1 个。实现更接近"空闲 >15s 补发"而非"无条件每 15s"；活动期不发。历史 FLAKY 来自观察窗与作业时长（5~36s）竞赛，已通过先等作业结束消除。 |
| `E_CANCELLED` (409) | 未验证 | 契约未定义可观测触发路径；实测重复 cancel 返回 200（非 409）。`POST /api/job/cancel` 语义已按 §9.4 PASS（cancelled ≤5s、returncode null、目录保留）。 |
| `E_TIMEOUT` (504) | 未验证 | 需 >`limits.op_timeout_s`(300s) 的 op；本轮未构造。 |
| `E_INTERNAL` (500) | 未验证 | 需不可控内部异常；未构造，也不应人为制造。 |
| 符号链接路径逃逸 | BLOCKED-ENV | 本机无创建 symlink 特权（WinError 1314）；已用 `..`/`%2F`/`..\` 等 9 例逃逸替代覆盖（PASS）。 |
| 静态缺失文件 404 的 body 形状 | 低风险差异 | `GET /assets/__no_such_asset__.js` → 404 但 body 为 `text/plain`（`404 not found：…`），非 §1 的 JSON `{error,code:"not-found"}`。契约未明确规定该情形 body；不泄漏字节。已记为差异。 |
| `op` 的 `argv=[]` / `op.json.stderr=""` | 文档口径差异 | 后端对单步 op 走 cifkit **进程内**调用（§0 允许），故 argv 为空、无子进程 stderr；失败原文保留在 `op.json.error.message`。§5.2 示例给的是 CLI argv。job 的 argv 为非空 CLI 调用（PASS）。 |
| 只读 runs-dir 的构造方式 | 方法说明 | 沙箱拒绝 `icacls`（exit 5），改用「`--runs-dir` 指向已存在文件」→ 服务 `caps.runs_writable=false` + 写动作 403 `E_READONLY_RUNS` + 浏览 200（§9.5 全 PASS）。 |

## 7. 证据文件清单

- 探针：`_verify/{common.py, v01_contract.py, v02_adversarial.py, v03_e2e.py, v04_browser.py, v05_plugin.py, selftest_plumbing.py, probe_sse_keepalive.py, probe_sse_timing.py, probe_ro_strategies.py, README.md}`
- 日志：`_verify/logs/{v01..v05}_console.txt`、`v01..v05` 的 `.log`（PASS/FAIL 行）、`probe_sse_keepalive.txt`、`probe_sse_timing.txt`、`v05_plugin_console.txt`、服务与 Edge 子进程日志
- 截图：`_verify/shots/`（Edge 不可用，本期未生成）
