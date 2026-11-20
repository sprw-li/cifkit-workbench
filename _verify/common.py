r"""验收公共工具（verifier 专用；只通过真实子进程 + 真实 socket 驱动被测服务）。

设计原则（见 CONTRACT.md 与 task-4 独立性要求）：
* 不 import 被测实现（server.py / bridge.py / web/**）；唯一"读实现"的地方是
  cifkit 自己的公共 API（cifkit.api.capabilities/validate），用来做权威对照。
* runs 目录一律用系统临时目录（或环境变量 CIFWB_VERIFY_RUNS 指定），
  绝不写本会话不可写的 <cifkit 检出>\runs\。
* 缺前置（server.py / web）时记 BLOCKED，不记 PASS，也不记 FAIL。

目录约定：
  _verify/logs/    服务子进程日志 + 每个探针的 PASS/FAIL 行
  _verify/shots/   浏览器截图
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

VERIFY = Path(__file__).resolve().parent
WORKBENCH = VERIFY.parent          # 本仓库根（_verify 的父目录，与 server.py 同层）


def _resolve_repo() -> Path:
    """cifkit 检出根：CIFKIT_ROOT → 同级/同仓检出 → 已安装的 cifkit 包所在根。

    独立发布后本仓库与 cifkit 不再同仓：优先认环境变量，其次认"同级检出/同仓"，
    最后回落到已安装包；都找不到时退回旧行为（WORKBENCH.parent）。
    """
    candidates: list[Path] = []
    env_root = os.environ.get("CIFKIT_ROOT")
    if env_root:
        candidates.append(Path(env_root))
    candidates += [
        WORKBENCH.parent / "Project5-skill-cif",   # 独立仓 + 同级 cifkit 检出
        WORKBENCH.parent,                          # 同仓布局（cifkit/workbench）
    ]
    for candidate in candidates:
        if (candidate / "cifkit" / "__init__.py").is_file():
            return candidate.resolve()
    try:
        import cifkit  # noqa: PLC0415

        root = Path(cifkit.__file__).resolve().parents[1]
        if (root / "cifkit" / "__init__.py").is_file():
            return root
    except Exception:  # noqa: BLE001 - 未安装
        pass
    return WORKBENCH.parent.resolve()


REPO = _resolve_repo()             # cifkit 检出根（CIFKIT_ROOT / 同级 / 已安装包）
CONTRACT = WORKBENCH / "CONTRACT.md"
SERVER_PY = WORKBENCH / "server.py"
WEB = WORKBENCH / "web"
PLUGIN = REPO / "dsh-plugin"
SHOTS = VERIFY / "shots"

os.environ.setdefault("PYTHONUTF8", "1")


def _pick_python() -> str:
    candidates = [
        os.environ.get("CIFWB_PYTHON"),
        REPO / ".venv" / "Scripts" / "python.exe",
        REPO / ".venv" / "bin" / "python",
    ]
    for cand in candidates:
        if cand and Path(cand).is_file():
            return str(cand)
    return sys.executable


PYTHON = _pick_python()

#: 探针自己的 runs 根。默认系统临时目录；要固定位置时设 CIFWB_VERIFY_RUNS。
RUNS_ROOT = Path(os.environ.get("CIFWB_VERIFY_RUNS") or (Path(tempfile.gettempdir()) / "cifwb-verify-runs"))

LOGS = VERIFY / "logs"


def ensure_dirs() -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    SHOTS.mkdir(parents=True, exist_ok=True)


def log(text: str = "") -> None:
    print(text, flush=True)


def log_to(name: str, text: str) -> Path:
    LOGS.mkdir(parents=True, exist_ok=True)
    path = LOGS / name
    path.write_text(text.rstrip("\n") + "\n", encoding="utf-8")
    log(f"[log written] {path}")
    return path


class Report:
    """收集 PASS/FAIL/BLOCKED。BLOCKED != FAIL != PASS，exit code 只反映 FAIL。"""

    def __init__(self, title: str) -> None:
        self.title = title
        self.rows: list[tuple[str, bool, str]] = []
        self.blocked_rows: list[tuple[str, str]] = []
        self.notes: list[str] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.rows.append((name, bool(ok), detail))
        log(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}")
        return bool(ok)

    def blocked(self, name: str, detail: str = "") -> None:
        self.blocked_rows.append((name, detail))
        log(f"[BLOCKED] {name}  {detail}")

    def note(self, text: str) -> None:
        self.notes.append(text)
        log(f"[NOTE] {text}")

    def finish(self, log_name: str | None = None) -> int:
        failed = [row for row in self.rows if not row[1]]
        log(
            f"== {self.title}: {len(self.rows) - len(failed)}/{len(self.rows)} PASS"
            + (f"，{len(self.blocked_rows)} BLOCKED（未计为失败）" if self.blocked_rows else "")
            + " =="
        )
        if log_name:
            lines = [f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}" for name, ok, detail in self.rows]
            lines += [f"[BLOCKED] {name}  {detail}" for name, detail in self.blocked_rows]
            lines += [f"[NOTE] {text}" for text in self.notes]
            log_to(log_name, "\n".join(lines))
        return 1 if failed else 0


# --------------------------------------------------------------------------- #
# CONTRACT.md 真源解析（判据从契约读，不从实现读）
# --------------------------------------------------------------------------- #

def contract_text() -> str:
    """契约原文；把误写成 \\` 的转义反引号归一化回 `（契约是唯一真源，解析要稳）。"""
    raw_text = CONTRACT.read_text(encoding="utf-8")
    return raw_text.replace("\\`", "`")


def parse_endpoints() -> list[dict]:
    """解析 §2 端点总表 → [{method, paths, purpose}]。"""
    rows: list[dict] = []
    in_sec = False
    for line in contract_text().splitlines():
        s = line.strip()
        if s.startswith("## 2."):
            in_sec = True
            continue
        if in_sec and s.startswith("## 3."):
            break
        if not in_sec or not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) < 3 or cells[0] not in ("GET", "HEAD", "POST"):
            continue
        paths = [p.split("?")[0] for p in re.findall(r"\`([^\`]+)\`", cells[1])]
        rows.append({"method": cells[0], "paths": paths, "purpose": cells[2]})
    if not rows:
        raise RuntimeError("CONTRACT §2 端点表解析为空（契约结构变了？）")
    return rows


def parse_error_codes() -> dict[str, int]:
    """解析 §6 错误码表 → {E_*: http_status}。"""
    rows: dict[str, int] = {}
    for line in contract_text().splitlines():
        s = line.strip()
        if not s.startswith("| `E_"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) < 2:
            continue
        code = cells[0].strip("`")
        try:
            rows[code] = int(cells[1])
        except ValueError:
            continue
    if not rows:
        raise RuntimeError("CONTRACT §6 错误码表解析为空（契约结构变了？）")
    return rows


def parse_ops() -> list[str]:
    """解析 §5 的 op 白名单表（两列、首列反引号）→ [op]。"""
    ops: list[str] = []
    in_sec = False
    for line in contract_text().splitlines():
        s = line.strip()
        if s.startswith("## 5."):
            in_sec = True
            continue
        if in_sec and (s.startswith("### 5.1") or s.startswith("## 6.")):
            break
        if in_sec and s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if len(cells) == 2 and cells[0].startswith("`") and cells[0].endswith("`"):
                name = cells[0].strip("`")
                if re.fullmatch(r"[a-z_]+", name):
                    ops.append(name)
    if not ops:
        raise RuntimeError("CONTRACT §5 op 白名单解析为空（契约结构变了？）")
    return ops


def parse_artifact_exts() -> set[str]:
    """解析 §7.4 的 artifact 扩展名白名单 → {'.png', ...}。"""
    m = re.search(r"扩展名在\s*\`\{([^}]*)\}\`", contract_text())
    if not m:
        m = re.search(r"\{((?:\.[a-z0-9]+,\s*)+\.[a-z0-9]+)\}", contract_text())
    if not m:
        raise RuntimeError("CONTRACT §7.4 artifact 扩展名白名单解析失败（契约结构变了？）")
    return {"." + item.strip().lstrip(".") for item in m.group(1).split(",") if item.strip()}


# --------------------------------------------------------------------------- #
# cifkit 权威对照（只读 cifkit 自己的公共 API）
# --------------------------------------------------------------------------- #

def cifkit_capabilities() -> dict:
    sys.path.insert(0, str(REPO))
    from cifkit import api  # noqa: PLC0415

    return api.capabilities()


# --------------------------------------------------------------------------- #
# 服务进程
# --------------------------------------------------------------------------- #

def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_port(port: int, timeout: float = 90.0, proc: subprocess.Popen | None = None) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            return False
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.1)
    return False


def prereqs_missing() -> list[str]:
    missing = []
    if not SERVER_PY.is_file():
        missing.append(str(SERVER_PY))
    if not (WEB / "index.html").is_file():
        missing.append(str(WEB / "index.html"))
    return missing


class Server:
    """真实子进程工作台服务；上下文管理器保证退出。"""

    def __init__(
        self,
        runs_dir: Path,
        port: int | None = None,
        extra: list[str] | None = None,
        exec_: bool = True,
        env: dict | None = None,
        drop_env: list[str] | None = None,
        cwd: Path | None = None,
        ready_timeout: float = 90.0,
    ) -> None:
        ensure_dirs()
        self.port = port if port is not None else free_port()
        self.runs_dir = Path(runs_dir)
        try:
            self.runs_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass  # runs-dir 指向文件/不可写路径：交给服务自己探测 runs_writable=false（§9.5）

        self.base = f"http://127.0.0.1:{self.port}"
        self.extra_env = dict(env or {})
        self.drop_env = list(drop_env or [])
        self.cwd = Path(cwd) if cwd else WORKBENCH
        self.ready_timeout = ready_timeout
        self.argv = [
            PYTHON, str(SERVER_PY),
            "--host", "127.0.0.1",
            "--port", str(self.port),
            "--runs-dir", str(self.runs_dir),
            "--cifkit-root", str(REPO),
            "--python", PYTHON,
        ]
        if exec_:
            self.argv.append("--allow-exec")
        self.argv += list(extra or [])
        self.proc: subprocess.Popen | None = None
        self.stdout_file = open(LOGS / f"server-{self.port}.log", "w", encoding="utf-8")

    def repro_cmd(self) -> str:
        def q(part: str) -> str:
            return f'"{part}"' if " " in part else part

        return " ".join(q(part) for part in self.argv)

    def __enter__(self) -> "Server":
        child_env = {**os.environ, "PYTHONUTF8": "1", **self.extra_env}
        for key in self.drop_env:
            child_env.pop(key, None)
        self.proc = subprocess.Popen(
            self.argv, cwd=str(self.cwd),
            stdout=self.stdout_file, stderr=subprocess.STDOUT, env=child_env,
        )
        atexit.register(self.stop)
        if not wait_port(self.port, timeout=self.ready_timeout, proc=self.proc):
            tail = ""
            try:
                tail = (LOGS / f"server-{self.port}.log").read_text(encoding="utf-8", errors="replace")[-800:]
            except OSError:
                pass
            rc = self.proc.poll()
            raise RuntimeError(f"server 未就绪（exit={rc}）：{self.repro_cmd()}\n日志尾部：{tail}")
        return self

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        try:
            self.stdout_file.flush()
        except Exception:
            pass

    def __exit__(self, *exc) -> None:
        self.stop()


# --------------------------------------------------------------------------- #
# HTTP / SSE
# --------------------------------------------------------------------------- #

def http(
    base: str,
    method: str,
    path: str,
    body=None,
    timeout: float = 300.0,
    raw_body: bytes | None = None,
    headers: dict | None = None,
) -> tuple[int, dict, str]:
    """返回 (status, headers, text)；HTTPError 不抛；传输错误返回 status=0。"""
    data = raw_body
    if data is None and body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return 0, {}, f"__transport_error__: {type(exc).__name__}: {exc}"


def get(base: str, path: str, timeout: float = 300.0):
    return http(base, "GET", path, timeout=timeout)


def post(base: str, path: str, body=None, timeout: float = 300.0, raw_body: bytes | None = None):
    return http(base, "POST", path, body=body, timeout=timeout, raw_body=raw_body)


def sse(
    base: str,
    path: str,
    method: str = "GET",
    body=None,
    timeout: float = 30.0,
    read_seconds: float | None = None,
) -> dict:
    """读 SSE 流。返回 dict(status, headers, frames, raw, keepalives, timed_out, error)。"""
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base + path, data=data, method=method, headers={"Accept": "text/event-stream"})
    if data is not None:
        req.add_header("Content-Type", "application/json")
    result: dict = {"status": 0, "headers": {}, "frames": [], "raw": [], "keepalives": [], "timed_out": False, "error": None}
    deadline = time.monotonic() + (read_seconds if read_seconds is not None else timeout)
    try:
        response = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        result["status"] = exc.code
        result["headers"] = dict(exc.headers)
        result["raw"] = [exc.read().decode("utf-8", "replace")]
        return result
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    result["status"] = response.status
    result["headers"] = dict(response.headers)
    event: str | None = None
    data_lines: list[str] = []
    try:
        while time.monotonic() < deadline:
            try:
                line = response.readline()
            except (socket.timeout, TimeoutError):
                result["timed_out"] = True
                break
            if not line:
                break
            text = line.decode("utf-8", "replace").rstrip("\r\n")
            result["raw"].append(text)
            if text == "":
                if event is not None or data_lines:
                    result["frames"].append({"event": event or "message", "data": "\n".join(data_lines)})
                event, data_lines = None, []
                continue
            if text.startswith(":"):
                result["keepalives"].append(text)
            elif text.startswith("event:"):
                event = text[6:].strip()
            elif text.startswith("data:"):
                data_lines.append(text[5:].lstrip())
    finally:
        try:
            response.close()
        except Exception:
            pass
    if event is not None or data_lines:
        result["frames"].append({"event": event or "message", "data": "\n".join(data_lines)})
    return result


def js(text: str):
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        return None


def frames_by_event(stream: dict, name: str) -> list[dict]:
    return [f for f in stream.get("frames", []) if f.get("event") == name]


def json_frames(stream: dict, name: str) -> list[dict]:
    out = []
    for frame in frames_by_event(stream, name):
        parsed = js(frame.get("data", ""))
        if isinstance(parsed, dict):
            out.append(parsed)
    return out


# --------------------------------------------------------------------------- #
# runs 目录：无副作用比对 / 临时 run
# --------------------------------------------------------------------------- #

def runs_signature(root: Path) -> dict[str, tuple[int, int]]:
    """目录树 (rel → (mtime_ns, size))，用于 GET 无副作用比对。"""
    root = Path(root)
    signature: dict[str, tuple[int, int]] = {}
    if not root.exists():
        return signature
    for path in sorted(root.rglob("*")):
        try:
            stat = path.stat()
        except OSError:
            continue
        signature[str(path.relative_to(root))] = (stat.st_mtime_ns, stat.st_size)
    return signature


def runs_mtime_set(root: Path) -> set[float]:
    return {value[0] for value in runs_signature(root).values()}


def fresh(name: str) -> Path:
    """一个只属于本次验收的空 runs 目录。"""
    path = RUNS_ROOT / name
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def smallest_fixture() -> Path:
    files = sorted((REPO / "tests" / "data").glob("*.cif"), key=lambda p: p.stat().st_size)
    if not files:
        raise RuntimeError(f"找不到 CIF fixture：{REPO / 'tests' / 'data'}")
    return files[0]


# --------------------------------------------------------------------------- #
# 作业（POST /api/job 的请求体在 CONTRACT §2/§3.4 未逐字段规定 → 探针按候选顺序发现）
# --------------------------------------------------------------------------- #

def pipeline_job_bodies(cif_path: Path, outdir: Path | None = None) -> list[dict]:
    """CONTRACT §9.2 冻结的 kind=pipeline 请求体（payload.query 与 payload.spec 两种合法形态）。

    outdir=None 时不带 outdir，让服务用 §9.2 默认命名 job-<yyyyMMdd-HHmmss>-pipeline-<4随机>。
    """
    query = str(cif_path)
    spec = {"query": query, "source": "local", "structure_id": query}
    bodies = []
    for core in ({"spec": spec}, {"query": query, "source": "local"}):
        payload = dict(core)
        if outdir is not None:
            payload["outdir"] = str(outdir)
        bodies.append({"kind": "pipeline", "payload": payload})
    return bodies


def start_job(base: str, bodies: list[dict], timeout: float = 120.0) -> tuple[int, dict | None, dict | None, list[dict]]:
    """按候选顺序尝试启动作业；返回 (status, 成功响应, 采用的 body, 全部尝试记录)。"""
    attempts: list[dict] = []
    for body in bodies:
        status, _headers, text = post(base, "/api/job", body, timeout=timeout)
        parsed = js(text)
        attempts.append({"body": body, "status": status, "text": text[:300]})
        if status == 200 and isinstance(parsed, dict) and parsed.get("ok") and isinstance(parsed.get("job"), dict):
            return status, parsed, body, attempts
    return (attempts[-1]["status"] if attempts else 0), None, None, attempts


def wait_job(base: str, job_id: str, timeout: float = 600.0, interval: float = 0.5) -> dict | None:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        status, _headers, text = get(base, f"/api/job?id={job_id}", timeout=60)
        parsed = js(text)
        if status == 200 and isinstance(parsed, dict):
            last = parsed.get("job")
            if isinstance(last, dict) and last.get("status") in ("done", "failed", "cancelled"):
                return last
        time.sleep(interval)
    return last


def op_dirs(root: Path) -> set[str]:
    root = Path(root)
    if not root.is_dir():
        return set()
    return {path.name for path in root.iterdir() if path.is_dir() and path.name.startswith("op-")}
