"""
Админ-панель бота
Команды только для администраторов (из config.ADMIN_IDS)
"""
import logging
from aiogram import Router, Bot, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import config
from database import db
from product_manager import ProductManager, ChannelPoster

logger = logging.getLogger(__name__)
admin_router = Router()
product_manager = ProductManager()


def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


def admin_check(func):
    """Декоратор проверки прав"""
    async def wrapper(message_or_callback, *args, **kwargs):
        user_id = message_or_callback.from_user.id
        if not is_admin(user_id):
            if hasattr(message_or_callback, 'answer'):
                await message_or_callback.answer("⛔ Нет доступа")
            return
        return await func(message_or_callback, *args, **kwargs)
    return wrapper


class AdminStates(StatesGroup):
    # Добавление товара
    add_product_name = State()
    add_product_price = State()
    add_product_category = State()
    add_product_description = State()
    add_product_photo = State()
    # Изменение наценки
    set_markup_category = State()
    set_markup_value = State()
    # Рассылка
    broadcast_text = State()


def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить товар", callback_data="adm_add_product")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="adm_stats")],
        [InlineKeyboardButton(text="💬 Последние сообщения", callback_data="adm_messages")],
        [InlineKeyboardButton(text="🛒 Заказы", callback_data="adm_orders")],
        [InlineKeyboardButton(text="🏷️ Изменить наценки", callback_data="adm_markups")],
        [InlineKeyboardButton(text="📢 Опубликовать все товары", callback_data="adm_post_all")],
        [InlineKeyboardButton(text="📣 Рассылка", callback_data="adm_broadcast")],
    ])


@admin_router.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "🔐 <b>Панель администратора</b>\n\nВыберите действие:",
        parse_mode="HTML",
        reply_markup=admin_keyboard()
    )


# ---- СТАТИСТИКА ----

@admin_router.callback_query(F.data == "adm_stats")
async def adm_stats(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return

    users_count = db.get_users_count()
    products = db.get_all_products()
    orders = db.get_orders(limit=1000)
    categories = db.get_categories()

    text = (
        f"📊 <b>Статистика магазина</b>\n\n"
        f"👤 Пользователей: <b>{users_count}</b>\n"
        f"📦 Товаров: <b>{len(products)}</b>\n"
        f"📁 Категорий: <b>{len(categories)}</b>\n"
        f"🛒 Заказов: <b>{len(orders)}</b>\n\n"
        f"<b>Наценки по категориям:</b>\n"
    )
    for cat in categories:
        text += f"• {cat['name']}: {cat['markup_percent']}%\n"

    await callback.message.edit_text(
        text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm_back")]
        ])
    )


# ---- СООБЩЕНИЯ ----

@admin_router.callback_query(F.data == "adm_messages")
async def adm_messages(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return

    messages = db.get_all_messages(limit=30)
    if not messages:
        await callback.answer("Сообщений нет", show_alert=True)
        return

    text = "💬 <b>Последние сообщения:</b>\n\n"
    for msg in messages[:20]:
        direction = "👤" if msg['direction'] == 'user' else "🤖"
        user_info = f"@{msg['username']}" if msg['username'] else f"ID:{msg['user_id']}"
        text += f"{direction} <b>{user_info}</b>: {msg['text'][:80]}\n"
        text += f"   <i>{msg['created_at']}</i>\n\n"

    await callback.message.edit_text(
        text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm_back")]
        ])
    )


# ---- ЗАКАЗЫ ----

@admin_router.callback_query(F.data == "adm_orders")
async def adm_orders(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return

    orders = db.get_orders(limit=20)
    if not orders:
        await callback.answer("Заказов нет", show_alert=True)
        return

    text = "🛒 <b>Последние заказы:</b>\n\n"
    for order in orders:
        user_info = f"@{order['username']}" if order['username'] else f"ID:{order['user_id']}"
        text += (
            f"#{order['id']} | {user_info}\n"
            f"📦 {order['product_name']}\n"
            f"💰 {order['price']}₽ | {order['status']}\n"
            f"🕐 {order['created_at']}\n\n"
        )

    await callback.message.edit_text(
        text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm_back")]
        ])
    )


# ---- ДОБАВЛЕНИЕ ТОВАРА ----

