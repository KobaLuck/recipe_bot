import os
import threading
import difflib

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from settings import Config
from app import create_app
from models import (
    db,
    User,
    Recipe,
    RecipeIngredient,
    Tag,
    TagInRecipe,
    Ingredient,
    Favorite,
    ShoppingCart,
)

# FSM states
(
    RECIPE_NAME,
    RECIPE_DESC,
    COOK_TIME,
    ING_NAME,
    ING_QTY,
    TAGS,
    IMAGE,
    URL,
    CONFIRM,
) = range(9)

# Environment and config
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
if not TOKEN:
    raise RuntimeError('TELEGRAM_BOT_TOKEN не задан')

if not Config.ADMIN_IDS:
    raise RuntimeError('ADMIN_IDS не задан')

ADMIN_IDS = Config.ADMIN_IDS

app = create_app()
app.app_context().push()


def get_similar(name: str, cutoff: float = 0.6, limit: int = 5) -> list:
    names = [ing.name for ing in Ingredient.query.all()]
    return difflib.get_close_matches(name, names, n=limit, cutoff=cutoff)


async def get_or_create_user(user) -> User:
    u = User.query.filter_by(telegram_id=user.id).first()
    if not u:
        u = User(
            telegram_id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
        )
        db.session.add(u)
        db.session.commit()
    return u


async def start_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_or_create_user(update.effective_user)
    buttons = [
        [InlineKeyboardButton('📋 Список', callback_data='view_list')],
        [InlineKeyboardButton('⭐ Избранное', callback_data='view_fav')],
    ]
    if update.effective_user.id in ADMIN_IDS:
        buttons.insert(1, [InlineKeyboardButton('➕ Добавить рецепт', callback_data='add_recipe')])
    await update.effective_message.reply_text(
        f'Привет, {user.first_name}! Выберите действие:',
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def view_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = await get_or_create_user(q.from_user)
    recs = Recipe.query.all()
    if not recs:
        await q.message.reply_text('Список пуст')
        return
    for r in recs:
        fav = bool(Favorite.query.filter_by(user_id=user.id, recipe_id=r.id).first())
        cart = bool(ShoppingCart.query.filter_by(user_id=user.id, recipe_id=r.id).first())
        btns = [
            InlineKeyboardButton('ℹ️', callback_data=f'detail_{r.id}'),
            InlineKeyboardButton('⭐' if not fav else '❌', callback_data=f'fav_{r.id}'),
            InlineKeyboardButton('🛒' if not cart else '❌', callback_data=f'cart_{r.id}'),
        ]
        if update.effective_user.id in ADMIN_IDS:
            btns.append(InlineKeyboardButton('✏️ Ссылка', callback_data=f'editurl_{r.id}'))
        await q.message.reply_text(
            r.name,
            reply_markup=InlineKeyboardMarkup([btns]),
        )


async def view_fav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = await get_or_create_user(q.from_user)
    favs = Favorite.query.filter_by(user_id=user.id).all()
    if not favs:
        await q.message.reply_text('Нет избранного')
        return
    for f in favs:
        await q.message.reply_text(f.recipe.name)


async def recipe_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    action, rid = q.data.split('_', 1)
    rid = int(rid)
    rec = Recipe.query.get_or_404(rid)
    if action == 'detail':
        ing = '\n'.join(f'- {i.ingredient.name}: {i.amount}' for i in rec.recipe_ingredients)
        text = (
            '🍳 Рецепт\n'
            f"{rec.name}\n"
            f"{rec.description}\n"
            f"⏱ {rec.cooking_time} мин\n"
            f"🔗 {rec.resource_url or '-'}\n"
            f"Ингредиенты:\n{ing}\n"
            "👇 Подтвердите, если всё верно"
        )
        await q.message.reply_text(text)
        return
    if action in ('fav', 'cart'):
        Model = Favorite if action == 'fav' else ShoppingCart
        inst = Model.query.filter_by(user_id=q.from_user.id, recipe_id=rid).first()
        if inst:
            db.session.delete(inst)
            msg = 'Удалено'
        else:
            db.session.add(Model(user_id=q.from_user.id, recipe_id=rid))
            msg = 'Добавлено'
        db.session.commit()
        await q.answer(msg)
        return
    if action == 'editurl':
        context.user_data.clear()
        context.user_data['edit_id'] = rid
        await q.message.reply_text('Введите новую ссылку:')
        return URL


async def add_recipe_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data.clear()
    await q.message.reply_text('Введите название рецепта:')
    return RECIPE_NAME


async def recipe_name_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['name'] = update.effective_message.text.strip()
    await update.effective_message.reply_text('Введите описание рецепта:')
    return RECIPE_DESC


async def recipe_desc_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['description'] = update.effective_message.text.strip()
    await update.effective_message.reply_text('Укажите время приготовления (минуты):')
    return COOK_TIME


async def cook_time_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.effective_message.text.strip()
    if not txt.isdigit():
        await update.effective_message.reply_text('Пожалуйста, введите число')
        return COOK_TIME
    context.user_data['time'] = int(txt)
    context.user_data['ings'] = []
    await update.effective_message.reply_text(
        'Добавьте ингредиент или нажмите ✅ Готово:',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton('✅ Готово', callback_data='ing_done')]
        ]),
    )
    return ING_NAME


