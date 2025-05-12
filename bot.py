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

# Add this new function for remuxing MKV files with metadata
async def remux_with_metadata(input_path, author_tag=""):
    """Remux an MKV file with metadata using mkvpropedit"""
    try:
        output_dir = os.path.dirname(input_path)
        file_name = os.path.basename(input_path)
        name, ext = os.path.splitext(file_name)
        output_path = os.path.join(output_dir, f"{name}_remuxed{ext}")
        
        # First, copy the original file to the output path
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
        
        return output_path
    except Exception as e:
        logging.exception(f"Error in remux_with_metadata: {e}")
        return None

# Modify download_with_progress to include a flag for remuxing
async def download_with_progress(url, dest_path, message, context, chat_id, remux=False, author_tag=""):
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
        
        # Add remuxing step if requested and file is MKV
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

# Add a new command handler for downloading with remux
async def handle_lm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if len(context.args) < 3 or "-n" not in context.args:
            await update.message.reply_text("❌ Usage: /lm <url> -n <filename> [-a <author_tag>]")
            return

        url_index = context.args.index("-n") - 1
        url = context.args[url_index]
        name_index = context.args.index("-n") + 1
        
        # Check if author tag is provided
        author_tag = ""
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

# Modify the main block to add the new handler
if __name__ == '__main__':
    BOT_TOKEN = "6385636650:AAGsa2aZ2mQtPFB2tk81rViOO_H_6hHFoQE"  # Replace with your actual bot token
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Create wrapper functions
    async def wrapper_lm(update: Update, context: ContextTypes.DEFAULT_TYPE):
        asyncio.create_task(handle_lm(update, context))
    
    # Add other wrapper functions here (existing ones from your code)
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
    app.add_handler(CommandHandler("lm", wrapper_lm))  # New handler for remuxing
    app.add_handler(CommandHandler("l", wrapper_l))
    app.add_handler(CommandHandler("unzip", wrapper_unzip))
    app.add_handler(CommandHandler("clean", wrapper_clean))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, wrapper_direct))
    app.add_handler(CommandHandler("force_stop", wrapper_force_stop))
    app.add_handler(CommandHandler("force_start", wrapper_force_start))

    print("🤖 Bot running...")
    app.run_polling()
