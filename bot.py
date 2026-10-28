"""Telegram client for the Recipes API. No network or database work on import."""

import asyncio
import base64
import io
import json
import logging
import os
import tempfile
from pathlib import Path
from urllib.parse import urljoin, urlparse

import aiohttp
from dotenv import load_dotenv
from telegram import InlineKeyboardButton as Button
from telegram import InlineKeyboardMarkup as Markup
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    TypeHandler,
    filters,
)

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
SITE_API_BASE = os.getenv("SITE_API_BASE", "").strip().rstrip("/")
API_PAGE_SIZE = max(1, min(20, int(os.getenv("API_PAGE_SIZE") or "10")))
TOKENS_FILE = Path(os.getenv("TOKENS_FILE", str(BASE_DIR / "instance" / "bot_user_tokens.json")))
LOGGER = logging.getLogger(__name__)
(
    AUTH_CHOICE,
    LOGIN_EMAIL,
    LOGIN_PASS,
    REG_EMAIL,
    REG_USERNAME,
    REG_FIRST,
    REG_LAST,
    REG_PASS,
    NAME,
    DESCRIPTION,
    COOK_TIME,
    ING_LETTER,
    ING_PAGE,
    ING_QTY,
    ING_MORE,
    TAGS,
    IMAGE,
    URL,
    CONFIRM,
) = range(19)
END = ConversationHandler.END
# The site requires an image even when the user skips uploading one.
PLACEHOLDER_PNG_DATAURI = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAe0lEQVR4nO3PQQ0AIBDAsPOvDxH8MYAIHg3Jkgno5uz1dcMFDWhBA1rQgBY0oAUNaEEDWtCAFjSgBQ1oQQNa0IAWNKAFDWhBA1rQgBY0oAUNaEEDWtCAFjSgBQ1oQQNa0IAWNKAFDWhBA1rQgBY0oAUNaEEDWtCAFjx2AdXk8ndJ+FMbAAAAAElFTkSuQmCC"
)


def validate_config():
    if not TOKEN or TOKEN == "your_telegram_bot_token_here":
        raise RuntimeError("Укажите TELEGRAM_BOT_TOKEN в .env")
    parsed = urlparse(SITE_API_BASE)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.netloc
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("Укажите SITE_API_BASE в .env, например https://example.com/api/")


