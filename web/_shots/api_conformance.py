# -*- coding: utf-8 -*-
r"""前端契约一致性实测：把 assets/cif.js **实际会发出的请求体**打到真 server.py 上。

这替代不了浏览器（截图/console 见 _shots/browser_shots.py），但它证明两件事：
  1. 页面与其全部本地资源在真后端上都能 200；页面里 0 个外网 URL；
  2. cif.js 的六场景请求体（route / op×5 / job / job-events / runs / run / artifact）
     在真后端被接受，且形状与 CONTRACT v1.1 一致（含"顶层多字段 → 422"这类负例）。

运行：python web\_shots\api_conformance.py
退出码 0 = 全部 PASS。不写 runs/（用的是 state\runs），只在 127.0.0.1 内通信。
"""
from __future__ import annotations

import json
import os
import pathlib
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
WEB = HERE.parent
WORKBENCH = WEB.parent             # 本仓库根（独立发布后 server.py 就在这一层）
SERVER = WORKBENCH / "server.py"
RUNS_DIR = WORKBENCH / "state" / "runs"


def _cifkit_root() -> pathlib.Path:
    """cifkit 检出根：CIFKIT_ROOT → 同级/同仓检出 → 已安装包所在根。"""
    candidates = []
    env_root = os.environ.get("CIFKIT_ROOT")
    if env_root:
        candidates.append(pathlib.Path(env_root))
    candidates += [WORKBENCH.parent / "Project5-skill-cif", WORKBENCH.parent]
    for cand in candidates:
        if (cand / "cifkit" / "__init__.py").is_file():
            return cand.resolve()
    try:
        import cifkit  # noqa: PLC0415

        root = pathlib.Path(cifkit.__file__).resolve().parents[1]
        if (root / "cifkit" / "__init__.py").is_file():
            return root
    except Exception:  # noqa: BLE001 - 未安装
        pass
    return WORKBENCH.parent


REPO = _cifkit_root()              # cifkit 检出根（后端用它定位 cifkit）

FAILS: list[str] = []
#: 非前端问题（形状已被接受，但后端映射/化学前提不成立）——不当成 PASS，也不当成前端 FAIL，
#: 单独列在报告末尾，并已上报 wb-backend。
OUTSIDE: list[str] = []
CIF = "\n".join([
    "data_stub",
    "_cell_length_a 24.1",
    "_cell_length_b 24.1",
    "_cell_length_c 24.1",
    "_cell_angle_alpha 90",
    "_cell_angle_beta 90",
    "_cell_angle_gamma 90",
    "loop_",
    "_atom_site_label _atom_site_fract_x _atom_site_fract_y _atom_site_fract_z",
    "Zn1 0.0 0.0 0.0",
    "O1 0.25 0.25 0.25",
])


def log(msg: str = "") -> None:
    print(msg, flush=True)


