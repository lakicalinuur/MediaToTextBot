import os
import asyncio
import threading
import json
import requests
import logging
import time
import subprocess
from flask import Flask, request, abort
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.enums import ChatAction, ChatMemberStatus
import telebot

FFMPEG_ENV = os.environ.get("FFMPEG_BINARY", "")
POSSIBLE_FFMPEG_PATHS = [FFMPEG_ENV, "./ffmpeg", "/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg", "ffmpeg"]
FFMPEG_BINARY = None
for p in POSSIBLE_FFMPEG_PATHS:
    if not p:
        continue
    try:
        subprocess.run([p, "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
        FFMPEG_BINARY = p
        break
    except Exception:
        continue
if FFMPEG_BINARY is None:
    logging.warning("ffmpeg binary not found. Set FFMPEG_BINARY env var or place ffmpeg in ./ffmpeg or /usr/bin/ffmpeg")

IMAGE_PATH = os.environ.get("IMAGE_PATH", "/mnt/data/F56900F3-ABC7-4B3D-8707-BEEEA1E1B521.jpeg")

API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT1_TOKEN = os.environ.get("BOT1_TOKEN", "")
BOT2_TOKEN = os.environ.get("BOT2_TOKEN", "")

WEBHOOK_URL_BASE = os.environ.get("WEBHOOK_URL_BASE", "")
PORT = int(os.environ.get("PORT", "8080"))

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

USER_FREE_USAGE = int(os.environ.get("USER_FREE_USAGE", 1))

def parse_keys(s):
    if not s:
        return []
    parts = [p.strip() for p in s.split(",")]
    return [p for p in parts if p]

class KeyRotator:
    def __init__(self, keys):
        self.keys = list(keys)
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

gemini_rotator = KeyRotator(parse_keys(GEMINI_API_KEYS))

app = Client("media_transcriber", api_id=API_ID, api_hash=API_HASH, bot_token=BOT1_TOKEN)

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
user_usage_count = {}

def get_user_mode(uid, default="📄 Text File"):
    return user_mode.get(uid, default)

def get_user_usage_count(uid):
    return user_usage_count.get(uid, 0)

def increment_user_usage_count(uid):
    user_usage_count[uid] = user_usage_count.get(uid, 0) + 1
    return user_usage_count[uid]

def convert_to_wav(input_path: str) -> str:
    if FFMPEG_BINARY is None:
        raise RuntimeError("FFmpeg binary not found for conversion.")
    base_name = os.path.basename(input_path)
    file_id = os.path.splitext(base_name)[0]
    output_path = os.path.join(DOWNLOADS_DIR, f"{file_id}_converted.wav")
    command = [
        FFMPEG_BINARY,
        "-i", input_path,
        "-acodec", "pcm_s16le",
        "-ac", "1",
        "-ar", "16000",
        output_path,
        "-y"
    ]
    logging.info(f"Running FFmpeg command: {' '.join(command)}")
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=REQUEST_TIMEOUT_GEMINI)
        return output_path
    except subprocess.CalledProcessError as e:
        logging.error(f"FFmpeg failed with error: {e.stderr.decode()}")
        raise RuntimeError(f"FFmpeg conversion failed: {e.stderr.decode()}")
    except subprocess.TimeoutExpired:
        logging.error("FFmpeg conversion timed out.")
        raise RuntimeError("FFmpeg conversion timed out.")
    except Exception as e:
        logging.error(f"FFmpeg error: {e}")
        raise RuntimeError(f"FFmpeg conversion failed: {e}")

def upload_and_transcribe_gemini(file_path: str) -> str:
    if not gemini_rotator.keys:
        raise RuntimeError("No Gemini keys available")
    mime_type = "audio/wav"
    file_ext = os.path.splitext(file_path)[1].lower()
    gemini_supported_extensions = [".wav", ".mp3", ".aiff", ".aac", ".ogg", ".flac"]
    requires_conversion = file_ext not in gemini_supported_extensions
    original_file_path = file_path
    converted_path = None
    if requires_conversion:
        try:
            converted_path = convert_to_wav(file_path)
            file_path = converted_path
            mime_type = "audio/wav"
            logging.info(f"File {original_file_path} converted to {file_path}")
        except RuntimeError as e:
            raise RuntimeError(f"Failed to convert media file: {e}")
    last_exc = None
    transcription_text = None
    uploaded_file_name = None
    uploaded_file_uri = None
    try:
        file_size = os.path.getsize(file_path)
    except Exception as e:
        raise RuntimeError(f"Could not read file size: {e}")
    for key in gemini_rotator.get_order():
        try:
            upload_url = f"https://generativelanguage.googleapis.com/upload/v1beta/files?key={key}"
            headers = {
                "X-Goog-Upload-Protocol": "raw",
                "X-Goog-Upload-Command": "start, upload, finalize",
                "X-Goog-Upload-Header-Content-Length": str(file_size),
                "Content-Type": mime_type
            }
            logging.info(f"Attempting RAW file upload: {file_path} with key {key[:4]}...")
            with open(file_path, 'rb') as f:
                data = f.read()
                upload_response = requests.post(
                    upload_url,
                    headers=headers,
                    data=data,
                    timeout=REQUEST_TIMEOUT_GEMINI
                )
            if upload_response.status_code != 200:
                logging.warning(f"Gemini Upload failed (HTTP {upload_response.status_code}), rotating. {upload_response.text}")
                gemini_rotator.mark_failure(key)
                last_exc = f"Upload Error {upload_response.status_code}: {upload_response.text}"
                continue
            upload_data = upload_response.json()
            if "file" in upload_data:
                uploaded_file_name = upload_data["file"].get("name")
                uploaded_file_uri = upload_data["file"].get("uri")
            else:
                uploaded_file_name = upload_data.get("name")
                uploaded_file_uri = upload_data.get("uri")
            if not uploaded_file_uri or not uploaded_file_name:
                logging.error(f"Missing name or URI in response: {upload_data}")
                raise RuntimeError("Uploaded file URI/Name missing from response.")
            logging.info(f"File uploaded: Name={uploaded_file_name}, URI={uploaded_file_uri}")
            state_check_url = f"https://generativelanguage.googleapis.com/v1beta/{uploaded_file_name}?key={key}"
            for _ in range(5):
                state_resp = requests.get(state_check_url, timeout=10)
                if state_resp.status_code == 200:
                    state_data = state_resp.json()
                    state = state_data.get("state", "PROCESSING")
                    if state == "ACTIVE":
                        break
                    elif state == "FAILED":
                        raise RuntimeError(f"File processing failed: {state_data}")
                time.sleep(2)
            generate_url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={key}"
            prompt = "Transcribe the audio in this file. Automatically detect the language and provide a clean, accurate transcription in the original language of the audio. Do not add any introductory phrases or explanations."
            payload = {
                "contents": [{
                    "parts": [
                        {"fileData": {"mimeType": mime_type, "fileUri": uploaded_file_uri}},
                        {"text": prompt}
                    ]
                }]
            }
            logging.info(f"Attempting transcription with model {GEMINI_MODEL}...")
            response = requests.post(
                generate_url,
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=REQUEST_TIMEOUT_GEMINI
            )
            if response.status_code != 200:
                logging.warning(f"Gemini Transcription failed (HTTP {response.status_code}), rotating. {response.text}")
                gemini_rotator.mark_failure(key)
                last_exc = f"Transcription Error {response.status_code}: {response.text}"
                try:
                    delete_url = f"https://generativelanguage.googleapis.com/v1beta/{uploaded_file_name}?key={key}"
                    requests.delete(delete_url, timeout=10)
                except Exception:
                    pass
                uploaded_file_name = None
                continue
            response_data = response.json()
            try:
                transcription_text = response_data["candidates"][0]["content"]["parts"][0]["text"]
            except (KeyError, IndexError):
                raise RuntimeError("Empty or malformed transcription response from Gemini.")
            gemini_rotator.mark_success(key)
            delete_url = f"https://generativelanguage.googleapis.com/v1beta/{uploaded_file_name}?key={key}"
            requests.delete(delete_url, timeout=10)
            logging.info(f"Uploaded file {uploaded_file_name} deleted.")
            uploaded_file_name = None
            break
        except Exception as e:
            logging.warning(f"Gemini transcription general error, rotating to next key: {str(e)}")
            gemini_rotator.mark_failure(key)
            last_exc = e
            if uploaded_file_name:
                try:
                    delete_url = f"https://generativelanguage.googleapis.com/v1beta/{uploaded_file_name}?key={key}"
                    requests.delete(delete_url, timeout=10)
                except Exception:
                    pass
                uploaded_file_name = None
            continue
    if converted_path and os.path.exists(converted_path):
        os.remove(converted_path)
    if transcription_text is None:
        raise RuntimeError(f"Gemini transcription failed after all key rotations. Last error: {last_exc}")
    return transcription_text

def ask_gemini(text, instruction, timeout=REQUEST_TIMEOUT_GEMINI):
    if not gemini_rotator.keys:
        raise RuntimeError("No GEMINI keys available for text processing")
    last_exc = None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    for key in gemini_rotator.get_order():
        try:
            params = {"key": key}
            headers = {"Content-Type": "application/json"}
            payload = {
                "contents": [{
                    "parts": [{"text": f"{instruction}\n\n{text}"}]
                }]
            }
            response = requests.post(url, params=params, headers=headers, json=payload, timeout=timeout)
            if response.status_code == 200:
                gemini_rotator.mark_success(key)
                data = response.json()
                try:
                    return data["candidates"][0]["content"]["parts"][0]["text"]
                except (KeyError, IndexError):
                    return "Error: Empty response from Gemini."
            elif response.status_code == 403:
                logging.warning(f"Gemini key failed (403), rotating. {response.text}")
                gemini_rotator.mark_failure(key)
                last_exc = f"403 Forbidden: {response.text}"
                continue
            else:
                logging.warning(f"Gemini API Error {response.status_code}, rotating. {response.text}")
                gemini_rotator.mark_failure(key)
                last_exc = f"HTTP {response.status_code}: {response.text}"
                continue
        except Exception as e:
            logging.warning("Gemini general error, rotating to next key: %s", str(e))
            gemini_rotator.mark_failure(key)
            last_exc = e
            continue
    raise RuntimeError(f" Gemini key failed. Last error: {last_exc}")

def build_action_keyboard(text_length):
    buttons = []
    buttons.append([InlineKeyboardButton("⭐️ Get translating", callback_data=f"translate_menu|")])
    if text_length > 1000:
        buttons.append([InlineKeyboardButton("Summarize", callback_data=f"summarize|")])
    return InlineKeyboardMarkup(buttons)

def build_language_keyboard(origin):
    buttons, row = [], []
    for i, (label, code) in enumerate(LANGS, 1):
        row.append(InlineKeyboardButton(label, callback_data=f"lang|{code}|{label}|{origin}"))
        if i % 3 == 0:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)

