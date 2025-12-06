import os
import time
import threading
import requests
from fastapi import FastAPI
from fastapi.responses import JSONResponse, HTMLResponse
import uvicorn
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.enums import ChatAction, ChatMemberStatus

API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT2_TOKEN", "")
DOWNLOADS_DIR = os.environ.get("DOWNLOADS_DIR", "./downloads")
REQUIRED_CHANNEL = os.environ.get("REQUIRED_CHANNEL", "")
FFMPEG_BINARY = os.environ.get("FFMPEG_BINARY", "/usr/bin/ffmpeg")
REQUEST_TIMEOUT_GEMINI = int(os.environ.get("REQUEST_TIMEOUT_GEMINI", "300"))
GEMINI_API_KEYS = os.environ.get("GEMINI_API_KEYS", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "20"))
MAX_UPLOAD_SIZE = MAX_UPLOAD_MB * 1024 * 1024
MAX_MESSAGE_CHUNK = 4095
MAX_AUDIO_DURATION_SEC = 9 * 60 * 60
WEB_PORT = int(os.environ.get("PORT", "8080"))
WEB_MAX_RECENT = int(os.environ.get("WEB_MAX_RECENT", "30"))

os.makedirs(DOWNLOADS_DIR, exist_ok=True)

tg = Client("media_transcriber", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

LANGS = [("🇬🇧 English","en"),("🇸🇦 العربية","ar"),("🇪🇸 Español","es"),("🇫🇷 Français","fr"),("🇷🇺 Русский","ru"),("🇩🇪 Deutsch","de"),("🇮🇳 हिन्दी","hi"),("🇮🇷 فارسی","fa"),("🇮🇩 Indonesia","id"),("🇺🇦 Українська","uk"),("🇦🇿 Azərbaycan","az"),("🇮🇹 Italiano","it"),("🇹🇷 Türkçe","tr"),("🇧🇬 Български","bg"),("🇷🇸 Srpski","sr"),("🇵🇰 اردو","ur"),("🇹🇭 ไทย","th"),("🇻🇳 Tiếng Việt","vi"),("🇯🇵 日本語","ja"),("🇰🇷 한국어","ko"),("🇨🇳 中文","zh"),("🇳🇱 Nederlands:nl","nl"),("🇸🇪 Svenska","sv"),("🇳🇴 Norsk","no"),("🇮🇱 עברית","he"),("🇩🇰 Dansk","da"),("🇪🇹 አማርኛ","am"),("🇫🇮 Suomi","fi"),("🇧🇩 বাংলা","bn"),("🇰🇪 Kiswahili","sw"),("🇪🇹 Oromo","om"),("🇳🇵 नेपाली","ne"),("🇵🇱 Polski","pl"),("🇬🇷 Ελληνικά","el"),("🇨🇿 Čeština","cs"),("🇮🇸 Íslenska","is"),("🇱🇹 Lietuvių","lt"),("🇱🇻 Latviešu","lv"),("🇭🇷 Hrvatski","hr"),("🇷🇸 Bosanski","bs"),("🇭🇺 Magyar","hu"),("🇷🇴 Română","ro"),("🇸🇴 Somali","so"),("🇲🇾 Melayu","ms"),("🇺🇿 O'zbekcha","uz"),("🇵🇭 Tagalog","tl"),("🇵🇹 Português","pt")]

user_mode = {}
user_transcriptions = {}
action_usage = {}
web_transcriptions = []

def save_web_record(name, text, source="web"):
    record = {"id": int(time.time()*1000), "name": name, "text": text, "source": source, "ts": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())}
    web_transcriptions.insert(0, record)
    if len(web_transcriptions) > WEB_MAX_RECENT:
        web_transcriptions.pop()
    return record

@tg.on_message(filters.command(["start","help"]) & filters.private)
async def send_welcome(client, message: Message):
    if not await ensure_joined(client, message):
        return
    welcome_text = "Salaam\nSend voice/audio/video to transcribe"
    await message.reply_text(welcome_text)

