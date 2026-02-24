"""
Telegram Reseller Bot
"""

import asyncio
import logging
import re
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import config
from database import db
from product_manager import ProductManager, ChannelPoster
from parser import TelegramParser
from admin import admin_router
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
# КЛАВИАТУРЫ
# ─────────────────────────────────────────────

def main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛍 Каталог товаров",        callback_data="catalog")],
        [InlineKeyboardButton(text="🔍 Поиск товара",           callback_data="search")],
        [InlineKeyboardButton(text="📦 Категории",              callback_data="categories")],
        [InlineKeyboardButton(text="📞 Связаться с менеджером", callback_data="contact")],
    ])


def categories_keyboard():
    categories = db.get_categories()
    buttons = []
    for cat in categories:
        buttons.append([InlineKeyboardButton(
            text=f"{cat.get('emoji','📦')} {cat['name']}",
            callback_data=f"cat_{cat['id']}_0"
        )])
    buttons.append([InlineKeyboardButton(text="⬅️ Главное меню", callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def products_keyboard(products, cat_id, page, total):
    buttons = []
    start = page * ITEMS_PER_PAGE
    page_products = products[start:start + ITEMS_PER_PAGE]

    for p in page_products:
        price = f"{p['price_with_markup']:,.0f}".replace(',', ' ')
        name = p['name'][:32] + '…' if len(p['name']) > 32 else p['name']
        buttons.append([InlineKeyboardButton(
            text=f"{name} — {price}₽",
            callback_data=f"product_{p['id']}_{cat_id}_{page}"
        )])

    # Пагинация
    nav = []
    total_pages = (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"cat_{cat_id}_{page-1}"))
    nav.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="noop"))
    if start + ITEMS_PER_PAGE < total:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"cat_{cat_id}_{page+1}"))
    if total_pages > 1:
        buttons.append(nav)

    buttons.append([InlineKeyboardButton(text="⬅️ К категориям", callback_data="categories")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def product_detail_keyboard(product, cat_id, page):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Заказать / Узнать детали", callback_data=f"order_{product['id']}")],
        [InlineKeyboardButton(text="⬅️ Назад к списку",           callback_data=f"cat_{cat_id}_{page}")],
    ])


def admin_reply_keyboard(user_id, username):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"💬 Написать клиенту @{username or user_id}",
            url=f"tg://user?id={user_id}"
        )],
    ])


# ─────────────────────────────────────────────
# КАРТОЧКА ТОВАРА
# ─────────────────────────────────────────────

def format_product_card(product):
    price = f"{product['price_with_markup']:,.0f}".replace(',', ' ')
    cat = product.get('category_name', '')
    cat_emoji = {
        'iPhone': '📱', 'MacBook': '💻', 'iPad': '📟',
        'Apple Watch': '⌚', 'AirPods': '🎧', 'iMac / Mac': '🖥',
        'Samsung': '📲', 'Dyson': '🌀', 'PlayStation': '🎮',
        'Наушники': '🎵', 'Очки': '🕶', 'Аксессуары Apple': '🔌',
    }.get(cat, '📦')

    text = (
        f"{cat_emoji} <b>{product['name']}</b>\n"
        f"{'─' * 30}\n"
        f"💰 <b>Цена: {price} ₽</b>\n"
        f"📂 {cat}\n"
    )
    if product.get('description'):
        text += f"\n📝 {product['description']}\n"
    text += f"\n{'─' * 30}\n👇 Нажмите <b>«Заказать»</b> — ответим в течение часа"
    return text


# ─────────────────────────────────────────────
# СОСТОЯНИЯ
# ─────────────────────────────────────────────

class SearchState(StatesGroup):
    waiting_query = State()


# ─────────────────────────────────────────────
# ХЕНДЛЕРЫ
# ─────────────────────────────────────────────

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await db.save_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    await db.log_message(message.from_user.id, message.text, "user")
    await message.answer(
        f"👋 Привет, <b>{message.from_user.first_name}</b>!\n\n"
        "🛍 Добро пожаловать в <b>Apple City</b> — магазин техники по выгодным ценам.\n\n"
        "Выберите раздел:",
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "📋 <b>Как пользоваться ботом:</b>\n\n"
        "• <b>Каталог</b> — все товары по категориям\n"
        "• <b>Поиск</b> — по названию или цене:\n"
        "  <i>iPhone 16</i>  •  <i>до 50000</i>  •  <i>от 30000 до 80000</i>\n"
        "• <b>Заказать</b> — менеджер свяжется с вами\n\n"
        "⏱ Отвечаем в течение 1–2 часов в рабочее время.",
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "noop")
async def cb_noop(callback: types.CallbackQuery):
    await callback.answer()


