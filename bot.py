import os
import aiohttp
import asyncio
import shutil
import tempfile
import zipfile
import rarfile
import time
import logging
import re
from datetime import datetime
import pytz  # You'll need to install this: pip install pytz
from urllib.parse import urlparse, unquote
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

GOOGLE_PHOTOS_FOLDER = "/sdcard/DCIM/Camera/"  # Update this if needed

# Dictionary to track active downloads
active_downloads = {}

# IST timezone
IST = pytz.timezone('Asia/Kolkata')

def set_file_timestamp_to_ist(file_path):
    """
    Set file's creation and modification time to current IST time.
    This ensures Google Photos shows the correct upload date.
    """
    try:
        # Get current IST time as Unix timestamp
        ist_now = datetime.now(IST)
        unix_timestamp = ist_now.timestamp()
        
        # Set both access time and modification time to current IST
        os.utime(file_path, (unix_timestamp, unix_timestamp))
        
        # For Android, also try to set using touch command for better compatibility
        ist_formatted = ist_now.strftime("%Y%m%d%H%M.%S")
        touch_command = f"touch -t {ist_formatted} '{file_path}'"
        os.system(touch_command)
        
        logging.info(f"Set timestamp for {file_path} to {ist_now.strftime('%Y-%m-%d %H:%M:%S IST')}")
        return True
    except Exception as e:
        logging.error(f"Failed to set timestamp for {file_path}: {e}")
        return False

def clean_filename(filename):
    """
    Clean up filenames by replacing dots with spaces, except for the file extension.
    Example: thor.2011.full.movie.mkv -> thor 2011 full movie.mkv
    """
    # Get file extension
    name, ext = os.path.splitext(filename)
    
    # Replace dots with spaces in the name part only
    cleaned_name = name.replace('.', ' ')
    
    # Rejoin with the extension
    return cleaned_name + ext

async def get_filename_from_url(session, url):
    try:
        # Try to get filename from Content-Disposition header
        async with session.head(url, allow_redirects=True) as resp:
            cd = resp.headers.get("Content-Disposition")
            if cd and "filename=" in cd:
                filename = cd.split("filename=")[1].strip("\"")
                return clean_filename(unquote(filename))
                
        async with session.get(url, allow_redirects=True) as resp:
            cd = resp.headers.get("Content-Disposition")
            if cd and "filename=" in cd:
                filename = cd.split("filename=")[1].strip("\"")
                return clean_filename(unquote(filename))
    except:
        pass
    
    # If headers don't work, try to extract from URL
    parsed = urlparse(url)
    url_filename = os.path.basename(parsed.path)
    url_filename = unquote(url_filename)  # URL decode the filename
    
    if url_filename:
        return clean_filename(url_filename)
    else:
        return "file.mp4"  # Default filename