@admin_router.callback_query(F.data == "adm_add_product")
async def adm_add_product_start(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(AdminStates.add_product_name)
    await callback.message.edit_text(
        "➕ <b>Добавление нового товара</b>\n\nВведите название товара:",
        parse_mode="HTML"
    )


@admin_router.message(AdminStates.add_product_name)
async def adm_product_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(AdminStates.add_product_price)
    await message.answer("💰 Введите закупочную цену (число):")


@admin_router.message(AdminStates.add_product_price)
async def adm_product_price(message: types.Message, state: FSMContext):
    try:
        price = float(message.text.replace(',', '.'))
        await state.update_data(price=price)
        await state.set_state(AdminStates.add_product_category)

        cats = db.get_categories()
        cats_text = "\n".join([f"• {c['name']} ({c['markup_percent']}%)" for c in cats])
        await message.answer(
            f"📦 Введите категорию товара:\n\n<b>Существующие категории:</b>\n{cats_text}",
            parse_mode="HTML"
        )
    except ValueError:
        await message.answer("❌ Введите корректное число (например: 1500 или 1500.50)")


@admin_router.message(AdminStates.add_product_category)
async def adm_product_category(message: types.Message, state: FSMContext):
    await state.update_data(category=message.text)
    await state.set_state(AdminStates.add_product_description)
    await message.answer("📝 Введите описание товара (или отправьте '-' чтобы пропустить):")


@admin_router.message(AdminStates.add_product_description)
async def adm_product_description(message: types.Message, state: FSMContext):
    description = None if message.text == '-' else message.text
    await state.update_data(description=description)
    await state.set_state(AdminStates.add_product_photo)
    await message.answer("🖼️ Отправьте фото товара (или '-' чтобы пропустить):")


@admin_router.message(AdminStates.add_product_photo)
async def adm_product_photo(message: types.Message, state: FSMContext):
    photo_id = None
    if message.photo:
        photo_id = message.photo[-1].file_id
    elif message.text != '-':
        photo_id = None

    data = await state.get_data()
    await state.clear()

    # Сохраняем товар
    from parser import ManualParser
    product_id = ManualParser.add_product(
        name=data['name'],
        price=data['price'],
        category=data['category'],
        description=data.get('description'),
        photo_id=photo_id
    )
    product = db.get_product(product_id)

    markup_pct = product['markup_percent']
    final_price = product['price_with_markup']

    await message.answer(
        f"✅ <b>Товар добавлен!</b>\n\n"
        f"📦 {product['name']}\n"
        f"💰 Закупка: {data['price']}₽\n"
        f"🏷️ Наценка: {markup_pct}%\n"
        f"💵 Цена продажи: {final_price}₽\n"
        f"📁 Категория: {product['category_name']}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 Опубликовать в канал", callback_data=f"adm_post_{product_id}")],
            [InlineKeyboardButton(text="⬅️ В меню", callback_data="adm_back")],
        ])
    )


# ---- ПУБЛИКАЦИЯ ОДНОГО ТОВАРА ----

