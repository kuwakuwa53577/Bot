import os
import threading
import socket
import asyncio
import aiohttp
import requests
import base64
import json
import logging
import datetime
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks
from flask import Flask, render_template_string, request, jsonify, redirect, session

import firebase_admin
from firebase_admin import credentials, firestore

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from google.genai import Client
ai = Client(api_key=os.getenv("GEMINI_API_KEY"))

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("DISCORD_BOT_TOKEN")
PROXY_URL = os.getenv("PROXY_URL")
PORT = int(os.getenv("PORT", 10000))
WEB_URL = os.getenv("WEB_URL", "").rstrip("/")

CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
TARGET_GUILD_ID = os.getenv("TARGET_GUILD_ID")
REDIRECT_URI = f"{WEB_URL}/oauth/callback" if WEB_URL else f"http://localhost:{PORT}/oauth/callback"

MEMBER_ROLE_ID = int(os.getenv("MEMBER_ROLE_ID", "0"))
ADMIN_ROLE_ID = int(os.getenv("ADMIN_ROLE_ID", "0"))
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "0"))
SUMMARY_CHANNEL_ID = int(os.getenv("SUMMARY_CHANNEL_ID", "0"))
EXECUTION_CHANNEL_ID = int(os.getenv("EXECUTION_CHANNEL_ID", "0"))
JAIL_ROLE_ID = int(os.getenv("JAIL_ROLE_ID", "0"))
WELCOME_CHANNEL_ID = int(os.getenv("WELCOME_CHANNEL_ID", "0"))
CREATOR_CHANNEL_ID = int(os.getenv("CREATOR_VOICE_CHANNEL_ID", "0"))
CATEGORY_ID = os.getenv("CATEGORY_ID", "0")
CATEGORY_ID = int(CATEGORY_ID) if CATEGORY_ID.isdigit() and CATEGORY_ID != "0" else None

TARGET_CHANNEL_ID = int(os.getenv("TARGET_CHANNEL_ID", "0"))
MILESTONE_CHANNEL_ID = int(os.getenv("MILESTONE_CHANNEL_ID", str(WELCOME_CHANNEL_ID)))
JST = ZoneInfo("Asia/Tokyo")

BUMP_CHANNEL_ID = int(os.getenv("BUMP_CHANNEL_ID", "0"))
BUMP_NOTIFY_USER_ID = int(os.getenv("BUMP_NOTIFY_USER_ID", "0"))

raw_reaction_channels = os.getenv("TARGET_REACTION_CHANNEL_IDS", "")
TARGET_REACTION_CHANNEL_IDS = []
for ch_id in raw_reaction_channels.split(","):
    ch_id = ch_id.strip()
    if ch_id.isdigit():
        TARGET_REACTION_CHANNEL_IDS.append(int(ch_id))

auth_env = os.getenv("AUTHORIZED_USER_IDS", "")
if auth_env:
    AUTHORIZED_USER_IDS = {int(uid.strip()) for uid in auth_env.split(",") if uid.strip().isdigit()}
else:
    AUTHORIZED_USER_IDS = set()

ROLE_IDS = {
    "愛国心の塊": int(os.getenv("ROLE_PATRIOT_ID", "0")),
    "野球好き": int(os.getenv("ROLE_BASEBALL_ID", "0")),
    "サッカー好き": int(os.getenv("ROLE_SOCCER_ID", "0")),
    "バスケ好き": int(os.getenv("ROLE_BASKET_ID", "0")),
    "天才肌": int(os.getenv("ROLE_GENIUS_SKIN_ID", "0")),
    "工作好き": int(os.getenv("ROLE_CRAFT_ID", "0")),
    "プログラミング好き": int(os.getenv("ROLE_PROGRAMMING_ID", "0")),
    "自由人": int(os.getenv("ROLE_FREEDOM_ID", "0")),
    "ゲームの天才": int(os.getenv("ROLE_GAME_GENIUS_ID", "0")),
    "天才": int(os.getenv("ROLE_GENIUS_ID", "0")),
    "浮上人": int(os.getenv("ROLE_ACTIVE_STREAK_ID", "0")),
    "面白い人": int(os.getenv("ROLE_FUNNY_ID", "0")),
    "PC勢": int(os.getenv("ROLE_PC_ID", "0")),
    "スマホ勢": int(os.getenv("ROLE_MOBILE_ID", "0"))
}

BASE_COIN_PER_MINUTE = 2
TITLE_BONUSES = {
    '通話廃人': 3,
    'ガチャ中毒': 1,
    '大富豪': 5
}

temporary_channels = set()

firebase_creds_json = os.getenv("FIREBASE_CREDENTIALS")
if firebase_creds_json:
    try:
        cred_dict = json.loads(firebase_creds_json)
        if "private_key" in cred_dict:
            cred_dict["private_key"] = cred_dict["private_key"].replace("\\n", "\n")
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred)
        print("Firebase Admin SDK initialized successfully")
    except Exception as e:
        print(f"Firebase init error: {e}")