async def download_media(message: Message) -> str:
    file_path = await message.download(file_name=os.path.join(DOWNLOADS_DIR, ""))
    return file_path

WELCOME_MESSAGE = """👋 **Salaam!**
• Send me
• **voice message**
• **audio file**
• **video**
• to transcribe for free
"""

HELP_MESSAGE = f"""/start - Show welcome message
/help - This help message
Send a voice/audio/video (up to {MAX_UPLOAD_MB}MB) to transcribe 
"""

async def is_user_in_channel(client, user_id: int) -> bool:
    if not REQUIRED_CHANNEL:
        return True
    try:
        member = await client.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
            ChatMemberStatus.RESTRICTED
        )
    except Exception:
        return False

async def send_join_prompt_to_target(client, uid: int, reply_target=None):
    clean_channel_username = REQUIRED_CHANNEL.replace("@", "")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Join", url=f"https://t.me/{clean_channel_username}")]
    ])
    text = f"First, join my channel 😜"
    try:
        if reply_target is not None:
            try:
                await reply_target.reply_text(text, reply_markup=kb)
                return
            except Exception:
                pass
        await client.send_message(uid, text, reply_markup=kb)
    except Exception:
        pass

async def ensure_joined(client, obj) -> bool:
    if not REQUIRED_CHANNEL:
        return True
    if isinstance(obj, CallbackQuery):
        uid = obj.from_user.id
        reply_target = obj.message
    else:
        uid = obj.from_user.id
        reply_target = obj
    try:
        free_count = get_user_usage_count(uid)
        if free_count < USER_FREE_USAGE:
            return True
    except Exception:
        pass
    try:
        if await is_user_in_channel(client, uid):
            return True
    except Exception:
        pass
    try:
        if isinstance(obj, CallbackQuery):
            try:
                await obj.answer("🚫 First join my channel", show_alert=True)
            except Exception:
                pass
        await send_join_prompt_to_target(client, uid, reply_target)
    except Exception:
        try:
            await client.send_message(uid, "🚫 Please join my channel to continue.")
        except Exception:
            pass
    return False

