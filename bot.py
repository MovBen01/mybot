"""
Telegram Reseller Bot — Apple City
"""
import asyncio
import logging
import re
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
)

from config import config
from database import db
from product_manager import ProductManager, ChannelPoster
from parser import TelegramParser
from admin import admin_router
from ai_consultant import ask_groq
import runner

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
dp.include_router(admin_router)

product_manager = ProductManager()
channel_poster = ChannelPoster(bot)

ITEMS_PER_PAGE = 10

# ─────────────────────────────────────────────
# СОСТОЯНИЯ
# ─────────────────────────────────────────────

class UserState(StatesGroup):
    search      = State()   # ждём поисковый запрос
    ai_chat     = State()   # чат с ИИ консультантом
    contact_msg = State()   # ждём сообщение для менеджера


# ─────────────────────────────────────────────
# REPLY КЛАВИАТУРА (кнопки снизу)
# ─────────────────────────────────────────────

def main_reply_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛍 Каталог"),    KeyboardButton(text="🛒 Корзина")],
            [KeyboardButton(text="📦 Мои заказы"), KeyboardButton(text="🤖 Консультант")],
            [KeyboardButton(text="ℹ️ О магазине"),  KeyboardButton(text="🔄 Сбросить диалог")],
        ],
        resize_keyboard=True
    )


# ─────────────────────────────────────────────
# INLINE КЛАВИАТУРЫ
# ─────────────────────────────────────────────

def categories_keyboard():
    categories = db.get_categories()
    buttons = []
    for cat in categories:
        buttons.append([InlineKeyboardButton(
            text=f"{cat.get('emoji','📦')} {cat['name']}",
            callback_data=f"cat_{cat['id']}_0"
        )])
    buttons.append([InlineKeyboardButton(text="🔍 Поиск", callback_data="search")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def products_keyboard(products, cat_id, page, total):
    buttons = []
    start = page * ITEMS_PER_PAGE
    page_products = products[start:start + ITEMS_PER_PAGE]

    for p in page_products:
        price = f"{p['price_with_markup']:,.0f}".replace(',', ' ')
        name  = p['name'][:32] + '…' if len(p['name']) > 32 else p['name']
        buttons.append([InlineKeyboardButton(
            text=f"{name} — {price}₽",
            callback_data=f"product_{p['id']}_{cat_id}_{page}"
        )])

    total_pages = (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"cat_{cat_id}_{page-1}"))
    nav.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="noop"))
    if start + ITEMS_PER_PAGE < total:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"cat_{cat_id}_{page+1}"))
    if total_pages > 1:
        buttons.append(nav)

    buttons.append([InlineKeyboardButton(text="⬅️ К категориям", callback_data="back_catalog")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def product_detail_keyboard(product, cat_id, page):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Заказать",           callback_data=f"order_{product['id']}")],
        [InlineKeyboardButton(text="⬅️ Назад к списку",    callback_data=f"cat_{cat_id}_{page}")],
    ])


def admin_reply_keyboard(user_id, username):
    if username:
        # Есть username — ссылка работает всегда
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"💬 Написать @{username}",
                url=f"https://t.me/{username}"
            )],
        ])
    else:
        # Нет username — кнопка без ссылки (просто текст с ID)
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"👤 ID: {user_id} (нет username)",
                callback_data="noop"
            )],
        ])


# ─────────────────────────────────────────────
# КАРТОЧКА ТОВАРА
# ─────────────────────────────────────────────

CAT_EMOJI = {
    'iPhone': '📱', 'MacBook': '💻', 'iPad': '📟',
    'Apple Watch': '⌚', 'AirPods': '🎧', 'iMac / Mac': '🖥',
    'Samsung': '📲', 'Dyson': '🌀', 'PlayStation': '🎮',
    'Наушники': '🎵', 'Очки': '🕶', 'Аксессуары Apple': '🔌',
}

def format_product_card(product):
    price = f"{product['price_with_markup']:,.0f}".replace(',', ' ')
    cat   = product.get('category_name', '')
    emoji = CAT_EMOJI.get(cat, '📦')
    text = (
        f"{emoji} <b>{product['name']}</b>\n"
        f"{'─' * 30}\n"
        f"💰 <b>{price} ₽</b>\n"
        f"📂 {cat}\n"
    )
    if product.get('description'):
        text += f"📝 {product['description']}\n"
    text += f"{'─' * 30}\n👇 Нажмите <b>«Заказать»</b> — ответим в течение часа"
    return text


