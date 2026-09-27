import asyncio
import logging
import os
import sys
import traceback
import discord
from discord.ext import commands
from flask import Flask, jsonify, request

# ==================================================
# 1. ロガーの設定（デバッグ・通信ログの可視化）
# ==================================================
logging.basicConfig(level=logging.INFO)
discord_logger = logging.getLogger("discord")
discord_logger.setLevel(logging.DEBUG)  # 接続エラーやWebSocket通信の詳細を確認

# ==================================================
# 2. 環境変数の取得
# ==================================================
BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN") or os.getenv("BOT_TOKEN")
PROXY_URL = os.getenv("PROXY_URL")  # WebshareのプロキシURL

# ==================================================
# 3. Discord Bot の初期化（プロキシ対応）
# ==================================================
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

# proxy パラメータを設定して初期化
discord_bot = commands.Bot(
    command_prefix="!", intents=intents, proxy=PROXY_URL
)


@discord_bot.event
async def on_ready():
  print(f"✅ Discord Bot ログイン成功: {discord_bot.user} (ID: {discord_bot.user.id})")


# ==================================================
# 4. Flask Web サーバーの初期化 & エンドポイント
# ==================================================
app = Flask(__name__)


@app.route("/", methods=["GET", "HEAD"])
def index():
  return jsonify({"status": "ok", "message": "Bot is running!"}), 200


# ==================================================
# 5. Discord Bot 起動処理（自動リトライ & 例外ハンドリング）
# ==================================================
async def start_bot_with_retry():
  if not BOT_TOKEN:
    print("❌ エラー: BOT_TOKEN が環境変数に設定されていません。")
    return

  sanitized_token = BOT_TOKEN.strip()

  if PROXY_URL:
    print(
        f"🌐 プロキシ経由で接続を試みます: {PROXY_URL.split('@')[-1]}"
    )  # パスワードを隠してログ出力
  else:
    print("⚠️ PROXY_URL が未設定のため、通常接続を試みます。")

  retry_delay = 15
  while True:
    try:
      print("🚀 Discord API へ接続中...")
      await discord_bot.start(sanitized_token)
      break
    except discord.errors.LoginFailure as e:
      print(f"❌ ログイン失敗（トークンが無効または再発行されています）: {e}")
      break
    except discord.errors.PrivilegedIntentsRequired as e:
      print(
          f"❌ インテントエラー: Developer Portal で Privileged Intents を有効にしてください: {e}"
      )
      break
    except discord.errors.HTTPException as e:
      print(f"⚠️ Discord HTTP エラー [{e.status}]: {e}")
      await asyncio.sleep(retry_delay)
    except Exception as e:
      print(f"❌ 接続中に予期せぬエラーが発生しました:")
      traceback.print_exc()
      await asyncio.sleep(retry_delay)


# ==================================================
# 6. アプリケーションのエントリーポイント
# ==================================================
async def main():
  # Flask サーバーを別スレッド（バックグラウンド）で起動
  loop = asyncio.get_running_loop()
  loop.create_task(
      asyncio.to_thread(
          app.run,
          host="0.0.0.0",
          port=int(os.getenv("PORT", 10000)),
          use_reloader=False,  # 二重起動を防止
      )
  )

  # Discord Bot の起動（リトライ処理付き）
  await start_bot_with_retry()


if __name__ == "__main__":
  try:
    asyncio.run(main())
  except KeyboardInterrupt:
    print("👋 プログラムを終了します。")