@tg.on_message(filters.private & (filters.audio | filters.voice | filters.video | filters.document))
async def handle_media(client, message: Message):
    if not await ensure_joined(client, message):
        return
    media = message.voice or message.audio or message.video or message.document
    if not media:
        return
    if getattr(media, "file_size", 0) > MAX_UPLOAD_SIZE:
        await message.reply_text(f"Send file less than {MAX_UPLOAD_MB}MB")
        return
    if getattr(media, "duration", 0) > MAX_AUDIO_DURATION_SEC:
        await message.reply_text("Audio too long")
        return
    await client.send_chat_action(message.chat.id, ChatAction.TYPING)
    file_path = None
    try:
        file_path = await message.download(file_name=os.path.join(DOWNLOADS_DIR, f"temp_{message.id}_"))
        text = await asyncio_to_thread_wrapper(upload_and_transcribe_gemini_sync, file_path)
        if not text:
            raise ValueError("Transcription failed")
        sent = await send_long_text(client, message.chat.id, text, message.id, message.from_user.id)
        if sent:
            user_transcriptions.setdefault(message.chat.id, {})[sent.id] = {"text": text, "origin": message.id}
            keyboard = build_action_keyboard(len(text))
            if len(text) > 1000:
                action_usage[f"{sent.chat.id}|{sent.id}|summarize"] = 0
            await sent.edit_reply_markup(keyboard)
    except Exception as e:
        await message.reply_text(f"Error: {e}")
    finally:
        try:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass

async def asyncio_to_thread_wrapper(func, *args, **kwargs):
    import asyncio as _a
    return await _a.to_thread(func, *args, **kwargs)

def convert_to_wav_sync(input_path: str) -> str:
    output_path = os.path.join(DOWNLOADS_DIR, f"{os.path.basename(input_path).split('.')[0]}_converted.wav")
    command = [FFMPEG_BINARY, "-i", input_path, "-acodec", "pcm_s16le", "-ac", "1", "-ar", "16000", output_path, "-y"]
    import subprocess as _sp
    _sp.run(command, check=True, capture_output=True, timeout=REQUEST_TIMEOUT_GEMINI)
    return output_path

class KeyRotator:
    def __init__(self, keys):
        self.keys = [k.strip() for k in keys.split(",") if k.strip()]
        self.pos = 0
        self.lock = threading.Lock()
    def get_order(self):
        with self.lock:
            n = len(self.keys)
            if n == 0:
                return []
            return [self.keys[(self.pos + i) % n] for i in range(n)]
    def mark_success(self, key):
        with self.lock:
            try:
                i = self.keys.index(key)
                self.pos = i
            except Exception:
                pass
    def mark_failure(self, key):
        with self.lock:
            n = len(self.keys)
            if n == 0:
                return
            try:
                i = self.keys.index(key)
                self.pos = (i + 1) % n
            except Exception:
                self.pos = (self.pos + 1) % n

gemini_rotator = KeyRotator(GEMINI_API_KEYS)

def execute_gemini_action_sync(action_callback):
    last_exc = None
    for key in gemini_rotator.get_order():
        try:
            result = action_callback(key)
            gemini_rotator.mark_success(key)
            return result
        except Exception as e:
            last_exc = e
            gemini_rotator.mark_failure(key)
    raise RuntimeError(f"Gemini failed after rotations. Last error: {last_exc}")

def gemini_api_call_sync(endpoint, payload, key, headers=None):
    url = f"https://generativelanguage.googleapis.com/v1beta/{endpoint}?key={key}"
    resp = requests.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT_GEMINI)
    resp.raise_for_status()
    return resp.json()

