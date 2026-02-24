"""
Парсер @BigSaleApple — постит сводный прайс 3 раза в день
"""
import asyncio
import re
import logging
import aiohttp
import ssl
from bs4 import BeautifulSoup
from datetime import datetime
from typing import Optional, List

from config import config
from database import db
from product_manager import ProductManager, ChannelPoster

logger = logging.getLogger(__name__)

SOURCE_CHANNEL = "BigSaleApple"
MIN_NAME_LEN = 8

GARBAGE_NAMES = [
    'гарантия', 'получите', 'официальный', 'отдел продаж',
    'оптовая', 'гарантийный', 'понедельник', 'суббота',
    'наша вилка', 'замена в сц', 'новинка', 'хит',
]

LINE_PRICE_RE = re.compile(
    r'^(.+?)\s*[-–—]\s*([\d]{2,3}[\d\s.,]{1,10})\s*$'
)

# Время постинга (часы по UTC, UTC+3 = Moscow, поэтому -3)
# 09:00 МСК = 06:00 UTC, 14:00 МСК = 11:00 UTC, 19:00 МСК = 16:00 UTC
POST_HOURS_UTC = [6, 11, 13]

# Максимум товаров на одну категорию в сводном посте
MAX_ITEMS_PER_CATEGORY = 15

# Максимум символов в одном сообщении Telegram
TG_MSG_LIMIT = 4000


