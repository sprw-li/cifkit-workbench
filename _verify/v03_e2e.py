r"""验收 v03：真跑 cifkit（离线数据）的端到端。

用 tests/data/zif8_cr_sod.cif（2208 原子）真跑 validate / geom / convert / repair
四个 op + 一条 kind=pipeline(source=local) 作业，断言：
* HTTP 返回值与 op.json / run 目录产物真的落盘（manifest.json、report.md、结构文件）
* validate 结论与 cifkit 自己的 api.validate 逐检查一致（独立对照，不读工作台实现）
* 每个动作的耗时被记录

runs-dir 在系统临时目录（本会话 <cifkit 检出>\runs\ 不可写，绝不写那里）。

运行：<cifkit 检出>\.venv\Scripts\python.exe _verify\v03_e2e.py
"""

from __future__ import annotations

import json
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    PYTHON, REPO, Report, Server, fresh, get, js, log, pipeline_job_bodies, post,
    prereqs_missing, start_job, wait_job,
)

FIXTURE = REPO / "tests" / "data" / "zif8_cr_sod.cif"
HFREE = REPO / "tests" / "data" / "zif8_raw_hfree.cif"
DATA_DIR = REPO / "tests" / "data"


def q(value: str) -> str:
    return urllib.parse.quote(str(value), safe="")


def op_call(base: str, op: str, params: dict | None = None, inp: dict | None = None, timeout: float = 600.0):
    body: dict = {"op": op, "params": params or {}}
    if inp is not None:
        body["input"] = inp
    t0 = time.monotonic()
    status, headers, text = post(base, "/api/op", body, timeout=timeout)
    wall = time.monotonic() - t0
    return status, text, js(text), wall


def run_abs_of(parsed) -> Path:
    return Path(str(((parsed or {}).get("run") or {}).get("abs") or ""))


def find_product(parsed, run_abs: Path, *keywords: str) -> Path | None:
    products = (parsed or {}).get("products") or []
    for product in products:
        name = str(product.get("name") or "")
        if any(key in name.lower() for key in keywords):
            candidate = run_abs / name
            if candidate.is_file():
                return candidate
    if run_abs.is_dir():
        for path in sorted(run_abs.iterdir()):
            if path.is_file() and any(key in path.name.lower() for key in keywords):
                return path
    return None


def natoms_of(path: Path) -> int:
    from pymatgen.core import Structure

    return len(Structure.from_file(str(path)))


def cif_natoms(path: Path) -> int:
    from cifkit import api as cifapi

    metrics = cifapi.validate(path=str(path)).get("metrics") or {}
    return int(metrics.get("natoms") or 0)


