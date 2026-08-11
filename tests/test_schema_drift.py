"""Проверка: рассинхрон схемы отдаётся внятным сообщением, обычная ошибка — прежним."""

from telegram_mcp.runtime import log_and_format_error
from telethon.errors.common import TypeNotFoundError


def test_schema_drift_message_is_actionable():
    msg = log_and_format_error("list_chats", TypeNotFoundError(0xD58A08C6, b"\x00"))
    assert "разошлась" in msg
    assert "patch_telethon_layer" in msg
    assert "НЕ «нет такого пользователя/чата»" in msg


def test_ordinary_error_keeps_old_format():
    msg = log_and_format_error("get_chat", ValueError("boom"))
    assert "code:" in msg
    assert "разошлась" not in msg
