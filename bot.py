import os
import aiohttp
import asyncio
import shutil
import tempfile
import zipfile
import rarfile
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

GOOGLE_PHOTOS_FOLDER = "/data/media/0/DCIM/Camera/"  # Change if needed

BOT_TOKEN = "6385636650:AAGsa2aZ2mQtPFB2tk81rViOO_H_6hHFoQE"

async def download_with_progress(url, dest_path, message, context, chat_id):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            total = int(resp.headers.get('content-length', 0))
            downloaded = 0
            chunk_size = 1024 * 1024
            with open(dest_path, 'wb') as f:
                async for chunk in resp.content.iter_chunked(chunk_size):
                    f.write(chunk)
                    downloaded += len(chunk)
                    percent = (downloaded / total) * 100
                    try:
                        await context.bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=message.message_id,
                            text=f"📥 Downloading...\nProgress: {downloaded//1024//1024}MB / {total//1024//1024}MB ({percent:.2f}%)"
                        )
                    except:
                        pass
    return dest_path

async def handle_l(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 3 or "-n" not in context.args:
        await update.message.reply_text("❌ Usage: /l <url> -n <filename>")
        return

    try:
        url_index = context.args.index("-n") - 1
        url = context.args[url_index]
        filename = context.args[url_index + 2]
        temp_file_path = os.path.join(tempfile.gettempdir(), filename)

        msg = await update.message.reply_text("⏳ Starting download...")
        await download_with_progress(url, temp_file_path, msg, context, update.effective_chat.id)

        dest_path = os.path.join(GOOGLE_PHOTOS_FOLDER, filename)
        shutil.move(temp_file_path, dest_path)

        await asyncio.sleep(30)
        os.remove(dest_path)

        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=msg.message_id,
            text=f"✅ Uploaded and deleted: {filename}"
        )

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def handle_unzip(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            return

        uploaded_files = 0
        for root, dirs, files in os.walk(extract_path):
            for file in files:
                src = os.path.join(root, file)
                dst = os.path.join(GOOGLE_PHOTOS_FOLDER, file)
                shutil.copy(src, dst)
                uploaded_files += 1
                await asyncio.sleep(1)
                os.remove(dst)

        shutil.rmtree(temp_dir)

        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=msg.message_id,
            text=f"✅ Extracted and uploaded {uploaded_files} file(s), then deleted."
        )

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

if __name__ == '__main__':
    import logging

    logging.basicConfig(level=logging.INFO)

    BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"  # Replace this with your actual bot token
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("l", handle_l))
    app.add_handler(CommandHandler("unzip", handle_unzip))

    print("🤖 Bot running...")
    app.run_polling()
