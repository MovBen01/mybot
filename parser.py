"""
Парсер публичного канала @BigSaleApple через t.me/s/
"""
import asyncio
import re
import logging
import aiohttp
import ssl
from bs4 import BeautifulSoup
from typing import Optional, List

from config import config
from database import db
from product_manager import ProductManager, ChannelPoster

logger = logging.getLogger(__name__)

SOURCE_CHANNEL = "BigSaleApple"

# Минимальная длина названия товара
MIN_NAME_LEN = 8

# Слова которые точно НЕ являются названием товара
GARBAGE_NAMES = [
    'гарантия', 'получите', 'официальный', 'отдел продаж',
    'оптовая', 'гарантийный', 'понедельник', 'суббота',
    'наша вилка', 'замена в сц', 'новинка', 'хит',
]

# Паттерн: "Название -89.500" или "Название — 89 500"
LINE_PRICE_RE = re.compile(
    r'^(.+?)\s*[-–—]\s*([\d]{2,3}[\d\s.,]{1,10})\s*$'
)

# Паттерн плохого названия — только цвет/характеристика без бренда
COLOR_ONLY_RE = re.compile(
    r'^(black|white|silver|gold|blue|red|green|pink|purple|gray|grey|'
    r'ceramic|amber|jasper|nickel|copper|topaz|plum|velvet|coral|'
    r'чёрный|белый|серый|синий|красный|зелёный|розовый|золотой|'
    r'[\w\s/]+\s+\(.*\))\s*$',
    re.IGNORECASE
)


