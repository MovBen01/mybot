"""
База данных SQLite — товары, пользователи, логи, заказы
"""
import sqlite3
import json
import os
import logging
import asyncio
from datetime import datetime
from typing import Optional, List, Dict, Any
from functools import partial

from config import config

logger = logging.getLogger(__name__)


class Database:
    def __init__(self):
        self.db_path = config.DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init(self):
        """Создание таблиц"""
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    emoji TEXT DEFAULT '📦',
                    markup_percent REAL DEFAULT 15.0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT,
                    name TEXT NOT NULL,
                    description TEXT,
                    original_price REAL,
                    category_id INTEGER REFERENCES categories(id),
                    photo_id TEXT,
                    is_available INTEGER DEFAULT 1,
                    source_url TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    username TEXT,
                    full_name TEXT,
                    first_seen TEXT DEFAULT CURRENT_TIMESTAMP,
                    last_seen TEXT DEFAULT CURRENT_TIMESTAMP,
                    messages_count INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS messages_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    text TEXT,
                    direction TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    product_id INTEGER,
                    price REAL,
                    status TEXT DEFAULT 'pending',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_products_category ON products(category_id, is_available);
                CREATE INDEX IF NOT EXISTS idx_products_name ON products(name);
                CREATE INDEX IF NOT EXISTS idx_channel_posts_product ON channel_posts(product_id);

                CREATE TABLE IF NOT EXISTS channel_posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_id INTEGER,
                    channel_message_id INTEGER,
                    posted_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
            """)
            # Добавим дефолтные категории если таблица пустая
            cats = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
            if cats == 0:
                for cat_name, markup in config.MARKUP_RULES.items():
                    if cat_name != "default":
                        conn.execute(
                            "INSERT OR IGNORE INTO categories (name, markup_percent) VALUES (?, ?)",
                            (cat_name, markup)
                        )
        logger.info("Database initialized")

    # ---- КАТЕГОРИИ ----

    def get_categories(self) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM categories ORDER BY name").fetchall()
            return [dict(r) for r in rows]

    def get_category(self, cat_id: int) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM categories WHERE id=?", (cat_id,)).fetchone()
            return dict(row) if row else None

    def get_category_by_name(self, name: str) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM categories WHERE LOWER(name)=LOWER(?)", (name,)
            ).fetchone()
            return dict(row) if row else None

    def upsert_category(self, name: str, emoji: str = "📦", markup: float = 15.0) -> int:
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT id FROM categories WHERE LOWER(name)=LOWER(?)", (name,)
            ).fetchone()
            if existing:
                return existing["id"]
            cur = conn.execute(
                "INSERT INTO categories (name, emoji, markup_percent) VALUES (?,?,?)",
                (name, emoji, markup)
            )
            return cur.lastrowid

    def update_category_markup(self, cat_id: int, markup: float):
        with self._conn() as conn:
            conn.execute(
                "UPDATE categories SET markup_percent=? WHERE id=?", (markup, cat_id)
            )

    # ---- ТОВАРЫ ----

    def upsert_product(self, source_id: str, name: str, original_price: float,
                       category_id: int, description: str = None,
                       photo_id: str = None, source_url: str = None) -> int:
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT id FROM products WHERE source_id=?", (source_id,)
            ).fetchone()
            if existing:
                conn.execute(
                    """UPDATE products SET name=?, original_price=?, description=?,
                       photo_id=?, updated_at=CURRENT_TIMESTAMP WHERE source_id=?""",
                    (name, original_price, description, photo_id, source_id)
                )
                return existing["id"]
            cur = conn.execute(
                """INSERT INTO products (source_id, name, description, original_price,
                   category_id, photo_id, source_url)
                   VALUES (?,?,?,?,?,?,?)""",
                (source_id, name, description, original_price, category_id, photo_id, source_url)
            )
            return cur.lastrowid

    def get_products_by_category(self, cat_id: int) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT p.*, c.name as category_name, c.markup_percent,
                       ROUND(p.original_price * (1 + c.markup_percent/100)) as price_with_markup
                FROM products p
                JOIN categories c ON p.category_id = c.id
                WHERE p.category_id=? AND p.is_available=1
                ORDER BY p.name
            """, (cat_id,)).fetchall()
            return [dict(r) for r in rows]

    def get_product(self, product_id: int) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute("""
                SELECT p.*, c.name as category_name, c.markup_percent,
                       ROUND(p.original_price * (1 + c.markup_percent/100)) as price_with_markup
                FROM products p
                JOIN categories c ON p.category_id = c.id
                WHERE p.id=?
            """, (product_id,)).fetchone()
            return dict(row) if row else None

    def search_products(self, query: str = "", price_from: float = None, price_to: float = None) -> List[Dict]:
        with self._conn() as conn:
            sql = """
                SELECT p.*, c.name as category_name, c.markup_percent,
                       ROUND(p.original_price * (1 + c.markup_percent/100)) as price_with_markup
                FROM products p
                JOIN categories c ON p.category_id = c.id
                WHERE p.is_available=1
            """
            params = []
            if query:
                sql += " AND (LOWER(p.name) LIKE LOWER(?) OR LOWER(p.description) LIKE LOWER(?))"
                params += [f"%{query}%", f"%{query}%"]
            if price_from:
                sql += " AND ROUND(p.original_price * (1 + c.markup_percent/100)) >= ?"
                params.append(price_from)
            if price_to:
                sql += " AND ROUND(p.original_price * (1 + c.markup_percent/100)) <= ?"
                params.append(price_to)
            sql += " ORDER BY price_with_markup LIMIT 100"
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def get_all_products(self) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT p.*, c.name as category_name, c.markup_percent,
                       ROUND(p.original_price * (1 + c.markup_percent/100)) as price_with_markup
                FROM products p
                JOIN categories c ON p.category_id = c.id
                WHERE p.is_available=1
                ORDER BY c.name, p.name
            """).fetchall()
            return [dict(r) for r in rows]

    # ---- ПОЛЬЗОВАТЕЛИ ----

    async def save_user(self, user_id: int, username: str = None, full_name: str = None):
        with self._conn() as conn:
            existing = conn.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone()
            if existing:
                conn.execute(
                    "UPDATE users SET username=?, full_name=?, last_seen=CURRENT_TIMESTAMP, "
                    "messages_count=messages_count+1 WHERE id=?",
                    (username, full_name, user_id)
                )
            else:
                conn.execute(
                    "INSERT INTO users (id, username, full_name) VALUES (?,?,?)",
                    (user_id, username, full_name)
                )

    def get_all_users(self) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM users ORDER BY last_seen DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_users_count(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    # ---- ЛОГИ СООБЩЕНИЙ ----

    async def log_message(self, user_id: int, text: str, direction: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO messages_log (user_id, text, direction) VALUES (?,?,?)",
                (user_id, text, direction)
            )

    def get_user_messages(self, user_id: int, limit: int = 50) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM messages_log WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_all_messages(self, limit: int = 100) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT ml.*, u.username, u.full_name
                FROM messages_log ml
                LEFT JOIN users u ON ml.user_id = u.id
                ORDER BY ml.created_at DESC LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]

    # ---- ЗАКАЗЫ ----

    async def save_order(self, user_id: int, product_id: int, price: float):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO orders (user_id, product_id, price) VALUES (?,?,?)",
                (user_id, product_id, price)
            )

    def get_orders(self, limit: int = 50) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT o.*, u.username, u.full_name, p.name as product_name
                FROM orders o
                LEFT JOIN users u ON o.user_id = u.id
                LEFT JOIN products p ON o.product_id = p.id
                ORDER BY o.created_at DESC LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]

    # ---- ПОСТЫ В КАНАЛ ----

    def is_product_posted(self, product_id: int) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id FROM channel_posts WHERE product_id=?", (product_id,)
            ).fetchone()
            return row is not None

    def save_channel_post(self, product_id: int, message_id: int):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO channel_posts (product_id, channel_message_id) VALUES (?,?)",
                (product_id, message_id)
            )


db = Database()