async def download_with_progress(url, dest_path, message, context, chat_id):
    try:
        # Create a unique download ID for tracking
        download_id = f"{chat_id}_{message.message_id}"
        
        # Add to active downloads dictionary
        active_downloads[download_id] = {
            "task": asyncio.current_task(),
            "dest_path": dest_path,
            "cancelled": False
        }
        
        # Create cancel button
        cancel_button = InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel Download", callback_data=f"cancel_{download_id}")]
        ])
        
        # Update message with cancel button
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message.message_id,
            text="⏳ Starting download...",
            reply_markup=cancel_button
        )
        
        # Optimized connection settings for high-speed transfers
        conn = aiohttp.TCPConnector(limit=10, force_close=False, enable_cleanup_closed=True)
        async with aiohttp.ClientSession(
            connector=conn, 
            timeout=aiohttp.ClientTimeout(total=None)
        ) as session:
            async with session.get(url) as resp:
                total = int(resp.headers.get('content-length', 0))
                downloaded = 0
                # Larger chunk size for high-speed connection (8MB)
                chunk_size = 8 * 1024 * 1024
                start_time = time.time()
                last_update_time = time.time()
                last_activity_time = time.time()
                
                with open(dest_path, 'wb') as f:
                    async for chunk in resp.content.iter_chunked(chunk_size):
                        # Check if download was cancelled
                        if active_downloads[download_id]["cancelled"]:
                            # Close file and delete it
                            f.close()
                            if os.path.exists(dest_path):
                                os.remove(dest_path)
                            raise asyncio.CancelledError("Download cancelled by user")
                            
                        f.write(chunk)
                        # Only flush periodically to improve write performance
                        if downloaded % (32 * 1024 * 1024) == 0:  # Every 32MB
                            f.flush()
                            os.fsync(f.fileno())
                        
                        downloaded += len(chunk)
                        current_time = time.time()
                        last_activity_time = current_time
                        
                        # Update progress every 5 seconds
                        if current_time - last_update_time >= 5:
                            elapsed_time = current_time - start_time
                            speed = downloaded / 1024 / 1024 / elapsed_time if elapsed_time > 0 else 0
                            percent = (downloaded / total) * 100 if total > 0 else 0
                            
                            try:
                                await context.bot.edit_message_text(
                                    chat_id=chat_id,
                                    message_id=message.message_id,
                                    text=f"📥 Downloading at high speed...\nProgress: {downloaded//1024//1024}MB / {total//1024//1024}MB ({percent:.2f}%)\nSpeed: {speed:.2f} MB/s",
                                    reply_markup=cancel_button
                                )
                            except Exception as e:
                                logging.warning(f"Could not update message: {e}")
                            
                            last_update_time = current_time
                        
                        # Keep-alive messages every 30 seconds if no data received
                        if current_time - last_activity_time > 30:
                            try:
                                await context.bot.edit_message_text(
                                    chat_id=chat_id,
                                    message_id=message.message_id,
                                    text=f"📥 High-speed download in progress...\nStill working... Please wait.",
                                    reply_markup=cancel_button
                                )
                            except Exception:
                                pass
                            last_activity_time = current_time
                
                    # Final flush to ensure all data is written
                    # Do it INSIDE the 'with' block before the file is closed
                    f.flush()
                    os.fsync(f.fileno())
                
        # Remove from active downloads
        if download_id in active_downloads:
            del active_downloads[download_id]
            
        return dest_path
    except asyncio.CancelledError as e:
        # Handle cancellation
        if os.path.exists(dest_path):
            os.remove(dest_path)
        # Clean up active downloads record
        if download_id in active_downloads:
            del active_downloads[download_id]
        raise
    except asyncio.TimeoutError:
        logging.error("Download timed out")
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message.message_id,
            text="⚠️ Download timed out. Please try again with a different source."
        )
        # Clean up active downloads record
        if download_id in active_downloads:
            del active_downloads[download_id]
        raise
    except aiohttp.ClientError as e:
        logging.exception(f"Connection error during download: {e}")
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message.message_id,
            text=f"⚠️ Connection error: {e}"
        )
        # Clean up active downloads record
        if download_id in active_downloads:
            del active_downloads[download_id]
        raise
    except Exception as e:
        logging.exception(f"Error during download: {e}")
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message.message_id,
                text=f"❌ Download error: {e}"
            )
        except:
            pass
        # Clean up active downloads record
        if download_id in active_downloads:
            del active_downloads[download_id]
        raise e

