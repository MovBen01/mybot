"""
python debug_parser.py
"""
import asyncio
import aiohttp
import ssl
import re
from bs4 import BeautifulSoup

SOURCE_CHANNEL = "BigSaleApple"

async def fetch(session, url, ssl_ctx):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    async with session.get(url, headers=headers, ssl=ssl_ctx, timeout=aiohttp.ClientTimeout(total=15)) as resp:
        return await resp.text()

async def main():
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    async with aiohttp.ClientSession() as session:
        # Загружаем первую страницу
        html = await fetch(session, f"https://t.me/s/{SOURCE_CHANNEL}", ssl_ctx)
        soup = BeautifulSoup(html, 'html.parser')

        # Находим ID самого старого поста на странице чтобы загрузить предыдущие
        all_posts = soup.find_all('div', class_='tgme_widget_message_wrap')
        print(f"Первая страница: {len(all_posts)} постов")

        # Берём ID первого поста для пагинации
        first_msg = all_posts[0].find('div', class_='tgme_widget_message') if all_posts else None
        before_id = None
        if first_msg:
            data_post = first_msg.get('data-post', '')
            before_id = data_post.split('/')[-1]
            print(f"Загружаем посты до ID: {before_id}\n")

        # Загружаем ещё страницу с более старыми постами
        more_posts = []
        if before_id:
            url2 = f"https://t.me/s/{SOURCE_CHANNEL}?before={before_id}"
            html2 = await fetch(session, url2, ssl_ctx)
            soup2 = BeautifulSoup(html2, 'html.parser')
            more_posts = soup2.find_all('div', class_='tgme_widget_message_wrap')
            print(f"Вторая страница: {len(more_posts)} постов")

        all_wraps = list(more_posts) + list(all_posts)
        print(f"Всего постов для анализа: {len(all_wraps)}")
        print("="*60)

        # Показываем посты у которых ЕСТЬ фото или текст с числами
        shown = 0
        for wrap in all_wraps:
            text_el = wrap.find('div', class_='tgme_widget_message_text')
            text = text_el.get_text('\n', strip=True) if text_el else ""
            photo_el = wrap.find('a', class_='tgme_widget_message_photo_wrap')

            has_numbers = bool(re.search(r'\d{3,}', text))
            has_photo = bool(photo_el)

            if has_photo or has_numbers:
                print(f"\n{'✅ ФОТО + ' if has_photo else ''}{'💰 ЧИСЛА' if has_numbers else ''}")
                print(text[:400] if text else "(только фото, нет текста)")
                print("-"*60)
                shown += 1
                if shown >= 8:
                    break

        if shown == 0:
            print("\nПостов с фото или ценами не найдено!")
            print("Показываю первые 3 поста как есть:")
            for wrap in all_wraps[:3]:
                text_el = wrap.find('div', class_='tgme_widget_message_text')
                text = text_el.get_text('\n', strip=True) if text_el else "(нет текста)"
                print(f"\n{text[:300]}")
                print("-"*40)

asyncio.run(main())
