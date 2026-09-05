import asyncio
import json
import os
from datetime import datetime
from threading import Thread
from flask import Flask, render_template_string, request
import discord
from discord import app_commands
from discord.ext import commands

# --------------------------------------------------
# 設定 & 環境変数
# --------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ROLE_ID = int(os.getenv("ADMIN_ROLE_ID", "0"))
WEB_URL = os.getenv("WEB_URL")  # 例: https://xxx.onrender.com
PORT = int(os.getenv("PORT", 5000))
DATA_FILE = "user_data.json"

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

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
        button { background: #89b4fa; color: #11111b; border: none; padding: 12px 24px; font-size: 16px; border-radius: 6px; cursor: pointer; font-weight: bold; width: 100%; }
        button:hover { background: #b4befe; }
        .info { color: #a6adc8; font-size: 13px; margin-top: 15px; }
    </style>
</head>
<body>
    <div class="card">
        <h2>サーバーのルール承諾</h2>
        <p>以下のボタンを押すとルールに同意し、接続情報（IPアドレス）が記録されます。</p>
        <form method="POST">
            <button type="submit">ルールに同意して認証する</button>
        </form>
        <p class="info">※安全のため、IPアドレスは管理者のみに共有されます。</p>
    </div>
</body>
</html>
"""

@app.route("/")
def home():
    return "Bot status: Running"

@app.route("/verify/<int:user_id>", methods=["GET", "POST"])
def verify(user_id):
    # Renderなどのリバースプロキシ経由のIPを取得
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
                roles_list = [r.name for r in member.roles if r.name != "@everyone"]

        data = load_data()
        data[str(user_id)] = {
            "username": username,
            "roles": roles_list,
            "ip": ip_address,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        save_data(data)

        return "<h2 style='text-align:center; padding-top: 50px;'>認証が完了しました！このページを閉じてDiscordに戻ってください。</h2>"

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

@discord_bot.tree.command(name="iplist", description="承諾メンバーのIPアドレス一覧を表示します（管理者限定）")
async def iplist_command(interaction: discord.Interaction):
    user_role_ids = [r.id for r in interaction.user.roles]
    if ADMIN_ROLE_ID not in user_role_ids:
        await interaction.response.send_message("このコマンドを実行する権限がありません。", ephemeral=True)
        return

    data = load_data()
    if not data:
        await interaction.response.send_message("記録されているIPアドレスはありません。", ephemeral=True)
        return

    lines = ["📜 **【ルール承諾メンバー & IPアドレス一覧】**\n"]
    for user_id, info in data.items():
        roles_str = f" [{', '.join(info['roles'])}]" if info['roles'] else ""
        left_part = f"・{info['username']}{roles_str}"
        ip_part = info['ip']
        lines.append(f"{left_part:<30} │ {ip_part}")

    output_text = "```text\n" + "\n".join(lines) + "\n```"
    await interaction.response.send_message(output_text, ephemeral=True)

# --------------------------------------------------
# メイン実行
# --------------------------------------------------
async def main():
    # Flaskを別スレッドで起動
    flask_thread = Thread(target=lambda: app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False))
    flask_thread.daemon = True
    flask_thread.start()

    # Bot起動
    await discord_bot.start(BOT_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
