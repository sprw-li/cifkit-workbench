r"""定向证据：SSE keepalive 到达时刻（判断 §4.2「每 15s」是否被活动抑制 / 锚点在哪）。

运行：<cifkit 检出>\.venv\Scripts\python.exe _verify\probe_sse_timing.py
"""
import json, sys, time, urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import REPO, Server, fresh, pipeline_job_bodies, start_job

FIXTURE = REPO / "tests" / "data" / "zif8_cr_sod.cif"


def timed_stream(base, path, seconds=45.0):
    req = urllib.request.Request(base + path, headers={"Accept": "text/event-stream"})
    resp = urllib.request.urlopen(req, timeout=45)
    t0 = time.monotonic()
    rows = []
    while time.monotonic() - t0 < seconds:
        try:
            line = resp.readline()
        except Exception as exc:  # noqa: BLE001
            rows.append((round(time.monotonic() - t0, 1), "ERR " + type(exc).__name__))
            break
        if not line:
            break
        s = line.decode("utf-8", "replace").rstrip()
        if s:
            rows.append((round(time.monotonic() - t0, 1), s[:100]))
    resp.close()
    return rows


with Server(fresh("sse-timing")) as server:
    q = str(FIXTURE)
    long_spec = {"query": q, "source": "local", "structure_id": q, "supercell": [3, 3, 3],
                 "output_formats": ["vasp", "cif", "xyz"]}
    st, job, body, attempts = start_job(server.base, [{"kind": "pipeline", "payload": {"spec": long_spec}}])
    jid = job["job"]["id"]
    print("job", jid, "start status", st)
    rows = timed_stream(server.base, f"/api/job/events?id={jid}", seconds=34)
    keepalives = [r for r in rows if r[1].startswith(":")]
    events = [(t, s) for t, s in rows if s.startswith("event:")]
    print("total lines", len(rows), "keepalive_count", len(keepalives))
    print("keepalive_times", json.dumps(keepalives))
    print("event_times", json.dumps(events[:24]))
    print("last_rows", json.dumps(rows[-6:], ensure_ascii=False))
