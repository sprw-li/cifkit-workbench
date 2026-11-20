"""server.py 的 HTTP 层测试：路由、方法、静态白名单、请求体校验、SSE、只读。"""

from __future__ import annotations

import http.client
import json
import sys
import time
from pathlib import Path

import pytest

from helpers import Api, make_fake_run, request, sse_events


# --------------------------------------------------------------------------- #
# 基础路由 / 形状
# --------------------------------------------------------------------------- #
def test_bootstrap_shape(live_server, api: Api):
    status, payload = api.get("/api/bootstrap")
    assert status == 200 and payload["ok"] is True
    cifkit = payload["cifkit"]
    for key in ("version", "api_version", "root", "python", "runs_dir", "cache", "ops"):
        assert key in cifkit, key
    assert cifkit["ops"], "cifkit.ops 不能为空"
    assert set(payload["limits"]) >= {"max_import_bytes", "pipeline_timeout_s", "op_timeout_s",
                                      "job_keep", "max_upload_bytes"}
    assert set(payload["caps"]) == {"exec", "runs_writable", "network"}
    assert payload["caps"]["exec"] is True
    assert payload["scenarios"] and len(payload["scenarios"]) == 6


def test_index_html_served(live_server, api: Api):
    status, _payload, raw, headers = api.raw("GET", "/")
    assert status == 200
    assert "<!doctype" in raw.lower()
    assert headers.get("Cache-Control") == "no-store"


def test_method_not_allowed(live_server, api: Api):
    for method, path in (("PUT", "/api/runs"), ("DELETE", "/api/runs"), ("PATCH", "/api/route"),
                         ("OPTIONS", "/"), ("POST", "/api/runs"), ("GET", "/api/route")):
        status, payload, _raw, headers = request(live_server.base, method, path)
        assert status == 405, f"{method} {path} -> {status}"
        assert payload and payload.get("code") == "method-not-allowed"
        assert headers.get("Allow")


def test_unknown_paths(live_server, api: Api):
    status, payload = api.get("/api/nope")
    assert status == 404 and payload["code"] == "not-found"
    status, _payload, raw, _headers = api.raw("GET", "/nope")
    assert status == 404 and "404" in raw


def test_head_has_no_body(live_server, api: Api):
    status, _payload, raw, headers = api.raw("HEAD", "/api/bootstrap")
    assert status == 200
    assert raw == ""
    assert int(headers["Content-Length"]) > 0


# --------------------------------------------------------------------------- #
# 静态资源
# --------------------------------------------------------------------------- #
def test_static_whitelist_serves_files(live_server, api: Api, web_root: Path):
    status, _payload, raw, headers = api.raw("GET", "/assets/app.css")
    assert status == 200 and "color" in raw
    assert headers["Content-Type"].startswith("text/css")
    status, _payload, _raw, _headers = api.raw("GET", "/assets/missing.css")
    assert status == 404
    # 白名单之外的目录不能读（即使文件真实存在）
    status, _payload, _raw, _headers = api.raw("GET", "/secret.txt")
    assert status == 404


def test_static_path_traversal_is_400(live_server):
    for target in ("/assets/../secret.txt", "/../etc/passwd", "/assets/%2e%2e/secret.txt"):
        conn = http.client.HTTPConnection("127.0.0.1", live_server.httpd.server_address[1], timeout=10)
        conn.request("GET", target)
        response = conn.getresponse()
        status = response.status
        payload = json.loads(response.read().decode("utf-8") or "{}")
        conn.close()
        assert status == 400, f"{target} -> {status}"
        assert payload.get("code") == "bad-path"


# --------------------------------------------------------------------------- #
# 请求体校验
# --------------------------------------------------------------------------- #
def test_unknown_body_fields_are_422(live_server, api: Api):
    status, payload = api.post("/api/route", {"text": "sod mof", "extra": 1})
    assert status == 422 and payload["error"] == "E_VALIDATION"
    status, payload = api.post("/api/op", {"op": "validate", "input": {"cif_text": "x"},
                                           "argv": ["rm"]})
    assert status == 422
    status, payload = api.post("/api/job", {"kind": "pipeline", "payload": {"query": "x"},
                                            "text": "legacy"})
    assert status == 422
    status, payload = api.post("/api/run", {"scenario": "kickoff", "nope": 1})
    assert status == 422


