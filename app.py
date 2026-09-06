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
  <!-- Google Fonts から美しい明朝体（Shippori Mincho）を読み込み -->
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Shippori+Mincho:wght@400;600;800&display=swap" rel="stylesheet">

  <style>
    /* ==========================================
       全体スタイル・フォント設定
       ========================================== */
    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }

    body {
      font-family: "Shippori Mincho", "Yu Mincho", "YuMincho", "Hiragino Mincho ProN", serif;
      color: #ffffff; /* 文字色は白 */
      height: 100vh;
      display: flex;
      justify-content: center;
      align-items: center;
      overflow: hidden;
      position: relative;
    }

    /* ==========================================
       背景動画 & オーバーレイ設定
       ========================================== */
    .video-background {
      position: absolute;
      top: 0;
      left: 0;
      width: 100%;
      height: 100%;
      object-fit: cover; /* 画面全体を満たすように拡大縮小 */
      z-index: -2;
    }

    /* 文字が見やすくなるように背景動画の上に暗い膜を貼る */
    .video-overlay {
      position: absolute;
      top: 0;
      left: 0;
      width: 100%;
      height: 100%;
      background: rgba(0, 0, 0, 0.55); /* 黒色で透過度55% */
      z-index: -1;
    }

    /* ==========================================
       認証カード UI (すりガラス風デザイン)
       ========================================== */
    .container {
      background: rgba(255, 255, 255, 0.08); /* わずかに白い透明カード */
      backdrop-filter: blur(12px);            /* すりガラス効果 */
      -webkit-backdrop-filter: blur(12px);
      border: 1px solid rgba(255, 255, 255, 0.2);
      border-radius: 16px;
      padding: 40px 30px;
      width: 90%;
      max-width: 420px;
      text-align: center;
      box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
    }

    h1 {
      font-size: 1.8rem;
      font-weight: 600;
      margin-bottom: 12px;
      letter-spacing: 0.08em;
    }

    p.subtitle {
      font-size: 0.95rem;
      color: rgba(255, 255, 255, 0.8);
      margin-bottom: 28px;
      line-height: 1.6;
    }

    /* 認証ボタン */
    .verify-btn {
      width: 100%;
      padding: 14px 0;
      font-family: inherit;
      font-size: 1rem;
      font-weight: 600;
      color: #000000;          /* ボタン内文字は黒 */
      background-color: #ffffff; /* ボタン背景は白 */
      border: none;
      border-radius: 8px;
      cursor: pointer;
      transition: all 0.3s ease;
      letter-spacing: 0.05em;
    }

    .verify-btn:hover {
      background-color: rgba(255, 255, 255, 0.85);
      transform: translateY(-2px);
      box-shadow: 0 4px 15px rgba(255, 255, 255, 0.2);
    }

    .verify-btn:disabled {
      opacity: 0.6;
      cursor: not-allowed;
      transform: none;
    }

    /* ステータスメッセージ表示エリア */
    #status-message {
      margin-top: 20px;
      font-size: 0.9rem;
      min-height: 1.2em;
    }
  </style>
</head>
<body>

  <!-- 背景動画 (loop, autoplay, muted, playsinline が必須) -->
  <video class="video-background" autoplay loop muted playsinline>
    <!-- 背景に流したい動画ファイルのパスを指定（例: background.mp4） -->
    <source src="videoplayback.mp4" type="video/mp4">
  </video>

  <!-- 動画の上に重ねる暗いレイヤー -->
  <div class="video-overlay"></div>

  <!-- 認証カード -->
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

      btn.disabled = true;
      btn.innerText = "処理中...";
      statusMsg.innerText = "";

      try {
        // Flaskバックエンドの /verify エンドポイントへリクエスト
        const response = await fetch("/verify", {
          method: "POST",
          headers: {
            "Content-Type": "application/json"
          },
          body: JSON.stringify({
            // 必要に応じてDiscordユーザーIDなどを指定
            user_id: "123456789012345678" 
          })
        });

        const data = await response.json();

        if (response.ok) {
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
        description="下のボタンを押してWebページでルールを承諾してください。",
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
@discord_bot.tree.command(name="kuwakuwa", description=" ")
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
