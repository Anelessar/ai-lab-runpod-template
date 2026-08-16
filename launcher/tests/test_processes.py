import time
from pathlib import Path

import pytest

from app.processes import ProcessManager


def test_only_one_standalone_process_runs_at_a_time(tmp_path: Path) -> None:
    manager = ProcessManager(tmp_path / "logs")
    first = manager.start("first", "sleep 30", cwd=tmp_path, env={}, port=7860)
    try:
        assert first.pid > 0
        with pytest.raises(RuntimeError, match="один standalone"):
            manager.start("second", "sleep 30", cwd=tmp_path, env={}, port=8080)
    finally:
        manager.stop("first")
        time.sleep(0.01)
    assert manager.current() is None
