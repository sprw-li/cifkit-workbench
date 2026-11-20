r"""验收 v05：DSH 插件 dsh-cifkit 的独立复核（不修改插件文件）。

跑实现者自带的离线 harness 并独立记录退出码与 PASS/FAIL 条数：
  node dsh-plugin/dev/verify-host.mjs
  node dsh-plugin/dev/verify-client.mjs
外加 node --check index.js / client.js（语法门槛）与 package.json 结构检查。

运行：<cifkit 检出>\.venv\Scripts\python.exe _verify\v05_plugin.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import PLUGIN, Report, js, log, log_to  # noqa: E402


def node(args: list[str], timeout: float = 300.0):
    return subprocess.run(
        ["node", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(PLUGIN), timeout=timeout,
    )


def count_marks(text: str) -> tuple[int, int]:
    passes = len(re.findall(r"(?i)\bpass(?:ed)?\b|\bok\b|\[ok\]|PASS|✔|✓", text))
    fails = len(re.findall(r"(?i)\bfail(?:ed)?\b|\[fail\]|FAIL|✗|✘", text))
    return passes, fails


def main() -> int:
    report = Report("v05 插件独立复核（dsh-cifkit）")
    lines: list[str] = []

    if not PLUGIN.is_dir():
        report.blocked("插件目录存在", str(PLUGIN))
        return report.finish("v05_plugin.log")

    pkg = PLUGIN / "package.json"
    if pkg.is_file():
        parsed = js(pkg.read_text(encoding="utf-8", errors="replace"))
        report.check(
            "package.json 可解析且含 name/version",
            isinstance(parsed, dict) and all(key in parsed for key in ("name", "version")),
            f"name={(parsed or {}).get('name')!r} version={(parsed or {}).get('version')!r}",
        )
    else:
        report.check("package.json 存在", False, str(pkg))

    for entry in ("index.js", "client.js"):
        path = PLUGIN / entry
        if not path.is_file():
            report.blocked(f"node --check {entry}", f"缺 {path}")
            continue
        proc = node(["--check", entry])
        report.check(
            f"node --check {entry} exit 0",
            proc.returncode == 0,
            f"exit={proc.returncode} stderr={proc.stderr.strip()[:200]!r}",
        )

    for script, label in (("dev/verify-host.mjs", "host"), ("dev/verify-client.mjs", "client")):
        path = PLUGIN / script
        if not path.is_file():
            report.blocked(f"node {script}", f"缺 {path}（独立复核无法进行）")
            continue
        proc = node([script], timeout=300)
        out = (proc.stdout + proc.stderr).strip()
        passes, fails = count_marks(out)
        lines.append(f"===== node {script} (exit={proc.returncode}) =====")
        lines.append(out[-4000:])
        log(f"\n$ node {script}\nexit={proc.returncode} PASS标记={passes} FAIL标记={fails}")
        log(out[-1200:])
        report.check(
            f"node {script} exit 0（PASS 标记 {passes} / FAIL 标记 {fails}）",
            proc.returncode == 0 and fails == 0,
            f"exit={proc.returncode} PASS={passes} FAIL={fails}",
        )

    if lines:
        log_to("v05_plugin_console.txt", "\n".join(lines))
    return report.finish("v05_plugin.log")


if __name__ == "__main__":
    raise SystemExit(main())
