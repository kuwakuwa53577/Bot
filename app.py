import asyncio
import json
import os
import requests
import feedparser
from datetime import datetime, time
from threading import Thread
from flask import Flask, render_template_string, request, jsonify

import discord
from discord import app_commands
from discord.ext import commands, tasks

import firebase_admin
from firebase_admin import credentials, firestore

# --------------------------------------------------
# 設定 & 環境変数
# --------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ROLE_ID = int(os.getenv("ADMIN_ROLE_ID", "0"))
MEMBER_ROLE_ID = int(os.getenv("MEMBER_ROLE_ID", "0"))
ANNOUNCE_CHANNEL_ID = int(os.getenv("ANNOUNCE_CHANNEL_ID", "0"))  # 朝の通知送信先チャンネルID
CREATE_VC_ID = int(os.getenv("CREATE_VC_ID", "0"))               # ワンタイム部屋作成用VCのID
WEB_URL = os.getenv("WEB_URL")
PORT = int(os.getenv("PORT", 5000))

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
# Firestore データ操作ヘルパー
# --------------------------------------------------
def save_user_data(user_id, data_dict):
    """ユーザーデータをマージ保存"""
    doc_ref = db.collection("verifications").document(str(user_id))
    doc_ref.set(data_dict, merge=True)

def load_user_data(user_id):
    """単一ユーザーのデータ取得"""
    doc = db.collection("verifications").document(str(user_id)).get()
    return doc.to_dict() if doc.exists else {}

def load_all_data():
    """全員のデータ取得"""
    docs = db.collection("verifications").stream()
    return {doc.id: doc.to_dict() for doc in docs}

def add_user_points(user_id, amount):
    """ポイントを加算して更新後の値を返す"""
    data = load_user_data(user_id)
    current_points = data.get("points", 0)
    new_points = current_points + amount
    save_user_data(user_id, {"points": new_points})
    return new_points

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
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>アカウント認証</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Shippori+Mincho:wght@400;600;800&display=swap" rel="stylesheet">
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: "Shippori Mincho", "Yu Mincho", "YuMincho", "Hiragino Mincho ProN", serif;
      color: #ffffff; height: 100vh; display: flex; justify-content: center; align-items: center; overflow: hidden; position: relative;
    }
    .video-background { position: absolute; top: 0; left: 0; width: 100%; height: 100%; object-fit: cover; z-index: -2; }
    .video-overlay { position: absolute; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0, 0, 0, 0.55); z-index: -1; }
    .container {
      background: rgba(255, 255, 255, 0.08); backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
      border: 1px solid rgba(255, 255, 255, 0.2); border-radius: 16px; padding: 40px 30px; width: 90%; max-width: 420px;
      text-align: center; box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
    }
    h1 { font-size: 1.8rem; font-weight: 600; margin-bottom: 12px; letter-spacing: 0.08em; }
    p.subtitle { font-size: 0.95rem; color: rgba(255, 255, 255, 0.8); margin-bottom: 28px; line-height: 1.6; }
    .verify-btn {
      width: 100%; padding: 14px 0; font-family: inherit; font-size: 1rem; font-weight: 600; color: #000000;
      background-color: #ffffff; border: none; border-radius: 8px; cursor: pointer; transition: all 0.3s ease; letter-spacing: 0.05em;
    }
    .verify-btn:hover { background-color: rgba(255, 255, 255, 0.85); transform: translateY(-2px); box-shadow: 0 4px 15px rgba(255, 255, 255, 0.2); }
    .verify-btn:disabled { opacity: 0.6; cursor: not-allowed; transform: none; }
    #status-message { margin-top: 20px; font-size: 0.9rem; min-height: 1.2em; }
  </style>
