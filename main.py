import os
import time
import json
import logging
import threading
import subprocess
from flask import Flask, request, abort
import requests
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, Update

FFMPEG_BINARY = os.environ.get("FFMPEG_BINARY", "/usr/bin/ffmpeg")
BOT_TOKEN = os.environ.get("BOT2_TOKEN", "")
WEBHOOK_URL_BASE = os.environ.get("WEBHOOK_URL_BASE", "")
PORT = int(os.environ.get("PORT", "8080"))
WEBHOOK_PATH = os.environ.get("WEBHOOK_PATH", "/webhook/")
WEBHOOK_URL = WEBHOOK_URL_BASE.rstrip('/') + WEBHOOK_PATH if WEBHOOK_URL_BASE else ""
REQUEST_TIMEOUT_GEMINI = int(os.environ.get("REQUEST_TIMEOUT_GEMINI", "300"))
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "250"))
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

if not BOT_TOKEN:
    logging.error("BOT_TOKEN is not set. Exiting.")
    raise SystemExit(1)

class KeyRotator:
    def __init__(self, keys_csv):
        self.keys = [k.strip() for k in (keys_csv or "").split(",") if k.strip()]
        self.pos = 0
        self.lock = threading.Lock()
    def get_key(self):
        with self.lock:
            return None if not self.keys else self.keys[self.pos]
    def rotate(self):
        with self.lock:
            if self.keys:
                self.pos = (self.pos + 1) % len(self.keys)
    def mark_success(self, key): self.rotate()
    def mark_failure(self, key): self.rotate()

gemini_rotator = KeyRotator(GEMINI_API_KEYS)

LANGS = [
("🇬🇧 English","en"), ("🇸🇦 العربية","ar"), ("🇪🇸 Español","es"), ("🇫🇷 Français","fr"),
("🇷🇺 Русский","ru"), ("🇩🇪 Deutsch","de"), ("🇮🇳 हिन्दी","hi"), ("🇮🇷 فارسی","fa"),
("🇮🇩 Indonesia","id"), ("🇺🇦 Українська","uk"), ("🇦🇿 Azərbaycan","az"), ("🇮🇹 Italiano","it"),
("🇹🇷 Türkçe","tr"), ("🇧🇬 Български","bg"), ("🇷🇸 Srpski","sr"), ("🇵🇰 اردو","ur"),
("🇹🇭 ไทย","th"), ("🇻🇳 Tiếng Việt","vi"), ("🇯🇵 日本語","ja"), ("🇰🇷 한국어","ko"),
("🇨🇳 中文","zh"), ("🇳🇱 Nederlands","nl"), ("🇸🇪 Svenska","sv"), ("🇳🇴 Norsk","no"),
("🇮🇱 עברית","he"), ("🇩🇰 Dansk","da"), ("🇪🇹 አማርኛ","am"), ("🇫🇮 Suomi","fi"),
("🇧🇩 বাংলা","bn"), ("🇰🇪 Kiswahili","sw"), ("🇪🇹 Oromo","om"), ("🇳🇵 नेपाली","ne"),
("🇵🇱 Polski","pl"), ("🇬🇷 Ελληνικά","el"), ("🇨🇿 Čeština","cs"), ("🇮🇸 Íslenska","is"),
("🇱🇹 Lietuvių","lt"), ("🇱🇻 Latviešu","lv"), ("🇭🇷 Hrvatski","hr"), ("🇷🇸 Bosanski","bs"),
("🇭🇺 Magyar","hu"), ("🇷🇴 Română","ro"), ("🇸🇴 Somali","so"), ("🇲🇾 Melayu","ms"),
("🇺🇿 O'zbekcha","uz"), ("🇵🇭 Tagalog","tl"), ("🇵🇹 Português","pt")
]

user_mode = {}
user_transcriptions = {}
action_usage = {}

def get_user_mode(uid, default="📄 Text File"):
    return user_mode.get(uid, default)

