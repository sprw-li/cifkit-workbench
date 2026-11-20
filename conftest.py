"""workbench 测试的公共装配（sys.path + marker 注册 + 默认跳过网络/慢用例 + 共享 fixture）。

pytest 从仓库根运行（rootdir=仓库根，配置见根 pyproject.toml），
本文件把本仓库根加进 sys.path，使 test_*.py 能直接 import bridge / server。

注意：本机沙箱只允许在工作区内写（TEMP 的子目录不可写），所以所有测试用的 runs-dir
都建在 state/_tests/ 下，并逐个清理。
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
import uuid
from pathlib import Path
from typing import Iterator

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


def _resolve_repo_root() -> Path:
    """cifkit 仓库根：CIFKIT_ROOT → 同仓/同级检出 → 已安装的 cifkit 包所在根。

    独立发布后本仓库（cifkit-workbench）与 cifkit 不再同仓，所以复用 bridge 的定位口径；
    定位失败时退回旧行为（HERE.parent），让用例自己报清晰的错。
    """
    try:
        import bridge as bridge_module  # HERE 已在 sys.path 上

        return Path(bridge_module.resolve_cifkit_root())
    except Exception:  # noqa: BLE001 - 交给具体用例报错
        return HERE.parent


REPO_ROOT = _resolve_repo_root()
WORK_STATE = HERE / "state" / "_tests"


def pytest_addoption(parser) -> None:
    group = parser.getgroup("workbench")
    group.addoption("--run-network", action="store_true", default=False,
                    help="运行标了 network 的用例（默认跳过：默认套件必须离线）")
    group.addoption("--run-slow", action="store_true", default=False,
                    help="运行标了 slow 的用例（默认跳过：默认套件必须 <=90s）")


def pytest_configure(config) -> None:
    config.addinivalue_line("markers", "network: 需要外网（默认跳过）")
    config.addinivalue_line("markers", "slow: 端到端慢用例（默认跳过）")
    config.addinivalue_line("markers", "e2e: 端到端集成")


def pytest_collection_modifyitems(config, items) -> None:
    if not config.getoption("--run-network"):
        marker = pytest.mark.skip(reason="需要外网：用 --run-network 打开")
        for item in items:
            if "network" in item.keywords:
                item.add_marker(marker)
    if not config.getoption("--run-slow"):
        marker = pytest.mark.skip(reason="慢用例：用 --run-slow 打开")
        for item in items:
            if "slow" in item.keywords:
                item.add_marker(marker)


# --------------------------------------------------------------------------- #
# 路径 / fixture
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def fixture_cif() -> Path:
    """cifkit 仓库自带的离线 fixture（tests/data/zif8_cr_sod.cif）。"""
    path = REPO_ROOT / "tests" / "data" / "zif8_cr_sod.cif"
    if not path.is_file():
        pytest.skip(f"缺少 fixture：{path}")
    return path


@pytest.fixture(scope="session")
def raw_fixture_cif() -> Path:
    path = REPO_ROOT / "tests" / "data" / "zif8_raw_hfree.cif"
    if not path.is_file():
        pytest.skip(f"缺少 fixture：{path}")
    return path


@pytest.fixture(scope="session")
def fixture_cif_text(fixture_cif) -> str:
    return fixture_cif.read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def work_state() -> Path:
    WORK_STATE.mkdir(parents=True, exist_ok=True)
    return WORK_STATE


@pytest.fixture()
def runs_dir(work_state) -> Iterator[Path]:
    path = work_state / f"runs-{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture()
def web_root(work_state) -> Iterator[Path]:
    path = work_state / f"web-{uuid.uuid4().hex[:8]}"
    (path / "assets").mkdir(parents=True, exist_ok=True)
    (path / "vendor").mkdir(parents=True, exist_ok=True)
    (path / "index.html").write_text("<!doctype html><html><body>workbench</body></html>",
                                     encoding="utf-8")
    (path / "assets" / "app.css").write_text("body{color:#000}", encoding="utf-8")
    (path / "secret.txt").write_text("secret", encoding="utf-8")
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture()
def bridge(runs_dir, repo_root):
    import bridge as bridge_module

    return bridge_module.Bridge(runs_dir, cifkit_root=repo_root, allow_exec=True)


class LiveServer:
    def __init__(self, httpd) -> None:
        self.httpd = httpd
        self.base = f"http://127.0.0.1:{httpd.server_address[1]}"

    @property
    def bridge(self):
        return self.httpd.bridge


@pytest.fixture()
def live_server(runs_dir, repo_root, web_root):
    import server as server_module

    httpd = server_module.create_server(runs_dir=runs_dir, host="127.0.0.1", port=0,
                                        web_root=web_root, allow_exec=True)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05},
                              daemon=True)
    thread.start()
    try:
        yield LiveServer(httpd)
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


@pytest.fixture()
def api(live_server):
    import helpers

    return helpers.Api(live_server.base)
