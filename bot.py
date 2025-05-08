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
MAX_RETRIES = 5
CHUNK_SIZE = 8 * 1024 * 1024  # 8MB chunks for better performance
PROGRESS_UPDATE_INTERVAL = 3  # Update progress every 3 seconds
DOWNLOAD_TIMEOUT = 60 * 60  # 60 minutes timeout per download attempt


async def get_filename_from_url(session, url):
    try:
        async with session.head(url, allow_redirects=True, timeout=30) as resp:
            cd = resp.headers.get("Content-Disposition")
            if cd and "filename=" in cd:
                filename = cd.split("filename=")[1].strip("\"")
                return filename
        async with session.get(url, allow_redirects=True, timeout=30) as resp:
            cd = resp.headers.get("Content-Disposition")
            if cd and "filename=" in cd:
                filename = cd.split("filename=")[1].strip("\"")
                return filename
    except:
        pass
    parsed = urlparse(url)
    return os.path.basename(parsed.path) or "file.mp4"

async def download_with_progress(url, dest_path, message, context, chat_id):
    """Improved download function with resume capability"""
    temp_download_path = f"{dest_path}.part"
    start_byte = 0
    retries = 0
    success = False
    
    # Check if partial download exists
    if os.path.exists(temp_download_path):
        start_byte = os.path.getsize(temp_download_path)
        logging.info(f"Resuming download from {start_byte} bytes")
    
    while retries < MAX_RETRIES and not success:
        try:
            timeout = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                headers = {}
                if start_byte > 0:
                    headers['Range'] = f'bytes={start_byte}-'
                
                async with session.get(url, headers=headers, allow_redirects=True) as resp:
                    if resp.status not in [200, 206]:
                        logging.error(f"Failed download, HTTP status: {resp.status}")
                        await asyncio.sleep(2 * retries)  # Exponential backoff
                        retries += 1
                        continue
                    
                    # Get file size
                    file_size = int(resp.headers.get('content-length', 0))
                    if start_byte > 0 and resp.status == 206:
                        total = start_byte + file_size
                    else:
                        total = file_size
                        start_byte = 0  # Reset if we couldn't resume
                    
                    if total == 0:
                        logging.warning("Content length is 0, using streaming mode")
                    
                    downloaded = start_byte
                    mode = 'ab' if start_byte > 0 else 'wb'
                    
                    start_time = time.time()
                    last_update_time = time.time()
                    last_progress_percent = 0
                    
                    with open(temp_download_path, mode) as f:
                        async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                            if not chunk:
                                continue
                            
                            f.write(chunk)
                            f.flush()
                            os.fsync(f.fileno())
                            
                            downloaded += len(chunk)
                            current_time = time.time()
                            elapsed_time = current_time - start_time
                            speed = downloaded / 1024 / 1024 / elapsed_time if elapsed_time > 0 else 0
                            
                            # Update progress at intervals or on significant progress
                            if (current_time - last_update_time >= PROGRESS_UPDATE_INTERVAL or 
                                (total > 0 and (downloaded / total) * 100 - last_progress_percent >= 5)):
                                
                                if total > 0:
                                    percent = (downloaded / total) * 100
                                    last_progress_percent = percent
                                    progress_text = f"📥 Downloading...\nProgress: {downloaded//1024//1024}MB / {total//1024//1024}MB ({percent:.2f}%)\nSpeed: {speed:.2f} MB/s"
                                else:
                                    progress_text = f"📥 Downloading...\nDownloaded: {downloaded//1024//1024}MB\nSpeed: {speed:.2f} MB/s"
                                
                                try:
                                    await context.bot.edit_message_text(
                                        chat_id=chat_id,
                                        message_id=message.message_id,
                                        text=progress_text
                                    )
                                except Exception as e:
                                    logging.warning(f"Failed to update progress: {str(e)}")
                                
                                last_update_time = current_time
                    
                    # Rename temp file to destination file when complete
                    shutil.move(temp_download_path, dest_path)
                    success = True
                    
                    return dest_path
        except asyncio.TimeoutError:
            logging.warning(f"Download timeout, attempt {retries+1}/{MAX_RETRIES}")
            if os.path.exists(temp_download_path):
                start_byte = os.path.getsize(temp_download_path)
            retries += 1
            await asyncio.sleep(2 * retries)  # Exponential backoff
            
            # Update user about the retry
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message.message_id,
                    text=f"⚠️ Download timeout. Retrying ({retries}/{MAX_RETRIES})..."
                )
            except:
                pass
        except Exception as e:
            logging.exception(f"Error during download (attempt {retries+1}/{MAX_RETRIES}): {str(e)}")
            retries += 1
            await asyncio.sleep(2 * retries)  # Exponential backoff
            
            # Update user about the retry
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message.message_id,
                    text=f"⚠️ Download error: {str(e)[:50]}... Retrying ({retries}/{MAX_RETRIES})..."
                )
            except:
                pass
    
    if not success:
        if os.path.exists(temp_download_path):
            os.remove(temp_download_path)
        raise Exception(f"Download failed after {MAX_RETRIES} attempts")
    
    return dest_path

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
            text=f"✅ Download complete: {filename}\n💾 Moving to Google Photos folder..."
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
        msg = await update.message.reply_text("⚡ Force-stopping Google Photos (ROOT)...")
        
        # Root-based killing commands (su -c)
        kill_commands = [
            # Method 1: Using 'am' with root (force-stop)
            "su -c 'am force-stop com.google.android.apps.photos'",
            
            # Method 2: Using 'killall' (root)
            "su -c 'killall -9 com.google.android.apps.photos'",
            
            # Method 3: Using 'pkill' (if available)
            "su -c 'pkill -9 -f com.google.android.apps.photos'",
            
            # Method 4: Manual process ID killing (robust)
            "su -c 'ps | grep com.google.android.apps.photos | grep -v grep | awk \"{print \\$2}\" | xargs kill -9'",
            
            # Clear app cache/data (optional)
            # "su -c 'pm clear com.google.android.apps.photos'",
        ]
        
        loop = asyncio.get_event_loop()
        success = False
        
        for cmd in kill_commands:
            try:
                exit_code = await loop.run_in_executor(None, lambda c=cmd: os.system(c))
                if exit_code == 0:
                    success = True
                await asyncio.sleep(1)
            except Exception as e:
                continue
        
        # Verify if stopped
        check_cmd = "su -c 'ps | grep com.google.android.apps.photos | grep -v grep'"
        result = await loop.run_in_executor(None, lambda: os.popen(check_cmd).read())
        
        if not result.strip():
            status = "✅ Google Photos **force-stopped** (root)!"
        else:
            status = "⚠️ Google Photos might still be running (check manually)"
        
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=msg.message_id,
            text=status
        )
        
    except Exception as e:
        logging.exception("Error stopping Google Photos")
        await update.message.reply_text(f"❌ Root Error: {e}")

async def handle_force_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        msg = await update.message.reply_text("⏳ Starting Google Photos...")
        
        # Start Google Photos with flags to ensure a fresh start
        loop = asyncio.get_event_loop()
        start_cmd = (
            "am start -n com.google.android.apps.photos/.home.HomeActivity " +
            "-a android.intent.action.MAIN " +
            "-c android.intent.category.LAUNCHER " +
            "--activity-clear-top"
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