def run_cmd(cmd, timeout=None):
    return subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)

def convert_to_wav(input_path: str) -> str:
    if not FFMPEG_BINARY:
        raise RuntimeError("FFmpeg not configured.")
    base = os.path.splitext(os.path.basename(input_path))[0]
    output = os.path.join(DOWNLOADS_DIR, f"{base}_converted.wav")
    cmd = [FFMPEG_BINARY, "-i", input_path, "-acodec", "pcm_s16le", "-ac", "1", "-ar", "16000", output, "-y"]
    run_cmd(cmd, timeout=REQUEST_TIMEOUT_GEMINI)
    return output

def gemini_api_call(endpoint, payload, key, timeout, headers=None):
    url = f"https://generativelanguage.googleapis.com/v1beta/{endpoint}?key={key}"
    res = requests.post(url, headers=headers or {"Content-Type": "application/json"}, json=payload, timeout=timeout)
    res.raise_for_status()
    return res.json()

def upload_and_transcribe_gemini(file_path: str) -> str:
    converted = None
    try:
        ext = os.path.splitext(file_path)[1].lower()
        allowed = [".wav", ".mp3", ".aiff", ".aac", ".ogg", ".flac"]
        if ext not in allowed:
            converted = convert_to_wav(file_path)
            file_path = converted
        size = os.path.getsize(file_path)
        mime = "audio/wav"
        last = None
        attempts = max(1, len(gemini_rotator.keys))
        for _ in range(attempts):
            key = gemini_rotator.get_key()
            if not key:
                raise RuntimeError("No Gemini keys available.")
            uploaded_name = None
            try:
                upload_url = f"https://generativelanguage.googleapis.com/upload/v1beta/files?key={key}"
                headers = {
                    "X-Goog-Upload-Protocol":"raw",
                    "X-Goog-Upload-Command":"start, upload, finalize",
                    "X-Goog-Upload-Header-Content-Length":str(size),
                    "Content-Type": mime
                }
                with open(file_path, "rb") as f:
                    up = requests.post(upload_url, headers=headers, data=f.read(), timeout=REQUEST_TIMEOUT_GEMINI)
                up.raise_for_status()
                ud = up.json()
                uploaded_name = ud.get("name")
                uploaded_uri = ud.get("uri")
                if not uploaded_uri:
                    raise RuntimeError("Upload failed or malformed response.")
                prompt = "Transcribe the audio in this file. Automatically detect the language and provide a clean, accurate transcription in the original language of the audio. Do not add any introductory phrases or explanations. Return only the transcription."
                payload = {"contents":[{"parts":[{"fileData":{"mimeType":mime,"fileUri":uploaded_uri}},{"text":prompt}]}]}
                resp = gemini_api_call(f"models/{GEMINI_MODEL}:generateContent", payload, key, REQUEST_TIMEOUT_GEMINI)
                text = resp["candidates"][0]["content"]["parts"][0]["text"]
                gemini_rotator.mark_success(key)
                return text
            except Exception as e:
                last = e
                logging.warning(f"Transcription attempt failed: {e}")
                gemini_rotator.mark_failure(key)
            finally:
                if uploaded_name:
                    try:
                        requests.delete(f"https://generativelanguage.googleapis.com/v1beta/{uploaded_name}?key={key}", timeout=10)
                    except Exception:
                        pass
        raise RuntimeError(f"Transcription failed after retries: {last}")
    finally:
        if converted and os.path.exists(converted):
            os.remove(converted)