@app.on_message(filters.command("start") & (filters.private | filters.group))
async def start(client, message: Message):
    if not await ensure_joined(client, message):
        return
    await message.reply_text(WELCOME_MESSAGE)

@app.on_message(filters.command("help") & (filters.private | filters.group))
async def help_command(client, message: Message):
    if not await ensure_joined(client, message):
        return
    await message.reply_text(HELP_MESSAGE)

@app.on_callback_query(filters.regex(r"^lang\|"))
async def language_callback_query(client, callback_query: CallbackQuery):
    if not await ensure_joined(client, callback_query):
        return
    try:
        parts = callback_query.data.split("|")
        _, code, label = parts[:3]
        origin = parts[3] if len(parts) > 3 else "unknown"
    except Exception:
        await callback_query.answer("Invalid language selection data.", show_alert=True)
        return
    
    if origin == "trans":
        await callback_query.answer(f"Translating to {label}...", show_alert=False)
        chat_id = callback_query.message.chat.id
        message_id = callback_query.message.id
        transcription_data = user_transcriptions.get(chat_id, {}).get(message_id)
        
        if not transcription_data:
            await callback_query.message.delete()
            await callback_query.message.reply_text("Original transcription data not found.")
            return

        original_text = transcription_data["text"]
        await callback_query.message.delete()
        await client.send_chat_action(chat_id, ChatAction.TYPING)

        instruction = f"Translate this text into {label}. Do not add any introductory phrases, or the original text. ONLY return the translated text."
        try:
            loop = asyncio.get_event_loop()
            translated_text = await loop.run_in_executor(None, ask_gemini, original_text, instruction)
            
            if len(translated_text) > MAX_MESSAGE_CHUNK:
                uid = callback_query.from_user.id
                mode = get_user_mode(uid, "Text File")
                if mode == "Split messages":
                    for part in [translated_text[i:i+MAX_MESSAGE_CHUNK] for i in range(0, len(translated_text), MAX_MESSAGE_CHUNK)]:
                        await client.send_message(chat_id, part, reply_to_message_id=transcription_data["origin"])
                else:
                    file_name = os.path.join(DOWNLOADS_DIR, f"Translation_{code}.txt")
                    with open(file_name, "w", encoding="utf-8") as f:
                        f.write(translated_text)
                    await client.send_document(chat_id, file_name, caption="Open this file and copy the text inside 👍", reply_to_message_id=transcription_data["origin"])
                    os.remove(file_name)
            else:
                await client.send_message(chat_id, translated_text, reply_to_message_id=transcription_data["origin"])
                
        except Exception as e:
            await client.send_message(chat_id, f"Translation error: {e}", reply_to_message_id=transcription_data["origin"])

    else:
        await callback_query.answer("Unknown origin", show_alert=True)

