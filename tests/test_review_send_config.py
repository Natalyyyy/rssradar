import sys
import pytest
import common
import report


@pytest.mark.parametrize("settings", ["", "TELEGRAM_TOKEN=test\n", "TELEGRAM_CHAT_ID=123\n"])
def test_send_requires_both_credentials_before_collection(monkeypatch, settings, capsys):
    common.ENV_PATH.write_text(settings)
    monkeypatch.setattr(sys, "argv", ["report.py", "pulse", "--send"])
    with pytest.raises(SystemExit) as exc:
        report.main()
    assert exc.value.code == 1
    assert "TELEGRAM" in capsys.readouterr().err
    assert not common.DB_PATH.exists()


def test_preview_does_not_require_credentials(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["report.py", "pulse", "--no-fetch"])
    report.main()
    assert "Тихо" in capsys.readouterr().out