# ─────────────────────────────────────────────
# СТАРТ И ОСНОВНЫЕ КОМАНДЫ
# ─────────────────────────────────────────────

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await db.save_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    await db.log_message(message.from_user.id, "/start", "user")
    await message.answer(
        f"👋 Привет, <b>{message.from_user.first_name}</b>!\n\n"
        f"🛍 Добро пожаловать в <b>{config.SHOP_NAME}</b> — магазин техники по выгодным ценам.\n\n"
        "Используйте кнопки меню ниже 👇",
        parse_mode="HTML",
        reply_markup=main_reply_keyboard()
    )


@dp.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=main_reply_keyboard())


# ─────────────────────────────────────────────
# REPLY КНОПКИ
# ─────────────────────────────────────────────

@dp.message(F.text == "🛍 Каталог")
async def btn_catalog(message: types.Message):
    await db.save_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    cats = await asyncio.to_thread(db.get_categories)
    if not cats:
        await message.answer("Каталог загружается, попробуйте позже.")
        return
    await message.answer(
        "📦 <b>Каталог товаров</b>\n\nВыберите категорию:",
        parse_mode="HTML",
        reply_markup=categories_keyboard()
    )


@dp.message(F.text == "🛒 Корзина")
async def btn_cart(message: types.Message):
    await message.answer(
        "🛒 <b>Корзина</b>\n\n"
        "Найдите нужный товар в каталоге и нажмите «Заказать» — "
        "менеджер свяжется с вами для оформления заказа.",
        parse_mode="HTML",
        reply_markup=categories_keyboard()
    )


@dp.message(F.text == "📦 Мои заказы")
async def btn_orders(message: types.Message):
    orders = await asyncio.to_thread(db.get_user_orders, message.from_user.id)
    if not orders:
        await message.answer(
            "📦 У вас пока нет заказов.\n\nПерейдите в каталог чтобы выбрать товар!",
            reply_markup=categories_keyboard()
        )
        return

    text = "📦 <b>Ваши заказы:</b>\n\n"
    for i, o in enumerate(orders[-10:], 1):
        status_emoji = {"pending":"⏳","confirmed":"✅","completed":"📦","cancelled":"❌"}.get(o.get('status','pending'), '⏳')
        price = f"{o['total_price']:,.0f}".replace(',', ' ')
        text += f"{i}. {status_emoji} <b>{o['product_name']}</b>\n   💰 {price}₽\n\n"

    await message.answer(text, parse_mode="HTML")


@dp.message(F.text == "🤖 Консультант")
async def btn_consultant(message: types.Message, state: FSMContext):
    if not config.GROQ_API_KEY:
        await message.answer(
            "🤖 ИИ консультант временно недоступен.\n\n"
            "Напишите ваш вопрос — менеджер ответит! 🤝"
        )
        return
    await state.set_state(UserState.ai_chat)
    await state.update_data(ai_history=[])
    await message.answer(
        "🤖 <b>ИИ Консультант</b>\n\n"
        "Задайте любой вопрос о товарах — помогу выбрать!\n\n"
        "Примеры:\n"
        "• <i>Какой iPhone лучше взять до 100 000₽?</i>\n"
        "• <i>Чем отличается MacBook Air от Pro?</i>\n"
        "• <i>Есть ли AirPods в наличии?</i>\n\n"
        "Для выхода нажмите <b>🔄 Сбросить диалог</b>",
        parse_mode="HTML"
    )


@dp.message(F.text == "ℹ️ О магазине")
async def btn_about(message: types.Message):
    await message.answer(
        f"ℹ️ <b>{config.SHOP_NAME}</b>\n\n"
        "🏪 Магазин техники Apple и других брендов\n"
        "✅ Гарантия на все товары\n"
        "🚚 Доставка по всей России\n"
        "💬 Консультация и подбор техники\n\n"
        f"📞 Менеджер: {config.MANAGER_USERNAME}\n\n"
        "⏱ Работаем: Пн-Пт 10:00–20:30, Сб-Вс 10:00–19:00",
        parse_mode="HTML"
    )


@dp.message(F.text == "🔄 Сбросить диалог")
async def btn_reset(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🔄 Диалог сброшен. Чем могу помочь?",
        reply_markup=main_reply_keyboard()
    )


# ─────────────────────────────────────────────
# ИИ КОНСУЛЬТАНТ — обработка сообщений
# ─────────────────────────────────────────────

