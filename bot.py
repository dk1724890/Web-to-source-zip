import os
import re
import time
import sqlite3
import shutil
import zipfile
import asyncio
import datetime
import aiohttp
import aiofiles
import random
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, 
    CommandHandler, 
    ContextTypes, 
    MessageHandler, 
    filters, 
    CallbackQueryHandler
)
from telegram.error import BadRequest

# --- 🛠 CONFIGURATION ---
TOKEN = "8586116847:AAGenQHPzShiYQvVKxUiXxniEI9jzMWrobY" 

REQUIRED_CHANNELS_DATA = {
    "@cineflixdk": "Join My Channel",
    "@html_leaker": "Join My Group"
}

ADMIN_ID = 754309254  # <--- Aapki ID set kar di gayi hai
ADMIN_USERNAME = "@DINESH_OWNER" 
DAILY_LIMIT = 2 
DB_FILE = "users_v3.db"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
]

MAX_DEPTH = 2
MAX_PAGES = 15 
CONCURRENT_REQUESTS = 10

# --- 🗄️ DATABASE MANAGER ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (user_id TEXT PRIMARY KEY, count INTEGER, last_date TEXT, 
                  premium INTEGER, referred_by TEXT, referrals_count INTEGER)''')
    conn.commit()
    conn.close()

def get_user(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id=?", (str(user_id),))
    user = c.fetchone()
    conn.close()
    if user:
        return {"id": user[0], "count": user[1], "date": user[2], "premium": bool(user[3]), "referred_by": user[4], "referrals": user[5]}
    return None

def set_premium(user_id, status=1):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE users SET premium=? WHERE user_id=?", (status, str(user_id)))
    conn.commit()
    conn.close()

def check_user_status(user_id):
    user_id_str = str(user_id)
    today = str(datetime.date.today())
    
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    user_data = get_user(user_id_str)
    
    if not user_data:
        c.execute("INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)", (user_id_str, 0, today, 0, None, 0))
        conn.commit()
        user_data = get_user(user_id_str)
    
    if user_data["date"] != today:
        c.execute("UPDATE users SET count=0, last_date=? WHERE user_id=?", (today, user_id_str))
        conn.commit()
        user_data["count"] = 0
    conn.close()

    # ADMIN AUR PREMIUM CHECK
    if int(user_id) == ADMIN_ID or user_data["premium"]: 
        return True, "Unlimited", user_data
    
    left = DAILY_LIMIT - user_data["count"]
    return (left > 0, left, user_data)

def increment_usage(user_id):
    if int(user_id) == ADMIN_ID: return # Admin count nahi badhega
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE users SET count = count + 1 WHERE user_id=?", (str(user_id),))
    conn.commit()
    conn.close()

# --- 🚀 SCRAPER CLASS ---
class SuperScraper:
    def __init__(self, url, update_func):
        self.url = url
        self.domain = urlparse(url).netloc
        self.base_folder = f"project_{int(time.time())}"
        self.visited_urls = set()
        self.downloaded_assets = {}
        self.stats = {"pages": 0, "files": 0}
        self.semaphore = asyncio.Semaphore(CONCURRENT_REQUESTS)
        self.update_func = update_func
        self.last_percent = 0
        os.makedirs(self.base_folder, exist_ok=True)

    async def update_progress(self):
        percent = int((len(self.visited_urls) / MAX_PAGES) * 100)
        if percent > 100: percent = 100
        if percent < 1: percent = 1
        
        if percent > self.last_percent:
            self.last_percent = percent
            bar_size = 10
            filled = int(bar_size * percent / 100)
            bar = "▓" * filled + "░" * (bar_size - filled)
            msg = (f"🚀 **Scraping Website...**\n\n"
                   f"📈 Progress: `{percent}%`\n"
                   f"`[{bar}]`\n\n"
                   f"📄 Pages: {self.stats['pages']}\n"
                   f"📂 Files: {self.stats['files']}")
            try: await self.update_func(msg)
            except: pass

    async def fetch(self, session, url):
        async with self.semaphore:
            try:
                headers = {'User-Agent': random.choice(USER_AGENTS)}
                async with session.get(url, timeout=15, headers=headers) as response:
                    if response.status == 200:
                        return await response.read(), response.headers.get('Content-Type', '')
            except: return None, None
        return None, None

    async def process_resource(self, session, url):
        if url in self.downloaded_assets: return self.downloaded_assets[url]
        content, ctype = await self.fetch(session, url)
        if content:
            parsed = urlparse(url)
            save_rel_path = parsed.path.lstrip('/') or "asset_" + str(random.randint(100,999))
            full_path = os.path.join(self.base_folder, save_rel_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            async with aiofiles.open(full_path, mode='wb') as f:
                await f.write(content)
            self.downloaded_assets[url] = save_rel_path
            self.stats["files"] += 1
            return save_rel_path
        return url

    async def scrape(self, session, url, depth):
        if url in self.visited_urls or depth > MAX_DEPTH or len(self.visited_urls) >= MAX_PAGES: return
        if urlparse(url).netloc != self.domain: return
        
        self.visited_urls.add(url)
        await self.update_progress()
        
        content, ctype = await self.fetch(session, url)
        if not content or "text/html" not in ctype: return

        self.stats["pages"] += 1
        soup = BeautifulSoup(content, 'html.parser')
        
        tags_attrs = {'img': 'src', 'link': 'href', 'script': 'src'}
        tasks = []
        for tag, attr in tags_attrs.items():
            for element in soup.find_all(tag, **{attr: True}):
                full_url = urljoin(url, element[attr])
                tasks.append(self.process_resource(session, full_url))
        await asyncio.gather(*tasks)

        save_path = os.path.join(self.base_folder, "index.html" if depth==0 else os.path.basename(urlparse(url).path) or "page.html")
        async with aiofiles.open(save_path, "w", encoding="utf-8", errors="ignore") as f:
            await f.write(soup.prettify())

        for a in soup.find_all('a', href=True):
            await self.scrape(session, urljoin(url, a['href']), depth + 1)

    async def run(self):
        async with aiohttp.ClientSession() as session:
            await self.scrape(session, self.url, 0)
        
        await self.update_func("✅ **Processing Complete! 100%**\nCreating ZIP file...")
        
        zip_name = f"{self.domain.replace('.','_')}.zip"
        def make_zip():
            with zipfile.ZipFile(zip_name, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, _, files in os.walk(self.base_folder):
                    for file in files:
                        p = os.path.join(root, file)
                        zipf.write(p, os.path.relpath(p, self.base_folder))
            shutil.rmtree(self.base_folder)
            return zip_name
        return await asyncio.to_thread(make_zip)

# --- 🔐 FORCE JOIN SYSTEM ---
async def check_membership(user_id, bot):
    if user_id == ADMIN_ID: return True # Admin ko join karne ki zarurat nahi
    for channel in REQUIRED_CHANNELS_DATA.keys():
        try:
            member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status in ['left', 'kicked', 'banned']: return False
        except: return False
    return True

async def get_join_markup():
    buttons = [[InlineKeyboardButton(text=name, url=f"https://t.me/{user.replace('@','')}")] 
               for user, name in REQUIRED_CHANNELS_DATA.items()]
    buttons.append([InlineKeyboardButton(text="🔄 Verify Membership", callback_data="verify")])
    return InlineKeyboardMarkup(buttons)

# --- 🤖 HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await check_membership(user_id, context.bot):
        await update.message.reply_text(
            "🛑 **Access Locked!**\n\nPlease join our channels to unlock the bot's features.",
            reply_markup=await get_join_markup(), parse_mode='HTML'
        )
        return

    await update.message.reply_text(
        f"👋 **Welcome {update.effective_user.first_name}!**\n\n"
        "Send me any website URL to start extraction.\n"
        "Use /status to check your limits.", parse_mode='HTML'
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    url = update.message.text.strip()
    
    # Broadcast Handle
    if context.user_data.get('state') == 'BROADCASTING' and user_id == ADMIN_ID:
        context.user_data['state'] = None
        conn = sqlite3.connect(DB_FILE)
        users = conn.execute("SELECT user_id FROM users").fetchall()
        conn.close()
        
        msg = await update.message.reply_text("📣 Sending broadcast...")
        success = 0
        for (uid,) in users:
            try:
                await context.bot.send_message(chat_id=uid, text=url)
                success += 1
            except: pass
        await msg.edit_text(f"✅ Broadcast Sent to {success} users.")
        return

    if not await check_membership(user_id, context.bot):
        await update.message.reply_text("❌ Join channels first!", reply_markup=await get_join_markup())
        return

    if not url.startswith('http'): return

    allowed, left, _ = check_user_status(user_id)
    if not allowed:
        await update.message.reply_text("⚠️ Daily Limit Reached! Buy Premium.")
        return

    status_msg = await update.message.reply_text("🔍 **Analyzing URL...**", parse_mode='HTML')

    async def update_status_text(text):
        try: await status_msg.edit_text(text, parse_mode='HTML')
        except: pass

    try:
        scraper = SuperScraper(url, update_status_text)
        zip_path = await scraper.run()
        increment_usage(user_id)
        
        await update.message.reply_document(
            document=open(zip_path, 'rb'), 
            caption=f"✅ **Extraction Done!**\n🌐 `{scraper.domain}`",
            parse_mode='HTML'
        )
        os.remove(zip_path)
        await status_msg.delete()
    except Exception as e:
        await status_msg.edit_text(f"❌ Error: {str(e)}")

# --- 👑 ADMIN COMMANDS ---
async def add_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        user_id = context.args[0]
        set_premium(user_id, 1)
        await update.message.reply_text(f"✅ User `{user_id}` is now a Premium Member!", parse_mode='Markdown')
    except:
        await update.message.reply_text("Usage: `/addpremium 12345678`", parse_mode='Markdown')

async def remove_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        user_id = context.args[0]
        set_premium(user_id, 0)
        await update.message.reply_text(f"❌ User `{user_id}` premium removed.")
    except:
        await update.message.reply_text("Usage: `/removepremium 12345678`", parse_mode='Markdown')

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    keyboard = [
        [InlineKeyboardButton("📊 Bot Stats", callback_data="admin_stats")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")]
    ]
    await update.message.reply_text("👑 **Admin Panel**", reply_markup=InlineKeyboardMarkup(keyboard))

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _, left, d = check_user_status(update.effective_user.id)
    plan = "👑 Admin/Unlimited" if update.effective_user.id == ADMIN_ID else ('👑 Premium' if d['premium'] else '🆓 Free')
    text = (f"👤 **Your Profile**\n\nPlan: {plan}\n"
            f"Daily Credits: {left}")
    await update.message.reply_text(text, parse_mode='HTML')

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

    if query.data == "verify":
        if await check_membership(user_id, context.bot):
            await query.answer("✅ Verified!", show_alert=True)
            await query.message.delete()
            await start(update, context)
        else:
            await query.answer("❌ You haven't joined yet!", show_alert=True)
            
    elif query.data == "admin_stats" and user_id == ADMIN_ID:
        conn = sqlite3.connect(DB_FILE)
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        conn.close()
        await query.message.edit_text(f"📊 **Total Users:** {count}")
        
    elif query.data == "admin_broadcast" and user_id == ADMIN_ID:
        context.user_data['state'] = 'BROADCASTING'
        await query.message.edit_text("💬 Send me the message to broadcast to all users.")

# --- 🚀 MAIN ---
def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("login", admin_panel))
    app.add_handler(CommandHandler("addpremium", add_premium))
    app.add_handler(CommandHandler("removepremium", remove_premium))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("Bot is live! Admin is active. 🔥")
    app.run_polling()

if __name__ == "__main__":
    main()
