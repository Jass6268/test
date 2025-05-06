import os
import aiohttp
import asyncio
import shutil
import tempfile
import zipfile
import rarfile
import time
import logging
from urllib.parse import urlparse
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

GOOGLE_PHOTOS_FOLDER = "/sdcard/DCIM/Camera/"  # Update this if needed

async def get_filename_from_url(session, url):
    try:
        async with session.head(url, allow_redirects=True) as resp:
            cd = resp.headers.get("Content-Disposition")
            if cd and "filename=" in cd:
                filename = cd.split("filename=")[1].strip("\"")
                return filename
        async with session.get(url, allow_redirects=True) as resp:
            cd = resp.headers.get("Content-Disposition")
            if cd and "filename=" in cd:
                filename = cd.split("filename=")[1].strip("\"")
                return filename
    except:
        pass
    parsed = urlparse(url)
    return os.path.basename(parsed.path) or "file.mp4"

async def download_with_progress(url, dest_path, message, context, chat_id):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                total = int(resp.headers.get('content-length', 0))
                downloaded = 0
                chunk_size = 4 * 1024 * 1024
                start_time = time.time()
                last_update_time = time.time()
                with open(dest_path, 'wb') as f:
                    async for chunk in resp.content.iter_chunked(chunk_size):
                        f.write(chunk)
                        f.flush()
                        os.fsync(f.fileno())
                        downloaded += len(chunk)
                        current_time = time.time()
                        elapsed_time = current_time - start_time
                        speed = downloaded / 1024 / 1024 / elapsed_time
                        if current_time - last_update_time >= 5:
                            percent = (downloaded / total) * 100
                            try:
                                await context.bot.edit_message_text(
                                    chat_id=chat_id,
                                    message_id=message.message_id,
                                    text=f"📥 Downloading...\nProgress: {downloaded//1024//1024}MB / {total//1024//1024}MB ({percent:.2f}%)\nSpeed: {speed:.2f} MB/s"
                                )
                            except Exception:
                                pass
                            last_update_time = current_time
        return dest_path
    except Exception as e:
        logging.exception("Error during download:")
        raise e

async def handle_l(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if len(context.args) < 3 or "-n" not in context.args:
            await update.message.reply_text("❌ Usage: /l <url> -n <filename>")
            return

        url_index = context.args.index("-n") - 1
        url = context.args[url_index]
        name_index = context.args.index("-n") + 1
        filename = " ".join(context.args[name_index:])
        temp_file_path = os.path.join(tempfile.gettempdir(), filename)

        msg = await update.message.reply_text("⏳ Starting download...")
        final_path = await download_with_progress(url, temp_file_path, msg, context, update.effective_chat.id)

        if not os.path.exists(final_path):
            await update.message.reply_text("❌ Download failed. File not found.")
            return

        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=msg.message_id,
            text=f"✅ Download complete: {filename}"
        )

        dest_path = os.path.join(GOOGLE_PHOTOS_FOLDER, filename)
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: shutil.move(final_path, dest_path))
        await loop.run_in_executor(None, lambda: os.system(f"termux-media-scan '{dest_path}'"))

        await update.message.reply_text(f"✅ File uploaded to device: {filename}")

    except Exception as e:
        logging.exception("Error in handle_l")
        await update.message.reply_text(f"❌ Error: {e}")

async def handle_unzip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if len(context.args) < 1:
            await update.message.reply_text("❌ Usage: /unzip <url>")
            return

        url = context.args[0]
        temp_dir = tempfile.mkdtemp()
        archive_name = os.path.join(temp_dir, "archive")
        msg = await update.message.reply_text("⏳ Starting archive download...")

        downloaded_path = await download_with_progress(url, archive_name, msg, context, update.effective_chat.id)

        extract_path = os.path.join(temp_dir, "extracted")
        os.makedirs(extract_path, exist_ok=True)

        if zipfile.is_zipfile(downloaded_path):
            with zipfile.ZipFile(downloaded_path, 'r') as zip_ref:
                zip_ref.extractall(extract_path)
        elif rarfile.is_rarfile(downloaded_path):
            with rarfile.RarFile(downloaded_path, 'r') as rar_ref:
                rar_ref.extractall(extract_path)
        else:
            await update.message.reply_text("❌ Unsupported archive format.")
            return

        uploaded_files = 0
        loop = asyncio.get_event_loop()
        for root, dirs, files in os.walk(extract_path):
            for file in files:
                src = os.path.join(root, file)
                dst = os.path.join(GOOGLE_PHOTOS_FOLDER, file)
                await loop.run_in_executor(None, lambda s=src, d=dst: shutil.copy(s, d))
                await loop.run_in_executor(None, lambda d=dst: os.system(f"termux-media-scan '{d}'"))
                uploaded_files += 1
                await asyncio.sleep(1)

        shutil.rmtree(temp_dir)

        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=msg.message_id,
            text=f"✅ Extracted and uploaded {uploaded_files} file(s)."
        )

    except Exception as e:
        logging.exception("Error in handle_unzip")
        await update.message.reply_text(f"❌ Error: {e}")

