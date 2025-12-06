import os
import asyncio
import threading
import json
import requests
import logging
import time
import subprocess
from flask import Flask, request, abort, render_template_string
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.enums import ChatAction, ChatMemberStatus

# --- Configuration ---
FFMPEG_BINARY = os.environ.get("FFMPEG_BINARY", "/usr/bin/ffmpeg")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "YourBotUsername") # Ku dar username-ka bot-kaaga!
BOT_TOKEN = os.environ.get("BOT_TOKEN", "") # Loo bedelay BOT_TOKEN meeshii BOT2_TOKEN
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")

WEBHOOK_URL_BASE = os.environ.get("WEBHOOK_URL_BASE", "")
PORT = int(os.environ.get("PORT", "8080"))
WEBHOOK_PATH = os.environ.get("WEBHOOK_PATH", "/webhook/")
WEBHOOK_URL = WEBHOOK_URL_BASE.rstrip('/') + WEBHOOK_PATH if WEBHOOK_URL_BASE else ""

REQUEST_TIMEOUT_GEMINI = int(os.environ.get("REQUEST_TIMEOUT_GEMINI", "300"))
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "20"))
MAX_UPLOAD_SIZE = MAX_UPLOAD_MB * 1024 * 1024
MAX_MESSAGE_CHUNK = 4095
MAX_AUDIO_DURATION_SEC = 9 * 60 * 60