@app.on_message(filters.command("mode") & (filters.private | filters.group))
async def choose_mode(client, message: Message):
    if not await ensure_joined(client, message):
        return
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Split messages", callback_data="mode|Split messages")],
        [InlineKeyboardButton("📄 Text File", callback_data="mode|Text File")]
    ])
    await message.reply_text("Choose **output mode**:", reply_markup=keyboard)

@app.on_callback_query(filters.regex(r"^mode\|"))
async def mode_callback_query(client, callback_query: CallbackQuery):
    if not await ensure_joined(client, callback_query):
        return
    try:
        _, mode_name = callback_query.data.split("|")
    except Exception:
        await callback_query.answer("Invalid mode selection data.", show_alert=True)
        return
    uid = callback_query.from_user.id
    user_mode[uid] = mode_name
    await callback_query.answer(f"Mode set to: {mode_name}", show_alert=False)
    try:
        await callback_query.message.delete()
    except Exception:
        pass

@app.on_message((filters.private | filters.group) & filters.text)
async def handle_text(client, message: Message):
    if not await ensure_joined(client, message):
        return
    uid = message.from_user.id
    text = message.text
    if text in ["Split messages", "Text File", "💬 Split messages", "📄 Text File"]:
        mode_name = text.replace("💬 ", "").replace("📄 ", "")
        user_mode[uid] = mode_name
        await message.reply_text(f"Output mode set to: **{mode_name}**")
        return

