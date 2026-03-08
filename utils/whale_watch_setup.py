"""
Whale Watch — One-Time Session Setup (Patch 139)

Run this ONCE on the VPS to authenticate your Telegram account and
create a Telethon session file.

Usage on VPS:
    cd /root/memecoin_engine
    set -a && source .env && set +a
    python3 utils/whale_watch_setup.py

You'll be prompted for your phone number and the login code Telegram sends.
After that, the session file is saved and the dashboard can use it automatically.

IMPORTANT: Run as the same user that runs the dashboard (root on this VPS).
The session file is stored at: data_storage/whale_watch.session
"""
import asyncio
import os
import sys

# Add engine root to path so imports work
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

SESSION_FILE = os.path.join(ROOT, "data_storage", "whale_watch.session")


async def setup():
    try:
        from telethon import TelegramClient
    except ImportError:
        print("ERROR: telethon not installed.")
        print("Run: pip install telethon")
        sys.exit(1)

    api_id   = int(os.getenv("TELEGRAM_API_ID",   "0") or "0")
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()

    if not api_id or not api_hash:
        print("ERROR: TELEGRAM_API_ID and TELEGRAM_API_HASH must be set in .env")
        sys.exit(1)

    os.makedirs(os.path.dirname(SESSION_FILE), exist_ok=True)

    print(f"\nCreating Telegram session at: {SESSION_FILE}")
    print("You'll receive a login code via Telegram or SMS.\n")

    client = TelegramClient(SESSION_FILE, api_id, api_hash)
    await client.start()

    me = await client.get_me()
    print(f"\n✅ Authenticated as: {me.first_name} (@{me.username})")
    print(f"   Session file:      {SESSION_FILE}")

    # Verify we can access the whale watch channel
    channel = os.getenv("WHALE_WATCH_CHANNEL", "").strip()
    if channel:
        try:
            entity = await client.get_entity(channel)
            print(f"   Channel found:     {getattr(entity, 'title', channel)}")
            print(f"\n✅ Setup complete! Restart the dashboard to activate Whale Watch.")
        except Exception as e:
            print(f"\n⚠️  Could not resolve channel '{channel}': {e}")
            print("   Check WHALE_WATCH_CHANNEL in .env — must be username, link, or invite.")
            print("   Session is still saved and valid.")
    else:
        print("\n⚠️  WHALE_WATCH_CHANNEL not set in .env — set it before starting the dashboard.")
        print("   Session is saved and ready.")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(setup())