class TelegramWebParser:

    BASE_URL = f"https://t.me/s/{SOURCE_CHANNEL}"

    def __init__(self, bot, channel_poster: ChannelPoster, product_manager: ProductManager):
        self.bot = bot
        self.channel_poster = channel_poster
        self.product_manager = product_manager

    async def start_monitoring(self):
        logger.info(f"Starting web parser for @{SOURCE_CHANNEL}")
        await self._parse_and_post()
        while True:
            await asyncio.sleep(config.PARSE_INTERVAL)
            logger.info("Running scheduled parse...")
            await self._parse_and_post()

    async def _fetch(self, url: str) -> Optional[str]:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, ssl=ssl_ctx,
                                       timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        return await resp.text()
        except Exception as e:
            logger.error(f"Fetch error: {e}")
        return None

    async def _fetch_all_pages(self) -> List[dict]:
        all_posts = []

        html = await self._fetch(self.BASE_URL)
        if not html:
            return []

        soup = BeautifulSoup(html, 'html.parser')
        wraps = soup.find_all('div', class_='tgme_widget_message_wrap')
        all_posts = self._extract_raw_posts(wraps) + all_posts

        before_id = None
        if wraps:
            first = wraps[0].find('div', class_='tgme_widget_message')
            if first:
                before_id = first.get('data-post', '').split('/')[-1]

        for _ in range(3):
            if not before_id:
                break
            html2 = await self._fetch(f"{self.BASE_URL}?before={before_id}")
            if not html2:
                break
            soup2 = BeautifulSoup(html2, 'html.parser')
            wraps2 = soup2.find_all('div', class_='tgme_widget_message_wrap')
            if not wraps2:
                break
            all_posts = self._extract_raw_posts(wraps2) + all_posts
            first2 = wraps2[0].find('div', class_='tgme_widget_message')
            if first2:
                before_id = first2.get('data-post', '').split('/')[-1]
            else:
                break

        return all_posts

    def _extract_raw_posts(self, wraps) -> List[dict]:
        posts = []
        for wrap in wraps:
            msg_div = wrap.find('div', class_='tgme_widget_message')
            msg_id = msg_div.get('data-post', '').split('/')[-1] if msg_div else '0'
            text_el = wrap.find('div', class_='tgme_widget_message_text')
            text = text_el.get_text('\n', strip=True) if text_el else ''
            if text:
                posts.append({'msg_id': msg_id, 'text': text})
        return posts

    def _parse_price(self, price_str: str) -> Optional[float]:
        cleaned = price_str.strip().replace(' ', '').replace('\xa0', '')
        # "89.500" → 89500 (точка как разделитель тысяч)
        if '.' in cleaned and len(cleaned.split('.')[-1]) == 3:
            cleaned = cleaned.replace('.', '')
        else:
            cleaned = cleaned.replace('.', '').replace(',', '')
        try:
            val = float(cleaned)
            if 500 <= val <= 2_000_000:
                return val
        except ValueError:
            pass
        return None

    def _is_valid_name(self, name: str) -> bool:
        """Проверяет что название — реальный товар, а не мусор"""
        if len(name) < MIN_NAME_LEN:
            return False

        name_lower = name.lower()

        # Фильтр мусорных слов
        if any(g in name_lower for g in GARBAGE_NAMES):
            return False

        # Фильтр телефонных номеров
        if re.search(r'\+7|8\s*\(|@\w', name):
            return False

        # Название должно содержать хотя бы одну латинскую или кириллическую букву
        if not re.search(r'[a-zA-Zа-яА-Я]{2,}', name):
            return False

        # Фильтр строк которые только цвет/вариант без бренда/модели
        # Название товара обычно содержит бренд или модель
        has_brand_or_model = re.search(
            r'(apple|iphone|ipad|mac|airpod|watch|dyson|samsung|sony|jbl|'
            r'bowers|oakley|ps5|tab|galaxy|xiaomi|huawei|lenovo|asus|'
            r'серия|series|pro|plus|ultra|max|mini|air|м\d|m\d)',
            name_lower
        )

        # Если нет известного бренда — название должно быть достаточно длинным
        if not has_brand_or_model and len(name) < 15:
            return False

        return True

    def _detect_category(self, name: str, post_header: str = '') -> str:
        text = (name + ' ' + post_header).lower()
        cats = {
            'iPhone':           ['iphone', 'айфон'],
            'MacBook':          ['macbook', 'макбук'],
            'iPad':             ['ipad', 'айпад', 'pencil', 'magic keyboard'],
            'Apple Watch':      ['apple watch', 'watch ultra', 'watch series'],
            'AirPods':          ['airpods', 'airpod'],
            'iMac':             ['imac', 'mac mini', 'mac pro', 'mac studio'],
            'Аксессуары Apple': ['magic mouse', 'magic trackpad', 'magsafe'],
            'Samsung':          ['samsung', 'galaxy', 'tab s'],
            'Dyson':            ['dyson'],
            'Наушники':         ['bowers', 'beats', 'jbl', 'sony wh', 'jabra'],
            'PlayStation':      ['ps5', 'playstation', 'vr2'],
            'Очки':             ['oakley', 'meta'],
        }
        for cat_name, keywords in cats.items():
            if any(kw in text for kw in keywords):
                return cat_name
        return 'Техника'

    def _extract_products_from_post(self, post: dict) -> List[dict]:
        text = post['text']
        msg_id = post['msg_id']
        lines = text.split('\n')
        products = []

        # Заголовок поста — первая осмысленная строка
        post_header = ''
        for line in lines[:3]:
            line = line.strip()
            if len(line) > 3 and not re.match(r'^\d{2}/\d{2}/\d{4}$', line):
                post_header = line
                break

        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue

            m = LINE_PRICE_RE.match(line)
            if not m:
                continue

            raw_name = m.group(1).strip()
            raw_price = m.group(2).strip()

            price = self._parse_price(raw_price)
            if not price:
                continue

            # Чистим название от эмодзи и технических символов
            name = re.sub(
                r'[🔙🔥🆕🇺🇸🇷🇺🇭🇰🇦🇲⬛️\[\]📱💻⌚🎧✏️🖥️🐭🔍]',
                '', raw_name
            ).strip()
            name = re.sub(r'\s+', ' ', name).strip()

            # Убираем артикулы в начале [MQRN3]
            name = re.sub(r'^\[[\w\d]+\]\s*', '', name)

            if not self._is_valid_name(name):
                continue

            category = self._detect_category(name, post_header)

            products.append({
                'source_id': f"web_{msg_id}_{i}",
                'name': name[:120],
                'price': price,
                'category': category,
            })

        return products

    async def _parse_and_post(self):
        posts = await self._fetch_all_pages()
        logger.info(f"Fetched {len(posts)} posts from @{SOURCE_CHANNEL}")

        all_products = []
        for post in posts:
            products = self._extract_products_from_post(post)
            all_products.extend(products)

        logger.info(f"Extracted {len(all_products)} valid products")

        new_count = 0
        for p in all_products:
            markup = config.MARKUP_RULES.get(
                p['category'].lower(),
                config.MARKUP_RULES.get('default', 12)
            )
            cat_id = db.upsert_category(p['category'], markup=markup)
            product_id = db.upsert_product(
                source_id=p['source_id'],
                name=p['name'],
                original_price=p['price'],
                category_id=cat_id,
            )
            if not db.is_product_posted(product_id):
                product = db.get_product(product_id)
                if product:
                    success = await self._post_to_channel(product)
                    if success:
                        db.save_channel_post(product_id, 0)
                        new_count += 1
                        # Задержка чтобы не получить flood ban
                        await asyncio.sleep(5)

        logger.info(f"Posted {new_count} new products to channel")

    async def _post_to_channel(self, product: dict) -> bool:
        price_formatted = f"{product['price_with_markup']:,.0f}".replace(',', ' ')
        text = (
            f"<b>{product['name']}</b>\n\n"
            f"💰 <b>Цена: {price_formatted}₽</b>\n"
            f"📦 {product['category_name']}"
            f"{config.CHANNEL_SIGNATURE}"
        )
        try:
            await self.bot.send_message(
                chat_id=config.MY_CHANNEL_ID,
                text=text,
                parse_mode="HTML"
            )
            logger.info(f"✅ {product['name']} — {price_formatted}₽")
            return True
        except Exception as e:
            err = str(e)
            if 'Retry after' in err or 'Too Many Requests' in err:
                # Извлекаем секунды ожидания и ждём
                m = re.search(r'retry after (\d+)', err, re.IGNORECASE)
                wait = int(m.group(1)) + 2 if m else 15
                logger.warning(f"Flood limit, waiting {wait}s...")
                await asyncio.sleep(wait)
                # Пробуем ещё раз
                try:
                    await self.bot.send_message(
                        chat_id=config.MY_CHANNEL_ID,
                        text=text,
                        parse_mode="HTML"
                    )
                    return True
                except Exception as e2:
                    logger.error(f"Retry failed: {e2}")
            else:
                logger.error(f"Post error: {e}")
            return False


class TelegramParser(TelegramWebParser):
    pass


class ManualParser:
    @staticmethod
    def add_product(name: str, price: float, category: str,
                    description: str = None, photo_id: str = None) -> int:
        markup = config.MARKUP_RULES.get(category.lower(), config.MARKUP_RULES.get('default', 12))
        cat_id = db.upsert_category(category, markup=markup)
        return db.upsert_product(
            source_id=f"manual_{name[:20]}_{price}",
            name=name,
            original_price=price,
            category_id=cat_id,
            description=description,
            photo_id=photo_id
        )