@app.on_callback_query(filters.regex(r"^(translate_menu|summarize)\|"))
async def action_callback_query(client, callback_query: CallbackQuery):
    if not await ensure_joined(client, callback_query):
        return
    action, _ = callback_query.data.split("|")
    chat_id = callback_query.message.chat.id
    message_id = callback_query.message.id
    transcription_data = user_transcriptions.get(chat_id, {}).get(message_id)
    
    if not transcription_data:
        await callback_query.answer("Transcription not found. Please resend the message.", show_alert=True)
        return
    
    if action == "translate_menu":
        await callback_query.message.edit_reply_markup(build_language_keyboard("trans"))
        return

    original_text = transcription_data["text"]
    key = f"{chat_id}|{message_id}|{action}"
    usage_count = action_usage.get(key, 0)
    
    if usage_count >= 1:
        await callback_query.answer(f"{action.capitalize()} unavailable (maybe expired or Used)", show_alert=True)
        return
    
    await callback_query.answer("Processing...", show_alert=False)
    await client.send_chat_action(chat_id, ChatAction.TYPING)
    instruction = ""
    if action == "summarize":
        instruction = "What is this report and what is it about? Please summarize them for me into the original language of the text without adding any introductions, notes, or extra phrases."
    
    try:
        loop = asyncio.get_event_loop()
        processed_text = await loop.run_in_executor(None, ask_gemini, original_text, instruction)
        
        action_usage[key] = usage_count + 1
        
        uid = callback_query.from_user.id
        if len(processed_text) > MAX_MESSAGE_CHUNK:
            mode = get_user_mode(uid, "Text File")
            if mode == "Split messages":
                await client.send_message(chat_id, "Long result: sending in parts.", reply_to_message_id=transcription_data["origin"])
                for part in [processed_text[i:i+MAX_MESSAGE_CHUNK] for i in range(0, len(processed_text), MAX_MESSAGE_CHUNK)]:
                    await client.send_message(chat_id, part, reply_to_message_id=transcription_data["origin"])
            else:
                file_name = os.path.join(DOWNLOADS_DIR, f"{action}.txt")
                with open(file_name, "w", encoding="utf-8") as f:
                    f.write(processed_text)
                caption_text = "Open this file and copy the text inside 👍" if action == "summarize" else action.capitalize()
                await client.send_document(chat_id, file_name, caption=caption_text, reply_to_message_id=transcription_data["origin"])
                os.remove(file_name)
        else:
            await client.send_message(chat_id, processed_text, reply_to_message_id=transcription_data["origin"])
    except RuntimeError as e:
        await callback_query.message.reply_text(f"❌ Error: {e}", reply_to_message_id=transcription_data["origin"])
    except Exception as e:
        logging.error(f"Action callback error: {e}")
        await callback_query.message.reply_text(f"❌ Unexpected error: {e}", reply_to_message_id=transcription_data["origin"])

