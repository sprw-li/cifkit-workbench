"""cifkit 工作台 · 桥接层（bridge）。

唯一接口真源：CONTRACT.md（冻结 2026-11-21）。本模块只做流程编排与进程管理：

* cifkit 仓库根 / python 解释器定位（CONTRACT §1）
* 进程内调用 cifkit.service.handle（快操作）
* CLI 子进程封装（spawn 数组、cwd、env、超时、kill）
* pipeline / batch 的行流式迭代（stdout 逐行 yield + 最终 --json 解析）
* runs 目录扫描与 RunSummary / RunSnapshot（§3.2）
* op run 目录与 op.json（§5）
* 内存作业表与 SSE 事件源（§3.4 / §4.2）

本模块**不实现任何晶体学逻辑**（§8）：不补氢、不算对称、不切面，一律调 cifkit。
"""

from __future__ import annotations

import json
import math
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

WORKBENCH_VERSION = "1.0"

# ---------------------------------------------------------------------------
# 错误模型（CONTRACT §6）
# ---------------------------------------------------------------------------
E_BAD_REQUEST = "E_BAD_REQUEST"
E_PATH_OUTSIDE = "E_PATH_OUTSIDE"
E_CONSENT_REQUIRED = "E_CONSENT_REQUIRED"
E_NOT_FOUND = "E_NOT_FOUND"
E_CONFLICT = "E_CONFLICT"
E_VALIDATION = "E_VALIDATION"
E_NO_STRUCTURE = "E_NO_STRUCTURE"
E_READONLY_RUNS = "E_READONLY_RUNS"
E_EXEC_DISABLED = "E_EXEC_DISABLED"
E_TIMEOUT = "E_TIMEOUT"
E_INTERNAL = "E_INTERNAL"
E_CANCELLED = "E_CANCELLED"

ERROR_STATUS: dict[str, int] = {
    E_BAD_REQUEST: 400,
    E_PATH_OUTSIDE: 400,
    E_CONSENT_REQUIRED: 400,
    E_NOT_FOUND: 404,
    E_CONFLICT: 409,
    E_VALIDATION: 422,
    E_NO_STRUCTURE: 422,
    E_READONLY_RUNS: 403,
    E_EXEC_DISABLED: 403,
    E_TIMEOUT: 504,
    E_INTERNAL: 500,
    E_CANCELLED: 409,
}


class WorkbenchError(Exception):
    """带稳定 code / HTTP status / hint / detail 的工作台错误。"""

    def __init__(self, code: str, message: str, hint: str | None = None, *,
                 detail: Any = None, run: Any = None, argv: Sequence[str] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.detail = detail
        self.run = run
        self.argv = list(argv) if argv is not None else None
        self.status = ERROR_STATUS.get(code, 500)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"ok": False, "error": self.code, "message": self.message}
        if self.hint:
            out["hint"] = self.hint
        if self.detail is not None:
            out["detail"] = _clean(self.detail)
        # CONTRACT §9.6：失败响应也带 run（失败后仍写好 op.json 的那个目录）与 argv
        out["run"] = _clean(self.run)
        if self.argv is not None:
            out["argv"] = _clean(self.argv)
        return out


# ---------------------------------------------------------------------------
# JSON / 路径 / 时间小工具
# ---------------------------------------------------------------------------
def _clean(value: Any) -> Any:
    """把任意值变成严格 JSON 可序列化的值（Path -> str，非有限浮点 -> None）。"""
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_clean(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value
    return str(value)


def json_dumps(value: Any) -> str:
    """严格 JSON（RFC 8259）：UTF-8 + ensure_ascii=False + allow_nan=False。"""
    return json.dumps(_clean(value), ensure_ascii=False, allow_nan=False, default=str)


def read_json_file(path: Path) -> Any:
    """读 JSON；不存在/不可解析一律 None（调用方负责 warnings，绝不臆造）。"""
    try:
        if not path.is_file():
            return None
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def write_json_file(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(_clean(payload), ensure_ascii=False, indent=2, default=str),
                   encoding="utf-8")
    os.replace(tmp, path)
    return path


def iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _norm(path: Any) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def is_within(path: Any, root: Any) -> bool:
    """True 表示 path 在 root 之内（含相等）。大小写按平台归一。"""
    p, r = _norm(path), _norm(root)
    return p == r or p.startswith(r + os.sep)


def _display(value: Any) -> str:
    text = str(value)
    return text if len(text) <= 120 else text[:117] + "..."


# ---------------------------------------------------------------------------
# artifact 扩展名白名单（CONTRACT §7.4）
# ---------------------------------------------------------------------------
IMAGE_EXT = {".png", ".jpg"}
STRUCTURE_EXT = {".cif", ".vasp", ".poscar", ".xyz", ".extxyz", ".lammps", ".data", ".qe",
                 ".in", ".cp2k", ".gjf", ".orca", ".xsf", ".gro", ".top", ".cfg", ".gen",
                 ".sdf", ".mol", ".pdb", ".incar", ".kpoints"}
JSON_EXT = {".json", ".jsonl"}
ARCHIVE_EXT = {".zip"}
TEXT_EXT = {".csv", ".md", ".txt"}
ARTIFACT_EXTENSIONS = IMAGE_EXT | STRUCTURE_EXT | JSON_EXT | ARCHIVE_EXT | TEXT_EXT


def artifact_kind(name: str) -> str:
    ext = Path(str(name)).suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in STRUCTURE_EXT:
        return "structure"
    if ext in JSON_EXT:
        return "json"
    if ext in ARCHIVE_EXT:
        return "archive"
    return "text"


# ---------------------------------------------------------------------------
# cifkit 根 / python 解释器定位（CONTRACT §1）
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent


def looks_like_cifkit_root(path: Any) -> bool:
    try:
        return (Path(path) / "cifkit" / "__init__.py").is_file()
    except OSError:  # pragma: no cover - 极端路径
        return False


def resolve_cifkit_root(explicit: str | Path | None = None, *, here: Path | None = None,
                        env: dict[str, str] | None = None,
                        cwd: str | Path | None = None) -> Path:
    """按 CONTRACT §1 的顺序定位 cifkit 仓库根；失败抛 E_INTERNAL（启动报错，不带半截状态）。"""
    here = Path(here or _HERE)
    env = os.environ if env is None else env
    cwd = Path(cwd or os.getcwd())
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    from_env = env.get("CIFKIT_ROOT")
    if from_env:
        candidates.append(Path(from_env))
    candidates += [
        here.parent / "Project5-skill-cif",   # 同级仓库
        here.parent,                          # 工作台与 cifkit 同仓
        cwd / "Project5-skill-cif",
        cwd,
        here,
    ]
    for candidate in candidates:
        if looks_like_cifkit_root(candidate):
            return candidate.resolve()
    try:  # 已安装包所在仓库根
        import cifkit  # noqa: PLC0415

        root = Path(cifkit.__file__).resolve().parents[1]
        if looks_like_cifkit_root(root):
            return root
    except Exception:  # noqa: BLE001 - 未安装
        pass
    raise WorkbenchError(
        E_INTERNAL,
        "找不到 cifkit 仓库根（需要含 cifkit/__init__.py 的目录）",
        "用 --cifkit-root PATH 或环境变量 CIFKIT_ROOT 指定",
    )


def resolve_python(explicit: str | Path | None = None, *, cifkit_root: str | Path,
                   env: dict[str, str] | None = None) -> str:
    """按 CONTRACT §1 的顺序定位与 cifkit 对齐的 python。"""
    env = os.environ if env is None else env
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    from_env = env.get("CIFKIT_PYTHON")
    if from_env:
        candidates.append(Path(from_env))
    root = Path(cifkit_root)
    candidates += [root / ".venv" / "Scripts" / "python.exe", root / ".venv" / "bin" / "python"]
    for candidate in candidates:
        try:
            if candidate.is_file():
                return str(candidate.resolve())
        except OSError:  # pragma: no cover
            continue
    return sys.executable


# ---------------------------------------------------------------------------
# CLI 子进程封装
# ---------------------------------------------------------------------------
class CliResult:
    """一次 CLI 调用的结果（原始 stdout / stderr 不加工）。"""

    __slots__ = ("argv", "returncode", "stdout", "stderr", "elapsed_ms", "cancelled",
                 "timed_out", "cwd")

    def __init__(self, argv: Sequence[str], returncode: int | None, stdout: str, stderr: str,
                 elapsed_ms: float, cancelled: bool, timed_out: bool, cwd: str) -> None:
        self.argv = [str(a) for a in argv]
        self.returncode = returncode
        self.stdout = stdout or ""
        self.stderr = stderr or ""
        self.elapsed_ms = elapsed_ms
        self.cancelled = cancelled
        self.timed_out = timed_out
        self.cwd = cwd

    def json(self) -> Any:
        return parse_json_document(self.stdout)

    def ok(self) -> bool:
        return self.returncode == 0 and not self.cancelled and not self.timed_out


def parse_json_document(text: str) -> Any:
    """解析一段 stdout：整段优先；否则取最后一个以 { 或 [ 开头的可解析文档段。"""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        pass
    lines = text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                return json.loads("\n".join(lines[index:]))
            except ValueError:
                continue
    return None


def _kill_tree(proc: subprocess.Popen | None) -> None:
    """终止子进程及其子进程（batch 会再 spawn）。

    Windows 先试 taskkill /T（能带走孙子进程），但它可能被沙箱/权限拒绝，
    所以必须**验证真的死了**再返回，否则回落到 proc.kill()。
    输出走 DEVNULL（受限环境下管道可能被拒），因此不用 capture_output。
    """
    if proc is None or proc.poll() is not None:
        return
    if os.name == "nt":
        killed = False
        try:
            done = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            killed = done.returncode == 0
        except OSError:
            killed = False
        if killed:
            try:
                proc.wait(timeout=3)
            except Exception:  # noqa: BLE001
                pass
            if proc.poll() is not None:
                return
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=3)
    except Exception:  # noqa: BLE001
        pass


