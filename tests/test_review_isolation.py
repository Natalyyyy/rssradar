import pytest
pytestmark = pytest.mark.usefixtures("report_http")

import sys
import common
import report


def test_main_collect_uses_only_stubbed_http(monkeypatch, capsys):
    """Запуск отчёта должен использовать подготовленную внешнюю границу."""
    monkeypatch.setattr(sys, "argv", ["report.py", "weekly"])
    report.main()
    assert "Тест попытался выйти в сеть" not in capsys.readouterr().err


def test_unstubbed_subprocess_is_blocked():
    import subprocess
    with pytest.raises(RuntimeError, match="внешний процесс"):
        subprocess.run(["/usr/bin/true"])
