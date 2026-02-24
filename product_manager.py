"""
ProductManager — управление наценками
ChannelPoster — публикация в канал
"""
import logging
from config import config
from database import db

logger = logging.getLogger(__name__)


class ProductManager:
    """Управление товарами и наценками"""

    def calculate_price(self, original_price: float, category_name: str) -> float:
        markup = config.MARKUP_RULES.get(
            category_name.lower(),
            config.MARKUP_RULES.get('default', 15)
        )
        return round(original_price * (1 + markup / 100))

    def update_category_markup(self, category_id: int, new_markup: float):
        db.update_category_markup(category_id, new_markup)
        logger.info(f"Updated markup for category {category_id}: {new_markup}%")


class ChannelPoster:
    """Публикация товаров в Telegram канал"""

    def __init__(self, bot):
        self.bot = bot

    def _format_post(self, product: dict) -> str:
        text = f"<b>{product['name']}</b>\n\n"

        if product.get('description'):
            text += f"{product['description']}\n\n"

        text += f"💰 <b>Цена: {product['price_with_markup']}₽</b>\n"
        text += f"📦 Категория: {product['category_name']}"
        text += config.CHANNEL_SIGNATURE

        return text

    async def post_product(self, product: dict) -> bool:
        try:
            text = self._format_post(product)

            if product.get('photo_id'):
                msg = await self.bot.send_photo(
                    chat_id=config.MY_CHANNEL_ID,
                    photo=product['photo_id'],
                    caption=text,
                    parse_mode="HTML"
                )
            else:
                msg = await self.bot.send_message(
                    chat_id=config.MY_CHANNEL_ID,
                    text=text,
                    parse_mode="HTML"
                )

            logger.info(f"Posted product {product['id']} to channel, msg_id={msg.message_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to post product {product.get('id')}: {e}")
            return False

    async def post_all_pending(self, products: list):
        """Публикует все непостованные товары"""
        import asyncio
        for product in products:
            if not db.is_product_posted(product['id']):
                success = await self.post_product(product)
                if success:
                    db.save_channel_post(product['id'], 0)
                await asyncio.sleep(config.POST_DELAY)