db = None
try:
    if firebase_admin._apps:
        db = firestore.client()
        print("Firestore client obtained successfully")
except Exception as e:
    print(f"Firestore client error: {e}")

def save_user_data(user_id, data_dict):
    if not db: return
    try: db.collection("verifications").document(str(user_id)).set(data_dict, merge=True)
    except Exception as e: print(f"Firestore save error: {e}")

def load_all_data():
    if not db: return {}
    try:
        docs = db.collection("verifications").stream()
        return {doc.id: doc.to_dict() for doc in docs}
    except Exception as e: return {}

def getUserData(user_id):
    str_id = str(user_id)
    if not db:
        return {"balance": 0, "lastDaily": 0, "multiplier": 1.0, "multiplierExpire": 0, "totalVoiceMinutes": 0, "totalGachaCount": 0, "totalSpentCoins": 0, "badges": ['新人メンバー'], "equippedBadge": '新人メンバー'}
    try:
        doc = db.collection("economy").document(str_id).get()
        if doc.exists:
            data = doc.to_dict()
            if data.get("multiplierExpire", 0) > 0 and datetime.datetime.now().timestamp() * 1000 > data["multiplierExpire"]:
                data["multiplier"] = 1.0
                data["multiplierExpire"] = 0
            return data
    except Exception: pass
    
    default_data = {"balance": 0, "lastDaily": 0, "multiplier": 1.0, "multiplierExpire": 0, "totalVoiceMinutes": 0, "totalGachaCount": 0, "totalSpentCoins": 0, "badges": ['新人メンバー'], "equippedBadge": '新人メンバー'}
    saveUserData(user_id, default_data)
    return default_data

def saveUserData(user_id, data_dict):
    if not db: return
    try: db.collection("economy").document(str(user_id)).set(data_dict, merge=True)
    except Exception as e: print(f"Economy data save error: {e}")

def checkAndAwardBadges(userData):
    newBadges = []
    badges = userData.get("badges", [])
    if userData.get("totalVoiceMinutes", 0) >= 600 and '通話廃人' not in badges:
        badges.append('通話廃人'); newBadges.append('通話廃人')
    if userData.get("totalGachaCount", 0) >= 50 and 'ガチャ中毒' not in badges:
        badges.append('ガチャ中毒'); newBadges.append('ガチャ中毒')
    if userData.get("balance", 0) >= 10000 and '大富豪' not in badges:
        badges.append('大富豪'); newBadges.append('大富豪')
    userData["badges"] = badges
    return newBadges

def get_user_ip_from_db(user_id: int) -> str:
    if not db: return "Database not connected"
    try:
        doc = db.collection("verifications").document(str(user_id)).get()
        if doc.exists: return doc.to_dict().get("ip", "IP not recorded")
    except Exception: pass
    return "IP fetch failed"

def get_user_email_from_db(user_id: int) -> str:
    if not db: return "Database not connected"
    try:
        doc = db.collection("verifications").document(str(user_id)).get()
        if doc.exists: return doc.to_dict().get("email", "Email not recorded")
    except Exception: pass
    return "Email fetch failed"

def is_user_jailed(user_id: int) -> bool:
    if not db: return False
    try: return db.collection("jail_list").document(str(user_id)).get().exists
    except Exception: return False

def is_authorized_or_admin(member: discord.Member) -> bool:
    if not member: return False
    if member.id in AUTHORIZED_USER_IDS or member.guild_permissions.administrator: return True
    return False

async def send_log(guild: discord.Guild, title: str, description: str, color=0x5865F2):
    if LOG_CHANNEL_ID == 0: return
    channel = guild.get_channel(LOG_CHANNEL_ID)
    if channel:
        try: await channel.send(embed=discord.Embed(title=title, description=description, color=color, timestamp=datetime.datetime.now()))
        except Exception: pass

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "samon_secret_key")

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ja">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>認証</title></head>
<body style="background:#111;color:#fff;font-family:sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;">
  <div style="background:rgba(255,255,255,0.05);padding:40px;border-radius:16px;text-align:center;">
    <h1>サーバー認証</h1>
    <p>下のボタンを押してDiscordアカウントで認証してください。</p>
    <a href="/oauth/login/{{ user_id }}" style="background:#5865F2;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:bold;display:inline-block;margin-top:20px;">Discordで認証する</a>
  </div>
</body>
</html>
"""

SUCCESS_TEMPLATE = """
<!DOCTYPE html>
<html lang="ja">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>認証成功</title></head>
<body style="background:#111;color:#fff;font-family:sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;">
  <div style="background:rgba(255,255,255,0.05);padding:40px;border-radius:16px;text-align:center;">
    <h1 style="color:#80ff80;">認証成功！</h1>
    <p>基本権限の付与が完了しました。<br>続いて、アンケートに答えて特別ロールをゲットしよう！</p>
    <a href="/questions/{{ user_id }}" style="background:#5865F2;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:bold;display:inline-block;margin-top:20px;">質問ページに進む</a>
  </div>
