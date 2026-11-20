"""测试用的小 HTTP 客户端与伪造 run 工具（stdlib only）。"""

from __future__ import annotations

import http.client
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


def request(base: str, method: str, path: str, body: Any = None, *, timeout: float = 180.0,
            headers: dict | None = None) -> tuple[int, Any, str]:
    parts = urlsplit(base)
    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=timeout)
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    hdrs = {"Content-Type": "application/json"} if data is not None else {}
    hdrs.update(headers or {})
    conn.request(method, path, body=data, headers=hdrs)
    response = conn.getresponse()
    raw = response.read().decode("utf-8", errors="replace")
    status = response.status
    response_headers = dict(response.getheaders())
    conn.close()
    try:
        payload = json.loads(raw)
    except ValueError:
        payload = None
    return status, payload, raw, response_headers  # type: ignore[return-value]


class Api:
    def __init__(self, base: str) -> None:
        self.base = base

    def get(self, path: str, **kwargs) -> tuple[int, Any]:
        status, payload, _raw, _headers = request(self.base, "GET", path, None, **kwargs)
        return status, payload

    def get_full(self, path: str, **kwargs):
        return request(self.base, "GET", path, None, **kwargs)

    def post(self, path: str, body: Any = None, **kwargs) -> tuple[int, Any]:
        status, payload, _raw, _headers = request(self.base, "POST", path, body, **kwargs)
        return status, payload

    def post_raw(self, path: str, body: Any = None, **kwargs):
        return request(self.base, "POST", path, body, **kwargs)

    def raw(self, method: str, path: str, body: Any = None, **kwargs):
        return request(self.base, method, path, body, **kwargs)


def sse_events(text: str) -> list[tuple[str, Any]]:
    """把 SSE body 解析成 [(event, data)]；注释帧（keepalive）忽略。"""
    events: list[tuple[str, Any]] = []
    current: str | None = None
    data_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("event: "):
            if current is not None:
                events.append((current, _parse(data_lines)))
            current = line[len("event: "):].strip()
            data_lines = []
        elif line.startswith("data: "):
            data_lines.append(line[len("data: "):])
        elif line == "" and current is not None:
            events.append((current, _parse(data_lines)))
            current = None
            data_lines = []
    if current is not None:
        events.append((current, _parse(data_lines)))
    return events


def _parse(data_lines: list[str]) -> Any:
    raw = "\n".join(data_lines)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def make_fake_run(runs_dir: Path, name: str = "demo_run", *, with_manifest: bool = True,
                  with_op: bool = False, with_preview: bool = False) -> Path:
    """造一个最小 run 目录（离线、快）；用于 GET/浏览类测试。"""
    run = Path(runs_dir) / name
    run.mkdir(parents=True, exist_ok=True)
    (run / "structure.cif").write_text("data_demo\n", encoding="utf-8")
    (run / "report.md").write_text("# demo report\n", encoding="utf-8")
    if with_manifest:
        manifest = {
            "schema": "cifkit-run/v1",
            "ok": True,
            "failed_step": None,
            "steps": [
                {"name": "resolve", "status": "ok", "detail": "demo", "data": {}},
                {"name": "export", "status": "ok", "detail": "formats=cif", "data": {}},
            ],
            "validation": {"ok": True, "checks": [], "metrics": {"natoms": 2, "formula": "H 2"}},
            "source": {"record": {"source": "local", "topology": "sod"}, "provenance": {}},
            "spec": {"topology": "sod"},
            "artifacts": {"report": str(run / "report.md")},
        }
        (run / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
        (run / "plan.json").write_text(json.dumps(
            [{"name": "resolve", "func": "resolve", "params": {}, "optional": False,
              "description": "检索"}], ensure_ascii=False), encoding="utf-8")
    if with_op:
        (run / "op.json").write_text(json.dumps(
            {"schema": "cifkit-workbench/op/v1", "op": "validate", "ok": True}, ensure_ascii=False),
            encoding="utf-8")
    if with_preview:
        (run / "preview.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return run