@dp.callback_query(F.data.in_({"back_main", "main_menu"}))
async def cb_main_menu(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.edit_text("🏠 Главное меню. Выберите раздел:", reply_markup=main_keyboard())


@dp.callback_query(F.data.in_({"categories", "catalog"}))
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
    parts = callback.data.split("_")
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
                [InlineKeyboardButton(text="⬅️ К категориям", callback_data="categories")]
            ])
        )
        return

    total = len(products)
    start = page * ITEMS_PER_PAGE
    end   = min(start + ITEMS_PER_PAGE, total)

    text = (
        f"{cat.get('emoji','📦')} <b>{cat['name']}</b>\n"
        f"{'─' * 28}\n"
        f"Товаров: <b>{total}</b>  •  Показано: {start+1}–{end}\n\n"
        f"Выберите товар 👇"
    )
    await callback.message.edit_text(
        text, parse_mode="HTML",
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

    text = format_product_card(product)
    try:
        await callback.message.edit_text(
            text, parse_mode="HTML",
            reply_markup=product_detail_keyboard(product, cat_id, page)
        )
    except Exception:
        await callback.message.answer(
            text, parse_mode="HTML",
            reply_markup=product_detail_keyboard(product, cat_id, page)
        )


@dp.callback_query(F.data == "search")
async def cb_search(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(SearchState.waiting_query)
    await callback.message.edit_text(
        "🔍 <b>Поиск товара</b>\n\n"
        "Введите запрос:\n"
        "• По названию: <i>iPhone 16 Pro</i>\n"
        "• По цене: <i>до 50000</i>\n"
        "• По диапазону: <i>от 30000 до 100000</i>\n\n"
        "/cancel — отмена",
        parse_mode="HTML"
    )


@dp.message(SearchState.waiting_query)
async def process_search(message: types.Message, state: FSMContext):
    query = message.text.strip()
    await state.clear()
    await db.log_message(message.from_user.id, message.text, "user")

    if query.startswith("/"):
        await message.answer("Поиск отменён.", reply_markup=main_keyboard())
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
            f"😔 По запросу «{query}» ничего не найдено.\nПопробуйте другой запрос.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔍 Новый поиск",   callback_data="search")],
                [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")],
            ])
        )
        return

    total = len(products)
    price_hint = ""
    if price_from and price_to:
        price_hint = f" (от {price_from:,} до {price_to:,} ₽)".replace(',', ' ')
    elif price_to:
        price_hint = f" (до {price_to:,} ₽)".replace(',', ' ')
    elif price_from:
        price_hint = f" (от {price_from:,} ₽)".replace(',', ' ')

    await message.answer(
        f"🔍 Найдено <b>{total}</b> товаров{price_hint}:",
        parse_mode="HTML",
        reply_markup=products_keyboard(products, 0, 0, total)
    )


@dp.callback_query(F.data.startswith("order_"))
async def cb_order(callback: types.CallbackQuery):
    await callback.answer()
    product_id = int(callback.data.split("_")[1])
    product    = await asyncio.to_thread(db.get_product, product_id)
    user       = callback.from_user
    price      = f"{product['price_with_markup']:,.0f}".replace(',', ' ') if product else "—"

    order_text = (
        f"🛒 <b>НОВЫЙ ЗАКАЗ!</b>\n"
        f"{'─' * 28}\n"
        f"👤 {user.full_name}\n"
        f"🆔 <code>{user.id}</code>  •  @{user.username or '—'}\n\n"
        f"📦 <b>{product['name'] if product else '—'}</b>\n"
        f"💰 {price} ₽\n"
        f"📂 {product['category_name'] if product else '—'}\n"
        f"{'─' * 28}\n"
        f"👇 Нажми кнопку чтобы написать клиенту"
    )
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id, order_text, parse_mode="HTML",
                reply_markup=admin_reply_keyboard(user.id, user.username)
            )
        except Exception as e:
            logger.error(f"Cannot notify admin {admin_id}: {e}")

    if product:
        await db.save_order(user.id, product_id, product['price_with_markup'])
    await db.log_message(user.id, f"[ORDER] {product['name'] if product else product_id}", "user")

    await callback.message.answer(
        "✅ <b>Заявка принята!</b>\n\n"
        "Менеджер свяжется с вами в ближайшее время. 🤝\n\n"
        "<i>Обычно отвечаем в течение 1–2 часов в рабочее время.</i>",
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )


@dp.callback_query(F.data == "contact")
async def cb_contact(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "📞 <b>Связаться с нами</b>\n\n"
        f"Менеджер: {config.MANAGER_USERNAME or 'напишите нам прямо здесь'}\n\n"
        "Или просто напишите сообщение — мы всё видим и ответим! 👇",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")],
        ])
    )


@dp.message(F.text[0] != "/")
async def handle_user_message(message: types.Message):
    await db.log_message(message.from_user.id, message.text or "[медиа]", "user")
    await db.save_user(message.from_user.id, message.from_user.username, message.from_user.full_name)

    user = message.from_user
    fwd_text = (
        f"💬 <b>Сообщение от клиента</b>\n"
        f"{'─' * 28}\n"
        f"👤 {user.full_name}  •  @{user.username or '—'}\n"
        f"🆔 <code>{user.id}</code>\n\n"
        f"📝 {message.text or '[медиа-контент]'}"
    )
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id, fwd_text, parse_mode="HTML",
                reply_markup=admin_reply_keyboard(user.id, user.username)
            )
        except Exception as e:
            logger.error(f"Cannot forward to admin {admin_id}: {e}")

    await message.answer(
        "💬 Сообщение получено! Менеджер ответит вам в ближайшее время. 🤝",
        reply_markup=main_keyboard()
    )


# Глобальный парсер
parser = TelegramParser(bot, channel_poster, product_manager)
runner.set_parser(parser)


async def main():
    db.init()
    asyncio.create_task(parser.start_monitoring())
    logger.info("Bot started!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