def run_cli(argv: Sequence[str], *, cwd: str | Path | None = None,
            env: dict[str, str] | None = None, timeout: float = 300.0,
            cancel_event: threading.Event | None = None,
            on_line: Callable[[str, str], None] | None = None,
            on_start: Callable[[subprocess.Popen], None] | None = None) -> CliResult:
    """以数组形式 spawn 子进程；逐行回调；支持超时与取消（强杀进程树）。"""
    started = time.monotonic()
    started_cwd = str(cwd or os.getcwd())
    full_env = os.environ.copy()
    full_env.setdefault("PYTHONIOENCODING", "utf-8")
    full_env.setdefault("PYTHONUTF8", "1")
    if env:
        full_env.update({str(k): str(v) for k, v in env.items()})
    popen_kwargs: dict[str, Any] = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        popen_kwargs["start_new_session"] = True

    argv_list = [str(a) for a in argv]
    try:
        proc = subprocess.Popen(
            argv_list, cwd=str(cwd) if cwd else None, env=full_env,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1, **popen_kwargs,
        )
    except OSError as exc:
        message = f"无法启动子进程 {argv_list[:1]}：{type(exc).__name__}: {exc}"
        return CliResult(argv_list, 127, "", message,
                         round((time.monotonic() - started) * 1000, 1), False, False, started_cwd)

    out_lines: list[str] = []
    err_lines: list[str] = []

    def _reader(stream, sink: list[str], label: str) -> None:
        try:
            for raw in stream:
                line = raw.rstrip("\r\n")
                sink.append(line)
                if on_line is not None:
                    try:
                        on_line(line, label)
                    except Exception:  # noqa: BLE001 - 回调不得杀死读取线程
                        pass
        except Exception:  # noqa: BLE001
            pass
        finally:
            try:
                stream.close()
            except Exception:  # noqa: BLE001
                pass

    threads = [
        threading.Thread(target=_reader, args=(proc.stdout, out_lines, "stdout"), daemon=True),
        threading.Thread(target=_reader, args=(proc.stderr, err_lines, "stderr"), daemon=True),
    ]
    for thread in threads:
        thread.start()
    if on_start is not None:
        try:
            on_start(proc)
        except Exception:  # noqa: BLE001
            pass

    cancelled = False
    timed_out = False
    deadline = (started + float(timeout)) if timeout else None
    while proc.poll() is None:
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            _kill_tree(proc)
            break
        if deadline is not None and time.monotonic() > deadline:
            timed_out = True
            _kill_tree(proc)
            break
        time.sleep(0.05)
    if cancelled or timed_out:
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            _kill_tree(proc)
    for thread in threads:
        thread.join(timeout=3)
    return CliResult(
        argv_list, proc.returncode, "\n".join(out_lines), "\n".join(err_lines),
        round((time.monotonic() - started) * 1000, 1), cancelled, timed_out, started_cwd,
    )


_PROGRESS_RE = re.compile(r"^\[([^\]]+)\]\s+(\S+)\s*(.*)")
_PROGRESS_STATUS = {"ok": "ok", "done": "ok", "warn": "ok", "skip": "skipped",
                    "skipped": "skipped", "fail": "failed", "failed": "failed",
                    "running": "running"}


def parse_progress_line(line: str) -> dict[str, str] | None:
    """解析 pipeline 进度行 [step] status detail（只用于展示，§8）。"""
    match = _PROGRESS_RE.match((line or "").strip())
    if not match:
        return None
    name, status, detail = match.group(1).strip(), match.group(2).strip().lower(), match.group(3).strip()
    mapped = _PROGRESS_STATUS.get(status)
    if mapped is None:
        return None
    return {"name": name, "status": mapped, "detail": detail}


# ---------------------------------------------------------------------------
# 作业（内存，§3.4）
# ---------------------------------------------------------------------------
class Job:
    """一个 pipeline / batch 长作业；带 SSE 事件队列，结束后仍可重放。"""

    KEEP_LINES = 200
    KEEP_EVENTS = 5000

    def __init__(self, job_id: str, kind: str, argv: Sequence[str], cwd: str, run_dir: Path) -> None:
        self.id = job_id
        self.kind = kind
        self.status = "running"
        self.started_at = iso_now()
        self.ended_at: str | None = None
        self.argv = [str(a) for a in argv]
        self.cwd = str(cwd)
        self.run_dir = str(run_dir)
        self.returncode: int | None = None
        self.error: str | None = None
        self.steps: list[dict[str, Any]] = []
        self.log_tail: list[str] = []
        self.result: Any = None
        self.cancel_event = threading.Event()
        self.process: subprocess.Popen | None = None
        self.spec_file: Path | None = None
        self._t0 = time.monotonic()
        self._t_end: float | None = None
        self._events: list[tuple[str, dict[str, Any]]] = []
        self._cond = threading.Condition()
        self._step_index: dict[str, int] = {}

    # -- 事件 ---------------------------------------------------------------
    def _emit(self, event: str, data: dict[str, Any]) -> None:
        with self._cond:
            self._events.append((event, data))
            if len(self._events) > self.KEEP_EVENTS:
                del self._events[: len(self._events) - self.KEEP_EVENTS]
            self._cond.notify_all()

    def note(self, message: str) -> None:
        self._emit("notice", {"message": message})

    def add_line(self, stream: str, line: str) -> None:
        with self._cond:
            self.log_tail.append(line)
            if len(self.log_tail) > self.KEEP_LINES:
                del self.log_tail[: len(self.log_tail) - self.KEEP_LINES]
        self._emit("log", {"line": line, "stream": stream})
        step = parse_progress_line(line)
        if step is not None:
            self.upsert_step(step["name"], step["status"], step["detail"])

    def upsert_step(self, name: str, status: str, detail: str = "") -> None:
        with self._cond:
            index = self._step_index.get(name)
            if index is None:
                index = len(self.steps)
                self._step_index[name] = index
                self.steps.append({"index": index, "name": name, "status": status, "detail": detail})
            else:
                self.steps[index]["status"] = status
                self.steps[index]["detail"] = detail
            payload = dict(self.steps[index])
        self._emit("step", payload)

    def finish(self, status: str, *, returncode: int | None = None, error: str | None = None,
               result: Any = None) -> None:
        with self._cond:
            self.status = status
            self.returncode = returncode
            if error is not None:
                self.error = error
            if result is not None:
                self.result = result
            self.ended_at = iso_now()
            self._t_end = time.monotonic()
        self._emit("done", self.done_payload())

    @property
    def elapsed_s(self) -> float:
        end = self._t_end if self._t_end is not None else time.monotonic()
        return round(end - self._t0, 1)

    def done_payload(self) -> dict[str, Any]:
        return {"job_id": self.id, "status": self.status, "returncode": self.returncode,
                "elapsed_s": self.elapsed_s, "run_dir": self.run_dir, "error": self.error}

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "status": self.status,
                "started_at": self.started_at, "ended_at": self.ended_at,
                "elapsed_s": self.elapsed_s, "argv": list(self.argv), "cwd": self.cwd,
                "run_dir": self.run_dir, "returncode": self.returncode, "error": self.error,
                "steps": [dict(step) for step in self.steps], "log_tail": list(self.log_tail),
                "result": _clean(self.result)}

    # -- SSE 消费 -----------------------------------------------------------
    def events_since(self, cursor: int) -> tuple[list[tuple[str, dict[str, Any]]], int]:
        with self._cond:
            total = len(self._events)
            if cursor > total:
                cursor = total
            return list(self._events[cursor:]), total

    def wait_for_events(self, cursor: int, timeout: float) -> bool:
        with self._cond:
            if len(self._events) > cursor or self.status != "running":
                return True
            self._cond.wait(timeout)
            return len(self._events) > cursor or self.status != "running"


# ---------------------------------------------------------------------------
# 场景（CONTRACT §3.3）
# ---------------------------------------------------------------------------
def _stage(label: str, description: str, index: int) -> dict[str, Any]:
    return {"index": index, "label": label, "description": description}


def _scenario(sid: str, title: str, subtitle: str, icon: str, action: str,
              stages: list[tuple[str, str]], inputs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": sid, "title": title, "subtitle": subtitle, "icon": icon, "action": action,
        "stages": [_stage(label, description, index) for index, (label, description) in enumerate(stages)],
        "inputs": inputs,
    }


def _inp(name: str, kind: str, label: str, required: bool = False,
         options: list[str] | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"name": name, "kind": kind, "label": label, "required": required}
    if options:
        item["options"] = options
    return item


SCENARIOS: list[dict[str, Any]] = [
    _scenario(
        "kickoff", "一句话开管线", "把自然语言请求解析成 TaskSpec，确认后跑完整 pipeline", "sparkles",
        "route+pipeline",
        [("解析请求", "把一句话解析成 TaskSpec 与执行计划（route，不执行）"),
         ("确认计划", "展示 spec 与 plan，等用户确认后再跑"),
         ("启动管线", "POST /api/job kind=pipeline"),
         ("流式进度", "SSE /api/job/events 回放日志与步骤"),
         ("汇总产物", "展示 manifest、report 与 artifacts")],
        [_inp("text", "text", "一句话请求（要什么结构、做什么处理）", True)],
    ),
    _scenario(
        "repair", "结构修复", "补氢 / 无序占位 / 过近原子", "wrench", "op:repair",
        [("读入结构", "解析 CIF 或结构文件"),
         ("修复", "补氢 / 无序占位 / 过近原子"),
         ("写出结构", "repaired.cif"),
         ("落 op.json", "记录参数、stdout/stderr 与产物")],
        [_inp("input", "file", "结构文件", True),
         _inp("add_h", "checkbox", "补氢（默认开）"),
         _inp("disorder", "checkbox", "修复无序（默认开）"),
         _inp("contacts", "checkbox", "移除过近原子（默认开）")],
    ),
    _scenario(
        "geometry", "几何加工", "标准化 / 超胞 / 切面 / 真空 / 簇", "shapes", "op:geom",
        [("读入结构", "解析 CIF 或结构文件"),
         ("几何加工", "标准化 / 超胞 / 切面 / 真空 / 簇"),
         ("写出结构", "geom-<op>.cif"),
         ("落 op.json", "记录参数、stdout/stderr 与产物")],
        [_inp("input", "file", "结构文件", True),
         _inp("standardize", "checkbox", "标准化"),
         _inp("supercell", "text", "超胞 a,b,c（如 2,2,1）"),
         _inp("surface", "text", "切面 h,k,l（如 1,1,1）"),
         _inp("vacuum", "number", "真空层厚度（Å）"),
         _inp("thickness", "number", "slab 厚度（Å）"),
         _inp("cluster", "select", "金属簇模式", False, ["sbu", "metal_cluster", "core"])],
    ),
    _scenario(
        "export", "导出与计算输入包", "任意格式 + 计算软件输入包（template）", "package", "op:convert",
        [("读入结构", "解析 CIF 或结构文件"),
         ("渲染格式", "逐格式写出 structure.*"),
         ("生成输入包", "计算软件输入包（template，不附 POTCAR/UPF）"),
         ("落 op.json", "记录参数、stdout/stderr 与产物")],
        [_inp("input", "file", "结构文件", True),
         _inp("formats", "text", "导出格式（逗号分隔，如 vasp,cif,xyz）"),
         _inp("bundles", "text", "输入包（逗号分隔，如 vasp,qe）"),
         _inp("stem", "text", "输出主名"),
         _inp("title", "text", "标题/注释")],
    ),
    _scenario(
        "inspect", "校验 / 特征 / 拓扑证据", "validate / features / topology", "search", "op:validate|features|topology",
        [("读入结构", "解析 CIF 或结构文件"),
         ("校验", "validate 检查表（natoms/lattice/...）"),
         ("特征与拓扑", "features / topology 证据"),
         ("落 op.json", "记录参数、stdout/stderr 与产物")],
        [_inp("input", "file", "结构文件", True),
         _inp("op", "select", "操作", False, ["validate", "features", "topology"]),
         _inp("rdf", "checkbox", "features：算 RDF"),
         _inp("topology", "checkbox", "features：识别拓扑"),
         _inp("rings", "checkbox", "features：统计环"),
         _inp("net", "text", "topology：比对的目标 net")],
    ),
    _scenario(
        "library", "库与溯源 / 数据集", "runs 浏览、run 快照与 dataset 构建", "library", "runs+op:dataset",
        [("浏览 runs", "GET /api/runs"),
         ("查看 run 快照", "manifest、report、artifacts"),
         ("构建数据集", "dataset（runs -> dataset.jsonl）"),
         ("落 op.json", "记录参数、stdout/stderr 与产物")],
        [_inp("from_runs", "text", "从 runs 目录构建（默认本次 runs-dir）"),
         _inp("from_library", "text", "从 library/index.jsonl 构建"),
         _inp("label_key", "text", "标签字段（如 topology/doi/source）"),
         _inp("dedupe", "select", "去重键", False,
              ["structure_sha256", "structure_file", "formula", "reduced_formula", "label"]),
         _inp("jobs", "number", "并行度（默认 1）")],
    ),
]

