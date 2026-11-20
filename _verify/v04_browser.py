r"""验收 v04：Edge headless 真实渲染 + 控制台 + 网络（在线态 / 停后端离线态）。

不做 DOM id 假设（前端 id 由实现者决定）：断言用页面文本量、像素统计、网络与 console。
截图存 _verify/shots/。

离线态 = 后端不在（用 python -m http.server 只发 web/ 静态文件），验证不白屏 + 零外网。

运行：<cifkit 检出>\.venv\Scripts\python.exe _verify\v04_browser.py
依赖：websocket-client、Pillow（本仓 .venv 已装）；Edge 由 CIFWB_EDGE 或常见路径探测。
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    PYTHON, REPO, SHOTS, WEB, WORKBENCH, LOGS, Report, Server, free_port, fresh, get, js,
    log, post, prereqs_missing, log_to,
)

LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]")


def find_edge() -> str:
    override = os.environ.get("CIFWB_EDGE")
    if override:
        return override
    found = shutil.which("msedge")
    if found:
        return found
    for candidate in (
        Path(os.environ.get("ProgramFiles", "")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft/Edge/Application/msedge.exe",
    ):
        if candidate.is_file():
            return str(candidate)
    return "msedge"


EDGE = find_edge()


class CDP:
    def __init__(self, ws_url: str) -> None:
        import websocket

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
            except Exception as exc:  # noqa: BLE001
                if type(exc).__name__ == "WebSocketTimeoutException":
                    continue
                raise RuntimeError(f"CDP recv 失败: {exc}") from exc
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
            except Exception as exc:  # noqa: BLE001
                if type(exc).__name__ == "WebSocketTimeoutException":
                    continue
                return
            self.events.append(json.loads(raw))

    def evaluate(self, expression: str):
        result = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True}, timeout=60)
        if result.get("exceptionDetails"):
            return {"__exception__": result["exceptionDetails"].get("text")}
        return result.get("result", {}).get("value")

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass


def start_edge(port: int, profile: Path) -> subprocess.Popen:
    cmd = [
        EDGE, "--headless=new",
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        f"--user-data-dir={profile}",
        "--no-first-run", "--no-default-browser-check",
        "--disable-gpu", "--hide-scrollbars",
        "--window-size=1512,1000",
        "about:blank",
    ]
    edge_log = LOGS / f"edge-{port}.log"
    edge_log.parent.mkdir(parents=True, exist_ok=True)
    handle = open(edge_log, "w", encoding="utf-8")
    proc = subprocess.Popen(cmd, stdout=handle, stderr=subprocess.STDOUT)
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as resp:
                json.loads(resp.read())
                return proc
        except Exception:  # noqa: BLE001
            if proc.poll() is not None:
                raise RuntimeError(f"Edge 进程已退出 exit={proc.returncode}（见 {edge_log.name}）")
            time.sleep(0.3)
    raise RuntimeError(f"Edge DevTools 端点未就绪（见 {edge_log.name}）")


def new_target(port: int) -> str:
    req = urllib.request.Request(f"http://127.0.0.1:{port}/json/new?about:blank", method="PUT")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["webSocketDebuggerUrl"]


def external(url: str) -> bool:
    if url.startswith(("data:", "blob:", "about:", "devtools:", "file:", "chrome:")):
        return False
    if url.startswith(("http://", "https://")):
        rest = url.split("://", 1)[1]
        host = rest.split("/", 1)[0].split("@")[-1].split(":")[0]
        return host not in LOCAL_HOSTS
    return False


def run_page(cdp: CDP, url: str, label: str, settle: float) -> dict:
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
        loaded = any(event.get("method") == "Page.loadEventFired" for event in cdp.events)
    cdp.pump(settle)
    dom = cdp.evaluate(
        "JSON.stringify({title:document.title,textLen:(document.body&&document.body.innerText||'').length,"
        "nodes:document.querySelectorAll('*').length,"
        "text:((document.body&&document.body.innerText)||'').replace(/\\s+/g,' ').slice(0,240),"
        "scripts:document.scripts.length,links:document.querySelectorAll('link[rel=stylesheet]').length,"
        "buttons:document.querySelectorAll('button').length,scenarioNodes:document.querySelectorAll('[data-scenario],[data-tab],[data-view]').length,"
        "offlineHint:!!document.querySelector('[data-offline],.offline,#offline'),"
        "hasApp:!!(document.body&&document.body.childElementCount>0)})"
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
            responses.append({"url": params["response"]["url"], "status": params["response"]["status"], "type": params.get("type")})
        elif method == "Network.loadingFailed":
            errors.append(f"Network.loadingFailed {params.get('errorText')} type={params.get('type')}")
        elif method == "Runtime.exceptionThrown":
            details = params.get("exceptionDetails") or {}
            text = "Runtime.exceptionThrown: " + str((details.get("exception") or {}).get("description") or details.get("text"))[:400]
            errors.append(text)
            js_errors.append(text)
        elif method == "Runtime.consoleAPICalled" and params.get("type") in ("error", "assert", "warning"):
            args = " ".join(str((a.get("value") if "value" in a else a.get("description"))) for a in params.get("args") or [])
            text = f"console.{params.get('type')}: {args[:400]}"
            errors.append(text)
            if params.get("type") in ("error", "assert"):
                js_errors.append(text)
        elif method == "Log.entryAdded" and (params.get("entry") or {}).get("level") in ("error", "warning"):
            entry = params["entry"]
            errors.append(f"Log[{entry.get('level')}] {entry.get('text')} {entry.get('url', '')}"[:400])
    return {
        "label": label, "url": url,
        "dom": js(dom) if isinstance(dom, str) else dom,
        "png": shot.get("data"),
        "requests": list(requests.values()),
        "external": [item for item in requests.values() if external(item["url"])],
        "failed": [item for item in responses if item["status"] >= 400],
        "errors": errors, "js_errors": js_errors, "loaded": loaded,
    }


def analyze_png(path: Path) -> dict:
    import warnings

    from PIL import Image

    warnings.filterwarnings("ignore", category=DeprecationWarning)
    image = Image.open(path).convert("RGB")
    pixels = list(image.getdata())
    total = len(pixels)
    white = sum(1 for r, g, b in pixels if r > 245 and g > 245 and b > 245)
    mean = tuple(round(sum(channel) / total, 1) for channel in zip(*pixels))
    return {
        "size": image.size, "mean_rgb": mean,
        "white_fraction": round(white / total, 4),
        "non_white_fraction": round(1 - white / total, 4),
        "distinct_colors": len(set(pixels)),
    }


def cdn_scan() -> list[str]:
    """页面/资源里是否引了外网（CDN）——唯一出网应是用户点 cifkit 数据源动作。"""
    hits: list[str] = []
    patterns = [
        re.compile(r"""(?:src|href)\s*=\s*["'](https?:)?//""", re.IGNORECASE),
        re.compile(r"""@import\s+(?:url\()?["']?https?://""", re.IGNORECASE),
        re.compile(r"""url\(\s*["']?https?://""", re.IGNORECASE),
    ]
    for path in sorted(WEB.rglob("*")):
        if not path.is_file() or "vendor" in path.parts:
            continue
        if path.suffix.lower() not in (".html", ".css", ".js", ".mjs"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in patterns:
            for match in pattern.finditer(text):
                line = text[: match.start()].count("\n") + 1
                hits.append(f"{path.relative_to(WORKBENCH)}:{line}: {match.group(0)[:60]}")
    return hits


def main() -> int:
    report = Report("v04 浏览器（Edge headless，在线 + 离线）")
    missing = prereqs_missing()
    if missing:
        report.blocked("前置存在（server.py + web/index.html）", f"缺 {missing}")
        return report.finish("v04_browser.log")
    SHOTS.mkdir(parents=True, exist_ok=True)

    hits = cdn_scan()
    report.check(
        f"v04[静态] 页面/资源零 CDN 外链（扫描 web/ 非 vendor 的 {len(hits)} 处命中应为 0）",
        not hits,
        f"命中={hits[:8] or '无'}",
    )

    def clean_ref(ref: str) -> str:
        clean = ref.split("?")[0].split("#")[0]
        while clean.startswith("./"):
            clean = clean[2:]
        return clean.lstrip("/")

    # ---- 非浏览器 HTTP 级证据（不依赖 Edge，workspace-write 沙箱下照常可跑） ----
    with Server(fresh("browser-http")) as http_server:
        st_home, hd_home, body_home = get(http_server.base, "/")
        report.check(
            "v04[HTTP] GET / → 200 text/html + no-store",
            st_home == 200 and "text/html" in hd_home.get("Content-Type", "")
            and "no-store" in hd_home.get("Cache-Control", ""),
            f"status={st_home} ct={hd_home.get('Content-Type')!r} bytes={len(body_home)}",
        )
        st_boot, _, boot_text = get(http_server.base, "/api/bootstrap")
        boot = js(boot_text)
        report.check(
            "v04[HTTP] GET /api/bootstrap → 200 且 scenarios/caps 有值",
            st_boot == 200 and isinstance(boot, dict) and bool(boot.get("scenarios"))
            and isinstance(boot.get("caps"), dict),
            f"status={st_boot} scenarios={len((boot or {}).get('scenarios') or [])} caps={(boot or {}).get('caps')}",
        )
        refs = re.findall(r"""(?:src|href)\s*=\s*["']([^"']+)["']""", body_home)
        local = sorted({clean_ref(ref) for ref in refs
                        if not ref.startswith(("http://", "https://", "//", "data:", "#", "mailto:"))})
        bad = []
        for ref in local:
            if not ref:
                continue
            status, _h, _b = get(http_server.base, "/" + ref)
            if status != 200:
                bad.append((ref, status))
        report.check(
            f"v04[HTTP] index.html 引用的 {len(local)} 个本地静态资源全部 200",
            bool(local) and not bad,
            f"失败={bad or '无'} 样例={local[:6]}",
        )

    if not Path(EDGE).is_file() and shutil.which(EDGE) is None:
        report.blocked("v04 浏览器像素/DOM/console（Edge headless）",
                       f"BLOCKED-ENV：未找到 msedge（CIFWB_EDGE 可指定）：{EDGE}")
        return report.finish("v04_browser.log")
    try:
        import websocket  # noqa: F401
        from PIL import Image  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        report.blocked("v04 浏览器像素/DOM/console（Edge headless）",
                       f"BLOCKED-ENV：缺 websocket-client/Pillow：{type(exc).__name__}: {exc}")
        return report.finish("v04_browser.log")

    debug_port = free_port()
    static_port = free_port()
    profile = Path(tempfile.mkdtemp(prefix="cifwb-edge-"))
    static_proc = subprocess.Popen(
        [PYTHON, "-m", "http.server", str(static_port), "--bind", "127.0.0.1", "--directory", str(WEB)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(REPO),
    )
    edge = None
    try:
        with Server(fresh("browser")) as backend:
            st, _, text = post(
                backend.base, "/api/op",
                {"op": "validate", "params": {},
                 "input": {"cif_text": (REPO / "tests" / "data" / "zif8_cr_sod.cif").read_text(encoding="utf-8", errors="replace")}},
                timeout=300,
            )
            log(f"预热 run：status={st} ok={isinstance(js(text), dict) and (js(text) or {}).get('ok')}")
            online_url = f"{backend.base}/"
            offline_url = f"http://127.0.0.1:{static_port}/index.html"
            try:
                edge = start_edge(debug_port, profile)
            except Exception as exc:  # noqa: BLE001
                tail = ""
                try:
                    tail = (LOGS / f"edge-{debug_port}.log").read_text(encoding="utf-8", errors="replace")[-1200:]
                except OSError:
                    pass
                report.blocked(
                    "v04 浏览器像素/DOM/console/网络（Edge headless）",
                    f"BLOCKED-ENV：{type(exc).__name__}: {exc}；Edge 原始日志尾部={tail!r}；"
                    f"复现（非沙箱终端）：\"{PYTHON}\" _verify\\v04_browser.py "
                    "或 web\\_shots\\browser_shots.py --backend real",
                )
            if edge is not None:
                log(f"Edge pid={edge.pid} devtools=127.0.0.1:{debug_port}")
                log(f"在线态 {online_url}（真实后端）")
                log(f"离线态 {offline_url}（仅静态文件，后端不存在）")
                for label, url, settle, filename in (
                    ("在线态", online_url, 2.0, "online.png"),
                    ("离线态", offline_url, 2.0, "offline.png"),
                ):
                    cdp = CDP(new_target(debug_port))
                    try:
                        info = run_page(cdp, url, label, settle)
                    finally:
                        cdp.close()
                    shot_path = SHOTS / filename
                    shot_path.write_bytes(base64.b64decode(info["png"]))
                    stats = analyze_png(shot_path)
                    info["stats"] = stats
                    info["shot"] = str(shot_path)
                    dom = info["dom"] if isinstance(info["dom"], dict) else {}
                    log(f"\n--- {label} ---")
                    log(f"    URL       : {info['url']}")
                    log(f"    DOM       : {json.dumps(dom, ensure_ascii=False)}")
                    log(f"    截图      : {shot_path.name} {stats}")
                    log(f"    请求数    : {len(info['requests'])}")
                    log(f"    外网请求  : {[item['url'] for item in info['external']] or '无'}")
                    log(f"    >=400     : {[(item['url'].split('/')[-1], item['status']) for item in info['failed']][:8] or '无'}")
                    log(f"    JS 错误   : {info['js_errors'][:4] or '无'}")
                    log(f"    浏览器日志: {info['errors'][:4] or '无'}")

                    report.check(f"v04.{label} 页面已加载（load 事件）", info["loaded"], f"url={info['url']}")
                    report.check(
                        f"v04.{label} 非白屏（DOM 文本 + 像素）",
                        bool(dom.get("textLen", 0) > 200) and stats["non_white_fraction"] > 0.03
                        and stats["distinct_colors"] > 50,
                        f"textLen={dom.get('textLen')} nodes={dom.get('nodes')} 非白像素={stats['non_white_fraction']:.1%} "
                        f"颜色数={stats['distinct_colors']} 文本样例={str(dom.get('text'))[:120]!r}",
                    )
                    report.check(
                        f"v04.{label} 零外网请求（不变量 9）",
                        not info["external"],
                        f"外网={[item['url'] for item in info['external']] or '无'}；同源 {len(info['requests'])} 条",
                    )
                    if label == "在线态":
                        report.check("v04.在线态 无 JS console.error / 未捕获异常", not info["js_errors"],
                                     f"JS 错误={info['js_errors'][:3] or '无'}")
                        report.check("v04.在线态 无 >=400 资源", not info["failed"],
                                     f"{[(item['url'].split('/')[-1], item['status']) for item in info['failed']][:6] or '无'}")
                    else:
                        api_only = all("/api/" in line for line in info["errors"]) if info["errors"] else True
                        report.check(
                            "v04.离线态 后端缺失时优雅降级（无未捕获 JS 异常；浏览器错误仅 /api/* 资源失败）",
                            not info["js_errors"] and api_only,
                            f"js_errors={info['js_errors'][:3] or '无'} 浏览器错误={info['errors'][:4] or '无'}",
                        )
                        report.check(
                            "v04.离线态 静态文件本身无 >=400（降级只发生在 /api/*）",
                            all("/api/" in item["url"] for item in info["failed"]) if info["failed"] else True,
                            f"{[(item['url'].split('/')[-1], item['status']) for item in info['failed']][:8] or '无'}",
                        )
    finally:
        if edge is not None:
            edge.terminate()
            try:
                edge.wait(timeout=15)
            except subprocess.TimeoutExpired:
                edge.kill()
        static_proc.terminate()
        try:
            static_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            static_proc.kill()

    if edge is not None:
        for name in ("online.png", "offline.png"):
            path = SHOTS / name
            report.check(f"截图落盘 shots/{name}", path.is_file() and path.stat().st_size > 5000,
                         f"bytes={path.stat().st_size if path.is_file() else 0}")
    return report.finish("v04_browser.log")


if __name__ == "__main__":
    raise SystemExit(main())
