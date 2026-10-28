import json
from datetime import datetime, timezone

import pytest
from telegram import Update
from telegram.request import BaseRequest

import bot


class TelegramTransport(BaseRequest):
    """Exercise PTB dispatch while replacing only Telegram's HTTP boundary."""

    def __init__(self):
        self.calls = []
        self.message_id = 100

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    async def do_request(self, url, method, request_data=None, **kwargs):
        endpoint = url.rsplit("/", 1)[-1]
        params = request_data.parameters if request_data else {}
        self.calls.append((endpoint, params))
        if endpoint == "getMe":
            result = {
                "id": 999,
                "is_bot": True,
                "first_name": "Recipes",
                "username": "recipes_test_bot",
            }
        elif endpoint in ("deleteMessage", "answerCallbackQuery"):
            result = True
        else:
            self.message_id += 1
            result = {
                "message_id": self.message_id,
                "date": int(datetime.now(timezone.utc).timestamp()),
                "chat": {"id": params.get("chat_id", 10), "type": "private"},
                "from": {"id": 999, "is_bot": True, "first_name": "Recipes"},
                "text": params.get("text", ""),
            }
        return 200, json.dumps({"ok": True, "result": result}).encode()


class Driver:
    def __init__(self, application, transport):
        self.application = application
        self.transport = transport
        self.counter = 0
        self.errors = []

    async def send(self, text=None, callback=None, photo=False, user_id=10, chat_type="private"):
        self.counter += 1
        user = {"id": user_id, "is_bot": False, "first_name": "Test"}
        message = {
            "message_id": self.counter,
            "date": int(datetime.now(timezone.utc).timestamp()),
            "chat": {"id": user_id, "type": chat_type},
            "from": user,
        }
        if photo:
            message["photo"] = [
                {"file_id": "test-photo", "file_unique_id": "photo", "width": 10, "height": 10}
            ]
        elif text is not None:
            message["text"] = text
            if text.startswith("/"):
                message["entities"] = [
                    {"type": "bot_command", "offset": 0, "length": len(text.split()[0])}
                ]
        raw = {"update_id": self.counter}
        if callback:
            message["text"] = "Button message"
            message["from"] = {"id": 999, "is_bot": True, "first_name": "Recipes"}
            raw["callback_query"] = {
                "id": str(self.counter),
                "from": user,
                "chat_instance": "1",
                "message": message,
                "data": callback,
            }
        else:
            raw["message"] = message
        await self.application.process_update(Update.de_json(raw, self.application.bot))
        assert not self.errors, self.errors

    @property
    def state(self):
        return self.application.handlers[0][0]._conversations.get((10, 10))

    @property
    def texts(self):
        return [
            params.get("text", "") for name, params in self.transport.calls if name == "sendMessage"
        ]


@pytest.fixture
async def driver(monkeypatch, tmp_path):
    from telegram.ext import ApplicationBuilder

    monkeypatch.setattr(bot, "TOKEN", "123456:TEST_TOKEN")
    monkeypatch.setattr(bot, "SITE_API_BASE", "http://localhost/api")
    monkeypatch.setattr(bot, "TOKENS_FILE", tmp_path / "tokens.json")
    transport = TelegramTransport()
    original = ApplicationBuilder
    monkeypatch.setattr(
        bot,
        "ApplicationBuilder",
        lambda: original().request(transport).get_updates_request(TelegramTransport()),
    )
    application = bot.build_application()
    result = Driver(application, transport)

    async def capture(update, context):
        result.errors.append(context.error)

    application.add_error_handler(capture)
    await application.initialize()
    yield result
    await application.shutdown()