SCENARIOS_BY_ID: dict[str, dict[str, Any]] = {item["id"]: item for item in SCENARIOS}

SCENARIO_DEFAULT_OP: dict[str, str] = {
    "repair": "repair",
    "geometry": "geom",
    "export": "convert",
    "inspect": "validate",
    "library": "dataset",
}

# ---------------------------------------------------------------------------
# op 白名单（CONTRACT §2 / §5）
# ---------------------------------------------------------------------------
OP_WHITELIST = ("fetch", "repair", "geom", "export", "convert", "validate", "features",
                "topology", "dataset", "sources_probe")

OP_PARAMS: dict[str, set[str]] = {
    "fetch": {"source", "topology", "name", "structure_id", "raw", "ready", "limit", "outdir"},
    "repair": {"add_h", "disorder", "contacts"},
    "geom": {"standardize", "supercell", "surface", "surface_frame", "vacuum", "thickness", "cluster"},
    "export": {"formats"},
    "convert": {"formats", "bundles", "stem", "title"},
    "validate": set(),
    "features": {"rdf", "rdf_bins", "rdf_max", "topology", "rings"},
    "topology": {"net"},
    "dataset": {"from_runs", "from_library", "label_key", "no_features", "no_structures", "dedupe", "jobs"},
    "sources_probe": set(),
}

#: 需要 input（三选一）的 op
OP_NEEDS_INPUT = {"repair", "geom", "export", "convert", "validate", "features", "topology"}

INPUT_KEYS = {"run", "name", "path", "cif_text"}


class ExecResult:
    """一次 op 执行的内部结果（dispatch 负责落 op.json）。"""

    __slots__ = ("result", "products", "argv", "stdout", "stderr", "returncode", "warnings")

    def __init__(self, result: Any, products: list[Path], argv: Sequence[str], stdout: str,
                 stderr: str, returncode: int | None, warnings: list[str] | None = None) -> None:
        self.result = result
        self.products = list(products)
        self.argv = [str(a) for a in argv]
        self.stdout = stdout or ""
        self.stderr = stderr or ""
        self.returncode = returncode
        self.warnings = list(warnings or [])


