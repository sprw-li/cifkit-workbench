r"""定向证据：CONTRACT §4.2「作业 SSE 每 15s 发 : keepalive 注释帧」。
用长作业（spec.supercell=[3,3,3] 放大结构）保证观察窗内作业仍在跑，避免 done 提前收尾。

运行：<cifkit 检出>\.venv\Scripts\python.exe _verify\probe_sse_keepalive.py
"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import REPO, Server, fresh, get, js, pipeline_job_bodies, sse, start_job, wait_job

FIXTURE = REPO / "tests" / "data" / "zif8_cr_sod.cif"


def long_bodies():
    q = str(FIXTURE)
    spec = {"query": q, "source": "local", "structure_id": q, "supercell": [3, 3, 3],
            "output_formats": ["vasp", "cif", "xyz"]}
    return [{"kind": "pipeline", "payload": {"spec": spec}}]


with Server(fresh("sse-keepalive")) as server:
    for attempt in range(1, 4):
        st, job, body, attempts = start_job(server.base, long_bodies())
        print(f"attempt {attempt}: start_job status={st} attempts={[(a['status'], a['text'][:70]) for a in attempts]}")
        if not job:
            raise SystemExit(2)
        jid = job["job"]["id"]
        print("job id=", jid)
        t0 = time.monotonic()
        stream = sse(server.base, f"/api/job/events?id={jid}", timeout=40, read_seconds=22)
        elapsed = time.monotonic() - t0
        events = [f["event"] for f in stream["frames"]]
        print("sse status=", stream["status"], "elapsed=%.1fs" % elapsed, "timed_out=", stream["timed_out"], "error=", stream["error"])
        print("event kinds=", sorted(set(events)), "n_frames=", len(events))
        print("keepalives=", stream["keepalives"])
        print("has_done=", "done" in events)
        _, _, tj = get(server.base, f"/api/job?id={jid}")
        status_now = ((js(tj) or {}).get("job") or {}).get("status")
        print("job status at window end=", status_now)
        if "done" not in events and status_now == "running":
            print("=> 结论：作业仍在跑、SSE 打开 %.1fs、keepalive 帧 %d 个" % (elapsed, len(stream["keepalives"])))
            print("raw_tail=", json.dumps(stream["raw"][-6:], ensure_ascii=False)[:500])
            break
        wait_job(server.base, jid, timeout=600)
        print("-> 作业在窗口内结束，重试以获得更长的观察窗")
