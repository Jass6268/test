import os
import aiohttp
import asyncio
import shutil
import tempfile
import zipfile
import rarfile
import time
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

GOOGLE_PHOTOS_FOLDER = "/sdcard/Downloads/"  # Update this if needed
# Global semaphore to limit concurrent downloads
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(2)

async def download_with_progress(url, dest_path, message, context, chat_id):
    try:
        async with DOWNLOAD_SEMAPHORE:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=3600)) as resp:
                    if resp.status != 200:
                        raise ValueError(f"Failed to download. Status code: {resp.status}")
                    
                    total = int(resp.headers.get('content-length', 0))
                    downloaded = 0
                    chunk_size = 4 * 1024 * 1024
                    start_time = time.time()
                    last_update_time = time.time()
                    
                    with open(dest_path, 'wb') as f:
                        async for chunk in resp.content.iter_chunked(chunk_size):
                            # Check if the task has been cancelled
                            if not context.args:
                                raise asyncio.CancelledError("Download cancelled")
                            
                            f.write(chunk)
                            f.flush()
                            os.fsync(f.fileno())
                            downloaded += len(chunk)
                            current_time = time.time()
                            elapsed_time = current_time - start_time
                            speed = downloaded / 1024 / 1024 / (elapsed_time or 0.1)
                            
                            # Update progress less frequently to reduce API calls
                            if current_time - last_update_time >= 10:
                                try:
                                    percent = (downloaded / total) * 100 if total > 0 else 0
                                    await context.bot.edit_message_text(
                                        chat_id=chat_id,
                                        message_id=message.message_id,
                                        text=f"📥 Downloading...\nProgress: {downloaded//1024//1024}MB / {total//1024//1024}MB ({percent:.2f}%)\nSpeed: {speed:.2f} MB/s"
                                    )
                                except Exception:
                                    pass
                                last_update_time = current_time
                    
                    return dest_path
    except asyncio.CancelledError:
        # Handle cancellation gracefully
        if os.path.exists(dest_path):
            os.remove(dest_path)
        raise
    except Exception as e:
        logging.exception("Error during download:")
        raise e

async def handle_l(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Validate command arguments
        if len(context.args) < 3 or "-n" not in context.args:
            await update.message.reply_text("❌ Usage: /l <url> -n <filename>")
            return

        # Find URL and filename
        url_index = context.args.index("-n") - 1
        url = context.args[url_index]
        filename = context.args[url_index + 2]
        
        # Create temporary file path
        temp_file_path = os.path.join(tempfile.gettempdir(), filename)

        # Send initial message
        msg = await update.message.reply_text("⏳ Starting download...")
        
        try:
            # Download file with progress tracking
            final_path = await download_with_progress(url, temp_file_path, msg, context, update.effective_chat.id)

            # Verify download
            if not os.path.exists(final_path):
                await update.message.reply_text("❌ Download failed. File not found.")
                return

            # Update download complete message
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=msg.message_id,
                text=f"✅ Download complete: {filename}"
            )

            # Move to destination
            dest_path = os.path.join(GOOGLE_PHOTOS_FOLDER, filename)
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            shutil.move(final_path, dest_path)
            
            # Scan media (for Termux)
            os.system(f"termux-media-scan {dest_path}")

            # Optional: Delete file after some time
            await asyncio.sleep(30)
            os.remove(dest_path)

            await update.message.reply_text(f"✅ File uploaded and deleted from device: {filename}")

        except asyncio.CancelledError:
            await update.message.reply_text("❌ Download was cancelled.")
        except Exception as download_error:
            await update.message.reply_text(f"❌ Download error: {download_error}")

    except Exception as e:
        logging.exception("Error in handle_l")
        await update.message.reply_text(f"❌ Unexpected error: {e}")

async def handle_unzip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Validate command arguments
        if len(context.args) < 1:
            await update.message.reply_text("❌ Usage: /unzip <url>")
            return

        url = context.args[0]
        
        # Create temporary directories
        temp_dir = tempfile.mkdtemp()
        archive_name = os.path.join(temp_dir, "archive")
        
        # Send initial message
        msg = await update.message.reply_text("⏳ Starting archive download...")

        try:
            # Download archive
            downloaded_path = await download_with_progress(url, archive_name, msg, context, update.effective_chat.id)

            # Prepare extraction directory
            extract_path = os.path.join(temp_dir, "extracted")
            os.makedirs(extract_path, exist_ok=True)

            # Extract archive
            if zipfile.is_zipfile(downloaded_path):
                with zipfile.ZipFile(downloaded_path, 'r') as zip_ref:
                    zip_ref.extractall(extract_path)
            elif rarfile.is_rarfile(downloaded_path):
                with rarfile.RarFile(downloaded_path, 'r') as rar_ref:
                    rar_ref.extractall(extract_path)
            else:
                await update.message.reply_text("❌ Unsupported archive format.")
                return

            # Upload and clean up extracted files
            uploaded_files = 0
            for root, dirs, files in os.walk(extract_path):
                for file in files:
                    src = os.path.join(root, file)
                    dst = os.path.join(GOOGLE_PHOTOS_FOLDER, file)
                    shutil.copy(src, dst)
                    os.system(f"termux-media-scan {dst}")
                    uploaded_files += 1
                    await asyncio.sleep(1)
                    os.remove(dst)

            # Clean up temporary files
            shutil.rmtree(temp_dir)

            # Update completion message
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=msg.message_id,
                text=f"✅ Extracted and uploaded {uploaded_files} file(s), then deleted."
            )

        except asyncio.CancelledError:
            await update.message.reply_text("❌ Archive download was cancelled.")
        except Exception as download_error:
            await update.message.reply_text(f"❌ Archive download error: {download_error}")

    except Exception as e:
        logging.exception("Error in handle_unzip")
        await update.message.reply_text(f"❌ Unexpected error: {e}")

async def handle_clean(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        deleted = 0
        for file in os.listdir(GOOGLE_PHOTOS_FOLDER):
            path = os.path.join(GOOGLE_PHOTOS_FOLDER, file)
            if os.path.isfile(path):
                os.remove(path)
                deleted += 1
        await update.message.reply_text(f"🧹 Deleted {deleted} file(s) from {GOOGLE_PHOTOS_FOLDER}")
    except Exception as e:
        logging.exception("Error in handle_clean")
        await update.message.reply_text(f"❌ Error while cleaning: {e}")

# Error handler for unexpected errors
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.error(f"An error occurred: {context.error}")
    if update:
        await update.message.reply_text("❌ An unexpected error occurred. Please try again.")

def main():
    # Replace with your actual bot token
    BOT_TOKEN = "6385636650:AAGsa2aZ2mQtPFB2tk81rViOO_H_6hHFoQE"  # Make sure to replace this
    
    # Create the Application and pass it your bot's token
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Add command handlers
    app.add_handler(CommandHandler("l", handle_l))
    app.add_handler(CommandHandler("unzip", handle_unzip))
    app.add_handler(CommandHandler("clean", handle_clean))

    # Add global error handler
    app.add_error_handler(error_handler)

    # Start the bot
    print("🤖 Bot running...")
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
