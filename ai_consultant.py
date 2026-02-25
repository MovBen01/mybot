"""
ИИ консультант на Groq — оптимизирован под бесплатный лимит токенов
"""
import logging
import aiohttp
import ssl
import re
from database import db
from config import config

logger = logging.getLogger(__name__)

# Короткий системный промпт — экономим токены
SYSTEM_PROMPT = """Ты консультант магазина {shop_name}. Помогаешь выбрать технику Apple и другую.
Отвечай коротко (1-2 предложения), по-русски, дружелюбно.
Называй цены если знаешь. Для заказа говори: найдите товар в Каталоге.
Не придумывай характеристики. Если не знаешь — предложи написать менеджеру.

Актуальные товары:
{products}"""


def _find_relevant_products(query: str) -> str:
    """Берёт только товары релевантные запросу — экономия токенов"""
    try:
        query_lower = query.lower()
        all_products = db.get_all_products()
        if not all_products:
            return "Каталог загружается."

        # Ключевые слова из запроса
        keywords = re.findall(r'\w+', query_lower)
        keywords = [k for k in keywords if len(k) > 2]

        # Ищем совпадения по названию
        scored = []
        for p in all_products:
            name_lower = p['name'].lower()
            score = sum(1 for k in keywords if k in name_lower)
            if score > 0:
                scored.append((score, p))

        # Берём топ-8 релевантных или топ-8 из каждой категории если запрос общий
        if scored:
            scored.sort(key=lambda x: x[0], reverse=True)
            top = [p for _, p in scored[:8]]
        else:
            # Общий запрос — по 2 товара из каждой категории
            by_cat = {}
            for p in all_products:
                cat = p['category_name']
                if cat not in by_cat:
                    by_cat[cat] = []
                if len(by_cat[cat]) < 2:
                    by_cat[cat].append(p)
            top = [p for items in by_cat.values() for p in items]

        lines = []
        for p in top[:12]:
            price = f"{int(p['price_with_markup']):,}".replace(',', ' ')
            lines.append(p['category_name'] + ": " + p['name'] + " — " + price + " руб")

        return "\n".join(lines) if lines else "Каталог пуст."
    except Exception as e:
        logger.error("Product list error: " + str(e))
        return "Каталог недоступен."


async def ask_groq(user_message: str, chat_history: list) -> str:
    if not config.GROQ_API_KEY:
        logger.error("GROQ_API_KEY not set!")
        return "ИИ консультант не настроен. Напишите менеджеру!"

    products = _find_relevant_products(user_message)
    system = SYSTEM_PROMPT.format(shop_name=config.SHOP_NAME, products=products)

    messages = [{"role": "system", "content": system}]
    # Только последние 2 сообщения истории — экономим токены
    for msg in chat_history[-2:]:
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
                    "Authorization": "Bearer " + config.GROQ_API_KEY,
                    "Content-Type": "application/json"
                },
                json={
                    "model": config.GROQ_MODEL,
                    "messages": messages,
                    "max_tokens": 150,
                    "temperature": 0.7,
                },
                ssl=ssl_ctx,
                timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    answer = data["choices"][0]["message"]["content"].strip()
                    logger.info("Groq OK, tokens used approx: " + str(len(system)//4 + len(user_message)//4))
                    return answer
                elif resp.status == 429:
                    # Rate limit — извлекаем время ожидания
                    text = await resp.text()
                    wait = "немного"
                    match = re.search(r'try again in (\d+)', text)
                    if match:
                        wait = match.group(1) + " сек"
                    logger.warning("Groq rate limit, retry in " + wait)
                    return "Слишком много запросов подряд. Подождите " + wait + " и спросите снова."
                else:
                    text = await resp.text()
                    logger.error("Groq HTTP " + str(resp.status) + ": " + text[:200])
                    return "Не смог получить ответ. Попробуйте ещё раз."
    except aiohttp.ClientConnectorError as e:
        logger.error("Groq connection error: " + str(e))
        return "Нет соединения с ИИ. Напишите менеджеру!"
    except Exception as e:
        logger.error("Groq error: " + str(e), exc_info=True)
        return "Консультант временно недоступен. Напишите менеджеру!"