def upload_and_transcribe_gemini_sync(file_path: str) -> str:
    original_path, converted_path = file_path, None
    file_ext = os.path.splitext(file_path)[1].lower()
    if file_ext not in [".wav", ".mp3", ".aiff", ".aac", ".ogg", ".flac"]:
        converted_path = convert_to_wav_sync(file_path)
        file_path = converted_path
    file_size = os.path.getsize(file_path)
    mime_type = "audio/wav"
    def perform_upload_and_transcribe(key):
        uploaded_name = None
        try:
            upload_url = f"https://generativelanguage.googleapis.com/upload/v1beta/files?key={key}"
            headers = {"X-Goog-Upload-Protocol": "raw", "X-Goog-Upload-Command": "start, upload, finalize", "X-Goog-Upload-Header-Content-Length": str(file_size), "Content-Type": mime_type}
            with open(file_path, "rb") as f:
                up_resp = requests.post(upload_url, headers=headers, data=f.read(), timeout=REQUEST_TIMEOUT_GEMINI).json()
            uploaded_name = up_resp.get("name", up_resp.get("file", {}).get("name"))
            uploaded_uri = up_resp.get("uri", up_resp.get("file", {}).get("uri"))
            if not uploaded_name:
                raise RuntimeError(f"Upload failed: {up_resp}")
            prompt = "Transcribe the audio in this file. Automatically detect the language and provide a clean transcription. Do not add intro phrases."
            payload = {"contents": [{"parts": [{"fileData": {"mimeType": mime_type, "fileUri": uploaded_uri}}, {"text": prompt}]}]}
            data = gemini_api_call_sync(f"models/{GEMINI_MODEL}:generateContent", payload, key, headers={"Content-Type": "application/json"})
            return data["candidates"][0]["content"]["parts"][0]["text"]
        finally:
            if uploaded_name:
                try:
                    requests.delete(f"https://generativelanguage.googleapis.com/v1beta/{uploaded_name}?key={key}", timeout=5)
                except Exception:
                    pass
    try:
        return execute_gemini_action_sync(perform_upload_and_transcribe)
    finally:
        if converted_path and os.path.exists(converted_path):
            os.remove(converted_path)

def ask_gemini_sync(text, instruction):
    def perform_text_query(key):
        payload = {"contents": [{"parts": [{"text": f"{instruction}\n\n{text}"}]}]}
        data = gemini_api_call_sync(f"models/{GEMINI_MODEL}:generateContent", payload, key, headers={"Content-Type": "application/json"})
        return data["candidates"][0]["content"]["parts"][0]["text"]
    return execute_gemini_action_sync(perform_text_query)

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

async def ensure_joined(client, obj):
    if not REQUIRED_CHANNEL:
        return True
    try:
        if hasattr(obj, "from_user"):
            uid = obj.from_user.id
        else:
            uid = obj.chat.id
        member = await client.get_chat_member(REQUIRED_CHANNEL, uid)
        return member.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER, ChatMemberStatus.RESTRICTED)
    except Exception:
        return False

async def send_long_text(client, chat_id, text, reply_id, uid, action="Transcript"):
    mode = user_mode.get(uid, "📄 Text File")
    if len(text) > MAX_MESSAGE_CHUNK:
        if mode == "Split messages":
            sent = None
            for i in range(0, len(text), MAX_MESSAGE_CHUNK):
                await client.send_chat_action(chat_id, ChatAction.TYPING)
                sent = await client.send_message(chat_id, text[i:i+MAX_MESSAGE_CHUNK], reply_to_message_id=reply_id)
            return sent
        else:
            fname = os.path.join(DOWNLOADS_DIR, f"{action}.txt")
            with open(fname, "w", encoding="utf-8") as f:
                f.write(text)
            await client.send_chat_action(chat_id, ChatAction.UPLOAD_DOCUMENT)
            sent = await client.send_document(chat_id, fname, caption="Open this file and copy the text inside 👍", reply_to_message_id=reply_id)
            os.remove(fname)
            return sent
    return await client.send_message(chat_id, text, reply_to_message_id=reply_id)

web = FastAPI()

@web.get("/", response_class=HTMLResponse)
async def root():
    html = """
    <!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Keepalive</title></head><body><h3>ok</h3></body></html>
    """
    return HTMLResponse(content=html, status_code=200)

@web.get("/ping")
async def ping():
    status = {"bot_connected": tg.is_connected}
    if not tg.is_connected:
        try:
            threading.Thread(target=try_start_tg, daemon=True).start()
        except Exception:
            pass
    return JSONResponse(status)

@web.get("/recent")
async def recent():
    return JSONResponse(web_transcriptions)

@web.post("/webhook_dummy")
async def webhook_dummy():
    return JSONResponse({"ok": True})

def try_start_tg():
    try:
        if not tg.is_connected:
            tg.start()
    except Exception:
        try:
            time.sleep(2)
            tg.start()
        except Exception:
            pass

def tg_watchdog():
    while True:
        try:
            if not tg.is_connected:
                try_start_tg()
        except Exception:
            pass
        time.sleep(10)

def start_web():
    uvicorn.run(web, host="0.0.0.0", port=WEB_PORT, log_level="info")

if __name__ == "__main__":
    threading.Thread(target=start_web, daemon=True).start()
    threading.Thread(target=tg_watchdog, daemon=True).start()
    try_start_tg()
    while True:
        time.sleep(1)
