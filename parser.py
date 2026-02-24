"""
Парсер публичного канала @BigSaleApple через t.me/s/
Формат постов: прайс-листы, одна строка = один товар
Пример: "iPhone 16 128GB Black -89.500"
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


class TelegramWebParser:

    BASE_URL = f"https://t.me/s/{SOURCE_CHANNEL}"

    # Паттерн цены: "Название товара -89.500" или "Название -89 500"
    # Цена идёт после тире в конце строки
    LINE_PRICE_RE = re.compile(
        r'^(.+?)\s*[-–—]\s*([\d]{2,3}[\d\s.,]{1,10})\s*$'
    )

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
        """Загружает несколько страниц канала"""
        all_posts = []

        # Первая страница
        html = await self._fetch(self.BASE_URL)
        if not html:
            return []

        soup = BeautifulSoup(html, 'html.parser')
        wraps = soup.find_all('div', class_='tgme_widget_message_wrap')
        posts_page1 = self._extract_raw_posts(wraps)
        all_posts = posts_page1 + all_posts

        # Листаем назад ещё 3 страницы
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
            older = self._extract_raw_posts(wraps2)
            all_posts = older + all_posts
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
            msg_id = '0'
            if msg_div:
                msg_id = msg_div.get('data-post', '').split('/')[-1]

            text_el = wrap.find('div', class_='tgme_widget_message_text')
            text = text_el.get_text('\n', strip=True) if text_el else ''

            if text:
                posts.append({'msg_id': msg_id, 'text': text})
        return posts

    def _parse_price(self, price_str: str) -> Optional[float]:
        """Парсит строку цены: '89.500' или '89 500' → 89500.0"""
        # Убираем пробелы-разделители тысяч и заменяем точку-разделитель
        cleaned = price_str.strip().replace(' ', '').replace('\xa0', '')
        # Формат 89.500 → это 89500 (точка как разделитель тысяч)
        if '.' in cleaned and len(cleaned.split('.')[-1]) == 3:
            cleaned = cleaned.replace('.', '')
        else:
            cleaned = cleaned.replace('.', '').replace(',', '')
        try:
            val = float(cleaned)
            if 500 <= val <= 1_000_000:  # фильтр: реальные цены на Apple
                return val
        except ValueError:
            pass
        return None

    def _detect_category(self, name: str) -> str:
        name_lower = name.lower()
        cats = {
            'iPhone': ['iphone', 'айфон'],
            'MacBook': ['macbook', 'макбук'],
            'iPad': ['ipad', 'айпад', 'pencil', 'magic keyboard'],
            'Apple Watch': ['watch ultra', 'watch series', 'apple watch'],
            'AirPods': ['airpods', 'airpod'],
            'iMac': ['imac', 'mac mini', 'mac pro', 'mac studio', 'apple display'],
            'Аксессуары Apple': ['magic mouse', 'magic trackpad', 'magsafe',
                                  'зарядка', 'кабель', 'чехол'],
            'Наушники': ['bowers', 'beats', 'наушники', 'sony', 'jabra'],
        }
        for cat_name, keywords in cats.items():
            if any(kw in name_lower for kw in keywords):
                return cat_name
        return 'Apple техника'

    def _extract_products_from_post(self, post: dict) -> List[dict]:
        """Разбирает один пост на список товаров"""
        text = post['text']
        msg_id = post['msg_id']
        lines = text.split('\n')
        products = []

        # Определяем категорию поста по первой строке (заголовок)
        post_category = None
        first_meaningful = next((l.strip() for l in lines if len(l.strip()) > 2
                                  and not re.match(r'^\d{2}/\d{2}/\d{4}$', l.strip())), '')
        if first_meaningful:
            post_category = self._detect_category(first_meaningful)

        for i, line in enumerate(lines):
            line = line.strip()
            if not line or len(line) < 5:
                continue

            # Ищем строки вида "Название товара -89.500"
            m = self.LINE_PRICE_RE.match(line)
            if not m:
                continue

            raw_name = m.group(1).strip()
            raw_price = m.group(2).strip()

            # Фильтруем мусор
            price = self._parse_price(raw_price)
            if not price:
                continue

            # Убираем из названия эмодзи-мусор и технические символы
            name = re.sub(r'[🔙🔥🆕🇺🇸🇷🇺🇭🇰⬛️\[\]]', '', raw_name).strip()
            name = re.sub(r'\s+', ' ', name).strip()

            if len(name) < 4:
                continue

            # Категория из названия или из заголовка поста
            category = self._detect_category(name)
            if category == 'Apple техника' and post_category:
                category = post_category

            products.append({
                'source_id': f"web_{msg_id}_{i}",
                'name': name,
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

        logger.info(f"Extracted {len(all_products)} products total")

        new_count = 0
        for p in all_products:
            markup = config.MARKUP_RULES.get(
                p['category'].lower(),
                config.MARKUP_RULES.get('default', 15)
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
                    await asyncio.sleep(2)
                    success = await self._post_to_channel(product)
                    if success:
                        db.save_channel_post(product_id, 0)
                        new_count += 1

        logger.info(f"Posted {new_count} new products to channel")

    async def _post_to_channel(self, product: dict) -> bool:
        text = (
            f"<b>{product['name']}</b>\n\n"
            f"💰 <b>Цена: {product['price_with_markup']:,.0f}₽</b>\n"
            f"📦 {product['category_name']}"
            f"{config.CHANNEL_SIGNATURE}"
        )
        try:
            await self.bot.send_message(
                chat_id=config.MY_CHANNEL_ID,
                text=text,
                parse_mode="HTML"
            )
            logger.info(f"✅ Posted: {product['name']} — {product['price_with_markup']}₽")
            return True
        except Exception as e:
            logger.error(f"Post error: {e}")
            return False


class TelegramParser(TelegramWebParser):
    pass


class ManualParser:
    @staticmethod
    def add_product(name: str, price: float, category: str,
                    description: str = None, photo_id: str = None) -> int:
        markup = config.MARKUP_RULES.get(category.lower(), config.MARKUP_RULES.get('default', 15))
        cat_id = db.upsert_category(category, markup=markup)
        return db.upsert_product(
            source_id=f"manual_{name[:20]}_{price}",
            name=name,
            original_price=price,
            category_id=cat_id,
            description=description,
            photo_id=photo_id
        )
