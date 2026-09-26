import asyncio
import json
import os
import random
from datetime import datetime
from threading import Thread

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
from flask import Flask, render_template_string, request, jsonify

import firebase_admin
from firebase_admin import credentials, firestore

# --------------------------------------------------
# 設定 & 環境変数
# --------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ROLE_ID = int(os.getenv("ADMIN_ROLE_ID", "0"))
MEMBER_ROLE_ID = int(os.getenv("MEMBER_ROLE_ID", "0"))
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))  # フィードバック受け取り用管理者ID
MORNING_CHANNEL_ID = int(os.getenv("MORNING_CHANNEL_ID", "0"))  # 朝の通知用チャンネルID
CREATE_ONETIME_VC_ID = int(os.getenv("CREATE_ONETIME_VC_ID", "0"))  # ワンタイム用トリガーVC ID
WEB_URL = os.getenv("WEB_URL")
PORT = int(os.getenv("PORT", 10000))

# --------------------------------------------------
# Firebase 初期化
# --------------------------------------------------
firebase_creds_json = os.getenv("FIREBASE_CREDENTIALS")

if firebase_creds_json:
    try:
        cred_dict = json.loads(firebase_creds_json)
        if "private_key" in cred_dict:
            cred_dict["private_key"] = cred_dict["private_key"].replace("\\n", "\n")
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred)
        print("✅ Firebase Admin SDK の初期化に成功しました")
    except Exception as e:
        print(f"❌ Firebase 初期化エラー: {e}")
elif os.path.exists("serviceAccountKey.json"):
    cred = credentials.Certificate("serviceAccountKey.json")
    firebase_admin.initialize_app(cred)
    print("✅ ローカルファイルで Firebase の初期化に成功しました")
else:
    print("⚠️ 警告: FIREBASE_CREDENTIALS または serviceAccountKey.json が見つかりません。")

db = firestore.client()

# --------------------------------------------------
# Firestore データヘルパー
# --------------------------------------------------
def save_user_data(user_id, data_dict):
    doc_ref = db.collection("verifications").document(str(user_id))
    doc_ref.set(data_dict, merge=True)

def load_all_data():
    docs = db.collection("verifications").stream()
    return {doc.id: doc.to_dict() for doc in docs}

def add_user_points(user_id, amount):
    doc_ref = db.collection("economy").document(str(user_id))
    doc = doc_ref.get()
    current = doc.to_dict().get("points", 0) if doc.exists else 0
    doc_ref.set({"points": current + amount}, merge=True)
    return current + amount

def get_user_points(user_id):
    doc = db.collection("economy").document(str(user_id)).get()
    return doc.to_dict().get("points", 0) if doc.exists else 0

# --------------------------------------------------
# Flask Webサーバー
# --------------------------------------------------
app = Flask(__name__)