@app.on_message((filters.private | filters.group) & (filters.audio | filters.voice | filters.video | filters.document))
async def handle_media(client, message: Message):
    if not await ensure_joined(client, message):
        return
    uid = message.from_user.id
    if REQUIRED_CHANNEL and get_user_usage_count(uid) < USER_FREE_USAGE:
        increment_user_usage_count(uid)
    
    size = None
    duration = None
    media = None

    if getattr(message, "document", None):
        media = message.document
    elif getattr(message, "audio", None):
        media = message.audio
    elif getattr(message, "video", None):
        media = message.video
    elif getattr(message, "voice", None):
        media = message.voice
    
    if media:
        size = getattr(media, "file_size", None)
        duration = getattr(media, "duration", None)
    
    if size is not None and size > MAX_UPLOAD_SIZE:
        await message.reply_text(f"Just Send me a file less than {MAX_UPLOAD_MB}MB 😎")
        return

    if duration is not None and duration > MAX_AUDIO_DURATION_SEC:
        hours = MAX_AUDIO_DURATION_SEC // 3600
        await message.reply_text(f"Bot-ka ma aqbalayo cod ka dheer {hours} saac. Fadlan soo dir mid ka gaaban.")
        return
        
    if FFMPEG_BINARY is None:
        file_ext = ""
        if getattr(message, "document", None):
            file_ext = os.path.splitext(message.document.file_name)[1].lower() if message.document.file_name else ""
        elif getattr(message, "video", None):
            file_ext = ".mp4"
        gemini_supported_extensions = [".wav", ".mp3", ".aiff", ".aac", ".ogg", ".flac"]
        if file_ext and file_ext not in gemini_supported_extensions:
             await message.reply_text("FFMPEG not available. Cannot convert videos. Transcription will fail.")
             return
    
    mode = get_user_mode(uid, "Text File")
    await client.send_chat_action(message.chat.id, ChatAction.TYPING)
    file_path = None
    try:
        file_path = await download_media(message)
    except Exception as e:
        await message.reply_text(f"⚠️ Download error: {e}")
        return
    await client.send_chat_action(message.chat.id, ChatAction.TYPING)
    try:
        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(None, upload_and_transcribe_gemini, file_path)
    except Exception as e:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
        await message.reply_text(f"❌ Transcription error: {e}")
        return
    finally:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
    if not text or text.startswith("Error:"):
        warning_text = text or "⚠️ Warning Make sure the voice is clear."
        await message.reply_text(warning_text, reply_to_message_id=message.id)
        return
    reply_msg_id = message.id
    sent_message = None
    if len(text) > MAX_MESSAGE_CHUNK:
        if mode == "Split messages":
            for part in [text[i:i+MAX_MESSAGE_CHUNK] for i in range(0, len(text), MAX_MESSAGE_CHUNK)]:
                await client.send_chat_action(message.chat.id, ChatAction.TYPING)
                sent_message = await message.reply_text(part, reply_to_message_id=reply_msg_id)
        else:
            file_name = os.path.join(DOWNLOADS_DIR, "Transcript.txt")
            with open(file_name, "w", encoding="utf-8") as f:
                f.write(text)
            await client.send_chat_action(message.chat.id, ChatAction.UPLOAD_DOCUMENT)
            sent_message = await client.send_document(message.chat.id, file_name, caption="Open this file and copy the text inside 👍", reply_to_message_id=reply_msg_id)
            os.remove(file_name)
    else:
        await client.send_chat_action(message.chat.id, ChatAction.TYPING)
        sent_message = await message.reply_text(text, reply_to_message_id=reply_msg_id)
    if sent_message:
        try:
            keyboard = build_action_keyboard(len(text))
            user_transcriptions.setdefault(sent_message.chat.id, {})[sent_message.id] = {"text": text, "origin": reply_msg_id}
            if len(text) > 1000:
                action_usage[f"{sent_message.chat.id}|{sent_message.id}|summarize"] = 0
            await sent_message.edit_reply_markup(keyboard)
        except Exception as e:
            logging.error(f"Failed to attach keyboard or init usage: {e}")