def test_bad_json_body_is_400(live_server):
    conn = http.client.HTTPConnection("127.0.0.1", live_server.httpd.server_address[1], timeout=10)
    conn.request("POST", "/api/op", body=b"{not json", headers={"Content-Type": "application/json"})
    response = conn.getresponse()
    status = response.status
    payload = json.loads(response.read().decode("utf-8") or "{}")
    conn.close()
    assert status == 400 and payload["error"] == "E_BAD_REQUEST"


def test_unknown_scenario_is_400(live_server, api: Api):
    status, payload = api.post("/api/run", {"scenario": "nope"})
    assert status == 400 and payload["error"] == "E_BAD_REQUEST"


# --------------------------------------------------------------------------- #
# artifact
# --------------------------------------------------------------------------- #
def test_artifact_download_header(live_server, api: Api, runs_dir: Path):
    make_fake_run(runs_dir, "demo_run")
    status, _payload, raw, headers = api.raw("GET",
                                             "/api/artifact?run=demo_run&name=report.md&download=1")
    assert status == 200
    assert raw.startswith("# demo")
    assert headers["Content-Disposition"].startswith("attachment;")
    status, _payload = api.get("/api/artifact?run=demo_run")
    assert status == 400
    status, _payload = api.get("/api/artifact?run=nope&name=report.md")
    assert status == 404


# --------------------------------------------------------------------------- #
# op / 只读
# --------------------------------------------------------------------------- #
def test_op_validate_over_http(live_server, api: Api, fixture_cif_text: str):
    status, payload = api.post("/api/op", {"op": "validate", "params": {},
                                           "input": {"cif_text": fixture_cif_text}})
    assert status == 200 and payload["ok"] is True
    run = payload["run"]
    assert run["kind"] == "op"
    assert (Path(run["abs"]) / "op.json").is_file()


def test_op_readonly_mode_is_403(live_server, api: Api, monkeypatch):
    monkeypatch.setattr(live_server.bridge, "allow_exec", False)
    status, payload = api.post("/api/op", {"op": "validate", "input": {"cif_text": "x"}})
    assert status == 403 and payload["error"] == "E_EXEC_DISABLED"


def test_failed_op_over_http_returns_run(live_server, api: Api):
    status, payload = api.post("/api/op", {"op": "repair",
                                           "input": {"cif_text": "not a cif"}})
    assert status in (422, 500)
    assert payload["ok"] is False
    assert payload["run"], "失败响应必须带 run（§9.6）"
    record = json.loads((Path(payload["run"]["abs"]) / "op.json").read_text(encoding="utf-8"))
    assert record["ok"] is False and record["stderr"]


def test_sources_probe_requires_consent(live_server, api: Api):
    status, payload = api.post("/api/op", {"op": "sources_probe", "params": {}})
    assert status == 400 and payload["error"] == "E_CONSENT_REQUIRED"


# --------------------------------------------------------------------------- #
# SSE：/api/run
# --------------------------------------------------------------------------- #
def test_run_sse_kickoff_frames(live_server):
    status, _payload, raw, headers = request(live_server.base, "POST", "/api/run",
                                             {"scenario": "kickoff", "text": "下载 sod 拓扑的 mof"})
    assert status == 200
    assert headers["Content-Type"].startswith("text/event-stream")
    events = sse_events(raw)
    kinds = [event for event, _ in events]
    assert kinds[0] == "meta"
    assert kinds[-1] == "done"
    assert kinds.count("stage") >= 5
    indices = [data["index"] for event, data in events if event == "stage"]
    assert all(0 <= index < 5 for index in indices)
    meta = events[0][1]
    assert meta["model"].startswith("cifkit-") and meta["mode"] == "live"


