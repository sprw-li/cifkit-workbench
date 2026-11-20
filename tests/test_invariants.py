"""CONTRACT §7 的十条不变量，一条一个测试（缺一即 bug）。

§7.1  GET 无副作用                 -> test_invariant_01_get_has_no_side_effects
§7.2  同名 run 不覆盖（409）        -> test_invariant_02_same_run_dir_conflict_409
§7.3  路径围栏（400 E_PATH_OUTSIDE）-> test_invariant_03_path_fence_rejects_escape
§7.4  artifact 扩展名白名单          -> test_invariant_04_artifact_extension_whitelist
§7.5  零写入浏览                    -> test_invariant_05_zero_write_browsing
§7.6  作业可取消（5s / cancelled）   -> test_invariant_06_job_cancellable
§7.7  失败留痕（写 op.json + stderr）-> test_invariant_07_failed_op_still_writes_op_json
§7.8  不伪造（null/[] + warnings）   -> test_invariant_08_no_fabrication
§7.9  离线（浏览零外网）             -> test_invariant_09_offline_browsing_no_network
§7.10 契约一致（formats 来自服务端） -> test_invariant_10_contract_consistency

所有用例默认离线；不写 runs/ 之外的位置，且清理自己造的 runs-dir。
"""

from __future__ import annotations

import json
import socket
import sys
import time
from pathlib import Path

import pytest

import bridge as bridge_module
from bridge import E_CONFLICT, WorkbenchError
from helpers import Api, make_fake_run, request, sse_events

CONTRACT_ARTIFACT_EXTENSIONS = {
    ".png", ".jpg", ".cif", ".vasp", ".poscar", ".xyz", ".extxyz", ".json", ".jsonl", ".csv",
    ".md", ".txt", ".lammps", ".data", ".qe", ".in", ".cp2k", ".gjf", ".orca", ".xsf", ".gro",
    ".top", ".cfg", ".gen", ".sdf", ".mol", ".pdb", ".zip", ".incar", ".kpoints",
}


def tree_signature(root: Path) -> list:
    """目录树签名：每个条目的相对路径 + mtime + 大小（用来断言"没写过"）。"""
    root = Path(root)
    if not root.is_dir():
        return []
    rows = []
    for path in sorted(root.rglob("*")):
        try:
            stat = path.stat()
        except OSError:
            continue
        rows.append((path.relative_to(root).as_posix(), int(stat.st_mtime_ns), stat.st_size))
    return rows


# --------------------------------------------------------------------------- #
# 1. GET 无副作用
# --------------------------------------------------------------------------- #
def test_invariant_01_get_has_no_side_effects(live_server, api: Api, runs_dir: Path):
    make_fake_run(runs_dir, "demo_run", with_preview=True)
    before = tree_signature(runs_dir)
    endpoints = [
        "/api/bootstrap", "/api/runs", "/api/run?id=demo_run",
        "/api/artifact?run=demo_run&name=report.md", "/api/sources", "/api/formats",
        "/api/jobs", "/api/job?id=job-nope",
    ]
    for endpoint in endpoints:
        status, _payload = api.get(endpoint)
        assert status in (200, 404), f"{endpoint} -> {status}"
    after = tree_signature(runs_dir)
    assert before == after, "GET 修改了 runs 目录（不变量 1 被破坏）"


# --------------------------------------------------------------------------- #
# 2. 同名 run 不覆盖
# --------------------------------------------------------------------------- #
class _FixedTime:
    """"bridge.time" 的替身：strftime 固定，其余透传（保证 op 目录名可复现）。"""

    def __getattr__(self, name):
        return getattr(time, name)

    def strftime(self, fmt, *args):  # noqa: A003
        return "20260101-000000"


def test_invariant_02_same_run_dir_conflict_409(bridge, monkeypatch, fixture_cif_text: str):
    monkeypatch.setattr(bridge_module, "time", _FixedTime())
    monkeypatch.setattr(bridge_module, "_new_token", lambda: "abcd")
    first = bridge.dispatch_op("validate", {}, None, {"cif_text": fixture_cif_text})
    run_dir = Path(first["run"]["abs"])
    assert (run_dir / "op.json").is_file()
    with pytest.raises(WorkbenchError) as info:
        bridge.dispatch_op("validate", {}, None, {"cif_text": fixture_cif_text})
    assert info.value.code == E_CONFLICT
    assert info.value.status == 409
    # 第一次的产物必须原样保留，不被第二次覆盖
    payload = json.loads((run_dir / "op.json").read_text(encoding="utf-8"))
    assert payload["ok"] is True


# --------------------------------------------------------------------------- #
# 3. 路径围栏
# --------------------------------------------------------------------------- #
def test_invariant_03_path_fence_rejects_escape(live_server, api: Api, runs_dir: Path):
    make_fake_run(runs_dir, "demo_run")
    cases = [
        "/api/run?id=../../etc",
        "/api/run?id=/etc/passwd",
        "/api/artifact?run=../../etc&name=report.md",
        "/api/artifact?run=demo_run&name=..%5C..%5Cx.cif",
        "/api/artifact?run=demo_run&name=..%2F..%2Fescape.cif",
    ]
    for path in cases:
        status, payload = api.get(path)
        assert status == 400, f"{path} -> {status}"
        assert payload and payload.get("error") == "E_PATH_OUTSIDE", f"{path} -> {payload}"


