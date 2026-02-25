"""
ИИ консультант на Groq — отвечает на вопросы о товарах магазина
"""
import logging
import aiohttp
import ssl
import json
from database import db
from config import config

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты — вежливый консультант магазина {shop_name}. 
Помогаешь клиентам выбрать технику и отвечаешь на вопросы о товарах.

ТОВАРЫ В НАЛИЧИИ:
{products}

ПРАВИЛА:
- Отвечай коротко и по делу (максимум 3-4 предложения)
- Если клиент спрашивает о конкретном товаре — назови цену из каталога
- Если товара нет в списке — честно скажи что его нет, предложи похожее
- Для заказа направляй к кнопке «Каталог» или «Заказать»
- Отвечай только на русском языке
- Не придумывай характеристики которых нет в каталоге
"""


def _build_product_list() -> str:
    """Строит краткий список товаров для промпта"""
    try:
        products = db.get_all_products()
        if not products:
            return "Каталог пока загружается."
        
        by_cat = {}
        for p in products:
            cat = p['category_name']
            if cat not in by_cat:
                by_cat[cat] = []
            if len(by_cat[cat]) < 8:  # максимум 8 товаров на категорию в промпте
                price = f"{p['price_with_markup']:,.0f}".replace(',', ' ')
                by_cat[cat].append(f"  • {p['name']} — {price}₽")
        
        lines = []
        for cat, items in by_cat.items():
            lines.append(f"{cat}:")
            lines.extend(items)
        return "\n".join(lines[:150])  # ограничиваем размер
    except Exception as e:
        logger.error(f"Error building product list: {e}")
        return "Каталог временно недоступен."


async def ask_groq(user_message: str, chat_history: list) -> str:
    """Отправляет запрос к Groq API"""
    if not config.GROQ_API_KEY:
        return "ИИ консультант временно недоступен. Напишите менеджеру напрямую!"

    products_list = _build_product_list()
    system = SYSTEM_PROMPT.format(
        shop_name=config.SHOP_NAME,
        products=products_list
    )

    # Формируем историю — последние 6 сообщений
    messages = [{"role": "system", "content": system}]
    for msg in chat_history[-6:]:
        messages.append(msg)
    messages.append({"role": "user", "content": user_message})

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {config.GROQ_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": config.GROQ_MODEL,
                    "messages": messages,
                    "max_tokens": 300,
                    "temperature": 0.7,
                },
                ssl=ssl_ctx,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["choices"][0]["message"]["content"].strip()
                else:
                    text = await resp.text()
                    logger.error(f"Groq error {resp.status}: {text}")
                    return "Не смог получить ответ. Попробуйте ещё раз или напишите менеджеру."
    except Exception as e:
        logger.error(f"Groq request failed: {e}")
        return "Консультант временно недоступен. Напишите менеджеру — он поможет! 🤝"