def test_run_sse_without_input_does_not_crash(live_server):
    status, _payload, raw, _headers = request(live_server.base, "POST", "/api/run",
                                              {"scenario": "repair", "payload": {}})
    assert status == 200
    events = sse_events(raw)
    assert events[-1][0] == "done"
    assert any(event == "notice" for event, _ in events)


# --------------------------------------------------------------------------- #
# 作业端点
# --------------------------------------------------------------------------- #
def _stub_job(bridge, monkeypatch):
    code = ("import json, sys, time; "
            "print('[resolve] ok demo', file=sys.stderr); "
            "time.sleep(0.05); "
            "print(json.dumps({'ok': True}))")
    monkeypatch.setattr(bridge, "_job_argv",
                        lambda kind, payload, run_dir: ([sys.executable, "-c", code], None))


def test_job_lifecycle_and_events_replay(live_server, api: Api, monkeypatch):
    _stub_job(live_server.bridge, monkeypatch)
    status, payload = api.post("/api/job", {"kind": "pipeline", "payload": {"query": "whatever"}})
    assert status == 200 and payload["ok"] is True
    job_id = payload["job"]["id"]
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        _status, current = api.get(f"/api/job?id={job_id}")
        if current["job"]["status"] != "running":
            break
        time.sleep(0.05)
    assert current["job"]["status"] == "done"
    assert current["job"]["result"] == {"ok": True}
    assert any(step["name"] == "resolve" for step in current["job"]["steps"])
    _status, _payload, raw, headers = api.raw("GET", f"/api/job/events?id={job_id}")
    assert headers["Content-Type"].startswith("text/event-stream")
    events = sse_events(raw)
    kinds = [event for event, _ in events]
    assert kinds[0] == "meta"
    assert kinds[-1] == "done"
    assert "log" in kinds and "step" in kinds
    assert events[-1][1]["status"] == "done"
    assert events[-1][1]["run_dir"]


def test_job_events_unknown_id_404(live_server, api: Api):
    status, payload = api.get("/api/job/events?id=job-nope")
    assert status == 404 and payload["error"] == "E_NOT_FOUND"


def test_job_cancel_endpoint(live_server, api: Api, monkeypatch):
    def slow_argv(kind, payload, run_dir):
        return ([sys.executable, "-c", "import time; time.sleep(60)"], None)

    monkeypatch.setattr(live_server.bridge, "_job_argv", slow_argv)
    status, payload = api.post("/api/job", {"kind": "pipeline", "payload": {"query": "x"}})
    assert status == 200
    job_id = payload["job"]["id"]
    status, payload = api.post("/api/job/cancel", {"id": job_id})
    assert status == 200 and payload["ok"] is True
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        _status, current = api.get(f"/api/job?id={job_id}")
        if current["job"]["status"] != "running":
            break
        time.sleep(0.05)
    assert current["job"]["status"] == "cancelled"


def test_job_payload_unknown_key_422(live_server, api: Api):
    status, payload = api.post("/api/job", {"kind": "pipeline",
                                           "payload": {"query": "x", "bogus": 1}})
    assert status == 422 and payload["error"] == "E_VALIDATION"


def test_job_outdir_conflict_409(live_server, api: Api, runs_dir: Path):
    taken = runs_dir / "taken"
    taken.mkdir()
    (taken / "keep.txt").write_text("x", encoding="utf-8")
    status, payload = api.post("/api/job", {"kind": "pipeline",
                                            "payload": {"query": "x", "outdir": "taken"}})
    assert status == 409 and payload["error"] == "E_CONFLICT"


def test_jobs_list_endpoint(live_server, api: Api, monkeypatch):
    _stub_job(live_server.bridge, monkeypatch)
    api.post("/api/job", {"kind": "pipeline", "payload": {"query": "whatever"}})
    status, payload = api.get("/api/jobs")
    assert status == 200 and isinstance(payload["jobs"], list) and payload["jobs"]
    assert payload["jobs"][0]["kind"] == "pipeline"