def ask_gemini(text, instruction, timeout=REQUEST_TIMEOUT_GEMINI):
    attempts = max(1, len(gemini_rotator.keys))
    for _ in range(attempts):
        key = gemini_rotator.get_key()
        if not key:
            raise RuntimeError("No GEMINI keys available.")
        try:
            payload = {"contents":[{"parts":[{"text": f"{instruction}\n\n[TEXT_TO_PROCESS]\n{text}"}]}]}
            resp = gemini_api_call(f"models/{GEMINI_MODEL}:generateContent", payload, key, timeout)
            gemini_rotator.mark_success(key)
            return resp["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as e:
            logging.warning(f"Gemini text error: {e}")
            gemini_rotator.mark_failure(key)
    raise RuntimeError("Gemini text processing failed after all key rotations.")

def build_action_keyboard(text_length):
    kb = [[InlineKeyboardButton("⭐️ Get translating", callback_data="translate_menu|")]]
    if text_length > 1000:
        kb.append([InlineKeyboardButton("Summarize", callback_data="summarize|")])
    return InlineKeyboardMarkup(kb)

def build_language_keyboard(origin):
    rows = []
    row = []
    for i, (label, code) in enumerate(LANGS, 1):
        row.append(InlineKeyboardButton(label, callback_data=f"lang|{code}|{label}|{origin}"))
        if i % 3 == 0:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
flask_app = Flask(__name__)

WELCOME_MESSAGE = """👋 **Salaam!**
• Send me
• **voice message**
• **audio file**
• **video**
• to transcribe for free
"""
HELP_MESSAGE = f"""/start - Show welcome message
/help - This help message
/mode - Choose output format
Send a voice/audio/video (up to {MAX_UPLOAD_MB}MB) to transcribe
"""

def is_user_in_channel(uid):
    if not REQUIRED_CHANNEL:
        return True
    try:
        m = bot.get_chat_member(REQUIRED_CHANNEL, uid)
        return m.status in ['member', 'administrator', 'creator', 'restricted']
    except Exception as e:
        logging.warning(f"Channel check failed: {e}")
        return False

def ensure_joined(obj):
    uid = obj.from_user.id
    if is_user_in_channel(uid):
        return True
    clean = REQUIRED_CHANNEL.replace("@", "") if REQUIRED_CHANNEL else ""
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔗 Join", url=f"https://t.me/{clean}")]])
    try:
        bot.reply_to(obj, "First, join my channel 😜", reply_markup=kb)
    except Exception:
        pass
    return False

@bot.message_handler(commands=['start','help'])
def send_welcome(message):
    if not ensure_joined(message):
        return
    try:
        if message.text == '/start':
            bot.reply_to(message, WELCOME_MESSAGE, parse_mode="Markdown")
        else:
            bot.reply_to(message, HELP_MESSAGE, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"send_welcome error: {e}")

@bot.message_handler(commands=['mode'])
def choose_mode(message):
    if not ensure_joined(message):
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Split messages", callback_data="mode|Split messages")],
        [InlineKeyboardButton("📄 Text File", callback_data="mode|Text File")]
    ])
    try:
        bot.reply_to(message, "Choose **output mode**:", reply_markup=kb, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"choose_mode error: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('mode|'))
def mode_callback_query(call):
    if not ensure_joined(call.message):
        try:
            bot.answer_callback_query(call.id, "🚫 First join my channel", show_alert=True)
        except Exception:
            pass
        return
    mode_name = call.data.split("|",1)[1]
    user_mode[call.from_user.id] = mode_name
    try:
        bot.answer_callback_query(call.id, f"Mode set to: {mode_name}")
        bot.edit_message_text(f"Output mode set to: **{mode_name}**", call.message.chat.id, call.message.message_id, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"mode_callback_query error: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('lang|'))
def language_callback_query(call):
    if not ensure_joined(call.message):
        try:
            bot.answer_callback_query(call.id, "🚫 First join my channel", show_alert=True)
        except Exception:
            pass
        return
    _, code, label, origin = call.data.split("|",3)
    chat_id = call.message.chat.id
    mid = call.message.message_id
    try:
        bot.answer_callback_query(call.id, f"Translating to {label}...")
    except Exception:
        pass
    tdata = user_transcriptions.get(chat_id, {}).get(mid)
    if not tdata:
        try:
            bot.send_message(chat_id, "Original transcription data not found.")
            bot.delete_message(chat_id, mid)
        except Exception:
            pass
        return
    try:
        bot.delete_message(chat_id, mid)
        bot.send_chat_action(chat_id, 'typing')
        instr = f"Translate this text into {label}. Do not add any introductory phrases, or the original text. ONLY return the translated text."
        translated = ask_gemini(tdata["text"], instr)
        send_long_text(bot, chat_id, translated, tdata["origin"], call.from_user.id)
    except Exception as e:
        logging.error(f"language_callback_query error: {e}")
        try:
            bot.send_message(chat_id, f"❌ Translation error: {e}", reply_to_message_id=tdata.get("origin"))
        except Exception:
            pass

@bot.callback_query_handler(func=lambda call: call.data.startswith(('translate_menu|','summarize|')))
def action_callback_query(call):
    if not ensure_joined(call.message):
        try:
            bot.answer_callback_query(call.id, "🚫 First join my channel", show_alert=True)
        except Exception:
            pass
        return
    action = call.data.split("|",1)[0]
    chat_id = call.message.chat.id
    mid = call.message.message_id
    tdata = user_transcriptions.get(chat_id, {}).get(mid)
    if not tdata:
        try:
            bot.answer_callback_query(call.id, "Transcription not found. Please resend the message.", show_alert=True)
        except Exception:
            pass
        return
    if action == "translate_menu":
        try:
            bot.edit_message_reply_markup(chat_id, mid, reply_markup=build_language_keyboard("trans"))
        except Exception as e:
            logging.error(f"translate_menu edit error: {e}")
        return
    key = f"{chat_id}|{mid}|{action}"
    if action_usage.get(key, 0) >= 1:
        try:
            bot.answer_callback_query(call.id, f"{action.capitalize()} unavailable (maybe expired or used)", show_alert=True)
        except Exception:
            pass
        return
    try:
        bot.answer_callback_query(call.id, "Processing...", show_alert=False)
        bot.send_chat_action(chat_id, 'typing')
        instr = "Summarize the following text. Do not add any introductory phrases, notes, or extra phrases. Use the original language of the text." if action == "summarize" else "Process the following text and return the result."
        processed = ask_gemini(tdata["text"], instr)
        action_usage[key] = action_usage.get(key, 0) + 1
        send_long_text(bot, chat_id, processed, tdata["origin"], call.from_user.id, action)
    except Exception as e:
        logging.error(f"action_callback_query error: {e}")
        try:
            bot.send_message(chat_id, f"❌ Error: {e}", reply_to_message_id=tdata.get("origin"))
        except Exception:
            pass

def get_file_info(message):
    media = None
    if message.voice:
        media = message.voice
    elif message.audio:
        media = message.audio
    elif message.video:
        media = message.video
    elif message.document and (message.document.mime_type and ('audio' in message.document.mime_type or 'video' in message.document.mime_type)):
        media = message.document
    else:
        return None, None
    size = getattr(media, 'file_size', None)
    duration = getattr(media, 'duration', 0)
    if size is None or size > MAX_UPLOAD_SIZE:
        try:
            bot.reply_to(message, f"Just Send me a file less than {MAX_UPLOAD_MB}MB 😎")
        except Exception:
            pass
        return None, None
    if duration > MAX_AUDIO_DURATION_SEC:
        hours = MAX_AUDIO_DURATION_SEC // 3600
        try:
            bot.reply_to(message, f"Bot-ka ma aqbalayo cod ka dheer {hours} saac. Fadlan soo dir mid ka gaaban.")
        except Exception:
            pass
        return None, None
    try:
        return bot.get_file(media.file_id), media
    except Exception as e:
        logging.error(f"get_file_info error: {e}")
        try:
            bot.reply_to(message, "❌ Error retrieving file information from Telegram.")
        except Exception:
            pass
        return None, None

@bot.message_handler(content_types=['voice','audio','video','document'])
def handle_media(message):
    if not ensure_joined(message):
        return
    file_info, media = get_file_info(message)
    if not file_info:
        return
    file_path = None
    try:
        bot.send_chat_action(message.chat.id, 'typing')
        file_path = os.path.join(DOWNLOADS_DIR, file_info.file_path.split('/')[-1])
        data = bot.download_file(file_info.file_path)
        with open(file_path, "wb") as f:
            f.write(data)
        text = upload_and_transcribe_gemini(file_path)
        if not text or text.strip().lower().startswith("error:") or len(text.strip()) < 5:
            warning = text or "⚠️ Warning Make sure the voice is clear."
            bot.reply_to(message, warning)
            return
        sent = send_long_text(bot, message.chat.id, text, message.id, message.from_user.id)
        if sent:
            keyboard = build_action_keyboard(len(text))
            user_transcriptions.setdefault(sent.chat.id, {})[sent.message_id] = {"text": text, "origin": message.id}
            if len(text) > 1000:
                action_usage[f"{sent.chat.id}|{sent.message_id}|summarize"] = 0
            bot.edit_message_reply_markup(sent.chat.id, sent.message_id, reply_markup=keyboard)
    except Exception as e:
        logging.error(f"handle_media error: {e}")
        try:
            bot.reply_to(message, f"❌ Error: {e}")
        except Exception:
            pass
    finally:
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass

def send_long_text(bot, chat_id, text, reply_id, uid, action="Transcript"):
    mode = get_user_mode(uid, "📄 Text File")
    sent = None
    try:
        if len(text) > MAX_MESSAGE_CHUNK:
            if mode == "Split messages":
                for i in range(0, len(text), MAX_MESSAGE_CHUNK):
                    bot.send_chat_action(chat_id, 'typing')
                    sent = bot.send_message(chat_id, text[i:i+MAX_MESSAGE_CHUNK], reply_to_message_id=reply_id)
            else:
                fname = os.path.join(DOWNLOADS_DIR, f"{action}.txt")
                with open(fname, "w", encoding="utf-8") as f:
                    f.write(text)
                bot.send_chat_action(chat_id, 'upload_document')
                caption = f"{action}: Open this file and copy the text inside 👍"
                sent = bot.send_document(chat_id, open(fname, 'rb'), caption=caption, reply_to_message_id=reply_id)
                try:
                    os.remove(fname)
                except Exception:
                    pass
        else:
            bot.send_chat_action(chat_id, 'typing')
            sent = bot.send_message(chat_id, text, reply_to_message_id=reply_id)
    except Exception as e:
        logging.error(f"send_long_text error: {e}")
        try:
            bot.send_message(chat_id, f"❌ Error sending result for {action}: {e}", reply_to_message_id=reply_id)
        except Exception:
            pass
    return sent

@flask_app.route("/", methods=["GET","POST","HEAD"])
def keep_alive_flask():
    return "Bot is alive (Flask/Telebot) ✅", 200

@flask_app.route(WEBHOOK_PATH, methods=['POST'])
def webhook_handler():
    if request.headers.get('content-type') == 'application/json':
        js = request.get_data().decode('utf-8')
        update = Update.de_json(js)
        try:
            bot.process_new_updates([update])
        except Exception as e:
            logging.error(f"Error processing update: {e}")
        return '', 200
    abort(403)

def run_webhook_bot():
    if not WEBHOOK_URL:
        logging.error("WEBHOOK_URL is not set. Cannot run in webhook mode.")
        return
    try:
        logging.info("Removing old webhook...")
        bot.remove_webhook()
        time.sleep(1)
        logging.info(f"Setting webhook to {WEBHOOK_URL}")
        bot.set_webhook(url=WEBHOOK_URL)
        logging.info(f"Webhook set. Starting Flask on port {PORT}")
        flask_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
    except Exception as e:
        logging.error(f"Failed webhook setup: {e}")
        print(f"FATAL: Failed to start bot. Check BOT_TOKEN, WEBHOOK_URL, and PORT. Error: {e}")

if __name__ == "__main__":
    run_webhook_bot()