telebot_bot = telebot.TeleBot(BOT2_TOKEN, threaded=False)
flask_app = Flask(__name__)
WEBHOOK_PATH = os.environ.get("WEBHOOK_PATH", "/webhook/")
WEBHOOK_URL = WEBHOOK_URL_BASE.rstrip('/') + WEBHOOK_PATH if WEBHOOK_URL_BASE else ""
MEDIA_TO_TEXT_BOT_LINK = os.environ.get("MEDIA_TO_TEXT_BOT_LINK", "https://t.me/MediaToTextBot")

@flask_app.route("/", methods=["GET", "POST", "HEAD"])
def keep_alive_flask():
    return "Bot is alive (Flask) ✅", 200

@flask_app.route(WEBHOOK_PATH, methods=['POST'])
def webhook_handler():
    if request.headers.get('content-type') == 'application/json':
        update = telebot.types.Update.de_json(request.data.decode('utf-8'))
        telebot_bot.process_new_updates([update])
        return '', 200
    else:
        abort(403)

@flask_app.route("/set_webhook", methods=["GET"])
def set_wh():
    try:
        if WEBHOOK_URL:
            telebot_bot.set_webhook(url=WEBHOOK_URL)
            return f"Webhook set to {WEBHOOK_URL}", 200
        return "No WEBHOOK_URL configured", 500
    except Exception as e:
        logging.error(f"Failed to set webhook for Bot 2: {e}")
        return "error", 500

@flask_app.route("/delete_webhook", methods=["GET"])
def del_wh():
    try:
        telebot_bot.delete_webhook()
        return "Webhook deleted.", 200
    except Exception as e:
        logging.error(f"Failed to delete webhook for Bot 2: {e}")
        return "error", 500

@telebot_bot.message_handler(content_types=["text", "photo", "audio", "voice", "video", "sticker", "document", "animation", "new_chat_members", "left_chat_member"])
def handle_all_messages(message):
    reply = (
        f"[Use our new bot]({MEDIA_TO_TEXT_BOT_LINK})\n"
        f"[استخدم بوتنا الجديد]({MEDIA_TO_TEXT_BOT_LINK})\n"
        f"[👇🏻👇🏻👇🏻👇🏻👇🏻👇🏻👇🏻]({MEDIA_TO_TEXT_BOT_LINK})"
    )
    telebot_bot.reply_to(message, reply, parse_mode="Markdown")

def run_flask():
    try:
        telebot_bot.delete_webhook()
    except Exception:
        pass
    time.sleep(1)
    try:
        if WEBHOOK_URL:
            telebot_bot.set_webhook(url=WEBHOOK_URL)
            logging.info(f"Relay bot webhook set successfully to {WEBHOOK_URL}")
    except Exception as e:
        logging.error(f"Failed to set relay bot webhook on startup: {e}")
    flask_app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    app.run()