# --------------------------------------------------------------------------- #
# 4. artifact 白名单
# --------------------------------------------------------------------------- #
def test_invariant_04_artifact_extension_whitelist(live_server, api: Api, runs_dir: Path):
    run = make_fake_run(runs_dir, "demo_run")
    (run / "payload.exe").write_bytes(b"MZ")
    status, _ = api.get("/api/artifact?run=demo_run&name=payload.exe")
    assert status == 422, "白名单外的扩展名必须 4xx（E_VALIDATION）"
    status, _payload, raw, _headers = api.raw("GET",
                                             "/api/artifact?run=demo_run&name=structure.cif")
    assert status == 200 and raw.splitlines() == ["data_demo"]
    status, _ = api.get("/api/artifact?run=demo_run&name=missing.cif")
    assert status == 404
    assert bridge_module.ARTIFACT_EXTENSIONS == CONTRACT_ARTIFACT_EXTENSIONS


# --------------------------------------------------------------------------- #
# 5. 零写入浏览
# --------------------------------------------------------------------------- #
def test_invariant_05_zero_write_browsing(live_server, api: Api, runs_dir: Path):
    make_fake_run(runs_dir, "demo_run")
    before = tree_signature(runs_dir)
    assert api.get("/")[0] == 200
    assert api.get("/api/bootstrap")[0] == 200
    assert api.get("/api/runs")[0] == 200
    assert api.get("/api/run?id=demo_run")[0] == 200
    assert api.get("/api/formats")[0] == 200
    status, _payload, raw, _headers = request(live_server.base, "POST", "/api/run",
                                              {"scenario": "kickoff", "text": "下载 sod 拓扑的 mof"})
    assert status == 200
    assert sse_events(raw)[0][0] == "meta"
    after = tree_signature(runs_dir)
    assert before == after, "浏览/场景流写入了 runs 目录（不变量 5 被破坏）"


# --------------------------------------------------------------------------- #
# 6. 作业可取消
# --------------------------------------------------------------------------- #
def test_invariant_06_job_cancellable(bridge, monkeypatch):
    keep = bridge.runs_dir / "job-cancel-run"

    def fake_argv(kind, payload, run_dir):
        code = ("import pathlib, time; "
                f"p = pathlib.Path(r'{run_dir}') / 'keep.txt'; "
                "p.parent.mkdir(parents=True, exist_ok=True); "
                "p.write_text('x', encoding='utf-8'); "
                "print('[resolve] ok demo'); "
                "time.sleep(60)")
        return [sys.executable, "-c", code], None

    monkeypatch.setattr(bridge, "_job_argv", fake_argv)
    job = bridge.start_job("pipeline", {"query": "whatever"})
    try:
        deadline = time.monotonic() + 10
        while job.process is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert job.process is not None, "子进程没有启动"
        keep_file = Path(job.run_dir) / "keep.txt"
        deadline = time.monotonic() + 10
        while not keep_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        started = time.monotonic()
        bridge.cancel_job(job.id)
        while job.status == "running" and time.monotonic() - started < 5:
            time.sleep(0.05)
        assert job.status == "cancelled", f"status={job.status}"
        assert time.monotonic() - started < 5, "取消后子进程没有在 5s 内结束"
        assert job.to_dict()["returncode"] in (None, -9, -15)
        assert keep_file.exists(), "取消不得删除产物目录"
    finally:
        bridge.cancel_job(job.id)


# --------------------------------------------------------------------------- #
# 7. 失败留痕
# --------------------------------------------------------------------------- #
def test_invariant_07_failed_op_still_writes_op_json(bridge):
    with pytest.raises(WorkbenchError) as info:
        bridge.dispatch_op("repair", {"add_h": True}, None,
                           {"cif_text": "this is definitely not a CIF"})
    payload = info.value.to_dict()
    run = payload.get("run")
    assert isinstance(run, dict), "失败的 op 也必须返回 run 字段（§9.6）"
    run_dir = Path(run["abs"])
    op_json = run_dir / "op.json"
    assert op_json.is_file(), "失败也必须写 op.json"
    record = json.loads(op_json.read_text(encoding="utf-8"))
    assert record["ok"] is False
    assert record["stderr"], "op.json 必须保留原始 stderr 原文"
    assert record["error"]["code"], "op.json 必须记录 error.code"
    assert payload["error"] in ("E_VALIDATION", "E_INTERNAL")


