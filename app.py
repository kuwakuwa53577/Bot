import asyncio
import logging
import os
import sys
import traceback
import datetime
import urllib.parse
import xml.etree.ElementTree as ET
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
from flask import Flask, jsonify, request

# ==================================================
# 1. ロガーの設定
# ==================================================
logging.basicConfig(level=logging.INFO)
discord_logger = logging.getLogger("discord")
discord_logger.setLevel(logging.INFO)

# ==================================================
# 2. 環境変数の取得 & 設定
# ==================================================
BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN") or os.getenv("BOT_TOKEN")
PROXY_URL = os.getenv("PROXY_URL")  # Webshare等のプロキシURL

# 朝通知を送る Discord チャンネル ID（環境変数または直接入力）
# 例: 123456789012345678
MORNING_CHANNEL_ID = int(os.getenv("MORNING_CHANNEL_ID", "0")) 

# ニュース検索キーワード（例: "日本 ニュース" や "テクノロジー"）
NEWS_QUERY = os.getenv("NEWS_QUERY", "日本 ニュース")

# ==================================================
# 3. Discord Bot の初期化
# ==================================================
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents, proxy=PROXY_URL)

    async def setup_hook(self):
        # スラッシュコマンド（/wake_up）を Discord に同期
        await self.tree.sync()
        print("✅ スラッシュコマンドを同期しました。")
        # 朝の定期実行タスクを開始
        if not morning_task.is_running():
            morning_task.start()

discord_bot = MyBot()

@discord_bot.event
async def on_ready():
    print(f"✅ Discord Bot ログイン成功: {discord_bot.user} (ID: {discord_bot.user.id})")

# ==================================================
# 4. 天気予報 & ニュース取得関数
# ==================================================
async def get_weather_info():
    """ Open-Meteo API を利用して東京の天気を取得（無料・キー不要） """
    url = "https://api.open-meteo.com/v1/forecast?latitude=35.6895&longitude=139.6917&daily=weathercode,temperature_2m_max,temperature_2m_min&timezone=Asia%2FTokyo"
    
    # 天気コード変換テーブル
    weather_codes = {
        0: "☀️ 晴れ", 1: "🌤️ おおむね晴れ", 2: "⛅ 時々曇り", 3: "☁️ 曇り",
        45: "霧", 48: "霧氷", 51: "🌦️ 小雨", 61: "☔ 雨", 71: "❄️ 雪",
        80: "🌦️ 俄雨", 95: "⚡ 雷雨"
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    code = data["daily"]["weathercode"][0]
                    temp_max = data["daily"]["temperature_2m_max"][0]
                    temp_min = data["daily"]["temperature_2m_min"][0]
                    weather_str = weather_codes.get(code, "不明")
                    return f"**東京の天気:** {weather_str}\n**最高気温:** {temp_max}°C / **最低気温:** {temp_min}°C"
    except Exception as e:
        print(f"天気情報の取得失敗: {e}")
    return "天気情報の取得に失敗しました。"

async def get_top_news(count=5):
    """ Google News RSS から指定件数の最新ニュースを取得 """
    encoded_query = urllib.parse.quote(NEWS_QUERY)
    url = f"https://news.google.com/rss/search?q={encoded_query}&hl=ja&gl=JP&ceid=JP:ja"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    xml_text = await response.text()
                    root = ET.fromstring(xml_text)
                    items = root.findall(".//item")[:count]
                    
                    news_list = []
                    for i, item in enumerate(items, 1):
                        title = item.find("title").text if item.find("title") is not None else "タイトルなし"
                        link = item.find("link").text if item.find("link") is not None else ""
                        news_list.append(f"{i}. [{title}]({link})")
                    
                    return "\n".join(news_list)
    except Exception as e:
        print(f"ニュースの取得失敗: {e}")
    return "ニュースの取得に失敗しました。"

async def create_morning_embed():
    """ 朝のメッセージ用 Embed（カード形式）を作成 """
    embed = discord.Embed(
        title="☀️ おはようございます！ 今日の朝のお知らせです",
        color=discord.Color.gold(),
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    
    weather = await get_weather_info()
    news = await get_top_news(5)
    
    embed.add_field(name="🌡️ 今日の天気", value=weather, inline=False)
    embed.add_field(name="📰 新着ニュース 5件", value=news, inline=False)
    
    return embed

# ==================================================
# 5. スラッシュコマンド（/wake_up）の定義
# ==================================================
@discord_bot.tree.command(name="wake_up", description="朝の挨拶・天気・ニュースを手動で取得して表示します")
async def wake_up(interaction: discord.Interaction):
    # 処理に時間がかかる場合があるため、まずレスポンスを保留
    await interaction.response.defer()
    embed = await create_morning_embed()
    await interaction.followup.send(embed=embed)

# ==================================================
# 6. 朝の定時自動送信タスク（毎朝 07:00 JST）
# ==================================================
@tasks.loop(minutes=1)
async def morning_task():
    # 日本時間（JST = UTC+9）の現在時刻を取得
    jst = datetime.timezone(datetime.timedelta(hours=9))
    now = datetime.datetime.now(jst)
    
    # 送信したい時刻を指定（例: 毎朝 07:00）
    if now.hour == 7 and now.minute == 0:
        if MORNING_CHANNEL_ID == 0:
            print("⚠️ MORNING_CHANNEL_ID が設定されていないため、定時送信をスキップしました。")
            return
            
        channel = discord_bot.get_channel(MORNING_CHANNEL_ID)
        if channel:
            embed = await create_morning_embed()
            await channel.send(embed=embed)
            # 1分間に複数回送信されるのを防ぐため 60 秒待機
            await asyncio.sleep(60)

# ==================================================
# 7. Flask Web サーバーの設定
# ==================================================
app = Flask(__name__)

@app.route("/", methods=["GET", "HEAD"])
def index():
    return jsonify({"status": "ok", "message": "Bot is running!"}), 200

# ==================================================
# 8. Bot 起動処理
# ==================================================
async def start_bot_with_retry():
    if not BOT_TOKEN:
        print("❌ エラー: BOT_TOKEN が環境変数に設定されていません。")
        return

    sanitized_token = BOT_TOKEN.strip()

    if PROXY_URL:
        print(f"🌐 プロキシ経由で接続を試みます: {PROXY_URL.split('@')[-1]}")
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
            print(f"❌ インテントエラー: Developer Portal で Privileged Intents を有効にしてください: {e}")
            break
        except discord.errors.HTTPException as e:
            print(f"⚠️ Discord HTTP エラー [{e.status}]: {e}")
            await asyncio.sleep(retry_delay)
        except Exception as e:
            print("❌ 接続中に予期せぬエラーが発生しました:")
            traceback.print_exc()
            await asyncio.sleep(retry_delay)

async def main():
    loop = asyncio.get_running_loop()
    loop.create_task(
        asyncio.to_thread(
            app.run,
            host="0.0.0.0",
            port=int(os.getenv("PORT", 10000)),
            use_reloader=False,
        )
    )
    await start_bot_with_retry()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("👋 プログラムを終了します。")
