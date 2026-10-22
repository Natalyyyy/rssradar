"""Тесты не читают рабочие настройки и не выходят в сеть."""
import pytest
import subprocess
import shutil
import requests
import common


@pytest.fixture(autouse=True)
def isolated_io(monkeypatch, tmp_path):
    monkeypatch.setattr(common, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(common, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(common, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(common, "DATA_DIR", tmp_path)

    def blocked(*args, **kwargs):
        raise AssertionError("Тест попытался выйти в сеть")

    monkeypatch.setattr(requests.sessions.Session, "request", blocked)

    def blocked_process(*args, **kwargs):
        raise RuntimeError("Тест попытался запустить внешний процесс")

    monkeypatch.setattr(subprocess, "run", blocked_process)


@pytest.fixture
def report_http(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    class Response:
        status_code = 200
        headers = {}
        content = b'<rss version="2.0"><channel><title>Test</title></channel></rss>'
        text = '<a href="/news/test"><h2>Test article</h2></a>'

        def raise_for_status(self):
            pass

    def get(url, **kwargs):
        response = Response()
        for section in ("engineering", "research"):
            if url.endswith("/" + section):
                response.text = response.text.replace("/news/", "/" + section + "/")
        return response

    monkeypatch.setattr(requests, "get", get)
