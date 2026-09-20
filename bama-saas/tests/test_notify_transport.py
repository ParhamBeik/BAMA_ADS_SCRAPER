import logging

import requests

from apps.core import notify


def test_telegram_failure_does_not_log_token(settings, monkeypatch, caplog):
    token = "test-secret-token"
    settings.BAMA_TELEGRAM_TOKEN = token

    def fail(url, **_kwargs):
        raise requests.ConnectionError(f"failed to connect to {url}")

    monkeypatch.setattr(notify.requests, "post", fail)

    with caplog.at_level(logging.WARNING, logger="bama.notify"):
        assert notify.send_telegram("message", "chat") is False

    assert token not in caplog.text
    assert "ConnectionError" in caplog.text
