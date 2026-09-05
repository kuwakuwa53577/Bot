import asyncio
import json
import os
from datetime import datetime
from threading import Thread
from flask import Flask, render_template_string, request
import discord
from discord import app_commands
from discord.ext import commands

import firebase_admin
from firebase_admin import credentials, firestore

# --------------------------------------------------
# 設定 & 環境変数
# --------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ROLE_ID = int(os.getenv("ADMIN_ROLE_ID", "0"))
MEMBER_ROLE_ID = int(os.getenv("MEMBER_ROLE_ID", "0"))
WEB_URL = os.getenv("WEB_URL")
PORT = int(os.getenv("PORT", 5000))

# --------------------------------------------------
# Firebase 初期化
# --------------------------------------------------
firebase_creds_json = os.getenv("FIREBASE_CREDENTIALS")

if firebase_creds_json:
    try:
        cred_dict = json.loads(firebase_creds_json)
        # Render等で \n が文字列になっている場合を考慮して改行コードへ変換
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
# Firestore データ読み書き関数
# --------------------------------------------------
def save_user_data(user_id, data_dict):
    """ユーザー単位でFirestoreにデータを保存・更新"""
    doc_ref = db.collection("verifications").document(str(user_id))
    doc_ref.set(data_dict, merge=True)

def load_all_data():
    """Firestoreから全員分のデータを取得"""
    docs = db.collection("verifications").stream()
    all_data = {}
    for doc in docs:
        all_data[doc.id] = doc.to_dict()
    return all_data

# --------------------------------------------------
# Flask Webサーバー
# --------------------------------------------------
app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ルール承諾 & 認証</title>
    <style>
        body { font-family: sans-serif; background: #1e1e2e; color: #cdd6f4; text-align: center; padding: 40px 20px; }
        .card { background: #313244; padding: 30px; border-radius: 12px; display: inline-block; max-width: 400px; width: 100%; box-sizing: border-box; }
        select, button { width: 100%; padding: 12px; font-size: 16px; border-radius: 6px; border: none; margin-bottom: 15px; box-sizing: border-box; }
        select { background: #45475a; color: #cdd6f4; }
        button { background: #89b4fa; color: #11111b; font-weight: bold; cursor: pointer; }
        button:hover { background: #b4befe; }
        .info { color: #a6adc8; font-size: 13px; margin-top: 15px; }
    </style>
</head>
<body>
    <div class="card">
        <h2>サーバーのルール承諾</h2>
        <p>居住地域を選択し、ルールに同意して認証してください。</p>
        <form method="POST">
            <select name="location" required>
                <option value="" disabled selected>お住まいの都道府県を選択</option>
                <option value="東京都">東京都</option>
                <option value="神奈川県">神奈川県</option>
                <option value="大阪府">大阪府</option>
                <option value="愛知県">愛知県</option>
                <option value="その他・海外">その他・海外</option>
            </select>
            <button type="submit">ルールに同意して認証する</button>
        </form>
        <p class="info">※接続情報（IPアドレス等）は管理者のみに共有されます。</p>
    </div>
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
                
                # メンバーロールの自動付与
                if MEMBER_ROLE_ID != 0:
                    role = guild.get_role(MEMBER_ROLE_ID)
                    if role:
                        asyncio.run_coroutine_threadsafe(
                            member.add_roles(role),
                            bot_instance.loop
                        )

                roles_list = [r.name for r in member.roles if r.name != "@everyone"]

        user_payload = {
            "username": username,
            "roles": roles_list,
            "ip": ip_address,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
        # Firestoreへ保存
        save_user_data(user_id, user_payload)

        return "<h2 style='text-align:center; padding-top: 50px;'>認証が完了しました！ロールが付与されましたので、Discordに戻ってください。</h2>"

    return render_template_string(HTML_TEMPLATE)

# --------------------------------------------------
# Discord Bot
# --------------------------------------------------
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

discord_bot = commands.Bot(command_prefix="!", intents=intents)

@discord_bot.event
async def on_ready():
    await discord_bot.tree.sync()
    print(f"Logged in as {discord_bot.user}")

@discord_bot.tree.command(name="rule", description="ルール承諾パネルを送信します")
async def rule_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="サーバー参加ルール",
        description="下のボタンを押してWebページでルールを承諾してください。\n※IPアドレスが管理者に記録されます。",
        color=0x3498db
    )
    view = discord.ui.View()
    btn = discord.ui.Button(label="ルールを承諾する", style=discord.ButtonStyle.primary)
    
    async def btn_callback(inter: discord.Interaction):
        user_url = f"{WEB_URL}/verify/{inter.user.id}"
        await inter.response.send_message(
            f"こちらの専用ページから認証を行ってください：\n{user_url}", 
            ephemeral=True
        )

    btn.callback = btn_callback
    view.add_item(btn)
    await interaction.response.send_message(embed=embed, view=view)

# /kuwakuwa コマンド（管理者のみ実行可能）
@discord_bot.tree.command(name="kuwakuwa", description="接続情報一覧")
@app_commands.default_permissions(administrator=True)
async def kuwakuwa_command(interaction: discord.Interaction):
    user_role_ids = [r.id for r in interaction.user.roles]
    if ADMIN_ROLE_ID not in user_role_ids:
        await interaction.response.send_message("このコマンドを実行する権限がありません。", ephemeral=True)
        return

    # Firestoreから全データ取得
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
        geo_part = info.get('geo', '位置情報なし')
        
        lines.append(f"{left_part:<28} │ IP: {ip_part}\n  └ Maps: {geo_part}")

    output_text = "\n".join(lines)
    await interaction.response.send_message(output_text, ephemeral=True)

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
