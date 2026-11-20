r"""验收 v02：对抗 / 边界 / 并发 / 取消 / SSE 异常路径。

判据来自 CONTRACT.md §1（方法/路径）、§2、§4.2、§5、§6、§7（不变量 2/6/7 等）。
不读实现；每个 FAIL 都带最小复现命令原文（由 common.Server.repro_cmd() + 本文件行号给出）。

运行：<cifkit 检出>\.venv\Scripts\python.exe _verify\v02_adversarial.py
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    REPO, Report, Server, fresh, get, http, js, log, op_dirs, pipeline_job_bodies,
    post, prereqs_missing, runs_signature, sse, start_job, wait_job,
)

FIXTURE = REPO / "tests" / "data" / "zif8_cr_sod.cif"
INJECT_MARKER = "CIFWB_INJECTED"


def q(value: str) -> str:
    return urllib.parse.quote(str(value), safe="")


def op_call(base: str, op: str, params: dict | None = None, inp: dict | None = None, timeout: float = 300.0, extra: dict | None = None):
    body: dict = {"op": op, "params": params or {}}
    if inp is not None:
        body["input"] = inp
    if extra:
        body.update(extra)
    status, headers, text = post(base, "/api/op", body, timeout=timeout)
    return status, headers, text, js(text)


def err_of(parsed) -> str:
    if not isinstance(parsed, dict):
        return ""
    return str(parsed.get("error") or parsed.get("code") or "")


def main() -> int:
    report = Report("v02 对抗/边界/并发/取消/SSE")
    missing = prereqs_missing()
    if missing:
        report.blocked("前置存在（server.py + web/index.html）", f"缺 {missing}")
        return report.finish("v02_adversarial.log")
    cif = FIXTURE.read_text(encoding="utf-8", errors="replace")
    tmp_runs = fresh("v02")

    with Server(tmp_runs) as server:
        st, _, btext = get(server.base, "/api/bootstrap")
        boot = js(btext) or {}
        limits = boot.get("limits") or {}
        max_upload = int(limits.get("max_upload_bytes") or 5 * 1024 * 1024)
        log(f"contract limits.max_upload_bytes={max_upload}")

        # ---------------- 请求体对抗 ----------------
        log("\n--- 请求体对抗 ---")
        st, _, text = post(server.base, "/api/op", raw_body=b'{"op": "validate", "params": ')
        report.check(
            "坏 JSON → 400 E_BAD_REQUEST（500 即失败）",
            st == 400 and err_of(js(text)) == "E_BAD_REQUEST",
            f"status={st} body={text[:200]!r}",
        )
        for literal in ("NaN", "Infinity", "-Infinity"):
            raw = ('{"op":"geom","params":{"vacuum":' + literal + '}}').encode()
            st, _, text = post(server.base, "/api/op", raw_body=raw)
            report.check(
                f"非有限数字字面量 {literal} → 拒绝（非 200/500，RFC 8259 不容）",
                st in (400, 422) and st != 500,
                f"status={st} body={text[:200]!r}",
            )
        st, _, text, parsed = op_call(server.base, "validate", {"bogus_key": 1}, {"cif_text": cif})
        report.check(
            "params 额外字段 → 422 E_VALIDATION（§5 白名单）",
            st == 422 and err_of(parsed) == "E_VALIDATION",
            f"status={st} error={err_of(parsed)!r} body={text[:200]!r}",
        )
        st, _, text, parsed = op_call(
            server.base, "validate", {}, {"cif_text": cif},
            extra={"argv": ["-c", f"print('{INJECT_MARKER}')"]},
        )
        report.check(
            "顶层 argv 注入 → 不执行外部命令（无注入标记 / 非 500）",
            st != 500 and INJECT_MARKER not in text and (st != 200 or isinstance((parsed or {}).get("result"), dict)),
            f"status={st} 含标记={INJECT_MARKER in text} body={text[:200]!r}",
        )

        # ---------------- 路径逃逸 ----------------
        log("\n--- 路径逃逸 ---")
        outside = [
            ("input.path 上跳", {"op": "validate", "params": {}, "input": {"path": str(REPO / ".." / ".." / "Windows" / "win.ini")}}),
            ("input.path 绝对系统路径", {"op": "validate", "params": {}, "input": {"path": r"C:\Windows\win.ini"}}),
            ("input.path UNC", {"op": "validate", "params": {}, "input": {"path": r"\\localhost\C$\Windows\win.ini"}}),
            ("input.run 上跳", {"op": "validate", "params": {}, "input": {"run": "../../Windows", "name": "win.ini"}}),
        ]
        bad = []
        for name, body in outside:
            st, _, text = post(server.base, "/api/op", body)
            err = err_of(js(text))
            if not (st == 400 and err == "E_PATH_OUTSIDE"):
                bad.append((name, st, err, text[:120]))
        report.check(
            f"input 路径逃逸 {len(outside)} 例全部 400 E_PATH_OUTSIDE",
            not bad,
            f"异常={bad or '无'}",
        )

        art_names = [
            "../../CONTRACT.md",
            "..\\..\\CONTRACT.md",
            "..%2F..%2FCONTRACT.md",
            "C:\\Windows\\win.ini",
            "..\\..\\..\\..\\Windows\\win.ini",
        ]
        bad = []
        for name in art_names:
            st, _, text = get(server.base, f"/api/artifact?run=x&name={q(name)}")
            if st == 200:
                bad.append((name, st, text[:100]))
        report.check(
            f"artifact name 逃逸 {len(art_names)} 例全部非 200（不穿越）",
            not bad,
            f"异常={bad or '无'}",
        )

        # ---------------- 超大上传 ----------------
        log("\n--- 超大上传 ---")
        oversize = "A" * (max_upload + 4096)
        t0 = time.monotonic()
        st, _, text, parsed = op_call(server.base, "validate", {}, {"cif_text": oversize}, timeout=180)
        report.check(
            f"cif_text 超 max_upload_bytes({max_upload}) → 4xx（非 200），耗时 {time.monotonic() - t0:.1f}s",
            st in (400, 413, 422) and st != 200,
            f"status={st} error={err_of(parsed)!r} body={text[:160]!r}",
        )
        huge = b'{"op":"validate","params":{},"x":"' + b"B" * (16 * 1024 * 1024) + b'"}'
        t0 = time.monotonic()
        try:
            st, _, text = post(server.base, "/api/op", raw_body=huge, timeout=180)
            huge_note = f"status={st} elapsed={time.monotonic() - t0:.1f}s body={text[:120]!r}"
        except Exception as exc:  # noqa: BLE001
            st = 0
            huge_note = f"request raised {type(exc).__name__}: {exc}"
        st_alive, _, alive_text = get(server.base, "/api/bootstrap")
        report.check(
            "16MB 请求体 → 4xx 且服务不崩（随后 /api/bootstrap 仍 200）",
            st in (400, 413, 422) and st_alive == 200,
            f"{huge_note}；存活 bootstrap status={st_alive}",
        )

        # ---------------- 符号链接逃逸（若可创建） ----------------
        log("\n--- 符号链接逃逸 ---")
        link = tmp_runs / "verify_link.cif"
        try:
            if link.is_symlink():
                link.unlink()
            os.symlink(FIXTURE, link)
            st, _, text, parsed = op_call(server.base, "validate", {}, {"path": str(link)})
            report.check(
                "符号链接指向 runs-dir 外 → 400 E_PATH_OUTSIDE（resolve 后判定）",
                st == 400 and err_of(parsed) == "E_PATH_OUTSIDE",
                f"status={st} error={err_of(parsed)!r} body={text[:160]!r}",
            )
        except (OSError, NotImplementedError) as exc:
            report.blocked("符号链接逃逸", f"本机无法创建 symlink：{type(exc).__name__}: {exc}")
        finally:
            try:
                if link.is_symlink():
                    link.unlink()
            except OSError:
                pass

        # ---------------- 并发 op：目录不覆盖 ----------------
        log("\n--- 并发 op x6 ---")
        before = op_dirs(tmp_runs)
        results: list[tuple[int, str, str]] = []
        lock = threading.Lock()

        def worker() -> None:
            st, _, text, parsed = op_call(server.base, "validate", {}, {"cif_text": cif}, timeout=300)
            rel = ""
            if isinstance(parsed, dict) and isinstance(parsed.get("run"), dict):
                rel = str(parsed["run"].get("rel") or "")
            with lock:
                results.append((st, rel, text[:120]))

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        after = op_dirs(tmp_runs)
        created = sorted(after - before)
        statuses = sorted({item[0] for item in results})
        rels = [item[1] for item in results]
        report.check(
            "并发 6x validate：全部 200、互不 500，run 目录两两不同且数量=6",
            statuses == [200] and len(set(rels)) == len(rels) == 6 and len(created) == 6,
            f"状态={statuses} 唯一rel={len(set(rels))} 新建目录={len(created)} 样例={created[:3]}",
        )

        # ---------------- 长作业：重复提交 409 / SSE / 取消 ----------------
        log("\n--- 长作业 / SSE / 取消 ---")
        outdir_a = tmp_runs / "job_out_a"
        bodies = pipeline_job_bodies(FIXTURE, outdir_a)
        st_a, job_a, used_body, attempts = start_job(server.base, bodies)
        report.check(
            "POST /api/job kind=pipeline(source=local) → 200 {ok,job}",
            st_a == 200 and isinstance(job_a, dict),
            (f"status={st_a} 采用body={used_body} 尝试={[(a['status'], a['text'][:60]) for a in attempts]}"[:400]),
        )
        if job_a is None:
            report.blocked("作业相关全部对抗项", "pipeline 作业无法启动；请求体在 CONTRACT §2/§3.4 未规定 —— 需实现者补文档")
            return report.finish("v02_adversarial.log")

        job = job_a.get("job") or {}
        job_id = str(job.get("id") or "")
        job_keys = ["id", "kind", "status", "started_at", "ended_at", "elapsed_s", "argv", "cwd",
                    "run_dir", "returncode", "error", "steps", "log_tail", "result"]
        absent = [key for key in job_keys if key not in job]
        report.check(
            "§3.4 Job 键齐全 + kind='pipeline' + argv 是数组",
            not absent and job.get("kind") == "pipeline" and isinstance(job.get("argv"), list),
            f"缺={absent or '无'} kind={job.get('kind')!r} argv={job.get('argv')}",
        )

        # ---------------- §9.2 冻结的 /api/job 形状 ----------------
        log("\n--- §9.2 job 形状/围栏 ---")
        st, _, t = post(server.base, "/api/job", {"kind": "pipeline"})
        report.check("§9.2 pipeline 缺 payload → 422 E_VALIDATION",
                     st == 422 and err_of(js(t)) == "E_VALIDATION", f"status={st} body={t[:160]!r}")
        st, _, t = post(server.base, "/api/job", {"kind": "pipeline", "payload": {"query": str(FIXTURE), "spec": {"query": str(FIXTURE)}, "outdir": str(tmp_runs / "job_x")}})
        report.check("§9.2 payload.query 与 payload.spec 互斥 → 422 E_VALIDATION",
                     st == 422 and err_of(js(t)) == "E_VALIDATION", f"status={st} body={t[:160]!r}")
        st, _, t = post(server.base, "/api/job", {"kind": "pipeline", "payload": {"query": str(FIXTURE), "bogus_key": 1, "outdir": str(tmp_runs / "job_y")}})
        report.check("§9.2 payload 未知键 → 422 E_VALIDATION（不静默忽略）",
                     st == 422 and err_of(js(t)) == "E_VALIDATION", f"status={st} body={t[:160]!r}")
        escape_outdir = REPO / ".." / "cifwb-escaped-outdir"
        st, _, t = post(server.base, "/api/job", {"kind": "pipeline", "payload": {"query": str(FIXTURE), "outdir": str(escape_outdir)}})
        report.check("§9.2 outdir 逃逸 runs-dir → 400 E_PATH_OUTSIDE",
                     st == 400 and err_of(js(t)) == "E_PATH_OUTSIDE", f"status={st} body={t[:160]!r}")
        nonempty = tmp_runs / "pre_existing_nonempty"
        nonempty.mkdir(parents=True, exist_ok=True)
        (nonempty / "keep.txt").write_text("x", encoding="utf-8")
        st, _, t = post(server.base, "/api/job", {"kind": "pipeline", "payload": {"query": str(FIXTURE), "outdir": str(nonempty)}})
        report.check("§9.2 outdir 已存在且非空 → 409 E_CONFLICT",
                     st == 409 and err_of(js(t)) == "E_CONFLICT", f"status={st} body={t[:160]!r}")

        st_dup, _, dup_text = post(server.base, "/api/job", used_body)
        dup = js(dup_text)
        report.check(
            "§6/不变量 2：同名作业在跑 → 第二个 409 E_CONFLICT（不静默并发写同一目录）",
            st_dup == 409 and err_of(dup) == "E_CONFLICT",
            f"status={st_dup} error={err_of(dup)!r} body={dup_text[:200]!r}",
        )

        stream = sse(server.base, f"/api/job/events?id={q(job_id)}", timeout=30, read_seconds=18)
        events = [frame["event"] for frame in stream.get("frames", [])]
        report.check(
            "§4.2 运行中作业 SSE：meta 帧 + 至少 1 条 log/step",
            stream.get("status") == 200 and "meta" in events and ({"log", "step"} & set(events)),
            f"status={stream.get('status')} events={events[:10]} keepalive={len(stream.get('keepalives', []))}",
        )
        if "done" in events:
            report.note("§4.2 运行中作业 SSE 的 keepalive：首个作业在观察窗内已结束，改由下方专用长作业验证")
        # 专用长作业（spec.supercell=[3,3,3]，空闲 >15s）验证 §4.2 keepalive 注释帧
        # 先等第一个作业结束，避免两个 pipeline 互相争用影响观察窗
        wait_job(server.base, job_id, timeout=600)
        long_spec = {"query": str(FIXTURE), "source": "local", "structure_id": str(FIXTURE),
                     "supercell": [3, 3, 3], "output_formats": ["vasp", "cif", "xyz"]}
        st_l, job_l, body_l, attempts_l = start_job(server.base, [{"kind": "pipeline", "payload": {"spec": long_spec}}])
        if st_l != 200 or not job_l:
            report.blocked("§4.2 SSE keepalive", f"专用长作业未启动：status={st_l} {attempts_l[-1]['text'][:120]}")
        else:
            jid_l = str((job_l.get("job") or {}).get("id") or "")
            _st_r, _, _tj_r = get(server.base, f"/api/job?id={q(jid_l)}")
            running = ((js(_tj_r) or {}).get("job") or {})
            t_l0 = time.monotonic()
            lstream = sse(server.base, f"/api/job/events?id={q(jid_l)}", timeout=60, read_seconds=45)
            l_elapsed = time.monotonic() - t_l0
            l_events = [f["event"] for f in lstream.get("frames", [])]
            l_keep = lstream.get("keepalives", [])
            l_done = "done" in l_events
            if len(l_keep) >= 1:
                report.check(
                    "§4.2 作业 SSE 在空闲 >15s 时发送 keepalive 注释帧（专用长作业，45s 窗口）",
                    True,
                    f"keepalive={l_keep[:3]} 共 {len(l_keep)} 次；窗口={l_elapsed:.1f}s done_in_window={l_done} events={sorted(set(l_events))}",
                )
            elif not l_done and l_elapsed >= 20:
                report.check(
                    "§4.2 作业 SSE 在空闲 >15s 时发送 keepalive 注释帧（专用长作业，45s 窗口）",
                    False,
                    f"keepalive=0 共 0 次；窗口={l_elapsed:.1f}s 作业仍在跑（running={running.get('status')!r}）",
                )
            else:
                report.blocked(
                    "§4.2 SSE keepalive",
                    f"45s 窗口内 keepalive=0 且作业在 {l_elapsed:.1f}s 内结束："
                    f"status={running.get('status')!r} error={running.get('error')!r} done_in_window={l_done}",
                )
            wait_job(server.base, jid_l, timeout=600)

        final = wait_job(server.base, job_id, timeout=600)
        report.check(
            "长作业最终 status='done' 且 returncode=0",
            isinstance(final, dict) and final.get("status") == "done" and final.get("returncode") == 0,
            (f"status={final.get('status') if isinstance(final, dict) else None} rc={final.get('returncode') if isinstance(final, dict) else None} elapsed={final.get('elapsed_s') if isinstance(final, dict) else None}"),
        )
        run_dir = Path(str((final or {}).get("run_dir") or ""))
        report.check(
            "§3.4 作业产物目录存在（run_dir 落盘）",
            bool(str(run_dir)) and run_dir.is_dir(),
            f"run_dir={run_dir}",
        )

        replay = sse(server.base, f"/api/job/events?id={q(job_id)}", timeout=30, read_seconds=20)
        replay_events = [frame["event"] for frame in replay.get("frames", [])]
        report.check(
            "§4.2 已结束作业 SSE 可重放：log 帧 + done",
            replay.get("status") == 200 and "done" in replay_events and "log" in replay_events,
            f"status={replay.get('status')} events={replay_events[:12]}",
        )

        # 取消语义
        outdir_b = tmp_runs / "job_out_b"
        bodies_b = pipeline_job_bodies(FIXTURE, outdir_b)
        st_b, job_b, used_body_b, attempts_b = start_job(server.base, bodies_b)
        if st_b != 200 or not job_b:
            report.blocked("作业取消（不变量 6）", f"第二个作业未启动：status={st_b} {attempts_b[-1]['text'][:150]}")
        else:
            job_id_b = str((job_b.get("job") or {}).get("id") or "")
            run_dir_b = Path(str((job_b.get("job") or {}).get("run_dir") or ""))
            deadline_db = time.monotonic() + 20
            while time.monotonic() < deadline_db:
                stj, _, tj = get(server.base, f"/api/job?id={job_id_b}")
                cur = ((js(tj) or {}).get("job") or {})
                if cur.get("run_dir"):
                    run_dir_b = Path(str(cur["run_dir"]))
                if str(run_dir_b) not in ("", ".") and run_dir_b.is_dir():
                    break
                time.sleep(0.5)
            time.sleep(1.5)
            t_cancel = time.monotonic()
            st_c, _, ctext = post(server.base, "/api/job/cancel", {"id": job_id_b})
            cancel_parsed = js(ctext)
            report.check(
                "§2 POST /api/job/cancel → 200 {ok,job}",
                st_c == 200 and isinstance(cancel_parsed, dict) and cancel_parsed.get("ok") is True,
                f"status={st_c} body={ctext[:200]!r}",
            )
            cancelled = wait_job(server.base, job_id_b, timeout=15, interval=0.25)
            elapsed = time.monotonic() - t_cancel
            report.check(
                "不变量 6：cancel 后 5s 内 status='cancelled'",
                isinstance(cancelled, dict) and cancelled.get("status") == "cancelled" and elapsed <= 5.0,
                f"status={cancelled.get('status') if isinstance(cancelled, dict) else None} 用时={elapsed:.2f}s",
            )
            rc_cancel = (cancelled or {}).get("returncode") if isinstance(cancelled, dict) else "__no_job__"
            report.check(
                "§9.4 cancel 后 returncode 为 null 或 -9/-15",
                rc_cancel in (None, -9, -15),
                f"returncode={rc_cancel!r}",
            )
            st_c2, _, ctext2 = post(server.base, "/api/job/cancel", {"id": job_id_b})
            parsed_c2 = js(ctext2)
            if st_c2 == 409 and err_of(parsed_c2) == "E_CANCELLED":
                report.check("§6 E_CANCELLED 409：重复 cancel 已取消作业", True,
                             f"status={st_c2} error='E_CANCELLED' body={ctext2[:160]!r}")
            else:
                report.note(f"§6 E_CANCELLED 409 未构造：重复 cancel 返回 status={st_c2} error={err_of(parsed_c2)!r}")
            if str(run_dir_b) not in ("", ".") and run_dir_b.is_dir():
                report.check("不变量 6：取消后产物目录保留（不删）", True, f"run_dir={run_dir_b} 仍存在")
            else:
                report.blocked("不变量 6 产物目录保留", f"cancel 时未见 run_dir：{run_dir_b}（可能尚未创建）")

        # ---------------- SSE / 方法异常路径 ----------------
        log("\n--- SSE / 方法异常路径 ---")
        unknown = sse(server.base, "/api/job/events?id=__no_such_job__", timeout=12, read_seconds=8)
        unknown_events = [frame["event"] for frame in unknown.get("frames", [])]
        report.check(
            "未知 job 的 SSE → 404 E_NOT_FOUND 或 error/done 帧（不悬挂）",
            (unknown.get("status") == 404) or ("error" in unknown_events) or ("done" in unknown_events),
            f"status={unknown.get('status')} events={unknown_events[:8]} timed_out={unknown.get('timed_out')} raw={unknown.get('raw', [])[:3]}",
        )

        st, _, text = get(server.base, "/api/op")
        report.check(
            "GET /api/op（写端点用 GET）→ 非 200（404/405，不产生 run）",
            st in (404, 405),
            f"status={st} body={text[:120]!r}",
        )
        st, _, text = post(server.base, "/api/job/cancel", {})
        report.check(
            "POST /api/job/cancel 缺 id → 400 E_BAD_REQUEST（非 500）",
            st == 400 and err_of(js(text)) == "E_BAD_REQUEST",
            f"status={st} body={text[:160]!r}",
        )
        st, hd, text = http(server.base, "HEAD", "/api/bootstrap")
        report.check(
            "§1 HEAD /api/bootstrap → 200 且无 body",
            st == 200 and text == "",
            f"status={st} body_len={len(text)}",
        )

        # ---------------- 不变量 7：失败也写 op.json ----------------
        log("\n--- 不变量 7：失败留痕 ---")
        before_dirs = op_dirs(tmp_runs)
        bad_cif = "data_verify_fail\n_cell_length_a 1\n_cell_length_b 1\n_cell_length_c 1\nloop_\n_atom_site_fract_x\n0.0\n"
        st, _, text, parsed = op_call(server.base, "validate", {}, {"cif_text": bad_cif})
        after_dirs = op_dirs(tmp_runs)
        new_dirs = sorted(after_dirs - before_dirs)
        fail_run = (parsed or {}).get("run") if isinstance(parsed, dict) else None
        fail_dir = Path(str((fail_run or {}).get("abs") or ""))
        if not (str(fail_dir) and fail_dir.is_dir()):
            fail_dir = tmp_runs / new_dirs[0] if new_dirs else fail_dir
        op_json_path = fail_dir / "op.json"
        op_json_found = js(op_json_path.read_text(encoding="utf-8", errors="replace")) if op_json_path.is_file() else None
        report.check(
            "§9.6/不变量 7：失败的 op 返回 run 字段 + 该目录落 op.json（ok:false + stderr 原文）",
            st != 200 and isinstance(fail_run, dict) and op_json_found is not None
            and op_json_found.get("ok") is False
            and (str(op_json_found.get("stderr") or "") != ""
                 or str(op_json_found.get("stdout") or "") != ""
                 or bool((op_json_found.get("error") or {}).get("message"))),
            f"status={st} error={err_of(parsed)!r} run字段={fail_run is not None} dir={fail_dir.name} 新目录={new_dirs} op.json.ok={op_json_found and op_json_found.get('ok')}",
        )

    return report.finish("v02_adversarial.log")


if __name__ == "__main__":
    raise SystemExit(main())
