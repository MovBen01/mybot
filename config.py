import os
from dataclasses import dataclass, field
from typing import List

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


@dataclass
class Config:
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

    ADMIN_IDS: List[int] = field(default_factory=lambda: [
        int(x) for x in os.getenv("ADMIN_IDS", "123456789").split(",")
    ])

    MY_CHANNEL_ID: str = os.getenv("MY_CHANNEL_ID", "@my_channel")
    SOURCE_CHANNEL_ID: str = os.getenv("SOURCE_CHANNEL_ID", "BigSaleApple")

    # Наценки по категориям (%)
    MARKUP_RULES: dict = field(default_factory=lambda: {
        "iphone":           10,
        "macbook":           8,
        "ipad":             12,
        "apple watch":      15,
        "airpods":          18,
        "imac":              8,
        "аксессуары apple": 25,
        "наушники":         20,
        "apple техника":    12,
        "default":          12,
    })

    # Как часто проверять новые посты (секунды). 3600 = раз в час
    PARSE_INTERVAL: int = int(os.getenv("PARSE_INTERVAL", "3600"))

    # Подпись под каждым постом в канале
    CHANNEL_SIGNATURE: str = os.getenv(
        "CHANNEL_SIGNATURE", "\n\n🛒 Заказать: @your_bot"
    )

    MANAGER_USERNAME: str = os.getenv("MANAGER_USERNAME", "@manager")
    DB_PATH: str = os.getenv("DB_PATH", "data/store.db")


config = Config()