# --------------------------------------------------------------------------- #
# 8. 不伪造
# --------------------------------------------------------------------------- #
def test_invariant_08_no_fabrication(bridge, runs_dir: Path):
    bare = runs_dir / "external_dir"
    bare.mkdir(parents=True, exist_ok=True)
    (bare / "structure.cif").write_text("data_x\n", encoding="utf-8")
    summary = bridge.run_summary(bare)
    assert summary["kind"] == "external"
    assert summary["n_steps"] == 0
    assert summary["atoms"] is None
    assert summary["formula"] is None
    assert summary["ok"] is None
    assert summary["has_manifest"] is False
    assert summary["warnings"], "不可知的数据必须配 warnings"
    snapshot = bridge.run_snapshot("external_dir")
    assert snapshot["manifest"] is None
    assert snapshot["report_md"] is None
    assert snapshot["plan"] == []
    assert snapshot["warnings"]


def test_invariant_08_op_run_falls_back_to_op_json(bridge, runs_dir: Path):
    """§10.1 的回落：有 op.json.result → 取其中的 natoms/formula；两处都没有 → null + warnings。"""
    fallback = runs_dir / "op_fallback"
    fallback.mkdir()
    (fallback / "op.json").write_text(json.dumps({
        "schema": "cifkit-workbench/op/v1", "op": "geom", "ok": True,
        "result": {"natoms": 276, "formula": "Zn12 H120 C96 N48"},
    }, ensure_ascii=False), encoding="utf-8")
    summary = bridge.run_summary(fallback)
    assert summary["kind"] == "op"
    assert summary["atoms"] == 276
    assert summary["formula"] == "Zn12H120C96N48"

    # validate 形态：数在 result.metrics 里
    nested = runs_dir / "op_nested"
    nested.mkdir()
    (nested / "op.json").write_text(json.dumps({
        "schema": "cifkit-workbench/op/v1", "op": "validate", "ok": True,
        "result": {"ok": True, "checks": [],
                   "metrics": {"natoms": 138, "formula": "Zn6 H60 C48 N24"}},
    }, ensure_ascii=False), encoding="utf-8")
    nested_summary = bridge.run_summary(nested)
    assert nested_summary["atoms"] == 138
    assert nested_summary["formula"] == "Zn6H60C48N24"

    # op.json 有但两个数都没有 → null + warnings（绝不臆造）
    blank = runs_dir / "op_blank"
    blank.mkdir()
    (blank / "op.json").write_text(json.dumps({
        "schema": "cifkit-workbench/op/v1", "op": "sources_probe", "ok": True, "result": [],
    }, ensure_ascii=False), encoding="utf-8")
    blank_summary = bridge.run_summary(blank)
    assert blank_summary["atoms"] is None and blank_summary["formula"] is None
    assert any("natoms" in item for item in blank_summary["warnings"])


# --------------------------------------------------------------------------- #
# 9. 离线
# --------------------------------------------------------------------------- #
def test_invariant_09_offline_browsing_no_network(live_server, api: Api, runs_dir: Path,
                                                  monkeypatch):
    make_fake_run(runs_dir, "demo_run")
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def guard(address):
        host = address[0] if isinstance(address, tuple) and address else ""
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise AssertionError(f"浏览路径尝试连接外网：{address}")
        return None

    def connect(self, address):
        guard(address)
        return real_connect(self, address)

    def connect_ex(self, address):
        guard(address)
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    for endpoint in ("/api/bootstrap", "/api/runs", "/api/run?id=demo_run",
                     "/api/artifact?run=demo_run&name=report.md", "/api/sources",
                     "/api/formats", "/api/jobs"):
        status, _payload = api.get(endpoint)
        assert status in (200, 404), endpoint


# --------------------------------------------------------------------------- #
# 10. 契约一致
# --------------------------------------------------------------------------- #
def test_invariant_10_contract_consistency(live_server, api: Api):
    status, bootstrap = api.get("/api/bootstrap")
    assert status == 200 and bootstrap["ok"] is True
    server_formats = bootstrap["formats"]
    assert server_formats, "Bootstrap.formats 必须来自服务端实际返回且非空"
    assert bootstrap["bundles"], "Bootstrap.bundles 必须非空"
    status, formats_payload = api.get("/api/formats")
    assert status == 200
    assert set(formats_payload["formats"]) == set(server_formats)
    _, service = live_server.bridge._load_cifkit()
    service_ops = set(service.OPS)
    inproc_ops = {"validate", "features", "convert", "topology", "fetch", "dataset_rows"}
    assert inproc_ops <= service_ops, f"cifkit.service 缺少进程内 op：{inproc_ops - service_ops}"
    cli_ops = {"repair", "geom", "export", "dataset", "sources_probe"}
    assert set(bridge_module.OP_WHITELIST) == inproc_ops - {"dataset_rows"} | cli_ops
    ids = [item["id"] for item in bootstrap["scenarios"]]
    assert ids == ["kickoff", "repair", "geometry", "export", "inspect", "library"]
    for scenario in bootstrap["scenarios"]:
        indices = [stage["index"] for stage in scenario["stages"]]
        assert indices == list(range(len(indices)))