class TelegramWebParser:

    BASE_URL = f"https://t.me/s/{SOURCE_CHANNEL}"

    def __init__(self, bot, channel_poster: ChannelPoster, product_manager: ProductManager):
        self.bot = bot
        self.channel_poster = channel_poster
        self.product_manager = product_manager

    async def start_monitoring(self):
        logger.info(f"Starting scheduler for @{SOURCE_CHANNEL}")
        logger.info(f"Will post at UTC hours: {POST_HOURS_UTC} (MSK: {[h+3 for h in POST_HOURS_UTC]})")

        # Сразу парсим и сохраняем в БД (без постинга)
        await self._fetch_and_save()

        # Планировщик
        while True:
            now = datetime.utcnow()
            current_hour = now.hour
            current_minute = now.minute

            # Постим в нужные часы в начале часа (0-2 минута)
            if current_hour in POST_HOURS_UTC and current_minute < 2:
                logger.info(f"Posting scheduled price list at {now.strftime('%H:%M UTC')}")
                await self._fetch_and_save()
                await self._post_price_list()
                # Ждём 5 минут чтобы не постить дважды в одном часу
                await asyncio.sleep(300)
            else:
                # Проверяем каждую минуту
                await asyncio.sleep(60)

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
        if len(name) < MIN_NAME_LEN:
            return False
        name_lower = name.lower()
        if any(g in name_lower for g in GARBAGE_NAMES):
            return False
        if re.search(r'\+7|8\s*\(|@\w', name):
            return False
        if not re.search(r'[a-zA-Zа-яА-Я]{2,}', name):
            return False
        has_brand = re.search(
            r'(apple|iphone|ipad|mac|airpod|watch|dyson|samsung|sony|jbl|'
            r'bowers|oakley|ps5|tab|galaxy|xiaomi|huawei|lenovo|asus|'
            r'серия|series|pro|plus|ultra|max|mini|air|м\d|m\d)',
            name_lower
        )
        if not has_brand and len(name) < 15:
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
            'iMac / Mac':       ['imac', 'mac mini', 'mac pro', 'mac studio'],
            'Аксессуары Apple': ['magic mouse', 'magic trackpad', 'magsafe'],
            'Samsung':          ['samsung', 'galaxy', 'tab s'],
            'Dyson':            ['dyson'],
            'Наушники':         ['bowers', 'beats', 'jbl', 'sony wh', 'jabra'],
            'PlayStation':      ['ps5', 'playstation', 'vr2'],
            'Очки':             ['oakley', 'meta hstn', 'meta vanguard'],
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
            name = re.sub(
                r'[🔙🔥🆕🇺🇸🇷🇺🇭🇰🇦🇲⬛️\[\]📱💻⌚🎧✏️🖥️🐭🔍]',
                '', raw_name
            ).strip()
            name = re.sub(r'\s+', ' ', name).strip()
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

    async def _fetch_and_save(self):
        """Парсим канал и сохраняем всё в БД"""
        posts = await self._fetch_all_pages()
        logger.info(f"Fetched {len(posts)} posts from @{SOURCE_CHANNEL}")

        count = 0
        for post in posts:
            products = self._extract_products_from_post(post)
            for p in products:
                markup = config.MARKUP_RULES.get(
                    p['category'].lower(),
                    config.MARKUP_RULES.get('default', 12)
                )
                cat_id = db.upsert_category(p['category'], markup=markup)
                db.upsert_product(
                    source_id=p['source_id'],
                    name=p['name'],
                    original_price=p['price'],
                    category_id=cat_id,
                )
                count += 1

        logger.info(f"Saved {count} products to DB")

    async def _post_price_list(self):
        """Постит сводный прайс по категориям"""
        products = db.get_all_products()
        if not products:
            logger.warning("No products to post")
            return

        # Группируем по категориям
        by_category = {}
        for p in products:
            cat = p['category_name']
            if cat not in by_category:
                by_category[cat] = []
            by_category[cat].append(p)

        now_msk = datetime.utcnow().hour + 3
        date_str = datetime.utcnow().strftime('%d.%m.%Y')

        # Формируем сообщения (может быть несколько если не влезет)
        messages = []
        current_msg = f"🗓 <b>Актуальный прайс на {date_str}</b>\n"
        current_msg += f"🕐 Обновлено в {now_msk:02d}:00 МСК\n"
        current_msg += "─" * 28 + "\n\n"

        # Порядок категорий
        cat_order = [
            'iPhone', 'MacBook', 'iPad', 'Apple Watch', 'AirPods',
            'iMac / Mac', 'Аксессуары Apple', 'Samsung', 'Dyson',
            'PlayStation', 'Наушники', 'Очки', 'Техника'
        ]

        # Сортируем категории по порядку
        sorted_cats = sorted(
            by_category.keys(),
            key=lambda x: cat_order.index(x) if x in cat_order else 99
        )

        cat_emojis = {
            'iPhone': '📱', 'MacBook': '💻', 'iPad': '📟',
            'Apple Watch': '⌚', 'AirPods': '🎧', 'iMac / Mac': '🖥',
            'Аксессуары Apple': '🔌', 'Samsung': '📲', 'Dyson': '🌀',
            'PlayStation': '🎮', 'Наушники': '🎵', 'Очки': '🕶',
            'Техника': '⚡',
        }

        for cat in sorted_cats:
            items = by_category[cat][:MAX_ITEMS_PER_CATEGORY]
            emoji = cat_emojis.get(cat, '📦')

            cat_block = f"{emoji} <b>{cat}</b>\n"
            for item in items:
                price_fmt = f"{item['price_with_markup']:,.0f}".replace(',', ' ')
                cat_block += f"• {item['name']} — <b>{price_fmt}₽</b>\n"
            cat_block += "\n"

            # Если не влезает в текущее сообщение — начинаем новое
            if len(current_msg) + len(cat_block) > TG_MSG_LIMIT:
                messages.append(current_msg)
                current_msg = cat_block
            else:
                current_msg += cat_block

        # Последнее сообщение
        if current_msg.strip():
            current_msg += f"\n{config.CHANNEL_SIGNATURE.strip()}"
            messages.append(current_msg)

        # Постим все части
        logger.info(f"Posting price list in {len(messages)} message(s)")
        for i, msg in enumerate(messages):
            try:
                await self.bot.send_message(
                    chat_id=config.MY_CHANNEL_ID,
                    text=msg,
                    parse_mode="HTML"
                )
                logger.info(f"✅ Posted part {i+1}/{len(messages)}")
                if i < len(messages) - 1:
                    await asyncio.sleep(3)
            except Exception as e:
                logger.error(f"Error posting part {i+1}: {e}")


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
