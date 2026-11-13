"""Unit tests for services/email.py: no reset tokens or addresses in output."""

import logging
from typing import ClassVar

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


class _CapturingMail:
    sent: ClassVar[list] = []

    def __init__(self, _config):
        pass

    async def send_message(self, message):
        _CapturingMail.sent.append(message)


INVALID_ADDRESS = "anna.private@gmail"


async def test_invalid_recipient_logs_only_the_exception_type(monkeypatch, capsys, caplog):
    """
    Regression: MessageSchema was built outside the try, so an invalid stored
    address raised a ValidationError whose text contains the address.
    """
    monkeypatch.setattr(email_service, "get_mail_config", lambda: object())
    monkeypatch.setattr(email_service, "FastMail", _CapturingMail)
    with caplog.at_level(logging.DEBUG):
        sent = await email_service.send_password_reset_email(INVALID_ADDRESS, TOKEN)

    assert sent is False
    out = capsys.readouterr()
    logged = "\n".join(record.getMessage() for record in caplog.records)
    for text in (out.out, out.err, logged):
        assert INVALID_ADDRESS not in text
        assert TOKEN not in text
    assert "Failed to send password reset email (ValidationError)" in logged


async def test_invalid_recipient_in_background_task_does_not_raise(monkeypatch, caplog):
    """Starlette logs exceptions from background tasks; none may escape."""
    from starlette.background import BackgroundTasks

    monkeypatch.setattr(email_service, "get_mail_config", lambda: object())
    monkeypatch.setattr(email_service, "FastMail", _CapturingMail)
    tasks = BackgroundTasks()
    tasks.add_task(
        email_service.send_password_reset_email,
        email=INVALID_ADDRESS,
        token=TOKEN,
        firstname="Anna",
    )
    with caplog.at_level(logging.DEBUG):
        await tasks()  # would raise the ValidationError on the old code
    assert INVALID_ADDRESS not in caplog.text


async def test_sends_reset_link_to_recipient(monkeypatch):
    monkeypatch.setattr(email_service, "get_mail_config", lambda: object())
    monkeypatch.setattr(email_service, "FastMail", _CapturingMail)
    monkeypatch.setattr(email_service.email_settings, "FRONTEND_URL", "https://app.example")
    _CapturingMail.sent = []

    sent = await email_service.send_password_reset_email(ADDRESS, TOKEN, "Alice")

    assert sent is True
    (message,) = _CapturingMail.sent
    assert [r.email for r in message.recipients] == [ADDRESS]
    assert f"https://app.example/reset-password?token={TOKEN}" in message.body
