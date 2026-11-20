r"""探针自检：用本地 stub 服务器验证 common.py 的 HTTP / SSE / runs_signature 管线。

不是对工作台的验收，而是对验收工具本身的自检（避免第二阶段把工具 bug 当成实现 bug）。

运行：<cifkit 检出>\.venv\Scripts\python.exe _verify\selftest_plumbing.py
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # noqa: ANN002
        pass

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/sse"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b"event: meta\ndata: {\"a\":1}\n\n")
            self.wfile.write(b": keepalive\n\n")
            self.wfile.write(b"event: done\ndata: {\"b\":2}\n\n")
            self.wfile.flush()
            return
        if self.path.startswith("/json"):
            body = json.dumps({"ok": True, "中文": "一句话"}, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/err"):
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":false,"error":"E_NOT_FOUND"}')
            return
        self.send_response(204)
        self.end_headers()

    def do_HEAD(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


def main() -> int:
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}")
        if not ok:
            failures.append(name)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        status, headers, body = common.get(base, "/json")
        check("GET JSON 200 + no-store + UTF-8 不转义", status == 200 and headers.get("Cache-Control") == "no-store"
              and "一句话" in body and common.js(body).get("ok") is True, f"status={status} body={body!r}")
        status, headers, body = common.get(base, "/err")
        check("HTTPError 不抛且可解析错误体", status == 404 and common.js(body).get("error") == "E_NOT_FOUND",
              f"status={status} body={body!r}")
        status, headers, body = common.http(base, "HEAD", "/json")
        check("HEAD 200 空 body", status == 200 and body == "", f"status={status} len={len(body)}")
        stream = common.sse(base, "/sse", timeout=5, read_seconds=3)
        events = [frame["event"] for frame in stream["frames"]]
        check("SSE meta/keepalive/done 解析", stream["status"] == 200 and events == ["meta", "done"]
              and len(stream["keepalives"]) == 1 and not stream["timed_out"], f"events={events} keepalive={len(stream['keepalives'])}")
        signature = common.runs_signature(Path(__file__).resolve().parent)
        check("runs_signature 可枚举", isinstance(signature, dict) and len(signature) > 0, f"entries={len(signature)}")
        eps = common.parse_endpoints()
        check("CONTRACT 端点解析非空且路径干净", len(eps) >= 10 and all(p.startswith("/") and "\\" not in p for row in eps for p in row["paths"]),
              f"rows={len(eps)}")
        ops = common.parse_ops()
        check("CONTRACT op 白名单 10 项", len(ops) == 10 and "sources_probe" in ops, f"ops={ops}")
    finally:
        server.shutdown()
    print(f"== selftest_plumbing: {'ALL PASS' if not failures else 'FAILURES: ' + ', '.join(failures)} ==")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
