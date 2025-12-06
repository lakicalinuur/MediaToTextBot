import os
import threading
import json
import requests
import logging
import time
import subprocess
import tempfile
from collections import deque
from itsdangerous import URLSafeTimedSerializer, BadSignature
from flask import Flask, request, abort, jsonify, render_template_string
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, Update

FFMPEG_BINARY = os.environ.get("FFMPEG_BINARY", "/usr/bin/ffmpeg")
BOT_TOKEN = os.environ.get("BOT2_TOKEN", "")
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

MAX_WEB_UPLOAD_MB = int(os.environ.get("MAX_WEB_UPLOAD_MB", "250"))
SECRET_KEY = os.environ.get("SECRET_KEY", "secret123")
MAX_PENDING_QUEUE = int(os.environ.get("MAX_PENDING_QUEUE", "20"))
MAX_CONCURRENT_TRANSCRIPTS = int(os.environ.get("MAX_CONCURRENT_TRANSCRIPTS", "2"))

os.makedirs(DOWNLOADS_DIR, exist_ok=True)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class KeyRotator:
    def __init__(self, keys):
        self.keys = [k.strip() for k in keys.split(",") if k.strip()]
        self.pos = 0
        self.lock = threading.Lock()
    def get_key(self):
        with self.lock:
            if not self.keys: return None
            key = self.keys[self.pos]
            self.pos = (self.pos + 1) % len(self.keys)
            return key
    def mark_success(self, key):
        with self.lock:
            try:
                i = self.keys.index(key)
                self.pos = (i + 1) % len(self.keys)
            except ValueError:
                pass
    def mark_failure(self, key):
        self.mark_success(key)

gemini_rotator = KeyRotator(GEMINI_API_KEYS)

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

user_mode = {}
user_transcriptions = {}
action_usage = {}

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
flask_app = Flask(__name__)

def get_user_mode(uid):
    return user_mode.get(uid, "📄 Text File")

def convert_to_wav(input_path: str) -> str:
    if not FFMPEG_BINARY: raise RuntimeError("FFmpeg binary not found.")
    output_path = os.path.join(DOWNLOADS_DIR, f"{os.path.basename(input_path).split('.')[0]}_converted.wav")
    command = [FFMPEG_BINARY, "-i", input_path, "-acodec", "pcm_s16le", "-ac", "1", "-ar", "16000", output_path, "-y"]
    subprocess.run(command, check=True, capture_output=True, timeout=REQUEST_TIMEOUT_GEMINI)
    return output_path

def execute_gemini_action(action_callback):
    last_exc = None
    for _ in range(len(gemini_rotator.keys) + 1):
        key = gemini_rotator.get_key()
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

def gemini_api_call(endpoint, payload, key, headers=None):
    url = f"https://generativelanguage.googleapis.com/v1beta/{endpoint}?key={key}"
    resp = requests.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT_GEMINI)
    resp.raise_for_status()
    return resp.json()

def upload_and_transcribe_gemini(file_path: str) -> str:
    original_path, converted_path = file_path, None
    if os.path.splitext(file_path)[1].lower() not in [".wav", ".mp3", ".aiff", ".aac", ".ogg", ".flac"]:
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
    try:
        return execute_gemini_action(perform_upload_and_transcribe)
    finally:
        if converted_path and os.path.exists(converted_path):
            os.remove(converted_path)

def ask_gemini(text, instruction):
    def perform_text_query(key):
        payload = {"contents": [{"parts": [{"text": f"{instruction}\n\n{text}"}]}]}
        data = gemini_api_call(f"models/{GEMINI_MODEL}:generateContent", payload, key, headers={"Content-Type": "application/json"})
        return data["candidates"][0]["content"]["parts"][0]["text"]
    return execute_gemini_action(perform_text_query)

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
            btns.append(row); row = []
    if row: btns.append(row)
    return InlineKeyboardMarkup(btns)

def ensure_joined(message):
    if not REQUIRED_CHANNEL: return True
    try:
        if bot.get_chat_member(REQUIRED_CHANNEL, message.from_user.id).status in ['member', 'administrator', 'creator']: return True
    except: pass
    clean = REQUIRED_CHANNEL.replace("@", "")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔗 Join", url=f"https://t.me/{clean}")]])
    bot.reply_to(message, "First, join my channel 😜", reply_markup=kb)
    return False

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    if ensure_joined(message):
        welcome_text = (
            "👋 Salaam!\n"
            "• Send me\n"
            "• voice message\n"
            "• audio file\n"
            "• video\n"
            "• to transcribe for free"
        )
        bot.reply_to(message, welcome_text, parse_mode="Markdown")

