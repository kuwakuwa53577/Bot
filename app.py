import asyncio
import json
import os
import datetime
from threading import Thread

import discord
from discord import app_commands
from discord.ext import commands
from flask import Flask, render_template_string, request, jsonify

import firebase_admin
from firebase_admin import credentials, firestore

import logging

# ログ設定
logging.basicConfig(level=logging.INFO)

# --------------------------------------------------
# 設定 & 環境変数
# --------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("DISCORD_BOT_TOKEN")
PROXY_URL = os.getenv("PROXY_URL")
MEMBER_ROLE_ID = int(os.getenv("MEMBER_ROLE_ID", "0"))
WEB_URL = os.getenv("WEB_URL", "").rstrip("/")  # 末尾のスラッシュを除去
PORT = int(os.getenv("PORT", 10000))

# 📌 【追加機能】認証を許可する特定のユーザーID（複数指定可能・カンマ区切りで環境変数に設定可）
verify_env = os.getenv("VERIFIED_USER_IDS", "")
if verify_env:
    VERIFIED_USER_IDS = {int(uid.strip()) for uid in verify_env.split(",") if uid.strip().isdigit()}
else:
    VERIFIED_USER_IDS = {1488072800278413500,1481668037286297682,1411674732025221120,1446053386989473944,1481200122866962522,1380211228894560307}

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

try:
    db = firestore.client()
except Exception as e:
    db = None
    print(f"⚠️ Firestoreクライアントの取得に失敗しました: {e}")

# --------------------------------------------------
# Firestore データヘルパー
# --------------------------------------------------
def save_user_data(user_id, data_dict):
    if not db:
        print("⚠️ データベースが未接続のためデータを保存できません")
        return
    try:
        doc_ref = db.collection("verifications").document(str(user_id))
        doc_ref.set(data_dict, merge=True)
    except Exception as e:
        print(f"❌ Firestore保存エラー: {e}")

def load_all_data():
    if not db:
        print("⚠️ データベースが未接続のためデータを読み込めません")
        return {}
    try:
        docs = db.collection("verifications").stream()
        return {doc.id: doc.to_dict() for doc in docs}
    except Exception as e:
        print(f"❌ Firestore読み込みエラー: {e}")
        return {}

# --------------------------------------------------
# Flask Webサーバー（IP取得＆認証）
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
    # 📌 1. 特定の人（ホワイトリスト）だけが認証できるようにチェック
    if VERIFIED_USER_IDS and user_id not in VERIFIED_USER_IDS:
        if request.method == "POST":
            return jsonify({"status": "error", "message": "あなたはこのサーバーで認証する権限がありません。"}), 403
        return "❌ 403 Forbidden: あなたはこのサーバーで認証する権限がありません。", 403

    ip_address = request.headers.get("X-Forwarded-For", request.remote_addr)
    if ip_address and "," in ip_address:
        ip_address = ip_address.split(",")[0].strip()

    if request.method == "POST":
        # ※ 同一IP重複ブロックの処理はここにありましたが、要望により廃止しました。

        guild = discord_bot.guilds[0] if discord_bot.guilds else None
        username = f"User_{user_id}"
        roles_list = []

        if guild:
            async def process_member():
                nonlocal username, roles_list
                try:
                    member = await guild.fetch_member(user_id)
                    if member:
                        username = member.display_name
                        roles_list = [r.name for r in member.roles if r.name != "@everyone"]
                        if MEMBER_ROLE_ID != 0:
                            role = guild.get_role(MEMBER_ROLE_ID)
                            if role:
                                await member.add_roles(role)
                except Exception as e:
                    print(f"❌ メンバー処理エラー: {e}")

            future = asyncio.run_coroutine_threadsafe(process_member(), discord_bot.loop)
            try:
                future.result(timeout=10)
            except Exception as e:
                print(f"❌ 非同期処理タイムアウト/エラー: {e}")

        save_user_data(user_id, {
            "username": username,
            "roles": roles_list,
            "ip": ip_address,
            "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })
        return jsonify({"status": "success", "message": "認証完了"})

    return render_template_string(HTML_TEMPLATE)

# --------------------------------------------------
# Discord Bot UI Views (動的ボタンコンポーネント)
# --------------------------------------------------
class RuleVerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="ルールを承諾する", style=discord.ButtonStyle.primary, custom_id="rule_verify_btn")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        
        # 📌 認証パネルのボタンを押した段階でもホワイトリストのチェックを行う
        if VERIFIED_USER_IDS and user_id not in VERIFIED_USER_IDS:
            await interaction.response.send_message("❌ あなたはこのサーバーで認証する権限がありません。", ephemeral=True)
            return

        base_url = WEB_URL if WEB_URL else f"http://localhost:{PORT}"
        verify_url = f"{base_url}/verify/{user_id}"
        
        await interaction.response.send_message(
            f"専用認証ページ：\n{verify_url}", 
            ephemeral=True
        )

# --------------------------------------------------
# Discord Bot クラス
# --------------------------------------------------
class MyBot(commands.Bot):
    async def setup_hook(self):
        self.add_view(RuleVerifyView())
        synced = await self.tree.sync()
        print(f"✅ スラッシュコマンド同期完了: {len(synced)} 件")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

discord_bot = MyBot(command_prefix="!", intents=intents, proxy=PROXY_URL)

@discord_bot.event
async def on_ready():
    print("========================================")
    print("✅ Discord Bot 接続成功")
    print(f"🤖 Bot: {discord_bot.user}")
    print("========================================")

# --------------------------------------------------
# スラッシュコマンド (/rule, /kuwakuwa)
# --------------------------------------------------

@discord_bot.tree.command(name="rule", description="ルール承諾パネルを送信します")
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
async def rule_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="サーバー参加ルール",
        description="下のボタンを押してWebページでルールを承諾してください。",
        color=0x3498db
    )
    await interaction.response.send_message(embed=embed, view=RuleVerifyView())