def _new_token() -> str:
    """4 位十六进制随机串（可被测试 monkeypatch 以复现同名冲突）。"""
    return secrets.token_hex(2)


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------
class Bridge:
    """工作台的全部业务逻辑；HTTP 层只做路由与序列化。"""

    def __init__(self, runs_dir: str | Path | None = None, *,
                 cifkit_root: str | Path | None = None,
                 python: str | Path | None = None,
                 allow_exec: bool = False,
                 allow_import_roots: Sequence[str | Path] | None = None,
                 max_import_bytes: int = 20 * 1024 * 1024,
                 max_upload_bytes: int = 5 * 1024 * 1024,
                 op_timeout_s: float = 300.0,
                 pipeline_timeout_s: float = 1800.0,
                 job_keep: int = 50,
                 verbose: bool = False) -> None:
        self.runs_dir = Path(runs_dir).resolve() if runs_dir else (_HERE / "state" / "runs")
        self.cifkit_root = resolve_cifkit_root(cifkit_root)
        self.python = resolve_python(python, cifkit_root=self.cifkit_root)
        self.allow_exec = bool(allow_exec)
        self.allow_import_roots = [Path(p).resolve() for p in (allow_import_roots or [])]
        self.max_import_bytes = int(max_import_bytes)
        self.max_upload_bytes = int(max_upload_bytes)
        self.op_timeout_s = float(op_timeout_s)
        self.pipeline_timeout_s = float(pipeline_timeout_s)
        self.job_keep = int(job_keep)
        self.verbose = bool(verbose)
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._job_order: list[str] = []
        self._job_seq = 0
        self._modules: tuple[Any, Any] | None = None
        self._modules_error: str | None = None
        self.runs_writable = self._probe_writable()

    # -- 基础设施 -----------------------------------------------------------
    def _probe_writable(self) -> bool:
        try:
            self.runs_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return False
        probe = self.runs_dir / f".workbench-writecheck-{os.getpid()}"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return True
        except OSError:
            return False

    def _load_cifkit(self) -> tuple[Any, Any]:
        """导入 cifkit.api / cifkit.service（进程内调用）。"""
        if self._modules is not None:
            return self._modules
        if self._modules_error is not None:
            raise WorkbenchError(E_INTERNAL, "cifkit 不可用：" + self._modules_error,
                                 "检查 --cifkit-root / --python 与 cifkit 的依赖安装")
        root = str(self.cifkit_root)
        if root not in sys.path:
            sys.path.insert(0, root)
        try:
            from cifkit import api as cifkit_api  # noqa: PLC0415
            from cifkit import service as cifkit_service  # noqa: PLC0415

            self._modules = (cifkit_api, cifkit_service)
        except Exception as exc:  # noqa: BLE001
            self._modules_error = f"{type(exc).__name__}: {exc}"
            raise WorkbenchError(E_INTERNAL, "cifkit 不可用：" + self._modules_error,
                                 "检查 --cifkit-root / --python 与 cifkit 的依赖安装")
        return self._modules

    @property
    def cifkit_version(self) -> str | None:
        try:
            import importlib.metadata as md  # noqa: PLC0415

            return md.version("cifkit")
        except Exception:  # noqa: BLE001
            try:
                import cifkit  # noqa: PLC0415

                return getattr(cifkit, "__version__", None)
            except Exception:  # noqa: BLE001
                return None

    def _inproc(self, op: str, params: dict[str, Any]) -> Any:
        """进程内 service.handle：快操作的统一入口。"""
        _, service = self._load_cifkit()
        response = service.handle({"op": op, "params": params})
        if isinstance(response, dict) and response.get("ok"):
            return response.get("data")
        error = (response or {}).get("error") or {}
        code = error.get("code", "internal-error")
        message = error.get("message") or "cifkit 调用失败"
        mapped = {"invalid-request": E_VALIDATION, "not-found": E_NOT_FOUND,
                  "unsupported-format": E_VALIDATION}.get(code, E_INTERNAL)
        raise WorkbenchError(mapped, f"cifkit 返回错误（{code}）：{message}",
                             "原始错误见 detail", detail=error)

    def python_module_argv(self, *args: str) -> list[str]:
        return [self.python, "-m", "cifkit", *[str(a) for a in args]]

    # -- 元数据（GET，无副作用） -------------------------------------------
    def cifkit_info(self, warnings: list[str]) -> dict[str, Any]:
        cache_env = os.environ.get("CIFKIT_CACHE")
        cache_path = Path(cache_env).expanduser() if cache_env else (self.cifkit_root / "_cache")
        info: dict[str, Any] = {
            "version": None, "api_version": None, "root": str(self.cifkit_root),
            "python": self.python, "runs_dir": str(self.runs_dir),
            "cache": {"ready": cache_path.is_dir(), "path": str(cache_path)},
            "ops": [],
        }
        try:
            api, service = self._load_cifkit()
            info["api_version"] = getattr(api, "API_VERSION", None)
            info["ops"] = sorted(service.OPS)
            info["version"] = self.cifkit_version
        except WorkbenchError as exc:
            warnings.append(exc.message + (f"（提示：{exc.hint}）" if exc.hint else ""))
            info["ops"] = sorted(OP_WHITELIST)
        return info

    def formats(self, warnings: list[str] | None = None) -> tuple[dict[str, str], dict[str, str]]:
        try:
            self._load_cifkit()
            from cifkit import export  # noqa: PLC0415

            return export.supported_formats(), export.supported_bundles()
        except Exception as exc:  # noqa: BLE001
            if warnings is not None:
                warnings.append(f"cifkit.export 不可用，formats/bundles 记为 {{}}：{type(exc).__name__}: {exc}")
            return {}, {}

    def sources(self, warnings: list[str] | None = None) -> list[dict[str, Any]]:
        try:
            self._load_cifkit()
            from cifkit.sources import source_status  # noqa: PLC0415

            rows = source_status()
        except Exception as exc:  # noqa: BLE001
            if warnings is not None:
                warnings.append(f"cifkit.sources 不可用，sources 记为 []：{type(exc).__name__}: {exc}")
            return []
        out: list[dict[str, Any]] = []
        for row in rows:
            name = str(row.get("name"))
            available = bool(row.get("available"))
            if available:
                status = "ready"
            elif row.get("requires_auth"):
                status = "missing-key"
            elif name == "qmof":
                status = "missing-mirror"
            else:
                status = "unknown"
            key_env = "MP_API_KEY" if name == "materials-project" else None
            out.append({
                "id": name,
                "label": row.get("description") or name,
                "needs_key": bool(row.get("requires_auth")),
                "key_env": key_env,
                "ready": available,
                "status": status,
                "topology_metadata": name == "core-mof",
                "detail": row.get("status") or "",
                "hint": row.get("auth_hint") or None,
                "probe": None,
            })
        return out

    def bootstrap(self) -> dict[str, Any]:
        warnings: list[str] = []
        cifkit = self.cifkit_info(warnings)
        formats, bundles = self.formats(warnings)
        if not formats:
            warnings.append("formats 为空：cifkit export 注册表不可读")
        if not bundles:
            warnings.append("bundles 为空：cifkit export 注册表不可读")
        return {
            "ok": True,
            "cifkit": cifkit,
            "sources": self.sources(warnings),
            "formats": formats,
            "bundles": bundles,
            "scenarios": SCENARIOS,
            "limits": {
                "max_import_bytes": self.max_import_bytes,
                "pipeline_timeout_s": self.pipeline_timeout_s,
                "op_timeout_s": self.op_timeout_s,
                "job_keep": self.job_keep,
                "max_upload_bytes": self.max_upload_bytes,
            },
            "caps": {
                "exec": self.allow_exec,
                "runs_writable": self.runs_writable,
                "network": False,
            },
            "warnings": warnings,
        }

    # -- runs 扫描（GET，无副作用） ----------------------------------------
    def resolve_run_dir(self, run_ref: str | Path) -> Path:
        raw = str(run_ref or "").strip()
        if not raw:
            raise WorkbenchError(E_BAD_REQUEST, "缺少 run id", "用 GET /api/runs 看可用 run")
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = self.runs_dir / raw
        try:
            candidate = candidate.resolve()
        except OSError as exc:
            raise WorkbenchError(E_NOT_FOUND, f"run 路径不可解析：{_display(raw)}") from exc
        roots = [self.runs_dir, *self.allow_import_roots]
        if not any(is_within(candidate, root) for root in roots):
            raise WorkbenchError(E_PATH_OUTSIDE, f"run 越界：{_display(raw)}",
                                 f"只允许 runs-dir（{self.runs_dir}）内的 run")
        if not candidate.is_dir():
            raise WorkbenchError(E_NOT_FOUND, f"run 不存在: {_display(raw)}",
                                 "先用 GET /api/runs 看可用 run")
        return candidate

    def resolve_artifact(self, run_path: Path, name: str) -> Path:
        if not isinstance(name, str) or not name.strip():
            raise WorkbenchError(E_BAD_REQUEST, "缺少 artifact name", "如 ?name=report.md")
        rel = name.replace("\\", "/")
        if rel.startswith("/") or any(part == ".." for part in rel.split("/")):
            raise WorkbenchError(E_PATH_OUTSIDE, f"artifact 名称越界：{_display(name)}",
                                 "name 必须是 run 目录内的相对路径")
        candidate = run_path / rel
        try:
            candidate = candidate.resolve()
        except OSError as exc:
            raise WorkbenchError(E_NOT_FOUND, f"artifact 不可解析：{_display(name)}") from exc
        if not is_within(candidate, run_path):
            raise WorkbenchError(E_PATH_OUTSIDE, f"artifact 名称越界：{_display(name)}",
                                 "name 必须是 run 目录内的相对路径")
        if not candidate.is_file():
            raise WorkbenchError(E_NOT_FOUND, f"artifact 不存在：{_display(name)}",
                                 "用 GET /api/run 看该 run 的 artifacts")
        if candidate.suffix.lower() not in ARTIFACT_EXTENSIONS:
            raise WorkbenchError(E_VALIDATION, f"不支持下载的扩展名：{candidate.suffix or '(无)'}",
                                 f"白名单：{sorted(ARTIFACT_EXTENSIONS)}")
        return candidate

    def _latest_run_dir(self) -> Path:
        candidates = [child for child in self.runs_dir.iterdir()
                      if child.is_dir() and not child.name.startswith(".")] if self.runs_dir.is_dir() else []
        if not candidates:
            raise WorkbenchError(E_NOT_FOUND, "还没有任何 run", "先用 POST /api/op 生成一个 run")
        return max(candidates, key=lambda child: child.stat().st_mtime)

    def run_rel(self, run_dir: Path) -> str:
        try:
            return run_dir.resolve().relative_to(self.runs_dir).as_posix()
        except ValueError:
            return run_dir.name

    def _iter_files(self, run_dir: Path) -> list[Path]:
        files: list[Path] = []
        try:
            for path in sorted(run_dir.rglob("*")):
                if path.is_file():
                    files.append(path)
        except OSError:
            pass
        return files

    def artifact_entry(self, path: Path, run_dir: Path) -> dict[str, Any]:
        try:
            rel = path.resolve().relative_to(run_dir.resolve()).as_posix()
        except ValueError:
            rel = path.name
        ext = path.suffix.lower()
        return {
            "name": rel,
            "rel": rel,
            "abs": str(path),
            "bytes": path.stat().st_size,
            "kind": artifact_kind(rel),
            "downloadable": ext in ARTIFACT_EXTENSIONS,
        }

    def run_summary(self, run_dir: Path) -> dict[str, Any]:
        run_dir = Path(run_dir).resolve()
        warnings: list[str] = []
        manifest = read_json_file(run_dir / "manifest.json")
        op = read_json_file(run_dir / "op.json")
        steps: list[Any] = []
        if isinstance(manifest, dict) and isinstance(manifest.get("steps"), list):
            steps = manifest["steps"]
        if not isinstance(manifest, dict):
            warnings.append("这个目录没有 manifest.json（单步 op run 或外部目录），steps 取 []" if op else
                            "这个目录没有 manifest.json，步骤/校验/组成不可知（绝不臆造）")
        atoms: int | None = None
        formula: str | None = None
        validation = manifest.get("validation") if isinstance(manifest, dict) else None
        metrics = validation.get("metrics") if isinstance(validation, dict) else None
        if isinstance(metrics, dict):
            if isinstance(metrics.get("natoms"), int):
                atoms = metrics["natoms"]
            if isinstance(metrics.get("formula"), str):
                formula = metrics["formula"].replace(" ", "")
        if atoms is None or formula is None:
            for step in reversed(steps):
                data = step.get("data") if isinstance(step, dict) else None
                if not isinstance(data, dict):
                    continue
                if atoms is None and isinstance(data.get("natoms"), int):
                    atoms = data["natoms"]
                if formula is None and isinstance(data.get("formula"), str):
                    formula = data["formula"].replace(" ", "")
        # CONTRACT §10.1：没有 manifest 的 op run 回落 op.json.result；
        # 值仍来自 cifkit 自己的 --json，不是工作台推算（validate 落在 metrics，features 落在 rows[0]）
        if (atoms is None or formula is None) and isinstance(op, dict):
            result = op.get("result")
            candidates: list[Any] = []
            if isinstance(result, dict):
                candidates.append(result)
                if isinstance(result.get("metrics"), dict):
                    candidates.append(result["metrics"])
                report = result.get("report")
                if isinstance(report, dict) and isinstance(report.get("metrics"), dict):
                    candidates.append(report["metrics"])
                rows = result.get("rows")
                if isinstance(rows, list) and rows and isinstance(rows[0], dict):
                    candidates.append(rows[0])
            for candidate in candidates:
                if atoms is None and isinstance(candidate.get("natoms"), int):
                    atoms = candidate["natoms"]
                if formula is None and isinstance(candidate.get("formula"), str):
                    formula = candidate["formula"].replace(" ", "")
                if atoms is not None and formula is not None:
                    break
        if atoms is None and formula is None and isinstance(op, dict):
            warnings.append("op.json.result 里没有 natoms/formula，原子数/组成不可知（不臆造）")
        source_name: str | None = None
        topology: str | None = None
        if isinstance(manifest, dict):
            source = manifest.get("source")
            if isinstance(source, dict):
                record = source.get("record") if isinstance(source.get("record"), dict) else {}
                provenance = source.get("provenance") if isinstance(source.get("provenance"), dict) else {}
                source_name = record.get("source") or provenance.get("source")
                topo = record.get("topology")
                if topo and topo != "unknown":
                    topology = topo
            spec = manifest.get("spec")
            if isinstance(spec, dict) and spec.get("topology"):
                topology = spec["topology"]
        files = self._iter_files(run_dir)
        artifacts = [self.artifact_entry(path, run_dir) for path in files]
        if isinstance(op, dict) and op.get("ok") is False:
            warnings.append("op.json 记录 ok=false（这次 op 失败），原文见 op.json.stderr")
        if isinstance(manifest, dict):
            ok_value: Any = manifest.get("ok")
            failed_step = manifest.get("failed_step")
        elif isinstance(op, dict):
            ok_value = op.get("ok")
            failed_step = None
        else:
            ok_value = None
            failed_step = None
        kind = "op" if isinstance(op, dict) else ("pipeline" if isinstance(manifest, dict) else "external")
        return {
            "id": run_dir.name,
            "rel": self.run_rel(run_dir),
            "abs": str(run_dir),
            "mtime": run_dir.stat().st_mtime,
            "kind": kind,
            "ok": ok_value,
            "failed_step": failed_step,
            "n_steps": len(steps),
            "n_artifacts": len(artifacts),
            "atoms": atoms,
            "formula": formula,
            "topology": topology,
            "source": source_name,
            "has_manifest": isinstance(manifest, dict),
            "has_report": (run_dir / "report.md").is_file(),
            "has_preview": any(entry["kind"] == "image" for entry in artifacts),
            "warnings": warnings,
        }

    def list_runs(self) -> list[dict[str, Any]]:
        if not self.runs_dir.is_dir():
            return []
        runs: list[dict[str, Any]] = []
        for child in sorted(self.runs_dir.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            try:
                runs.append(self.run_summary(child))
            except OSError:
                continue
        runs.sort(key=lambda item: item["mtime"], reverse=True)
        return runs

    def run_snapshot(self, run_id: str | Path | None = None) -> dict[str, Any]:
        run_dir = self.resolve_run_dir(run_id) if run_id else self._latest_run_dir()
        summary = self.run_summary(run_dir)
        warnings = list(summary["warnings"])
        manifest = read_json_file(run_dir / "manifest.json")
        op = read_json_file(run_dir / "op.json")
        report_md: str | None = None
        report_path = run_dir / "report.md"
        if report_path.is_file():
            try:
                text = report_path.read_text(encoding="utf-8", errors="replace")
                if len(text) > 2 * 1024 * 1024:
                    text = text[: 2 * 1024 * 1024]
                    warnings.append("report.md 超过 2MB，已截断展示")
                report_md = text
            except OSError:
                warnings.append("report.md 存在但读取失败")
        plan = read_json_file(run_dir / "plan.json")
        if plan is None:
            plan = []
            warnings.append("没有 plan.json：计划不可知，plan 取 []")
        steps = manifest.get("steps") if isinstance(manifest, dict) else []
        if not isinstance(steps, list):
            steps = []
        validation = manifest.get("validation") if isinstance(manifest, dict) else None
        artifacts = [self.artifact_entry(path, run_dir) for path in self._iter_files(run_dir)]
        snapshot = dict(summary)
        snapshot.update({
            "manifest": manifest,
            "report_md": report_md,
            "plan": _clean(plan),
            "steps": _clean(steps),
            "artifacts": artifacts,
            "validation": _clean(validation),
            "op": op,
            "warnings": warnings,
        })
        return snapshot

    def artifact_payload(self, run_id: str | Path, name: str) -> tuple[Path, dict[str, Any]]:
        run_dir = self.resolve_run_dir(run_id)
        path = self.resolve_artifact(run_dir, name)
        return path, self.artifact_entry(path, run_dir)

    # -- /api/route ---------------------------------------------------------
    def route(self, text: str) -> dict[str, Any]:
        if not isinstance(text, str) or not text.strip():
            raise WorkbenchError(E_BAD_REQUEST, "缺少 text（一句话请求）",
                                 '例如 {"text": "下载 sod 拓扑的 MOF，补氢，2x2x1 超胞"}')
        outcome = run_cli(self.python_module_argv("route", text.strip(), "--json"),
                          cwd=self.cifkit_root, timeout=self.op_timeout_s)
        payload = outcome.json()
        if outcome.returncode == 2:
            spec = payload.get("spec") if isinstance(payload, dict) else None
            plan = payload.get("plan") if isinstance(payload, dict) else None
            raise WorkbenchError(
                E_NO_STRUCTURE, "没有解析出拓扑/结构名/结构 id，无法规划",
                "在文本里补充要什么结构：拓扑（sod）、名字（ZIF-8/GaN）、id（mp-149）或本地文件路径",
                detail={"spec": spec, "plan": plan},
            )
        if outcome.returncode != 0 or not isinstance(payload, dict) or "spec" not in payload:
            tail = (outcome.stderr or outcome.stdout or "").strip().splitlines()
            raise WorkbenchError(E_INTERNAL, f"route 失败（退出码 {outcome.returncode}）",
                                 tail[-1] if tail else None)
        spec = payload.get("spec") or {}
        warnings = list(spec.get("notes") or []) + list(spec.get("assumptions") or [])
        return {"ok": True, "spec": spec, "plan": payload.get("plan") or [], "warnings": warnings}

    # -- op ----------------------------------------------------------------
    def check_op(self, op: str) -> str:
        if not isinstance(op, str) or not op.strip():
            raise WorkbenchError(E_BAD_REQUEST, "缺少 op", f"可用: {', '.join(OP_WHITELIST)}")
        op = op.strip()
        if op not in OP_WHITELIST:
            raise WorkbenchError(E_VALIDATION, f"不支持的 op: {_display(op)}",
                                 f"白名单: {', '.join(OP_WHITELIST)}")
        return op

    def check_params(self, op: str, params: Any) -> dict[str, Any]:
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise WorkbenchError(E_BAD_REQUEST, "params 必须是 JSON 对象")
        allowed = OP_PARAMS.get(op, set())
        unknown = sorted(key for key in params if key not in allowed)
        if unknown:
            raise WorkbenchError(
                E_VALIDATION, f"op={op} 的参数不在白名单：{unknown}",
                f"允许的参数: {sorted(allowed) if allowed else '(无)'}；不接受 argv",
                detail={"violations": [f"params.{key}" for key in unknown]},
            )
        return dict(params)

    def new_op_dir(self, op: str, *, stamp: str | None = None,
                   token: str | None = None) -> Path:
        """新建 op run 目录；同名已存在 -> E_CONFLICT（不静默复用）。"""
        stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
        attempts = 1 if token else 5
        last_name = ""
        for _ in range(attempts):
            current = token or _new_token()
            last_name = f"op-{stamp}-{op}-{current}"
            candidate = self.runs_dir / last_name
            if not candidate.exists():
                try:
                    candidate.mkdir(parents=True, exist_ok=False)
                except FileExistsError:
                    continue
                return candidate
        raise WorkbenchError(E_CONFLICT, f"run 目录已存在：{last_name}",
                             "目录不会被覆盖；等一秒重试或换个 op 名")

    def _resolve_input(self, input_spec: Any, run_dir: Path) -> tuple[Path | None, dict[str, Any], list[str]]:
        warnings: list[str] = []
        if input_spec is None:
            input_spec = {}
        if not isinstance(input_spec, dict):
            raise WorkbenchError(E_BAD_REQUEST, "input 必须是 JSON 对象", '如 {"path": "..."}')
        unknown = sorted(key for key in input_spec if key not in INPUT_KEYS)
        if unknown:
            raise WorkbenchError(E_VALIDATION, f"input 未知字段：{unknown}",
                                 f"允许: {sorted(INPUT_KEYS)}")
        choices = [key for key in ("cif_text", "path", "run")
                   if input_spec.get(key) is not None and input_spec.get(key) != ""]
        if not choices:
            raise WorkbenchError(E_BAD_REQUEST, "缺少 input（三选一：run+name / path / cif_text）",
                                 '例如 {"cif_text": "..."} 或 {"run": "op-...", "name": "repaired.cif"}')
        chosen = choices[0]
        if len(choices) > 1:
            warnings.append(f"input 同时给了 {choices}，按优先级采用 {chosen}")
        if chosen == "cif_text":
            text = input_spec.get("cif_text")
            if not isinstance(text, str) or not text.strip():
                raise WorkbenchError(E_BAD_REQUEST, "cif_text 必须是非空字符串")
            raw = text.encode("utf-8")
            if len(raw) > self.max_upload_bytes:
                raise WorkbenchError(E_VALIDATION, f"cif_text 超过上限 {self.max_upload_bytes} 字节")
            target = run_dir / "input.cif"
            target.write_text(text, encoding="utf-8")
            return target, {"from": "cif_text", "ref": "input.cif", "bytes": len(raw)}, warnings
        if chosen == "path":
            raw_path = input_spec.get("path")
            if not isinstance(raw_path, str) or not raw_path.strip():
                raise WorkbenchError(E_BAD_REQUEST, "input.path 必须是非空字符串")
            source = Path(raw_path).expanduser()
            if not source.is_absolute():
                source = self.cifkit_root / source
            try:
                source = source.resolve()
            except OSError as exc:
                raise WorkbenchError(E_NOT_FOUND, f"输入路径不可解析：{_display(raw_path)}") from exc
            roots = [self.runs_dir, *self.allow_import_roots]
            if not any(is_within(source, root) for root in roots):
                raise WorkbenchError(
                    E_PATH_OUTSIDE, f"输入路径越界：{_display(raw_path)}",
                    f"只允许 runs-dir（{self.runs_dir}）或 --allow-import-root 内的文件")
            if not source.is_file():
                raise WorkbenchError(E_NOT_FOUND, f"输入文件不存在：{_display(raw_path)}")
            if is_within(source, run_dir):
                return source, {"from": "path", "ref": str(source)}, warnings
            target = run_dir / "input.cif"
            shutil.copyfile(source, target)
            return target, {"from": "path", "ref": str(source), "copied": "input.cif"}, warnings
        run_ref = input_spec.get("run")
        name = input_spec.get("name")
        if not isinstance(run_ref, str) or not run_ref.strip():
            raise WorkbenchError(E_BAD_REQUEST, "input.run 需要字符串 run id")
        if not isinstance(name, str) or not name.strip():
            raise WorkbenchError(E_BAD_REQUEST, "input.run 需要 name（产物文件名）")
        run_path = self.resolve_run_dir(run_ref)
        artifact = self.resolve_artifact(run_path, name)
        ref = f"{self.run_rel(run_path)}/{name}"
        return artifact, {"from": "run", "ref": ref}, warnings

    # -- op 执行 -----------------------------------------------------------
    def _cli_exec(self, op: str, argv: Sequence[str], run_dir: Path) -> CliResult:
        outcome = run_cli(argv, cwd=self.cifkit_root, timeout=self.op_timeout_s)
        detail = {"stderr": outcome.stderr, "stdout": outcome.stdout, "returncode": outcome.returncode}
        if outcome.timed_out:
            raise WorkbenchError(E_TIMEOUT, f"op={op} 超时（>{self.op_timeout_s:.0f}s），已终止子进程",
                                 "调大 --op-timeout-s 或减小输入", detail=detail)
        if outcome.cancelled:
            raise WorkbenchError(E_CANCELLED, f"op={op} 被取消", None, detail=detail)
        if outcome.returncode != 0:
            tail = (outcome.stderr or outcome.stdout or "").strip().splitlines()
            text = tail[-1] if tail else f"退出码 {outcome.returncode}"
            code = E_VALIDATION if outcome.returncode in (2, 3) else E_INTERNAL
            raise WorkbenchError(code, f"op={op} 失败（退出码 {outcome.returncode}）：{text}",
                                 "原始 stdout/stderr 见 detail", detail=detail)
        return outcome

    def _op_validate(self, run_dir: Path, path: Path, params: dict[str, Any]) -> ExecResult:
        data = self._inproc("validate", {"path": str(path)})
        target = write_json_file(run_dir / "validation.json", data)
        return ExecResult(data, [target], [], json_dumps(data), "", 0)

    def _op_features(self, run_dir: Path, path: Path, params: dict[str, Any]) -> ExecResult:
        call = {"path": str(path),
                "rdf": bool(params.get("rdf", True)),
                "rdf_bins": int(params.get("rdf_bins", 50)),
                "rdf_max": float(params.get("rdf_max", 8.0)),
                "topology": bool(params.get("topology", False)),
                "rings": bool(params.get("rings", False))}
        row = self._inproc("features", call)
        csv_path = run_dir / "features.csv"
        self._write_single_row_csv(row, csv_path)
        result = {"ok": True, "n_inputs": 1, "n_ok": 1, "n_failed": 0,
                  "out": str(csv_path), "rows": [row]}
        return ExecResult(result, [csv_path], [], json_dumps(result), "", 0)

    def _op_convert(self, run_dir: Path, path: Path, params: dict[str, Any],
                    *, export_only: bool = False) -> ExecResult:
        formats = [str(item).strip().lower() for item in (params.get("formats") or []) if str(item).strip()]
        bundles = [] if export_only else [str(item).strip().lower()
                                          for item in (params.get("bundles") or []) if str(item).strip()]
        if not formats and not bundles:
            raise WorkbenchError(E_VALIDATION, "至少给 formats 或 bundles 之一",
                                 '如 {"formats": ["vasp", "cif"]}')
        call: dict[str, Any] = {"path": str(path), "outdir": str(run_dir),
                               "formats": formats, "bundles": bundles}
        chosen_title = params.get("stem") or params.get("title")
        if chosen_title:
            call["title"] = str(chosen_title)
        data = self._inproc("convert", call)
        products: list[Path] = []
        formats_map = data.get("formats") if isinstance(data, dict) else None
        if isinstance(formats_map, dict):
            products += [Path(value) for value in formats_map.values() if isinstance(value, str)]
        bundles_map = data.get("bundles") if isinstance(data, dict) else None
        if isinstance(bundles_map, dict):
            for info in bundles_map.values():
                if isinstance(info, dict):
                    for item in (info.get("files") or []):
                        candidate = Path(item)
                        products.append(candidate if candidate.is_absolute() else (run_dir / item))
        result = dict(data) if isinstance(data, dict) else {"ok": True}
        return ExecResult(result, products, [], json_dumps(result), "", 0)

    def _op_topology(self, run_dir: Path, path: Path, params: dict[str, Any]) -> ExecResult:
        call: dict[str, Any] = {"path": str(path)}
        if params.get("net") is not None:
            call["net"] = str(params["net"]).lower()
        data = self._inproc("topology", call)
        target = write_json_file(run_dir / "topology.json", data)
        return ExecResult(data, [target], [], json_dumps(data), "", 0)

    def _op_fetch(self, run_dir: Path, path: Path | None, params: dict[str, Any]) -> ExecResult:
        warnings: list[str] = []
        if params.get("outdir"):
            warnings.append("outdir 参数已忽略：op 的产物目录固定为本次 run 目录（CONTRACT §5.1）")
        raw = params.get("raw")
        ready = params.get("ready")
        if raw is None:
            raw = (not bool(ready)) if ready is not None else False
        call: dict[str, Any] = {"raw": bool(raw), "limit": int(params.get("limit") or 5),
                                "include_structure": True}
        for key in ("source", "topology", "name", "structure_id"):
            if params.get(key) is not None:
                call[key] = params[key]
        data = self._inproc("fetch", call)
        products: list[Path] = []
        result = dict(data) if isinstance(data, dict) else {"data": data}
        cif_text = result.pop("cif_text", None)
        if isinstance(cif_text, str) and cif_text.strip():
            target = run_dir / "fetched.cif"
            target.write_text(cif_text, encoding="utf-8")
            products.append(target)
        elif result.get("cif_text_error"):
            warnings.append(f"cifkit 未能给出 CIF 文本：{result['cif_text_error']}")
        products.append(write_json_file(run_dir / "fetch.json", result))
        return ExecResult(result, products, [], json_dumps(result), "", 0, warnings)

    def _op_repair(self, run_dir: Path, path: Path, params: dict[str, Any]) -> ExecResult:
        add_h = params.get("add_h", True)
        disorder = params.get("disorder", True)
        contacts = params.get("contacts", True)
        out = run_dir / "repaired.cif"
        argv = self.python_module_argv("repair", str(path), "-o", str(out))
        if add_h:
            argv.append("--add-h")
        if not disorder:
            argv.append("--no-disorder")
        if not contacts:
            argv.append("--no-contacts")
        argv.append("--json")
        outcome = self._cli_exec("repair", argv, run_dir)
        return ExecResult(outcome.json() or {}, [out], argv, outcome.stdout, outcome.stderr,
                          outcome.returncode)

    def _op_geom(self, run_dir: Path, path: Path, params: dict[str, Any]) -> ExecResult:
        supercell = params.get("supercell")
        surface = params.get("surface")
        cluster = params.get("cluster")
        standardize = bool(params.get("standardize", False))
        vacuum = params.get("vacuum")
        thickness = params.get("thickness")
        if (not any(value is not None for value in (supercell, surface, cluster, vacuum, thickness))
                and not standardize):
            raise WorkbenchError(E_VALIDATION, "geom 至少给一个操作参数",
                                 "standardize / supercell / surface / vacuum / thickness / cluster 至少一个")
        if surface:
            stem = "surface"
        elif supercell:
            stem = "supercell"
        elif cluster:
            stem = "cluster"
        elif standardize:
            stem = "standardize"
        else:
            stem = "vacuum"
        out = run_dir / f"geom-{stem}.cif"
        argv = self.python_module_argv("geom", str(path), "-o", str(out))
        if standardize:
            argv.append("--standardize")
        if supercell:
            argv += ["--supercell", *[str(v) for v in _as_int_triple("supercell", supercell)]]
        if surface:
            argv += ["--surface", *[str(v) for v in _as_int_triple("surface", surface)]]
        if params.get("surface_frame"):
            frame = str(params["surface_frame"]).lower()
            if frame not in ("slab", "cell"):
                raise WorkbenchError(E_VALIDATION, "surface_frame 只能是 slab 或 cell")
            argv += ["--surface-frame", frame]
        if vacuum is not None:
            argv += ["--vacuum", str(float(vacuum))]
        if thickness is not None:
            argv += ["--thickness", str(float(thickness))]
        if cluster:
            argv += ["--cluster", str(cluster)]
        argv.append("--json")
        outcome = self._cli_exec("geom", argv, run_dir)
        return ExecResult(outcome.json() or {}, [out], argv, outcome.stdout, outcome.stderr,
                          outcome.returncode)

    def _dataset_source(self, value: Any, *, default: Path, label: str) -> str | None:
        """from_runs / from_library 可以是路径字符串，也可以是布尔 true（用默认目录）。

        CONTRACT §5 只登记了字段名，前端把它读成布尔；cifkit 的 --from-runs/--from-library
        收的是**路径**。这里两种都收：true -> 默认目录，字符串 -> 该路径，其它类型 -> 422。
        路径不存在也给 422（参数错），不让它落到 500。
        """
        if value is None or value is False or value == "":
            return None
        if value is True:
            resolved = Path(default)
        elif isinstance(value, str):
            resolved = Path(value).expanduser()
            if not resolved.is_absolute():
                resolved = self.cifkit_root / resolved
        else:
            raise WorkbenchError(
                E_VALIDATION,
                f"dataset 的 {label} 必须是路径字符串，或 true（表示用默认 {default}）",
                "路径相对 --cifkit-root 解析；不接受数字/对象")
        resolved = resolved.resolve()
        if not resolved.exists():
            raise WorkbenchError(E_VALIDATION, f"dataset 的 {label} 路径不存在：{resolved}",
                                 f"给存在的目录；true 表示用默认 {default}")
        return str(resolved)

    def _op_dataset(self, run_dir: Path, path: Path | None, params: dict[str, Any]) -> ExecResult:
        from_runs = self._dataset_source(params.get("from_runs"), default=self.runs_dir,
                                         label="from_runs")
        from_library = self._dataset_source(params.get("from_library"),
                                            default=self.cifkit_root / "library",
                                            label="from_library")
        warnings: list[str] = []
        if not from_runs and not from_library:
            from_runs = str(self.runs_dir)
            warnings.append(f"未给 from_runs/from_library，默认用本次 runs-dir：{self.runs_dir}")
        outdir = run_dir / "dataset"
        argv = self.python_module_argv("dataset")
        if from_runs:
            argv += ["--from-runs", str(from_runs)]
        if from_library:
            argv += ["--from-library", str(from_library)]
        argv += ["--outdir", str(outdir)]
        if params.get("label_key"):
            argv += ["--label-key", str(params["label_key"])]
        if params.get("no_features"):
            argv.append("--no-features")
        if params.get("no_structures"):
            argv.append("--no-structures")
        if params.get("dedupe"):
            argv += ["--dedupe", str(params["dedupe"])]
        if params.get("jobs") is not None:
            argv += ["--jobs", str(int(params["jobs"]))]
        argv.append("--json")
        outcome = self._cli_exec("dataset", argv, run_dir)
        products = list(self._iter_files(outdir))
        return ExecResult(outcome.json() or {}, products, argv, outcome.stdout, outcome.stderr,
                          outcome.returncode, warnings)

    def _op_sources_probe(self, run_dir: Path, path: Path | None, params: dict[str, Any]) -> ExecResult:
        argv = self.python_module_argv("sources", "--probe", "--json")
        outcome = self._cli_exec("sources_probe", argv, run_dir)
        data = outcome.json() or []
        target = write_json_file(run_dir / "sources.json", data)
        return ExecResult(data, [target], argv, outcome.stdout, outcome.stderr, outcome.returncode)

    def _write_single_row_csv(self, row: Any, path: Path) -> Path:
        import csv  # noqa: PLC0415

        path.parent.mkdir(parents=True, exist_ok=True)
        keys: list[str] = []
        if isinstance(row, dict):
            keys = [str(key) for key in row]
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(keys)
            if isinstance(row, dict):
                writer.writerow([_clean(row.get(key)) for key in keys])
        return path

    def dispatch_op(self, op: str, params: Any = None, run: Any = None, input_spec: Any = None,
                    *, consent: Any = False) -> dict[str, Any]:
        """执行一次 op：新建 run 目录 -> 调用 cifkit -> 写 op.json -> 返回结构化结果。"""
        op = self.check_op(op)
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise WorkbenchError(E_BAD_REQUEST, "params 必须是 JSON 对象")
        params = dict(params)
        if op == "sources_probe":
            granted = bool(consent) or bool(params.pop("consent", False))
            if not granted:
                raise WorkbenchError(E_CONSENT_REQUIRED, "sources_probe 会联网探测数据源，需要显式同意",
                                     "请求带 consent: true（或 params.consent: true）")
        else:
            params.pop("consent", None)
        params = self.check_params(op, params)
        if not self.allow_exec:
            raise WorkbenchError(E_EXEC_DISABLED, "服务以只读模式运行（未开 --allow-exec），拒绝执行 op",
                                 "用 --allow-exec 启动；或只使用 GET 浏览端点")
        if not self.runs_writable:
            raise WorkbenchError(E_READONLY_RUNS, f"runs-dir 不可写：{self.runs_dir}",
                                 "改用可写的 --runs-dir，或只用 GET 浏览端点")
        effective_input = input_spec
        if run is not None and input_spec is None:
            effective_input = {"run": run}
        needs_input = op in OP_NEEDS_INPUT
        if not needs_input:
            effective_input = None

        with self._lock:
            run_dir = self.new_op_dir(op)
            started = time.time()
            started_at = iso_now()
            argv: list[str] = []
            stdout = ""
            stderr = ""
            returncode: int | None = None
            result: Any = None
            products: list[Path] = []
            warnings: list[str] = []
            input_info: dict[str, Any] = {}
            error: WorkbenchError | None = None
            try:
                if needs_input:
                    path, input_info, input_warnings = self._resolve_input(effective_input, run_dir)
                    warnings += input_warnings
                else:
                    path = None
                handlers: dict[str, Callable[[Path, Any, dict[str, Any]], ExecResult]] = {
                    "validate": self._op_validate,
                    "features": self._op_features,
                    "convert": self._op_convert,
                    "export": lambda r, p, q: self._op_convert(r, p, q, export_only=True),
                    "topology": self._op_topology,
                    "fetch": self._op_fetch,
                    "repair": self._op_repair,
                    "geom": self._op_geom,
                    "dataset": self._op_dataset,
                    "sources_probe": self._op_sources_probe,
                }
                outcome = handlers[op](run_dir, path, params)
                result = outcome.result
                products = outcome.products
                argv = outcome.argv
                stdout = outcome.stdout
                stderr = outcome.stderr
                returncode = outcome.returncode
                warnings += outcome.warnings
            except WorkbenchError as exc:
                error = exc
                detail = exc.detail if isinstance(exc.detail, dict) else {}
                stdout = str(detail.get("stdout") or "")
                stderr = str(detail.get("stderr") or "")
                returncode = detail.get("returncode")
                if exc.hint:
                    warnings.append(exc.hint)
            except (TypeError, ValueError) as exc:
                # params 值类型不对（如 supercell="a,b,c"）：这是参数错误，不是服务端 bug
                error = WorkbenchError(E_VALIDATION, f"op={op} 的参数类型不合法：{exc}",
                                       "按 CONTRACT §5 的参数表检查 params 类型")
            except Exception as exc:  # noqa: BLE001 - 任何未预期异常都是 500，不掀翻服务
                error = WorkbenchError(E_INTERNAL, f"服务端内部错误（{type(exc).__name__}）",
                                       "看服务端日志；这通常是 bug，请带复现步骤上报")
                sys.stderr.write(f"[workbench] op={op} 内部错误：{type(exc).__name__}: {exc}\n")
                sys.stderr.flush()
            elapsed_ms = round((time.time() - started) * 1000, 1)
            entries = [self.artifact_entry(item, run_dir) for item in self._iter_files(run_dir)
                       if item.name != "op.json"]
            payload: dict[str, Any] = {
                "schema": "cifkit-workbench/op/v1", "op": op, "ok": error is None,
                "argv": list(argv), "cwd": str(self.cifkit_root), "python": self.python,
                "cifkit_version": self.cifkit_version, "started_at": started_at,
                "elapsed_ms": elapsed_ms, "input": _clean(input_info), "params": _clean(params),
                "returncode": returncode, "stdout": stdout, "stderr": stderr,
                "result": _clean(result), "products": entries,
                "workbench_version": WORKBENCH_VERSION,
            }
            if error is not None:
                payload["error"] = {"code": error.code, "message": error.message}
                if error.hint:
                    payload["error"]["hint"] = error.hint
            write_json_file(run_dir / "op.json", payload)
            if error is not None:
                error.run = self.run_summary(run_dir)
                error.argv = list(argv)
                raise error
            return {
                "ok": True, "op": op, "run": self.run_summary(run_dir), "result": _clean(result),
                "argv": list(argv), "elapsed_ms": elapsed_ms, "products": entries,
                "warnings": warnings,
            }

    # -- 作业（CONTRACT §9.2） ----------------------------------------------
    JOB_PAYLOAD_KEYS: dict[str, set[str]] = {
        "pipeline": {"query", "spec", "source", "topology", "formats", "surface_frame",
                     "bundles", "ingest", "outdir"},
        "batch": {"specs", "specs_file", "jobs", "timeout", "ingest", "outdir"},
    }

    def job_outdir(self, kind: str, outdir: Any) -> Path:
        """解析 outdir：缺省 <runs-dir>/job-<ts>-<kind>-<rand>；必须在 runs-dir 内。"""
        if outdir is not None and not isinstance(outdir, str):
            raise WorkbenchError(E_VALIDATION, "payload.outdir 必须是字符串")
        if outdir is None or outdir == "":
            return self.runs_dir / f"job-{time.strftime('%Y%m%d-%H%M%S')}-{kind}-{_new_token()}"
        raw_out = outdir
        candidate = Path(raw_out).expanduser()
        if not candidate.is_absolute():
            candidate = self.runs_dir / raw_out
        try:
            candidate = candidate.resolve()
        except OSError as exc:
            raise WorkbenchError(E_BAD_REQUEST, f"outdir 不可解析：{_display(raw_out)}") from exc
        if not is_within(candidate, self.runs_dir):
            raise WorkbenchError(E_PATH_OUTSIDE, f"outdir 必须落在 runs-dir 内：{_display(raw_out)}",
                                 f"runs-dir = {self.runs_dir}")
        return candidate

    def _check_job_outdir(self, run_dir: Path) -> None:
        if run_dir.exists():
            if not run_dir.is_dir():
                raise WorkbenchError(E_CONFLICT, f"outdir 已被同名文件占用：{self.run_rel(run_dir)}")
            if any(run_dir.iterdir()):
                raise WorkbenchError(E_CONFLICT, f"outdir 已存在且非空：{self.run_rel(run_dir)}",
                                     "换一个 outdir；工作台不覆盖已有目录")
        for job in list(self._jobs.values()):
            if job.status == "running" and _norm(job.run_dir) == _norm(run_dir):
                raise WorkbenchError(E_CONFLICT, f"同一 outdir 已有 running 作业：{job.id}",
                                     "先 cancel 或等它结束")

    def _job_argv(self, kind: str, payload: dict[str, Any],
                  run_dir: Path) -> tuple[list[str], Path | None]:
        if kind == "pipeline":
            spec_file: Path | None = None
            argv = self.python_module_argv("pipeline")
            spec = payload.get("spec")
            if spec is not None:
                cleaned = {key: value for key, value in spec.items()
                           if key not in ("spec_inputs", "spec_unknown_keys")}
                # 规格文件写在 runs-dir 下的隐藏目录（系统 TEMP 在受限环境下不可写；点开头目录
                # 不会被 list_runs 当成 run），作业结束后删除
                spec_dir = self.runs_dir / "._jobspecs"
                spec_dir.mkdir(parents=True, exist_ok=True)
                spec_file = spec_dir / f"{run_dir.name}.json"
                spec_file.write_text(json.dumps(_clean(cleaned), ensure_ascii=False),
                                     encoding="utf-8")
                argv += ["--spec", str(spec_file)]
            else:
                argv.append(str(payload.get("query") or "").strip())
            argv += ["--outdir", str(run_dir)]
            if payload.get("source"):
                argv += ["--source", str(payload["source"])]
            if payload.get("topology"):
                argv += ["--topology", str(payload["topology"])]
            formats = payload.get("formats")
            if formats:
                argv += ["--format", ",".join(str(item) for item in formats)]
            if payload.get("surface_frame"):
                argv += ["--surface-frame", str(payload["surface_frame"])]
            bundles = payload.get("bundles")
            if bundles:
                argv += ["--bundle", ",".join(str(item) for item in bundles)]
            if payload.get("ingest"):
                argv.append("--ingest")
            argv.append("--json")
            return argv, spec_file
        argv = self.python_module_argv("batch", "--outdir", str(run_dir), "--json")
        if payload.get("specs_file"):
            argv += ["--specs-file", str(payload["specs_file"])]
        if payload.get("jobs") is not None:
            argv += ["--jobs", str(int(payload["jobs"]))]
        if payload.get("timeout") is not None:
            argv += ["--timeout", str(float(payload["timeout"]))]
        if payload.get("ingest"):
            argv.append("--ingest")
        for item in (payload.get("specs") or []):
            argv += ["--spec", str(item)]
        return argv, None

    def start_job(self, kind: str, payload: Any = None) -> Job:
        """启动 pipeline / batch 作业（请求体形状见 CONTRACT §9.2）。"""
        kind = str(kind or "").strip().lower()
        if kind not in ("pipeline", "batch"):
            raise WorkbenchError(E_VALIDATION, f"不支持的 kind: {_display(kind)}",
                                 "可用: pipeline | batch")
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            raise WorkbenchError(E_BAD_REQUEST, "payload 必须是 JSON 对象")
        allowed = self.JOB_PAYLOAD_KEYS[kind]
        unknown = sorted(key for key in payload if key not in allowed)
        if unknown:
            raise WorkbenchError(E_VALIDATION, f"{kind} 的 payload 未知键：{unknown}",
                                 f"允许: {sorted(allowed)}",
                                 detail={"violations": [f"payload.{key}" for key in unknown]})
        if not self.allow_exec:
            raise WorkbenchError(E_EXEC_DISABLED, "服务以只读模式运行（未开 --allow-exec），拒绝启动作业",
                                 "用 --allow-exec 启动")
        if not self.runs_writable:
            raise WorkbenchError(E_READONLY_RUNS, f"runs-dir 不可写：{self.runs_dir}",
                                 "改用可写的 --runs-dir")
        if kind == "pipeline":
            query = payload.get("query")
            spec = payload.get("spec")
            if query is not None and spec is not None:
                raise WorkbenchError(E_VALIDATION, "payload.query 与 payload.spec 互斥",
                                     "二选一：给自然语言 query，或给现成的 TaskSpec 对象")
            if not query and not spec:
                raise WorkbenchError(E_VALIDATION, "pipeline 需要 payload.query 或 payload.spec",
                                     '如 {"kind":"pipeline","payload":{"query":"..."}}')
            if spec is not None and not isinstance(spec, dict):
                raise WorkbenchError(E_VALIDATION, "payload.spec 必须是 JSON 对象")
            if query is not None and not isinstance(query, str):
                raise WorkbenchError(E_VALIDATION, "payload.query 必须是字符串")
            for key in ("source", "topology"):
                if payload.get(key) is not None and not isinstance(payload[key], str):
                    raise WorkbenchError(E_VALIDATION, f"payload.{key} 必须是字符串")
            if payload.get("surface_frame") not in (None, "slab", "cell"):
                raise WorkbenchError(E_VALIDATION, "payload.surface_frame 只能是 slab 或 cell")
            for key in ("formats", "bundles"):
                value = payload.get(key)
                if value is None:
                    continue
                if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                    raise WorkbenchError(E_VALIDATION, f"payload.{key} 必须是字符串数组")
        else:
            specs = payload.get("specs")
            specs_file = payload.get("specs_file")
            if specs is not None and specs_file is not None:
                raise WorkbenchError(E_VALIDATION, "payload.specs 与 payload.specs_file 互斥", "二选一")
            if specs is None and not specs_file:
                raise WorkbenchError(E_VALIDATION, "batch 需要 payload.specs 或 payload.specs_file",
                                     '如 {"kind":"batch","payload":{"specs":["...","..."]}}')
            if specs is not None and (not isinstance(specs, list) or not specs
                                      or not all(isinstance(item, str) and item.strip()
                                                 for item in specs)):
                raise WorkbenchError(E_VALIDATION, "payload.specs 必须是非空字符串数组")
            if specs_file is not None and not isinstance(specs_file, str):
                raise WorkbenchError(E_VALIDATION, "payload.specs_file 必须是字符串")
            if payload.get("jobs") is not None and not isinstance(payload["jobs"], int):
                raise WorkbenchError(E_VALIDATION, "payload.jobs 必须是整数")
            if payload.get("timeout") is not None and not isinstance(payload["timeout"], (int, float)):
                raise WorkbenchError(E_VALIDATION, "payload.timeout 必须是数字")
        run_dir = self.job_outdir(kind, payload.get("outdir"))
        self._check_job_outdir(run_dir)
        argv, spec_file = self._job_argv(kind, payload, run_dir)
        job_id = run_dir.name
        with self._lock:
            job = Job(job_id, kind, argv, str(self.cifkit_root), run_dir)
            job.spec_file = spec_file
            self._jobs[job_id] = job
            self._job_order.append(job_id)
            while len(self._job_order) > self.job_keep:
                old = self._job_order.pop(0)
                self._jobs.pop(old, None)
        thread = threading.Thread(target=self._run_job, args=(job,), daemon=True,
                                  name=f"cifkit-job-{job_id}")
        thread.start()
        return job

    def _run_job(self, job: Job) -> None:
        try:
            outcome = run_cli(
                job.argv, cwd=self.cifkit_root, timeout=self.pipeline_timeout_s,
                cancel_event=job.cancel_event,
                on_line=lambda line, stream: job.add_line(stream, line),
                on_start=lambda proc: setattr(job, "process", proc),
            )
        except Exception as exc:  # noqa: BLE001
            job.finish("failed", error=f"{type(exc).__name__}: {exc}")
            return
        finally:
            if job.spec_file is not None:
                try:
                    job.spec_file.unlink()
                except OSError:
                    pass
                job.spec_file = None
        if outcome.cancelled:
            raw_code = outcome.returncode
            # §9.4：returncode 取 null 或 -9/-15 之一（Windows taskkill 会给 1，此处归一为 null）
            reported = raw_code if (raw_code is None or raw_code < 0) else None
            job.finish("cancelled", returncode=reported,
                       error=f"作业被取消（子进程原始退出码 {raw_code}）")
            return
        if outcome.timed_out:
            job.finish("failed", returncode=outcome.returncode,
                       error=f"超时（>{self.pipeline_timeout_s:.0f}s），已终止子进程")
            return
        result = parse_json_document(outcome.stdout)
        if result is None and outcome.stdout.strip():
            job.note("最终 --json 解析失败，result 记为 null（原文见 log_tail）")
        if outcome.returncode == 0:
            job.finish("done", returncode=0, result=result)
            return
        tail = (outcome.stderr or outcome.stdout or "").strip().splitlines()
        job.finish("failed", returncode=outcome.returncode, result=result,
                   error=tail[-1] if tail else f"退出码 {outcome.returncode}")

    def get_job(self, job_id: str) -> Job:
        if not isinstance(job_id, str) or not job_id.strip():
            raise WorkbenchError(E_BAD_REQUEST, "缺少 job id")
        job = self._jobs.get(job_id.strip())
        if job is None:
            raise WorkbenchError(E_NOT_FOUND, f"作业不存在：{_display(job_id)}",
                                 "作业只保留在内存里（最多 50 条），重启即丢；用 GET /api/jobs 看当前列表")
        return job

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = [self._jobs[job_id] for job_id in reversed(self._job_order) if job_id in self._jobs]
        return [job.to_dict() for job in jobs]

    def cancel_job(self, job_id: str) -> Job:
        job = self.get_job(job_id)
        if job.status == "running":
            job.note("收到取消请求，正在终止子进程")
            job.cancel_event.set()
            _kill_tree(job.process)
        return job

    def job_events(self, job_id: str) -> Iterator[tuple[str, dict[str, Any]]]:
        """SSE 事件源（§4.2）：meta -> log/step/notice/error -> done；结束的作业可重放。"""
        job = self.get_job(job_id)
        yield ("meta", {"job_id": job.id, "kind": job.kind, "argv": list(job.argv)})
        cursor = 0
        while True:
            events, cursor = job.events_since(cursor)
            for event, data in events:
                yield (event, data)
            if job.status != "running":
                break
            if not job.wait_for_events(cursor, 15.0):
                yield ("keepalive", {})
        if not any(event == "done" for event, _ in job.events_since(0)[0]):
            yield ("done", job.done_payload())

    # -- /api/run（EduKit 兼容流） -----------------------------------------
    def model_string(self) -> str:
        return f"cifkit-{self.cifkit_version or '0.1.0'}"

    def stream_scenario(self, scenario: str, *, text: str | None = None, files: Any = None,
                        payload: Any = None) -> Iterator[tuple[str, dict[str, Any]]]:
        if not isinstance(scenario, str) or scenario not in SCENARIOS_BY_ID:
            raise WorkbenchError(E_BAD_REQUEST, f"未知 scenario: {_display(scenario)}",
                                 f"可用: {', '.join(SCENARIOS_BY_ID)}")
        if text is not None and not isinstance(text, str):
            raise WorkbenchError(E_VALIDATION, "text 必须是字符串")
        if files is not None and not isinstance(files, list):
            raise WorkbenchError(E_VALIDATION, "files 必须是数组")
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            raise WorkbenchError(E_VALIDATION, "payload 必须是 JSON 对象")
        stages = SCENARIOS_BY_ID[scenario]["stages"]
        run_id = f"run-{time.strftime('%Y%m%d-%H%M%S')}-{_new_token()}"
        started = time.monotonic()
        yield ("meta", {"run_id": run_id, "mode": "live", "model": self.model_string()})
        for stage in stages:
            yield ("stage", {"index": stage["index"], "label": stage["label"], "status": "running"})
        try:
            yield from self._scenario_work(scenario, text, payload)
        except WorkbenchError as exc:
            data = {"message": exc.message}
            if exc.hint:
                data["hint"] = exc.hint
            yield ("error", data)
        except Exception as exc:  # noqa: BLE001 - 页面不崩
            yield ("error", {"message": f"{type(exc).__name__}: {exc}", "hint": "看服务端日志"})
        for stage in stages:
            yield ("stage", {"index": stage["index"], "label": stage["label"], "status": "ok",
                             "detail": "完成"})
        yield ("done", {"run_id": run_id, "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
                        "mode": "live"})

    def _scenario_work(self, scenario: str, text: str | None,
                       payload: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
        if scenario == "kickoff":
            try:
                routed = self.route(text if isinstance(text, str) else str(payload.get("text") or ""))
            except WorkbenchError as exc:
                if exc.code == E_NO_STRUCTURE and isinstance(exc.detail, dict):
                    yield ("notice", {"message": "没有解析出拓扑/结构名/结构 id，请在文本里补充。"})
                    yield ("delta", {"text": _markdown_route(exc.detail.get("spec"),
                                                             exc.detail.get("plan"), [exc.message])})
                    return
                raise
            yield ("delta", {"text": _markdown_route(routed["spec"], routed["plan"], routed["warnings"])})
            yield ("notice", {"message": "规划完成（未执行）。确认后 POST /api/job "
                                         "{kind:'pipeline', text:...} 执行，进度看 /api/job/events。"})
            return
        if scenario == "library":
            dataset_params = payload.get("params")
            if not isinstance(dataset_params, dict) or not dataset_params:
                yield ("delta", {"text": _markdown_runs(self.list_runs())})
                yield ("notice", {"message": "要给 runs 构建 ML 数据集，POST /api/op {op:'dataset', params:{...}}。"})
                return
            op = "dataset"
        else:
            op = payload.get("op") or SCENARIO_DEFAULT_OP.get(scenario)
        if not isinstance(op, str):
            raise WorkbenchError(E_INTERNAL, f"场景 {scenario} 没有可执行的 op")
        allowed = {"repair": {"repair"}, "geometry": {"geom"}, "export": {"convert", "export"},
                   "inspect": {"validate", "features", "topology"}, "library": {"dataset"}}.get(scenario)
        if allowed is not None and op not in allowed:
            raise WorkbenchError(E_VALIDATION,
                                 f"场景 {scenario} 只支持 op={'/'.join(sorted(allowed))}，收到 {_display(op)}")
        params = payload.get("params")
        input_spec = payload.get("input")
        if input_spec in (None, {}) and payload:
            input_spec = {key: payload[key] for key in INPUT_KEYS if key in payload} or None
        if input_spec in (None, {}):
            yield ("notice", {"message": "缺少 input：请给 {\"input\": {\"path\": ...}} 或 "
                                         "{\"input\": {\"cif_text\": ...}}。"})
            return
        dispatched = self.dispatch_op(op, params, None, input_spec,
                                      consent=bool(payload.get("consent")))
        yield ("delta", {"text": _markdown_op(dispatched)})
        yield ("notice", {"message": f"op={dispatched['op']} 完成，run={dispatched['run']['rel']}"})


def _as_int_triple(name: str, value: Any) -> list[int]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise WorkbenchError(E_VALIDATION, f"{name} 必须是 3 个整数（如 [2, 2, 1]）")
    try:
        return [int(item) for item in value]
    except (TypeError, ValueError) as exc:
        raise WorkbenchError(E_VALIDATION, f"{name} 必须是 3 个整数（如 [2, 2, 1]）") from exc


# ---------------------------------------------------------------------------
# SSE delta 的 Markdown 组装（只用于观感，权威结果看结构化返回）
# ---------------------------------------------------------------------------
def _md_value(value: Any) -> str:
    text = json_dumps(value) if isinstance(value, (dict, list)) else str(value)
    return text if len(text) <= 200 else text[:197] + "..."


def _markdown_route(spec: Any, plan: Any, warnings: list[str]) -> str:
    lines = ["## 解析出的 TaskSpec", ""]
    if isinstance(spec, dict):
        for key in ("source", "topology", "structure_name", "structure_id", "supercell", "surface",
                    "vacuum", "output_formats", "slab_thickness", "repair_h", "repair_disorder",
                    "remove_close_contacts"):
            lines.append(f"- {key}: {_md_value(spec.get(key))}")
    else:
        lines.append("（没有 spec）")
    lines += ["", "## 执行计划", ""]
    if isinstance(plan, list):
        for index, step in enumerate(plan, 1):
            if isinstance(step, dict):
                suffix = "（可选）" if step.get("optional") else ""
                lines.append(f"{index}. {step.get('name')}：{step.get('description')}{suffix}")
    if warnings:
        lines += ["", "## 提醒", ""]
        lines += [f"- {item}" for item in warnings]
    return "\n".join(lines) + "\n"


def _markdown_runs(runs: list[dict[str, Any]]) -> str:
    lines = [f"## runs（{len(runs)} 个）", ""]
    if not runs:
        lines.append("（还没有 run）")
    for run in runs[:50]:
        lines.append(f"- **{run.get('rel')}** · kind={run.get('kind')} · ok={run.get('ok')} · "
                     f"atoms={run.get('atoms')} · formula={run.get('formula')} · "
                     f"artifacts={run.get('n_artifacts')}")
    return "\n".join(lines) + "\n"


def _markdown_op(dispatched: dict[str, Any]) -> str:
    run = dispatched.get("run") or {}
    lines = [f"## op={dispatched.get('op')} 完成", "",
             f"- run: {run.get('rel')}",
             f"- 产物: {len(dispatched.get('products') or [])} 个",
             f"- 用时: {dispatched.get('elapsed_ms')} ms"]
    if run.get("atoms") is not None:
        lines.append(f"- atoms: {run.get('atoms')}")
    if run.get("formula"):
        lines.append(f"- formula: {run.get('formula')}")
    products = dispatched.get("products") or []
    if products:
        lines += ["", "### 产物", ""]
        lines += [f"- {item.get('rel')}（{item.get('kind')}，{item.get('bytes')} B）"
                  for item in products[:30]]
    warnings = dispatched.get("warnings") or []
    if warnings:
        lines += ["", "### 提醒", ""]
        lines += [f"- {item}" for item in warnings]
    return "\n".join(lines) + "\n"


__all__ = [
    "ARTIFACT_EXTENSIONS", "Bridge", "CliResult", "E_BAD_REQUEST", "E_CANCELLED", "E_CONFLICT",
    "E_CONSENT_REQUIRED", "E_EXEC_DISABLED", "E_INTERNAL", "E_NOT_FOUND", "E_NO_STRUCTURE",
    "E_PATH_OUTSIDE", "E_READONLY_RUNS", "E_TIMEOUT", "E_VALIDATION", "ERROR_STATUS", "ExecResult",
    "INPUT_KEYS", "Job", "OP_PARAMS", "OP_WHITELIST", "SCENARIOS", "SCENARIOS_BY_ID",
    "WORKBENCH_VERSION", "WorkbenchError", "artifact_kind", "is_within", "iso_now", "json_dumps",
    "looks_like_cifkit_root", "parse_json_document", "parse_progress_line", "read_json_file",
    "resolve_cifkit_root", "resolve_python", "run_cli", "write_json_file",
]
