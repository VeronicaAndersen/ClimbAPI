"""Unit tests for services/email.py: no reset tokens or addresses in output."""

import logging

from services import email as email_service

TOKEN = "live-reset-token-abc123"
ADDRESS = "alice.private@example.com"


async def test_unconfigured_mail_does_not_print_or_log_the_token(monkeypatch, capsys, caplog):
    """Regression: the full reset link, token included, was printed to stdout."""
    monkeypatch.setattr(email_service, "get_mail_config", lambda: None)
    with caplog.at_level(logging.DEBUG):
        sent = await email_service.send_password_reset_email(ADDRESS, TOKEN, "Alice")

    assert sent is False
    out = capsys.readouterr()
    logged = "\n".join(record.getMessage() for record in caplog.records)
    for text in (out.out, out.err, logged):
        assert TOKEN not in text
        assert ADDRESS not in text
    assert "Mail is not configured" in logged


async def test_send_failure_logs_only_the_exception_type(monkeypatch, capsys, caplog):
    """Regression: the exception text, which can include the address, was printed."""

    class FailingMail:
        def __init__(self, _config):
            pass

        async def send_message(self, _message):
            raise ConnectionError(f"could not deliver to {ADDRESS}")

    monkeypatch.setattr(email_service, "get_mail_config", lambda: object())
    monkeypatch.setattr(email_service, "FastMail", FailingMail)
    with caplog.at_level(logging.DEBUG):
        sent = await email_service.send_password_reset_email(ADDRESS, TOKEN)

    assert sent is False
    out = capsys.readouterr()
    logged = "\n".join(record.getMessage() for record in caplog.records)
    for text in (out.out, out.err, logged):
        assert ADDRESS not in text
        assert TOKEN not in text
    assert "Failed to send password reset email (ConnectionError)" in logged
