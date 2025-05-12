import os
import aiohttp
import asyncio
import shutil
import tempfile
import zipfile
import rarfile
import time
import logging
import subprocess
from urllib.parse import urlparse
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

GOOGLE_PHOTOS_FOLDER = "/sdcard/DCIM/Camera/"  # Update this if needed
REMUX_TIMEOUT = 1800  # 30 minutes

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

# Add this function for remuxing MKV files with metadata
async def remux_with_metadata(input_path, author_tag="TG-@MoralMovies"):
    """Remux an MKV file with metadata using mkvpropedit"""
    try:
        logging.info(f"Starting remux for: {input_path}")
        
        # Create output path with same directory but new filename
        output_dir = os.path.dirname(input_path)
        file_name = os.path.basename(input_path)
        name, ext = os.path.splitext(file_name)
        output_path = os.path.join(output_dir, f"{name}_remuxed{ext}")
        
        # Copy the original file to the output path
        shutil.copy2(input_path, output_path)
        
        # Extract title from filename (remove extension)
        title = os.path.splitext(file_name)[0]
        
        # Set metadata using mkvpropedit
        cmd = [
            'mkvpropedit', output_path,
            '--edit', 'info',
            '--set', f'title={title}'
        ]
        
        # Add author tag if provided
        if author_tag:
            cmd.extend(['--set', f'director={author_tag}'])
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            logging.error(f"Remux failed: {stderr.decode()}")
            return None
        
        logging.info(f"Successfully remuxed: {input_path} to {output_path}")
        return output_path
    except Exception as e:
        logging.exception(f"Error in remux_with_metadata: {e}")
        return None

async def download_with_progress(url, dest_path, message, context, chat_id, remux=True, author_tag="TG-@MoralMovies"):
    """Download with progress and auto-remux MKV files"""
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
        
        # Auto-remux if it's an MKV file
        if remux and dest_path.lower().endswith('.mkv'):
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message.message_id,
                text="🔧 Remuxing MKV file with metadata..."
            )
            
            try:
                # Set a timeout for the remuxing process
                remux_task = asyncio.create_task(remux_with_metadata(dest_path, author_tag))
                remuxed_path = await asyncio.wait_for(remux_task, timeout=REMUX_TIMEOUT)
                
                if remuxed_path and os.path.exists(remuxed_path):
                    # Use the remuxed file
                    return remuxed_path
                else:
                    # Continue with original file if remux failed
                    logging.warning(f"Remux failed for {dest_path}, using original file")
                    return dest_path
            except asyncio.TimeoutError:
                logging.error("Remux timed out")
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message.message_id,
                    text="⚠️ Remuxing timed out, continuing with original file."
                )
                return dest_path
            except Exception as e:
                logging.error(f"Error during remux: {e}")
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message.message_id,
                    text=f"⚠️ Remuxing failed, continuing with original file: {str(e)}"
                )
                return dest_path
                
        return dest_path
    except Exception as e:
        logging.exception("Error during download:")
        raise e

