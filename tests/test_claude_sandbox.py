"""Модель ходит с сужённым набором инструментов и без токенов (ревью 29.08.2026).

Проверено живьём и записано в content-planner/morning.sh: `--permission-mode`
разрешает, но НЕ сужает набор — с одним белым списком модель спокойно
выполняла команды шелла. Сужает `--tools`. В промпт идут заголовки произвольных внешних RSS-лент.

Тест поведенческий: подменяем subprocess.run и смотрим, с чем реально позвали
CLI и что осталось в окружении процесса.
"""
import subprocess
import types

import report


def _перехват(monkeypatch, модуль):
    захват = {}
    monkeypatch.setattr(модуль.shutil, "which", lambda name: "/test/codex" if name == "codex" else None)

    def подделка(cmd, **kw):
        захват["cmd"] = list(cmd)
        захват["env"] = kw.get("env")
        захват["input"] = kw.get("input")
        return types.SimpleNamespace(returncode=0, stdout="ответ модели", stderr="")

    monkeypatch.setattr(модуль.subprocess, "run", подделка)
    return захват


def _промпт(tmp_path):
    p = tmp_path / "prompt.md"
    p.write_text("инструкция", encoding="utf-8")
    return str(p)


def test_набор_инструментов_сужен(monkeypatch, tmp_path):
    захват = _перехват(monkeypatch, report)
    report.claude_summary([{"title": "тема", "link": "https://x/y"}], _промпт(tmp_path))
    assert захват["cmd"][:2] == ["bash", str(report.Path(report.__file__).resolve().parent / "llm_run.sh")]
    assert захват["cmd"][захват["cmd"].index("--engine") + 1] == "codex"
    assert захват["cmd"][захват["cmd"].index("--model") + 1] == "gpt-6-sol"
    assert захват["cmd"][захват["cmd"].index("--effort") + 1] == "low"
    assert захват["cmd"][захват["cmd"].index("--access") + 1] == "none"


def test_промпт_через_stdin_а_не_argv(monkeypatch, tmp_path):
    захват = _перехват(monkeypatch, report)
    report.claude_summary([{"title": "тема", "link": "https://x/y"}], _промпт(tmp_path))
    assert "тема" in захват["input"]
    assert "тема" not in захват["cmd"]
    assert захват["cmd"][захват["cmd"].index("--timeout") + 1] == "300"


def test_токены_не_доезжают_до_модели(monkeypatch, tmp_path):
    for имя in ("TELEGRAM_TOKEN",):
        monkeypatch.setenv(имя, "живой-" + имя)
    захват = _перехват(monkeypatch, report)
    report.claude_summary([{"title": "тема", "link": "https://x/y"}], _промпт(tmp_path))
    env = захват["env"]
    if env is None:
        import os
        env = os.environ
    утекло = [имя for имя in ("TELEGRAM_TOKEN",) if имя in env]
    assert утекло == [], "в окружение модели утекли " + str(утекло)
    assert env["CODEX_BIN"] == "/test/codex"
