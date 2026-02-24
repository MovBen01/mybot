"""
Telegram Reseller Bot
Парсит товары из источника, добавляет наценку по категориям, постит в канал
"""

import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import config
from database import db
from product_manager import ProductManager, ChannelPoster
from parser import TelegramParser
from admin import admin_router

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
dp.include_router(admin_router)

product_manager = ProductManager()
channel_poster = ChannelPoster(bot)


class SearchState(StatesGroup):
    waiting_query = State()


def main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛍️ Каталог товаров", callback_data="catalog")],
        [InlineKeyboardButton(text="🔍 Поиск товара", callback_data="search")],
        [InlineKeyboardButton(text="📦 Категории", callback_data="categories")],
        [InlineKeyboardButton(text="📞 Связаться с менеджером", callback_data="contact")],
    ])


def categories_keyboard():
    categories = db.get_categories()
    buttons = []
    for cat in categories:
        buttons.append([InlineKeyboardButton(
            text=f"{cat['emoji']} {cat['name']}",
            callback_data=f"cat_{cat['id']}"
        )])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def products_keyboard(products, category_id=None):
    buttons = []
    for i, p in enumerate(products[:10]):
        buttons.append([InlineKeyboardButton(
            text=f"{p['name'][:35]}... — {p['price_with_markup']}₽" if len(p['name']) > 35
                 else f"{p['name']} — {p['price_with_markup']}₽",
            callback_data=f"product_{p['id']}"
        )])
    back = f"cat_{category_id}" if category_id else "categories"
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=back)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def product_detail_keyboard(product, category_id=None):
    back = f"cat_{category_id}" if category_id else "catalog"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📞 Заказать / Узнать детали", callback_data=f"order_{product['id']}")],
        [InlineKeyboardButton(text="⬅️ Назад к категории", callback_data=back)],
    ])


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await db.save_user(
        user_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name
    )
    await db.log_message(message.from_user.id, message.text, "user")
    await message.answer(
        f"👋 Привет, {message.from_user.first_name}!\n\n"
        "🛍️ Добро пожаловать в наш магазин!\n"
        "Здесь вы можете просмотреть актуальный ассортимент и цены.\n\n"
        "Выберите действие:",
        reply_markup=main_keyboard()
    )


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await db.log_message(message.from_user.id, message.text, "user")
    text = (
        "📋 <b>Как пользоваться ботом:</b>\n\n"
        "• <b>Каталог</b> — все доступные товары\n"
        "• <b>Категории</b> — товары по разделам\n"
        "• <b>Поиск</b> — найти конкретный товар\n"
        "• <b>Заказ</b> — нажмите «Заказать» под товаром\n\n"
        "Менеджер свяжется с вами в течение рабочего дня! 🤝"
    )
    await message.answer(text, parse_mode="HTML")


@dp.callback_query(F.data == "back_main")
@dp.callback_query(F.data == "main_menu")
async def cb_main_menu(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "Главное меню. Выберите действие:",
        reply_markup=main_keyboard()
    )


@dp.callback_query(F.data == "categories")
@dp.callback_query(F.data == "catalog")
async def cb_categories(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "📦 <b>Категории товаров:</b>\n\nВыберите категорию для просмотра:",
        parse_mode="HTML",
        reply_markup=categories_keyboard()
    )


@dp.callback_query(F.data.startswith("cat_"))
async def cb_category_products(callback: types.CallbackQuery):
    cat_id = int(callback.data.split("_")[1])
    cat = db.get_category(cat_id)
    products = db.get_products_by_category(cat_id)

    if not products:
        await callback.answer("В этой категории пока нет товаров", show_alert=True)
        return

    text = f"{'emoji' in cat and cat.get('emoji', '') or ''} <b>{cat['name']}</b>\n\n"
    text += f"Найдено товаров: {len(products)}\nВыберите товар:"

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=products_keyboard(products, cat_id)
    )


@dp.callback_query(F.data.startswith("product_"))
async def cb_product_detail(callback: types.CallbackQuery):
    product_id = int(callback.data.split("_")[1])
    product = db.get_product(product_id)

    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return

    text = (
        f"<b>{product['name']}</b>\n\n"
        f"💰 <b>Цена: {product['price_with_markup']}₽</b>\n"
        f"📦 Категория: {product['category_name']}\n"
    )
    if product.get('description'):
        text += f"\n📝 {product['description']}\n"

    if product.get('photo_id'):
        await callback.message.answer_photo(
            photo=product['photo_id'],
            caption=text,
            parse_mode="HTML",
            reply_markup=product_detail_keyboard(product, product.get('category_id'))
        )
        await callback.message.delete()
    else:
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=product_detail_keyboard(product, product.get('category_id'))
        )