@dp.message(UserState.ai_chat)
async def ai_chat_message(message: types.Message, state: FSMContext):
    if not message.text or message.text.startswith("/"):
        await state.clear()
        await message.answer("Диалог с консультантом завершён.", reply_markup=main_reply_keyboard())
        return

    data = await state.get_data()
    history = data.get("ai_history", [])

    # Показываем что печатаем
    await bot.send_chat_action(message.chat.id, "typing")

    response = await ask_groq(message.text, history)

    # Сохраняем историю
    history.append({"role": "user",      "content": message.text})
    history.append({"role": "assistant", "content": response})
    await state.update_data(ai_history=history[-12:])

    await message.answer(
        f"🤖 {response}\n\n"
        "<i>Нажмите «🔄 Сбросить диалог» чтобы выйти</i>",
        parse_mode="HTML"
    )

    # Логируем вопрос клиента
    await db.log_message(message.from_user.id, f"[AI] {message.text}", "user")


# ─────────────────────────────────────────────
# КАТАЛОГ — inline навигация
# ─────────────────────────────────────────────

@dp.callback_query(F.data == "noop")
async def cb_noop(callback: types.CallbackQuery):
    await callback.answer()


@dp.callback_query(F.data.in_({"back_catalog", "categories", "catalog"}))
async def cb_categories(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "📦 <b>Категории товаров</b>\n\nВыберите категорию:",
        parse_mode="HTML",
        reply_markup=categories_keyboard()
    )


@dp.callback_query(F.data.startswith("cat_"))
async def cb_category_products(callback: types.CallbackQuery):
    await callback.answer()
    parts  = callback.data.split("_")
    cat_id = int(parts[1])
    page   = int(parts[2]) if len(parts) > 2 else 0

    cat, products = await asyncio.gather(
        asyncio.to_thread(db.get_category, cat_id),
        asyncio.to_thread(db.get_products_by_category, cat_id)
    )

    if not cat:
        await callback.message.edit_text("Категория не найдена.", reply_markup=categories_keyboard())
        return

    if not products:
        await callback.message.edit_text(
            f"{cat.get('emoji','📦')} <b>{cat['name']}</b>\n\nТоваров пока нет 😔",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ К категориям", callback_data="back_catalog")]
            ])
        )
        return

    total = len(products)
    start = page * ITEMS_PER_PAGE
    end   = min(start + ITEMS_PER_PAGE, total)

    await callback.message.edit_text(
        f"{cat.get('emoji','📦')} <b>{cat['name']}</b>\n"
        f"{'─' * 28}\n"
        f"Товаров: <b>{total}</b>  •  {start+1}–{end}\n\nВыберите товар 👇",
        parse_mode="HTML",
        reply_markup=products_keyboard(products, cat_id, page, total)
    )


@dp.callback_query(F.data.startswith("product_"))
async def cb_product_detail(callback: types.CallbackQuery):
    await callback.answer()
    parts      = callback.data.split("_")
    product_id = int(parts[1])
    cat_id     = int(parts[2]) if len(parts) > 2 else 0
    page       = int(parts[3]) if len(parts) > 3 else 0

    product = await asyncio.to_thread(db.get_product, product_id)
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return

    try:
        await callback.message.edit_text(
            format_product_card(product), parse_mode="HTML",
            reply_markup=product_detail_keyboard(product, cat_id, page)
        )
    except Exception:
        await callback.message.answer(
            format_product_card(product), parse_mode="HTML",
            reply_markup=product_detail_keyboard(product, cat_id, page)
        )


@dp.callback_query(F.data == "search")
async def cb_search(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(UserState.search)
    await callback.message.edit_text(
        "🔍 <b>Поиск товара</b>\n\n"
        "Введите запрос:\n"
        "• По названию: <i>iPhone 16 Pro</i>\n"
        "• По цене: <i>до 50000</i>\n"
        "• По диапазону: <i>от 30000 до 100000</i>",
        parse_mode="HTML"
    )


@dp.message(UserState.search)
async def process_search(message: types.Message, state: FSMContext):
    query = message.text.strip()
    await state.clear()
    await db.log_message(message.from_user.id, f"[SEARCH] {message.text}", "user")

    if query.startswith("/"):
        await message.answer("Поиск отменён.", reply_markup=main_reply_keyboard())
        return

    # Парсим цены
    price_from, price_to, text_query = None, None, query
    m_range = re.search(r'от\s+(\d+)\s+до\s+(\d+)', query, re.IGNORECASE)
    m_to    = re.search(r'до\s+(\d+)', query, re.IGNORECASE)
    m_from  = re.search(r'от\s+(\d+)', query, re.IGNORECASE)

    if m_range:
        price_from, price_to = int(m_range.group(1)), int(m_range.group(2))
        text_query = ""
    elif m_to:
        price_to   = int(m_to.group(1))
        text_query = re.sub(r'до\s+\d+', '', query).strip()
    elif m_from:
        price_from = int(m_from.group(1))
        text_query = re.sub(r'от\s+\d+', '', query).strip()

    products = await asyncio.to_thread(db.search_products, text_query, price_from, price_to)

    if not products:
        await message.answer(
            f"😔 По запросу «{query}» ничего не найдено.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔍 Новый поиск",   callback_data="search")],
                [InlineKeyboardButton(text="📦 Все категории", callback_data="back_catalog")],
            ])
        )
        return

    total = len(products)
    await message.answer(
        f"🔍 Найдено <b>{total}</b> товаров:",
        parse_mode="HTML",
        reply_markup=products_keyboard(products, 0, 0, total)
    )