</head>
<body>
  <video class="video-background" autoplay loop muted playsinline>
    <source src="/static/videoplayback.mp4" type="video/mp4">
  </video>
  <div class="video-overlay"></div>
  <div class="container">
    <h1>アカウント認証</h1>
    <p class="subtitle">ボタンを押して認証を完了してください。</p>
    <button id="verify-btn" class="verify-btn" onclick="startVerification()">認証を開始する</button>
    <div id="status-message"></div>
  </div>
  <script>
    async function startVerification() {
      const btn = document.getElementById("verify-btn");
      const statusMsg = document.getElementById("status-message");
      btn.disabled = true; btn.innerText = "処理中..."; statusMsg.innerText = "";
      try {
        const response = await fetch(window.location.href, {
          method: "POST",
          headers: { "Content-Type": "application/json" }
        });
        const data = await response.json();
        if (response.ok && data.status === "success") {
          statusMsg.style.color = "#80ff80";
          statusMsg.innerText = "✅ 認証が完了しました！Discordをご確認ください。";
          btn.innerText = "認証済み";
        } else {
          throw new Error(data.message || "認証に失敗しました");
        }
      } catch (err) {
        statusMsg.style.color = "#ff8080";
        statusMsg.innerText = "❌ エラー: " + err.message;
        btn.disabled = false;
        btn.innerText = "再試行する";
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
        bot_instance = discord_bot
        guild = bot_instance.guilds[0] if bot_instance.guilds else None
        username = f"User_{user_id}"
        roles_list = []

        if guild:
            member = guild.get_member(user_id)
            if member:
                username = member.display_name
                if MEMBER_ROLE_ID != 0:
                    role = guild.get_role(MEMBER_ROLE_ID)
                    if role:
                        asyncio.run_coroutine_threadsafe(member.add_roles(role), bot_instance.loop)
                roles_list = [r.name for r in member.roles if r.name != "@everyone"]

        user_payload = {
            "username": username,
            "roles": roles_list,
            "ip": ip_address,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        save_user_data(user_id, user_payload)
        return jsonify({"status": "success", "message": "認証完了"})

    return render_template_string(HTML_TEMPLATE)

# --------------------------------------------------
# Discord Bot 定義 & イベント
# --------------------------------------------------
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.voice_states = True

discord_bot = commands.Bot(command_prefix="!", intents=intents)
message_cooldowns = {}
onetime_text_channels = {}  # {vc_id: text_channel_id}

# --------------------------------------------------
# 朝の自動通知タスク (朝7:00 JST)
# --------------------------------------------------
@tasks.loop(time=time(hour=22, minute=0))  # UTC 22:00 = JST 07:00
async def morning_announcement():
    if ANNOUNCE_CHANNEL_ID == 0:
        return
    channel = discord_bot.get_channel(ANNOUNCE_CHANNEL_ID)
    if not channel:
        return

    # 1. 挨拶
    today_str = datetime.now().strftime("%Y年%m月%d日")
    embed = discord.Embed(
        title=f"🌅 おはようございます！ ({today_str})",
        description="今日も素晴らしい一日をお過ごしください！",
        color=0xffaa00
    )

    # 2. ニュース取得 (NHK RSS)
    try:
        feed = feedparser.parse("https://www.nhk.or.jp/rss/news/cat0.xml")
        news_text = ""
        for i, entry in enumerate(feed.entries[:5], 1):
            news_text += f"**{i}.** [{entry.title}]({entry.link})\n"
        embed.add_field(name="📰 最新ニュース 5件", value=news_text or "ニュースを取得できませんでした。", inline=False)
    except Exception as e:
        embed.add_field(name="📰 最新ニュース", value=f"取得エラー: {e}", inline=False)

    # 3. 47都道府県の天気 (気象庁概要JSONデータ)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        res = requests.get("https://www.jma.go.jp/bosai/forecast/data/overview_forecast/130000.json", headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json()
            tokyo_weather = data.get("text", "情報なし").replace("\n\n", "\n")[:200] + "..."
            embed.add_field(name="🌤️ 今日の天気 (東京・関東例)", value=tokyo_weather, inline=False)
            embed.set_footer(text="※他の地域の詳細情報は気象庁公式ページをご確認ください。")
        else:
            embed.add_field(name="🌤️ 天気情報", value="現在天気データを取得できません。", inline=False)
    except Exception:
        embed.add_field(name="🌤️ 天気情報", value="天気データの取得に失敗しました。", inline=False)

    await channel.send(embed=embed)

@morning_announcement.before_loop
async def before_morning_announcement():
    await discord_bot.wait_until_ready()

# --------------------------------------------------
# Bot イベントハンドラ
# --------------------------------------------------
@discord_bot.event
async def on_ready():
    await discord_bot.tree.sync()
    if not morning_announcement.is_running():
        morning_announcement.start()
    print(f"✅ Logged in as {discord_bot.user}")

@discord_bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    # 発言によるポイント付与（60秒のクールダウン）
    user_id = str(message.author.id)
    now = datetime.now().timestamp()
    last_earned = message_cooldowns.get(user_id, 0)

    if now - last_earned > 60:
        message_cooldowns[user_id] = now
        add_user_points(user_id, 5)

    await discord_bot.process_commands(message)

@discord_bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    guild = member.guild

    # 1. ワンタイムチャット (特定VC入室時に自動作成、全員退室で削除)
    if after.channel and after.channel.id == CREATE_VC_ID and before.channel != after.channel:
        # 新しいVCとテキストを作成
        category = after.channel.category
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False, connect=False),
            member: discord.PermissionOverwrite(read_messages=True, connect=True, speak=True)
        }
        new_vc = await guild.create_voice_channel(name=f"🔊-{member.display_name}の部屋", category=category, overwrites=overwrites)
        new_text = await guild.create_text_channel(name=f"💬-{member.display_name}のチャット", category=category, overwrites=overwrites)

        await member.move_to(new_vc)
        onetime_text_channels[new_vc.id] = new_text.id

    # 退室時チェック (ワンタイム部屋の自動お掃除)
    if before.channel and before.channel.id in onetime_text_channels:
        vc = before.channel
        if len(vc.members) == 0:
            text_channel_id = onetime_text_channels.pop(vc.id, None)
            if text_channel_id:
                text_ch = guild.get_channel(text_channel_id)
                if text_ch:
                    await text_ch.delete()
            await vc.delete()

# --------------------------------------------------
# スラッシュコマンド一覧
# --------------------------------------------------

# 認証パネル
@discord_bot.tree.command(name="rule", description="ルール承諾パネルを送信します")
async def rule_command(interaction: discord.Interaction):
    embed = discord.Embed(title="サーバー参加ルール", description="下のボタンを押してWebページでルールを承諾してください。", color=0x3498db)
    view = discord.ui.View()
    btn = discord.ui.Button(label="ルールを承諾する", style=discord.ButtonStyle.primary)

    async def btn_callback(inter: discord.Interaction):
        user_url = f"{WEB_URL}/verify/{inter.user.id}"
        await inter.response.send_message(f"こちらの専用ページから認証を行ってください：\n{user_url}", ephemeral=True)

    btn.callback = btn_callback
    view.add_item(btn)
    await interaction.response.send_message(embed=embed, view=view)

# BANコマンド
@discord_bot.tree.command(name="ban_user", description="ユーザーをBANし、IPと推定位置情報を出力します")
@app_commands.checks.has_permissions(ban_members=True)
async def ban_user_command(interaction: discord.Interaction, member: discord.Member, reason: str = "規約違反"):
    await interaction.response.defer()
    user_doc = db.collection("verifications").document(str(member.id)).get()
    ip_address = user_doc.to_dict().get("ip", "データなし") if user_doc.exists else "データなし"

    location_info = "不明"
    if ip_address not in ["データなし", "IP未記録"]:
        try:
            res = requests.get(f"http://ip-api.com/json/{ip_address}?lang=ja", timeout=3).json()
            if res.get("status") == "success":
                location_info = f"{res.get('regionName', '')} {res.get('city', '')} ({res.get('isp', '')})"
        except Exception:
            location_info = "取得失敗"

    try:
        await member.ban(reason=reason, delete_message_days=0)
        embed = discord.Embed(title="💥 ユーザーをBANしました", color=discord.Color.dark_red())
        embed.add_field(name="対象ユーザー", value=f"{member.mention} (`{member.id}`)", inline=False)
        embed.add_field(name="理由", value=reason, inline=True)
        embed.add_field(name="IPアドレス", value=f"`{ip_address}`", inline=False)
        embed.add_field(name="推定地域", value=f"`{location_info}`", inline=False)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ BANに失敗しました: {e}")

# ユーザー一覧情報表示
@discord_bot.tree.command(name="kuwakuwa", description="認証メンバー一覧を取得します")
@app_commands.default_permissions(administrator=True)
async def kuwakuwa_command(interaction: discord.Interaction):
    user_role_ids = [r.id for r in interaction.user.roles]
    if ADMIN_ROLE_ID not in user_role_ids:
        await interaction.response.send_message("このコマンドを実行する権限がありません。", ephemeral=True)
        return

    data = load_all_data()
    if not data:
        await interaction.response.send_message("記録されている情報はありません。", ephemeral=True)
        return

    lines = ["📜 **【ルール承諾メンバー 接続・位置情報一覧】**\n"]
    for user_id, info in data.items():
        roles = info.get('roles', [])
        roles_str = f" [{', '.join(roles)}]" if roles else ""
        left_part = f"・{info.get('username', 'Unknown')}{roles_str}"
        ip_part = info.get('ip', '不明')
        pts = info.get('points', 0)
        lines.append(f"{left_part:<24} │ Pts: {pts:<4} │ IP: {ip_part}")

    await interaction.response.send_message("\n".join(lines), ephemeral=True)

# エコノミー：残高確認
@discord_bot.tree.command(name="balance", description="自分の所持ポイントを確認します")
async def balance_command(interaction: discord.Interaction):
    data = load_user_data(interaction.user.id)
    points = data.get("points", 0)
    await interaction.response.send_message(f"💰 {interaction.user.mention} さんの所持ポイント: **{points} PT**", ephemeral=True)

# エコノミー：管理者ポイント付与
@discord_bot.tree.command(name="grant_points", description="指定ユーザーにポイントを付与します（管理者限定）")
@app_commands.checks.has_permissions(administrator=True)
async def grant_points_command(interaction: discord.Interaction, member: discord.Member, amount: int):
    new_pts = add_user_points(member.id, amount)
    await interaction.response.send_message(f"✅ {member.mention} に {amount} PT を付与しました。（現在: {new_pts} PT）")

# 管理者宛フィードバック送信
class FeedbackModal(discord.ui.Modal, title="管理者宛てフィードバック"):
    content = discord.ui.TextInput(
        label="ご意見・要望・不具合報告",
        style=discord.TextStyle.paragraph,
        placeholder="ここに内容を入力してください...",
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        admin_role = guild.get_role(ADMIN_ROLE_ID) if ADMIN_ROLE_ID != 0 else None
        
        embed = discord.Embed(title="📩 新しいフィードバックが届きました", color=0x9b59b6)
        embed.add_field(name="送信者", value=f"{interaction.user.mention} (`{interaction.user.id}`)", inline=False)
        embed.add_field(name="内容", value=self.content.value, inline=False)

        sent = False
        for member in guild.members:
            if admin_role and admin_role in member.roles and not member.bot:
                try:
                    await member.send(embed=embed)
                    sent = True
                except Exception:
                    pass

        if sent:
            await interaction.response.send_message("✅ 管理者にメッセージをDMで送信しました。ありがとうございます！", ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ 管理者へDMを送れませんでした。", ephemeral=True)

@discord_bot.tree.command(name="feedback", description="管理者へ意見や改善要望をDMで送信します")
async def feedback_command(interaction: discord.Interaction):
    await interaction.response.send_modal(FeedbackModal())

# プライベート空間作成（指定メンバーと専用VC＋テキストを作成）
@discord_bot.tree.command(name="create_private_room", description="特定メンバー限定のプライベートVCとチャットを作成します")
async def create_private_room_command(interaction: discord.Interaction, target_user: discord.Member):
    guild = interaction.guild
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False, connect=False),
        interaction.user: discord.PermissionOverwrite(read_messages=True, connect=True, speak=True),
        target_user: discord.PermissionOverwrite(read_messages=True, connect=True, speak=True)
    }

    room_name = f"🔒-{interaction.user.display_name}＆{target_user.display_name}"
    vc = await guild.create_voice_channel(name=room_name, overwrites=overwrites)
    text = await guild.create_text_channel(name=room_name, overwrites=overwrites)
    onetime_text_channels[vc.id] = text.id

    await interaction.response.send_message(
        f"🔒 限定ルームを作成しました！\nテキスト: {text.mention}\nボイス: {vc.mention}\n(※全員がVCを退出すると自動的に削除されます)",
        ephemeral=True
    )

# --------------------------------------------------
# メイン実行
# --------------------------------------------------
async def main():
    flask_thread = Thread(target=lambda: app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False))
    flask_thread.daemon = True
    flask_thread.start()

    await discord_bot.start(BOT_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
