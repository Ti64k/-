import telebot
import os
import instaloader
import time
import random
import shutil
import itertools
import glob
from instaloader import LoginRequiredException, Profile
from telebot.types import InputMediaPhoto, InputMediaVideo, InlineKeyboardMarkup, InlineKeyboardButton
import yt_dlp
import requests
import re
import json
import subprocess

# قراءة المعلومات من السيرفر (آمن)
TOKEN = os.environ.get('BOT_TOKEN')
bot = telebot.TeleBot(TOKEN)

UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 15_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Instagram 239.1.0.26.109"
ACTIVE_SESSIONS = []

# نظام تسجيل الدخول التلقائي
def setup_login():
    print("🚀 Starting login process...")
    user1 = os.environ.get('USER_1')
    pass1 = os.environ.get('PASS_1')
    
    if user1 and pass1:
        try:
            print(f"🔐 Logging in to: {user1}...")
            L = instaloader.Instaloader(
                download_pictures=True, download_videos=True, save_metadata=False, compress_json=False,
                user_agent=UA, max_connection_attempts=1, iphone_support=True
            )
            L.login(user1, pass1)
            filename = f"session-{user1}"
            L.save_session_to_file(filename=filename)
            ACTIVE_SESSIONS.append(L)
            print(f"✅ Success: {user1}")
        except Exception as e:
            print(f"❌ Login Failed: {e}")

setup_login()

def get_random_agent():
    if not ACTIVE_SESSIONS: 
        return instaloader.Instaloader(user_agent=UA, iphone_support=True)
    return random.choice(ACTIVE_SESSIONS)

# --- Highlight Manager ---
class HighlightManager:
    def __init__(self): self._cache = {} 
    def get_highlights_list(self, agent, username):
        try:
            profile = Profile.from_username(agent.context, username)
            highlights = []
            if hasattr(profile, 'get_highlights'):
                for h in profile.get_highlights():
                    highlights.append({'id': h.unique_id, 'title': h.title})
            elif hasattr(profile, 'get_highlight_reels'):
                for h in profile.get_highlight_reels():
                    highlights.append({'id': h.unique_id, 'title': h.title})
            # Fallback manual extraction
            if not highlights and hasattr(profile, '_node'):
                 raw = profile._node.get("edge_highlight_reels", {}).get("edges", [])
                 for edge in raw:
                     node = edge.get("node", {})
                     highlights.append({'id': int(node.get("id")), 'title': node.get("title", "Highlight")})
            return highlights
        except: return None

    def get_highlight_items_page(self, agent, username, highlight_id, offset=0, limit=10):
        try:
            profile = Profile.from_username(agent.context, username)
            target = None
            iterators = []
            if hasattr(profile, 'get_highlights'): iterators.append(profile.get_highlights())
            if hasattr(profile, 'get_highlight_reels'): iterators.append(profile.get_highlight_reels())
            
            for it in iterators:
                for h in it:
                    if str(h.unique_id) == str(highlight_id):
                        target = h; break
                if target: break
            
            if not target: return [], False
            batch = list(itertools.islice(target.get_items(), offset, offset + limit + 1))
            has_more = len(batch) > limit
            return batch[:limit], has_more
        except: return [], False

hl_manager = HighlightManager()

# --- Tools ---
def clean_username(text):
    if "instagram.com" in text: return text.split("?")[0].rstrip("/").split("/")[-1]
    return text

def extract_audio(path):
    try:
        new_path = path.rsplit('.', 1)[0] + ".mp3"
        subprocess.run(f'ffmpeg -i "{path}" -vn -acodec libmp3lame -q:a 2 "{new_path}" -y -loglevel quiet', shell=True)
        return new_path if os.path.exists(new_path) else None
    except: return None

