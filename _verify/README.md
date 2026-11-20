# _verify —— 独立验收（verifier）

本目录只属于验收角色，不改实现文件（server.py / bridge.py / web/** / tests/** / dsh-plugin/**）。
判据唯一来自 CONTRACT.md 与 cifkit 自己的文档/API，绝不读实现来迁就。

## 布局

| 路径 | 作用 |
|---|---|
| common.py | 起服务（自由端口/真实子进程）、HTTP/SSE、runs 目录签名、CONTRACT 解析、作业候选请求体 |
| v01_contract.py | 契约一致性：§1/§2/§3/§5/§6 + 不变量 1/3/4/5/10 |
| v02_adversarial.py | 对抗/边界/并发/取消/SSE：坏 JSON、NaN、逃逸、超大上传、并发、取消、未知作业 |
| v03_e2e.py | 真跑 cifkit 离线数据：validate/geom/convert/repair + pipeline(source=local)，与 api.validate 对照 |
| v04_browser.py | Edge headless：在线态 + 离线态截图、console、零外网 |
| v05_plugin.py | 插件独立复核：node --check + dev/verify-host.mjs + dev/verify-client.mjs |
| selftest_plumbing.py | 验收工具自检（stub 服务器验证 HTTP/SSE/签名/CONTRACT 解析），不是对工作台的验收 |
| logs/ | 服务子进程日志、每个探针的 PASS/FAIL 行、插件的 console |
| shots/ | v04 截图（online.png / offline.png） |

## 运行

    <cifkit 检出>\.venv\Scripts\python.exe _verify\v01_contract.py
    <cifkit 检出>\.venv\Scripts\python.exe _verify\v02_adversarial.py
    <cifkit 检出>\.venv\Scripts\python.exe _verify\v03_e2e.py
    <cifkit 检出>\.venv\Scripts\python.exe _verify\v04_browser.py
    <cifkit 检出>\.venv\Scripts\python.exe _verify\v05_plugin.py

每个探针 exit 0 = 无 FAIL；BLOCKED 表示跑不了（缺前置/缺权限），不计为失败也不计为通过。
完整验收里 BLOCKED 必须变成 PASS，否则按 FAIL 处理（Lead 裁定）。

## runs 目录

探针的 runs-dir 默认在系统临时目录 %TEMP%\cifwb-verify-runs（可用 CIFWB_VERIFY_RUNS 覆盖）。
绝不写 <cifkit 检出>\runs\（本会话该目录不可写）。
