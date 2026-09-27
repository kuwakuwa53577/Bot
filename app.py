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
        synced = await self.tree.sync()
        self.morning_task.start()
        print(f"✅ スラッシュコマンド同期完了: {len(synced)} 件")
        print("   - User Install対応: /balance /daily /feedback")
        print("   - Guild Install専用: /wake_up /rule /ban_user /kuwakuwa /pvc")

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
intents.voice_states = True

discord_bot = MyBot(command_prefix="!", intents=intents)

@discord_bot.event
async def on_ready():
    print("========================================")
    print("✅ Discord Bot 接続成功")
    print(f"🤖 Bot: {discord_bot.user}")
    print(f"🆔 ID: {discord_bot.user.id if discord_bot.user else 'unknown'}")
    print(f"🏠 Guild数: {len(discord_bot.guilds)}")
    print("========================================")

@discord_bot.event
async def on_disconnect():
    print("⚠️ Discord Gateway から切断されました")

@discord_bot.event
async def on_resumed():
    print("🔄 Discord Gateway に再接続しました")

@discord_bot.event
async def on_error(event, *args, **kwargs):
    import traceback
    print(f"❌ Discordイベントエラー: {event}")
    traceback.print_exc()

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
# 目覚まし送信処理（非同期バックグラウンドタスク）
# --------------------------------------------------
async def send_wake_messages(interaction: discord.Interaction, count: int):
    MAX_WAKE_COUNT = 10
    count = min(max(int(count), 1), MAX_WAKE_COUNT)

    image_url = "https://logo-imagecluster.img.mixi.jp/photo/comm/99/35/1429935_233.gif"
    allowed_mentions = discord.AllowedMentions(
        everyone=True,
        users=False,
        roles=False
    )

    for i in range(count):
        try:
            embed = discord.Embed(
                title=f"🚨 うおｗうおｗうおｗ ({i + 1}/{count})",
                description="🚨 目覚まし通知です！",
                color=discord.Color.red()
            )
            embed.set_thumbnail(url=image_url)

            await interaction.followup.send(
                content="@everyone 🚨🚨🚨",
                embed=embed,
                allowed_mentions=allowed_mentions,
                wait=True
            )

            if i + 1 < count:
                await asyncio.sleep(2.0)

        except discord.errors.HTTPException as e:
            print(f"⚠️ wake_up HTTPエラー [{e.status}]: {e}")

            if e.status == 429:
                retry_after = getattr(e, "retry_after", None)
                if retry_after is None:
                    retry_after = 5.0
                print(f"⚠️ Discordレート制限。{retry_after}秒待機します。")
                await asyncio.sleep(float(retry_after))
                continue

            if e.status in (500, 502, 503, 504):
                await asyncio.sleep(5.0)
                continue

            break

        except discord.Forbidden as e:
            print(f"❌ wake_up 権限エラー: {e}")
            break

        except Exception as e:
            print(f"❌ wake_up 送信エラー [{i + 1}/{count}]: {e}")
            break

async def run_wake_messages(interaction: discord.Interaction, count: int):
    try:
        await send_wake_messages(interaction, count)
    except Exception as e:
        print(f"❌ wake_up バックグラウンドタスク失敗: {e}")

# --------------------------------------------------
# スラッシュコマンド
# --------------------------------------------------
@discord_bot.tree.command(
    name="wake_up",
    description="目覚まし通知を送信します"
)
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(
    guilds=True,
    dms=True,
    private_channels=True
)
@app_commands.describe(
    count="送信回数（1～10）"
)
async def wake_up_command(
    interaction: discord.Interaction,
    count: int = 1
):
    if interaction.user.id != ADMIN_USER_ID:
        user_role_ids = [
            r.id for r in getattr(interaction.user, "roles", [])
        ]

        if ADMIN_ROLE_ID == 0 or ADMIN_ROLE_ID not in user_role_ids:
            await interaction.response.send_message(
                "❌ このコマンドを実行する権限がありません。",
                ephemeral=True
            )
            return

    if count < 1:
        await interaction.response.send_message(
            "❌ 回数は1以上を指定してください。",
            ephemeral=True
        )
        return

    if count > 10:
        await interaction.response.send_message(
            "❌ このコマンドは1回の実行につき最大10回です。",
            ephemeral=True
        )
        return

    await interaction.response.send_message(
        f"⏰ おぜうモードでeveryoneへの通知を {count} 回送信開始します…！",
        ephemeral=False
    )

    asyncio.create_task(
        run_wake_messages(interaction, count)
    )

@discord_bot.tree.command(name="rule", description="ルール承諾パネルを送信します")
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
async def rule_command(interaction: discord.Interaction):
    embed = discord.Embed(title="サーバー参加ルール", description="下のボタンを押してWebページでルールを承諾してください。", color=0x3498db)
    view = discord.ui.View()
    btn = discord.ui.Button(label="ルールを承諾する", style=discord.ButtonStyle.primary)
    
    async def btn_callback(inter: discord.Interaction):
        await inter.response.send_message(f"専用認証ページ：\n{WEB_URL}/verify/{inter.user.id}", ephemeral=True)

    btn.callback = btn_callback
    view.add_item(btn)
    await interaction.response.send_message(embed=embed, view=view)

@discord_bot.tree.command(name="ban_user", description="ユーザーをBANし、記録された認証情報を確認してBANします")
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
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
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
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
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def balance_command(interaction: discord.Interaction):
    pts = get_user_points(interaction.user.id)
    await interaction.response.send_message(f"💰 {interaction.user.mention} さんの所持ポイント: **{pts} PT**", ephemeral=True)

@discord_bot.tree.command(name="daily", description="デイリーログインボーナス（100PT）を獲得します")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def daily_command(interaction: discord.Interaction):
    pts = add_user_points(interaction.user.id, 100)
    await interaction.response.send_message(f"🎁 デイリーボーナス100PTを受け取りました！ (現在: {pts} PT)", ephemeral=True)

@discord_bot.tree.command(name="feedback", description="管理者へご意見・ご要望を送信します")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
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
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
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
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN が Render の Environment Variables に設定されていません。")

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
    flask_thread = Thread(target=lambda: app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False))
    flask_thread.daemon = True
    flask_thread.start()
    print(f"🌐 Flask サーバーをポート {PORT} で起動しました")

    await start_bot_with_retry()

if __name__ == "__main__":
    asyncio.run(main())