# ─────────────────────────────────────────────
# ЗАКАЗ
# ─────────────────────────────────────────────

@dp.callback_query(F.data.startswith("order_"))
async def cb_order(callback: types.CallbackQuery):
    await callback.answer()
    product_id = int(callback.data.split("_")[1])
    product    = await asyncio.to_thread(db.get_product, product_id)
    user       = callback.from_user
    price      = f"{product['price_with_markup']:,.0f}".replace(',', ' ') if product else "—"

    # Уведомление администратору
    order_text = (
        f"🛒 <b>НОВЫЙ ЗАКАЗ!</b>\n"
        f"{'─' * 28}\n"
        f"👤 {user.full_name}\n"
        f"🆔 <code>{user.id}</code>  •  @{user.username or '—'}\n\n"
        f"📦 <b>{product['name'] if product else '—'}</b>\n"
        f"💰 {price} ₽\n"
        f"📂 {product['category_name'] if product else '—'}\n"
        f"{'─' * 28}"
    )
    # Кнопка для связи с клиентом
    if user.username:
        reply_kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=f"💬 Написать @{user.username}", url=f"https://t.me/{user.username}")
        ]])
    else:
        reply_kb = None  # без кнопки если нет username

    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id, order_text, parse_mode="HTML",
                reply_markup=reply_kb
            )
            logger.info(f"Order notification sent to admin {admin_id}")
        except Exception as e:
            logger.error(f"Cannot notify admin {admin_id}: {e}", exc_info=True)

    if product:
        await db.save_order(user.id, product_id, product['price_with_markup'])
    await db.log_message(user.id, f"[ORDER] {product['name'] if product else product_id}", "user")

    await callback.message.answer(
        "✅ <b>Заявка принята!</b>\n\n"
        "Менеджер свяжется с вами в ближайшее время. 🤝\n"
        "<i>Обычно отвечаем в течение 1–2 часов.</i>",
        parse_mode="HTML",
        reply_markup=main_reply_keyboard()
    )


# ─────────────────────────────────────────────
# ВСЕ ОСТАЛЬНЫЕ СООБЩЕНИЯ → пересылка админу
# ─────────────────────────────────────────────

@dp.message(F.text[0] != "/")
async def handle_user_message(message: types.Message, state: FSMContext):
    # Если в состоянии поиска или ИИ — не перехватываем
    current_state = await state.get_state()
    if current_state in (UserState.search, UserState.ai_chat):
        return

    await db.log_message(message.from_user.id, message.text or "[медиа]", "user")
    await db.save_user(message.from_user.id, message.from_user.username, message.from_user.full_name)

    user = message.from_user
    fwd_text = (
        f"💬 <b>Сообщение от клиента</b>\n"
        f"{'─' * 28}\n"
        f"👤 {user.full_name}  •  @{user.username or '—'}\n"
        f"🆔 <code>{user.id}</code>\n\n"
        f"📝 {message.text or '[медиа]'}"
    )
    notified = False
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id, fwd_text, parse_mode="HTML",
                reply_markup=admin_reply_keyboard(user.id, user.username)
            )
            notified = True
        except Exception as e:
            logger.error(f"Cannot forward to admin {admin_id}: {e}")

    if not notified:
        logger.error("Could not notify ANY admin — check ADMIN_IDS!")

    await message.answer(
        "💬 Сообщение получено! Менеджер ответит вам в ближайшее время. 🤝",
        reply_markup=main_reply_keyboard()
    )


# ─────────────────────────────────────────────
# ЗАПУСК
# ─────────────────────────────────────────────

parser = TelegramParser(bot, channel_poster, product_manager)
runner.set_parser(parser)


async def main():
    db.init()
    asyncio.create_task(parser.start_monitoring())
    logger.info("Bot started!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