async def handle_clean(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        deleted = 0
        loop = asyncio.get_event_loop()
        for file in os.listdir(GOOGLE_PHOTOS_FOLDER):
            path = os.path.join(GOOGLE_PHOTOS_FOLDER, file)
            if os.path.isfile(path):
                await loop.run_in_executor(None, lambda p=path: os.remove(p))
                deleted += 1
        await update.message.reply_text(f"🧹 Deleted {deleted} file(s) from {GOOGLE_PHOTOS_FOLDER}")
    except Exception as e:
        logging.exception("Error in handle_clean")
        await update.message.reply_text(f"❌ Error while cleaning: {e}")

async def handle_direct_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        url = update.message.text.strip()
        async with aiohttp.ClientSession() as session:
            filename = await get_filename_from_url(session, url)

        temp_file_path = os.path.join(tempfile.gettempdir(), filename)
        msg = await update.message.reply_text(f"⏳ Downloading {filename}...")
        final_path = await download_with_progress(url, temp_file_path, msg, context, update.effective_chat.id)

        dest_path = os.path.join(GOOGLE_PHOTOS_FOLDER, filename)
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: shutil.move(final_path, dest_path))
        await loop.run_in_executor(None, lambda: os.system(f"termux-media-scan '{dest_path}'"))

        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=msg.message_id,
            text=f"✅ Downloaded and saved: {filename}"
        )

    except Exception as e:
        logging.exception("Error in handle_direct_link")
        await update.message.reply_text(f"❌ Error: {e}")

async def handle_force_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        msg = await update.message.reply_text("🔄 Force stopping Google Photos...")
        
        # Kill the app process - multiple approaches for better effectiveness
        loop = asyncio.get_event_loop()
        
        # Use pkill first
        await loop.run_in_executor(None, lambda: os.system("pkill -f com.google.android.apps.photos"))
        
        # Use am kill for system-level force close
        await loop.run_in_executor(None, lambda: os.system("am kill com.google.android.apps.photos"))
        
        # Additional cleanup
        await loop.run_in_executor(None, lambda: os.system("am clear-recent-tasks"))
        
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=msg.message_id,
            text="✅ Google Photos has been stopped and removed from recents!"
        )
        
    except Exception as e:
        logging.exception("Error stopping Google Photos")
        await update.message.reply_text(f"❌ Error: {e}")

async def handle_force_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        msg = await update.message.reply_text("⏳ Starting Google Photos...")
        
        # Start Google Photos
        loop = asyncio.get_event_loop()
        start_cmd = (
            "am start -n com.google.android.apps.photos/.home.HomeActivity " +
            "-a android.intent.action.MAIN " +
            "-c android.intent.category.LAUNCHER " +
            "--activity-clear-task"
        )
        
        await loop.run_in_executor(None, lambda: os.system(start_cmd))
        
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=msg.message_id,
            text="✅ Google Photos started successfully!"
        )
        
    except Exception as e:
        logging.exception("Error starting Google Photos")
        await update.message.reply_text(f"❌ Error: {e}")


if __name__ == '__main__':
    BOT_TOKEN = "6385636650:AAGsa2aZ2mQtPFB2tk81rViOO_H_6hHFoQE"  # Replace with your actual bot token
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    async def wrapper_l(update: Update, context: ContextTypes.DEFAULT_TYPE):
        asyncio.create_task(handle_l(update, context))

    async def wrapper_unzip(update: Update, context: ContextTypes.DEFAULT_TYPE):
        asyncio.create_task(handle_unzip(update, context))

    async def wrapper_clean(update: Update, context: ContextTypes.DEFAULT_TYPE):
        asyncio.create_task(handle_clean(update, context))

    async def wrapper_direct(update: Update, context: ContextTypes.DEFAULT_TYPE):
        asyncio.create_task(handle_direct_link(update, context))

    async def wrapper_force_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
        asyncio.create_task(handle_force_stop(update, context))

    async def wrapper_force_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        asyncio.create_task(handle_force_start(update, context))

    app.add_handler(CommandHandler("l", wrapper_l))
    app.add_handler(CommandHandler("unzip", wrapper_unzip))
    app.add_handler(CommandHandler("clean", wrapper_clean))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, wrapper_direct))
    app.add_handler(CommandHandler("force_stop", wrapper_force_stop))
    app.add_handler(CommandHandler("force_start", wrapper_force_start))

    print("🤖 Bot running...")
    app.run_polling()