async def handle_l(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /l command - download file with auto-remux for MKV"""
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
        # Auto-remux is enabled by default now
        final_path = await download_with_progress(url, temp_file_path, msg, context, update.effective_chat.id)

        if not os.path.exists(final_path):
            await update.message.reply_text("❌ Download failed. File not found.")
            return

        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=msg.message_id,
            text=f"✅ Download complete: {os.path.basename(final_path)}"
        )

        dest_path = os.path.join(GOOGLE_PHOTOS_FOLDER, os.path.basename(final_path))
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: shutil.move(final_path, dest_path))
        await loop.run_in_executor(None, lambda: os.system(f"termux-media-scan '{dest_path}'"))

        await update.message.reply_text(f"✅ File uploaded to device: {os.path.basename(dest_path)}")

    except Exception as e:
        logging.exception("Error in handle_l")
        await update.message.reply_text(f"❌ Error: {e}")

async def handle_unzip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /unzip command with auto-remux for MKV files"""
    try:
        if len(context.args) < 1:
            await update.message.reply_text("❌ Usage: /unzip <url>")
            return

        url = context.args[0]
        temp_dir = tempfile.mkdtemp()
        archive_name = os.path.join(temp_dir, "archive")
        msg = await update.message.reply_text("⏳ Starting archive download...")

        downloaded_path = await download_with_progress(url, archive_name, msg, context, update.effective_chat.id, remux=False)

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

        # First, find and remux all MKV files
        mkv_files = []
        for root, dirs, files in os.walk(extract_path):
            for file in files:
                if file.lower().endswith('.mkv'):
                    mkv_files.append(os.path.join(root, file))
        
        remuxed_files = 0
        remuxed_paths = {}  # Map original paths to remuxed paths
        
        if mkv_files:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=msg.message_id,
                text=f"🔧 Remuxing {len(mkv_files)} MKV file(s)..."
            )
            
            for mkv_file in mkv_files:
                try:
                    remux_task = asyncio.create_task(remux_with_metadata(mkv_file))
                    remuxed_path = await asyncio.wait_for(remux_task, timeout=REMUX_TIMEOUT)
                    if remuxed_path and os.path.exists(remuxed_path):
                        remuxed_paths[mkv_file] = remuxed_path
                        remuxed_files += 1
                except Exception as e:
                    logging.error(f"Failed to remux {mkv_file}: {str(e)}")
        
        uploaded_files = 0
        loop = asyncio.get_event_loop()
        
        # Now upload all files (including remuxed ones)
        for root, dirs, files in os.walk(extract_path):
            for file in files:
                src = os.path.join(root, file)
                # Skip original files that were remuxed
                if src in remuxed_paths:
                    continue
                    
                dst = os.path.join(GOOGLE_PHOTOS_FOLDER, file)
                await loop.run_in_executor(None, lambda s=src, d=dst: shutil.copy(s, d))
                await loop.run_in_executor(None, lambda d=dst: os.system(f"termux-media-scan '{d}'"))
                uploaded_files += 1
        
        # Upload remuxed files
        for original, remuxed in remuxed_paths.items():
            remuxed_file = os.path.basename(remuxed)
            dst = os.path.join(GOOGLE_PHOTOS_FOLDER, remuxed_file)
            await loop.run_in_executor(None, lambda s=remuxed, d=dst: shutil.copy(s, d))
            await loop.run_in_executor(None, lambda d=dst: os.system(f"termux-media-scan '{d}'"))
            uploaded_files += 1

        shutil.rmtree(temp_dir)

        status_text = f"✅ Extracted and uploaded {uploaded_files} file(s)."
        if remuxed_files > 0:
            status_text += f"\n🔧 Remuxed {remuxed_files} MKV file(s) with metadata."
            
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=msg.message_id,
            text=status_text
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
    """Handle direct links with auto-remux for MKV files"""
    try:
        url = update.message.text.strip()
        async with aiohttp.ClientSession() as session:
            filename = await get_filename_from_url(session, url)

        temp_file_path = os.path.join(tempfile.gettempdir(), filename)
        msg = await update.message.reply_text(f"⏳ Downloading {filename}...")
        # Auto-remux is enabled by default now
        final_path = await download_with_progress(url, temp_file_path, msg, context, update.effective_chat.id)

        if not os.path.exists(final_path):
            await update.message.reply_text("❌ Download failed. File not found.")
            return

        dest_path = os.path.join(GOOGLE_PHOTOS_FOLDER, os.path.basename(final_path))
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: shutil.move(final_path, dest_path))
        await loop.run_in_executor(None, lambda: os.system(f"termux-media-scan '{dest_path}'"))

        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=msg.message_id,
            text=f"✅ Downloaded and saved: {os.path.basename(dest_path)}"
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

# Keep the handle_lm function for backward compatibility
async def handle_lm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Legacy handler for /lm command (same as /l now)"""
    try:
        if len(context.args) < 3 or "-n" not in context.args:
            await update.message.reply_text("❌ Usage: /lm <url> -n <filename> [-a <author_tag>]")
            return

        url_index = context.args.index("-n") - 1
        url = context.args[url_index]
        name_index = context.args.index("-n") + 1
        
        # Check if author tag is provided
        author_tag = "TG-@MoralMovies"  # Default value
        if "-a" in context.args:
            try:
                author_index = context.args.index("-a") + 1
                author_tag = context.args[author_index]
                # Get filename by excluding the author part
                filename = " ".join(context.args[name_index:context.args.index("-a")])
            except (ValueError, IndexError):
                filename = " ".join(context.args[name_index:])
        else:
            filename = " ".join(context.args[name_index:])
        
        temp_file_path = os.path.join(tempfile.gettempdir(), filename)

        msg = await update.message.reply_text("⏳ Starting download with metadata remuxing...")
        final_path = await download_with_progress(url, temp_file_path, msg, context, update.effective_chat.id, remux=True, author_tag=author_tag)

        if not os.path.exists(final_path):
            await update.message.reply_text("❌ Download failed. File not found.")
            return

        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=msg.message_id,
            text=f"✅ Download and remuxing complete: {os.path.basename(final_path)}"
        )

        dest_path = os.path.join(GOOGLE_PHOTOS_FOLDER, os.path.basename(final_path))
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: shutil.move(final_path, dest_path))
        await loop.run_in_executor(None, lambda: os.system(f"termux-media-scan '{dest_path}'"))

        await update.message.reply_text(f"✅ File uploaded to device: {os.path.basename(dest_path)}")

    except Exception as e:
        logging.exception("Error in handle_lm")
        await update.message.reply_text(f"❌ Error: {e}")

if __name__ == '__main__':
    BOT_TOKEN = "6385636650:AAGsa2aZ2mQtPFB2tk81rViOO_H_6hHFoQE"  # Replace with your actual bot token
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Create wrapper functions
    async def wrapper_lm(update: Update, context: ContextTypes.DEFAULT_TYPE):
        asyncio.create_task(handle_lm(update, context))
    
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

    # Register handlers
    app.add_handler(CommandHandler("lm", wrapper_lm))  # Keep for backward compatibility
    app.add_handler(CommandHandler("l", wrapper_l))
    app.add_handler(CommandHandler("unzip", wrapper_unzip))
    app.add_handler(CommandHandler("clean", wrapper_clean))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, wrapper_direct))
    app.add_handler(CommandHandler("force_stop", wrapper_force_stop))
    app.add_handler(CommandHandler("force_start", wrapper_force_start))

    print("🤖 Bot running with auto-remux enabled for MKV files...")
    app.run_polling()
