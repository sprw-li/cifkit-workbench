r"""诊断：为 E_READONLY_RUNS 找一条在本机沙箱内可行的「不可写 runs-dir」构造方式。"""
import json, os, shutil, socket, subprocess, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import common


def bounded(args, wait=25.0):
    proc = subprocess.Popen([common.PYTHON, str(common.SERVER_PY), *args], cwd=str(common.WORKBENCH),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    port = int(args[args.index("--port") + 1])
    deadline = time.monotonic() + wait
    state = "timeout"
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            state = "exited"; break
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                state = "started"; break
        except OSError:
            time.sleep(0.2)
    return proc, port, state


base = common.VERIFY / "_roprobe"
shutil.rmtree(base, ignore_errors=True)
base.mkdir(parents=True, exist_ok=True)
file_path = base / "runs_is_a_file"
file_path.write_text("x", encoding="utf-8")
for label, rd in (("existing-file", file_path), ("under-file", file_path / "sub")):
    proc, port, state = bounded(["--host", "127.0.0.1", "--port", str(common.free_port()), "--runs-dir", str(rd),
                                 "--cifkit-root", str(common.REPO), "--python", common.PYTHON, "--allow-exec"])
    info = {"state": state, "rc": proc.returncode if state == "exited" else None}
    if state == "started":
        _, _, t = common.get(f"http://127.0.0.1:{port}", "/api/bootstrap")
        info["caps"] = ((common.js(t) or {}).get("caps") or {})
        st2, _, t2 = common.post(f"http://127.0.0.1:{port}", "/api/op",
                                 {"op": "validate", "params": {}, "input": {"cif_text": "data_x\n_cell_length_a 1\n"}})
        info["op_status"] = st2
        info["op_error"] = (common.js(t2) or {}).get("error")
        proc.terminate()
        try: proc.wait(timeout=10)
        except subprocess.TimeoutExpired: proc.kill()
    else:
        try: info["out"] = proc.stdout.read()[-300:] if proc.stdout else ""
        except Exception: pass
        if proc.poll() is None:
            proc.terminate()
    print(label, json.dumps(info, ensure_ascii=False)[:600])

d = Path(tempfile.mkdtemp(prefix="cifwb-acl-"))
(d / "runs").mkdir()
user = os.environ.get("USERNAME") or ""
r = subprocess.run(["icacls", str(d / "runs"), "/deny", f"{user}:(OI)(CI)(W)"], capture_output=True, text=True)
print("icacls-temp", r.returncode, (r.stderr or r.stdout).strip()[:200])
r2 = subprocess.run(["icacls", str(d / "runs"), "/remove:d", user], capture_output=True, text=True)
print("icacls-restore", r2.returncode)
shutil.rmtree(d, ignore_errors=True)
shutil.rmtree(base, ignore_errors=True)