DEFAULT_GEMINI_KEYS = os.environ.get("DEFAULT_GEMINI_KEYS", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_API_KEYS = os.environ.get("GEMINI_API_KEYS", DEFAULT_GEMINI_KEYS)
REQUIRED_CHANNEL = os.environ.get("REQUIRED_CHANNEL", "")

DOWNLOADS_DIR = os.environ.get("DOWNLOADS_DIR", "./downloads")

os.makedirs(DOWNLOADS_DIR, exist_ok=True)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Key Rotator Class ---
class KeyRotator:
    def __init__(self, keys):
        self.keys = [k.strip() for k in keys.split(",") if k.strip()]
        self.pos = 0
        self.lock = threading.Lock()
    def get_order(self):
        with self.lock:
            n = len(self.keys)
            if n == 0: return []
            return [self.keys[(self.pos + i) % n] for i in range(n)]
    def mark_success(self, key):
        with self.lock:
            try: i = self.keys.index(key); self.pos = i
            except ValueError: pass
    def mark_failure(self, key):
        with self.lock:
            n = len(self.keys)
            if n == 0: return
            try: i = self.keys.index(key); self.pos = (i + 1) % n
            except ValueError: self.pos = (self.pos + 1) % n

gemini_rotator = KeyRotator(GEMINI_API_KEYS)

# --- Languages ---
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

# --- Global State ---
user_mode = {}
user_transcriptions = {}
action_usage = {}

# --- Pyrogram Client ---
app = Client("media_transcriber", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- Utility Functions ---
def get_user_mode(uid):
    return user_mode.get(uid, "📄 Text File")

def convert_to_wav(input_path: str) -> str:
    if not FFMPEG_BINARY: raise RuntimeError("FFmpeg binary not found.")
    output_path = os.path.join(DOWNLOADS_DIR, f"{os.path.basename(input_path).split('.')[0]}_converted.wav")
    command = [FFMPEG_BINARY, "-i", input_path, "-acodec", "pcm_s16le", "-ac", "1", "-ar", "16000", output_path, "-y"]
    subprocess.run(command, check=True, capture_output=True, timeout=REQUEST_TIMEOUT_GEMINI)
    return output_path

def gemini_api_call(endpoint, payload, key, headers=None):
    url = f"https://generativelanguage.googleapis.com/v1beta/{endpoint}?key={key}"
    resp = requests.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT_GEMINI)
    resp.raise_for_status()
    return resp.json()

def execute_gemini_action(action_callback):
    last_exc = None
    for key in gemini_rotator.get_order():
        if not key: raise RuntimeError("No Gemini keys available")
        try:
            result = action_callback(key)
            gemini_rotator.mark_success(key)
            return result
        except Exception as e:
            last_exc = e
            logging.warning(f"Gemini error with key {str(key)[:4]}: {e}")
            gemini_rotator.mark_failure(key)
    raise RuntimeError(f"Gemini failed after rotations. Last error: {last_exc}")

def upload_and_transcribe_gemini(file_path: str) -> str:
    original_path, converted_path = file_path, None
    file_ext = os.path.splitext(file_path)[1].lower()
    if file_ext not in [".wav", ".mp3", ".aiff", ".aac", ".ogg", ".flac"]:
        converted_path = convert_to_wav(file_path)
        file_path = converted_path
    file_size = os.path.getsize(file_path)
    mime_type = "audio/wav"
    def perform_upload_and_transcribe(key):
        uploaded_name = None
        try:
            upload_url = f"https://generativelanguage.googleapis.com/upload/v1beta/files?key={key}"
            headers = {
                "X-Goog-Upload-Protocol": "raw", "X-Goog-Upload-Command": "start, upload, finalize",
                "X-Goog-Upload-Header-Content-Length": str(file_size), "Content-Type": mime_type
            }
            with open(file_path, 'rb') as f:
                up_resp = requests.post(upload_url, headers=headers, data=f.read(), timeout=REQUEST_TIMEOUT_GEMINI).json()
            uploaded_name = up_resp.get("name", up_resp.get("file", {}).get("name"))
            uploaded_uri = up_resp.get("uri", up_resp.get("file", {}).get("uri"))
            if not uploaded_name: raise RuntimeError("Upload failed.")
            prompt = "Transcribe the audio in this file. Automatically detect the language and provide a clean transcription. Do not add intro phrases."
            payload = {"contents": [{"parts": [{"fileData": {"mimeType": mime_type, "fileUri": uploaded_uri}}, {"text": prompt}]}]}
            data = gemini_api_call(f"models/{GEMINI_MODEL}:generateContent", payload, key, headers={"Content-Type": "application/json"})
            return data["candidates"][0]["content"]["parts"][0]["text"]
        finally:
            if uploaded_name:
                try: requests.delete(f"https://generativelanguage.googleapis.com/v1beta/{uploaded_name}?key={key}", timeout=5)
                except: pass
    try: return execute_gemini_action(perform_upload_and_transcribe)
    finally:
        if converted_path and os.path.exists(converted_path): os.remove(converted_path)

def ask_gemini(text, instruction):
    def perform_text_query(key):
        payload = {"contents": [{"parts": [{"text": f"{instruction}\n\n{text}"}]}]}
        data = gemini_api_call(f"models/{GEMINI_MODEL}:generateContent", payload, key, headers={"Content-Type": "application/json"})
        return data["candidates"][0]["content"]["parts"][0]["text"]
    return execute_gemini_action(perform_text_query)

def build_action_keyboard(text_len):
    btns = [[InlineKeyboardButton("⭐️ Get translating", callback_data="translate_menu|")]]
    if text_len > 1000: btns.append([InlineKeyboardButton("Summarize", callback_data="summarize|")])
    return InlineKeyboardMarkup(btns)

def build_lang_keyboard(origin):
    btns, row = [], []
    for i, (lbl, code) in enumerate(LANGS, 1):
        row.append(InlineKeyboardButton(lbl, callback_data=f"lang|{code}|{lbl}|{origin}"))
        if i % 3 == 0: btns.append(row); row=[]
    if row: btns.append(row)
    return InlineKeyboardMarkup(btns)

async def ensure_joined(client, obj):
    if not REQUIRED_CHANNEL: return True
    uid = obj.from_user.id
    reply_target = obj if isinstance(obj, Message) else obj.message
    try:
        member = await client.get_chat_member(REQUIRED_CHANNEL, uid)
        if member.status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER, ChatMemberStatus.RESTRICTED]: return True
    except: pass
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔗 Join", url=f"https://t.me/{REQUIRED_CHANNEL.replace('@','')}")]])
    await reply_target.reply_text("First, join my channel 😜", reply_markup=kb)
    return False

async def send_long_text(client: Client, chat_id: int, text: str, reply_id: int, uid: int, action: str = "Transcript"):
    mode = get_user_mode(uid)
    if len(text) > MAX_MESSAGE_CHUNK:
        if mode == "Split messages":
            sent = None
            for i in range(0, len(text), MAX_MESSAGE_CHUNK):
                sent = await client.send_message(chat_id, text[i:i+MAX_MESSAGE_CHUNK], reply_to_message_id=reply_id)
            return sent
        else:
            fname = os.path.join(DOWNLOADS_DIR, f"{action}.txt")
            with open(fname, "w", encoding="utf-8") as f: f.write(text)
            sent = await client.send_document(chat_id, fname, caption="Open this file and copy the text inside 👍", reply_to_message_id=reply_id)
            os.remove(fname)
            return sent
    return await client.send_message(chat_id, text, reply_to_message_id=reply_id)

# --- Pyrogram Handlers ---
@app.on_message(filters.command(["start", "help"]) & filters.private)
async def start_help(client, message: Message):
    if not await ensure_joined(client, message): return
    text = f"👋 **Salaam!**\n• Send me\n• **voice message**\n• **audio file**\n• **video**\n• to transcribe for free"
    await message.reply_text(text, parse_mode='markdown')

