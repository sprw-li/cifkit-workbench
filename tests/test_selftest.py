"""server.py --selftest 的验收（与命令行同一条代码路径）。"""

from __future__ import annotations

import server as server_module


def test_server_selftest_exits_zero(capsys):
    code = server_module.selftest(port=0)
    captured = capsys.readouterr()
    output = captured.out
    assert code == 0, output
    assert "0 项失败" in output or "项通过" in output
    assert "FAIL" not in output
