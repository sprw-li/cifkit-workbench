"""bridge.py 的单元测试：定位、CLI 封装与流式、op dispatch、run 形状、作业解析。"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

import bridge as bridge_module
from bridge import Bridge, WorkbenchError

# --------------------------------------------------------------------------- #
# 定位（CONTRACT §1）
# --------------------------------------------------------------------------- #
def test_resolve_cifkit_root_order(repo_root: Path, work_state: Path):
    here = repo_root / "workbench"
    explicit = bridge_module.resolve_cifkit_root(explicit=repo_root, here=here, env={}, cwd=repo_root)
    assert explicit == repo_root.resolve()
    from_env = bridge_module.resolve_cifkit_root(here=here, env={"CIFKIT_ROOT": str(repo_root)},
                                                 cwd=repo_root)
    assert from_env == repo_root.resolve()
    # 同级仓库优先于「同仓」
    parent = work_state / "fake-sibling"
    sibling = parent / "Project5-skill-cif"
    (sibling / "cifkit").mkdir(parents=True, exist_ok=True)
    (sibling / "cifkit" / "__init__.py").write_text("", encoding="utf-8")
    fake_here = parent / "workbench"
    fake_here.mkdir(parents=True, exist_ok=True)
    assert bridge_module.resolve_cifkit_root(here=fake_here, env={}, cwd=parent) == sibling.resolve()
    # 工作台与 cifkit 同仓
    same = work_state / "fake-same"
    (same / "cifkit").mkdir(parents=True, exist_ok=True)
    (same / "cifkit" / "__init__.py").write_text("", encoding="utf-8")
    here2 = same / "workbench"
    here2.mkdir(parents=True, exist_ok=True)
    assert bridge_module.resolve_cifkit_root(here=here2, env={}, cwd=work_state) == same.resolve()


def test_resolve_python_prefers_explicit(repo_root: Path):
    resolved = bridge_module.resolve_python(sys.executable, cifkit_root=repo_root, env={})
    assert resolved == str(Path(sys.executable).resolve())
    venv = bridge_module.resolve_python(None, cifkit_root=repo_root, env={})
    assert Path(venv).is_file()


def test_is_within_and_artifact_kind():
    assert bridge_module.is_within("/a/b/c", "/a/b")
    assert not bridge_module.is_within("/a/bc", "/a/b")
    assert bridge_module.artifact_kind("preview.png") == "image"
    assert bridge_module.artifact_kind("structure.cif") == "structure"
    assert bridge_module.artifact_kind("dataset.jsonl") == "json"
    assert bridge_module.artifact_kind("bundle.zip") == "archive"
    assert bridge_module.artifact_kind("report.md") == "text"


def test_json_dumps_is_strict_rfc8259():
    text = bridge_module.json_dumps({"x": float("nan"), "y": float("inf"), "p": Path("a/b")})
    assert "NaN" not in text and "Infinity" not in text
    assert json.loads(text) == {"x": None, "y": None, "p": str(Path("a/b"))}


def test_parse_progress_line():
    assert bridge_module.parse_progress_line("[resolve] ok local:x atoms=1") == {
        "name": "resolve", "status": "ok", "detail": "local:x atoms=1"}
    assert bridge_module.parse_progress_line("[render] skip 缺 matplotlib")["status"] == "skipped"
    assert bridge_module.parse_progress_line("[manifest] failed boom")["status"] == "failed"
    assert bridge_module.parse_progress_line("not a progress line") is None


def test_parse_json_document_tolerates_prefix_noise():
    assert bridge_module.parse_json_document('noise\n{"a": 1}\n') == {"a": 1}
    assert bridge_module.parse_json_document("") is None
    assert bridge_module.parse_json_document("{broken") is None


# --------------------------------------------------------------------------- #
# CLI 子进程封装
# --------------------------------------------------------------------------- #
def test_run_cli_captures_stdout_stderr_and_lines():
    lines: list[tuple[str, str]] = []
    result = bridge_module.run_cli(
        [sys.executable, "-c", "import sys; print('out1'); print('err1', file=sys.stderr)"],
        timeout=30, on_line=lambda line, stream: lines.append((stream, line)))
    assert result.returncode == 0
    assert result.stdout.strip() == "out1"
    assert result.stderr.strip() == "err1"
    assert ("stdout", "out1") in lines and ("stderr", "err1") in lines
    assert result.cancelled is False and result.timed_out is False


def test_run_cli_timeout_kills_process():
    started = time.monotonic()
    result = bridge_module.run_cli([sys.executable, "-c", "import time; time.sleep(30)"],
                                   timeout=1.0)
    assert result.timed_out is True
    assert time.monotonic() - started < 10
    assert result.returncode is not None


def test_run_cli_cancel_event_kills_process():
    event = threading.Event()
    threading.Timer(0.3, event.set).start()
    started = time.monotonic()
    result = bridge_module.run_cli([sys.executable, "-c", "import time; time.sleep(30)"],
                                   timeout=60, cancel_event=event)
    assert result.cancelled is True
    assert time.monotonic() - started < 5


def test_run_cli_missing_executable_is_reported():
    result = bridge_module.run_cli(["definitely-not-a-real-binary-xyz"], timeout=5)
    assert result.returncode != 0 and result.stderr


# --------------------------------------------------------------------------- #
# 参数白名单 / 只读 / 围栏
# --------------------------------------------------------------------------- #
def test_check_op_and_params_whitelist(bridge):
    assert bridge.check_op("geom") == "geom"
    with pytest.raises(WorkbenchError) as info:
        bridge.check_op("rm -rf")
    assert info.value.code == "E_VALIDATION"
    with pytest.raises(WorkbenchError) as info:
        bridge.check_params("validate", {"argv": ["--evil"]})
    assert info.value.code == "E_VALIDATION"
    assert bridge.check_params("geom", {"supercell": [2, 2, 1]})["supercell"] == [2, 2, 1]


def test_op_disabled_without_allow_exec(runs_dir: Path, repo_root: Path):
    read_only = Bridge(runs_dir, cifkit_root=repo_root, allow_exec=False)
    assert read_only.allow_exec is False
    with pytest.raises(WorkbenchError) as info:
        read_only.dispatch_op("validate", {}, None, {"cif_text": "x"})
    assert info.value.code == "E_EXEC_DISABLED" and info.value.status == 403
    assert not list(runs_dir.iterdir()), "只读模式不得创建 run 目录"


def test_readonly_runs_dir_returns_403(work_state: Path, repo_root: Path):
    blocker = work_state / "runs-blocker-file"
    blocker.write_text("not a directory", encoding="utf-8")
    instance = Bridge(blocker, cifkit_root=repo_root, allow_exec=True)
    assert instance.runs_writable is False
    with pytest.raises(WorkbenchError) as info:
        instance.dispatch_op("validate", {}, None, {"cif_text": "x"})
    assert info.value.code == "E_READONLY_RUNS" and info.value.status == 403


def test_new_op_dir_conflict_is_409(bridge):
    first = bridge.new_op_dir("geom", stamp="20260101-000000", token="abcd")
    assert first.is_dir()
    with pytest.raises(WorkbenchError) as info:
        bridge.new_op_dir("geom", stamp="20260101-000000", token="abcd")
    assert info.value.code == "E_CONFLICT" and info.value.status == 409


def test_op_requires_input(bridge):
    with pytest.raises(WorkbenchError) as info:
        bridge.dispatch_op("geom", {"supercell": [2, 2, 1]}, None, None)
    assert info.value.code == "E_BAD_REQUEST"
    assert info.value.to_dict()["run"] is not None, "失败也要返回 run（§9.6）"


def test_input_path_fence(bridge, work_state: Path):
    outside = work_state / "outside.cif"
    outside.write_text("data_x\n", encoding="utf-8")
    with pytest.raises(WorkbenchError) as info:
        bridge.dispatch_op("validate", {}, None, {"path": str(outside)})
    assert info.value.code == "E_PATH_OUTSIDE" and info.value.status == 400


# --------------------------------------------------------------------------- #
# op（进程内 + CLI）
# --------------------------------------------------------------------------- #
def test_op_validate_inproc_writes_validation_json(bridge, fixture_cif_text: str):
    outcome = bridge.dispatch_op("validate", {}, None, {"cif_text": fixture_cif_text})
    assert outcome["ok"] is True
    run = outcome["run"]
    assert run["kind"] == "op"
    assert run["ok"] is True
    run_dir = Path(run["abs"])
    assert (run_dir / "op.json").is_file()
    assert (run_dir / "validation.json").is_file()
    record = json.loads((run_dir / "op.json").read_text(encoding="utf-8"))
    assert record["schema"] == "cifkit-workbench/op/v1"
    assert record["op"] == "validate"
    assert record["input"]["from"] == "cif_text"
    assert outcome["result"]["ok"] is True
    assert any(item["name"] == "validation.json" for item in outcome["products"])
    # §10.1：op run 没有 manifest，回落 op.json.result.metrics（值来自 cifkit --json，不是推算）
    assert run["atoms"] == 138
    assert run["formula"] == "Zn6H60C48N24"
    assert run["warnings"]


def test_op_features_writes_csv_shape(bridge, fixture_cif_text: str):
    outcome = bridge.dispatch_op("features", {"rdf": False}, None, {"cif_text": fixture_cif_text})
    result = outcome["result"]
    assert result["n_inputs"] == 1 and result["n_ok"] == 1 and result["n_failed"] == 0
    assert isinstance(result["rows"], list) and result["rows"]
    assert (Path(outcome["run"]["abs"]) / "features.csv").is_file()


def test_op_convert_writes_structure_files(bridge, fixture_cif_text: str):
    outcome = bridge.dispatch_op("convert", {"formats": ["cif", "xyz"]}, None,
                                 {"cif_text": fixture_cif_text})
    assert outcome["ok"] is True
    run_dir = Path(outcome["run"]["abs"])
    files = sorted(path.name for path in run_dir.iterdir())
    assert any(name.endswith(".cif") for name in files)
    assert any(name.endswith(".xyz") for name in files)


def test_op_params_not_in_whitelist(bridge):
    with pytest.raises(WorkbenchError) as info:
        bridge.dispatch_op("geom", {"argv": ["--evil"]}, None, {"cif_text": "x"})
    assert info.value.code == "E_VALIDATION" and info.value.status == 422


def test_op_dataset_accepts_bool_and_string_sources(bridge, runs_dir: Path):
    outcome = bridge.dispatch_op("dataset", {"from_runs": True, "jobs": 2}, None, None)
    assert outcome["ok"] is True
    assert outcome["result"]["n_runs"] == 0
    assert (Path(outcome["run"]["abs"]) / "op.json").is_file()
    outcome = bridge.dispatch_op("dataset", {"from_runs": str(runs_dir)}, None, None)
    assert outcome["ok"] is True
    with pytest.raises(WorkbenchError) as info:
        bridge.dispatch_op("dataset", {"from_runs": "definitely/not/here-xyz"}, None, None)
    assert info.value.code == "E_VALIDATION" and info.value.status == 422
    with pytest.raises(WorkbenchError) as info:
        bridge.dispatch_op("dataset", {"from_runs": 123}, None, None)
    assert info.value.code == "E_VALIDATION" and info.value.status == 422


def test_op_params_wrong_type_is_422_not_500(bridge, fixture_cif_text: str):
    with pytest.raises(WorkbenchError) as info:
        bridge.dispatch_op("geom", {"supercell": "2,2,1"}, None, {"cif_text": fixture_cif_text})
    assert info.value.code == "E_VALIDATION" and info.value.status == 422
    assert info.value.to_dict()["run"] is not None


def test_op_sources_probe_requires_consent(bridge):
    with pytest.raises(WorkbenchError) as info:
        bridge.dispatch_op("sources_probe", {}, None, None, consent=False)
    assert info.value.code == "E_CONSENT_REQUIRED" and info.value.status == 400


# --------------------------------------------------------------------------- #
# route / run 形状
# --------------------------------------------------------------------------- #
def test_route_returns_spec_and_plan(bridge):
    routed = bridge.route("下载 RCSR 为 sod 的 mof，补氢，2x2x1 超胞")
    assert routed["ok"] is True
    assert routed["spec"]["topology"] == "sod"
    assert routed["plan"] and routed["plan"][0]["name"] == "resolve"


def test_route_without_structure_is_422(bridge):
    with pytest.raises(WorkbenchError) as info:
        bridge.route("你好呀")
    assert info.value.code == "E_NO_STRUCTURE" and info.value.status == 422
    detail = info.value.to_dict()["detail"]
    assert detail["spec"] is not None and detail["plan"] is not None


def test_run_summary_from_manifest(bridge, runs_dir: Path):
    from helpers import make_fake_run

    make_fake_run(runs_dir, "demo_run", with_preview=True)
    summary = bridge.run_summary(runs_dir / "demo_run")
    assert summary["rel"] == "demo_run"
    assert summary["kind"] == "pipeline"
    assert summary["ok"] is True
    assert summary["atoms"] == 2 and summary["formula"] == "H2"
    assert summary["topology"] == "sod" and summary["source"] == "local"
    assert summary["n_steps"] == 2
    assert summary["has_manifest"] and summary["has_report"] and summary["has_preview"]
    runs = bridge.list_runs()
    assert [item["rel"] for item in runs] == ["demo_run"]


def test_run_snapshot_shape(bridge, runs_dir: Path):
    from helpers import make_fake_run

    make_fake_run(runs_dir, "demo_run")
    snapshot = bridge.run_snapshot("demo_run")
    for key in ("manifest", "report_md", "plan", "steps", "artifacts", "validation", "op",
                "warnings"):
        assert key in snapshot, key
    assert snapshot["report_md"].startswith("# demo")
    assert snapshot["plan"][0]["name"] == "resolve"
    assert snapshot["op"] is None
    names = {item["name"] for item in snapshot["artifacts"]}
    assert {"structure.cif", "report.md", "manifest.json"} <= names


# --------------------------------------------------------------------------- #
# 作业（CONTRACT §9.2 + §3.4）
# --------------------------------------------------------------------------- #
def test_job_payload_validation(bridge):
    with pytest.raises(WorkbenchError) as info:
        bridge.start_job("pipeline", {"query": "a", "spec": {}})
    assert info.value.code == "E_VALIDATION"
    with pytest.raises(WorkbenchError) as info:
        bridge.start_job("pipeline", {"query": "a", "unknown": 1})
    assert info.value.code == "E_VALIDATION"
    with pytest.raises(WorkbenchError) as info:
        bridge.start_job("pipeline", {})
    assert info.value.code == "E_VALIDATION"
    with pytest.raises(WorkbenchError) as info:
        bridge.start_job("batch", {})
    assert info.value.code == "E_VALIDATION"
    with pytest.raises(WorkbenchError) as info:
        bridge.start_job("nope", {})
    assert info.value.code == "E_VALIDATION"
    with pytest.raises(WorkbenchError) as info:
        bridge.start_job("pipeline", {"query": "a", "outdir": "../../escape"})
    assert info.value.code == "E_PATH_OUTSIDE"


def test_job_outdir_default_inside_runs_dir(bridge):
    resolved = bridge.job_outdir("pipeline", None)
    assert bridge_module.is_within(resolved, bridge.runs_dir)
    assert resolved.name.startswith("job-") and resolved.name.endswith("-pipeline-" + resolved.name[-4:])


def test_job_outdir_nonempty_conflict(bridge, runs_dir: Path):
    taken = runs_dir / "taken"
    taken.mkdir()
    (taken / "keep.txt").write_text("x", encoding="utf-8")
    with pytest.raises(WorkbenchError) as info:
        bridge.start_job("pipeline", {"query": "a", "outdir": "taken"})
    assert info.value.code == "E_CONFLICT" and info.value.status == 409


def test_job_streams_progress_and_final_json(bridge, monkeypatch):
    code = ("import json, sys, time; "
            "print('[resolve] ok demo'); "
            "print('hello log'); "
            "print('[export] ok formats=cif', file=sys.stderr); "
            "time.sleep(0.05); "
            "print(json.dumps({'ok': True, 'outdir': 'demo'}))")
    monkeypatch.setattr(bridge, "_job_argv",
                        lambda kind, payload, run_dir: ([sys.executable, "-c", code], None))
    job = bridge.start_job("pipeline", {"query": "whatever"})
    deadline = time.monotonic() + 60
    while job.status == "running" and time.monotonic() < deadline:
        time.sleep(0.05)
    assert job.status == "done", job.error
    assert job.returncode == 0
    assert job.result == {"ok": True, "outdir": "demo"}
    # stdout/stderr 由两个读取线程并发消费，跨流顺序不保证（真实 pipeline 的进度行只在
    # stderr 一个流里）；这里只断言两条 step 都被解析到且状态正确
    names = sorted(step["name"] for step in job.to_dict()["steps"])
    assert names == ["export", "resolve"]
    assert all(step["status"] == "ok" for step in job.to_dict()["steps"])
    assert any("hello log" in line for line in job.to_dict()["log_tail"])


def test_job_failure_records_error_and_tail(bridge, monkeypatch):
    code = "import sys; print('[resolve] failed boom', file=sys.stderr); sys.exit(3)"
    monkeypatch.setattr(bridge, "_job_argv",
                        lambda kind, payload, run_dir: ([sys.executable, "-c", code], None))
    job = bridge.start_job("pipeline", {"query": "whatever"})
    deadline = time.monotonic() + 60
    while job.status == "running" and time.monotonic() < deadline:
        time.sleep(0.05)
    assert job.status == "failed"
    assert job.returncode == 3
    assert job.error
    assert job.to_dict()["steps"][0]["status"] == "failed"


def test_job_accepts_spec_and_writes_spec_file(bridge, monkeypatch):
    captured: dict = {}

    def fake_argv(kind, payload, run_dir):
        captured["payload"] = payload
        return ([sys.executable, "-c", "print('{}')"], None)

    monkeypatch.setattr(bridge, "_job_argv", fake_argv)
    job = bridge.start_job("pipeline", {"spec": {"source": "local", "structure_id": "x"},
                                        "formats": ["cif", "vasp"]})
    deadline = time.monotonic() + 60
    while job.status == "running" and time.monotonic() < deadline:
        time.sleep(0.05)
    assert job.status == "done"
    assert captured["payload"]["formats"] == ["cif", "vasp"]


def test_job_events_replay_is_ordered(bridge, monkeypatch):
    code = ("import json, sys, time; "
            "print('[resolve] ok demo', file=sys.stderr); "
            "time.sleep(0.05); "
            "print(json.dumps({'ok': True}))")
    monkeypatch.setattr(bridge, "_job_argv",
                        lambda kind, payload, run_dir: ([sys.executable, "-c", code], None))
    job = bridge.start_job("pipeline", {"query": "whatever"})
    deadline = time.monotonic() + 60
    while job.status == "running" and time.monotonic() < deadline:
        time.sleep(0.05)
    events = list(bridge.job_events(job.id))
    kinds = [event for event, _ in events]
    assert kinds[0] == "meta"
    assert kinds[-1] == "done"
    assert "log" in kinds and "step" in kinds
    assert events[-1][1]["status"] == "done"


def test_get_job_unknown_is_404(bridge):
    with pytest.raises(WorkbenchError) as info:
        bridge.get_job("job-nope")
    assert info.value.code == "E_NOT_FOUND" and info.value.status == 404


# --------------------------------------------------------------------------- #
# 慢：离线本地 pipeline 端到端（默认跳过）
# --------------------------------------------------------------------------- #
@pytest.mark.slow
@pytest.mark.e2e
def test_pipeline_e2e_offline(bridge, repo_root: Path, fixture_cif: Path):
    """local 数据源的 pipeline 全程离线；验证 Job 的 run_dir 真能产出 manifest。"""
    spec = {"source": "local", "structure_id": str(fixture_cif), "topology": "sod",
            "repair_h": False, "repair_disorder": True, "remove_close_contacts": True,
            "output_formats": ["cif"]}
    job = bridge.start_job("pipeline", {"spec": spec})
    deadline = time.monotonic() + 600
    while job.status == "running" and time.monotonic() < deadline:
        time.sleep(0.2)
    assert job.status == "done", (job.status, job.error, job.to_dict()["log_tail"][-10:])
    run_dir = Path(job.run_dir)
    assert (run_dir / "manifest.json").is_file()
    assert job.result and job.result.get("ok") is True
