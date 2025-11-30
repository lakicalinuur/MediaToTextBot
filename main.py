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
# assemblyai waxaan ka saaray
from pymongo import MongoClient
import telebot
# Ku darista Gemini SDK
from google import genai

# --- QAYBTA FFMPEG CHECKING IYO BINARY LOCATION (Sidaad Codsatay) ---
FFMPEG_ENV = os.environ.get("FFMPEG_BINARY", "")
POSSIBLE_FFMPEG_PATHS = [FFMPEG_ENV, "./ffmpeg", "/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg", "ffmpeg"]
FFMPEG_BINARY = None
for p in POSSIBLE_FFMPEG_PATHS:
    if not p:
        continue
    try:
        # Tijaabi in ffmpeg la heli karo
        subprocess.run([p, "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
        FFMPEG_BINARY = p
        break
    except Exception:
        continue
if FFMPEG_BINARY is None:
    logging.warning("ffmpeg binary not found. Set FFMPEG_BINARY env var or place ffmpeg in ./ffmpeg or /usr/bin/ffmpeg")

# --- XOGTA IYO DEGENERATION (SETTINGS) ---

IMAGE_PATH = os.environ.get("IMAGE_PATH", "/mnt/data/F56900F3-ABC7-4B3D-8707-BEEEA1E1B521.jpeg")

DB_USER = os.environ.get("DB_USER", "")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_APPNAME = os.environ.get("DB_APPNAME", "SpeechBot")
MONGO_URI = os.environ.get("MONGO_URI") or f"mongodb+srv://{DB_USER}:{DB_PASSWORD}@cluster0.n4hdlxk.mongodb.net/{DB_APPNAME}?retryWrites=true&w=majority&appName={DB_APPNAME}"

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

# AssemblyAI waxaan ka saaray
DEFAULT_GEMINI_KEYS = os.environ.get("DEFAULT_GEMINI_KEYS", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash") # KUSOO QAADASHADA MODEL KA CUSUB

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

# AssemblyAI rotator waxaan ka saaray
gemini_rotator = KeyRotator(parse_keys(GEMINI_API_KEYS))

# Waxaan ku soo daray genai.Client
try:
    gemini_client = genai.Client(api_key=gemini_rotator.keys[0])
except IndexError:
    logging.warning("No Gemini API keys provided. Please set GEMINI_API_KEYS.")
except Exception as e:
    logging.error(f"Error initializing Gemini client: {e}")
    gemini_client = None


mongo_client = MongoClient(MONGO_URI)
db = mongo_client[DB_APPNAME]
users_collection = db.users
usage_collection = db.usage

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
user_lang = {}
user_mode = {}
user_transcriptions = {}
action_usage = {}
user_usage_count = {}

# --- FUNCTIONS-KII AYAA ISBADALAY ---

def set_user_preferences(uid, lang=None, mode=None):
    update = {}
    if lang is not None:
        update["lang"] = lang
    if mode is not None:
        update["mode"] = mode
    if update:
        users_collection.update_one({"_id": uid}, {"$set": update}, upsert=True)
        if "lang" in update:
            user_lang[uid] = update["lang"]
        if "mode" in update:
            user_mode[uid] = update["mode"]

def get_user_preferences(uid):
    doc = users_collection.find_one({"_id": uid})
    return doc or {}

def get_user_lang(uid, default="en"):
    if uid in user_lang:
        return user_lang[uid]
    doc = get_user_preferences(uid)
    lang = doc.get("lang")
    if lang:
        user_lang[uid] = lang
        return lang
    return default

def get_user_mode(uid, default="📄 Text File"):
    if uid in user_mode:
        return user_mode[uid]
    doc = get_user_preferences(uid)
    mode = doc.get("mode")
    if mode:
        user_mode[uid] = mode
        return mode
    return default

def get_user_usage_count(uid):
    doc = usage_collection.find_one({"_id": uid})
    return doc.get("count", 0) if doc else 0

def increment_user_usage_count(uid):
    usage_collection.update_one({"_id": uid}, {"$inc": {"count": 1}}, upsert=True)
    return get_user_usage_count(uid)

# Funtion Cusub si uu ugu rogo media-ha mid la adeegsan karo (WAV)
def convert_to_wav(input_path: str) -> str:
    """Wuxuu media file u rogayaa WAV format (16-bit PCM, 16 KHz)."""
    if FFMPEG_BINARY is None:
        raise RuntimeError("FFmpeg binary not found for conversion.")

    # Faylka cusub ee WAV ah ayaa la abuuri doonaa meesha Downloads_DIR
    base_name = os.path.basename(input_path)
    file_id = os.path.splitext(base_name)[0]
    output_path = os.path.join(DOWNLOADS_DIR, f"{file_id}_converted.wav")

    # Command-ka lagu rogaayo: -ac 1 (mono), -ar 16000 (16kHz), -acodec pcm_s16le (16-bit PCM)
    command = [
        FFMPEG_BINARY,
        "-i", input_path,
        "-acodec", "pcm_s16le",
        "-ac", "1",
        "-ar", "16000",
        output_path,
        "-y" # Overwrite
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

# Transcribe file using Gemini STT
def transcribe_file_gemini(file_path: str, lang_code: str = "en") -> str:
    """Wuxuu u dirayaa faylka codka Gemini si looga dhigo qoraal (transcription)."""
    if not gemini_client or not gemini_rotator.keys:
        raise RuntimeError("No Gemini client or keys available")

    # U hubi inuu yahay format-ka la taageero (Gemini File API waxa uu taageeraa: WAV, MP3, AIFF, AAC, OGG, FLAC)
    mime_type = "audio/wav"  # Default-ka hadii la beddelo

    # Wuxuu hubinayaa haddii faylka uu u baahan yahay in la beddelo
    file_ext = os.path.splitext(file_path)[1].lower()
    
    # Faylasha aan ahayn format-ka la taageero (sida MP4) ayaa la beddelayaa
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

    # Key rotator ayaa loo isticmaali doonaa haddii upload-ka ama generate_content ay fashilmaan.
    last_exc = None
    transcription_text = None
    
    # Isku day kasta oo ku fashilma, waxaan beddelaynaa key-ga oo aan dib u bilownaa geeddi-socodka
    for key in gemini_rotator.get_order():
        try:
            # 1. Ku dhig key-ga cusub client-ka
            gemini_client._client._credentials.api_key = key
            
            # 2. Upload faylka
            logging.info(f"Attempting to upload file: {file_path} with key {key[:4]}...")
            uploaded_file = gemini_client.files.upload(file=file_path, mime_type=mime_type)
            logging.info(f"File uploaded successfully: {uploaded_file.name}")

            # 3. Generate content (Transcribe)
            prompt = f"Transcribe the audio in this file. Provide a clean, accurate transcription in {lang_code}. Do not add any introductory phrases or explanations."
            
            logging.info(f"Attempting transcription with model {GEMINI_MODEL}...")
            response = gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[uploaded_file, prompt],
                request_options={"timeout": REQUEST_TIMEOUT_GEMINI}
            )
            
            transcription_text = response.text
            
            # 4. Tirtir uploaded file (si aysan u buuxsamin quota-da)
            gemini_client.files.delete(name=uploaded_file.name)
            logging.info(f"Uploaded file {uploaded_file.name} deleted.")
            
            # Guul
            gemini_rotator.mark_success(key)
            break 
            
        except Exception as e:
            logging.warning(f"Gemini transcription key failed, rotating to next key: {str(e)}")
            gemini_rotator.mark_failure(key)
            last_exc = e
            
            # Isku day inaad tirtirto faylka haddii la soo geliyay
            if 'uploaded_file' in locals():
                try:
                    gemini_client.files.delete(name=uploaded_file.name)
                except Exception:
                    pass
            
            continue # U gudub key-ga xiga

    # Nadiifi converted file
    if converted_path and os.path.exists(converted_path):
        os.remove(converted_path)

    if transcription_text is None:
        raise RuntimeError(f"Gemini transcription failed after all key rotations. Last error: {last_exc}")
        
    return transcription_text

# Function-kii ask_gemini ayaa la cusboonaysiiyay si uu ula shaqeeyo text-ka oo kaliya
def ask_gemini(text, instruction, timeout=REQUEST_TIMEOUT_GEMINI):
    """Wuxuu isticmaalayaa Gemini si uu u sameeyo Clean-up ama Summary (Text-in, Text-out)."""
    if not gemini_client or not gemini_rotator.keys:
        raise RuntimeError("No GEMINI client or keys available for text processing")
    last_exc = None
    
    for key in gemini_rotator.get_order():
        try:
            # Ku dhig key-ga cusub client-ka
            gemini_client._client._credentials.api_key = key
            
            # Isticmaal SDK-ga
            logging.info(f"Attempting text generation with key {key[:4]}...")
            
            response = gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[f"{instruction}\n\n{text}"],
                request_options={"timeout": timeout}
            )
            
            # Guul
            gemini_rotator.mark_success(key)
            return response.text
        
        except Exception as e:
            logging.warning(f"Gemini text processing key failed, rotating to next key: {str(e)}")
            gemini_rotator.mark_failure(key)
            last_exc = e
            continue
            
    raise RuntimeError(f"Gemini text processing failed after all key rotations. Last error: {last_exc}")


def build_action_keyboard(chat_id, message_id, text_length):
    buttons = []
    buttons.append([InlineKeyboardButton("⭐️Clean transcript", callback_data=f"clean|{chat_id}|{message_id}")])
    if text_length > 1000:
        buttons.append([InlineKeyboardButton("Get Summarize", callback_data=f"summarize|{chat_id}|{message_id}")])
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
/lang  - Change language
/mode  - Change result delivery mode
/help  - This help message

Send a voice/audio/video (up to {MAX_UPLOAD_MB}MB) and I will transcribe it \n The dev and admin are @orlaki
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
            await client.send_message(uid, "🚫 Please join  my channel to continue.")
        except Exception:
            pass
    return False

@app.on_message(filters.command("start") & filters.private)
async def start(client, message: Message):
    if not await ensure_joined(client, message):
        return
    buttons, row = [], []
    for i, (label, code) in enumerate(LANGS, 1):
        row.append(InlineKeyboardButton(label, callback_data=f"lang|{code}|{label}|start"))
        if i % 3 == 0:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    keyboard = InlineKeyboardMarkup(buttons)
    await message.reply_text("**Choose your file language for transcription using the below buttons:**", reply_markup=keyboard)

@app.on_message(filters.command("help") & filters.private)
async def help_command(client, message: Message):
    if not await ensure_joined(client, message):
        return
    await message.reply_text(HELP_MESSAGE)

@app.on_message(filters.command("lang") & filters.private)
async def lang_command(client, message: Message):
    if not await ensure_joined(client, message):
        return
    buttons, row = [], []
    for i, (label, code) in enumerate(LANGS, 1):
        row.append(InlineKeyboardButton(label, callback_data=f"lang|{code}|{label}|lang"))
        if i % 3 == 0:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    keyboard = InlineKeyboardMarkup(buttons)
    await message.reply_text("**Choose your file language for transcription using the below buttons:**", reply_markup=keyboard)

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
    uid = callback_query.from_user.id
    set_user_preferences(uid, lang=code)
    if origin == "start":
        await callback_query.message.edit_text(WELCOME_MESSAGE, reply_markup=None)
    elif origin == "lang":
        await callback_query.message.delete()
    await callback_query.answer(f"Language set to: {label}", show_alert=False)

@app.on_message(filters.command("mode") & filters.private)
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
    set_user_preferences(uid, mode=mode_name)
    await callback_query.answer(f"Mode set to: {mode_name}", show_alert=False)
    try:
        await callback_query.message.delete()
    except Exception:
        pass

@app.on_message(filters.private & filters.text)
async def handle_text(client, message: Message):
    if not await ensure_joined(client, message):
        return
    uid = message.from_user.id
    text = message.text
    if text in ["💬 Split messages", "📄 Text File"]:
        user_mode[uid] = text
        set_user_preferences(uid, mode=text)
        await message.reply_text(f"Output mode set to: **{text}**")
        return

@app.on_message(filters.private & (filters.audio | filters.voice | filters.video | filters.document))
async def handle_media(client, message: Message):
    if not await ensure_joined(client, message):
        return
    uid = message.from_user.id
    
    # Kordhi tirada isticmaalka ka hor inta uusan bilaabin habka transcription-ka
    if REQUIRED_CHANNEL and get_user_usage_count(uid) < USER_FREE_USAGE:
        increment_user_usage_count(uid)

    if not get_user_lang(uid, None):
        buttons, row = [], []
        for i, (label, code) in enumerate(LANGS, 1):
            row.append(InlineKeyboardButton(label, callback_data=f"lang|{code}|{label}|start"))
            if i % 3 == 0:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        keyboard = InlineKeyboardMarkup(buttons)
        await message.reply_text("**Please choose your file language first:**", reply_markup=keyboard)
        return
    size = None
    try:
        if getattr(message, "document", None) and getattr(message.document, "file_size", None):
            size = message.document.file_size
        elif getattr(message, "audio", None) and getattr(message.audio, "file_size", None):
            size = message.audio.file_size
        elif getattr(message, "video", None) and getattr(message.video, "file_size", None):
            size = message.video.file_size
        elif getattr(message, "voice", None) and getattr(message.voice, "file_size", None):
            size = message.voice.file_size
    except Exception:
        size = None
    if size is not None and size > MAX_UPLOAD_SIZE:
        await message.reply_text(f"Just Send me a file less than {MAX_UPLOAD_MB}MB 😎")
        return
    
    # Hubi in ffmpeg la heli karo haddii la rabo beddelid (conversion)
    if FFMPEG_BINARY is None:
        if getattr(message, "video", None) or (getattr(message, "document", None) and message.document.mime_type in ["video/mp4", "video/quicktime"]):
             await message.reply_text("⚠️ FFMPEG ma la heli karo. Ma beddeli karo MP4 ama Videos-ka kale. Transcription-ka wuu fashilmayaa.")
             return # Waa la sii wadi karaa haddii uu yahay audio format la taageero, laakin hadda waxaan u haynaa inay tahay Video.

    lang = get_user_lang(uid)
    mode = get_user_mode(uid, "📄 Text File")
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
        # ISTICMAALKA GEMINI TRANSCRIPTION FUNCTION
        text = await loop.run_in_executor(None, transcribe_file_gemini, file_path, lang)
    except Exception as e:
        # Hubi inaad ka tirtirto faylka download-ka ah
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
            
        await message.reply_text(f"❌ Transcription error: {e}")
        return
        
    finally:
        # Hubi inaad ka tirtirto faylka download-ka ah ka dib isticmaalka
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
            
    if not text or text.startswith("Error:"):
        await message.reply_text(text or "⚠️ Digniin: Hubi in codka uu cad yahay ama aad ku hadlayso luqadda aad dooratay.", reply_to_message_id=message.id)
        return
        
    reply_msg_id = message.id
    sent_message = None
    if len(text) > MAX_MESSAGE_CHUNK:
        if mode == "💬 Split messages":
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
            keyboard = build_action_keyboard(sent_message.chat.id, sent_message.id, len(text))
            user_transcriptions.setdefault(sent_message.chat.id, {})[sent_message.id] = {"text": text, "origin": reply_msg_id}
            action_usage[f"{sent_message.chat.id}|{sent_message.id}|clean"] = 0
            if len(text) > 1000:
                action_usage[f"{sent_message.chat.id}|{sent_message.id}|summarize"] = 0
            await sent_message.edit_reply_markup(keyboard)
        except Exception as e:
            logging.error(f"Failed to attach keyboard or init usage: {e}")

@app.on_callback_query(filters.regex(r"^clean\|"))
async def clean_up_callback(client, callback_query: CallbackQuery):
    if not await ensure_joined(client, callback_query):
        return
    try:
        _, chat_id_str, msg_id_str = callback_query.data.split("|")
        chat_id = int(chat_id_str)
        msg_id = int(msg_id_str)
    except Exception:
        await callback_query.answer("Invalid callback data.", show_alert=True)
        return
    usage_key = f"{chat_id}|{msg_id}|clean"
    usage = action_usage.get(usage_key, 0)
    if usage >= 1:
        await callback_query.answer("Clean up unavailable (maybe expired or Used)", show_alert=True)
        return
    action_usage[usage_key] = usage + 1
    stored = user_transcriptions.get(chat_id, {}).get(msg_id)
    if not stored:
        await callback_query.answer("Clean up unavailable (maybe expired or Used)", show_alert=True)
        return
    stored_text = stored.get("text")
    orig_msg_id = stored.get("origin")
    await callback_query.answer("Cleaning up...", show_alert=False)
    await client.send_chat_action(chat_id, ChatAction.TYPING)
    try:
        loop = asyncio.get_event_loop()
        uid = callback_query.from_user.id
        lang = get_user_lang(uid, "en")
        mode = get_user_mode(uid, "📄 Text File")
        instruction = f"translate and normalize this transcription in (language={lang}). Remove ASR artifacts like [inaudible], repeated words, filler noises, timestamps, and incorrect punctuation. Produce a clean, well-punctuated, readable text in the same language. Do not add introductions or explanations."
        # ISTICMAALKA GEMINI FUNCTION-KII CUSBOONAYSIIYAY
        cleaned_text = await loop.run_in_executor(None, ask_gemini, stored_text, instruction)
        if not cleaned_text:
            await client.send_message(chat_id, "No cleaned text returned.", reply_to_message_id=orig_msg_id)
            return
        if len(cleaned_text) > MAX_MESSAGE_CHUNK:
            if mode == "💬 Split messages":
                for part in [cleaned_text[i:i+MAX_MESSAGE_CHUNK] for i in range(0, len(cleaned_text), MAX_MESSAGE_CHUNK)]:
                    await client.send_message(chat_id, part, reply_to_message_id=orig_msg_id)
            else:
                file_name = os.path.join(DOWNLOADS_DIR, "Cleaned.txt")
                with open(file_name, "w", encoding="utf-8") as f:
                    f.write(cleaned_text)
                await client.send_chat_action(chat_id, ChatAction.UPLOAD_DOCUMENT)
                await client.send_document(chat_id, file_name, caption="Cleaned Transcript", reply_to_message_id=orig_msg_id)
                os.remove(file_name)
        else:
            await client.send_message(chat_id, cleaned_text, reply_to_message_id=orig_msg_id)
    except Exception as e:
        logging.exception("Error in clean_up_callback")
        await client.send_message(chat_id, f"❌ Error during cleanup: {e}", reply_to_message_id=orig_msg_id)

@app.on_callback_query(filters.regex(r"^summarize\|"))
async def get_key_points_callback(client, callback_query: CallbackQuery):
    if not await ensure_joined(client, callback_query):
        return
    try:
        _, chat_id_str, msg_id_str = callback_query.data.split("|")
        chat_id = int(chat_id_str)
        msg_id = int(msg_id_str)
    except Exception:
        await callback_query.answer("Invalid callback data.", show_alert=True)
        return
    usage_key = f"{chat_id}|{msg_id}|summarize"
    usage = action_usage.get(usage_key, 0)
    if usage >= 1:
        await callback_query.answer("Summarize unavailable (maybe expired or Used)", show_alert=True)
        return
    action_usage[usage_key] = usage + 1
    stored = user_transcriptions.get(chat_id, {}).get(msg_id)
    if not stored:
        await callback_query.answer("Summarize unavailable (maybe expired or Used)", show_alert=True)
        return
    stored_text = stored.get("text")
    orig_msg_id = stored.get("origin")
    await callback_query.answer("Generating summary...", show_alert=False)
    await client.send_chat_action(chat_id, ChatAction.TYPING)
    try:
        loop = asyncio.get_event_loop()
        uid = callback_query.from_user.id
        lang = get_user_lang(uid, "en")
        mode = get_user_mode(uid, "📄 Text File")
        instruction = f"What is this report and what is it about? Please summarize them for me into (lang={lang}) without adding any introductions, notes, or extra phrases."
        # ISTICMAALKA GEMINI FUNCTION-KII CUSBOONAYSIIYAY
        summary = await loop.run_in_executor(None, ask_gemini, stored_text, instruction)
        if not summary:
            await client.send_message(chat_id, "No Summary returned.", reply_to_message_id=orig_msg_id)
            return
        if len(summary) > MAX_MESSAGE_CHUNK:
            if mode == "💬 Split messages":
                for part in [summary[i:i+MAX_MESSAGE_CHUNK] for i in range(0, len(summary), MAX_MESSAGE_CHUNK)]:
                    await client.send_message(chat_id, part, reply_to_message_id=orig_msg_id)
            else:
                file_name = os.path.join(DOWNLOADS_DIR, "Summary.txt")
                with open(file_name, "w", encoding="utf-8") as f:
                    f.write(summary)
                await client.send_chat_action(chat_id, ChatAction.UPLOAD_DOCUMENT)
                await client.send_document(chat_id, file_name, caption="Summary", reply_to_message_id=orig_msg_id)
                os.remove(file_name)
        else:
            await client.send_message(chat_id, summary, reply_to_message_id=orig_msg_id)
    except Exception as e:
        logging.exception("Error in get_key_points_callback")
        await client.send_message(chat_id, f"❌ Error during summary: {e}", reply_to_message_id=orig_msg_id)

# Qaybta Telebot iyo Flask ee webhook-ka

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