</body>
</html>
"""

QUESTIONNAIRE_TEMPLATE = """
<!DOCTYPE html>
<html lang="ja">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>アンケート</title></head>
<body style="background:#111;color:#fff;font-family:sans-serif;display:flex;justify-content:center;align-items:center;min-height:100vh;padding:20px;">
  <div style="background:rgba(255,255,255,0.05);padding:30px;border-radius:16px;width:100%;max-width:500px;">
    <h1 style="color:#80ff80;text-align:center;margin-bottom:15px;">スペシャルアンケート</h1>
    <form action="/submit-answers/{{ user_id }}" method="POST">
      <div style="margin-bottom:15px;"><label>自分は面白いと思いますか？</label>
        <select name="q1_funny" style="width:100%;padding:8px;background:#222;color:#fff;border-radius:5px;"><option value="はい">はい</option><option value="いいえ">いいえ</option></select>
      </div>
      <div style="margin-bottom:15px;"><label>主なゲーム環境は？</label>
        <select name="q2_platform" style="width:100%;padding:8px;background:#222;color:#fff;border-radius:5px;"><option value="PC">PC</option><option value="スマホ">スマホ</option><option value="その他">その他</option></select>
      </div>
      <div style="margin-bottom:15px;"><label>趣味やマイブーム（野球 / プログラミング / 工作 等）</label>
        <input type="text" name="q3_hobby" placeholder="例: 野球、プログラミング" required style="width:100%;padding:8px;background:#222;color:#fff;border-radius:5px;border:1px solid #444;">
      </div>
      <button type="submit" style="width:100%;padding:12px;background:#5865F2;color:#fff;border:none;border-radius:8px;font-weight:bold;cursor:pointer;">回答を送信してロールを受け取る</button>
    </form>
  </div>
</body>
</html>
"""

DONE_TEMPLATE = """
<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8"><title>完了</title></head>
<body style="background:#111;color:#fff;font-family:sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;">
  <div style="background:rgba(255,255,255,0.05);padding:40px;border-radius:16px;text-align:center;">
    <h1 style="color:#80ff80;">すべて完了しました！</h1>
    <p>Discordアプリに戻ってご確認ください！</p>
  </div>