def tiktok_dl(url, out):
    try:
        ydl_opts = {'outtmpl': f'{out}/%(title).50s.%(ext)s', 'quiet': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl: ydl.extract_info(url, download=True)
        return True
    except: return False

def send_files(chat_id, path, caption=""):
    files = []
    for r, d, f in os.walk(path):
        for file in f:
            if file.endswith(('.jpg', '.mp4', '.png')): files.append(os.path.join(r, file))
    if not files: 
        bot.send_message(chat_id, "⚠️ No media found.")
        return
    
    files.sort()
    chunks = [files[i:i + 10] for i in range(0, len(files), 10)]
    for chunk in chunks:
        media = []
        for f in chunk:
            try:
                if f.endswith('.mp4'): media.append(InputMediaVideo(open(f, 'rb'), caption=caption))
                else: media.append(InputMediaPhoto(open(f, 'rb'), caption=caption))
            except: pass
        if media: bot.send_media_group(chat_id, media)

# --- Handlers ---
@bot.message_handler(commands=['start'])
def start(m): bot.reply_to(m, "✅ **Bot Online on Oracle Cloud**")

@bot.message_handler(func=lambda m: "http" in m.text)
def links(m):
    url = m.text.strip()
    if "instagram.com" in url and "/p/" not in url and "/reel/" not in url:
       username_check(m); return

    msg = bot.reply_to(m, "⏳ Processing...")
    uid = f"dl_{m.chat.id}_{time.time()}"
    path = os.path.join(os.getcwd(), uid)
    
    try:
        if "instagram.com" in url:
            L = get_random_agent()
            short = re.search(r'/(p|reel|tv)/([^/?#&]+)', url).group(2)
            post = instaloader.Post.from_shortcode(L.context, short)
            L.download_post(post, target=uid)
        elif "tiktok.com" in url:
            tiktok_dl(url, path)
            
        bot.edit_message_text("📤 Uploading...", m.chat.id, msg.message_id)
        send_files(m.chat.id, path)
        bot.delete_message(m.chat.id, msg.message_id)
    except Exception as e:
        bot.edit_message_text(f"❌ Error: {e}", m.chat.id, msg.message_id)
    try: shutil.rmtree(path)
    except: pass

@bot.message_handler(func=lambda m: True)
def username_check(m):
    user = clean_username(m.text.strip())
    if re.match(r"^[A-Za-z0-9._]+$", user):
        send_recon_dashboard(m, user)

def send_recon_dashboard(message, username):
    msg = bot.reply_to(message, f"📡 Scanning: {username}...")
    L = get_random_agent()
    try:
        profile = Profile.from_username(L.context, username)
        access = "✅ Public" if not profile.is_private or profile.followed_by_viewer else "⛔ Private"
        info = f"🎯 Target: {username}\n📝 {profile.biography}\n🔓 {access}"
        markup = InlineKeyboardMarkup()
        if "Public" in access:
            markup.row(InlineKeyboardButton("Posts", callback_data=f"posts:{username}:0"),
                       InlineKeyboardButton("Highlights", callback_data=f"high:{username}"))
            markup.row(InlineKeyboardButton("Story", callback_data=f"story:{username}"),
                       InlineKeyboardButton("Network", callback_data=f"net:{username}"))
        bot.delete_message(message.chat.id, msg.message_id)
        bot.send_photo(message.chat.id, profile.profile_pic_url, caption=info, reply_markup=markup)
    except: bot.edit_message_text("❌ Not found.", message.chat.id, msg.message_id)

@bot.callback_query_handler(func=lambda call: True)
def callback(call):
    data = call.data.split(':')
    action, user = data[0], data[1]
    path = os.path.join(os.getcwd(), f"cb_{call.message.chat.id}_{time.time()}")
    L = get_random_agent()
    
    try:
        profile = Profile.from_username(L.context, user)
        
        if action == "posts":
            offset = int(data[2])
            bot.answer_callback_query(call.id, "Downloading...")
            posts = profile.get_posts()
            batch = list(itertools.islice(posts, offset, offset + 11))
            has_more = len(batch) > 10
            for p in batch[:10]: 
                try: L.download_post(p, target=path)
                except: pass
            send_files(call.message.chat.id, path, f"Page {offset//10+1}")
            shutil.rmtree(path, ignore_errors=True)
            if has_more:
                nav = InlineKeyboardMarkup()
                nav.add(InlineKeyboardButton("Next", callback_data=f"posts:{user}:{offset+10}"))
                bot.send_message(call.message.chat.id, "More?", reply_markup=nav)

        elif action == "story":
            if not L.context.is_logged_in: 
                bot.send_message(call.message.chat.id, "⚠️ Need Login."); return
            bot.answer_callback_query(call.id, "Checking Stories...")
            stories = L.get_stories(userids=[profile.userid])
            for s in stories:
                for i in s.get_items(): L.download_storyitem(i, target=path)
            send_files(call.message.chat.id, path, "Stories")
            shutil.rmtree(path, ignore_errors=True)

        elif action == "high":
            if not L.context.is_logged_in:
                bot.send_message(call.message.chat.id, "⚠️ Need Login."); return
            bot.answer_callback_query(call.id, "Checking Highlights...")
            hls = hl_manager.get_highlights_list(L, user)
            if not hls: bot.send_message(call.message.chat.id, "No highlights."); return
            kb = InlineKeyboardMarkup()
            for h in hls: kb.add(InlineKeyboardButton(h['title'], callback_data=f"hldl:{user}:{h['id']}:0"))
            bot.send_message(call.message.chat.id, "Select:", reply_markup=kb)

        elif action == "hldl":
            hid = int(data[2])
            off = int(data[3])
            bot.answer_callback_query(call.id, "Downloading...")
            items, more = hl_manager.get_highlight_items_page(L, user, hid, off)
            for i in items: 
                try: L.download_storyitem(i, target=path)
                except: pass
            send_files(call.message.chat.id, path)
            shutil.rmtree(path, ignore_errors=True)
            if more:
                kb = InlineKeyboardMarkup()
                kb.add(InlineKeyboardButton("More", callback_data=f"hldl:{user}:{hid}:{off+10}"))
                bot.send_message(call.message.chat.id, "Continue?", reply_markup=kb)

        elif action == "net":
             bot.send_message(call.message.chat.id, "Network analysis...")

    except Exception as e:
        print(e)
        try: shutil.rmtree(path)
        except: pass

print("🚀 Oracle Bot Started...")
bot.infinity_polling()

