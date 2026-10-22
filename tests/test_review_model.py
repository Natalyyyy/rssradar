import subprocess
from types import SimpleNamespace
import pytest
import report


@pytest.mark.parametrize("kind", ["summary", "clusters"])
@pytest.mark.parametrize("outcome", ["ok", "empty", "exit", "timeout", "oserror"])
def test_model_invocation_is_isolated(monkeypatch, tmp_path, kind, outcome):
    monkeypatch.setattr(report.shutil, "which", lambda name: "/test/claude")
    monkeypatch.setenv("TELEGRAM_TOKEN", "secret")
    prompt = tmp_path / "prompt.md"
    prompt.write_text("instructions")
    calls = []
    def run(cmd, **kw):
        calls.append(cmd)
        assert cmd[cmd.index("--tools") + 1] == ""
        assert cmd[cmd.index("--model") + 1] == "claude-opus-5"
        assert "TELEGRAM_TOKEN" not in kw["env"]
        assert "untrusted title" in kw["input"]
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(cmd, 300)
        if outcome == "oserror":
            raise OSError("unavailable")
        return SimpleNamespace(returncode=1 if outcome == "exit" else 0,
                               stdout='' if outcome == "empty" else '[{"theme":"X","items":[]}]')
    monkeypatch.setattr(report.subprocess, "run", run)
    fn = getattr(report, "claude_" + kind)
    for _ in range(2):
        result = fn([{"title": "untrusted title", "link": "https://x"}], prompt)
        assert (result is not None) == (outcome == "ok")
    assert len(calls) == 2
    assert fn([], prompt) is None


def test_clusters_missing_cli_and_invalid_json(monkeypatch, tmp_path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("instructions")
    entries = [{"title":"x", "link":"https://x"}]
    monkeypatch.setattr(report.shutil, "which", lambda name: None)
    assert report.claude_clusters(entries, prompt) is None
    monkeypatch.setattr(report.shutil, "which", lambda name: "/test/claude")
    monkeypatch.setattr(report.subprocess, "run", lambda *a, **k:
                        SimpleNamespace(returncode=0, stdout="[broken]"))
    assert report.claude_clusters(entries, prompt) is None