@bot.message_handler(commands=['mode'])
def choose_mode(message):
    if ensure_joined(message):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💬 Split messages", callback_data="mode|Split messages")],
            [InlineKeyboardButton("📄 Text File", callback_data="mode|Text File")]
        ])
        bot.reply_to(message, "How do I send you long transcripts?:", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith('mode|'))
def mode_cb(call):
    if not ensure_joined(call.message): return
    mode = call.data.split("|")[1]
    user_mode[call.from_user.id] = mode
    try:
        bot.edit_message_text(f"you choosed: {mode}", call.message.chat.id, call.message.message_id, reply_markup=None)
    except: pass
    bot.answer_callback_query(call.id, f"Mode set to: {mode} ☑️")

@bot.callback_query_handler(func=lambda c: c.data.startswith('lang|'))
def lang_cb(call):
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except: pass
    _, code, lbl, origin = call.data.split("|")
    process_text_action(call, origin, f"Translate to {lbl}", f"Translate this text in to language {lbl}. No extra text ONLY return the translated text.")

@bot.callback_query_handler(func=lambda c: c.data.startswith(('translate_menu|', 'summarize|')))
def action_cb(call):
    action, _ = call.data.split("|")
    if action == "translate_menu":
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=build_lang_keyboard("trans"))
    else:
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except: pass
        process_text_action(call, call.message.message_id, "Summarize", "Summarize this in original language.")

def process_text_action(call, origin_msg_id, log_action, prompt_instr):
    if not ensure_joined(call.message): return
    chat_id, msg_id = call.message.chat.id, call.message.message_id
    data = user_transcriptions.get(chat_id, {}).get(msg_id)
    if not data:
        bot.answer_callback_query(call.id, "Data not found (expired). Resend file.", show_alert=True)
        return
    text = data["text"]
    key = f"{chat_id}|{msg_id}|{log_action}"
    if "Summarize" in log_action and action_usage.get(key, 0) >= 1:
        bot.answer_callback_query(call.id, "Already summarized!", show_alert=True)
        return
    bot.answer_callback_query(call.id, "Processing...")
    bot.send_chat_action(chat_id, 'typing')
    try:
        res = ask_gemini(text, prompt_instr)
        action_usage[key] = action_usage.get(key, 0) + 1
        send_long_text(chat_id, res, data["origin"], call.from_user.id, log_action)
    except Exception as e:
        bot.send_message(chat_id, f"Error: {e}")

@bot.message_handler(content_types=['voice', 'audio', 'video', 'document'])
def handle_media(message):
    if not ensure_joined(message): return
    media = message.voice or message.audio or message.video or message.document
    if not media: return
    if getattr(media, 'file_size', 0) > MAX_UPLOAD_SIZE:
        token = signed_upload_token(message.chat.id)
        link = f"{WEBHOOK_URL_BASE.rstrip('/')}/upload/{token}" if WEBHOOK_URL_BASE else f"/upload/{token}"
        bot.reply_to(message, f"⚠️ File wayn: Upload halkan: {link}")
        return
    bot.send_chat_action(message.chat.id, 'typing')
    file_path = os.path.join(DOWNLOADS_DIR, f"temp_{message.id}_{media.file_unique_id}")
    try:
        file_info = bot.get_file(media.file_id)
        downloaded = bot.download_file(file_info.file_path)
        with open(file_path, 'wb') as f: f.write(downloaded)
        text = upload_and_transcribe_gemini(file_path)
        if not text: raise ValueError("Empty response")
        sent = send_long_text(message.chat.id, text, message.id, message.from_user.id)
        if sent:
            user_transcriptions.setdefault(message.chat.id, {})[sent.message_id] = {"text": text, "origin": message.id}
            bot.edit_message_reply_markup(message.chat.id, sent.message_id, reply_markup=build_action_keyboard(len(text)))
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")
    finally:
        if os.path.exists(file_path): os.remove(file_path)

def send_long_text(chat_id, text, reply_id, uid, action="Transcript"):
    mode = get_user_mode(uid)
    if len(text) > MAX_MESSAGE_CHUNK:
        if mode == "Split messages":
            sent = None
            for i in range(0, len(text), MAX_MESSAGE_CHUNK):
                sent = bot.send_message(chat_id, text[i:i+MAX_MESSAGE_CHUNK], reply_to_message_id=reply_id)
            return sent
        else:
            fname = os.path.join(DOWNLOADS_DIR, f"{action}.txt")
            with open(fname, "w", encoding="utf-8") as f: f.write(text)
            sent = bot.send_document(chat_id, open(fname, 'rb'), caption="Open this file and copy the text inside 👍", reply_to_message_id=reply_id)
            os.remove(fname)
            return sent
    return bot.send_message(chat_id, text, reply_to_message_id=reply_id)

