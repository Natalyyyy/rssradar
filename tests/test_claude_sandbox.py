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
    assert "--tools" in захват["cmd"], "набор инструментов не сужен"
    assert захват["cmd"][захват["cmd"].index("--tools") + 1] == ""


def test_шелл_и_подагенты_запрещены(monkeypatch, tmp_path):
    захват = _перехват(monkeypatch, report)
    report.claude_summary([{"title": "тема", "link": "https://x/y"}], _промпт(tmp_path))
    assert "--disallowedTools" in захват["cmd"]
    запреты = захват["cmd"][захват["cmd"].index("--disallowedTools") + 1]
    for имя in ("Bash", "Monitor", "Task", "WebFetch"):
        assert имя in запреты, имя


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