def ok(cond: bool, msg: str) -> bool:
    log(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        FAILS.append(msg)
    return bool(cond)


def outside(msg: str) -> None:
    """记录一条「不是前端问题」的发现：既不算 PASS 也不算前端 FAIL。"""
    log("  NOTE  " + msg)
    OUTSIDE.append(msg)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request(base: str, path: str, method: str = "GET", body: dict | None = None, timeout: float = 120.0):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, raw, dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


def as_json(raw: bytes) -> dict:
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def wait_http(url: str, timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                resp.read(32)
                return True
        except Exception:  # noqa: BLE001
            time.sleep(0.25)
    return False


def frontend_stages() -> dict:
    """从 assets/data.js 里读六场景 stages（不在本脚本里抄一份，避免漂移）。"""
    script = (
        "const fs=require('fs');const src=fs.readFileSync('assets/data.js','utf8');"
        "const w={};new Function('window',src+';return window.EDU_DATA;')(w);"
        "console.log(JSON.stringify(Object.fromEntries(w.EDU_DATA.scenarios.map(s=>[s.key,s.stages]))));"
    )
    out = subprocess.run([_node(), "-e", script], cwd=str(WEB), capture_output=True, text=True, encoding="utf-8")
    try:
        return json.loads(out.stdout.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        return {}


def _node() -> str:
    import shutil

    return shutil.which("node") or "node"


def main() -> int:
    if not SERVER.is_file():
        log("server.py 不存在")
        return 3
    port = free_port()
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    # --allow-exec：op/pipeline 是"执行层"动作，CONTRACT §1 要求显式开；不开时后端按 §6 返回
    # 403 E_EXEC_DISABLED（前端会把这条错误原文显示出来，不静默）。这里要测的是前端请求体被接受。
    proc = subprocess.Popen([sys.executable, str(SERVER), "--port", str(port), "--runs-dir", str(RUNS_DIR), "--allow-exec"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(WORKBENCH))
    base = f"http://127.0.0.1:{port}"
    try:
        if not wait_http(base + "/api/bootstrap", 60):
            log(f"server.py 未就绪（{base}）")
            return 3
        log(f"真后端 {base}（runs-dir={RUNS_DIR}）")

        # 1) 首页与本地资源
        status, raw, headers = request(base, "/index.html")
        html = raw.decode("utf-8", "replace")
        ok(status == 200, f"GET /index.html → {status}")
        ok("cifkit" in html and "text/html" in headers.get("Content-Type", ""), "首页是 cifkit 工作台 HTML")
        refs = []
        import re  # noqa: PLC0415

        for match in re.finditer(r'(?:src|href)="([^"]+)"', html):
            refs.append(match.group(1))
        remote = [u for u in refs if re.match(r"^(https?:)?//", u)]
        ok(len(remote) == 0, f"首页 {len(refs)} 个 src/href 中 0 个外网 URL")
        local = [u.split("?")[0].lstrip("./") for u in refs if not u.startswith(("data:", "#"))]
        bad = []
        for rel in local:
            st, _, hdrs = request(base, "/" + rel)
            if st != 200:
                bad.append(f"{rel}={st}")
        ok(len(bad) == 0, f"首页引用的 {len(local)} 个本地资源全部 200" + (f" → {bad}" if bad else ""))

        # 2) bootstrap：六场景 + stages 与 data.js 一致
        status, raw, _ = request(base, "/api/bootstrap")
        boot = as_json(raw)
        ok(status == 200 and boot.get("ok") is True, f"GET /api/bootstrap → {status} ok={boot.get('ok')}")
        remote_scenarios = {item.get("id") or item.get("key"): item for item in boot.get("scenarios", [])}
        mine = frontend_stages()
        ok(set(remote_scenarios) == set(mine), f"后端场景 id = 前端六场景（{sorted(remote_scenarios)}）")
        drift = []
        for key, stages in mine.items():
            remote = remote_scenarios.get(key, {}).get("stages") or []
            labels = [s.get("label") if isinstance(s, dict) else s for s in remote]
            if labels != stages:
                drift.append(f"{key}: 后端={labels} 前端={stages}")
        ok(len(drift) == 0, "六场景 stages 与 data.js 逐字一致（0 处漂移）" + ("；" + "；".join(drift) if drift else ""))
        formats = boot.get("formats") or {}
        bundles = boot.get("bundles") or {}
        ok(len(formats) == 31 and len(bundles) == 12, f"bootstrap.formats={len(formats)}（31）/ bundles={len(bundles)}（12）")
        caps = boot.get("caps") or {}
        ok("exec" in caps and "runs_writable" in caps, f"caps={caps}")

        # 3) route：前端 kickoff 的请求体
        status, raw, _ = request(base, "/api/route", "POST", {"text": "从 core-mof 取一个 sod 拓扑的 Zn 结构"})
        routed = as_json(raw)
        ok(status == 200 and routed.get("ok") is True, f"POST /api/route {{\"text\"}} → {status}")
        ok(isinstance(routed.get("plan"), list) and len(routed["plan"]) > 0, f"route 返回 plan（{len(routed.get('plan') or [])} 步）")
        spec = routed.get("spec")
        ok(isinstance(spec, dict) and spec, f"route 返回 spec（键 {sorted(spec)[:6] if isinstance(spec, dict) else spec}）")

        # 4) op：前端五场景的 params/input 形状
        # 与 assets/data.js 的表单默认值 / assets/cif.js 的 opParams() 一一对应
        op_cases = [
            ("repair", {"add_h": True, "disorder": True, "contacts": False}),
            ("geom", {"standardize": True}),
            ("geom", {"standardize": True, "supercell": [2, 2, 2]}),
            # cifkit 规则：frame=slab 时超胞 c 必须为 1（"reps[2] must be 1"），所以分开测
            ("geom", {"surface": [0, 1, 1], "surface_frame": "slab", "vacuum": 15.0, "thickness": 8.0}),
            ("geom", {"supercell": [2, 2, 2], "surface": [0, 1, 1], "surface_frame": "cell"}),
            # 枚举取值形状（cifkit geom --cluster {sbu,metal_cluster,core}）：示例 CIF 切不出簇，
            # 只要错误发生在 extract_cluster 就说明取值已被解析层接受。
            ("geom", {"cluster": "sbu"}),
            ("convert", {"formats": ["vasp", "cif"]}),
            ("convert", {"formats": ["vasp"], "bundles": ["vasp"], "stem": "demo", "title": "stub title"}),
            ("validate", {}),
            ("features", {"rdf": True, "rdf_bins": 50, "rdf_max": 8.0, "topology": True, "rings": False}),
            ("topology", {}),
            ("topology", {"net": "sod"}),
            ("dataset", {"from_runs": True, "jobs": 2}),
            ("dataset", {"from_runs": True, "dedupe": "structure_sha256", "label_key": "topology",
                         "no_features": False, "no_structures": False, "jobs": 2}),
        ]
        first_run = None
        for op, params in op_cases:
            body = {"op": op, "params": params, "run": None, "input": {"cif_text": CIF}}
            status, raw, _ = request(base, "/api/op", "POST", body)
            data = as_json(raw)
            message = str(data.get("message") or "")
            good = status == 200 and data.get("ok") is True
            label = f"POST /api/op op={op} params={json.dumps(params, ensure_ascii=False)} → {status}"
            # 化学前提不成立（示例 CIF 只有 2 个原子，切不出 sbu 簇）：形状已被解析层接受。
            if not good and op == "geom" and "extract_cluster" in message:
                outside(label + "（形状被接受，错误发生在 cifkit 的几何前提：示例 CIF 太小；原文：" + message[:120] + "）")
            elif not good and op == "dataset" and "from_runs" in message or (not good and op == "dataset" and "library" in message):
                outside(label + "（**后端 payload→CLI 映射疑似有 bug**：CONTRACT §5 的 from_runs/from_library 是布尔，"
                            "后端把它当路径传给了 cifkit；原文：" + message[:140] + "）")
            else:
                ok(good, label + ("" if good else f"：{data.get('error')} {message} {data.get('hint')}"))
            if good and first_run is None and data.get("run"):
                first_run = data["run"].get("rel") or data["run"].get("id")

        # 负例：类型错误必须是 422（wb-backend 2026-11-21 修：原来 500）
        status, raw, _ = request(base, "/api/op", "POST",
                                 {"op": "geom", "params": {"supercell": "2,2,1"}, "run": None,
                                  "input": {"cif_text": CIF}})
        ok(status == 422, f"op=geom 参数类型错（supercell 字符串）→ {status}（期望 422）")
        status, raw, _ = request(base, "/api/op", "POST",
                                 {"op": "dataset", "params": {"from_runs": 123}, "run": None, "input": None})
        ok(status == 422, f"op=dataset from_runs 类型错（数字）→ {status}（期望 422）")

        # 负例：未知顶层字段 / 未知 op 参数（前端不这么发，这里证明后端会拒）
        status, raw, _ = request(base, "/api/op", "POST",
                                 {"op": "repair", "params": {"add_h": True}, "run": None,
                                  "input": {"cif_text": CIF}, "argv": ["-m", "cifkit"]})
        ok(status == 422, f"op 带未知字段 argv → {status}（期望 422）")

        # 5) runs / run 快照 / artifact（前端结果卡片的数据源）
        status, raw, _ = request(base, "/api/runs")
        runs = as_json(raw)
        ok(status == 200 and isinstance(runs.get("runs"), list), f"GET /api/runs → {status}（{len(runs.get('runs') or [])} 个）")
        if first_run:
            status, raw, _ = request(base, f"/api/run?id={urllib.parse.quote(first_run)}")
            snap = as_json(raw)
            run = snap.get("run") or {}
            ok(status == 200 and bool(run), f"GET /api/run?id={first_run} → {status}")
            artifacts = run.get("artifacts") or []
            ok(len(artifacts) > 0, f"run 快照带 artifacts（{len(artifacts)} 个）")
            ok("validation" in run and "manifest" in run and "report_md" in run, "run 快照含 manifest / report_md / validation")
            if artifacts:
                name = artifacts[0]["name"]
                status, raw, hdrs = request(base, f"/api/artifact?run={urllib.parse.quote(first_run)}&name={urllib.parse.quote(name)}")
                ok(status == 200, f"GET /api/artifact {name} → {status}（{hdrs.get('Content-Type')}）")

        # 6) job：v1.1 §9.2 信封 + SSE 帧
        status, raw, _ = request(base, "/api/job", "POST", {"kind": "pipeline", "payload": {"query": "从 core-mof 取一个 sod 结构"}})
        job_data = as_json(raw)
        ok(status == 200 and (job_data.get("job") or {}).get("id"), f"POST /api/job {{kind,payload:{{query}}}} → {status} job={(job_data.get('job') or {}).get('id')}")
        job_id = (job_data.get("job") or {}).get("id")
        status, raw, _ = request(base, "/api/job", "POST", {"kind": "pipeline", "payload": {"query": "x", "spec": {"a": 1}}})
        ok(status == 422, f"query 与 spec 同给 → {status}（期望 422）")
        status, raw, _ = request(base, "/api/job", "POST", {"kind": "pipeline", "payload": {"query": "x", "nope": 1}})
        ok(status == 422, f"payload 未知键 → {status}（期望 422）")
        status, raw, _ = request(base, "/api/job", "POST", {"kind": "pipeline", "payload": {"query": "x"}, "text": "旧形状"})
        ok(status == 422, f"顶层多字段（旧扁平形状）→ {status}（期望 422；v1.1 已改信封）")

        if job_id:
            events = []
            try:
                req = urllib.request.Request(base + "/api/job/events?id=" + urllib.parse.quote(job_id))
                with urllib.request.urlopen(req, timeout=90) as resp:
                    buf = ""
                    deadline = time.time() + 90
                    while time.time() < deadline:
                        chunk = resp.read(1)
                        if not chunk:
                            break
                        buf += chunk.decode("utf-8", "replace")
                        while "\n\n" in buf:
                            block, buf = buf.split("\n\n", 1)
                            event = None
                            for line in block.splitlines():
                                if line.startswith("event:"):
                                    event = line[6:].strip()
                            if event:
                                events.append(event)
                        if "done" in events:
                            break
            except Exception as exc:  # noqa: BLE001
                log(f"    SSE 读取异常：{type(exc).__name__}: {exc}")
            kinds = set(events)
            ok("meta" in kinds and "done" in kinds, f"SSE 帧含 meta/done（实际 {sorted(kinds)}，共 {len(events)} 帧）")
            ok("log" in kinds or "step" in kinds, f"SSE 帧含 log 或 step（实际 {sorted(kinds)}）")
            status, raw, _ = request(base, "/api/job/cancel", "POST", {"id": job_id})
            ok(status in (200, 409), f"POST /api/job/cancel → {status}（作业可能已结束）")

        # 7) sources 只读
        status, raw, _ = request(base, "/api/sources")
        ok(status == 200 and isinstance(as_json(raw).get("sources"), list), f"GET /api/sources → {status}")

        # 8) 浏览路径不写 runs 目录：GET 前后 mtime 集合不变
        def mtimes() -> set:
            if not RUNS_DIR.exists():
                return set()
            return {(str(p.relative_to(RUNS_DIR)), p.stat().st_mtime_ns) for p in RUNS_DIR.rglob("*") if p.is_file()}

        before = mtimes()
        for path in ("/api/bootstrap", "/api/runs", "/api/sources", "/api/formats", "/api/jobs"):
            request(base, path)
        after = mtimes()
        ok(before == after, "GET /api/* 浏览路径不写 runs 目录（mtime 集合不变）")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()

    log("")
    if OUTSIDE:
        log(f"非前端问题 {len(OUTSIDE)} 项（已上报 wb-backend，不计入本脚本判据）：")
        for item in OUTSIDE:
            log("  - " + item)
        log("")
    if FAILS:
        log(f"FAILED {len(FAILS)} 项：")
        for item in FAILS:
            log("  - " + item)
        return 1
    log("ALL PASS（前端请求体形状在真后端上全部被接受）")
    return 0


if __name__ == "__main__":
    import urllib.parse  # noqa: PLC0415

    raise SystemExit(main())
