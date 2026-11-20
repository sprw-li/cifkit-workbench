"""cifkit 工作台 · 本地 HTTP 服务（stdlib only）。

唯一真源契约：CONTRACT.md（冻结 v1.0 + v1.1 修订记录）。安全线：

* 只绑 127.0.0.1 / localhost（其它值启动即 exit 2）；
* 仅 GET / HEAD / POST，其余 405；
* 静态资源白名单前缀，路径穿越 400；
* GET /api/* 一律无副作用；
* 默认只读（未开 --allow-exec 时写动作 403 E_EXEC_DISABLED）。

本文件只做 HTTP 路由与静态资源；一切 cifkit 调用都在 bridge 里完成。
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import shutil
import sys
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import bridge as bridge_module  # noqa: E402
from bridge import (  # noqa: E402
    E_BAD_REQUEST,
    E_INTERNAL,
    E_VALIDATION,
    SCENARIOS,
    SCENARIOS_BY_ID,
    Bridge,
    WorkbenchError,
    json_dumps,
)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8780
ALLOWED_HOSTS = ("127.0.0.1", "localhost")
WEB_ROOT = _HERE / "web"
STATIC_PREFIXES = ("/assets/", "/vendor/", "/components/")
GET_EXACT = ("/", "/index.html", "/api/bootstrap", "/api/runs", "/api/run", "/api/artifact",
             "/api/sources", "/api/formats", "/api/jobs", "/api/job", "/api/job/events")
POST_EXACT = ("/api/route", "/api/op", "/api/job", "/api/job/cancel", "/api/run")
MAX_BODY_BYTES = 64 * 1024 * 1024

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".jsonl": "application/x-ndjson; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".vasp": "text/plain; charset=utf-8",
    ".poscar": "text/plain; charset=utf-8",
    ".cif": "chemical/x-cif; charset=utf-8",
    ".xyz": "text/plain; charset=utf-8",
    ".xsf": "text/plain; charset=utf-8",
    ".map": "application/json; charset=utf-8",
}


def content_type_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in _CONTENT_TYPES:
        return _CONTENT_TYPES[suffix]
    guessed = mimetypes.guess_type(str(path))[0]
    if guessed is None:
        return "application/octet-stream"
    if guessed.startswith("text/") and "charset" not in guessed:
        return f"{guessed}; charset=utf-8"
    return guessed


def _internal_error(exc: BaseException) -> WorkbenchError:
    """500 的 message 不回显 Python 异常文本；细节只写服务端 stderr。"""
    sys.stderr.write(f"[workbench] 内部错误：{type(exc).__name__}: {exc}\n")
    sys.stderr.flush()
    return WorkbenchError(E_INTERNAL, f"服务端内部错误（{type(exc).__name__}）",
                          "看服务端日志；这通常是 bug，请带复现步骤上报")


class _WorkbenchHTTPServer(ThreadingHTTPServer):
    """带 bridge 引用的线程服务器；连接被浏览器掐断时不打 traceback。"""

    daemon_threads = True
    allow_reuse_address = True
    bridge: Bridge
    web_root: Path
    verbose: bool = False

    def handle_error(self, request, client_address) -> None:  # noqa: ANN001 - stdlib 签名
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


class WorkbenchHandler(BaseHTTPRequestHandler):
    """路由：GET/HEAD 浏览 + 静态白名单 + /api/*；其余方法 405。"""

    server_version = "cifkit-workbench/1.0"
    protocol_version = "HTTP/1.1"

    # -- 基础设施 -----------------------------------------------------------
    @property
    def bridge(self) -> Bridge:
        return self.server.bridge  # type: ignore[attr-defined]

    @property
    def web_root(self) -> Path:
        return self.server.web_root  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - stdlib 名字
        if getattr(self.server, "verbose", False):
            sys.stderr.write(f"{self.address_string()} {fmt % args}\n")

    def _send_bytes(self, status: int, content_type: str, body: bytes,
                    extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_text(self, status: int, text: str, extra: dict | None = None) -> None:
        self._send_bytes(status, "text/plain; charset=utf-8", text.encode("utf-8"), extra)

    def _send_json(self, status: int, payload: dict) -> None:
        self._send_bytes(status, "application/json; charset=utf-8",
                         json_dumps(payload).encode("utf-8"))

    def _send_error(self, exc: WorkbenchError) -> None:
        self._send_json(exc.status, exc.to_dict())

    def _send_code(self, status: int, error: str, code: str, message: str,
                   hint: str | None = None, extra_headers: dict | None = None) -> None:
        payload = {"ok": False, "error": error, "code": code, "message": message}
        if hint:
            payload["hint"] = hint
        body = json_dumps(payload).encode("utf-8")
        self._send_bytes(status, "application/json; charset=utf-8", body, extra_headers)

    def _send_unknown(self, path: str) -> None:
        if path.startswith("/api/"):
            self._send_code(HTTPStatus.NOT_FOUND, "E_NOT_FOUND", "not-found",
                            f"未知路径: {path}", "可用端点见 CONTRACT §2")
        else:
            self._send_text(HTTPStatus.NOT_FOUND, f"404 not found: {path}\n")

    def _method_not_allowed(self, allow: str = "GET, POST") -> None:
        self._send_code(HTTPStatus.METHOD_NOT_ALLOWED, "E_METHOD_NOT_ALLOWED", "method-not-allowed",
                        f"405 method not allowed：{self.command}（只接受 {allow}）",
                        extra_headers={"Allow": allow})

    # -- 路径安全 -----------------------------------------------------------
    def _safe_path(self) -> str | None:
        raw_path = urlsplit(self.path).path
        try:
            decoded = unquote(raw_path, errors="strict")
        except UnicodeDecodeError:
            return None
        if (not decoded.startswith("/") or "\\" in decoded or "\x00" in decoded
                or any(part == ".." for part in decoded.split("/"))):
            return None
        return decoded

    def _serve_file(self, candidate: Path) -> None:
        try:
            resolved = candidate.resolve()
        except OSError:
            self._send_text(HTTPStatus.NOT_FOUND, "404 not found\n")
            return
        root = self.web_root.resolve()
        if resolved != root and root not in resolved.parents:
            self._send_code(HTTPStatus.BAD_REQUEST, "E_BAD_PATH", "bad-path",
                            "400 bad path：资源必须先出白名单目录")
            return
        if not resolved.is_file():
            self._send_text(HTTPStatus.NOT_FOUND, f"404 not found：{resolved.name}\n")
            return
        try:
            body = resolved.read_bytes()
        except OSError:
            self._send_text(HTTPStatus.NOT_FOUND, "404 not found\n")
            return
        self._send_bytes(HTTPStatus.OK, content_type_for(resolved), body)

    # -- 请求体 -------------------------------------------------------------
    def _read_json_body(self, allowed: set | None = None) -> dict:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise WorkbenchError(E_BAD_REQUEST, "缺少 Content-Length", "请求体必须是 JSON 对象")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise WorkbenchError(E_BAD_REQUEST, f"非法 Content-Length: {raw_length!r}") from exc
        if length < 0 or length > MAX_BODY_BYTES:
            raise WorkbenchError(E_BAD_REQUEST, f"请求体过大或非法: {length} bytes")
        raw = self.rfile.read(length) if length else b""
        if not raw.strip():
            raise WorkbenchError(E_BAD_REQUEST, "空请求体", "至少提供所需字段")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise WorkbenchError(E_BAD_REQUEST, f"JSON 解析失败: {exc}", "请求体必须是 UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            raise WorkbenchError(E_BAD_REQUEST, "请求体必须是 JSON 对象")
        if allowed is not None:
            unknown = sorted(key for key in payload if key not in allowed)
            if unknown:
                raise WorkbenchError(E_VALIDATION, f"请求体未知字段：{unknown}",
                                     f"允许字段: {sorted(allowed)}",
                                     detail={"violations": unknown})
        return payload

    # -- GET / HEAD ---------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - stdlib 名字
        self._handle_get()

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib 名字
        self._handle_get()

    def _handle_get(self) -> None:
        path = self._safe_path()
        if path is None:
            self._send_code(HTTPStatus.BAD_REQUEST, "E_BAD_PATH", "bad-path", "400 bad path")
            return
        query = {key: values[-1] for key, values in parse_qs(urlsplit(self.path).query).items()}
        try:
            if path in ("/", "/index.html"):
                index = self.web_root / "index.html"
                if not index.is_file():
                    self._send_text(HTTPStatus.NOT_FOUND,
                                    "404 not found：web/index.html 尚未就绪（前端页面未落地）\n")
                    return
                self._serve_file(index)
                return
            if path.startswith(STATIC_PREFIXES):
                self._serve_file(self.web_root / path.lstrip("/"))
                return
            if path == "/api/bootstrap":
                self._send_json(HTTPStatus.OK, self.bridge.bootstrap())
                return
            if path == "/api/runs":
                self._send_json(HTTPStatus.OK, {"ok": True, "runs": self.bridge.list_runs()})
                return
            if path == "/api/run":
                self._send_json(HTTPStatus.OK,
                                {"ok": True, "run": self.bridge.run_snapshot(query.get("id"))})
                return
            if path == "/api/artifact":
                self._handle_artifact(query)
                return
            if path == "/api/sources":
                warnings: list[str] = []
                self._send_json(HTTPStatus.OK,
                                {"ok": True, "sources": self.bridge.sources(warnings),
                                 "warnings": warnings})
                return
            if path == "/api/formats":
                warnings = []
                formats, bundles = self.bridge.formats(warnings)
                self._send_json(HTTPStatus.OK, {"ok": True, "formats": formats,
                                                "bundles": bundles, "warnings": warnings})
                return
            if path == "/api/jobs":
                self._send_json(HTTPStatus.OK, {"ok": True, "jobs": self.bridge.list_jobs()})
                return
            if path == "/api/job":
                self._send_json(HTTPStatus.OK,
                                {"ok": True, "job": self.bridge.get_job(query.get("id")).to_dict()})
                return
            if path == "/api/job/events":
                self._handle_job_events(query)
                return
            if path in POST_EXACT:
                self._method_not_allowed("POST")
                return
            self._send_unknown(path)
        except WorkbenchError as exc:
            self._send_error(exc)
        except Exception as exc:  # noqa: BLE001 - 不掀翻服务
            self._send_error(_internal_error(exc))

    def _handle_artifact(self, query: dict) -> None:
        run_id = query.get("run")
        name = query.get("name")
        if not run_id:
            raise WorkbenchError(E_BAD_REQUEST, "缺少 run", "如 /api/artifact?run=<rel>&name=report.md")
        if not name:
            raise WorkbenchError(E_BAD_REQUEST, "缺少 name", "如 /api/artifact?run=<rel>&name=report.md")
        path, entry = self.bridge.artifact_payload(run_id, name)
        extra = {}
        download = str(query.get("download") or "").lower()
        if download in ("1", "true", "yes", "on"):
            safe = entry["name"].split("/")[-1].replace('"', "")
            extra["Content-Disposition"] = f'attachment; filename="{safe}"'
        self._send_bytes(HTTPStatus.OK, content_type_for(path), path.read_bytes(), extra)

    def _handle_job_events(self, query: dict) -> None:
        job_id = query.get("id")
        if not job_id:
            raise WorkbenchError(E_BAD_REQUEST, "缺少 id", "如 /api/job/events?id=job-...")
        self.bridge.get_job(job_id)  # 404 在开流前抛出
        if self.command == "HEAD":
            self._send_bytes(HTTPStatus.OK, "text/event-stream; charset=utf-8", b"",
                             {"X-Accel-Buffering": "no"})
            return
        self._sse_open()
        try:
            for event, data in self.bridge.job_events(job_id):
                self._sse_emit(event, data)
        except WorkbenchError as exc:
            self._sse_emit("error", {"message": exc.message})
            self._sse_emit("done", {"job_id": job_id, "status": "error"})
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True
        except Exception as exc:  # noqa: BLE001
            self._sse_emit("error", {"message": f"{type(exc).__name__}: {exc}"})

    # -- POST ---------------------------------------------------------------
    def do_POST(self) -> None:  # noqa: N802 - stdlib 名字
        path = self._safe_path()
        if path is None:
            self._send_code(HTTPStatus.BAD_REQUEST, "E_BAD_PATH", "bad-path", "400 bad path")
            return
        try:
            if path == "/api/route":
                self._handle_route()
                return
            if path == "/api/op":
                self._handle_op()
                return
            if path == "/api/job":
                self._handle_job()
                return
            if path == "/api/job/cancel":
                self._handle_job_cancel()
                return
            if path == "/api/run":
                self._handle_run()
                return
            if path in GET_EXACT or path.startswith(STATIC_PREFIXES):
                self._method_not_allowed("GET")
                return
            self._send_unknown(path)
        except WorkbenchError as exc:
            self._send_error(exc)
        except Exception as exc:  # noqa: BLE001
            self._send_error(_internal_error(exc))

    def _handle_route(self) -> None:
        body = self._read_json_body({"text"})
        self._send_json(HTTPStatus.OK, self.bridge.route(body.get("text")))

    def _handle_op(self) -> None:
        body = self._read_json_body({"op", "params", "run", "input", "consent"})
        result = self.bridge.dispatch_op(body.get("op"), body.get("params"), body.get("run"),
                                         body.get("input"), consent=body.get("consent", False))
        self._send_json(HTTPStatus.OK, result)

    def _handle_job(self) -> None:
        body = self._read_json_body({"kind", "payload"})
        payload = body.get("payload")
        if payload is None:
            payload = {}
        job = self.bridge.start_job(body.get("kind"), payload)
        self._send_json(HTTPStatus.OK, {"ok": True, "job": job.to_dict()})

    def _handle_job_cancel(self) -> None:
        body = self._read_json_body({"id"})
        job = self.bridge.cancel_job(body.get("id"))
        self._send_json(HTTPStatus.OK, {"ok": True, "job": job.to_dict()})

    # -- SSE ---------------------------------------------------------------
    def _sse_open(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def _sse_emit(self, event: str, data: dict) -> None:
        if event == "keepalive":
            frame = b": keepalive\n\n"
        else:
            frame = f"event: {event}\ndata: {json_dumps(data)}\n\n".encode("utf-8")
        self.wfile.write(frame)
        self.wfile.flush()

    def _handle_run(self) -> None:
        """EduKit 兼容的流式通道；帧格式见 CONTRACT §4.1。"""
        body = self._read_json_body({"scenario", "text", "files", "payload"})
        scenario = body.get("scenario")
        if not isinstance(scenario, str) or scenario not in SCENARIOS_BY_ID:
            raise WorkbenchError(E_BAD_REQUEST, f"未知 scenario: {scenario!r}",
                                 f"可用: {', '.join(SCENARIOS_BY_ID)}")
        if body.get("text") is not None and not isinstance(body["text"], str):
            raise WorkbenchError(E_VALIDATION, "text 必须是字符串")
        if body.get("files") is not None and not isinstance(body["files"], list):
            raise WorkbenchError(E_VALIDATION, "files 必须是数组")
        payload = body.get("payload")
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            raise WorkbenchError(E_VALIDATION, "payload 必须是 JSON 对象")
        self._sse_open()
        try:
            for event, data in self.bridge.stream_scenario(
                    scenario, text=body.get("text"), files=body.get("files"), payload=payload):
                self._sse_emit(event, data)
        except WorkbenchError as exc:
            try:
                self._sse_emit("error", {"message": exc.message, **({"hint": exc.hint} if exc.hint else {})})
                self._sse_emit("done", {"run_id": "error", "elapsed_ms": 0, "mode": "live"})
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                self.close_connection = True
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True
        except Exception as exc:  # noqa: BLE001 - 页面不崩：error 帧 + done 帧
            try:
                self._sse_emit("error", {"message": f"{type(exc).__name__}: {exc}", "hint": "看服务端日志"})
                self._sse_emit("done", {"run_id": "error", "elapsed_ms": 0, "mode": "live"})
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                self.close_connection = True

    # -- 其它方法一律 405 ---------------------------------------------------
    def do_PUT(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_DELETE(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_PATCH(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_TRACE(self) -> None:  # noqa: N802
        self._method_not_allowed()


def create_server(runs_dir: str | Path | None = None, host: str = DEFAULT_HOST,
                  port: int = DEFAULT_PORT, web_root: str | Path | None = None,
                  bridge: Bridge | None = None, verbose: bool = False,
                  **bridge_options) -> _WorkbenchHTTPServer:
    """创建（不启动）工作台服务器。port=0 交给系统分配；读 server_address 拿端口。"""
    instance = bridge or Bridge(runs_dir, **bridge_options)
    httpd = _WorkbenchHTTPServer((host, int(port)), WorkbenchHandler)
    httpd.bridge = instance
    httpd.web_root = Path(web_root).resolve() if web_root else WEB_ROOT
    httpd.verbose = verbose
    httpd.daemon_threads = True
    return httpd


# --------------------------------------------------------------------------- #
# 启动 / 自检
# --------------------------------------------------------------------------- #
def _selftest_requests(base: str, httpd: _WorkbenchHTTPServer) -> list[tuple[str, bool, str]]:
    """在已启动的服务上跑一遍契约自检；返回 (名字, 是否通过, 说明)。"""
    import http.client as http_client
    import urllib.error
    import urllib.request

    results: list[tuple[str, bool, str]] = []

    def http(method: str, url: str, body: dict | None = None, timeout: float = 120.0):
        data = None if body is None else json_dumps(body).encode("utf-8")
        request = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", errors="replace")

    def parse(text: str) -> dict:
        try:
            payload = json.loads(text)
            return payload if isinstance(payload, dict) else {}
        except ValueError:
            return {}

    # runs 目录签名（不变量 1：只在纯浏览阶段前后比较）
    def runs_signature() -> list:
        runs_dir = Path(httpd.bridge.runs_dir)
        if not runs_dir.is_dir():
            return []
        return sorted((child.name, child.stat().st_mtime) for child in runs_dir.iterdir())

    # 1) bootstrap
    status, text = http("GET", f"{base}/api/bootstrap")
    payload = parse(text)
    scenarios = payload.get("scenarios") or []
    ids = [item.get("id") for item in scenarios if isinstance(item, dict)]
    formats = payload.get("formats") or {}
    caps = payload.get("caps") or {}
    ok = (status == 200 and payload.get("ok") is True and len(formats) > 0
          and ids == [item["id"] for item in SCENARIOS] and caps.get("exec") is True)
    results.append(("GET /api/bootstrap（scenarios/formats/caps）", ok,
                    f"status={status} formats={len(formats)} scenarios={len(ids)} caps.exec={caps.get('exec')}"))

    # 2) formats（必须来自服务端实际返回，不硬编码）
    status_f, text_f = http("GET", f"{base}/api/formats")
    formats_payload = parse(text_f)
    server_formats = formats_payload.get("formats") or {}
    ok = status_f == 200 and server_formats and set(server_formats) == set(formats)
    results.append(("GET /api/formats 与 bootstrap.formats 一致", ok,
                    f"status={status_f} n={len(server_formats)}"))

    # 3) runs
    status, text = http("GET", f"{base}/api/runs")
    ok = status == 200 and parse(text).get("ok") is True and isinstance(parse(text).get("runs"), list)
    results.append(("GET /api/runs", ok, f"status={status}"))

    # 4) sources（离线，不 probe）
    status, text = http("GET", f"{base}/api/sources")
    ok = status == 200 and isinstance(parse(text).get("sources"), list)
    results.append(("GET /api/sources（离线不 probe）", ok, f"status={status}"))

    # 5) 405
    status, _ = http("PUT", f"{base}/api/runs")
    results.append(("PUT /api/runs -> 405", status == 405, f"status={status}"))

    # 6) 未知路径 404
    status, _ = http("GET", f"{base}/api/nope")
    results.append(("GET /api/nope -> 404", status == 404, f"status={status}"))

    # 7) 静态路径穿越 400（裸 socket，未归一化 request-target）
    try:
        conn = http_client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
        conn.request("GET", "/../etc/passwd")
        traversal_status = conn.getresponse().status
        conn.close()
    except Exception:  # noqa: BLE001
        traversal_status = 0
    results.append(("GET /../etc/passwd -> 400", traversal_status == 400,
                    f"status={traversal_status}"))

    # 8) run 路径围栏 400
    status, text = http("GET", f"{base}/api/run?id=../../etc")
    results.append(("GET /api/run?id=../../etc -> 400 E_PATH_OUTSIDE",
                    status == 400 and parse(text).get("error") == "E_PATH_OUTSIDE", f"status={status}"))

    # 9) route（离线）
    status, text = http("POST", f"{base}/api/route", {"text": "下载 sod 拓扑的 MOF，补氢，2x2x1 超胞"})
    payload = parse(text)
    spec = payload.get("spec") or {}
    ok = status == 200 and payload.get("ok") is True and spec.get("topology") == "sod" and payload.get("plan")
    results.append(("POST /api/route（sod -> spec+plan）", ok,
                    f"status={status} topology={spec.get('topology')} steps={len(payload.get('plan') or [])}"))

    # 10) op=validate（进程内，落 op.json + validation.json）
    fixture = None
    data_dir = Path(httpd.bridge.cifkit_root) / "tests" / "data"
    if data_dir.is_dir():
        candidates = sorted(data_dir.glob("*.cif"))
        if candidates:
            fixture = candidates[0]
    if fixture is not None:
        cif_text = fixture.read_text(encoding="utf-8")
        status, text = http("POST", f"{base}/api/op",
                            {"op": "validate", "params": {}, "input": {"cif_text": cif_text}})
        payload = parse(text)
        run = payload.get("run") or {}
        run_dir = Path(run.get("abs") or "")
        ok = (status == 200 and payload.get("ok") is True
              and (run_dir / "op.json").is_file() and (run_dir / "validation.json").is_file())
        results.append(("POST /api/op validate（op.json+validation.json）", ok,
                        f"status={status} run={run.get('rel')}"))
    else:
        results.append(("POST /api/op validate（无 fixture，跳过）", True, "no tests/data/*.cif"))

    # 11) op 参数白名单 422
    status, text = http("POST", f"{base}/api/op",
                        {"op": "validate", "params": {"argv": ["rm", "-rf", "/"]},
                         "input": {"cif_text": "data_x"}})
    results.append(("POST /api/op 参数不在白名单 -> 422", status == 422, f"status={status}"))

    # 12) 只读模式 403
    readonly_bridge = Bridge(httpd.bridge.runs_dir, cifkit_root=httpd.bridge.cifkit_root,
                             python=httpd.bridge.python, allow_exec=False)
    try:
        readonly_bridge.dispatch_op("validate", {}, None, {"cif_text": "x"})
        readonly_ok = False
        readonly_detail = "no error raised"
    except WorkbenchError as exc:
        readonly_ok = exc.code == "E_EXEC_DISABLED" and exc.status == 403
        readonly_detail = f"{exc.code}/{exc.status}"
    results.append(("只读模式（未开 --allow-exec）op -> 403 E_EXEC_DISABLED", readonly_ok, readonly_detail))

    # 纯浏览阶段开始：此后到 after 快照之间不得有任何写动作
    before = runs_signature()

    # 13) SSE：kickoff
    status, text = http("POST", f"{base}/api/run", {"scenario": "kickoff", "text": "下载 sod 的 mof"})
    events = [line[len("event: "):].strip() for line in text.splitlines() if line.startswith("event: ")]
    indices = []
    for line in text.splitlines():
        if line.startswith("data: "):
            data = parse(line[len("data: "):])
            if "index" in data:
                indices.append(data["index"])
    n_stages = len(SCENARIOS_BY_ID["kickoff"]["stages"])
    ok = (status == 200 and events[:1] == ["meta"] and events[-1:] == ["done"]
          and "stage" in events and "delta" in events and bool(indices)
          and all(0 <= index < n_stages for index in indices))
    results.append(("POST /api/run kickoff（SSE 帧）", ok,
                    f"status={status} events={events} indices={sorted(set(indices))}"))

    # 14) 作业不存在 -> 404（开流前）
    status, text = http("GET", f"{base}/api/job/events?id=job-nope")
    results.append(("GET /api/job/events 未知 id -> 404", status == 404, f"status={status}"))

    # 15) 首页
    index = Path(httpd.web_root) / "index.html"
    status, text = http("GET", f"{base}/")
    if index.is_file():
        lowered = text[:4096].lower()
        results.append(("GET /（web/index.html）", status == 200
                        and ("<html" in lowered or "<!doctype" in lowered), f"status={status}"))
    else:
        results.append(("GET /（web/index.html 未落地，只验证 404 语义）", status == 404,
                        f"status={status}"))

    # 16) 不变量 1：GET 无副作用
    after = runs_signature()
    results.append(("不变量1 GET 无副作用（runs mtime 集合不变）", before == after,
                    f"before={len(before)} after={len(after)}"))
    return results


def selftest(host: str = DEFAULT_HOST, port: int = 0,
             runs_dir: str | Path | None = None, web_root: str | Path | None = None,
             verbose: bool = False, **bridge_options) -> int:
    """起服务 -> 自检 -> 退出（退出码 0 表示通过）。绝不阻塞在 serve_forever 上。

    自检用一个私有临时 runs-dir 且内部开启 exec（这是诊断，不是用户会话），
    保证离线可重复；用户自己的 --allow-exec / --runs-dir 不受影响。
    """
    # 自检的 runs-dir 放在 state/ 下（本机沙箱只允许工作区内写；TEMP 子目录不可写），
    # 结束即删；绝不碰用户的 --runs-dir
    selftest_root = _HERE / "state" / "_selftest" / f"{os.getpid()}-{int(time.time())}"
    selftest_root.mkdir(parents=True, exist_ok=True)
    options = dict(bridge_options)
    options.pop("allow_exec", None)
    httpd = create_server(runs_dir=runs_dir or (selftest_root / "runs"), host=host, port=port,
                          web_root=web_root, verbose=verbose, allow_exec=True, **options)
    bound_host, bound_port = httpd.server_address[:2]
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{bound_port}"
    print(f"[selftest] 服务已启动：{base}（runs-dir={httpd.bridge.runs_dir}）")
    failures = 0
    try:
        started = time.monotonic()
        checks = _selftest_requests(base, httpd)
        for name, ok, detail in checks:
            print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}")
            if not ok:
                failures += 1
        print(f"[selftest] 完成：{len(checks) - failures}/{len(checks)} 项通过，"
              f"用时 {time.monotonic() - started:.1f}s")
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        if runs_dir is None:
            shutil.rmtree(selftest_root, ignore_errors=True)
    return 0 if failures == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="server", description="cifkit 工作台服务（stdlib，仅 127.0.0.1）")
    parser.add_argument("--host", default=DEFAULT_HOST, help="只接受 127.0.0.1 / localhost")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="监听端口（0=自动分配）")
    parser.add_argument("--runs-dir", default=None, help="runs 目录（默认 state/runs）")
    parser.add_argument("--open", action="store_true", help="启动后打开浏览器")
    parser.add_argument("--selftest", action="store_true", help="起服务自检后退出（退出码 0 表示通过）")
    parser.add_argument("--verbose", action="store_true", help="打印访问日志")
    parser.add_argument("--cifkit-root", default=None, help="cifkit 仓库根（默认自动定位）")
    parser.add_argument("--python", default=None, help="与 cifkit 对齐的 python 解释器")
    parser.add_argument("--allow-exec", action="store_true",
                        help="允许 op/job 执行与写 runs-dir（默认只读，写动作 403 E_EXEC_DISABLED）")
    parser.add_argument("--allow-import-root", action="append", default=None,
                        help="允许作为 input.path 的额外根（可多次；默认仅 runs-dir）")
    parser.add_argument("--max-import-bytes", type=int, default=None,
                        help="导入/上传体积上限（字节，默认 20MB）")
    parser.add_argument("--op-timeout-s", type=float, default=None, help="单步 op 超时（默认 300s）")
    parser.add_argument("--pipeline-timeout-s", type=float, default=None, help="作业超时（默认 1800s）")
    args = parser.parse_args(argv)

    if args.host not in ALLOWED_HOSTS:
        print(f"错误：--host 只接受 {' / '.join(ALLOWED_HOSTS)}（工作台无鉴权，不得对外监听），"
              f"收到 {args.host!r}", file=sys.stderr)
        return 2

    bridge_options: dict = {
        "cifkit_root": args.cifkit_root,
        "python": args.python,
        "allow_exec": args.allow_exec,
        "allow_import_roots": args.allow_import_root,
    }
    if args.max_import_bytes is not None:
        bridge_options["max_import_bytes"] = args.max_import_bytes
    if args.op_timeout_s is not None:
        bridge_options["op_timeout_s"] = args.op_timeout_s
    if args.pipeline_timeout_s is not None:
        bridge_options["pipeline_timeout_s"] = args.pipeline_timeout_s

    if args.selftest:
        return selftest(host=args.host, port=args.port, runs_dir=args.runs_dir,
                        verbose=args.verbose, **bridge_options)

    try:
        httpd = create_server(runs_dir=args.runs_dir, host=args.host, port=args.port,
                              verbose=args.verbose, **bridge_options)
    except WorkbenchError as exc:
        print(f"错误：{exc.message}" + (f"（{exc.hint}）" if exc.hint else ""), file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"错误：无法监听 {args.host}:{args.port}（{exc}）", file=sys.stderr)
        return 1

    bound_host, bound_port = httpd.server_address[:2]
    url = f"http://{bound_host}:{bound_port}/"
    print("cifkit 工作台已启动：")
    print(f"  URL      : {url}")
    print(f"  runs-dir : {httpd.bridge.runs_dir}"
          + ("" if httpd.bridge.runs_writable else "（不可写 -> 写动作 403 E_READONLY_RUNS）"))
    print(f"  cifkit   : {httpd.bridge.cifkit_root}")
    print(f"  python   : {httpd.bridge.python}")
    print(f"  执行     : " + ("已开启（--allow-exec）" if httpd.bridge.allow_exec
                              else "只读（未开 --allow-exec，GET 浏览可用）"))
    if httpd.bridge.allow_import_roots:
        print(f"  导入根   : {[str(p) for p in httpd.bridge.allow_import_roots]}")
    print(f"  规则     : 只绑 {bound_host}、仅 GET/HEAD/POST、无鉴权；默认零外网")
    print("  Ctrl+C 停止")
    if args.open:
        webbrowser.open(url)
    try:
        httpd.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