def main() -> int:
    report = Report("v03 端到端（真跑 cifkit 离线数据）")
    missing = prereqs_missing()
    if missing:
        report.blocked("前置存在（server.py + web/index.html）", f"缺 {missing}")
        return report.finish("v03_e2e.log")
    if not FIXTURE.is_file():
        report.blocked("fixture zif8_cr_sod.cif", str(FIXTURE))
        return report.finish("v03_e2e.log")
    cif = FIXTURE.read_text(encoding="utf-8", errors="replace")
    tmp_runs = fresh("v03")
    timings: list[tuple[str, float, float, str]] = []

    from cifkit import api as cifapi

    direct = cifapi.validate(path=str(FIXTURE))
    base_natoms = int((direct.get("metrics") or {}).get("natoms") or 0)
    log(f"cifkit 权威对照：api.validate({FIXTURE.name}) ok={direct.get('ok')} natoms={base_natoms} checks={len(direct.get('checks') or [])}")

    # 服务用 --allow-import-root 暴露 tests/data（测 input.path 分支），runs-dir 可写
    with Server(tmp_runs, extra=["--allow-import-root", str(DATA_DIR)]) as server:
        # ---------------- validate（input.path 走 allow-import-root） ----------------
        log("\n--- op=validate ---")
        st, text, parsed, wall = op_call(server.base, "validate", {}, {"path": str(FIXTURE)})
        timings.append(("validate", wall, float((parsed or {}).get("elapsed_ms") or 0), f"status={st}"))
        ok = st == 200 and isinstance(parsed, dict) and parsed.get("ok") is True
        result = (parsed or {}).get("result") or {}
        direct_checks = [(c.get("name"), bool(c.get("ok"))) for c in direct.get("checks") or []]
        op_checks = [(c.get("name"), bool(c.get("ok"))) for c in result.get("checks") or []]
        report.check(
            f"validate 与 cifkit.api.validate 逐检查一致（{len(direct_checks)} 项）",
            ok and direct_checks == op_checks and bool(direct_checks),
            f"一致={direct_checks == op_checks} cifkit={direct_checks} op={op_checks}",
        )
        report.check(
            "validate 结论 ok / metrics.natoms 与 cifkit 一致",
            ok and bool(result.get("ok")) == bool(direct.get("ok"))
            and int((result.get("metrics") or {}).get("natoms") or -1) == base_natoms,
            f"op.ok={result.get('ok')} cifkit.ok={direct.get('ok')} op.natoms={(result.get('metrics') or {}).get('natoms')} cifkit.natoms={base_natoms}",
        )
        v_run = run_abs_of(parsed)
        report.check("validate run 目录 + op.json 落盘", v_run.is_dir() and (v_run / "op.json").is_file(),
                     f"run={v_run} op.json={(v_run / 'op.json').is_file()}")

        # ---------------- geom（真超胞，核对原子数） ----------------
        log("\n--- op=geom（supercell=[2,1,1]） ---")
        st, text, parsed, wall = op_call(server.base, "geom", {"supercell": [2, 1, 1]}, {"cif_text": cif})
        timings.append(("geom", wall, float((parsed or {}).get("elapsed_ms") or 0), f"status={st}"))
        ok = st == 200 and isinstance(parsed, dict) and parsed.get("ok") is True
        g_run = run_abs_of(parsed)
        geom_file = find_product(parsed, g_run, "geom", ".cif")
        got = natoms_of(geom_file) if geom_file else -1
        report.check(
            f"geom 超胞产物落盘且原子数 ={base_natoms}*2={base_natoms * 2}（实测 {got}）",
            ok and geom_file is not None and got == base_natoms * 2,
            f"status={st} product={geom_file} natoms={got} run={g_run}",
        )
        report.check("geom run 目录 + op.json 落盘", g_run.is_dir() and (g_run / "op.json").is_file(),
                     f"run={g_run}")

        # ---------------- convert（多格式，核对文件真落盘） ----------------
        log("\n--- op=convert（formats=[cif,vasp,xyz]） ---")
        st, text, parsed, wall = op_call(server.base, "convert", {"formats": ["cif", "vasp", "xyz"]}, {"cif_text": cif})
        timings.append(("convert", wall, float((parsed or {}).get("elapsed_ms") or 0), f"status={st}"))
        ok = st == 200 and isinstance(parsed, dict) and parsed.get("ok") is True
        c_run = run_abs_of(parsed)
        vasp_file = find_product(parsed, c_run, ".vasp")
        xyz_file = find_product(parsed, c_run, ".xyz")
        sizes = {path.name: path.stat().st_size for path in (vasp_file, xyz_file) if path}
        report.check(
            "convert 产出 vasp/xyz 文件真落盘且非空",
            ok and vasp_file is not None and xyz_file is not None and all(size > 0 for size in sizes.values()),
            f"status={st} files={sizes} run={c_run}",
        )
        vasp_atoms = natoms_of(vasp_file) if vasp_file else -1
        report.check(
            f"convert 的 vasp 结构可被 pymatgen 读回且原子数={base_natoms}",
            vasp_atoms == base_natoms,
            f"vasp={vasp_file} natoms={vasp_atoms}",
        )
        report.check("convert run 目录 + op.json 落盘", c_run.is_dir() and (c_run / "op.json").is_file(), f"run={c_run}")

        # ---------------- repair（zif8_cr_sod 按任务要求 + hfree 对照） ----------------
        log("\n--- op=repair ---")
        st, text, parsed, wall = op_call(server.base, "repair", {"add_h": True, "disorder": False, "contacts": False}, {"cif_text": cif})
        timings.append(("repair(cr_sod)", wall, float((parsed or {}).get("elapsed_ms") or 0), f"status={st}"))
        ok = st == 200 and isinstance(parsed, dict) and parsed.get("ok") is True
        r_run = run_abs_of(parsed)
        repaired = find_product(parsed, r_run, "repaired")
        report.check(
            "repair(zif8_cr_sod) 产物 repaired.cif 落盘",
            ok and repaired is not None and repaired.stat().st_size > 0,
            f"status={st} product={repaired} run={r_run} body={text[:160]!r}",
        )
        report.check("repair run 目录 + op.json 落盘", r_run.is_dir() and (r_run / "op.json").is_file(), f"run={r_run}")

        if HFREE.is_file():
            hfree_text = HFREE.read_text(encoding="utf-8", errors="replace")
            before_atoms = cif_natoms(HFREE)
            st2, text2, parsed2, wall2 = op_call(server.base, "repair", {"add_h": True}, {"cif_text": hfree_text})
            timings.append(("repair(hfree)", wall2, float((parsed2 or {}).get("elapsed_ms") or 0), f"status={st2}"))
            r2_run = run_abs_of(parsed2)
            repaired2 = find_product(parsed2, r2_run, "repaired")
            after_atoms = natoms_of(repaired2) if repaired2 else -1
            report.check(
                f"repair(缺氢 {HFREE.name}) 补氢后原子数增加（{before_atoms} → {after_atoms}）",
                st2 == 200 and repaired2 is not None and after_atoms > before_atoms,
                f"status={st2} before={before_atoms} after={after_atoms} product={repaired2}",
            )

        # ---------------- pipeline（source=local，长作业） ----------------
        log("\n--- kind=pipeline（source=local） ---")
        # §9.2：outdir 缺省 → 服务用 job-<yyyyMMdd-HHmmss>-pipeline-<4随机> 且必须落在 runs-dir 内
        st_p, job_p, used_body, attempts = start_job(server.base, pipeline_job_bodies(FIXTURE))
        body_note = f"采用body={used_body} 尝试={[(a['status'], a['text'][:50]) for a in attempts]}"
        report.check("pipeline 作业启动 → 200 {ok,job}", st_p == 200 and isinstance(job_p, dict), body_note[:400])
        if not job_p:
            report.blocked("pipeline 端到端", "作业未启动；CONTRACT §2/§3.4 未规定 /api/job 请求体")
            return report.finish("v03_e2e.log")

        job = job_p.get("job") or {}
        job_id = str(job.get("id") or "")
        t0 = time.monotonic()
        final = wait_job(server.base, job_id, timeout=900)
        wall = time.monotonic() - t0
        timings.append(("pipeline", wall, float((final or {}).get("elapsed_s") or 0) * 1000, f"status={(final or {}).get('status')}"))
        report.check(
            "pipeline 作业 status='done' 且 returncode=0",
            isinstance(final, dict) and final.get("status") == "done" and final.get("returncode") == 0,
            f"status={(final or {}).get('status')} rc={(final or {}).get('returncode')} elapsed_s={(final or {}).get('elapsed_s')} argv={(final or {}).get('argv')}",
        )
        run_dir = Path(str((final or {}).get("run_dir") or ""))
        report.check(
            "§9.2 outdir 缺省 → job-<yyyyMMdd-HHmmss>-pipeline-<4随机> 且落在 runs-dir 内",
            bool(run_dir.name) and run_dir.name.startswith("job-") and "-pipeline-" in run_dir.name
            and len(run_dir.name.rsplit("-", 1)[-1]) == 4
            and run_dir.resolve().parent == tmp_runs.resolve(),
            f"run_dir={run_dir} parent={run_dir.parent}",
        )
        manifest = run_dir / "manifest.json"
        report_md = run_dir / "report.md"
        report.check(
            "pipeline 产物 manifest.json + report.md 真落盘",
            manifest.is_file() and report_md.is_file() and report_md.stat().st_size > 0,
            f"run_dir={run_dir} manifest={manifest.is_file()} report.md={report_md.is_file()} report_bytes={report_md.stat().st_size if report_md.is_file() else 0}",
        )
        manifest_json = js(manifest.read_text(encoding="utf-8", errors="replace")) if manifest.is_file() else None
        steps = (manifest_json or {}).get("steps") if isinstance(manifest_json, dict) else None
        report.check(
            "manifest.json 可解析且含 steps（非空）",
            isinstance(manifest_json, dict) and isinstance(steps, list) and bool(steps),
            f"keys={sorted(manifest_json)[:12] if isinstance(manifest_json, dict) else None} n_steps={len(steps) if isinstance(steps, list) else None}",
        )
        log_tail = (final or {}).get("log_tail") or []
        report.check(
            "§3.4 作业 log_tail 保留子进程原文（非空）",
            isinstance(log_tail, list) and len(log_tail) > 0,
            f"n_lines={len(log_tail)} tail={log_tail[-2:] if log_tail else []}",
        )

        # pipeline run 的 RunSnapshot
        if run_dir.is_dir():
            rel = run_dir.name
            st_s, _, text_s = get(server.base, f"/api/run?id={q(rel)}")
            snap = ((js(text_s) or {}).get("run")) if js(text_s) else None
            report.check(
                "§3.2 pipeline RunSnapshot：manifest/report_md/steps 非空",
                st_s == 200 and isinstance(snap, dict) and isinstance(snap.get("manifest"), dict)
                and bool(snap.get("report_md")) and bool(snap.get("steps")),
                f"status={st_s} manifest={'dict' if isinstance((snap or {}).get('manifest'), dict) else type((snap or {}).get('manifest')).__name__} "
                f"report_md_len={len(str((snap or {}).get('report_md') or ''))} steps={len((snap or {}).get('steps') or [])}",
            )
        else:
            report.blocked("pipeline RunSnapshot", f"run_dir 不存在：{run_dir}")

    # ---------------- 耗时记录 ----------------
    log("\n--- 耗时（wall s / 服务端 elapsed_ms） ---")
    for name, wall, server_ms, note in timings:
        log(f"  {name:<16} wall={wall:7.2f}s  server_elapsed_ms={server_ms:9.1f}  {note}")
    report.check(
        f"耗时记录完整（{len(timings)} 个动作）",
        all(wall >= 0 and server_ms >= 0 for _, wall, server_ms, _ in timings),
        "；".join(f"{n}:wall={w:.2f}s/ms={m:.0f}" for n, w, m, _ in timings),
    )

    return report.finish("v03_e2e.log")


if __name__ == "__main__":
    raise SystemExit(main())