@app.route("/ping")
def ping():
    return "pong", 200

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>アカウント認証</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: sans-serif; color: #fff; height: 100vh; display: flex; justify-content: center; align-items: center; background: #111; }
    .container { background: rgba(255,255,255,0.1); padding: 40px; border-radius: 16px; text-align: center; width: 90%; max-width: 400px; }
    .verify-btn { width: 100%; padding: 14px; font-size: 1rem; color: #000; background: #fff; border: none; border-radius: 8px; cursor: pointer; margin-top: 20px; }
  </style>
</head>
<body>
  <div class="container">
    <h1>アカウント認証</h1>
    <p>ボタンを押して認証を完了してください。</p>
    <button id="verify-btn" class="verify-btn" onclick="startVerification()">認証を開始する</button>
    <div id="status-message" style="margin-top:15px;"></div>
  </div>
  <script>
    async function startVerification() {
      const btn = document.getElementById("verify-btn");
      const statusMsg = document.getElementById("status-message");
      btn.disabled = true; btn.innerText = "処理中...";
      try {
        const res = await fetch(window.location.href, { method: "POST", headers: { "Content-Type": "application/json" } });
        const data = await res.json();
        if (res.ok && data.status === "success") {
          statusMsg.style.color = "#80ff80"; statusMsg.innerText = "✅ 認証完了！Discordをご確認ください。";
          btn.innerText = "認証済み";
        } else { throw new Error(data.message || "認証失敗"); }
      } catch (err) {
        statusMsg.style.color = "#ff8080"; statusMsg.innerText = "❌ エラー: " + err.message;
        btn.disabled = false; btn.innerText = "再試行する";
      }
    }
  </script>
</body>
</html>
"""

@app.route("/")
def home():
    return "Bot status: Running"

@app.route("/verify/<int:user_id>", methods=["GET", "POST"])
def verify(user_id):
    ip_address = request.headers.get("X-Forwarded-For", request.remote_addr)
    if ip_address and "," in ip_address:
        ip_address = ip_address.split(",")[0].strip()

    if request.method == "POST":
        guild = discord_bot.guilds[0] if discord_bot.guilds else None
        username = f"User_{user_id}"
        roles_list = []

        if guild:
            member = guild.get_member(user_id)
            if member:
                username = member.display_name
                if MEMBER_ROLE_ID != 0:
                    role = guild.get_role(MEMBER_ROLE_ID)
                    if role:
                        asyncio.run_coroutine_threadsafe(member.add_roles(role), discord_bot.loop)
                roles_list = [r.name for r in member.roles if r.name != "@everyone"]

        save_user_data(user_id, {
            "username": username, "roles": roles_list, "ip": ip_address,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })
        return jsonify({"status": "success", "message": "認証完了"})

    return render_template_string(HTML_TEMPLATE)

# --------------------------------------------------
# Discord Bot クラス & 定期タスク
# --------------------------------------------------
class MyBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user_cooldowns = {}
        self.onetime_channels = {}  # {vc_id: text_channel_id}

    async def setup_hook(self):
        await self.tree.sync()
        self.morning_task.start()
        print("✅ スラッシュコマンド同期完了 & 定期タスク起動")

    @tasks.loop(hours=24)
    async def morning_task(self):
        if MORNING_CHANNEL_ID == 0:
            return
        channel = self.get_channel(MORNING_CHANNEL_ID)
        if not channel:
            return

        async with aiohttp.ClientSession() as session:
            weather_text = "☀️ **【全国47都道府県 本日の天気予報】**\n"
            weather_text += "・北海道・東北: 晴れのち曇り\n・関東・東海: 快晴 ☀️\n・関西・中国・四国: 時々雨 ☔\n・九州・沖縄: 晴れ 🌤️\n"
            
            news_items = [
                "1. 最新の国内経済トピックに関する発表がありました。",
                "2. 本日の全国的な気象傾向について気象庁が警戒を呼びかけています。",
                "3. 最新技術に関する新たな国際標準が採択されました。",
                "4. 地域社会の活性化に向けた新たな取り組みがスタート。",
                "5. 今週末のスポーツ大会に向けた出場選手の発表。"
            ]
            
            embed = discord.Embed(title="🌅 おはようございます！朝の定期通知", color=discord.Color.gold())
            embed.add_field(name="🌤️ 天気予報概要", value=weather_text, inline=False)
            embed.add_field(name="📰 新着トピック5選", value="\n".join(news_items), inline=False)
            
            await channel.send(embed=embed)

    @morning_task.before_loop
    async def before_morning_task(self):
        await self.wait_until_ready()

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.voice_states = True

discord_bot = MyBot(command_prefix="!", intents=intents)

# --------------------------------------------------
# イベントハンドラー
# --------------------------------------------------
@discord_bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    now = datetime.now().timestamp()
    last_time = discord_bot.user_cooldowns.get(message.author.id, 0)
    if now - last_time > 30:
        discord_bot.user_cooldowns[message.author.id] = now
        add_user_points(message.author.id, 5)

    await discord_bot.process_commands(message)

@discord_bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    guild = member.guild

    if after.channel and after.channel.id == CREATE_ONETIME_VC_ID:
        category = after.channel.category
        new_vc = await guild.create_voice_channel(f"🔊-{member.display_name}の部屋", category=category)
        new_txt = await guild.create_text_channel(f"💬-{member.display_name}専用チャット", category=category)
        
        await new_txt.set_permissions(guild.default_role, read_messages=False)
        await new_txt.set_permissions(member, read_messages=True, send_messages=True)

        discord_bot.onetime_channels[new_vc.id] = new_txt.id
        await member.move_to(new_vc)
        await new_txt.send(f"{member.mention} 専用のテキストチャットを作成しました。全員が退室すると自動削除されます。")

    if before.channel and before.channel.id in discord_bot.onetime_channels:
        if len(before.channel.members) == 0:
            txt_id = discord_bot.onetime_channels.pop(before.channel.id, None)
            if txt_id:
                txt_chan = guild.get_channel(txt_id)
                if txt_chan:
                    await txt_chan.delete()
            await before.channel.delete()

# --------------------------------------------------
# スラッシュコマンド群
# --------------------------------------------------

@discord_bot.tree.command(name="wake_up", description="おぜう仕様でeveryoneに超強力な目覚まし通知を送信します")
async def wake_up_command(interaction: discord.Interaction):
    # 実行権限チェック（実行者自身のIDまたは管理者ロールのみ許可）
    if interaction.user.id != ADMIN_USER_ID:
        user_role_ids = [r.id for r in interaction.user.roles]
        if ADMIN_ROLE_ID not in user_role_ids:
            await interaction.response.send_message("❌ このコマンドを実行する権限がありません。", ephemeral=True)
            return

    await interaction.response.send_message("⏰ おぜうモードでeveryoneへの通知を開始します…！", ephemeral=True)

    # 1つのメッセージに大量のメンションを詰め込む（おぜうBot風の絨毯爆撃スタイル）
    # ※文字列の長さに応じて、1回の送信に含まれる@everyoneの数を調整してください
    spam_mentions = " ".join(["@everyone"] * 40)

    wake_messages = [
        f"{spam_mentions}\n<@everyoneうおｗ",
        f"{spam_mentions}\n<@everyone",
        f"{spam_mentions}\n<@everyone",
        f"{spam_mentions}\n<@everyone",
        f"{spam_mentions}\n<@everyone>"
    ]

    image_url = "https://logo-imagecluster.img.mixi.jp/photo/comm/99/35/1429935_233.gif"

    for i in range(100):
        try:
            embed = discord.Embed(
                title=f"🚨 うおｗうおｗうおｗ ({i+1}/100)",
                description=wake_messages[i],
                color=discord.Color.red()
            )
            embed.set_thumbnail(url=image_url)
            
            # 本文（content）側にもeveryoneを仕込むことで、より確実に通知を飛ばします
            await interaction.channel.send(content="@everyone 🚨🚨🚨", embed=embed)
            
            # レートリミット（5秒制限）に引っかからない絶妙な間隔（1.5秒〜2秒）
            await asyncio.sleep(1.5)
        except Exception as e:
            print(f"目覚まし送信エラー: {e}")
            break


@discord_bot.tree.command(name="rule", description="ルール承諾パネルを送信します")
async def rule_command(interaction: discord.Interaction):
    embed = discord.Embed(title="サーバー参加ルール", description="下のボタンを押してWebページでルールを承諾してください。", color=0x3498db)
    view = discord.ui.View()
    btn = discord.ui.Button(label="ルールを承諾する", style=discord.ButtonStyle.primary)
    
    async def btn_callback(inter: discord.Interaction):
        await inter.response.send_message(f"専用認証ページ：\n{WEB_URL}/verify/{inter.user.id}", ephemeral=True)

    btn.callback = btn_callback
    view.add_item(btn)
    await interaction.response.send_message(embed=embed, view=view)

@discord_bot.tree.command(name="ban_user", description="ユーザーをBANし、IPと位置情報を出力")
@app_commands.checks.has_permissions(ban_members=True)
async def ban_user_command(interaction: discord.Interaction, member: discord.Member, reason: str = "規約違反"):
    await interaction.response.defer()
    user_doc = db.collection("verifications").document(str(member.id)).get()
    ip_address = user_doc.to_dict().get("ip", "未記録") if user_doc.exists else "データなし"
    
    location_info = "不明"
    if ip_address not in ["データなし", "未記録"]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"http://ip-api.com/json/{ip_address}?lang=ja", timeout=3) as res:
                    if res.status == 200:
                        data = await res.json()
                        if data.get("status") == "success":
                            location_info = f"{data.get('regionName','')} {data.get('city','')} ({data.get('isp','')})"
        except Exception: pass

    try:
        await member.ban(reason=reason, delete_message_days=0)
        embed = discord.Embed(title="💥 ユーザーをBANしました", color=discord.Color.dark_red())
        embed.add_field(name="対象", value=f"{member.mention} (`{member.id}`)", inline=False)
        embed.add_field(name="理由", value=reason, inline=True)
        embed.add_field(name="IPアドレス", value=f"`{ip_address}`", inline=False)
        embed.add_field(name="推定位置", value=f"`{location_info}`", inline=False)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ BAN失敗: {e}")

@discord_bot.tree.command(name="kuwakuwa", description="認証メンバー一覧を取得")
@app_commands.default_permissions(administrator=True)
async def kuwakuwa_command(interaction: discord.Interaction):
    data = load_all_data()
    if not data:
        await interaction.response.send_message("記録はありません。", ephemeral=True)
        return
    lines = ["📜 **【ルール承諾メンバー 一覧】**\n"]
    for uid, info in data.items():
        lines.append(f"・{info.get('username','Unknown')} │ IP: {info.get('ip','不明')}")
    await interaction.response.send_message("\n".join(lines), ephemeral=True)

@discord_bot.tree.command(name="balance", description="自分の所持ポイントを確認します")
async def balance_command(interaction: discord.Interaction):
    pts = get_user_points(interaction.user.id)
    await interaction.response.send_message(f"💰 {interaction.user.mention} さんの所持ポイント: **{pts} PT**", ephemeral=True)

@discord_bot.tree.command(name="daily", description="デイリーログインボーナス（100PT）を獲得します")
async def daily_command(interaction: discord.Interaction):
    pts = add_user_points(interaction.user.id, 100)
    await interaction.response.send_message(f"🎁 デイリーボーナス100PTを受け取りました！ (現在: {pts} PT)", ephemeral=True)

@discord_bot.tree.command(name="feedback", description="管理者へご意見・ご要望を匿名送信します")
async def feedback_command(interaction: discord.Interaction, message: str):
    if ADMIN_USER_ID == 0:
        await interaction.response.send_message("❌ 管理者IDが設定されていません。", ephemeral=True)
        return
    admin = await discord_bot.fetch_user(ADMIN_USER_ID)
    if admin:
        embed = discord.Embed(title="📩 サーバーフィードバック受信", description=message, color=discord.Color.blue())
        embed.set_footer(text=f"送信元ユーザーID: {interaction.user.id}")
        await admin.send(embed=embed)
        await interaction.response.send_message("✅ 管理者へメッセージを送信しました！", ephemeral=True)

@discord_bot.tree.command(name="pvc", description="特定の人だけが入れるプライベート部屋を作成します")
async def pvc_command(interaction: discord.Interaction, target_user: discord.Member):
    guild = interaction.guild
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False, connect=False),
        interaction.user: discord.PermissionOverwrite(read_messages=True, connect=True, send_messages=True),
        target_user: discord.PermissionOverwrite(read_messages=True, connect=True, send_messages=True),
    }
    cat = await guild.create_category(f"🔒-{interaction.user.display_name}の秘密部屋", overwrites=overwrites)
    await guild.create_voice_channel("通話部屋", category=cat)
    txt = await guild.create_text_channel("専用チャット", category=cat)
    
    await txt.send(f"{interaction.user.mention} {target_user.mention} プライベート部屋を作成しました！")
    await interaction.response.send_message("✅ プライベート部屋を作成しました！", ephemeral=True)

# --------------------------------------------------
# リトライ付きBot起動処理
# --------------------------------------------------
async def start_bot_with_retry():
    retry_delay = 15
    max_delay = 300

    while True:
        try:
            print("🚀 Discord Bot に接続を試みています...")
            await discord_bot.start(BOT_TOKEN)
            break
        except discord.errors.HTTPException as e:
            if e.status in (429, 502, 504):
                print(f"⚠️ Discord API レート制限/通信エラー ({e.status})。{retry_delay} 秒後に再試行します...")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_delay)
            else:
                print(f"❌ Discord HTTP エラー: {e}")
                await asyncio.sleep(15)
        except Exception as e:
            print(f"❌ 予期せぬエラーが発生しました: {e}")
            await asyncio.sleep(15)

# --------------------------------------------------
# メイン実行
# --------------------------------------------------
async def main():
    # Flaskを先にバックグラウンドスレッドで確実に起動
    flask_thread = Thread(target=lambda: app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False))
    flask_thread.daemon = True
    flask_thread.start()
    print(f"🌐 Flask サーバーをポート {PORT} で起動しました")

    # Discord Botをリトライ制御付きで起動
    await start_bot_with_retry()

if __name__ == "__main__":
    asyncio.run(main())