@discord_bot.tree.command(name="kuwakuwa", description="認証メンバーおよびIPアドレス一覧を取得します")
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
@app_commands.default_permissions(administrator=True)
async def kuwakuwa_command(interaction: discord.Interaction):
    try:
        data = load_all_data()
        if not data:
            await interaction.response.send_message("記録はありません（またはデータベースが未接続です）。", ephemeral=True)
            return
        
        lines = []
        for uid, info in data.items():
            username = info.get('username', 'Unknown')
            ip = info.get('ip', '未記録')
            updated_at = info.get('updated_at', '日時不明')
            lines.append(f"・**{username}** (ID: `{uid}`) │ IP: `{ip}` │ ({updated_at})")
        
        current_chunk = "📜 **【ルール承諾メンバー・IP情報一覧】**\n\n"
        chunks = []
        
        for line in lines:
            if len(current_chunk) + len(line) + 1 > 1950:
                chunks.append(current_chunk)
                current_chunk = ""
            current_chunk += line + "\n"
        
        if current_chunk:
            chunks.append(current_chunk)

        await interaction.response.send_message(chunks[0], ephemeral=True)
        for chunk in chunks[1:]:
            await interaction.followup.send(chunk, ephemeral=True)

    except Exception as e:
        print(f"❌ kuwakuwaコマンド実行エラー: {e}")
        if not interaction.response.is_done():
            await interaction.response.send_message(f"❌ エラーが発生しました: {e}", ephemeral=True)

# --------------------------------------------------
# Bot起動処理 (リトライ機能付き)
# --------------------------------------------------
async def start_bot_with_retry():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN が環境変数に設定されていません。")

    retry_delay = 15
    max_delay = 300

    while True:
        try:
            print("🚀 Discord Bot に接続を試みています...")
            await discord_bot.start(BOT_TOKEN)
            break
        except discord.errors.HTTPException as e:
            if e.status in (429, 502, 504):
                print(f"⚠️ Discord API 通信エラー ({e.status})。{retry_delay} 秒後に再試行します...")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_delay)
            else:
                print(f"❌ Discord HTTP エラー: {e}")
                await asyncio.sleep(15)
        except Exception as e:
            print(f"❌ エラーが発生しました: {e}")
            await asyncio.sleep(15)

# --------------------------------------------------
# メイン実行 (Flask と Discord Bot を同時に起動)
# --------------------------------------------------
async def main():
    # Flaskを別スレッドで起動（Renderのポート監視対策）
    flask_thread = Thread(target=lambda: app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False))
    flask_thread.daemon = True
    flask_thread.start()
    print(f"🌐 Flask サーバーをポート {PORT} で起動しました")

    # Discord Bot 起動（リトライ機能付き）
    await start_bot_with_retry()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("👋 プログラムを終了します。")