@admin_router.callback_query(F.data.startswith("adm_post_"))
async def adm_post_product(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return

    if callback.data == "adm_post_all":
        products = db.get_all_products()
        await callback.answer(f"Публикуем {len(products)} товаров...")
        poster = ChannelPoster(callback.bot if hasattr(callback, 'bot') else None)
        # Нужен bot instance — передаём через глобальный доступ
        from bot import bot as main_bot, channel_poster as main_poster
        await main_poster.post_all_pending(products)
        await callback.message.answer(f"✅ Опубликовано товаров в канал!")
        return

    product_id = int(callback.data.split("_")[-1])
    product = db.get_product(product_id)

    from bot import channel_poster as main_poster
    success = await main_poster.post_product(product)
    if success:
        db.save_channel_post(product_id, 0)
        await callback.answer("✅ Опубликовано!")
    else:
        await callback.answer("❌ Ошибка публикации", show_alert=True)


# ---- НАЦЕНКИ ----

@admin_router.callback_query(F.data == "adm_markups")
async def adm_markups(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return

    cats = db.get_categories()
    buttons = []
    for cat in cats:
        buttons.append([InlineKeyboardButton(
            text=f"{cat['name']} — {cat['markup_percent']}%",
            callback_data=f"adm_markup_{cat['id']}"
        )])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="adm_back")])

    await callback.message.edit_text(
        "🏷️ <b>Наценки по категориям</b>\n\nНажмите на категорию чтобы изменить:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


@admin_router.callback_query(F.data.startswith("adm_markup_"))
async def adm_markup_select(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return

    cat_id = int(callback.data.split("_")[-1])
    cat = db.get_category(cat_id)
    await state.set_state(AdminStates.set_markup_value)
    await state.update_data(cat_id=cat_id)

    await callback.message.edit_text(
        f"🏷️ Категория: <b>{cat['name']}</b>\n"
        f"Текущая наценка: <b>{cat['markup_percent']}%</b>\n\n"
        f"Введите новый процент наценки (например: 25):",
        parse_mode="HTML"
    )


@admin_router.message(AdminStates.set_markup_value)
async def adm_markup_set(message: types.Message, state: FSMContext):
    try:
        new_markup = float(message.text.replace(',', '.'))
        data = await state.get_data()
        await state.clear()

        db.update_category_markup(data['cat_id'], new_markup)
        cat = db.get_category(data['cat_id'])

        await message.answer(
            f"✅ Наценка для категории <b>{cat['name']}</b> обновлена: <b>{new_markup}%</b>",
            parse_mode="HTML",
            reply_markup=admin_keyboard()
        )
    except ValueError:
        await message.answer("❌ Введите корректное число")


# ---- ПУБЛИКАЦИЯ ВСЕХ ----

@admin_router.callback_query(F.data == "adm_post_all")
async def adm_post_all(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return

    products = db.get_all_products()
    unpublished = [p for p in products if not db.is_product_posted(p['id'])]

    await callback.answer(f"Публикуем {len(unpublished)} товаров...", show_alert=True)

    from bot import channel_poster as main_poster
    await main_poster.post_all_pending(unpublished)

    await callback.message.answer(
        f"✅ Публикация завершена! Опубликовано товаров: {len(unpublished)}"
    )


# ---- РАССЫЛКА ----

@admin_router.callback_query(F.data == "adm_broadcast")
async def adm_broadcast_start(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(AdminStates.broadcast_text)
    await callback.message.edit_text(
        "📣 Введите текст для рассылки всем пользователям:\n\n(Поддерживается HTML: <b>жирный</b>, <i>курсив</i>)"
    )


@admin_router.message(AdminStates.broadcast_text)
async def adm_broadcast_send(message: types.Message, state: FSMContext):
    await state.clear()
    from bot import bot as main_bot

    users = db.get_all_users()
    success = 0
    fail = 0

    for user in users:
        try:
            await main_bot.send_message(
                user['id'],
                f"📢 <b>Сообщение от магазина:</b>\n\n{message.text}",
                parse_mode="HTML"
            )
            success += 1
        except Exception:
            fail += 1

    await message.answer(
        f"✅ Рассылка завершена!\n"
        f"📨 Доставлено: {success}\n"
        f"❌ Ошибок: {fail}",
        reply_markup=admin_keyboard()
    )


# ---- НАЗАД ----

@admin_router.callback_query(F.data == "adm_back")
async def adm_back(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    await callback.message.edit_text(
        "🔐 <b>Панель администратора</b>\n\nВыберите действие:",
        parse_mode="HTML",
        reply_markup=admin_keyboard()
    )


# ---- ПРИНУДИТЕЛЬНЫЙ ПОСТИНГ ----

@admin_router.message(Command("post_now"))
async def cmd_post_now(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("⏳ Парсю канал и готовлю прайс-лист...")
    try:
        from bot import bot as main_bot, channel_poster as main_poster, product_manager as pm
        from parser import TelegramWebParser
        # Создаём парсер без роутера — просто для вызова методов
        parser = TelegramWebParser.__new__(TelegramWebParser)
        parser.bot = main_bot
        parser.channel_poster = main_poster
        parser.product_manager = pm
        await parser._fetch_and_save()
        await parser._post_price_list()
        await message.answer("✅ Прайс-лист опубликован в канал!")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@admin_router.message(Command("whoami"))
async def cmd_whoami(message: types.Message):
    """Показывает твой ID — для отладки"""
    await message.answer(
        f"👤 Твой ID: <code>{message.from_user.id}</code>\n"
        f"🔐 Ты админ: {'✅ Да' if is_admin(message.from_user.id) else '❌ Нет'}\n"
        f"📋 ADMIN_IDS в конфиге: <code>{config.ADMIN_IDS}</code>",
        parse_mode="HTML"
    )