@flask_app.route("/", methods=["GET"])
def index():
    return "Bot Running", 200

@flask_app.route(WEBHOOK_PATH, methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        bot.process_new_updates([Update.de_json(request.get_data().decode('utf-8'))])
        return '', 200
    abort(403)

serializer = URLSafeTimedSerializer(SECRET_KEY)
PENDING_QUEUE = deque()
queue_lock = threading.Lock()
transcript_semaphore = threading.Semaphore(MAX_CONCURRENT_TRANSCRIPTS)

HTML_TEMPLATE = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Upload File</title><style>body{font-family:Arial,sans-serif;background-color:#f0f2f5;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;color:#333}.container{background-color:#fff;padding:30px;border-radius:12px;box-shadow:0 4px 15px rgba(0,0,0,0.1);text-align:center;width:90%;max-width:500px;box-sizing:border-box}h2{margin-top:0;color:#555;font-size:1.5rem}p{font-size:0.9rem;color:#666;margin-bottom:20px}.file-upload-wrapper{position:relative;overflow:hidden;display:inline-block;cursor:pointer;width:100%}.file-upload-input{position:absolute;left:0;top:0;opacity:0;cursor:pointer;font-size:100px;width:100%;height:100%}.file-upload-label{background-color:#007bff;color:#fff;padding:12px 20px;border-radius:8px;transition:background-color 0.3s;display:block;font-size:1rem}.file-upload-label:hover{background-color:#0056b3}#file-name{margin-top:15px;font-style:italic;color:#777;font-size:0.9rem;word-wrap:break-word;overflow-wrap:break-word;min-height:20px}#progress-bar-container{width:100%;background-color:#e0e0e0;border-radius:5px;margin-top:20px;display:none}#progress-bar{width:0%;height:15px;background-color:#28a745;border-radius:5px;text-align:center;color:white;line-height:15px;transition:width 0.3s ease}#status-message{margin-top:15px;font-weight:bold}.loading-spinner{display:none;width:40px;height:40px;border:4px solid #f3f3f3;border-top:4px solid #007bff;border-radius:50%;animation:spin 1s linear infinite;margin:20px auto}@keyframes spin{0%{transform:rotate(0deg)}100%{transform:rotate(360deg)}}@media (max-width:600px){.container{padding:20px}}</style></head><body><div class="container"><h2>Upload Your Audio/Video</h2><p>Your file is too big for Telegram, but you can upload it here. Max size: {{ max_mb }}MB.</p><div class="file-upload-wrapper"><input type="file" id="file-input" class="file-upload-input" accept=".mp3,.wav,.m4a,.ogg,.webm,.flac,.mp4,.mkv,.avi,.mov,.hevc,.aac,.aiff,.amr,.wma,.opus,.m4v,.ts,.flv,.3gp"><label for="file-input" class="file-upload-label"><span id="upload-text">Choose File to Upload</span></label></div><div id="file-name"></div><div id="progress-bar-container"><div id="progress-bar">0%</div></div><div id="status-message"></div><div class="loading-spinner" id="spinner"></div></div><script>const fileInput=document.getElementById('file-input');const fileNameDiv=document.getElementById('file-name');const progressBarContainer=document.getElementById('progress-bar-container');const progressBar=document.getElementById('progress-bar');const statusMessageDiv=document.getElementById('status-message');const spinner=document.getElementById('spinner');const uploadTextSpan=document.getElementById('upload-text');const MAX_SIZE_MB={{ max_mb }};fileInput.addEventListener('change',function(){if(this.files.length>0){const file=this.files[0];fileNameDiv.textContent=`Selected: ${file.name}`;statusMessageDiv.textContent='';progressBarContainer.style.display='none';progressBar.style.width='0%';progressBar.textContent='0%';if(file.size>MAX_SIZE_MB*1024*1024){statusMessageDiv.style.color='red';statusMessageDiv.textContent=`Error: File size exceeds the maximum limit of ${MAX_SIZE_MB}MB.`;uploadTextSpan.textContent='Choose File to Upload';fileNameDiv.textContent=''}else{uploadFile(file)}}});function uploadFile(file){const formData=new FormData();formData.append('file',file);const xhr=new XMLHttpRequest();xhr.open('POST',window.location.href);xhr.upload.addEventListener('progress',function(e){if(e.lengthComputable){const percent=Math.round((e.loaded/e.total)*100);progressBarContainer.style.display='block';progressBar.style.width=percent+'%';progressBar.textContent=percent+'%';statusMessageDiv.textContent=`Uploading... ${percent}%`;if(percent===100){statusMessageDiv.textContent='Upload complete. Processing...';spinner.style.display='block'}}});xhr.onload=function(){spinner.style.display='none';if(xhr.status===200){statusMessageDiv.style.color='#28a745';statusMessageDiv.textContent='Success! Your transcript will be sent to your Telegram chat shortly.'}else{statusMessageDiv.style.color='red';statusMessageDiv.textContent=`Error: ${xhr.responseText||'An unknown error occurred.'}`}};xhr.onerror=function(){spinner.style.display='none';statusMessageDiv.style.color='red';statusMessageDiv.textContent='Network error. Please try again.'};xhr.send(formData);}</script></body></html>"""

def signed_upload_token(chat_id):
    return serializer.dumps({"chat_id": chat_id})

def unsign_upload_token(token):
    return serializer.loads(token)

def bytes_to_tempfile(b, filename):
    ext = filename.rsplit(".", 1)[-1] if "." in filename else "tmp"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=f".{ext}")
    tmp.write(b)
    tmp.flush()
    tmp.close()
    return tmp.name

def enqueue_web_upload(chat_id, tmp_path):
    with queue_lock:
        if len(PENDING_QUEUE) >= MAX_PENDING_QUEUE:
            try: os.remove(tmp_path)
            except: pass
            return False
        PENDING_QUEUE.append(("web_upload", chat_id, tmp_path))
        return True

def worker_loop():
    while True:
        transcript_semaphore.acquire()
        item = None
        with queue_lock:
            if PENDING_QUEUE:
                item = PENDING_QUEUE.popleft()
        if not item:
            transcript_semaphore.release()
            time.sleep(0.5)
            continue
        try:
            kind = item[0]
            if kind == "web_upload":
                _, chat_id, local_path = item
                try:
                    text = upload_and_transcribe_gemini(local_path)
                except Exception as e:
                    try:
                        bot.send_message(chat_id, f"❌ Error transcribing uploaded file: {e}")
                    except:
                        pass
                    continue
                if not text:
                    try:
                        bot.send_message(chat_id, "⚠️ Transcription returned empty result")
                    except:
                        pass
                    continue
                try:
                    sent = send_long_text(chat_id, text, None, chat_id)
                    if sent:
                        try:
                            user_transcriptions.setdefault(chat_id, {})[sent.message_id] = {"text": text, "origin": "web_upload"}
                            bot.edit_message_reply_markup(chat_id, sent.message_id, reply_markup=build_action_keyboard(len(text)))
                        except:
                            pass
                except Exception:
                    pass
                finally:
                    try: os.remove(local_path)
                    except: pass
        except Exception:
            logging.exception("Error in worker")
        finally:
            transcript_semaphore.release()
        time.sleep(0.2)

def start_workers():
    for _ in range(MAX_CONCURRENT_TRANSCRIPTS):
        t = threading.Thread(target=worker_loop, daemon=True)
        t.start()

start_workers()

@flask_app.route("/generate_upload/<int:chat_id>", methods=["GET"])
def generate_upload(chat_id):
    token = signed_upload_token(chat_id)
    link = f"{request.url_root.rstrip('/')}/upload/{token}"
    return jsonify({"upload_link": link})

@flask_app.route("/upload/<token>", methods=["GET", "POST"])
def upload_large_file(token):
    try:
        data = unsign_upload_token(token)
    except BadSignature:
        return "<h3>Invalid link</h3>", 400
    chat_id = data.get("chat_id")
    if request.method == 'GET':
        return render_template_string(HTML_TEMPLATE, max_mb=MAX_WEB_UPLOAD_MB)
    file = request.files.get('file')
    if not file:
        return "No file uploaded", 400
    file_bytes = file.read()
    if len(file_bytes) > MAX_WEB_UPLOAD_MB * 1024 * 1024:
        return f"File too large. Max allowed is {MAX_WEB_UPLOAD_MB}MB.", 400
    tmp_path = bytes_to_tempfile(file_bytes, file.filename or "upload_file")
    ok = enqueue_web_upload(chat_id, tmp_path)
    if not ok:
        return jsonify({"status": "rejected", "message": "Server busy. Try again later."}), 503
    return jsonify({"status": "accepted", "message": "Upload accepted. Processing started. Your transcription will be sent to your Telegram chat when ready."})

if __name__ == "__main__":
    if WEBHOOK_URL:
        bot.remove_webhook()
        time.sleep(0.5)
        bot.set_webhook(url=WEBHOOK_URL)
        flask_app.run(host="0.0.0.0", port=PORT)
    else:
        print("Webhook URL not set, exiting.")