async def ing_name_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q:
        await q.answer()
        if q.data == 'ing_done':
            if not context.user_data['ings']:
                await q.message.reply_text('Добавьте хотя бы один ингредиент')
                return ING_NAME
            await q.message.reply_text('Введите теги через запятую:')
            return TAGS
        name = q.data
    else:
        name = update.effective_message.text.strip()
    context.user_data['current_ing'] = name
    await update.effective_message.reply_text(f'Укажите количество для {name}:')
    return ING_QTY


async def ing_qty_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.effective_message.text.strip()
    if not txt.replace('.', '', 1).isdigit():
        await update.effective_message.reply_text('Пожалуйста, введите число')
        return ING_QTY
    name = context.user_data.pop('current_ing')
    context.user_data['ings'].append({'name': name, 'amount': txt})
    await update.effective_message.reply_text(
        f'Добавлено: {name} — {txt}',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton('✅ Готово', callback_data='ing_done')],
            [InlineKeyboardButton('➕ Добавить ещё', callback_data=name)],
        ]),
    )
    return ING_NAME


async def tags_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['tags'] = [t.strip() for t in update.effective_message.text.split(',')]
    await update.effective_message.reply_text(
        'Пришлите фото или нажмите Пропустить',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton('Пропустить', callback_data='skip_image')]
        ]),
    )
    return IMAGE


async def image_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        context.user_data['img'] = None if update.callback_query.data == 'skip_image' else None
    else:
        context.user_data['img'] = update.effective_message.photo[-1].file_id
    await update.effective_message.reply_text(
        'Введите ссылку на оригинал или нажмите Пропустить',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton('Пропустить', callback_data='skip_url')]
        ]),
    )
    return URL


async def url_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        context.user_data['url'] = None if update.callback_query.data == 'skip_url' else None
    else:
        context.user_data['url'] = update.effective_message.text.strip()

    data = context.user_data
    ing_list = '\n'.join(f"- {i['name']}: {i['amount']}" for i in data['ings'])
    summary = (
        f"Название: {data['name']}\n"
        f"Описание: {data['description']}\n"
        f"Время: {data['time']} мин\n"
        f"Ингредиенты:\n{ing_list}\n"
        f"Теги: {', '.join(data['tags'])}\n"
        f"Фото: {'есть' if data['img'] else 'нет'}\n"
        f"Ссылка: {data['url'] or 'нет'}"
    )
    await update.effective_message.reply_text(
        summary,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton('✅ Подтвердить', callback_data='do_confirm'),
                InlineKeyboardButton('❌ Отменить', callback_data='cancel'),
            ]
        ]),
    )
    return CONFIRM


async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = context.user_data
    r = Recipe(
        author_id=update.effective_user.id,
        name=data['name'],
        description=data['description'],
        cooking_time=data['time'],
        image_path=data.get('img'),
        resource_url=data.get('url'),
    )
    db.session.add(r)
    db.session.flush()
    for ing in data['ings']:
        obj = Ingredient.query.filter_by(name=ing['name']).first()
        if not obj:
            obj = Ingredient(name=ing['name'], measurement_unit='шт')
            db.session.add(obj)
            db.session.flush()
        db.session.add(RecipeIngredient(
            recipe_id=r.id, ingredient_id=obj.id, amount=ing['amount']
        ))
    for t in data['tags']:
        tg = Tag.query.filter_by(name=t).first()
        if not tg:
            tg = Tag(name=t, slug=t)
            db.session.add(tg)
            db.session.flush()
        db.session.add(TagInRecipe(recipe_id=r.id, tag_id=tg.id))
    db.session.commit()
    await q.message.reply_text('✅ Рецепт создан!')
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text('🚫 Операция отменена')
    else:
        await update.effective_message.reply_text('🚫 Операция отменена')
    return ConversationHandler.END


# Conversation handler
conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(add_recipe_start, pattern='^add_recipe$')],
    states={
        RECIPE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, recipe_name_handler)],
        RECIPE_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, recipe_desc_handler)],
        COOK_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, cook_time_handler)],
        ING_NAME: [
            CallbackQueryHandler(ing_name_handler, pattern='^ing_done$'),
            MessageHandler(filters.TEXT & ~filters.COMMAND, ing_name_handler),
        ],
        ING_QTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, ing_qty_handler)],
        TAGS: [MessageHandler(filters.TEXT & ~filters.COMMAND, tags_handler)],
        IMAGE: [
            MessageHandler(filters.PHOTO, image_handler),
            CallbackQueryHandler(image_handler, pattern='^skip_image$'),
        ],
        URL: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, url_handler),
            CallbackQueryHandler(url_handler, pattern='^skip_url$'),
        ],
        CONFIRM: [
            CallbackQueryHandler(confirm, pattern='^do_confirm$'),
            CallbackQueryHandler(cancel, pattern='^cancel$'),
        ],
    },
    fallbacks=[CallbackQueryHandler(cancel, pattern='^cancel$')],
)

if __name__ == '__main__':
    threading.Thread(target=lambda: app.run(use_reloader=False)).start()
    bot = ApplicationBuilder().token(TOKEN).build()
    bot.add_handler(CommandHandler('start', start_menu))
    bot.add_handler(CallbackQueryHandler(view_list, pattern='^view_list$'))
    bot.add_handler(CallbackQueryHandler(view_fav, pattern='^view_fav$'))
    bot.add_handler(CallbackQueryHandler(recipe_callback, pattern='^(detail|fav|cart|editurl)_'))
    bot.add_handler(conv)
    bot.run_polling()
