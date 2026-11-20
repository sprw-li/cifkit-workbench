r"""task-2 验收：Edge headless + CDP 实测（截图 / 外网请求 / console error / 未捕获异常）。

覆盖
----
* 离线态：静态服务器托管 web（没有 /api/*）→ 页面必须显示「离线演示（无后端）」，
  不白屏、console.error 0、未捕获异常 0、加载期外网请求 0。
* 在线态：起真后端 server.py（--allow-exec）→ 首页 + 六场景各渲染一次，断言结果卡片 /
  校验清单 / 产物 / 下一步 / 作业日志（kickoff）都在，且加载期外网请求 0。
  （server.py 不存在时本脚本直接 exit 3，不会拿假后端出"看起来像真的"截图。）

运行
----
    <cifkit 检出>\.venv\Scripts\python.exe web\_shots\browser_shots.py
    ... --backend real        # 显式要求真 server.py（不存在就 exit 3）
    ... --backend auto        # 默认：server.py 存在即用它，否则 exit 3

退出码 0 = 全部 PASS。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

try:
    import websocket  # websocket-client
except Exception as exc:  # noqa: BLE001
    print("需要 websocket-client：", exc)
    raise SystemExit(3)

HERE = pathlib.Path(__file__).resolve().parent
WEB = HERE.parent
WORKBENCH = WEB.parent             # 本仓库根（独立发布后 server.py 就在这一层）
SHOTS = HERE


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


REPO = _cifkit_root()              # cifkit 检出根（截图脚本只连真后端，不再假设同仓）
SERVER = WORKBENCH / "server.py"
RUNS_DIR = WORKBENCH / "state" / "runs"
LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]")
CIF_SAMPLE = "\n".join([
    "data_stub",
    "_cell_length_a 24.2",
    "_cell_length_b 24.2",
    "_cell_length_c 24.2",
    "_cell_angle_alpha 90",
    "_cell_angle_beta 90",
    "_cell_angle_gamma 90",
    "loop_",
    "_atom_site_label _atom_site_fract_x _atom_site_fract_y _atom_site_fract_z",
    "Zn1 0.0 0.0 0.0",
    "O1 0.25 0.25 0.25",
])

FAILS: list[str] = []


def log(msg: str = "") -> None:
    print(msg, flush=True)


def ok(cond: bool, msg: str) -> bool:
    if cond:
        log("  PASS  " + msg)
    else:
        log("  FAIL  " + msg)
        FAILS.append(msg)
    return bool(cond)


def find_edge() -> str:
    override = os.environ.get("CIFKIT_EDGE")
    if override:
        return override
    found = shutil.which("msedge")
    if found:
        return found
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LOCALAPPDATA")):
        if not base:
            continue
        for rel in ("Microsoft/Edge/Application/msedge.exe", "Microsoft/Edge Beta/Application/msedge.exe"):
            candidate = pathlib.Path(base) / rel
            if candidate.is_file():
                return str(candidate)
    return "msedge"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_http(url: str, timeout: float = 40.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                resp.read(64)
                return True
        except Exception:  # noqa: BLE001
            time.sleep(0.25)
    return False


class CDP:
    def __init__(self, ws_url: str) -> None:
        self.ws = websocket.create_connection(ws_url, timeout=30, suppress_origin=True)
        self.ws.settimeout(0.2)
        self.next_id = 0
        self.events: list[dict] = []

    def call(self, method: str, params: dict | None = None, timeout: float = 60.0) -> dict:
        self.next_id += 1
        mid = self.next_id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                raw = self.ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            data = json.loads(raw)
            if data.get("id") == mid:
                if "error" in data:
                    raise RuntimeError(f"{method}: {data['error']}")
                return data.get("result", {})
            self.events.append(data)
        raise TimeoutError(f"CDP {method} 超时")

    def pump(self, seconds: float) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                raw = self.ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            except Exception:  # noqa: BLE001
                return
            self.events.append(json.loads(raw))

    def eval(self, expression: str):
        result = self.call("Runtime.evaluate",
                           {"expression": expression, "returnByValue": True, "awaitPromise": True}, timeout=60)
        if result.get("exceptionDetails"):
            return {"__exception__": str(result["exceptionDetails"].get("text"))}
        return result.get("result", {}).get("value")

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass


def start_edge(port: int, profile: pathlib.Path) -> subprocess.Popen:
    cmd = [find_edge(), "--headless=new", f"--remote-debugging-port={port}", "--remote-allow-origins=*",
           f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
           "--disable-gpu", "--hide-scrollbars", "--window-size=1512,1000", "about:blank"]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not wait_http(f"http://127.0.0.1:{port}/json/version", 60):
        raise RuntimeError("Edge DevTools 端点未就绪")
    return proc


def new_target(port: int) -> str:
    req = urllib.request.Request(f"http://127.0.0.1:{port}/json/new?about:blank", method="PUT")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["webSocketDebuggerUrl"]


def external(url: str) -> bool:
    if url.startswith(("data:", "blob:", "about:", "devtools:", "file:", "chrome:", "ws:", "wss:")):
        return False
    if url.startswith(("http://", "https://")):
        host = url.split("://", 1)[1].split("/", 1)[0].split("@")[-1].split(":")[0]
        return host not in LOCAL_HOSTS
    return False


def clear_input_js(scenario_js: str) -> str:
    return "(function(){" + scenario_js + "return 'ok';})()"


def fill_cif_js() -> str:
    payload = json.dumps(CIF_SAMPLE)
    return clear_input_js(
        "var r=document.querySelector('input[name=\"cifInputMode\"][value=\"cif_text\"]');"
        "if(r){r.checked=true;r.dispatchEvent(new Event('click',{bubbles:true}));}"
        "var t=document.getElementById('cifInputCif');"
        "if(!t)return 'no-cif-box';"
        "t.value=" + payload + ";t.dispatchEvent(new Event('input',{bubbles:true}));"
    )


def run_page(cdp: CDP, url: str, label: str, steps: list[tuple[str, str, float]], settle: float = 1.0) -> dict:
    cdp.call("Page.enable")
    cdp.call("Runtime.enable")
    cdp.call("Network.enable")
    cdp.call("Log.enable")
    cdp.events.clear()
    cdp.call("Page.navigate", {"url": url})
    deadline = time.time() + 60
    loaded = False
    while time.time() < deadline and not loaded:
        cdp.pump(0.3)
        loaded = any(e.get("method") == "Page.loadEventFired" for e in cdp.events)
    cdp.pump(settle)

    interactions = []
    for name, js, wait in steps:
        out = cdp.eval(js)
        interactions.append({name: out})
        cdp.pump(wait)

    dom = cdp.eval(
        "JSON.stringify({"
        "title:document.title,"
        "textLen:(document.body.innerText||'').length,"
        "online:!!(window.CIF&&window.CIF.state&&window.CIF.state.online),"
        "scenario:(window.EduKit&&EduKit.state&&EduKit.state.scenarioKey)||null,"
        "scenarioTitle:(document.getElementById('scenarioTitle')||{}).textContent||null,"
        "formFields:document.querySelectorAll('#cifFormFields [data-field]').length,"
        "inputSource:!!(document.getElementById('cifInputSource')&&!document.getElementById('cifInputSource').hidden),"
        "card:!!document.querySelector('#cifResult .cif-card'),"
        "cardOffline:!!document.querySelector('#cifResult .cif-card.is-offline'),"
        "cardBadges:[].map.call(document.querySelectorAll('#cifResult .cif-badge'),function(n){return n.textContent;}),"
        "summaryRows:document.querySelectorAll('#cifResult .cif-kv').length,"
        "checks:document.querySelectorAll('#cifResult .cif-check-row').length,"
        "products:document.querySelectorAll('#cifResult .cif-product').length,"
        "downloads:document.querySelectorAll('#cifResult a[download]').length,"
        "preview:document.querySelectorAll('#cifResult .cif-preview img, #cifResult .cif-preview.is-offline').length,"
        "report:document.querySelectorAll('#cifResult .cif-report, #cifResult .cif-report-raw').length,"
        "next:document.querySelectorAll('#cifResult [data-cif-goto]').length,"
        "jobLog:!!document.querySelector('#cifJobLog .cif-log'),"
        "jobSteps:document.querySelectorAll('#cifJobLog .cif-steps li').length,"
        "cancelBtn:!!document.getElementById('cifCancelJob'),"
        "offlineBanner:!!(document.getElementById('cifOfflineBanner')&&!document.getElementById('cifOfflineBanner').hidden),"
        "modeText:(document.getElementById('modeText')||{}).textContent||null,"
        "stages:document.querySelectorAll('#stages .stage').length,"
        "scenarioButtons:document.querySelectorAll('#scenarioList [data-key]').length"
        "})"
    )
    shot = cdp.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})

    requests: dict[str, dict] = {}
    responses: list[dict] = []
    errors: list[str] = []
    js_errors: list[str] = []
    for event in cdp.events:
        method = event.get("method")
        params = event.get("params") or {}
        if method == "Network.requestWillBeSent":
            requests[params["requestId"]] = {"url": params["request"]["url"], "type": params.get("type")}
        elif method == "Network.responseReceived":
            responses.append({"url": params["response"]["url"], "status": params["response"]["status"],
                              "type": params.get("type")})
        elif method == "Runtime.exceptionThrown":
            details = params.get("exceptionDetails") or {}
            text = "Runtime.exceptionThrown: " + str((details.get("exception") or {}).get("description") or details.get("text"))[:400]
            errors.append(text)
            js_errors.append(text)
        elif method == "Runtime.consoleAPICalled" and params.get("type") in ("error", "assert"):
            args = " ".join(str(a.get("value") if "value" in a else a.get("description")) for a in params.get("args") or [])
            text = f"console.{params.get('type')}: {args[:400]}"
            errors.append(text)
            js_errors.append(text)
        elif method == "Log.entryAdded" and (params.get("entry") or {}).get("level") == "error":
            entry = params["entry"]
            errors.append(f"Log[error] {entry.get('text')} {entry.get('url', '')}"[:400])

    exts = [item for item in requests.values() if external(item["url"])]
    failed = [item for item in responses if item["status"] >= 400]
    return {"label": label, "url": url, "dom": json.loads(dom) if isinstance(dom, str) else dom,
            "png": shot.get("data"), "requests": list(requests.values()), "external": exts,
            "failed": failed, "errors": errors, "js_errors": js_errors, "interactions": interactions,
            "loaded": loaded}


def save_shot(info: dict, name: str) -> None:
    (SHOTS / name).write_bytes(base64.b64decode(info["png"]))


def assert_common(info: dict) -> None:
    dom = info["dom"] or {}
    ok(info["loaded"], f"[{info['label']}] 页面 load 完成")
    ok(dom.get("textLen", 0) > 400, f"[{info['label']}] 非白屏（正文 {dom.get('textLen')} 字符）")
    ok(len(info["external"]) == 0, f"[{info['label']}] 加载期外网请求 = 0（实际 {len(info['external'])}）")
    ok(len(info["js_errors"]) == 0, f"[{info['label']}] console.error / 未捕获异常 = 0"
       + (f"（{info['js_errors'][:2]}）" if info["js_errors"] else ""))
    ok(dom.get("scenarioButtons") == 6, f"[{info['label']}] 侧栏六场景按钮 = {dom.get('scenarioButtons')}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=("auto", "real"), default="auto")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()

    real_server = SERVER
    if not real_server.is_file():
        log(f"server.py 不存在（--backend {args.backend}）：本脚本只连真后端，不再退化到替身。")
        return 3
    backend_kind = "real"
    log("后端类型：real = server.py（--allow-exec）")

    static_port = free_port()
    backend_port = args.port or free_port()
    debug_port = free_port()
    procs: list[subprocess.Popen] = []

    static = subprocess.Popen([sys.executable, "-m", "http.server", str(static_port), "--bind", "127.0.0.1",
                               "--directory", str(WEB)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    procs.append(static)
    static_url = f"http://127.0.0.1:{static_port}/index.html"

    backend_cmd = [sys.executable, str(real_server), "--port", str(backend_port),
                   "--runs-dir", str(RUNS_DIR), "--allow-exec"]
    backend = subprocess.Popen(backend_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(WORKBENCH))
    procs.append(backend)
    backend_base = f"http://127.0.0.1:{backend_port}"

    if not wait_http(f"{backend_base}/api/bootstrap", 60):
        log(f"后端 {backend_base} 未就绪（{' '.join(backend_cmd)}）")
        for proc in procs:
            proc.terminate()
        return 3
    if not wait_http(static_url, 30):
        log("静态服务器未就绪")
        for proc in procs:
            proc.terminate()
        return 3

    profile = pathlib.Path(tempfile.mkdtemp(prefix="cifkit-edge-"))
    edge = start_edge(debug_port, profile)
    procs.append(edge)
    log(f"Edge headless pid={edge.pid} devtools=127.0.0.1:{debug_port}")
    log(f"离线页 {static_url}（无 /api/*）")
    log(f"在线页 {backend_base}/（{backend_kind} 后端）")

    quick = [("点击执行", "(function(){var b=document.getElementById('runBtn');if(!b)return 'no-runBtn';b.click();return 'clicked';})()", 3.0)]
    with_cif = [("填入 CIF", fill_cif_js(), 0.4)] + quick

    plan = [
        ("离线首页", static_url, [], 1.2, "01_offline_home.png", True),
        ("离线执行(kickoff)", static_url, quick, 4.0, "02_offline_kickoff.png", True),
        ("在线首页(kickoff)", backend_base + "/", [], 1.5, "03_online_home_kickoff.png", False),
        ("在线 kickoff route+job", backend_base + "/", [
            ("填一句话", clear_input_js(
                "var t=document.getElementById('cifField_text');"
                "if(!t)return 'no-textarea';"
                "t.value='从 core-mof 取一个 sod 拓扑的 Zn 结构，标准几何加工后导出 VASP';"
                "t.dispatchEvent(new Event('input',{bubbles:true}));"), 0.3),
            ("点路由预览", "(function(){document.getElementById('runBtn').click();return 'clicked';})()", 2.5),
            ("确认开工", "(function(){var b=document.getElementById('cifStartJob');if(!b)return 'no-start';b.click();return 'clicked';})()", 6.0),
        ], 1.0, "04_online_kickoff_job.png", False),
        ("在线 repair", backend_base + "/?demo=repair", with_cif + [("点执行", "(function(){document.getElementById('runBtn').click();return 'clicked';})()", 3.0)], 1.0, "05_online_repair.png", False),
        ("在线 geometry", backend_base + "/?demo=geometry", with_cif + [("点执行", "(function(){document.getElementById('runBtn').click();return 'clicked';})()", 3.0)], 1.0, "06_online_geometry.png", False),
        ("在线 export", backend_base + "/?demo=export", with_cif + [("点执行", "(function(){document.getElementById('runBtn').click();return 'clicked';})()", 3.0)], 1.0, "07_online_export.png", False),
        ("在线 inspect", backend_base + "/?demo=inspect", with_cif + [("点执行", "(function(){document.getElementById('runBtn').click();return 'clicked';})()", 3.0)], 1.0, "08_online_inspect.png", False),
        ("在线 library", backend_base + "/?demo=library", [("点执行", "(function(){document.getElementById('runBtn').click();return 'clicked';})()", 3.5)], 1.5, "09_online_library.png", False),
    ]

    try:
        for label, url, steps, settle, filename, offline_expected in plan:
            cdp = CDP(new_target(debug_port))
            try:
                info = run_page(cdp, url, label, steps, settle)
            finally:
                cdp.close()
            save_shot(info, filename)
            dom = info["dom"] or {}
            log("")
            log(f"--- {label} → _shots/{filename} ---")
            log(f"    DOM      : {json.dumps(dom, ensure_ascii=False)}")
            log(f"    请求数   : {len(info['requests'])}；外网 {len(info['external'])}；非 2xx {len(info['failed'])}")
            if info["js_errors"]:
                log(f"    JS 错误  : {info['js_errors'][:3]}")
            assert_common(info)
            if offline_expected:
                ok(dom.get("online") is False, f"[{label}] 离线态：window.CIF.state.online === false")
                ok(dom.get("offlineBanner") is True, f"[{label}] 离线横幅可见")
                ok("离线" in str(dom.get("modeText") or ""), f"[{label}] 徽章文案含「离线」（{dom.get('modeText')}）")
                if dom.get("card"):
                    ok(dom.get("cardOffline") is True, f"[{label}] 结果卡片标注 is-offline")
                    ok(dom.get("downloads") == 0, f"[{label}] 离线卡片不提供下载（0 个）")
                    ok(dom.get("next", 0) > 0, f"[{label}] 有「下一步」入口")
            else:
                ok(dom.get("online") is True, f"[{label}] 在线态：window.CIF.state.online === true")
                if "首页" not in label:
                    ok(dom.get("card") is True, f"[{label}] 结果卡片已渲染")
                    ok(dom.get("summaryRows", 0) > 0, f"[{label}] 结构数字行 {dom.get('summaryRows')} > 0")
                    ok(dom.get("products", 0) > 0, f"[{label}] 产物列表 {dom.get('products')} > 0")
                    ok(dom.get("next", 0) > 0, f"[{label}] 有「下一步」入口")
                    ok(dom.get("report", 0) > 0, f"[{label}] Markdown 报告区存在")
                if "kickoff" in label and "job" in label:
                    ok(dom.get("jobLog") is True, f"[{label}] 作业日志（SSE）已渲染")
                    ok(dom.get("jobSteps", 0) >= 3, f"[{label}] 步骤条 {dom.get('jobSteps')} >= 3")
                if label in ("在线 repair", "在线 geometry", "在线 export", "在线 inspect"):
                    ok(dom.get("checks", 0) > 0, f"[{label}] 校验清单 {dom.get('checks')} > 0")
                    ok(dom.get("downloads", 0) > 0, f"[{label}] 产物下载链接 {dom.get('downloads')} > 0")
                    ok(dom.get("preview", 0) > 0, f"[{label}] PNG 预览区存在")
    finally:
        for proc in procs:
            try:
                proc.terminate()
            except Exception:  # noqa: BLE001
                pass
        time.sleep(0.6)
        for proc in procs:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

    log("")
    if FAILS:
        log(f"FAILED {len(FAILS)} 项：")
        for item in FAILS:
            log("  - " + item)
        return 1
    log("ALL PASS（截图见 _shots/*.png）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