</body>
</html>
"""

@app.route("/")
@app.route("/ping")
def ping():
    return "Bot is running!", 200

@app.route("/verify/<int:user_id>")
def verify(user_id):
    session['target_user_id'] = user_id
    return render_template_string(HTML_TEMPLATE, user_id=user_id)

@app.route("/oauth/login/<int:user_id>")
def oauth_login(user_id):
    session['target_user_id'] = user_id
    return redirect(f"https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&response_type=code&scope=identify%20email%20guilds%20guilds.join")

@app.route("/oauth/callback")
def oauth_callback():
    code = request.args.get("code")
    user_id = session.get('target_user_id')
    if not code or not user_id: return "Error", 400

    ip_address = request.headers.get("X-Forwarded-For", request.remote_addr)
    if ip_address and "," in ip_address: ip_address = ip_address.split(",")[0].strip()

    token_res = requests.post("https://discord.com/api/oauth2/token", data={
        "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
        "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI
    }, headers={"Content-Type": "application/x-www-form-urlencoded"})

    if token_res.status_code != 200: return "Token error", 400
    access_token = token_res.json().get("access_token")
    user_info = requests.get("https://discord.com/api/users/@me", headers={"Authorization": f"Bearer {access_token}"}).json()

    if TARGET_GUILD_ID and access_token:
        requests.put(f"https://discord.com/api/v10/guilds/{TARGET_GUILD_ID}/members/{user_id}", headers={
            "Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"
        }, json={"access_token": access_token})

    oauth_data = {"username": user_info.get("username"), "email": user_info.get("email", "none"), "ip": ip_address, "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    session['oauth_data'] = oauth_data
    save_user_data(user_id, oauth_data)

    guild = discord_bot.guilds[0] if discord_bot.guilds else None
    if guild and MEMBER_ROLE_ID != 0:
        async def assign_member():
            try:
                member = await guild.fetch_member(user_id)
                role = guild.get_role(MEMBER_ROLE_ID)
                if member and role: await member.add_roles(role)
            except Exception: pass
        asyncio.run_coroutine_threadsafe(assign_member(), discord_bot.loop)

    return render_template_string(SUCCESS_TEMPLATE, user_id=user_id)

@app.route("/questions/<int:user_id>")
def questions_page(user_id):
    session['target_user_id'] = user_id
    return render_template_string(QUESTIONNAIRE_TEMPLATE, user_id=user_id)

@app.route("/submit-answers/<int:user_id>", methods=["POST"])
def submit_answers(user_id):
    answers = {"q1_funny": request.form.get("q1_funny", ""), "q2_platform": request.form.get("q2_platform", ""), "q3_hobby": request.form.get("q3_hobby", "")}
    roles_to_add = []
    if answers["q1_funny"] == "はい" and ROLE_IDS["面白い人"] != 0: roles_to_add.append(ROLE_IDS["面白い人"])
    if answers["q2_platform"] == "PC" and ROLE_IDS["PC勢"] != 0: roles_to_add.append(ROLE_IDS["PC勢"])
    elif answers["q2_platform"] == "スマホ" and ROLE_IDS["スマホ勢"] != 0: roles_to_add.append(ROLE_IDS["スマホ勢"])

    hobby = answers["q3_hobby"]
    for key, r_id in [("野球", ROLE_IDS["野球好き"]), ("サッカー", ROLE_IDS["サッカー好き"]), ("バスケ", ROLE_IDS["バスケ好き"]), ("工作", ROLE_IDS["工作好き"]), ("プログラミング", ROLE_IDS["プログラミング好き"])]:
        if key in hobby and r_id != 0: roles_to_add.append(r_id)

    guild = discord_bot.guilds[0] if discord_bot.guilds else None
    if guild and roles_to_add:
        async def assign_roles():
            try:
                member = await guild.fetch_member(user_id)
                if member:
                    for r_id in roles_to_add:
                        role = guild.get_role(r_id)
                        if role and role not in member.roles: await member.add_roles(role)
            except Exception: pass
        asyncio.run_coroutine_threadsafe(assign_roles(), discord_bot.loop)

    save_user_data(user_id, {**session.get('oauth_data', {}), "answers": answers})
    return render_template_string(DONE_TEMPLATE)

class DynamicRoleButton(discord.ui.Button):
    def __init__(self, label: str, role_id: int):
        super().__init__(style=discord.ButtonStyle.primary, label=label, custom_id=f"role_btn_{role_id}")
        self.role_id = role_id

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild: return
        role = guild.get_role(self.role_id)
        if not role:
            return await interaction.response.send_message("指定されたロールが見つかりませんでした。", ephemeral=True)
        member = interaction.user
        try:
            if role in member.roles:
                await member.remove_roles(role)
                await interaction.response.send_message(f"ロール [{role.name}] を解除しました。", ephemeral=True)
            else:
                await member.add_roles(role)
                await interaction.response.send_message(f"ロール [{role.name}] を付与しました。", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"ロールの変更に失敗しました: {e}", ephemeral=True)

class DynamicRoleView(discord.ui.View):
    def __init__(self, buttons_info: list):
        super().__init__(timeout=None)
        for label, role_id in buttons_info:
            self.add_item(DynamicRoleButton(label, role_id))

class AllInOneBot(commands.Bot):
    async def setup_hook(self):
        self.add_view(RuleVerifyView())
        try:
            guild_obj = discord.Object(id=int(TARGET_GUILD_ID)) if TARGET_GUILD_ID and TARGET_GUILD_ID.isdigit() else None
            if guild_obj:
                self.tree.copy_global_to(guild=guild_obj)
                synced = await self.tree.sync(guild=guild_obj)
                print(f"Synced {len(synced)} slash commands to guild.")
            else:
                synced = await self.tree.sync()
                print(f"Synced {len(synced)} global slash commands.")
        except Exception as e:
            print(f"Command sync error: {e}")
        
        scheduler = AsyncIOScheduler(timezone=JST)
        scheduler.add_job(job_morning, CronTrigger(hour=7, minute=0, timezone=JST))
        scheduler.add_job(job_evening, CronTrigger(hour=16, minute=30, timezone=JST))
        scheduler.add_job(job_night, CronTrigger(hour=22, minute=0, timezone=JST))
        scheduler.start()

        mining_and_scheduler_loop.start()
        execution_loop.start()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
intents.voice_states = True

discord_bot = AllInOneBot(command_prefix="!", intents=intents, proxy=PROXY_URL)

async def get_weather():
    url = "https://weather.tsukumijima.net/api/forecast/city/130010"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    forecasts = data.get("forecasts", [])
                    if forecasts:
                        today = forecasts[0]
                        telop = today.get("telop", "不明")
                        temp_data = today.get("temperature", {})
                        max_temp = temp_data.get("max", {}).get("celsius", "-")
                        min_temp = temp_data.get("min", {}).get("celsius", "-")
                        return f"東京の天気: {telop} / 最低 {min_temp}℃ / 最高 {max_temp}℃"
    except Exception: pass
    return "天気情報の取得に失敗しました。"

async def get_news():
    news_list = []
    url = "https://www.nhk.or.jp/rss/news/cat0.xml"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    root = ET.fromstring(text)
                    items = root.findall(".//item")[:5]
                    for item in items:
                        t = item.find("title")
                        l = item.find("link")
                        if t is not None and l is not None:
                            news_list.append(f"• [{t.text}]({l.text})")
    except Exception: pass
    return "\n".join(news_list) if news_list else "ニュースの取得に失敗しました。"

async def get_earthquake_info():
    url = "https://api.p2pquake.net/v2/history?codes=551&limit=1"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data and isinstance(data, list) and len(data) > 0:
                        latest = data[0]
                        eq = latest.get("earthquake", {})
                        t = eq.get("time", "不明")
                        hypo = eq.get("hypocenter", {}).get("name", "不明")
                        mag = eq.get("hypocenter", {}).get("magnitude", "不明")
                        return f"発生時刻: {t}\n震源地: {hypo} (M{mag})"
    except Exception: pass
    return "地震情報の取得に失敗しました。"

async def get_target_channel():
    if TARGET_CHANNEL_ID == 0: return None
    try:
        ch = discord_bot.get_channel(TARGET_CHANNEL_ID)
        if not ch: ch = await discord_bot.fetch_channel(TARGET_CHANNEL_ID)
        return ch
    except Exception: return None

async def job_morning():
    ch = await get_target_channel()
    if not ch: return
    w = await get_weather()
    n = await get_news()
    q = await get_earthquake_info()
    embed = discord.Embed(title="おはようございます！本日の朝刊情報", color=discord.Color.gold())
    embed.add_field(name="天気予報", value=w, inline=False)
    embed.add_field(name="最新ニュース (上位5件)", value=n, inline=False)
    embed.add_field(name="地震情報", value=q, inline=False)
    await ch.send("おはようございます！今日も一日頑張りましょう！", embed=embed)

async def job_evening():
    ch = await get_target_channel()
    if ch: await ch.send("夕方ですね、外出などの際は気を付けてください。")

async def job_night():
    ch = await get_target_channel()
    if ch: await ch.send("夜ですね。そろそろ休みましょう。")

@tasks.loop(minutes=1)
async def mining_and_scheduler_loop():
    try:
        for guild in discord_bot.guilds:
            for channel in guild.channels:
                if isinstance(channel, discord.VoiceChannel) and len(channel.members) >= 2:
                    for member in channel.members:
                        if member.bot or member.voice.selfMute or member.voice.selfDeaf: continue
                        uData = getUserData(member.id)
                        uData["totalVoiceMinutes"] = uData.get("totalVoiceMinutes", 0) + 1
                        calcCoins = BASE_COIN_PER_MINUTE * uData.get("multiplier", 1.0)
                        equipped = uData.get("equippedBadge")
                        if equipped and equipped in TITLE_BONUSES: calcCoins += TITLE_BONUSES[equipped]
                        uData["balance"] = uData.get("balance", 0) + round(calcCoins)
                        saveUserData(member.id, uData)
    except Exception as e:
        print(f"Mining error: {e}")

@tasks.loop(minutes=1)
async def execution_loop():
    if EXECUTION_CHANNEL_ID == 0 or not db: return
    try:
        docs = db.collection("jail_list").stream()
        for doc in docs:
            data = doc.to_dict()
            user_id = int(doc.id)
            for guild in discord_bot.guilds:
                channel = guild.get_channel(EXECUTION_CHANNEL_ID)
                if channel:
                    await channel.send(f"Jail user: <@{user_id}> | Username: {data.get('username')} | IP: {data.get('ip')} | Reason: {data.get('reason')}")
    except Exception as e:
        print(f"Execution loop error: {e}")

@discord_bot.event
async def on_ready():
    print(f"Bot connected: {discord_bot.user}")

@discord_bot.event
async def on_member_join(member: discord.Member):
    if WELCOME_CHANNEL_ID != 0:
        try:
            wch = discord_bot.get_channel(WELCOME_CHANNEL_ID) or await discord_bot.fetch_channel(WELCOME_CHANNEL_ID)
            if wch:
                await wch.send(f"{member.mention} さんがサーバーに参加しました！")
        except Exception: pass
    if MILESTONE_CHANNEL_ID != 0:
        try:
            total = member.guild.member_count
            if total % 10 == 0:
                mch = discord_bot.get_channel(MILESTONE_CHANNEL_ID) or await discord_bot.fetch_channel(MILESTONE_CHANNEL_ID)
                if mch:
                    await mch.send(embed=discord.Embed(title="メンバー突破記念", description=f"総メンバー数が **{total}人** に到達しました！", color=discord.Color.purple()))
        except Exception: pass

@discord_bot.event
async def on_message(message: discord.Message):
    if not message.guild:
        return

    if message.author.id == 302050872383242240:
        embed_texts = []
        for e in message.embeds:
            if e.title: embed_texts.append(e.title)
            if e.description: embed_texts.append(e.description)
            for f in e.fields:
                embed_texts.append(f"{f.name} {f.value}")
        full_text = (message.content or "") + " " + " ".join(embed_texts)

        if any(w in full_text for w in ["表示アップ", "Bump", "bump", "成功"]):
            try:
                await message.channel.send("Bump受け付けました！2時間後に通知します。")
            except Exception as e:
                print(f"Bump reply error: {e}")

            async def bump_remind(ch):
                await asyncio.sleep(7200)
                mention = f"<@{BUMP_NOTIFY_USER_ID}>" if BUMP_NOTIFY_USER_ID else "みなさん"
                try:
                    await ch.send(f"2時間経過しました！ {mention} `/bump` をお願いします！")
                except Exception as e:
                    print(f"Bump remind error: {e}")

            discord_bot.loop.create_task(bump_remind(message.channel))
        return

    if message.author.bot:
        return

    if TARGET_REACTION_CHANNEL_IDS and message.channel.id in TARGET_REACTION_CHANNEL_IDS:
        try: await message.add_reaction("👍")
        except Exception: pass

    if message.content.startswith('.kuwakuwa'):
        args = message.content[9:].strip()
        if not args:
            await message.reply('Please provide text for AI.')
            return
        await message.channel.sendTyping()
        try:
            response = ai.models.generate_content(model='gemini-2.5-flash', contents=args)
            replyText = response.text
            await message.reply(replyText[:2000] if len(replyText) > 2000 else replyText)
        except Exception as e:
            await message.reply(f'AI error: {e}')
        return

    if is_user_jailed(message.author.id):
        try: await message.delete()
        except Exception: pass
        return

    if SUMMARY_CHANNEL_ID != 0 and ("discord.gg/" in message.content or "discord.com/invite/" in message.content):
        if message.channel.id != SUMMARY_CHANNEL_ID:
            s_ch = message.guild.get_channel(SUMMARY_CHANNEL_ID)
            if s_ch:
                await s_ch.send(embed=discord.Embed(title="Invite Link Detected", description=f"Sender: {message.author.mention}\n\n{message.content}", color=0x5865F2))

    await discord_bot.process_commands(message)

@discord_bot.event
async def on_voice_state_update(oldState, newState):
    member = newState.member
    guild = newState.guild
    if not member: return

    if CREATOR_CHANNEL_ID != 0 and newState.channelId == CREATOR_CHANNEL_ID:
        try:
            vc = await guild.channels.create(name=f"{member.display_name}'s room", type=discord.ChannelType.voice, parent=CATEGORY_ID)
            await member.voice.set_channel(vc)
            temporary_channels.add(vc.id)
        except Exception as e: print(f"VC create error: {e}")

    if oldState.channel and oldState.channel.id != CREATOR_CHANNEL_ID:
        left = oldState.channel
        if left.id in temporary_channels and len(left.members) == 0:
            try:
                await left.delete()
                temporary_channels.remove(left.id)
            except Exception: pass

class RuleVerifyView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="認証を行う", style=discord.ButtonStyle.primary, custom_id="rule_verify_btn")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        base_url = WEB_URL if WEB_URL else f"http://localhost:{PORT}"
        view = discord.ui.View().add_item(discord.ui.Button(label="認証ページを開く", style=discord.ButtonStyle.link, url=f"{base_url}/verify/{interaction.user.id}"))
        await interaction.response.send_message("下のボタンをクリックして認証を進めてください。", view=view, ephemeral=True)

@discord_bot.tree.command(name="rule", description="認証パネルを送信します")
@app_commands.default_permissions(administrator=True)
async def rule_command(interaction: discord.Interaction):
    await interaction.response.send_message(embed=discord.Embed(title="認証", description="ボタンを押してシステム認証を行ってください。", color=0x5865F2), view=RuleVerifyView())

@discord_bot.tree.command(name="balance", description="所持金を確認します")
async def balance_cmd(interaction: discord.Interaction):
    data = getUserData(interaction.user.id)
    await interaction.response.send_message(f"所持金: {data['balance']:,} コイン", ephemeral=True)

@discord_bot.tree.command(name="daily", description="デイリーボーナスを受け取る")
async def daily_cmd(interaction: discord.Interaction):
    data = getUserData(interaction.user.id)
    now = int(datetime.datetime.now().timestamp() * 1000)
    if now - data.get("lastDaily", 0) < 86400000:
        return await interaction.response.send_message("デイリーボーナスは24時間に1回です。", ephemeral=True)
    data["balance"] = data.get("balance", 0) + 300
    data["lastDaily"] = now
    saveUserData(interaction.user.id, data)
    await interaction.response.send_message("デイリーボーナス +300 コイン獲得！")

@discord_bot.tree.command(name="gacha", description="ガチャを回す")
async def gacha_cmd(interaction: discord.Interaction):
    data = getUserData(interaction.user.id)
    if data.get("balance", 0) < 100: return await interaction.response.send_message("コインが不足しています。", ephemeral=True)
    data["balance"] -= 100; data["totalGachaCount"] = data.get("totalGachaCount", 0) + 1
    
    import random
    rand = random.random() * 100
    if rand < 5:
        data["balance"] += 500; desc = "超激レア (UR) +500コイン"
    elif rand < 15:
        data["multiplier"] = 1.5; data["multiplierExpire"] = int(datetime.datetime.now().timestamp() * 1000) + 7200000; desc = "通話マイニング1.5倍ブースト (2時間)"
    elif rand < 50:
        data["balance"] += 150; desc = "レア (SR) +150コイン"
    else: desc = "ノーマル"
    
    saveUserData(interaction.user.id, data)
    await interaction.response.send_message(embed=discord.Embed(title="ガチャ結果", description=desc, color=0x5865F2))

@discord_bot.tree.command(name="profile", description="プロフィールと実績を確認")
async def profile_cmd(interaction: discord.Interaction, user: discord.Member = None):
    target = user or interaction.user
    data = getUserData(target.id)
    newB = checkAndAwardBadges(data)
    saveUserData(target.id, data)
    v_min = data.get("totalVoiceMinutes", 0)
    embed = discord.Embed(title=f"{target.name} のプロフィール", color=0x00AAFF)
    embed.add_field(name="装着称号", value=data.get("equippedBadge", "なし"), inline=False)
    embed.add_field(name="所持金", value=f"{data.get('balance', 0):,} コイン", inline=True)
    embed.add_field(name="通話時間", value=f"{v_min // 60}時間 {v_min % 60}分", inline=True)
    embed.add_field(name="所持バッジ", value=" | ".join(data.get("badges", [])), inline=False)
    msg = f"新称号獲得: {' '.join(newB)}" if newB else None
    await interaction.response.send_message(content=msg, embeds=[embed])

@discord_bot.tree.command(name="title", description="称号を変更する")
async def title_cmd(interaction: discord.Interaction, name: str):
    data = getUserData(interaction.user.id)
    if name not in data.get("badges", []): return await interaction.response.send_message("その称号を所持していません。", ephemeral=True)
    data["equippedBadge"] = name; saveUserData(interaction.user.id, data)
    await interaction.response.send_message(f"称号を [ {name} ] に変更しました。", ephemeral=True)

@discord_bot.tree.command(name="ticket", description="チケット作成パネル設置")
async def ticket_cmd(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator: return await interaction.response.send_message("権限がありません。", ephemeral=True)
    view = discord.ui.View(timeout=None)
    btn = discord.ui.Button(label="チケットを作成", style=discord.ButtonStyle.primary, custom_id="create_ticket")
    async def cb(inter: discord.Interaction):
        await inter.response.defer(ephemeral=True)
        ch = await inter.guild.channels.create(name=f"ticket-{inter.user.name}", type=discord.ChannelType.text)
        await ch.set_permissions(inter.guild.default_role, view_channel=False)
        await ch.set_permissions(inter.user, view_channel=True, send_messages=True)
        await ch.send(f"{inter.user.mention} さん、ようこそ", view=discord.ui.View().add_item(discord.ui.Button(label="閉じる", style=discord.ButtonStyle.danger, custom_id="close_ticket")))
        await inter.edit_reply(content=f"作成しました: {ch.mention}")
    btn.callback = cb; view.add_item(btn)
    await interaction.response.send_message(embed=discord.Embed(title="お問い合わせ", description="ボタンを押してプライベート相談部屋を作成", color=0x5865F2), view=view)

# ※Discordの引数の最大制限（25個）に収まるよう、最大10個（合計20個の引数）までに安全に調整しています
@discord_bot.tree.command(name="reaction_role", description="【管理者専用】最大10個のロール付与ボタンパネルを作成します")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    title="パネルのタイトル", message="パネルの説明本文",
    role1="ロール1", label1="ラベル1", role2="ロール2", label2="ラベル2",
    role3="ロール3", label3="ラベル3", role4="ロール4", label4="ラベル4",
    role5="ロール5", label5="ラベル5", role6="ロール6", label6="ラベル6",
    role7="ロール7", label7="ラベル7", role8="ロール8", label8="ラベル8",
    role9="ロール9", label9="ラベル9", role10="ロール10", label10="ラベル10"
)
async def reaction_role_command(
    interaction: discord.Interaction, title: str, message: str,
    role1: discord.Role, label1: str,
    role2: discord.Role = None, label2: str = None,
    role3: discord.Role = None, label3: str = None,
    role4: discord.Role = None, label4: str = None,
    role5: discord.Role = None, label5: str = None,
    role6: discord.Role = None, label6: str = None,
    role7: discord.Role = None, label7: str = None,
    role8: discord.Role = None, label8: str = None,
    role9: discord.Role = None, label9: str = None,
    role10: discord.Role = None, label10: str = None
):
    await interaction.response.defer(thinking=True, ephemeral=True)
    roles = [
        (role1, label1), (role2, label2), (role3, label3), (role4, label4), (role5, label5),
        (role6, label6), (role7, label7), (role8, label8), (role9, label9), (role10, label10)
    ]
    buttons_info = []
    for r, l in roles:
        if r and l:
            buttons_info.append((l, r.id))
    
    if not buttons_info:
        return await interaction.followup.send("有効なロールとラベルが指定されていません。", ephemeral=True)

    embed = discord.Embed(title=title, description=message, color=discord.Color.blue())
    view = DynamicRoleView(buttons_info)
    await interaction.channel.send(embed=embed, view=view)
    await interaction.followup.send(f"{len(buttons_info)}個のボタンを持つリアクションロールパネルを送信しました！", ephemeral=True)

@discord_bot.tree.command(name="ip", description="IPアドレス解析")
async def ip_cmd(interaction: discord.Interaction, ip_address: str):
    await interaction.response.defer(thinking=True)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://ip-api.com/json/{ip_address}?lang=ja") as resp: data = await resp.json()
        embed = discord.Embed(title=f"IP解析: {ip_address}", color=discord.Color.blue())
        embed.add_field(name="エリア", value=f"{data.get('country')} {data.get('regionName')} {data.get('city')}")
        embed.add_field(name="ISP", value=data.get('isp'))
        await interaction.followup.send(embed=embed)
    except Exception as e: await interaction.followup.send(f"エラー: {e}")

@discord_bot.tree.command(name="dns", description="DNSレコード解決")
async def dns_cmd(interaction: discord.Interaction, domain_name: str):
    await interaction.response.defer(thinking=True)
    try:
        addr = await asyncio.to_thread(socket.getaddrinfo, domain_name, None)
        ipv4 = {sa[0] for _, _, _, _, sa in addr if _ == socket.AF_INET}
        await interaction.followup.send(embed=discord.Embed(title=f"DNS: {domain_name}", description="\n".join([f"`{ip}`" for ip in ipv4]), color=discord.Color.gold()))
    except Exception as e: await interaction.followup.send(f"解決失敗: {e}")

@discord_bot.tree.command(name="status", description="稼働状況確認")
async def status_cmd(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://discordstatus.com/api/v2/summary.json") as resp: data = await resp.json()
        st = "正常稼働中" if data.get("status", {}).get("indicator") == "none" else "障害あり"
        await interaction.followup.send(embed=discord.Embed(title="Discord稼働状況", description=st, color=discord.Color.dark_embed()))
    except Exception as e: await interaction.followup.send(f"エラー: {e}")

@discord_bot.tree.command(name="userinfo", description="データベースからユーザー情報を調査")
@app_commands.default_permissions(administrator=True)
async def userinfo_cmd(interaction: discord.Interaction, member: discord.Member):
    ip = get_user_ip_from_db(member.id)
    email = get_user_email_from_db(member.id)
    await interaction.response.send_message(embed=discord.Embed(title=f"調査: {member}", description=f"IP: {ip}\nEmail: {email}", color=discord.Color.red()), ephemeral=True)

@discord_bot.tree.command(name="jail", description="ユーザーを監獄送り")
@app_commands.default_permissions(administrator=True)
async def jail_cmd(interaction: discord.Interaction, user: discord.Member, reason: str = "違反行為"):
    if db:
        db.collection("jail_list").document(str(user.id)).set({"username": str(user), "ip": get_user_ip_from_db(user.id), "reason": reason})
    if JAIL_ROLE_ID != 0:
        r = interaction.guild.get_role(JAIL_ROLE_ID)
        if r: await user.add_roles(r)
    await interaction.response.send_message(f"{user.mention} を監獄送りにしました。", ephemeral=True)

@discord_bot.tree.command(name="unjail", description="ユーザーを監獄から解放")
@app_commands.default_permissions(administrator=True)
async def unjail_cmd(interaction: discord.Interaction, user: discord.Member):
    if db: db.collection("jail_list").document(str(user.id)).delete()
    if JAIL_ROLE_ID != 0:
        r = interaction.guild.get_role(JAIL_ROLE_ID)
        if r and r in user.roles: await user.remove_roles(r)
    await interaction.response.send_message(f"{user.mention} を解放しました。", ephemeral=True)

@discord_bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.data and interaction.data.get('custom_id') == 'close_ticket':
        await interaction.response.send_message('5秒後にこのチャンネルを削除します', ephemeral=True)
        await asyncio.sleep(5)
        try: await interaction.channel.delete()
        except Exception: pass

if __name__ == "__main__":
    flask_thread = threading.Thread(target=lambda: app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False))
    flask_thread.daemon = True
    flask_thread.start()
    print(f"Flask server started on port {PORT}")

    if not BOT_TOKEN:
        print("Error: BOT_TOKEN is not set.")
    else:
        discord_bot.run(BOT_TOKEN)
