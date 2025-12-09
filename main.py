import os
import threading
import requests
import logging
import time
import subprocess
from flask import Flask, request, abort
from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Update, CallbackQuery
DB_USER = “lakicalinuur”
DB_PASSWORD = “DjReFoWZGbwjry8K”
DB_APPNAME = “SpeechBot”
MONGO_URI = f”mongodb+srv://{DB_USER}:{DB_PASSWORD}@cluster0.n4hdlxk.mongodb.net/?retryWrites=true&w=majority&appName={DB_APPNAME}”
FFMPEG_BINARY = os.environ.get(“FFMPEG_BINARY”, “/usr/bin/ffmpeg”)
BOT_TOKEN = os.environ.get(“BOT2_TOKEN”, “”)
API_ID = int(os.environ.get(“API_ID”, “0”))
API_HASH = os.environ.get(“API_HASH”, “”)
WEBHOOK_URL_BASE = os.environ.get(“WEBHOOK_URL_BASE”, “”)
PORT = int(os.environ.get(“PORT”, “8080”))
WEBHOOK_PATH = os.environ.get(“WEBHOOK_PATH”, “/webhook/”)
WEBHOOK_URL = WEBHOOK_URL_BASE.rstrip(’/’) + WEBHOOK_PATH if WEBHOOK_URL_BASE else “”
REQUEST_TIMEOUT_GEMINI = int(os.environ.get(“REQUEST_TIMEOUT_GEMINI”, “300”))
MAX_UPLOAD_MB = int(os.environ.get(“MAX_UPLOAD_MB”, “20”))
MAX_UPLOAD_SIZE = MAX_UPLOAD_MB * 1024 * 1024
MAX_MESSAGE_CHUNK = 4095
GEMINI_MODEL = os.environ.get(“GEMINI_MODEL”, “gemini-2.5-flash-lite”)
DOWNLOADS_DIR = os.environ.get(“DOWNLOADS_DIR”, “./downloads”)
DAILY_LIMIT = int(os.environ.get(“DAILY_LIMIT”, “19”))
WINDOW_SECONDS = 24 * 3600
TUTORIAL_CHANNEL = “@NotifyBchat”
os.makedirs(DOWNLOADS_DIR, exist_ok=True)
logging.basicConfig(level=logging.INFO, format=’%(asctime)s - %(levelname)s - %(message)s’)
LANGS = [
(“🇬🇧 English”,“en”), (“🇸🇦 العربية”,“ar”), (“🇪🇸 Español”,“es”), (“🇫🇷 Français”,“fr”),
(“🇷🇺 Русский”,“ru”), (“🇩🇪 Deutsch”,“de”), (“🇮🇳 हिन्दी”,“hi”), (“🇮🇷 فارسی”,“fa”),
(“🇮🇩 Indonesia”,“id”), (“🇺🇦 Українська”,“uk”), (“🇦🇿 Azərbaycan”,“az”), (“🇮🇹 Italiano”,“it”),
(“🇹🇷 Türkçe”,“tr”), (“🇧🇬 Български”,“bg”), (“🇷🇸 Srpski”,“sr”), (“🇵🇰 اردو”,“ur”),
(“🇹🇭 ไทย”,“th”), (“🇻🇳 Tiếng Việt”,“vi”), (“🇯🇵 日本語”,“ja”), (“🇰🇷 한국어”,“ko”),
(“🇨🇳 中文”,“zh”), (“🇳🇱 Nederlands:nl”, “nl”), (“🇸🇪 Svenska”,“sv”), (“🇳🇴 Norsk”,“no”),
(“🇮🇱 עברית”,“he”), (“🇩🇰 Dansk”,“da”), (“🇪🇹 አማርኛ”,“am”), (“🇫🇮 Suomi”,“fi”),
(“🇧🇩 বাংলা”,“bn”), (“🇰🇪 Kiswahili”,“sw”), (“🇪🇹 Oromo”,“om”), (“🇳🇵 नेपाली”,“ne”),
(“🇵🇱 Polski”,“pl”), (“🇬🇷 Ελληνικά”,“el”), (“🇨🇿 Čeština”,“cs”), (“🇮🇸 Íslenska”,“is”),
(“🇱🇹 Lietuvių”,“lt”), (“🇱🇻 Latviešu”,“lv”), (“🇭🇷 Hrvatski”,“hr”), (“🇷🇸 Bosanski”,“bs”),
(“🇭🇺 Magyar”,“hu”), (“🇷🇴 Română”,“ro”), (“🇸🇴 Somali”,“so”), (“🇲🇾 Melayu”,“ms”),
(“🇺🇿 O’zbekcha”,“uz”), (“🇵🇭 Tagalog”,“tl”), (“🇵🇹 Português”,“pt”)
]
user_transcriptions = {}
action_usage = {}
user_keys = {}
user_awaiting_key = {}
lock = threading.Lock()
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
mongo_client.admin.command(“ping”)
db = mongo_client.get_database(DB_APPNAME or “SpeechBotDB”)
users_col = db.get_collection(“users”)
actions_col = db.get_collection(“action_usage”)
for doc in users_col.find({}):
try:
uid = int(doc[“uid”])
user_keys[uid] = {
“key”: doc.get(“key”),
“count”: int(doc.get(“count”, 0)),
“window_start”: int(doc.get(“window_start”)) if doc.get(“window_start”) is not None else None
}
except:
continue
for doc in actions_col.find({}):
k = doc.get(“key”)
try:
c = int(doc.get(“count”, 0))
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
users_col.delete_many({“uid”: uid})
return
users_col.update_one(
{“uid”: uid},
{”$set”: {“uid”: uid, “key”: info.get(“key”), “count”: int(info.get(“count”, 0)), “window_start”: info.get(“window_start”)}},
upsert=True
)
def persist_action_usage_to_db(key):
if actions_col is None:
return
cnt = action_usage.get(key, 0)
actions_col.update_one({“key”: key}, {”$set”: {“key”: key, “count”: int(cnt)}}, upsert=True)
def is_gemini_key(key):
if not key:
return False
k = key.strip()
return k.startswith(“AIza”) or k.startswith(“AIzaSy”)
def store_user_key(uid, key):
with lock:
user_keys[uid] = {“key”: key.strip(), “count”: 0, “window_start”: now_ts()}
user_awaiting_key.pop(uid, None)
persist_user_to_db(uid)
def reset_count_if_needed(uid):
with lock:
info = user_keys.get(uid)
if not info and users_col is not None:
doc = users_col.find_one({“uid”: uid})
if not doc:
return
info = {“key”: doc.get(“key”), “count”: int(doc.get(“count”, 0)), “window_start”: int(doc.get(“window_start”)) if doc.get(“window_start”) is not None else None}
user_keys[uid] = info
if not info:
return
ws = info.get(“window_start”)
if ws is None:
info[“count”] = 0
info[“window_start”] = now_ts()
persist_user_to_db(uid)
return
elapsed = now_ts() - ws
if elapsed >= WINDOW_SECONDS:
info[“count”] = 0
info[“window_start”] = now_ts()
persist_user_to_db(uid)
def increment_count(uid):
with lock:
info = user_keys.get(uid)
if not info and users_col is not None:
doc = users_col.find_one({“uid”: uid})
if not doc:
return
info = {“key”: doc.get(“key”), “count”: int(doc.get(“count”, 0)), “window_start”: int(doc.get(“window_start”)) if doc.get(“window_start”) is not None else None}
user_keys[uid] = info
if not info:
return
info[“count”] = info.get(“count”, 0) + 1
if info.get(“window_start”) is None:
info[“window_start”] = now_ts()
persist_user_to_db(uid)
def seconds_left_for_user(uid):
with lock:
info = user_keys.get(uid)
if not info and users_col is not None:
doc = users_col.find_one({“uid”: uid})
if doc:
info = {“key”: doc.get(“key”), “count”: int(doc.get(“count”, 0)), “window_start”: int(doc.get(“window_start”)) if doc.get(“window_start”) is not None else None}
user_keys[uid] = info
if not info:
return 0
ws = info.get(“window_start”)
if ws is None:
return 0
rem = WINDOW_SECONDS - (now_ts() - ws)
return rem if rem > 0 else 0
def format_hms(secs):
h = secs // 3600
m = (secs % 3600) // 60
s = secs % 60
return f”{h}h {m}m {s}s”
def get_user_key_or_raise(uid):
with lock:
info = user_keys.get(uid)
if not info and users_col is not None:
doc = users_col.find_one({“uid”: uid})
if doc:
info = {“key”: doc.get(“key”), “count”: int(doc.get(“count”, 0)), “window_start”: int(doc.get(“window_start”)) if doc.get(“window_start”) is not None else None}
user_keys[uid] = info
if not info or not info.get(“key”):
raise RuntimeError(“API_KEY_MISSING”)
ws = info.get(“window_start”)
if ws is None:
info[“window_start”] = now_ts()
info[“count”] = 0
persist_user_to_db(uid)
return info[“key”]
elapsed = now_ts() - ws
if elapsed >= WINDOW_SECONDS:
info[“window_start”] = now_ts()
info[“count”] = 0
persist_user_to_db(uid)
return info[“key”]
if info.get(“count”, 0) >= DAILY_LIMIT:
rem = WINDOW_SECONDS - elapsed
raise RuntimeError(f”API_DAILY_LIMIT_REACHED|{int(rem)}”)
return info[“key”]
def convert_to_wav(input_path: str) -> str:
if not FFMPEG_BINARY:
raise RuntimeError(“FFmpeg binary not found.”)
output_path = os.path.join(DOWNLOADS_DIR, f”{os.path.basename(input_path).split(’.’)[0]}converted.wav”)
command = [FFMPEG_BINARY, “-i”, input_path, “-acodec”, “pcm_s16le”, “-ac”, “1”, “-ar”, “16000”, output_path, “-y”]
subprocess.run(command, check=True, capture_output=True, timeout=REQUEST_TIMEOUT_GEMINI)
return output_path
def gemini_api_call(endpoint, payload, key, headers=None):
url = f”https://generativelanguage.googleapis.com/v1beta/{endpoint}?key={key}”
resp = requests.post(url, headers=headers or {“Content-Type”: “application/json”}, json=payload, timeout=REQUEST_TIMEOUT_GEMINI)
resp.raise_for_status()
return resp.json()
def upload_and_transcribe_gemini(file_path: str, uid: int) -> str:
original_path, converted_path = file_path, None
if os.path.splitext(file_path)[1].lower() not in [”.wav”, “.mp3”, “.aiff”, “.aac”, “.ogg”, “.flac”]:
converted_path = convert_to_wav(file_path)
file_path = converted_path
file_size = os.path.getsize(file_path)
mime_type = “audio/wav”
key = get_user_key_or_raise(uid)
uploaded_name = None
try:
upload_url = f”https://generativelanguage.googleapis.com/upload/v1beta/files?key={key}”
headers = {
“X-Goog-Upload-Protocol”: “raw”,
“X-Goog-Upload-Command”: “start, upload, finalize”,
“X-Goog-Upload-Header-Content-Length”: str(file_size),
“Content-Type”: mime_type
}
with open(file_path, ‘rb’) as f:
up_resp = requests.post(upload_url, headers=headers, data=f.read(), timeout=REQUEST_TIMEOUT_GEMINI).json()
uploaded_name = up_resp.get(“name”, up_resp.get(“file”, {}).get(“name”))
uploaded_uri = up_resp.get(“uri”, up_resp.get(“file”, {}).get(“uri”))
if not uploaded_name:
raise RuntimeError(“Upload failed.”)
prompt = “Transcribe the audio in this file. Automatically detect the language and provide a clean transcription. Do not add intro phrases.”
payload = {“contents”: [{“parts”: [{“fileData”: {“mimeType”: mime_type, “fileUri”: uploaded_uri}}, {“text”: prompt}]}]}
data = gemini_api_call(f”models/{GEMINI_MODEL}:generateContent”, payload, key)
res_text = data[“candidates”][0][“content”][“parts”][0][“text”]
increment_count(uid)
return res_text
finally:
if uploaded_name:
try:
requests.delete(f”https://generativelanguage.googleapis.com/v1beta/{uploaded_name}?key={key}”, timeout=5)
except:
pass
if converted_path and os.path.exists(converted_path):
os.remove(converted_path)
def ask_gemini(text, instruction, uid):
key = get_user_key_or_raise(uid)
payload = {“contents”: [{“parts”: [{“text”: f”{instruction}\n\n{text}”}]}]}
data = gemini_api_call(f”models/{GEMINI_MODEL}:generateContent”, payload, key)
res_text = data[“candidates”][0][“content”][“parts”][0][“text”]
increment_count(uid)
return res_text
def build_action_keyboard(text_len):
btns = [[InlineKeyboardButton(“⭐️ Get translating”, callback_data=“translate_menu|”)]]
if text_len > 1000:
btns.append([InlineKeyboardButton(“Summarize”, callback_data=“summarize|”)])
return InlineKeyboardMarkup(inline_keyboard=btns)
def build_lang_keyboard(origin):
btns = []
row = []
for i, (lbl, code) in enumerate(LANGS, 1):
row.append(InlineKeyboardButton(lbl, callback_data=f”lang|{code}|{lbl}|{origin}”))
if i % 3 == 0:
btns.append(row)
row = []
if row:
btns.append(row)
return InlineKeyboardMarkup(inline_keyboard=btns)
def send_key_missing_alert(chat_id, app):
try:
chat_info = app.get_chat(TUTORIAL_CHANNEL)
if getattr(chat_info, “pinned_message”, None):
app.forward_messages(chat_id, TUTORIAL_CHANNEL, chat_info.pinned_message.message_id)
except:
pass
app = Client(“speech_bot”, api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
flask_app = Flask(name)
@app.on_message(filters.command([“start”, “help”]) & filters.private)
async def send_welcome(client, message):
welcome_text = “👋 Salaam!\n• Send me\n• voice message\n• audio file\n• video\n• to transcribe for free”
await message.reply_text(welcome_text)
user_awaiting_key[message.from_user.id] = True
@app.on_message(filters.command(“setkey”) & filters.private)
async def setkey_cmd(client, message):
args = message.text.split(maxsplit=1)
if len(args) < 2:
await message.reply_text(“Usage: /setkey YOUR_GEMINI_KEY”)
return
key = args[1].strip()
if not is_gemini_key(key):
user_awaiting_key[message.from_user.id] = True
await message.reply_text(“❌ not  Gemini key try again”)
return
store_user_key(message.from_user.id, key)
await message.reply_text(“☑️ Okay, your daily limit is 19 requests.\nNow send me the audio or video so I can transcribe”)
@app.on_message(filters.private & filters.text)
async def text_handler(client, message):
uid = message.from_user.id
if user_awaiting_key.get(uid) and not message.text.startswith(”/”):
key = message.text.strip()
if not is_gemini_key(key):
user_awaiting_key[uid] = True
await message.reply_text(“❌ not  Gemini key try again”)
return
store_user_key(uid, key)
await message.reply_text(“☑️ Okay, your daily limit is 19 requests.\nNow send me the audio or video so I can transcribe”)
return
if message.text.startswith(”/getcount”):
info = user_keys.get(uid)
if not info:
send_key_missing_alert(message.chat.id, client)
return
reset_count_if_needed(uid)
cnt = info.get(‘count’, 0)
rem = seconds_left_for_user(uid)
if cnt >= DAILY_LIMIT:
await message.reply_text(f”You have reached the daily limit of {DAILY_LIMIT}. Time remaining: {format_hms(rem)}.”)
else:
await message.reply_text(f”Used: {cnt}. Remaining time in window: {format_hms(rem)}. Limit: {DAILY_LIMIT}.”)
return
if message.text.startswith(”/removekey”):
if uid in user_keys:
user_keys.pop(uid, None)
if users_col is not None:
users_col.delete_many({“uid”: uid})
await message.reply_text(“Key removed from memory.”)
else:
await message.reply_text(“No key found.”)
return
@app.on_callback_query()
async def callback_router(client, callback_query: CallbackQuery):
data = callback_query.data or “”
if data.startswith(“lang|”):
parts = data.split(”|”)
if len(parts) >= 4:
, code, lbl, origin = parts[0], parts[1], parts[2], parts[3]
try:
await callback_query.message.edit_reply_markup(reply_markup=None)
except:
pass
await process_text_action(client, callback_query, origin, f”Translate to {lbl}”, f”Translate this text in to language {lbl}. No extra text ONLY return the translated text.”)
return
if data.startswith(“translate_menu|”) or data.startswith(“summarize|”):
action = data.split(”|”)[0]
if action == “translate_menu”:
try:
await callback_query.message.edit_reply_markup(reply_markup=build_lang_keyboard(“trans”))
except:
pass
else:
try:
await callback_query.message.edit_reply_markup(reply_markup=None)
except:
pass
await process_text_action(client, callback_query, callback_query.message.message_id, “Summarize”, “Summarize this in original language.”)
@app.on_message(filters.voice | filters.audio | filters.video | filters.document)
async def handle_media(client, message):
media = message.voice or message.audio or message.video or message.document
if not media:
return
if getattr(media, ‘file_size’, 0) > MAX_UPLOAD_SIZE:
await message.reply_text(f”Just Send me a file less than {MAX_UPLOAD_MB}MB 😎”)
return
await client.send_chat_action(message.chat.id, ‘typing’)
file_path = os.path.join(DOWNLOADS_DIR, f”temp{message.id}{getattr(media, ‘file_unique_id’, ‘’)}”)
try:
downloaded = await client.download_media(media, file_path)
try:
text = upload_and_transcribe_gemini(downloaded, message.from_user.id)
except Exception as e:
em = str(e)
if em == “API_KEY_MISSING”:
send_key_missing_alert(message.chat.id, client)
return
if em.startswith(“API_DAILY_LIMIT_REACHED”):
parts = em.split(”|”)
secs = int(parts[1]) if len(parts) > 1 else seconds_left_for_user(message.from_user.id)
await message.reply_text(f”Daily limit reached. Time left: {format_hms(secs)}.”)
return
raise
if not text:
raise ValueError(“Empty response”)
sent = await send_long_text(client, message.chat.id, text, message.id, message.from_user.id)
if sent:
sent_id = sent.message_id
user_transcriptions.setdefault(message.chat.id, {})[sent_id] = {“text”: text, “origin”: message.id}
try:
await client.edit_message_reply_markup(message.chat.id, sent_id, reply_markup=build_action_keyboard(len(text)))
except:
pass
except Exception as e:
await message.reply_text(f”❌ Error: {e}”)
finally:
if os.path.exists(file_path):
os.remove(file_path)
async def process_text_action(client, callback_query: CallbackQuery, origin, log_action, prompt_instr):
chat_id = callback_query.message.chat.id
msg_id = callback_query.message.message_id
data = user_transcriptions.get(chat_id, {}).get(msg_id)
if not data:
await callback_query.answer(“Data not found (expired). Resend file.”, show_alert=True)
return
text = data[“text”]
key = f”{chat_id}|{msg_id}|{log_action}”
used = action_usage.get(key, 0)
if “Summarize” in log_action and used >= 1:
await callback_query.answer(“Already summarized!”, show_alert=True)
return
await callback_query.answer(“Processing…”)
await client.send_chat_action(chat_id, ‘typing’)
try:
res = ask_gemini(text, prompt_instr, callback_query.from_user.id)
with lock:
action_usage[key] = action_usage.get(key, 0) + 1
persist_action_usage_to_db(key)
await send_long_text(client, chat_id, res, data[“origin”], callback_query.from_user.id, log_action)
except Exception as e:
msg = str(e)
if msg == “API_KEY_MISSING”:
send_key_missing_alert(chat_id, client)
elif msg.startswith(“API_DAILY_LIMIT_REACHED”):
parts = msg.split(”|”)
secs = int(parts[1]) if len(parts) > 1 else seconds_left_for_user(callback_query.from_user.id)
await client.send_message(chat_id, f”Daily limit reached. Time left: {format_hms(secs)}.”)
else:
await client.send_message(chat_id, f”Error: {e}”)
async def send_long_text(client, chat_id, text, reply_id, uid, action=“Transcript”):
if len(text) > MAX_MESSAGE_CHUNK:
fname = os.path.join(DOWNLOADS_DIR, f”{action}.txt”)
with open(fname, “w”, encoding=“utf-8”) as f:
f.write(text)
sent = await client.send_document(chat_id, fname, caption=“Open this file and copy the text inside 👍”, reply_to_message_id=reply_id)
try:
os.remove(fname)
except:
pass
return sent
return await client.send_message(chat_id, text, reply_to_message_id=reply_id)
@flask_app.route(”/”, methods=[“GET”])
def index_route():
return “Bot Running”, 200
@flask_app.route(WEBHOOK_PATH, methods=[“POST”])
def webhook():
if request.headers.get(‘content-type’) == ‘application/json’:
body = request.get_data().decode(‘utf-8’)
try:
update = Update.de_json(body)
app.process_updates([update])
return ‘’, 200
except:
return ‘’, 200
abort(403)
if name == “main”:
if WEBHOOK_URL:
try:
requests.get(f”https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={WEBHOOK_URL}”)
except:
pass
flask_app.run(host=“0.0.0.0”, port=PORT)
else:
app.run()
