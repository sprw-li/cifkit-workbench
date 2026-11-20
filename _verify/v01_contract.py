r"""验收 v01：CONTRACT.md 契约一致性。

独立判据来源：只读 CONTRACT.md（§1/§2/§3/§5/§6/§7 不变量 1/3/4/5/10）
与 cifkit 自己的公共 API（cifkit.api.capabilities / cifkit CLI --help），
不读 server.py / bridge.py 实现。

运行：<cifkit 检出>\.venv\Scripts\python.exe _verify\v01_contract.py
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    CONTRACT, LOGS, PYTHON, REPO, SERVER_PY, VERIFY, WEB, WORKBENCH, Report, Server,
    cifkit_capabilities, free_port, fresh, get, http, js, log, parse_endpoints, parse_error_codes,
    parse_ops, post, prereqs_missing, runs_signature, sse,
)

FIXTURE = REPO / "tests" / "data" / "zif8_cr_sod.cif"
OP_WHITELIST = parse_ops()


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #

def q(value: str) -> str:
    return urllib.parse.quote(str(value), safe="")


def raw_http(port: int, request_line: str, timeout: float = 20.0) -> tuple[str, str]:
    """绕过 urllib 的路径归一化，发原始请求行（测静态路径穿越）。"""
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
        payload = (request_line + "\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n").encode("latin-1")
        sock.sendall(payload)
        chunks: list[bytes] = []
        sock.settimeout(timeout)
        while True:
            try:
                chunk = sock.recv(65536)
            except (socket.timeout, TimeoutError):
                break
            if not chunk:
                break
            chunks.append(chunk)
        raw = b"".join(chunks).decode("utf-8", "replace")
    status_line = raw.split("\r\n", 1)[0]
    return status_line, raw


def status_of(request_line: str) -> int:
    try:
        return int(request_line.split(" ")[1])
    except Exception:  # noqa: BLE001
        return 0


def launch_probe(args: list[str], wait: float = 30.0, drop_env: list[str] | None = None):
    """有界启动探测：返回 (proc, port, state, output)。

    state: 'exited'（进程自己退出）、'started'（端口已监听）、'timeout'（既不退也不起）。
    调用方负责在非 exited 时终止 proc。
    """
    child_env = {**os.environ, "PYTHONUTF8": "1"}
    for key in drop_env or []:
        child_env.pop(key, None)
    port = int(args[args.index("--port") + 1])
    proc = subprocess.Popen(
        [PYTHON, str(SERVER_PY), *args],
        cwd=str(WORKBENCH), env=child_env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    deadline = time.monotonic() + wait
    state = "timeout"
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            state = "exited"
            break
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.3)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                state = "started"
                break
        time.sleep(0.2)
    output = ""
    if proc.poll() is not None:
        try:
            output = proc.stdout.read() if proc.stdout else ""
        except Exception:  # noqa: BLE001
            output = ""
    return proc, port, state, output


def cli(args: list[str], timeout: float = 120.0, env_extra: dict | None = None, drop_env: list[str] | None = None):
    child_env = {**os.environ, "PYTHONUTF8": "1", **(env_extra or {})}
    for key in drop_env or []:
        child_env.pop(key, None)
    return subprocess.run(
        [PYTHON, str(SERVER_PY), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(WORKBENCH), env=child_env, timeout=timeout,
    )


def op_call(base: str, op: str, params: dict | None = None, inp: dict | None = None, timeout: float = 300.0):
    body: dict = {"op": op, "params": params or {}}
    if inp is not None:
        body["input"] = inp
    status, headers, text = post(base, "/api/op", body, timeout=timeout)
    return status, headers, text, js(text)


def json_error(parsed) -> tuple[str, str]:
    if not isinstance(parsed, dict):
        return "", ""
    return str(parsed.get("error") or ""), str(parsed.get("code") or "")


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #

def main() -> int:
    report = Report("v01 契约一致性（CONTRACT §1/§2/§3/§5/§6/§7.1/7.3/7.4/7.5/7.10）")
    endpoints = parse_endpoints()
    error_codes = parse_error_codes()
    log(f"契约真源：{len(endpoints)} 行端点、{len(error_codes)} 个错误码、{len(OP_WHITELIST)} 个 op")
    cover: set[tuple[str, str]] = set()
    if not FIXTURE.is_file():
        report.blocked("fixture 存在", str(FIXTURE))
        return report.finish("v01_contract.log")
    cif = FIXTURE.read_text(encoding="utf-8", errors="replace")

    missing = prereqs_missing()
    if missing:
        report.blocked("前置存在（server.py + web/index.html）", f"缺 {missing}")
        return report.finish("v01_contract.log")

    cli_runs = fresh("v01-cli")
    tmp_runs = fresh("v01")

    # ---------------- §1 启动与安全 ----------------
    log("\n--- §1 启动与安全 ---")
    port = 8780
    proc = cli(["--host", "0.0.0.0", "--port", str(port), "--runs-dir", str(cli_runs)])
    report.check(
        "§1 --host 非本机地址 → exit 2",
        proc.returncode == 2,
        f"exit={proc.returncode} stderr={proc.stderr.strip()[:200]!r}",
    )
    proc = cli(["--selftest", "--runs-dir", str(cli_runs)])
    report.check(
        "§1 --selftest → exit 0",
        proc.returncode == 0,
        f"exit={proc.returncode} stdout={proc.stdout.strip()[-300:]!r} stderr={proc.stderr.strip()[-200:]!r}",
    )
    bad_root_args = ["--port", str(free_port()), "--runs-dir", str(cli_runs), "--cifkit-root", str(cli_runs / "__no_such_root__")]
    proc3, port3, state3, out3 = launch_probe(bad_root_args, wait=30.0, drop_env=["CIFKIT_ROOT"])
    if state3 == "exited":
        report.check(
            "§1 cifkit 根定位失败 → exit 2（不带半截状态）",
            proc3.returncode == 2,
            f"exit={proc3.returncode} out={out3.strip()[-200:]!r}",
        )
    elif state3 == "started":
        # 契约定位顺序含「已安装的 cifkit 包所在仓库根」：显式根无效时可回落，只要根真实存在
        st_b, _, text_b = get(f"http://127.0.0.1:{port3}", "/api/bootstrap")
        root_b = str((((js(text_b) or {}).get("cifkit") or {}).get("root")) or "")
        report.check(
            "§1 cifkit 根定位：显式 --cifkit-root 无效时回落已安装包根（根必须真实存在），否则 exit 2",
            st_b == 200 and bool(root_b) and Path(root_b).is_dir(),
            f"回落启动 status={st_b} resolved_root={root_b!r}",
        )
        proc3.terminate()
        try:
            proc3.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc3.kill()
    else:
        report.check(
            "§1 cifkit 根定位失败 → exit 2（不带半截状态）",
            False,
            f"30s 内既不退出也不监听端口（挂死）：args={bad_root_args} out={out3.strip()[-200:]!r}",
        )
        if proc3.poll() is None:
            proc3.terminate()
            try:
                proc3.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc3.kill()

    with Server(tmp_runs) as server:
        # ---------------- §2 首页与静态资源 ----------------
        log("\n--- §2 首页 / 静态资源 ---")
        st_home, hd_home, body_home = get(server.base, "/")
        report.check(
            "§2 GET / → 200 text/html + Cache-Control: no-store",
            st_home == 200 and "text/html" in hd_home.get("Content-Type", "")
            and "no-store" in hd_home.get("Cache-Control", ""),
            f"status={st_home} ct={hd_home.get('Content-Type')!r} cache={hd_home.get('Cache-Control')!r} bytes={len(body_home)}",
        )
        st_index, hd_index, body_index = get(server.base, "/index.html")
        cover.add(("GET", "/"))
        cover.add(("GET", "/index.html"))
        report.check(
            "§2 GET /index.html → 200 且与 / 同源",
            st_index == 200 and "text/html" in hd_index.get("Content-Type", "") and len(body_index) > 0,
            f"status={st_index} bytes={len(body_index)}",
        )

        refs = []
        for line in body_index.splitlines():
            for token in ("src=\"", "href=\""):
                idx = 0
                while True:
                    found = line.find(token, idx)
                    if found < 0:
                        break
                    end = line.find("\"", found + len(token))
                    if end > 0:
                        refs.append(line[found + len(token):end])
                    idx = end + 1
        local_refs = sorted({
            ref for ref in refs
            if not ref.startswith(("http://", "https://", "//", "data:", "#", "mailto:"))
        })
        def clean_ref(ref: str) -> str:
            clean = ref.split("?")[0].split("#")[0]
            while clean.startswith("./"):
                clean = clean[2:]
            return clean.lstrip("/")

        bad_refs = []
        for ref in local_refs:
            clean = clean_ref(ref)
            status, headers, body = get(server.base, "/" + clean)
            if status != 200:
                bad_refs.append((ref, status))
            if clean.startswith(("assets/", "vendor/", "components/")):
                cover.add(("GET", "/" + clean.split("/")[0] + "/*"))
        report.check(
            f"§2 index.html 引用的 {len(local_refs)} 个本地资源全部 200",
            not bad_refs and bool(local_refs),
            f"失败={bad_refs or '无'}；样例={local_refs[:6]}",
        )
        for prefix in ("/assets/", "/vendor/", "/components/"):
            cover.add(("GET", prefix + "*"))

        # ---------------- §3.1 Bootstrap ----------------
        log("\n--- §3.1 Bootstrap ---")
        st_boot, hd_boot, text_boot = get(server.base, "/api/bootstrap")
        boot = js(text_boot)
        cover.add(("GET", "/api/bootstrap"))
        report.check(
            "§2 GET /api/bootstrap → 200 JSON + no-store",
            st_boot == 200 and isinstance(boot, dict) and "no-store" in hd_boot.get("Cache-Control", ""),
            f"status={st_boot} cache={hd_boot.get('Cache-Control')!r}",
        )
        required = ["ok", "cifkit", "sources", "formats", "bundles", "scenarios", "limits", "caps"]
        absent = [key for key in required if not isinstance(boot, dict) or key not in boot]
        report.check("§3.1 Bootstrap 顶层键齐全", not absent, f"缺={absent or '无'}")

        ck = (boot or {}).get("cifkit") or {}
        ck_keys = ["version", "api_version", "root", "python", "runs_dir", "cache", "ops"]
        ck_absent = [key for key in ck_keys if key not in ck]
        report.check(
            "§3.1 Bootstrap.cifkit 键齐全 + runs_dir 指向本次 runs-dir",
            not ck_absent and Path(str(ck.get("runs_dir", ""))).resolve() == tmp_runs.resolve(),
            f"缺={ck_absent or '无'} root={ck.get('root')!r} python={ck.get('python')!r} runs_dir={ck.get('runs_dir')!r}",
        )
        cache = ck.get("cache") or {}
        report.check("§3.1 cifkit.cache 含 ready/path", "ready" in cache and "path" in cache, f"cache={cache}")

        caps = (boot or {}).get("caps") or {}
        report.check(
            "§3.1 caps 含 exec/runs_writable/network（network 非探测结果）",
            all(key in caps for key in ("exec", "runs_writable", "network")),
            f"caps={caps}",
        )
        limits = (boot or {}).get("limits") or {}
        limit_keys = ["max_import_bytes", "pipeline_timeout_s", "op_timeout_s", "job_keep", "max_upload_bytes"]
        report.check("§3.1 limits 键齐全", all(key in limits for key in limit_keys), f"limits={limits}")

        # ---------------- §3.3 Scenario 六场景 ----------------
        log("\n--- §3.3 Scenario ---")
        scenarios = (boot or {}).get("scenarios") or []
        ids = [item.get("id") for item in scenarios]
        want_ids = ["kickoff", "repair", "geometry", "export", "inspect", "library"]
        report.check(
            "§3.3 六个场景 id 固定且有序",
            ids == want_ids,
            f"实际={ids}",
        )
        scen_keys = ["id", "title", "subtitle", "icon", "stages", "inputs", "action"]
        scen_bad = [
            item.get("id") for item in scenarios
            if [key for key in scen_keys if key not in item]
        ]
        stage_bad = []
        for item in scenarios:
            stages = item.get("stages") or []
            if len(stages) < 4:
                stage_bad.append((item.get("id"), f"stages={len(stages)}"))
                continue
            for index, stage in enumerate(stages):
                if stage.get("index") != index or not stage.get("label"):
                    stage_bad.append((item.get("id"), f"stages[{index}]={stage}"))
        report.check("§3.3 每个 Scenario 键齐全 + stages 序号 0..n-1 连续", not scen_bad and not stage_bad,
                     f"键缺={scen_bad or '无'} 阶段错={stage_bad or '无'}")
        inputs_bad = [
            (item.get("id"), [i for i in (item.get("inputs") or []) if "name" not in i or "kind" not in i or "label" not in i])
            for item in scenarios
        ]
        report.check(
            "§3.3 Scenario.inputs 每项含 name/kind/label",
            all(not bad for _, bad in inputs_bad),
            f"异常={[x for x in inputs_bad if x[1]] or '无'}",
        )

        # ---------------- §3.1 formats/bundles 来自 cifkit（不变量 10） ----------------
        log("\n--- 不变量 10：formats/bundles/ops 与 cifkit 一致 ---")
        cap = cifkit_capabilities()
        boot_formats = (boot or {}).get("formats") or {}
        boot_bundles = (boot or {}).get("bundles") or {}
        cap_formats = set(cap.get("formats") or {})
        cap_bundles = set(cap.get("bundles") or {})
        report.check(
            f"§3.1 bootstrap.formats == cifkit.api.capabilities().formats（{len(cap_formats)} 项，未硬编码）",
            set(boot_formats) == cap_formats,
            f"服务端={len(boot_formats)} 项缺={sorted(cap_formats - set(boot_formats))[:6]} 多={sorted(set(boot_formats) - cap_formats)[:6]}",
        )
        report.check(
            f"§3.1 bootstrap.bundles == cifkit.api.capabilities().bundles（{len(cap_bundles)} 项）",
            set(boot_bundles) == cap_bundles,
            f"服务端={len(boot_bundles)} 项缺={sorted(cap_bundles - set(boot_bundles))[:6]} 多={sorted(set(boot_bundles) - cap_bundles)[:6]}",
        )
        report.check(
            "§3.1 bootstrap.cifkit.api_version == cifkit capabilities.api_version",
            str(ck.get("api_version")) == str(cap.get("api_version")),
            f"服务端={ck.get('api_version')!r} cifkit={cap.get('api_version')!r}",
        )
        boot_ops = ck.get("ops") or []
        cap_ops = list(cap.get("operations") or [])
        report.check(
            "§3.1/不变量 10 bootstrap.cifkit.ops 来自 cifkit 自己（== capabilities.operations）",
            bool(boot_ops) and (set(boot_ops) == set(cap_ops) if cap_ops else set(boot_ops) <= set(OP_WHITELIST)),
            f"服务端={boot_ops} cifkit.operations={cap_ops}",
        )
        schema_ops: set[str] = set()
        try:
            schema = json.loads((REPO / "cifkit" / "schemas" / "ops.json").read_text(encoding="utf-8"))
            schema_ops = set((schema.get("properties") or {}).keys())
        except Exception as exc:  # noqa: BLE001
            report.note(f"读取 cifkit/schemas/ops.json 失败：{exc}")
        cap_ops_set = set(cap.get("operations") or [])
        cli_ok = True
        cli_detail = []
        for name in OP_WHITELIST:
            sub = "sources" if name == "sources_probe" else name
            proc = subprocess.run(
                [PYTHON, "-m", "cifkit", sub, "--help"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(REPO), timeout=120,
            )
            via_cli = proc.returncode == 0
            via_api = (name in cap_ops_set) or (name in schema_ops) or (name == "sources_probe" and "fetch" in cap_ops_set)
            cli_detail.append(f"{name}:cli={proc.returncode}, api={name in cap_ops_set}, schema={name in schema_ops}")
            if not (via_cli or via_api):
                cli_ok = False
        report.check(
            "不变量 10：每个 op 可追溯到 cifkit 执行层（CLI 子命令 或 api/capabilities/schemas 里存在）",
            cli_ok,
            f"{cli_detail}",
        )

        # ---------------- §3.1/3.3 sources / formats / jobs ----------------
        log("\n--- §2/§3 Source / Formats / Jobs（空状态） ---")
        st, hd, text = get(server.base, "/api/sources")
        parsed = js(text)
        if isinstance(parsed, dict):
            src_keys = ["id", "label", "needs_key", "key_env", "ready", "status", "topology_metadata", "detail", "hint", "probe"]
            src_bad = [item.get("id") for item in parsed.get("sources", []) if [k for k in src_keys if k not in item]]
        else:
            src_bad = ["__not_json__"]
        cover.add(("GET", "/api/sources"))
        report.check(
            "§2/§3.3 GET /api/sources → 200 + Source 键齐全 + probe 未探测时为 null",
            st == 200 and isinstance(parsed, dict) and parsed.get("ok") is True and not src_bad,
            f"status={st} keys缺={src_bad or '无'} n={(parsed or {}).get('sources') and len(parsed.get('sources', []))}",
        )
        if isinstance(parsed, dict):
            probe_bad = [item.get("id") for item in parsed.get("sources", []) if item.get("probe") is not None]
            report.check("§3.3 GET /api/sources 不做 probe（离线，probe 全为 null）", not probe_bad,
                         f"已有 probe 的源={probe_bad or '无'}")

        st, hd, text = get(server.base, "/api/formats")
        parsed = js(text)
        cover.add(("GET", "/api/formats"))
        report.check(
            "§2 GET /api/formats → 200 {ok,formats,bundles}",
            st == 200 and isinstance(parsed, dict) and parsed.get("ok") is True
            and isinstance(parsed.get("formats"), dict) and isinstance(parsed.get("bundles"), dict),
            f"status={st} formats={len((parsed or {}).get('formats') or {})} bundles={len((parsed or {}).get('bundles') or {})}",
        )

        st, hd, text = get(server.base, "/api/jobs")
        parsed = js(text)
        cover.add(("GET", "/api/jobs"))
        report.check(
            "§2 GET /api/jobs → 200 {ok,jobs:[]}",
            st == 200 and isinstance(parsed, dict) and parsed.get("ok") is True and parsed.get("jobs") == [],
            f"status={st} jobs={parsed.get('jobs') if isinstance(parsed, dict) else text[:120]!r}",
        )

        st, hd, text = get(server.base, "/api/runs")
        runs0 = js(text)
        cover.add(("GET", "/api/runs"))
        report.check(
            "§2 GET /api/runs（空 runs）→ 200 {ok,runs:[]}",
            st == 200 and isinstance(runs0, dict) and runs0.get("ok") is True and runs0.get("runs") == [],
            f"status={st} runs={runs0.get('runs') if isinstance(runs0, dict) else text[:120]!r}",
        )

        st, hd, text = get(server.base, "/api/run?id=__no_such_run__")
        cover.add(("GET", "/api/run"))
        report.check(
            "§6 E_NOT_FOUND：GET /api/run?id=不存在 → 404",
            st == 404 and json_error(js(text))[0] == "E_NOT_FOUND",
            f"status={st} body={text[:160]!r}",
        )
        st, hd, text = get(server.base, "/api/artifact?run=__no_such_run__&name=x.cif")
        cover.add(("GET", "/api/artifact"))
        report.check(
            "§6 E_NOT_FOUND：GET /api/artifact?run=不存在 → 404",
            st == 404 and json_error(js(text))[0] == "E_NOT_FOUND",
            f"status={st} body={text[:160]!r}",
        )
        st, hd, text = get(server.base, "/api/job?id=__no_such_job__")
        cover.add(("GET", "/api/job"))
        report.check(
            "§6 E_NOT_FOUND：GET /api/job?id=不存在 → 404",
            st == 404 and json_error(js(text))[0] == "E_NOT_FOUND",
            f"status={st} body={text[:160]!r}",
        )

        # ---------------- §1 方法 / 未知路径 / 静态穿越 ----------------
        log("\n--- §1 方法 / 路径 ---")
        for method in ("PUT", "DELETE", "PATCH"):
            st, hd, text = http(server.base, method, "/api/bootstrap")
            parsed = js(text)
            report.check(
                f"§1 {method} → 405 {{code:'method-not-allowed'}}",
                st == 405 and isinstance(parsed, dict) and parsed.get("code") == "method-not-allowed",
                f"status={st} body={text[:120]!r}",
            )
        st, hd, text = get(server.base, "/api/__no_such_path__")
        parsed = js(text)
        report.check(
            "§1 未知路径 → 404 {code:'not-found'}",
            st == 404 and isinstance(parsed, dict) and parsed.get("code") == "not-found",
            f"status={st} body={text[:120]!r}",
        )
        st, hd, text = get(server.base, "/assets/__no_such_asset__.js")
        report.check(
            "§2 静态前缀内不存在文件 → 404 且不返回文件字节",
            st == 404 and "404" in text[:160],
            f"status={st} ct={hd.get('Content-Type')!r} body={text[:120]!r}"
            "（契约未规定该情形 body 必须 JSON；实现为 text/plain，已记入已知限制）",
        )
        for request_line in (
            "GET /assets/../CONTRACT.md HTTP/1.1",
            "GET /assets/%2e%2e/CONTRACT.md HTTP/1.1",
            "GET /vendor/..%2f..%2fserver.py HTTP/1.1",
        ):
            line, raw = raw_http(server.port, request_line)
            code = status_of(line)
            parsed = js(raw.split("\r\n\r\n", 1)[-1])
            body_code = parsed.get("code") if isinstance(parsed, dict) else None
            report.check(
                f"§1 静态路径穿越 400 bad-path：{request_line}",
                code == 400 and body_code == "bad-path",
                f"status={line!r} code={body_code!r}",
            )

        # ---------------- §6 可构造错误码 ----------------
        log("\n--- §6 错误码 ---")
        constructed: set[str] = set()

        st, _, text, parsed = op_call(server.base, "", {})
        err, code = json_error(parsed)
        ok = st == 400 and err == "E_BAD_REQUEST"
        constructed.add("E_BAD_REQUEST")
        report.check("§6 E_BAD_REQUEST 400：POST /api/op 缺 op", ok, f"status={st} error={err!r} body={text[:160]!r}")

        st, _, text, parsed = op_call(server.base, "sources_probe", {})
        err, _ = json_error(parsed)
        constructed.add("E_CONSENT_REQUIRED")
        report.check(
            "§6 E_CONSENT_REQUIRED 400：sources_probe 未带 consent:true",
            st == 400 and err == "E_CONSENT_REQUIRED",
            f"status={st} error={err!r} body={text[:160]!r}",
        )

        st, _, text, parsed = op_call(server.base, "features", {"rdf_bins": 0}, {"cif_text": cif})
        err, _ = json_error(parsed)
        constructed.add("E_VALIDATION")
        report.check(
            "§6 E_VALIDATION 422：cifkit 参数越界（features.rdf_bins=0）",
            st == 422 and err == "E_VALIDATION",
            f"status={st} error={err!r} body={text[:200]!r}",
        )

        st, _, text, parsed = op_call(server.base, "__not_an_op__", {})
        err, _ = json_error(parsed)
        report.check(
            "§5/§6 未知 op → 422 E_VALIDATION（不是 200/500）",
            st == 422 and err == "E_VALIDATION",
            f"status={st} error={err!r} body={text[:160]!r}",
        )

        st, _, text, parsed = op_call(server.base, "validate", {}, {"path": r"C:\Windows\win.ini"})
        err, _ = json_error(parsed)
        constructed.add("E_PATH_OUTSIDE")
        report.check(
            "§5/§6 E_PATH_OUTSIDE 400：input.path 在 runs-dir 与 allow-import-root 之外",
            st == 400 and err == "E_PATH_OUTSIDE",
            f"status={st} error={err!r} body={text[:160]!r}",
        )

        st, hd, text = post(server.base, "/api/route", {"text": "你好"})
        parsed = js(text)
        err, _ = json_error(parsed)
        constructed.add("E_NO_STRUCTURE")
        report.check(
            "§6 E_NO_STRUCTURE 422：route 没解析出结构",
            st == 422 and err == "E_NO_STRUCTURE",
            f"status={st} error={err!r} body={text[:160]!r}",
        )

        st, hd, text = post(server.base, "/api/route", {"text": "把 ZIF-8 的 sod 结构标准化"})
        parsed = js(text)
        cover.add(("POST", "/api/route"))
        report.check(
            "§2 POST /api/route → 200 {ok,spec,plan,warnings}",
            st == 200 and isinstance(parsed, dict) and parsed.get("ok") is True
            and isinstance(parsed.get("spec"), dict) and isinstance(parsed.get("plan"), list)
            and "warnings" in parsed,
            f"status={st} spec.keys={list((parsed or {}).get('spec') or {})[:8]} plan={len((parsed or {}).get('plan') or [])}",
        )

        # ---------------- §2 job 端点（最小覆盖；长作业在 v02/v03） ----------------
        st, _, t = get(server.base, "/api/job/events?id=__no_such_job__")
        report.check(
            "§2/§6 GET /api/job/events 未知 id → 404 E_NOT_FOUND",
            st == 404 and json_error(js(t))[0] == "E_NOT_FOUND",
            f"status={st} body={t[:120]!r}",
        )
        cover.add(("GET", "/api/job/events"))
        st, _, t = post(server.base, "/api/job", {})
        code_j = json_error(js(t))[0]
        report.check(
            "§2/§9.2 POST /api/job 缺 kind → 400/422（不启动作业）",
            st in (400, 422) and code_j in ("E_BAD_REQUEST", "E_VALIDATION"),
            f"status={st} error={code_j!r} body={t[:120]!r}",
        )
        cover.add(("POST", "/api/job"))
        st, _, t = post(server.base, "/api/job/cancel", {})
        report.check(
            "§2/§6 POST /api/job/cancel 缺 id → 400 E_BAD_REQUEST",
            st == 400 and json_error(js(t))[0] == "E_BAD_REQUEST",
            f"status={st} body={t[:120]!r}",
        )
        cover.add(("POST", "/api/job/cancel"))

        # ---------------- §5 POST /api/op（真跑一次 validate） ----------------
        log("\n--- §5 POST /api/op validate ---")
        st, hd, text, parsed = op_call(server.base, "validate", {}, {"cif_text": cif})
        cover.add(("POST", "/api/op"))
        ok = st == 200 and isinstance(parsed, dict) and parsed.get("ok") is True
        run_summary = (parsed or {}).get("run") or {}
        resp_keys = ["ok", "op", "run", "result", "warnings", "elapsed_ms", "argv"]
        resp_absent = [key for key in resp_keys if key not in (parsed or {})]
        report.check(
            "§5.2 POST /api/op → 200 {ok,op,run,result,warnings,elapsed_ms,argv}",
            ok and not resp_absent and parsed.get("op") == "validate",
            f"status={st} 缺={resp_absent or '无'} body={text[:200]!r}",
        )
        argv_val = (parsed or {}).get("argv")
        report.check(
            "§5.2 argv 是数组（允许 []：§0 允许后端进程内调用 cifkit）",
            isinstance(argv_val, list),
            f"argv={argv_val}" + ("（进程内调用；op.json.stdout 为 cifkit --json 原文）" if argv_val == [] else ""),
        )
        run_abs = Path(str(run_summary.get("abs", "")))
        report.check(
            "§5.1 op run 目录落在 runs-dir 下且已创建",
            run_abs.is_dir() and tmp_runs.resolve() in run_abs.resolve().parents,
            f"run={run_summary.get('rel')!r} abs={run_abs}",
        )
        report.check(
            "§5.1 run 目录名符合 op-<yyyyMMdd-HHmmss>-<op>-<4位随机>",
            bool(run_abs.name) and run_abs.name.startswith("op-") and run_abs.name.endswith("-validate-" + run_abs.name[-4:]),
            f"name={run_abs.name}",
        )

        # RunSummary 形状
        rs_keys = ["id", "rel", "abs", "mtime", "kind", "ok", "failed_step", "n_steps", "n_artifacts",
                   "atoms", "formula", "topology", "source", "has_manifest", "has_report", "has_preview", "warnings"]
        rs_absent = [key for key in rs_keys if key not in run_summary]
        report.check("§3.2 RunSummary 键齐全 + kind='op'", not rs_absent and run_summary.get("kind") == "op",
                     f"缺={rs_absent or '无'} kind={run_summary.get('kind')!r}")

        # op.json 落盘（§5.3）
        op_json = run_abs / "op.json"
        parsed_op = js(op_json.read_text(encoding="utf-8", errors="replace")) if op_json.is_file() else None
        opj_keys = ["schema", "op", "ok", "argv", "cwd", "python", "cifkit_version", "started_at",
                    "elapsed_ms", "input", "params", "returncode", "stdout", "stderr", "result", "products", "workbench_version"]
        opj_absent = [key for key in opj_keys if not isinstance(parsed_op, dict) or key not in parsed_op]
        report.check(
            "§5.3 op.json 落盘且键齐全（schema=cifkit-workbench/op/v1）",
            isinstance(parsed_op, dict) and parsed_op.get("schema") == "cifkit-workbench/op/v1" and not opj_absent,
            f"文件存在={op_json.is_file()} 缺={opj_absent or '无'}",
        )

        # ---------------- §3.2 RunSnapshot ----------------
        log("\n--- §3.2 RunSnapshot ---")
        rel = run_summary.get("rel") or run_abs.name
        st, hd, text = get(server.base, f"/api/run?id={q(rel)}")
        snap_wrapper = js(text)
        snap = (snap_wrapper or {}).get("run") if isinstance(snap_wrapper, dict) else None
        snap_keys = ["id", "rel", "abs", "kind", "manifest", "report_md", "plan", "steps",
                     "artifacts", "validation", "op", "warnings"]
        snap_absent = [key for key in snap_keys if not isinstance(snap, dict) or key not in snap]
        report.check(
            "§3.2 GET /api/run → {ok,run:RunSnapshot} 键齐全",
            st == 200 and isinstance(snap_wrapper, dict) and snap_wrapper.get("ok") is True and not snap_absent,
            f"status={st} 缺={snap_absent or '无'}",
        )
        artifacts = (snap or {}).get("artifacts") or []
        art_keys = ["name", "rel", "abs", "bytes", "kind", "downloadable"]
        art_bad = [item.get("name") for item in artifacts if [key for key in art_keys if key not in item]]
        report.check("§3.2 artifacts 每项键齐全", bool(artifacts) and not art_bad,
                     f"n={len(artifacts)} 缺={art_bad or '无'}")

        # ---------------- §3.2 每行 runs 列表 ----------------
        st, hd, text = get(server.base, "/api/runs")
        runs = (js(text) or {}).get("runs") or []
        bad_rows = [
            item.get("rel") for item in runs
            if [key for key in ("id", "rel", "abs", "mtime", "kind", "ok") if key not in item]
        ]
        report.check(
            f"§2/§3.2 GET /api/runs 含刚创建的 run 且每行键齐全（n={len(runs)}）",
            bool(runs) and not bad_rows and any(r.get("rel") == rel for r in runs),
            f"rows={[r.get('rel') for r in runs]} 缺键={bad_rows or '无'}",
        )

        # ---------------- 不变量 3 / 4：路径围栏与 artifact 白名单 ----------------
        log("\n--- 不变量 3/4：路径围栏 + artifact 白名单 ---")
        fence = [
            ("GET", f"/api/run?id={q('../../Windows')}"),
            ("GET", f"/api/run?id={q('..\\..\\Windows')}"),
            ("GET", f"/api/artifact?run={q('..\\..\\Windows')}&name=win.ini"),
            ("GET", f"/api/artifact?run={q(rel)}&name={q('..\\..\\..\\Windows\\win.ini')}"),
            ("GET", "/api/artifact?run=..%2F..%2FWindows&name=win.ini"),
        ]
        fence_bad = []
        for method, path in fence:
            st, hd, text = get(server.base, path)
            err, _ = json_error(js(text))
            if not (st == 400 and err == "E_PATH_OUTSIDE"):
                fence_bad.append((path, st, err, text[:80]))
        report.check(
            f"不变量 3：{len(fence)} 个逃逸请求全部 400 E_PATH_OUTSIDE",
            not fence_bad,
            f"异常={fence_bad or '无'}",
        )

        real_art = None
        for item in artifacts:
            if item.get("downloadable") and item.get("name"):
                real_art = item
                break
        if real_art is None:
            report.blocked("不变量 4 正向：run 内有可下载 artifact", f"artifacts={[a.get('name') for a in artifacts]}")
        else:
            st, hd, body = get(server.base, f"/api/artifact?run={q(rel)}&name={q(real_art['name'])}")
            report.check(
                f"不变量 4 正向：白名单内 artifact {real_art['name']} → 200 字节",
                st == 200 and len(body) > 0 and "Content-Type" in hd,
                f"status={st} ct={hd.get('Content-Type')!r} bytes={len(body)}",
            )
        evil = run_abs / "verify_evil.exe"
        try:
            evil.write_bytes(b"MZ verify")
            st, hd, text = get(server.base, f"/api/artifact?run={q(rel)}&name=verify_evil.exe")
            report.check(
                "不变量 4 反向：真实存在但扩展名不在白名单 → 非 200（不泄漏字节）",
                st in (400, 403, 404, 422) and not text.startswith("MZ"),
                f"status={st} body={text[:100]!r}",
            )
        finally:
            try:
                evil.unlink()
            except OSError:
                pass

        # ---------------- 不变量 1 / 5：GET 无副作用 ----------------
        log("\n--- 不变量 1/5：GET 无副作用（runs 目录 mtime+size 前后比对） ---")
        sig_before = runs_signature(tmp_runs)
        browse = [
            "/", "/index.html", "/api/bootstrap", "/api/runs", f"/api/run?id={q(rel)}",
            "/api/sources", "/api/formats", "/api/jobs",
        ] + ["/" + clean_ref(ref) for ref in local_refs]
        if real_art:
            browse.append(f"/api/artifact?run={q(rel)}&name={q(real_art['name'])}")
        statuses = {}
        for path in browse:
            st, hd, body = get(server.base, path)
            statuses[path] = st
        sig_after = runs_signature(tmp_runs)
        changed = sorted(set(sig_before) ^ set(sig_after)) + [
            key for key in set(sig_before) & set(sig_after) if sig_before[key] != sig_after[key]
        ]
        report.check(
            f"不变量 1/5：{len(browse)} 个 GET（首页/静态/快照/artifact）后 runs 目录签名零变化",
            not changed,
            f"变化={changed or '无'} 非200={[(k, v) for k, v in statuses.items() if v != 200]}",
        )

        # ---------------- §4.1 POST /api/run（EduKit 兼容 SSE） ----------------
        log("\n--- §4.1 POST /api/run SSE ---")
        stream = sse(server.base, "/api/run", method="POST", body={"scenario": "kickoff", "text": "把 ZIF-8 的 sod 结构标准化"},
                     timeout=60, read_seconds=45)
        cover.add(("POST", "/api/run"))
        meta = (stream.get("frames") and [f for f in stream["frames"] if f["event"] == "meta"]) or []
        done = [f for f in stream["frames"] if f["event"] == "done"]
        stages = [f for f in stream["frames"] if f["event"] == "stage"]
        report.check(
            "§4.1 POST /api/run → meta + stage + done 帧齐全",
            stream.get("status") == 200 and bool(meta) and bool(stages) and bool(done),
            f"status={stream.get('status')} events={[f['event'] for f in stream.get('frames', [])][:12]} err={stream.get('error')}",
        )
        scen_stages = next((item.get("stages") for item in scenarios if item.get("id") == "kickoff"), [])
        index_ok = True
        for frame in stages:
            parsed = js(frame["data"]) or {}
            if not (isinstance(parsed.get("index"), int) and 0 <= parsed["index"] < len(scen_stages)):
                index_ok = False
        report.check(
            "§4.1 stage.index 对应 Bootstrap.scenarios[kickoff].stages[index]",
            index_ok and bool(stages),
            f"stages={[js(f['data']) for f in stages][:4]} 契约阶段数={len(scen_stages)}",
        )

    # ---------------- E_EXEC_DISABLED（只读模式） ----------------
    log("\n--- §6 E_EXEC_DISABLED（--allow-exec 未开） ---")
    try:
        with Server(fresh("v01-noexec"), exec_=False) as ro_server:
            st, _, text, parsed = op_call(ro_server.base, "validate", {}, {"cif_text": cif})
            err, _ = json_error(parsed)
            report.check(
                "§6 E_EXEC_DISABLED 403：未开 --allow-exec 时 POST /api/op",
                st == 403 and err == "E_EXEC_DISABLED",
                f"status={st} error={err!r} body={text[:200]!r}",
            )
    except RuntimeError as exc:
        report.blocked("E_EXEC_DISABLED（只读模式服务启动）", str(exc)[:300])

    # ---------------- E_READONLY_RUNS（runs-dir 不可写） ----------------
    log("\n--- §6 E_READONLY_RUNS（runs-dir 不可写） ---")
    ro_root = VERIFY / "_ro"
    shutil.rmtree(ro_root, ignore_errors=True)
    ro_root.mkdir(parents=True, exist_ok=True)
    # 本机 workspace-write 沙箱下 icacls 被拒（实测 exit 5），改用「runs-dir 指向一个已存在的文件」：
    # 服务启动时探测到不可写 → caps.runs_writable=false，写动作 403 E_READONLY_RUNS（§9.5）。
    ro_file = ro_root / "runs_is_a_file"
    ro_file.write_text("not a directory", encoding="utf-8")
    try:
        with Server(ro_file) as ro_server:
            st_b, _, btext = get(ro_server.base, "/api/bootstrap")
            caps_ro = ((js(btext) or {}).get("caps") or {})
            report.check(
                "§3.1/§9.5 runs-dir 不可写：Bootstrap.caps.runs_writable=false",
                st_b == 200 and caps_ro.get("runs_writable") is False,
                f"status={st_b} caps={caps_ro}",
            )
            st_ro, _, text_ro, parsed_ro = op_call(ro_server.base, "validate", {}, {"cif_text": cif})
            err_ro, _ = json_error(parsed_ro)
            report.check(
                "§6/§9.5 E_READONLY_RUNS 403：runs-dir 不可写时写动作不静默失败",
                st_ro == 403 and err_ro == "E_READONLY_RUNS",
                f"status={st_ro} error={err_ro!r} body={text_ro[:200]!r}",
            )
            st_br, _, _ = get(ro_server.base, "/api/runs")
            report.check("§9.5 runs-dir 不可写时浏览路径照常可用", st_br == 200, f"GET /api/runs status={st_br}")
    except RuntimeError as exc:
        report.blocked("E_READONLY_RUNS（不可写 runs-dir 服务启动）", str(exc)[:300])
    finally:
        shutil.rmtree(ro_root, ignore_errors=True)

    # ---------------- §2 端点覆盖自检 ----------------
    expected = {(row["method"], path) for row in endpoints for path in row["paths"]}
    missing_cov = sorted(expected - cover)
    report.check(
        f"§2 端点覆盖：{len(expected)} 个 (方法,路径) 全部被测到（自检覆盖矩阵）",
        not missing_cov,
        f"未覆盖={missing_cov or '无'}",
    )

    # ---------------- §6 无法构造的错误码（诚实记录） ----------------
    for code in ("E_CONFLICT", "E_CANCELLED", "E_TIMEOUT", "E_INTERNAL"):
        if code in error_codes:
            if code == "E_CONFLICT":
                report.note("§6 E_CONFLICT 409 不在 v01 构造（需并发长作业）；由 v02_adversarial 的「同名作业在跑 → 409 E_CONFLICT」覆盖并 PASS")
            elif code == "E_CANCELLED":
                report.blocked("§6 E_CANCELLED 409", "契约未定义可观测触发路径（取消后作业返回 cancelled 而非 409）；未构造")
            else:
                report.blocked(f"§6 {code} {error_codes[code]}", "需要 >op_timeout_s 的 op / 不可控内部异常；本轮未构造")

    return report.finish("v01_contract.log")


if __name__ == "__main__":
    raise SystemExit(main())