async def handle_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle cancel button clicks"""
    query = update.callback_query
    await query.answer()
    
    callback_data = query.data
    if callback_data.startswith("cancel_"):
        download_id = callback_data[7:]  # Remove "cancel_" prefix
        
        if download_id in active_downloads:
            active_downloads[download_id]["cancelled"] = True
            
            # Try to cancel the download task
            task = active_downloads[download_id]["task"]
            if task and not task.done():
                task.cancel()
                
            # Delete the partial file
            dest_path = active_downloads[download_id]["dest_path"]
            if os.path.exists(dest_path):
                try:
                    os.remove(dest_path)
                except Exception as e:
                    logging.error(f"Error deleting partial file: {e}")
            
            # Update message
            await query.edit_message_text(
                text="✅ Download cancelled successfully!"
            )
            
            # Clean up active downloads record
            del active_downloads[download_id]
        else:
            # Download no longer active
            await query.edit_message_text(
                text="This download is no longer active."
            )

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
        
        try:
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
            
            # Set file timestamp to current IST time
            await loop.run_in_executor(None, lambda: set_file_timestamp_to_ist(dest_path))
            
            await loop.run_in_executor(None, lambda: os.system(f"termux-media-scan '{dest_path}'"))

            current_ist = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
            await update.message.reply_text(f"✅ File uploaded with IST timestamp: {filename}\n🕒 {current_ist}")
        except asyncio.CancelledError:
            # Download was cancelled, do nothing as it's already handled
            pass

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

        try:
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
                shutil.rmtree(temp_dir)
                return

            uploaded_files = 0
            loop = asyncio.get_event_loop()
            for root, dirs, files in os.walk(extract_path):
                for file in files:
                    src = os.path.join(root, file)
                    # Clean the filename when extracting
                    cleaned_file = clean_filename(file)
                    dst = os.path.join(GOOGLE_PHOTOS_FOLDER, cleaned_file)
                    await loop.run_in_executor(None, lambda s=src, d=dst: shutil.copy(s, d))
                    
                    # Set file timestamp to current IST time for each extracted file
                    await loop.run_in_executor(None, lambda d=dst: set_file_timestamp_to_ist(d))
                    
                    await loop.run_in_executor(None, lambda d=dst: os.system(f"termux-media-scan '{d}'"))
                    uploaded_files += 1
                    await asyncio.sleep(1)

            shutil.rmtree(temp_dir)

            current_ist = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=msg.message_id,
                text=f"✅ Extracted and uploaded {uploaded_files} file(s) with IST timestamps.\n🕒 {current_ist}"
            )
        except asyncio.CancelledError:
            # Download was cancelled
            shutil.rmtree(temp_dir)
            # Do nothing else as it's already handled

    except Exception as e:
        logging.exception("Error in handle_unzip")
        # Clean up temp directory if it exists
        try:
            if 'temp_dir' in locals():
                shutil.rmtree(temp_dir)
        except:
            pass
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
        
        current_ist = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
        await update.message.reply_text(f"🧹 Deleted {deleted} file(s) from {GOOGLE_PHOTOS_FOLDER}\n🕒 {current_ist}")
    except Exception as e:
        logging.exception("Error in handle_clean")
        await update.message.reply_text(f"❌ Error while cleaning: {e}")

async def handle_direct_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        url = update.message.text.strip()
        
        # Validate URL
        if not url.startswith(('http://', 'https://')):
            await update.message.reply_text("❌ Please provide a valid URL starting with http:// or https://")
            return
            
        async with aiohttp.ClientSession() as session:
            filename = await get_filename_from_url(session, url)

        temp_file_path = os.path.join(tempfile.gettempdir(), filename)
        msg = await update.message.reply_text(f"⏳ Downloading {filename}...")
        
        try:
            final_path = await download_with_progress(url, temp_file_path, msg, context, update.effective_chat.id)

            dest_path = os.path.join(GOOGLE_PHOTOS_FOLDER, filename)
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: shutil.move(final_path, dest_path))
            
            # Set file timestamp to current IST time
            await loop.run_in_executor(None, lambda: set_file_timestamp_to_ist(dest_path))
            
            await loop.run_in_executor(None, lambda: os.system(f"termux-media-scan '{dest_path}'"))

            current_ist = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=msg.message_id,
                text=f"✅ Downloaded with IST timestamp: {filename}\n🕒 {current_ist}"
            )
        except asyncio.CancelledError:
            # Download was cancelled, do nothing as it's already handled
            pass

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
        msg = await update.message.reply_text("⚡ Root-forcing Google Photos open...")
        
        # Pure root launch commands (no stopping)
        launch_commands = [
            # 1. Direct root launch with clear-top
            "su -c 'am start -n com.google.android.apps.photos/.home.HomeActivity --activity-clear-top'",
            
            # 2. Alternative root launch with MAIN action
            "su -c 'am start -a android.intent.action.MAIN -c android.intent.category.LAUNCHER -n com.google.android.apps.photos/.home.HomeActivity'",
            
            # 3. Deep intent launch (opens directly to gallery)
            "su -c 'am start -a android.intent.action.VIEW -d content://media/external/images/media com.google.android.apps.photos'"
        ]
        
        loop = asyncio.get_event_loop()
        
        # Execute all launch commands sequentially (no stopping logic)
        for cmd in launch_commands:
            try:
                await loop.run_in_executor(None, lambda c=cmd: os.system(c))
                await asyncio.sleep(1)  # Brief delay between attempts
            except:
                continue
        
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=msg.message_id,
            text="✅ Google Photos **forced open** with root!",
            parse_mode='Markdown'
        )
        
    except Exception as e:
        await update.message.reply_text(f"❌ Root launch failed: {str(e)}")

# New command to fix timestamps of existing files
async def handle_fix_timestamps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        msg = await update.message.reply_text("🔧 Fixing timestamps of existing files...")
        
        fixed_count = 0
        loop = asyncio.get_event_loop()
        
        for file in os.listdir(GOOGLE_PHOTOS_FOLDER):
            file_path = os.path.join(GOOGLE_PHOTOS_FOLDER, file)
            if os.path.isfile(file_path):
                success = await loop.run_in_executor(None, lambda p=file_path: set_file_timestamp_to_ist(p))
                if success:
                    fixed_count += 1
                await asyncio.sleep(0.1)  # Small delay to prevent overwhelming the system
        
        # Trigger media scan for all files
        await loop.run_in_executor(None, lambda: os.system(f"termux-media-scan '{GOOGLE_PHOTOS_FOLDER}'"))
        
        current_ist = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=msg.message_id,
            text=f"✅ Fixed timestamps for {fixed_count} files\n🕒 {current_ist}\n📱 Triggering Google Photos sync..."
        )
    except Exception as e:
        logging.exception("Error fixing timestamps")
        await update.message.reply_text(f"❌ Error fixing timestamps: {e}")

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

    async def wrapper_fix_timestamps(update: Update, context: ContextTypes.DEFAULT_TYPE):
        asyncio.create_task(handle_fix_timestamps(update, context))

    app.add_handler(CommandHandler("l", wrapper_l))
    app.add_handler(CommandHandler("unzip", wrapper_unzip))
    app.add_handler(CommandHandler("clean", wrapper_clean))
    app.add_handler(CommandHandler("fix_timestamps", wrapper_fix_timestamps))  # New command
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, wrapper_direct))
    app.add_handler(CommandHandler("force_stop", wrapper_force_stop))
    app.add_handler(CommandHandler("force_start", wrapper_force_start))
    
    # Add callback handler for cancel button
    app.add_handler(CallbackQueryHandler(handle_cancel_callback))

    print("🤖 Bot running with IST file timestamps...")
    app.run_polling()
