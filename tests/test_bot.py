import json
import os
from unittest.mock import AsyncMock

import pytest

import bot


@pytest.fixture
async def api(monkeypatch):
    """Real HTTP server, with the response shapes used by KobaLuck/Recipes."""
    from aiohttp import web

    calls = []
    recipes = []
    ingredients = [{"id": i, "name": f"Соль {i}", "measurement_unit": "г"} for i in range(1, 26)]
    tags = [{"id": 1, "name": "Завтрак", "slug": "breakfast"}]

    async def handle(request):
        payload = await request.json() if request.can_read_body else None
        path = request.path.removeprefix("/api/")
        calls.append(
            (
                request.method,
                path,
                payload,
                dict(request.query),
                request.headers.get("Authorization"),
            )
        )
        if path == "auth/token/login/":
            return web.json_response({"auth_token": "site-token"})
        if path == "auth/token/logout/":
            return web.Response(status=204)
        if path == "users/":
            return web.json_response({"id": 1}, status=201)
        if path == "ingredients/":
            return web.json_response(ingredients)
        if path == "tags/":
            return web.json_response(tags)
        if path == "recipes/" and request.method == "POST":
            assert request.headers.get("Authorization") == "Token site-token"
            assert (
                payload["ingredients"]
                and payload["tags"]
                and payload["image"].startswith("data:image/")
            )
            assert len({i["id"] for i in payload["ingredients"]}) == len(payload["ingredients"])
            recipes.append({**payload, "id": 1})
            return web.json_response(recipes[-1], status=201)
        if path == "recipes/":
            return web.json_response(
                {"results": recipes, "next": None, "previous": None, "count": len(recipes)}
            )
        if path == "recipes/download_shopping_cart/":
            # The site's DRF JSON renderer returns a JSON string with text/plain content type.
            return web.Response(text=json.dumps("Соль (г) — 2"), content_type="text/plain")
        if path.endswith("/favorite/") or path.endswith("/shopping_cart/"):
            return (
                web.Response(status=204)
                if request.method == "DELETE"
                else web.json_response({"id": 1}, status=201)
            )
        if path == "recipes/1/":
            return web.json_response(
                {
                    "id": 1,
                    "name": "Каша",
                    "text": "Готовим",
                    "cooking_time": 5,
                    "ingredients": [{"name": "Соль", "amount": 2, "measurement_unit": "г"}],
                }
            )
        return web.json_response({"detail": "Not found"}, status=404)

    app = web.Application()
    app.router.add_route("*", "/api/{path:.*}", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setattr(bot, "SITE_API_BASE", f"http://127.0.0.1:{port}/api")
    yield calls, recipes
    await runner.cleanup()


async def login(driver):
    await driver.send("/start")
    await driver.send(callback="auth:login")
    await driver.send("user@example.com")
    await driver.send(" password with spaces ")
    assert driver.state is None


async def recipe_to_ingredients(driver, command="/add"):
    bot.save_token_local(10, "site-token")
    await driver.send(command)
    assert driver.state == bot.NAME
    await driver.send("Каша")
    await driver.send("Вкусная каша")
    await driver.send("5")
    assert driver.state == bot.ING_LETTER


@pytest.mark.parametrize("command", ["/add", "/addrecipe"])
async def test_full_recipe_dispatch(driver, api, command):
    calls, recipes = api
    await login(driver)
    assert calls[0][2]["password"] == " password with spaces "
    assert any(name == "deleteMessage" for name, _ in driver.transport.calls)
    await recipe_to_ingredients(driver, command)
    await driver.send(callback="ing_letter:С")
    assert driver.state == bot.ING_PAGE
    await driver.send(callback="ing_page:2")
    await driver.send(callback="ing_select:11")
    assert driver.state == bot.ING_QTY
    await driver.send("2")
    await driver.send(callback="ing_done")
    assert driver.state == bot.TAGS
    await driver.send(callback="tag_select:1")
    await driver.send(callback="tags_done")
    await driver.send(callback="skip_image")
    await driver.send(callback="skip_url")
    assert driver.state == bot.CONFIRM
    await driver.send(callback="confirm_send")
    assert driver.state is None
    assert len(recipes) == 1
    assert recipes[0]["ingredients"] == [{"id": 11, "amount": 2}]
    assert recipes[0]["tags"] == [1]
    # Double clicking a previous confirmation cannot create a second recipe.
    await driver.send(callback="confirm_send")
    assert len(recipes) == 1


async def test_skip_tags_and_photo_upload(driver, api, monkeypatch):
    await recipe_to_ingredients(driver)
    await driver.send("Сол")
    await driver.send(callback="ing_select:1")
    await driver.send("2")
    await driver.send(callback="ing_done")
    await driver.send(callback="skip_tags")
    assert driver.state == bot.IMAGE
    download = AsyncMock(return_value="data:image/jpeg;base64,dGVzdA==")
    monkeypatch.setattr(bot, "download_photo_as_datauri", download)
    await driver.send(photo=True)
    await driver.send("https://example.com/recipe")
    await driver.send(callback="confirm_send")
    assert api[1][0]["image"] == download.return_value
    assert "https://example.com/recipe" in api[1][0]["text"]


@pytest.mark.parametrize(
    "phase",
    ["name", "desc", "time", "letters", "page", "qty", "more", "tags", "image", "url", "confirm"],
)
@pytest.mark.parametrize("cancel", ["/cancel", "button"])
async def test_cancel_every_recipe_step(driver, api, phase, cancel):
    bot.save_token_local(10, "site-token")
    steps = [
        ("name", "/add", None),
        ("desc", "Каша", None),
        ("time", "Описание", None),
        ("letters", "5", None),
        ("page", None, "ing_letter:С"),
        ("qty", None, "ing_select:1"),
        ("more", "2", None),
        ("tags", None, "ing_done"),
        ("image", None, "skip_tags"),
        ("url", None, "skip_image"),
        ("confirm", None, "skip_url"),
    ]
    for name, text, callback in steps:
        await driver.send(text, callback)
        if name == phase:
            break
    await driver.send(
        "/cancel" if cancel == "/cancel" else None, "cancel" if cancel == "button" else None
    )
    assert driver.state is None
    assert not driver.application.user_data[10]
    assert not api[1]


async def test_validation_empty_ingredients_and_duplicate(driver, api):
    await recipe_to_ingredients(driver)
    await driver.send(callback="ing_done")
    assert driver.state == bot.ING_LETTER
    await driver.send(callback="ing_letter:С")
    await driver.send(callback="ing_select:1")
    await driver.send("0")
    assert driver.state == bot.ING_QTY
    await driver.send("1")
    await driver.send(callback="ing_back_letters")
    await driver.send(callback="ing_letter:С")
    await driver.send(callback="ing_select:1")
    assert driver.state == bot.ING_LETTER
    assert len(driver.application.user_data[10]["ingredients"]) == 1


async def test_auth_registration_and_cancel(driver, api):
    await driver.send("/start")
    await driver.send(callback="auth:register")
    for text in ("user@example.com", "user", "Имя", "Фамилия", " password "):
        await driver.send(text)
    assert driver.state is None
    assert bot.load_token_local(10) == "site-token"
    registration = next(call for call in api[0] if call[1] == "users/")
    assert registration[2]["first_name"] == "Имя"
    await driver.send(callback="auth:login")
    await driver.send(callback="cancel")
    assert driver.state is None
    assert not driver.application.user_data[10]


async def test_menu_details_favorites_cart_logout(driver, api):
    await login(driver)
    api[1].append(
        {
            "id": 1,
            "name": "Каша",
            "cooking_time": 5,
            "ingredients": [{"name": "Соль", "amount": 2, "measurement_unit": "г"}],
        }
    )
    await driver.send(callback="list:all:1")
    await driver.send(callback="detail:1")
    await driver.send(callback="favorite:add:1")
    await driver.send(callback="favorite:del:1")
    await driver.send(callback="cart:add:1")
    await driver.send(callback="cart:del:1")
    await driver.send(callback="list:fav:1")
    await driver.send(callback="list:cart:1")
    await driver.send(callback="shopping_list")
    assert "Соль (г) — 2" in driver.texts
    await driver.send(callback="logout")
    assert bot.load_token_local(10) is None
    assert any(call[0] == "DELETE" and call[1] == "recipes/1/favorite/" for call in api[0])
    assert any(call[3].get("is_favorited") == "1" for call in api[0])


async def test_anonymous_and_groups(driver, api):
    await driver.send("/addrecipe")
    assert driver.state is None
    assert "Сначала войдите" in driver.texts[-1]
    await driver.send("/start", chat_type="group")
    await driver.send(callback="auth:login", chat_type="group")
    assert driver.state is None
    assert not api[0]


async def test_retry_preserves_recipe(driver, api, monkeypatch):
    await recipe_to_ingredients(driver)
    await driver.send(callback="ing_letter:С")
    await driver.send(callback="ing_select:1")
    await driver.send("1")
    await driver.send(callback="ing_done")
    await driver.send(callback="skip_tags")
    await driver.send(callback="skip_image")
    await driver.send(callback="skip_url")
    original = bot.api_post
    monkeypatch.setattr(bot, "api_post", AsyncMock(return_value=(400, {"name": ["Ошибка"]})))
    await driver.send(callback="confirm_send")
    assert driver.state == bot.CONFIRM
    assert driver.application.user_data[10]["name"] == "Каша"
    monkeypatch.setattr(bot, "api_post", original)
    await driver.send(callback="confirm_send")
    assert len(api[1]) == 1


async def test_network_failure(monkeypatch):
    monkeypatch.setattr(bot, "SITE_API_BASE", "http://127.0.0.1:1/api")
    status, data = await bot.api_get("recipes/")
    assert status == 503
    assert "недоступен" in data["detail"]


async def test_invalid_config(monkeypatch):
    monkeypatch.setattr(bot, "TOKEN", "")
    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        bot.validate_config()
    monkeypatch.setattr(bot, "TOKEN", "123:test")
    for value in ("", "/", "example.com", "file:///tmp/test"):
        monkeypatch.setattr(bot, "SITE_API_BASE", value)
        with pytest.raises(RuntimeError, match="SITE_API_BASE"):
            bot.validate_config()


def test_token_storage_and_corruption(monkeypatch, tmp_path):
    path = tmp_path / "nested" / "tokens.json"
    monkeypatch.setattr(bot, "TOKENS_FILE", path)
    bot.save_token_local(1, "secret")
    bot.save_token_local(2, "other")
    assert bot.load_token_local(1) == "secret"
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
    bot.del_token_local(1)
    assert bot.load_token_local(1) is None
    assert bot.load_token_local(2) == "other"
    path.write_text("broken")
    with pytest.raises(json.JSONDecodeError):
        bot.save_token_local(3, "new")
    assert path.read_text() == "broken"


async def test_shopping_list_aggregates_across_pages(driver, monkeypatch):
    bot.save_token_local(10, "site-token")
    ingredient = {"name": "Соль", "measurement_unit": "г", "amount": 2}
    getter = AsyncMock(
        side_effect=[
            (200, {"results": [{"ingredients": [ingredient]}], "next": "page2"}),
            (200, {"results": [{"ingredients": [ingredient]}], "next": None}),
        ]
    )
    monkeypatch.setattr(bot, "api_get", getter)
    await driver.send(callback="shopping_list")
    assert "Соль (г) — 4" in driver.texts
    assert getter.call_args_list[1].args[1]["page"] == 2


async def test_cancel_auth_password_and_restart(driver):
    await driver.send(callback="auth:login")
    await driver.send("test@example.com")
    assert driver.state == bot.LOGIN_PASS
    await driver.send("/start")
    assert driver.state is None
    assert not driver.application.user_data[10]