def read_tokens():
    if not TOKENS_FILE.exists():
        return {}
    data = json.loads(TOKENS_FILE.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Некорректный файл токенов")
    return data


def write_tokens(data):
    TOKENS_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=TOKENS_FILE.parent, prefix=".tokens-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream)
        os.replace(temporary, TOKENS_FILE)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_token_local(telegram_id, token):
    data = read_tokens()
    data[str(telegram_id)] = token
    write_tokens(data)


def load_token_local(telegram_id):
    return read_tokens().get(str(telegram_id))


def del_token_local(telegram_id):
    data = read_tokens()
    data.pop(str(telegram_id), None)
    write_tokens(data)


async def api_request(method, path, token=None, params=None, json_data=None, timeout=20):
    headers = {"Authorization": f"Token {token}"} if token else {}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
            async with session.request(
                method,
                f"{SITE_API_BASE}/{path.lstrip('/')}",
                params=params,
                json=json_data,
                headers=headers,
                allow_redirects=False,
            ) as response:
                try:
                    data = await response.json(content_type=None)
                except (ValueError, UnicodeError):
                    # Do not show HTML tracebacks or proxy diagnostics to users.
                    data = (
                        {"detail": "Сервер вернул некорректный ответ."}
                        if response.status != 204
                        else None
                    )
                return response.status, data
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return 503, {"detail": "Сайт временно недоступен. Попробуйте ещё раз."}


async def api_get(path, params=None, token=None):
    return await api_request("GET", path, token=token, params=params)


async def api_post(path, json_data=None, token=None):
    return await api_request("POST", path, token=token, json_data=json_data)


def format_api_errors(data):
    if isinstance(data, dict):
        return "\n".join(f"{key}: {format_api_errors(value)}" for key, value in data.items())[:2000]
    if isinstance(data, list):
        return "; ".join(map(str, data))[:2000]
    return str(data or "Неизвестная ошибка сервера.")[:2000]


def keyboard(rows=(), cancel=True):
    rows = list(rows)
    if cancel:
        rows.append([Button("❌ Отмена", callback_data="cancel")])
    return Markup(rows)


async def say(update, text, rows=(), cancel=True):
    """Use effective_message for both text updates and button callbacks."""
    await update.effective_message.reply_text(text, reply_markup=keyboard(rows, cancel))


async def answer(update):
    if update.callback_query:
        await update.callback_query.answer()


async def send_long(message, text, reply_markup=None):
    for start in range(0, len(text), 4000):
        last = start + 4000 >= len(text)
        await message.reply_text(
            text[start : start + 4000], reply_markup=reply_markup if last else None
        )


async def private_only(update, context):
    if update.effective_chat and update.effective_chat.type != "private":
        await answer(update)
        raise ApplicationHandlerStop


async def menu(update, context):
    await answer(update)
    context.user_data.clear()
    rows = [
        [Button("📋 Рецепты", callback_data="list:all:1")],
        [Button("➕ Создать рецепт", callback_data="add_recipe")],
        [
            Button("⭐ Избранное", callback_data="list:fav:1"),
            Button("🛒 Корзина", callback_data="list:cart:1"),
        ],
        [Button("🧾 Список покупок", callback_data="shopping_list")],
        [
            Button("Войти", callback_data="auth:login"),
            Button("Регистрация", callback_data="auth:register"),
        ],
        [Button("Выйти", callback_data="logout")],
    ]
    await say(
        update, "Выберите действие. Для отмены любого шага используйте /cancel.", rows, cancel=False
    )
    return END


async def start_handler(update, context):
    return await menu(update, context)


async def auth_choice_handler(update, context):
    await answer(update)
    context.user_data.clear()
    action = update.callback_query.data.split(":")[1]
    if action == "login":
        await say(update, "Введите email для входа:")
        return LOGIN_EMAIL
    await say(update, "Регистрация — введите email:")
    return REG_EMAIL


async def collect_text(update, context, key, prompt, current_state, next_state, limit=150):
    value = update.effective_message.text.strip()
    if not value or len(value) > limit:
        await say(update, f"Введите от 1 до {limit} символов:")
        return current_state
    context.user_data[key] = value
    await say(update, prompt)
    return next_state


async def auth_login_email(update, context):
    return await collect_text(
        update, context, "email", "Введите пароль:", LOGIN_EMAIL, LOGIN_PASS, 254
    )


async def delete_password_message(update):
    try:
        await update.effective_message.delete()
    except Exception:
        # Telegram may refuse deletion; never log or persist the password.
        pass


async def auth_login_pass(update, context):
    password = update.effective_message.text  # Spaces may be part of a password.
    await delete_password_message(update)
    status, data = await api_post(
        "auth/token/login/", {"email": context.user_data.get("email"), "password": password}
    )
    if status == 200 and isinstance(data, dict) and data.get("auth_token"):
        save_token_local(update.effective_user.id, data["auth_token"])
        await say(update, "Вход выполнен.", cancel=False)
        return await menu(update, context)
    context.user_data.clear()
    await say(update, "Ошибка входа: " + format_api_errors(data) + "\nВведите email заново:")
    return LOGIN_EMAIL


async def auth_register_email(update, context):
    return await collect_text(
        update, context, "email", "Введите username:", REG_EMAIL, REG_USERNAME, 254
    )


async def auth_register_username(update, context):
    return await collect_text(update, context, "username", "Введите имя:", REG_USERNAME, REG_FIRST)


async def auth_register_first(update, context):
    return await collect_text(
        update, context, "first_name", "Введите фамилию:", REG_FIRST, REG_LAST
    )


async def auth_register_last(update, context):
    return await collect_text(update, context, "last_name", "Введите пароль:", REG_LAST, REG_PASS)


async def auth_register_pass(update, context):
    password = update.effective_message.text
    await delete_password_message(update)
    payload = {
        key: context.user_data[key] for key in ("email", "username", "first_name", "last_name")
    }
    payload["password"] = password
    status, data = await api_post("users/", payload)
    context.user_data.clear()
    if status not in (200, 201):
        await say(
            update, "Ошибка регистрации: " + format_api_errors(data) + "\nВведите email заново:"
        )
        return REG_EMAIL
    status, data = await api_post(
        "auth/token/login/", {"email": payload["email"], "password": password}
    )
    if status == 200 and isinstance(data, dict) and data.get("auth_token"):
        save_token_local(update.effective_user.id, data["auth_token"])
        await say(update, "Регистрация и вход выполнены.", cancel=False)
    else:
        await say(update, "Регистрация выполнена. Для входа выберите «Войти».", cancel=False)
    return await menu(update, context)


async def logout(update, context):
    await answer(update)
    token = load_token_local(update.effective_user.id)
    if token:
        status, data = await api_post("auth/token/logout/", token=token)
        if status not in (200, 204, 401):
            await say(
                update,
                "Не удалось завершить сеанс на сайте: " + format_api_errors(data),
                cancel=False,
            )
            return END
    del_token_local(update.effective_user.id)
    await say(update, "Вы вышли из аккаунта.", cancel=False)
    return await menu(update, context)


async def start_add_recipe(update, context):
    await answer(update)
    context.user_data.clear()
    if not load_token_local(update.effective_user.id):
        await say(update, "Сначала войдите в аккаунт: /start → Войти.", cancel=False)
        return END
    await say(update, "Введите название рецепта:")
    return NAME


async def recipe_name(update, context):
    return await collect_text(
        update, context, "name", "Введите описание рецепта:", NAME, DESCRIPTION, 100
    )


async def recipe_desc(update, context):
    return await collect_text(
        update,
        context,
        "description",
        "Время приготовления в минутах (целое ≥ 1):",
        DESCRIPTION,
        COOK_TIME,
        2000,
    )


def positive_integer(text):
    try:
        value = int(text)
        return value if 1 <= value <= 2147483647 else None
    except ValueError:
        return None


async def show_letters(update):
    letters = list("АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯABCDEFGHIJKLMNOPQRSTUVWXYZ")
    rows = [
        [Button(ch, callback_data=f"ing_letter:{ch}") for ch in letters[i : i + 6]]
        for i in range(0, len(letters), 6)
    ]
    rows.append([Button("✅ Готово — к тегам", callback_data="ing_done")])
    await say(update, "Выберите первую букву или напишите часть названия ингредиента:", rows)
    return ING_LETTER


async def recipe_cook_time(update, context):
    value = positive_integer(update.effective_message.text)
    if value is None:
        await say(update, "Введите целое число от 1 до 2147483647:")
        return COOK_TIME
    context.user_data["cooking_time"] = value
    context.user_data["ingredients"] = []
    return await show_letters(update)


async def catalog(path, page, params=None):
    """Support DRF paginated responses and unpaginated catalogues from Recipes."""
    status, data = await api_get(
        path, params={**(params or {}), "page": page, "limit": API_PAGE_SIZE}
    )
    if status != 200:
        return status, data
    if isinstance(data, list):
        start = (page - 1) * API_PAGE_SIZE
        return 200, {
            "results": data[start : start + API_PAGE_SIZE],
            "previous": page > 1,
            "next": start + API_PAGE_SIZE < len(data),
        }
    if isinstance(data, dict) and isinstance(data.get("results"), list):
        return status, data
    return 502, {"detail": "Неверный формат каталога."}


async def show_ingredients(update, context, search, page=1):
    status, data = await catalog("ingredients/", page, {"name": search})
    if status != 200:
        await say(update, "Ошибка получения ингредиентов: " + format_api_errors(data))
        return ING_LETTER
    context.user_data["ing_search"] = search
    context.user_data["ing_page"] = page
    context.user_data["ing_options"] = {item["id"]: item for item in data["results"]}
    rows = [
        [
            Button(
                f"{item['name']} ({item['measurement_unit']})",
                callback_data=f"ing_select:{item['id']}",
            )
        ]
        for item in data["results"]
    ]
    nav = []
    if data.get("previous"):
        nav.append(Button("‹ Назад", callback_data=f"ing_page:{page - 1}"))
    if data.get("next"):
        nav.append(Button("Далее ›", callback_data=f"ing_page:{page + 1}"))
    if nav:
        rows.append(nav)
    rows += [
        [Button("Другой ингредиент", callback_data="ing_back_letters")],
        [Button("✅ Готово — к тегам", callback_data="ing_done")],
    ]
    await say(
        update,
        "Выберите ингредиент:"
        if data["results"]
        else "Ничего не найдено. Попробуйте другое название.",
        rows,
    )
    return ING_PAGE


async def ing_letter_handler(update, context):
    await answer(update)
    search = (
        update.callback_query.data.split(":", 1)[1]
        if update.callback_query
        else update.effective_message.text.strip()
    )
    if not search or len(search) > 100:
        return await show_letters(update)
    return await show_ingredients(update, context, search)


async def ing_page_handler(update, context):
    await answer(update)
    return await show_ingredients(
        update,
        context,
        context.user_data.get("ing_search", ""),
        int(update.callback_query.data.split(":")[1]),
    )


async def ing_select_handler(update, context):
    await answer(update)
    ing_id = int(update.callback_query.data.split(":")[1])
    item = context.user_data.get("ing_options", {}).get(ing_id)
    if not item:
        return await show_letters(update)
    if any(ing["id"] == ing_id for ing in context.user_data["ingredients"]):
        await say(update, "Этот ингредиент уже добавлен. Выберите другой.")
        return await show_letters(update)
    context.user_data["selected_ing"] = item
    await say(update, f"Количество для «{item['name']}» ({item['measurement_unit']}), целое ≥ 1:")
    return ING_QTY


async def ing_qty_handler(update, context):
    value = positive_integer(update.effective_message.text)
    if value is None:
        await say(update, "Введите целое количество от 1 до 2147483647:")
        return ING_QTY
    item = context.user_data.pop("selected_ing")
    context.user_data["ingredients"].append({**item, "amount": value})
    await say(
        update,
        f"Добавлено: {item['name']} — {value} {item['measurement_unit']}.\nВыберите следующий ингредиент или нажмите «Готово».",
        [
            [Button("Следующий ингредиент", callback_data="ing_back_letters")],
            [Button("✅ Готово — к тегам", callback_data="ing_done")],
        ],
    )
    return ING_MORE


async def ing_confirm_choose_handler(update, context):
    await answer(update)
    if update.callback_query.data == "ing_back_letters":
        return await show_letters(update)
    if not context.user_data.get("ingredients"):
        await say(update, "Добавьте хотя бы один ингредиент.")
        return await show_letters(update)
    return await show_tags(update, context)


async def show_tags(update, context, page=1):
    status, data = await catalog("tags/", page)
    if status != 200:
        await say(
            update,
            "Ошибка получения тегов: " + format_api_errors(data),
            [[Button("Повторить", callback_data="tag_page:1")]],
        )
        return TAGS
    context.user_data["tag_page"] = page
    context.user_data["tag_options"] = {item["id"]: item["name"] for item in data["results"]}
    chosen = context.user_data.setdefault("selected_tags", {})
    rows = [
        [
            Button(
                ("✅ " if item["id"] in chosen else "") + item["name"],
                callback_data=f"tag_select:{item['id']}",
            )
        ]
        for item in data["results"]
    ]
    nav = []
    if data.get("previous"):
        nav.append(Button("‹ Назад", callback_data=f"tag_page:{page - 1}"))
    if data.get("next"):
        nav.append(Button("Далее ›", callback_data=f"tag_page:{page + 1}"))
    if nav:
        rows.append(nav)
    rows += [
        [Button("✅ Готово — к фото", callback_data="tags_done")],
        [Button("Пропустить", callback_data="skip_tags")],
    ]
    await say(update, "Выберите теги. Выбрано: " + (", ".join(chosen.values()) or "нет"), rows)
    return TAGS


async def tag_page_handler(update, context):
    await answer(update)
    return await show_tags(update, context, int(update.callback_query.data.split(":")[1]))


async def tag_select_handler(update, context):
    await answer(update)
    tag_id = int(update.callback_query.data.split(":")[1])
    chosen = context.user_data.setdefault("selected_tags", {})
    options = context.user_data.get("tag_options", {})
    if tag_id in chosen:
        del chosen[tag_id]
    elif tag_id in options:
        chosen[tag_id] = options[tag_id]
    return await show_tags(update, context, context.user_data.get("tag_page", 1))


async def tags_done_handler(update, context):
    await answer(update)
    if not context.user_data.get("selected_tags"):
        # Site validation requires a tag. Skipping uses the first existing tag.
        if update.callback_query.data == "skip_tags":
            status, data = await catalog("tags/", 1)
            if status == 200 and data["results"]:
                tag = data["results"][0]
                context.user_data["selected_tags"] = {tag["id"]: tag["name"]}
                await say(update, f"Выбран тег по умолчанию: {tag['name']}.")
            else:
                await say(
                    update,
                    "Сайт требует хотя бы один тег. Добавьте тег на сайте и нажмите «Повторить».",
                    [[Button("Повторить", callback_data="tag_page:1")]],
                )
                return TAGS
        else:
            await say(update, "Выберите хотя бы один тег или нажмите «Пропустить».")
            return TAGS
    await say(
        update,
        "Пришлите фото рецепта или нажмите «Пропустить».",
        [[Button("Пропустить", callback_data="skip_image")]],
    )
    return IMAGE


async def image_handler(update, context):
    await answer(update)
    if update.callback_query:
        context.user_data.pop("image_file_id", None)
    elif update.effective_message.photo:
        context.user_data["image_file_id"] = update.effective_message.photo[-1].file_id
    else:
        await say(
            update,
            "Пришлите фото или нажмите «Пропустить».",
            [[Button("Пропустить", callback_data="skip_image")]],
        )
        return IMAGE
    await say(
        update,
        "Добавьте ссылку на источник или нажмите «Пропустить».",
        [[Button("Пропустить", callback_data="skip_url")]],
    )
    return URL


async def url_handler(update, context):
    await answer(update)
    value = None if update.callback_query else update.effective_message.text.strip()
    if value and (
        len(value) > 1000
        or urlparse(value).scheme not in ("http", "https")
        or not urlparse(value).netloc
    ):
        await say(
            update,
            "Введите ссылку http:// или https:// (до 1000 символов).",
            [[Button("Пропустить", callback_data="skip_url")]],
        )
        return URL
    context.user_data["source_url"] = value
    data = context.user_data
    ingredients = "\n".join(
        f"• {i['name']}: {i['amount']} {i['measurement_unit']}" for i in data["ingredients"]
    )
    summary = (
        f"Проверьте рецепт:\n\n{data['name']}\n{data['description']}\n"
        f"⏱ {data['cooking_time']} мин.\n{ingredients}\n"
        f"Теги: {', '.join(data['selected_tags'].values())}\n"
        f"Фото: {'есть' if data.get('image_file_id') else 'заглушка'}\nСсылка: {value or '—'}"
    )
    await send_long(
        update.effective_message,
        summary,
        keyboard([[Button("✅ Подтвердить", callback_data="confirm_send")]]),
    )
    return CONFIRM


async def download_photo_as_datauri(file_id, context):
    photo = await context.bot.get_file(file_id)
    data = await photo.download_as_bytearray()
    return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")


async def confirm_send_handler(update, context):
    await answer(update)
    token = load_token_local(update.effective_user.id)
    if not token:
        await say(update, "Для сохранения войдите в аккаунт: /start → Войти.", cancel=False)
        context.user_data.clear()
        return END
    data = context.user_data
    image = PLACEHOLDER_PNG_DATAURI
    if data.get("image_file_id"):
        try:
            image = await download_photo_as_datauri(data["image_file_id"], context)
        except Exception:
            await say(
                update,
                "Не удалось загрузить фото. Нажмите «Подтвердить» для повторной попытки.",
                [[Button("Повторить", callback_data="confirm_send")]],
            )
            return CONFIRM
    text = data["description"]
    if data.get("source_url"):
        text += "\n\nСсылка на источник: " + data["source_url"]
    payload = {
        "name": data["name"],
        "text": text,
        "cooking_time": data["cooking_time"],
        "ingredients": [{"id": i["id"], "amount": i["amount"]} for i in data["ingredients"]],
        "tags": list(data["selected_tags"]),
        "image": image,
    }
    status, response = await api_post("recipes/", payload, token=token)
    if status in (200, 201) and isinstance(response, dict):
        context.user_data.clear()
        await say(update, "Рецепт создан ✅", cancel=False)
        return await menu(update, context)
    if status == 401:
        del_token_local(update.effective_user.id)
    await say(
        update,
        "Не удалось сохранить: "
        + format_api_errors(response)
        + "\nЧерновик сохранён до отмены. При обрыве связи проверьте список рецептов перед повтором.",
        [[Button("Повторить", callback_data="confirm_send")]],
    )
    return CONFIRM


async def view_list(update, context):
    await answer(update)
    _, kind, page_text = update.callback_query.data.split(":")
    page = int(page_text)
    token = load_token_local(update.effective_user.id)
    if kind != "all" and not token:
        await say(update, "Для избранного и корзины войдите в аккаунт через /start.", cancel=False)
        return END
    params = {"page": page, "limit": API_PAGE_SIZE}
    if kind != "all":
        params["is_favorited" if kind == "fav" else "is_in_shopping_cart"] = 1
    status, data = await api_get("recipes/", params, token=token)
    if status != 200:
        await say(update, "Ошибка получения списка: " + format_api_errors(data), cancel=False)
        return END
    results = data.get("results", []) if isinstance(data, dict) else data
    for recipe in results:
        rid = recipe["id"]
        rows = [[Button("Подробнее", callback_data=f"detail:{rid}")]]
        if token:
            rows.append(
                [
                    Button(
                        "Убрать ⭐" if recipe.get("is_favorited") else "В избранное ⭐",
                        callback_data=f"favorite:{'del' if recipe.get('is_favorited') else 'add'}:{rid}",
                    ),
                    Button(
                        "Убрать 🛒" if recipe.get("is_in_shopping_cart") else "В корзину 🛒",
                        callback_data=f"cart:{'del' if recipe.get('is_in_shopping_cart') else 'add'}:{rid}",
                    ),
                ]
            )
        await say(update, f"{recipe['name']} — {recipe['cooking_time']} мин.", rows, cancel=False)
    rows = []
    if isinstance(data, dict):
        if data.get("previous"):
            rows.append(Button("‹ Назад", callback_data=f"list:{kind}:{page - 1}"))
        if data.get("next"):
            rows.append(Button("Далее ›", callback_data=f"list:{kind}:{page + 1}"))
    await say(
        update,
        f"Страница {page}." if results else "Рецептов пока нет.",
        ([rows] if rows else []) + [[Button("🏠 Меню", callback_data="home")]],
        cancel=False,
    )
    return END


async def recipe_detail(update, context):
    await answer(update)
    rid = int(update.callback_query.data.split(":")[1])
    status, data = await api_get(
        f"recipes/{rid}/", token=load_token_local(update.effective_user.id)
    )
    if status != 200:
        await say(update, "Рецепт недоступен: " + format_api_errors(data), cancel=False)
        return END
    ingredients = "\n".join(
        f"• {i['name']}: {i['amount']} {i['measurement_unit']}" for i in data["ingredients"]
    )
    text = f"{data['name']}\n\n{data['text']}\n\n⏱ {data['cooking_time']} мин.\n{ingredients}"
    await send_long(
        update.effective_message,
        text,
        keyboard([[Button("🏠 Меню", callback_data="home")]], cancel=False),
    )
    if data.get("image"):
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
                async with session.get(urljoin(SITE_API_BASE + "/", data["image"])) as response:
                    response.raise_for_status()
                    content = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        content.extend(chunk)
                        if len(content) > 10 * 1024 * 1024:
                            raise ValueError("Photo exceeds Telegram size limit")
            await update.effective_message.reply_photo(io.BytesIO(content))
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            await say(update, "Фото временно недоступно.", cancel=False)
    return END


async def recipe_action(update, context):
    await answer(update)
    kind, action, rid = update.callback_query.data.split(":")
    token = load_token_local(update.effective_user.id)
    if not token:
        await say(update, "Сначала войдите через /start.", cancel=False)
        return END
    endpoint = "favorite" if kind == "favorite" else "shopping_cart"
    status, data = await api_request(
        "POST" if action == "add" else "DELETE", f"recipes/{int(rid)}/{endpoint}/", token=token
    )
    await say(
        update,
        ("Добавлено." if action == "add" else "Удалено.")
        if status in (200, 201, 204)
        else "Ошибка: " + format_api_errors(data),
        cancel=False,
    )
    if status in (200, 201, 204):
        await update.callback_query.edit_message_reply_markup(
            reply_markup=keyboard(
                [
                    [
                        Button("Открыть избранное", callback_data="list:fav:1"),
                        Button("Открыть корзину", callback_data="list:cart:1"),
                    ],
                    [Button("Подробнее", callback_data=f"detail:{int(rid)}")],
                ],
                cancel=False,
            )
        )
    return END


async def shopping_list(update, context):
    await answer(update)
    token = load_token_local(update.effective_user.id)
    if not token:
        await say(update, "Сначала войдите через /start.", cancel=False)
        return END
    # Aggregate cart ingredients here; the legacy site's download endpoint groups
    # by recipe ingredient rows and does not combine repeated ingredients.
    totals = {}
    page = 1
    while True:
        status, data = await api_get(
            "recipes/", {"is_in_shopping_cart": 1, "page": page, "limit": 100}, token=token
        )
        if status != 200:
            await say(update, "Ошибка: " + format_api_errors(data), cancel=False)
            return END
        recipes = data.get("results", []) if isinstance(data, dict) else data
        for recipe in recipes:
            for ingredient in recipe.get("ingredients", []):
                key = (ingredient["name"], ingredient["measurement_unit"])
                totals[key] = totals.get(key, 0) + ingredient["amount"]
        if not isinstance(data, dict) or not data.get("next"):
            break
        page += 1
    text = "\n".join(
        f"{name} ({unit}) — {amount}" for (name, unit), amount in sorted(totals.items())
    )
    await send_long(update.effective_message, text or "Корзина пуста.")
    return END


async def cancel_handler(update, context):
    await answer(update)
    context.user_data.clear()
    await say(update, "Операция отменена.", cancel=False)
    return await menu(update, context)


async def stale_callback(update, context):
    await answer(update)
    await say(
        update, "Эта кнопка больше не активна. Используйте кнопки последнего шага или /cancel."
    )
    return None


async def error_handler(update, context):
    # Logging Update or exception text can expose passwords, tokens and API URLs.
    LOGGER.error("Update failed (%s)", type(context.error).__name__)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text(
            "Не удалось выполнить действие. Повторите его или используйте /cancel."
        )


def build_conv_handler():
    text = filters.TEXT & ~filters.COMMAND
    ingredient_choices = [
        CallbackQueryHandler(ing_letter_handler, pattern=r"^ing_letter:[A-ZА-ЯЁ]$"),
        CallbackQueryHandler(ing_page_handler, pattern=r"^ing_page:[1-9][0-9]*$"),
        CallbackQueryHandler(ing_select_handler, pattern=r"^ing_select:[1-9][0-9]*$"),
        CallbackQueryHandler(ing_confirm_choose_handler, pattern=r"^(ing_back_letters|ing_done)$"),
        MessageHandler(text, ing_letter_handler),
    ]
    return ConversationHandler(
        entry_points=[
            CommandHandler(["start", "menu"], menu),
            CommandHandler(["add", "addrecipe"], start_add_recipe),
            CallbackQueryHandler(menu, pattern=r"^home$"),
            CallbackQueryHandler(start_add_recipe, pattern=r"^add_recipe$"),
            CallbackQueryHandler(auth_choice_handler, pattern=r"^auth:(login|register)$"),
            CallbackQueryHandler(logout, pattern=r"^logout$"),
            CallbackQueryHandler(view_list, pattern=r"^list:(all|fav|cart):[1-9][0-9]*$"),
            CallbackQueryHandler(recipe_detail, pattern=r"^detail:[1-9][0-9]*$"),
            CallbackQueryHandler(recipe_action, pattern=r"^(favorite|cart):(add|del):[1-9][0-9]*$"),
            CallbackQueryHandler(shopping_list, pattern=r"^shopping_list$"),
        ],
        states={
            LOGIN_EMAIL: [MessageHandler(text, auth_login_email)],
            LOGIN_PASS: [MessageHandler(text, auth_login_pass)],
            REG_EMAIL: [MessageHandler(text, auth_register_email)],
            REG_USERNAME: [MessageHandler(text, auth_register_username)],
            REG_FIRST: [MessageHandler(text, auth_register_first)],
            REG_LAST: [MessageHandler(text, auth_register_last)],
            REG_PASS: [MessageHandler(text, auth_register_pass)],
            NAME: [MessageHandler(text, recipe_name)],
            DESCRIPTION: [MessageHandler(text, recipe_desc)],
            COOK_TIME: [MessageHandler(text, recipe_cook_time)],
            ING_LETTER: ingredient_choices,
            ING_PAGE: ingredient_choices,
            ING_MORE: ingredient_choices,
            ING_QTY: [MessageHandler(text, ing_qty_handler)],
            TAGS: [
                CallbackQueryHandler(tag_page_handler, pattern=r"^tag_page:[1-9][0-9]*$"),
                CallbackQueryHandler(tag_select_handler, pattern=r"^tag_select:[1-9][0-9]*$"),
                CallbackQueryHandler(tags_done_handler, pattern=r"^(tags_done|skip_tags)$"),
            ],
            IMAGE: [
                MessageHandler(filters.ALL & ~filters.COMMAND, image_handler),
                CallbackQueryHandler(image_handler, pattern=r"^skip_image$"),
            ],
            URL: [
                MessageHandler(text, url_handler),
                CallbackQueryHandler(url_handler, pattern=r"^skip_url$"),
            ],
            CONFIRM: [CallbackQueryHandler(confirm_send_handler, pattern=r"^confirm_send$")],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_handler),
            CommandHandler(["start", "menu"], menu),
            CallbackQueryHandler(cancel_handler, pattern=r"^cancel$"),
            CallbackQueryHandler(stale_callback),
        ],
        per_user=True,
        per_chat=True,
        allow_reentry=False,
    )


async def on_start(application):
    await application.bot.set_my_commands(
        [
            ("start", "Открыть меню"),
            ("addrecipe", "Создать рецепт"),
            ("cancel", "Отменить действие"),
        ]
    )
    LOGGER.warning("Bot ready: @%s", application.bot.username)


def build_application():
    validate_config()
    application = (
        ApplicationBuilder().token(TOKEN).concurrent_updates(False).post_init(on_start).build()
    )
    application.add_handler(TypeHandler(Update, private_only), group=-1)
    application.add_handler(build_conv_handler())
    application.add_handler(CommandHandler("cancel", cancel_handler))
    application.add_handler(CallbackQueryHandler(stale_callback))
    application.add_error_handler(error_handler)
    return application


def main():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    build_application().run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