@dp.callback_query(F.data == "search")
async def cb_search(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(SearchState.waiting_query)
    await callback.message.edit_text(
        "🔍 Введите название товара для поиска:\n\n(Отправьте /cancel для отмены)"
    )


@dp.message(SearchState.waiting_query)
async def process_search(message: types.Message, state: FSMContext):
    query = message.text.strip()
    await state.clear()
    await db.log_message(message.from_user.id, message.text, "user")

    if query.startswith("/"):
        await message.answer("Поиск отменён.", reply_markup=main_keyboard())
        return

    products = db.search_products(query)

    if not products:
        await message.answer(
            f"😔 По запросу «{query}» ничего не найдено.\nПопробуйте другой запрос.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔍 Попробовать ещё раз", callback_data="search")],
                [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")],
            ])
        )
        return

    await message.answer(
        f"🔍 По запросу «{query}» найдено {len(products)} товаров:",
        reply_markup=products_keyboard(products)
    )


@dp.callback_query(F.data.startswith("order_"))
async def cb_order(callback: types.CallbackQuery):
    product_id = int(callback.data.split("_")[1])
    product = db.get_product(product_id)
    user = callback.from_user

    # Уведомление администраторам
    order_text = (
        f"🛒 <b>Новый запрос на заказ!</b>\n\n"
        f"👤 Пользователь: {user.full_name}\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"📱 Username: @{user.username or 'нет'}\n\n"
        f"📦 Товар: {product['name']}\n"
        f"💰 Цена: {product['price_with_markup']}₽\n"
        f"🗂 Категория: {product['category_name']}"
    )

    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                order_text,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text=f"📩 Ответить клиенту",
                        url=f"tg://user?id={user.id}"
                    )]
                ])
            )
        except Exception as e:
            logger.error(f"Cannot notify admin {admin_id}: {e}")

    await db.save_order(user.id, product_id, product['price_with_markup'])
    await db.log_message(user.id, f"[ORDER REQUEST] {product['name']}", "user")

    await callback.message.answer(
        "✅ <b>Отлично!</b>\n\n"
        "Ваш запрос принят! Менеджер свяжется с вами в ближайшее время для уточнения деталей заказа. 🤝\n\n"
        "<i>Обычно отвечаем в течение 1-2 часов в рабочее время.</i>",
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )
    await callback.answer()


@dp.callback_query(F.data == "contact")
async def cb_contact(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "📞 <b>Связаться с нами:</b>\n\n"
        f"Напишите менеджеру напрямую или задайте вопрос прямо здесь — мы ответим!\n\n"
        f"📧 Менеджер: {config.MANAGER_USERNAME or 'свяжитесь через бота'}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")],
        ])
    )


# Обработка всех остальных сообщений — пересылка администраторам
@dp.message(F.text[0] != "/")
async def handle_user_message(message: types.Message):
    await db.log_message(message.from_user.id, message.text or "[медиа]", "user")
    await db.save_user(message.from_user.id, message.from_user.username, message.from_user.full_name)

    # Пересылаем сообщение администраторам
    user = message.from_user
    fwd_text = (
        f"💬 <b>Сообщение от пользователя</b>\n"
        f"👤 {user.full_name} | @{user.username or 'нет'} | ID: <code>{user.id}</code>\n\n"
        f"📝 {message.text or '[медиа-контент]'}"
    )

    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                fwd_text,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text="📩 Ответить",
                        url=f"tg://user?id={user.id}"
                    )]
                ])
            )
        except Exception as e:
            logger.error(f"Cannot forward to admin {admin_id}: {e}")

    await message.answer(
        "Спасибо за сообщение! Менеджер свяжется с вами в ближайшее время. 🤝",
        reply_markup=main_keyboard()
    )


# Глобальный парсер — доступен из admin.py
parser = TelegramParser(bot, channel_poster, product_manager)


async def main():
    # Инициализация БД
    db.init()

    # Запуск парсера в фоне
    asyncio.create_task(parser.start_monitoring())

    logger.info("Bot started!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
