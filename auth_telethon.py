"""
Запусти этот файл ОДИН РАЗ для авторизации Telethon.
После успешной авторизации запускай bot.py как обычно.

Запуск: python auth_telethon.py
"""
import asyncio
import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

API_ID = os.getenv("TELEGRAM_API_ID", "0")
API_HASH = os.getenv("TELEGRAM_API_HASH", "")

if API_ID == "0" or not API_HASH:
    print("\n❌ Ошибка: TELEGRAM_API_ID и TELEGRAM_API_HASH не заполнены в файле .env")
    print("   Получи их на https://my.telegram.org/apps\n")
    sys.exit(1)

async def main():
    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError

    os.makedirs("sessions", exist_ok=True)

    print("\n" + "="*50)
    print("  Авторизация Telethon")
    print("="*50)
    print("\nЭто нужно только ОДИН РАЗ.")
    print("После этого сессия сохранится и больше")
    print("спрашивать не будет.\n")

    client = TelegramClient("sessions/parser_session", int(API_ID), API_HASH)

    await client.connect()

    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"✅ Уже авторизован как: {me.first_name} (@{me.username})")
        print("   Можно запускать bot.py!\n")
        await client.disconnect()
        return

    print("Введи номер телефона в формате +79XXXXXXXXX:")
    phone = input("Номер: ").strip()

    try:
        await client.send_code_request(phone)
        print("\n✅ Код отправлен!")
        print("Проверь в Telegram — должно прийти сообщение от 'Telegram'")
        print("(Не SMS, а именно в приложении Telegram)\n")
    except Exception as e:
        print(f"❌ Ошибка отправки кода: {e}")
        await client.disconnect()
        return

    code = input("Введи код из Telegram: ").strip()

    try:
        await client.sign_in(phone, code)
    except SessionPasswordNeededError:
        print("\n🔒 У тебя включена двухфакторная аутентификация.")
        password = input("Введи пароль Telegram: ").strip()
        await client.sign_in(password=password)
    except Exception as e:
        print(f"❌ Ошибка входа: {e}")
        await client.disconnect()
        return

    me = await client.get_me()
    print(f"\n✅ Успешно! Вошёл как: {me.first_name} (@{me.username})")
    print("\nТеперь запускай bot.py — парсинг канала заработает автоматически!")
    print("="*50 + "\n")

    await client.disconnect()

asyncio.run(main())
