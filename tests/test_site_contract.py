"""Optional contract test against an unchanged checkout of KobaLuck/Recipes.

SITE_SOURCE=/path/to/Recipes/backend python -m pytest tests/test_site_contract.py
Only Telegram's HTTP transport is replaced. API, ORM and image validation are real.
"""

import asyncio
import io
import os
import sys
import threading
from pathlib import Path
from wsgiref.simple_server import WSGIRequestHandler, make_server

import pytest

import bot


@pytest.fixture
async def real_site(monkeypatch, tmp_path):
    source = os.getenv("SITE_SOURCE")
    if not source:
        pytest.skip("Set SITE_SOURCE to the Recipes backend checkout")
    pytest.importorskip("django")
    sys.path.insert(0, str(Path(source).resolve()))
    from config import settings as site_settings
    from django.conf import settings

    config = {name: getattr(site_settings, name) for name in dir(site_settings) if name.isupper()}
    config.update(
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": str(tmp_path / "site.sqlite3"),
            }
        },
        ALLOWED_HOSTS=["127.0.0.1", "localhost"],
        MEDIA_ROOT=str(tmp_path / "media"),
        DEBUG=False,
    )
    settings.configure(**config)
    import django
    from django.core.management import call_command

    await asyncio.to_thread(django.setup)
    await asyncio.to_thread(call_command, "migrate", verbosity=0, stdout=io.StringIO())
    from recipes.models import Ingredient, Tag

    await asyncio.to_thread(Ingredient.objects.create, name="Соль", measurement_unit="г")
    await asyncio.to_thread(Tag.objects.create, name="Завтрак", slug="breakfast")
    from django.core.wsgi import get_wsgi_application

    class QuietHandler(WSGIRequestHandler):
        def log_message(self, *args):
            pass

    server = make_server("127.0.0.1", 0, get_wsgi_application(), handler_class=QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(bot, "SITE_API_BASE", f"http://127.0.0.1:{server.server_port}/api")
    yield
    await asyncio.to_thread(server.shutdown)
    server.server_close()
    thread.join(timeout=5)


async def test_complete_flow_with_real_recipes_backend(driver, real_site):
    await driver.send("/start")
    await driver.send(callback="auth:register")
    for value in (
        "contract@example.com",
        "contract_user",
        "Test",
        "User",
        "correct-test-password-734",
    ):
        await driver.send(value)
    assert bot.load_token_local(10), driver.texts
    await driver.send("/addrecipe")
    for value in ("Каша", "Варим кашу", "10"):
        await driver.send(value)
    await driver.send(callback="ing_letter:С")
    await driver.send(callback="ing_select:1")
    await driver.send("2")
    await driver.send(callback="ing_done")
    await driver.send(callback="skip_tags")
    await driver.send(callback="skip_image")
    await driver.send(callback="skip_url")
    await driver.send(callback="confirm_send")
    assert driver.state is None, "\n".join(driver.texts[-3:])
    assert "Рецепт создан ✅" in driver.texts
    await driver.send(callback="favorite:add:1")
    await driver.send(callback="cart:add:1")
    await driver.send(callback="list:fav:1")
    assert any("Каша — 10" in text for text in driver.texts)
    status, second = await bot.api_post(
        "recipes/",
        {
            "name": "Второй рецепт",
            "text": "Описание",
            "cooking_time": 5,
            "ingredients": [{"id": 1, "amount": 3}],
            "tags": [1],
            "image": bot.PLACEHOLDER_PNG_DATAURI,
        },
        token=bot.load_token_local(10),
    )
    assert status == 201, second
    await driver.send(callback=f"cart:add:{second['id']}")
    await driver.send(callback="shopping_list")
    assert any("Соль (г) — 5" in text for text in driver.texts), driver.texts
    await driver.send(callback="cart:del:1")
    await driver.send(callback="favorite:del:1")
    await driver.send(callback="logout")
    assert bot.load_token_local(10) is None