@app.on_message(filters.command("mode") & filters.private)
async def choose_mode(client, message: Message):
    if not await ensure_joined(client, message): return
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Split messages", callback_data="mode|Split messages")],
        [InlineKeyboardButton("📄 Text File", callback_data="mode|Text File")]
    ])
    await message.reply_text("Choose **output mode**:", reply_markup=keyboard, parse_mode='markdown')

@app.on_callback_query(filters.regex(r"^mode\|"))
async def mode_cb(client, callback: CallbackQuery):
    if not await ensure_joined(client, callback): return
    mode = callback.data.split("|")[1]
    user_mode[callback.from_user.id] = mode
    await callback.answer(f"Mode set to: {mode} ☑️", show_alert=True)
    try: await callback.message.edit_text(f"Output mode set to: **{mode}**", reply_markup=None, parse_mode='markdown')
    except: pass

@app.on_callback_query(filters.regex(r"^lang\|"))
async def lang_cb(client, callback: CallbackQuery):
    if not await ensure_joined(client, callback): return
    try: await callback.message.edit_reply_markup(reply_markup=None)
    except: pass
    _, code, lbl, origin = callback.data.split("|")
    data = user_transcriptions.get(callback.message.chat.id, {}).get(callback.message.id)
    if not data: return await callback.answer("Transcription data expired.", show_alert=True)
    await callback.answer(f"Translating to {lbl}...")
    text = data["text"]
    instruction = f"Translate this text in to language {lbl}. No extra text ONLY return the translated text."
    translated = await client.loop.run_in_executor(None, ask_gemini, text, instruction)
    await send_long_text(client, callback.message.chat.id, translated, data["origin"], callback.from_user.id, f"Translation_{code}")

@app.on_callback_query(filters.regex(r"^(translate_menu|summarize)\|"))
async def action_cb(client, callback: CallbackQuery):
    if not await ensure_joined(client, callback): return
    chat_id = callback.message.chat.id
    msg_id = callback.message.id
    data = user_transcriptions.get(chat_id, {}).get(msg_id)
    if not data: return await callback.answer("Transcription data expired.", show_alert=True)
    action = callback.data.split("|")[0]
    if action == "translate_menu":
        await callback.message.edit_reply_markup(build_lang_keyboard("trans"))
    elif action == "summarize":
        key = f"{chat_id}|{msg_id}|{action}"
        if action_usage.get(key, 0) >= 1: return await callback.answer("Already summarized!", show_alert=True)
        await callback.answer("Summarizing...")
        try:
            instruction = "Summarize this in original language."
            processed = await client.loop.run_in_executor(None, ask_gemini, data["text"], instruction)
            action_usage[key] = 1
            await send_long_text(client, chat_id, processed, data["origin"], callback.from_user.id, "Summary")
        except Exception as e:
            await client.send_message(chat_id, f"❌ Summarization error: {e}", reply_to_message_id=data["origin"])

@app.on_message((filters.audio | filters.voice | filters.video | filters.document) & filters.private)
async def handle_media(client, message: Message):
    if not await ensure_joined(client, message): return
    media = getattr(message, 'audio', None) or getattr(message, 'voice', None) or getattr(message,'video',None) or getattr(message,'document',None)
    if not media: return
    size=getattr(media,'file_size',0)
    duration=getattr(media,'duration',0)
    
    if size>MAX_UPLOAD_SIZE: return await message.reply_text(f"Just Send me a file less than {MAX_UPLOAD_MB}MB 😎")
    if duration>MAX_AUDIO_DURATION_SEC:
        hours=MAX_AUDIO_DURATION_SEC//3600
        return await message.reply_text(f"Bot-ka ma aqbalayo cod ka dheer {hours} saac. Fadlan soo dir mid ka gaaban.")
        
    await client.send_chat_action(message.chat.id, ChatAction.TYPING)
    file_path=None
    try:
        file_path=await message.download(file_name=os.path.join(DOWNLOADS_DIR, f"temp_{message.id}_{media.file_unique_id}"))
        text=await client.loop.run_in_executor(None, upload_and_transcribe_gemini, file_path)
        
        sent_msg=await send_long_text(client, message.chat.id, text, message.id, message.from_user.id)
        
        if sent_msg:
            user_transcriptions.setdefault(message.chat.id,{})[sent_msg.id]={"text":text,"origin":message.id}
            await sent_msg.edit_reply_markup(build_action_keyboard(len(text)))
    except Exception as e:
        await message.reply_text(f"❌ Transcription error: {e}")
    finally:
        if file_path and os.path.exists(file_path): os.remove(file_path)

# --- Flask Web Application ---
flask_app = Flask(__name__)

# HTML Template for the landing page
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="so">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MediaToTextBot</title>
    <style>
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            margin: 0;
            padding: 0;
            background-color: #f4f4f9;
            color: #333;
            text-align: center;
        }
        .container {
            max-width: 800px;
            margin: 50px auto;
            padding: 20px;
            background-color: #fff;
            border-radius: 12px;
            box-shadow: 0 6px 15px rgba(0, 0, 0, 0.1);
        }
        header {
            background-color: #007bff;
            color: white;
            padding: 20px;
            border-radius: 10px 10px 0 0;
            margin-bottom: 20px;
        }
        h1 {
            margin: 0;
            font-size: 2.5em;
        }
        .info-section {
            padding: 20px;
            text-align: left;
            border-top: 1px solid #eee;
        }
        h2 {
            color: #007bff;
            border-bottom: 2px solid #007bff;
            padding-bottom: 5px;
            margin-bottom: 15px;
            font-size: 1.8em;
        }
        p {
            line-height: 1.6;
            margin-bottom: 15px;
        }
        .features-list {
            list-style: none;
            padding: 0;
        }
        .features-list li {
            background: #e9f7ff;
            margin-bottom: 10px;
            padding: 10px;
            border-left: 5px solid #007bff;
            border-radius: 5px;
            font-weight: 500;
        }
        .cta-button {
            display: inline-block;
            background-color: #28a745;
            color: white;
            padding: 15px 30px;
            text-decoration: none;
            border-radius: 8px;
            font-size: 1.2em;
            margin-top: 25px;
            transition: background-color 0.3s ease;
        }
        .cta-button:hover {
            background-color: #218838;
        }
        footer {
            margin-top: 30px;
            padding: 15px;
            color: #777;
            font-size: 0.9em;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>🎤 MediaToTextBot 📝</h1>
            <p>Bot-kaaga gaarka ah ee codka u beddela qoraal!</p>
        </header>
        <div class="info-section">
            <h2>Hordhac & Shaqada Bot-ka</h2>
            <p>Bot-kan wuxuu kuu fududeynayaa in aad si degdeg ah oo hufan u hesho qoraalka (Transcription) fariimaha codka ah, faylasha audio-ga, iyo xitaa muuqaallada video-ga. Wuxuu isticmaalayaa awoodda sirdoonka macmalka (AI) ee **Gemini** si uu u bixiyo natiijooyin sax ah.</p>
            
            <h2>Awoodaha Ugu Muhiimsan</h2>
            <ul class="features-list">
                <li>✅ **Transcription Dhameystiran:** Wuxuu u beddelayaa codka qoraal.</li>
                <li>🌍 **Turjumaad:** Wuxuu turjumayaa qoraalka luuqad kasta oo aad doorato.</li>
                <li>📝 **Soo Koobid (Summarization):** Wuxuu soo koobayaa qoraallada dhaadheer.</li>
                <li>⚙️ **Qaabka Output-ka:** Waxaad dooran kartaa inuu kuugu soo diro fariimo kala go'an ama Fayl Qoraal ah (Text File).</li>
                <li>🚀 **Xawaare Sare:** Wuxuu isticmaalayaa Gemini API si uu xawaare fiican u shaqeeyo.</li>
            </ul>
            
            <h2>Sida Loo Isticmaalo</h2>
            <p>Si aad u bilowdo, kaliya u dir bot-ka **fariin cod ah**, **fayl audio** ah, ama **video** gaaban (illaa {{ max_upload_mb }}MB). Bot-ku wuxuu isla markiiba bilaabayaa inuu u beddelo qoraal.</p>
            
            <a href="https://t.me/{{ bot_username }}" class="cta-button">Fariin u dir Bot-kaaga 🚀</a>
        </div>
        <footer>
            <p>&copy; 2025 MediaToTextBot - Powered by Pyrogram & Gemini AI.</p>
        </footer>
    </div>
</body>
</html>
"""

@flask_app.route("/")
def landing_page():
    return render_template_string(HTML_TEMPLATE, 
                                  max_upload_mb=MAX_UPLOAD_MB,
                                  bot_username=BOT_USERNAME)

@flask_app.route("/keep_alive")
def keep_alive_flask():
    return "Bot is alive (Flask) ✅", 200

# --- Combined Runner ---
def run_flask():
    logging.info(f"Starting Flask server on http://0.0.0.0:{PORT}")
    flask_app.run(host="0.0.0.0", port=PORT)

def run_pyrogram():
    logging.info("Starting Pyrogram bot...")
    app.run()

if __name__ == "__main__":
    if BOT_USERNAME == "YourBotUsername":
        logging.error("Fadlan beddel BOT_USERNAME qiimaha ku jira koodhka!")
    
    # Run Flask and Pyrogram concurrently using threads
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Run Pyrogram in the main thread (blocking call)
    run_pyrogram()
