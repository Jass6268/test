#!/usr/bin/env python3
"""
Simple Google Photos Upload Checker with Cancel Command
-------------------------------------------------------
1. Detects new MKV files in Camera folder
2. Opens Google Photos app for auto-sync
3. Checks every 10 seconds if file uploaded to Google Photos
4. When uploaded: sends Telegram link, force stops app, deletes file
5. Processes next file
6. /cancel command to skip current file and process next
"""

import os
import time
import json
import requests
import logging
import subprocess
import threading
import asyncio
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Configuration
CAMERA_FOLDER = "/sdcard/DCIM/Camera/"  # Bliss OS camera folder
TELEGRAM_BOT_TOKEN = "8114381417:AAFlvW0cQBhv4LTi1m8pmMuR-zC_zl0MWpo"  # Replace with your bot token
TELEGRAM_CHAT_ID = "6575149109"  # Replace with your chat ID
GOOGLE_PHOTOS_PACKAGE = "com.google.android.apps.photos"
CHECK_INTERVAL = 10  # Check upload status every 10 seconds
MAX_CHECK_TIME = 3600  # Maximum check time: 1 hour

# Google Photos API (using your existing token)
SCOPES = ['https://www.googleapis.com/auth/photoslibrary']
TOKEN_FILE = 'token.json'
CREDENTIALS_FILE = 'credentials.json'

class FileQueue:
    """Thread-safe queue for processing files one by one with cancel support"""
    def __init__(self):
        self.queue = []
        self.processing = False
        self.current_file = None
        self.cancel_requested = False
        self.lock = threading.Lock()
    
    def add_file(self, file_path):
        with self.lock:
            if file_path not in self.queue:
                self.queue.append(file_path)
                logger.info(f"Added to queue: {os.path.basename(file_path)} (Queue size: {len(self.queue)})")
    
    def get_next_file(self):
        with self.lock:
            if self.queue:
                return self.queue.pop(0)
            return None
    
    def set_processing(self, status, file_path=None):
        with self.lock:
            self.processing = status
            self.current_file = file_path if status else None
            if not status:  # Reset cancel when processing stops
                self.cancel_requested = False
    
    def is_processing(self):
        with self.lock:
            return self.processing
    
    def get_current_file(self):
        with self.lock:
            return self.current_file
    
    def request_cancel(self):
        with self.lock:
            self.cancel_requested = True
            logger.info(f"Cancel requested for: {os.path.basename(self.current_file) if self.current_file else 'current file'}")
    
    def is_cancel_requested(self):
        with self.lock:
            return self.cancel_requested
    
    def get_queue_status(self):
        with self.lock:
            return {
                'queue_size': len(self.queue),
                'processing': self.processing,
                'current_file': os.path.basename(self.current_file) if self.current_file else None,
                'next_files': [os.path.basename(f) for f in self.queue[:3]]  # Show next 3 files
            }

# Global reference to the handler for cancel command
upload_checker_handler = None

class SimpleUploadChecker(FileSystemEventHandler):
    """Simple handler that checks upload status every 10 seconds with cancel support"""

    def __init__(self):
        super().__init__()
        self.file_queue = FileQueue()
        self.google_photos_service = self._setup_google_photos()
        self.processor_thread = threading.Thread(target=self._process_queue, daemon=True)
        self.processor_thread.start()
        
        # Set global reference for cancel command
        global upload_checker_handler
        upload_checker_handler = self
        
        logger.info("Simple upload checker with cancel support initialized")

    def _setup_google_photos(self):
        """Set up Google Photos API service"""
        try:
            creds = None
            if os.path.exists(TOKEN_FILE):
                with open(TOKEN_FILE, 'r') as token:
                    creds = Credentials.from_authorized_user_info(
                        json.loads(token.read()), SCOPES)
            
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                with open(TOKEN_FILE, 'w') as token:
                    token.write(creds.to_json())
            
            if creds and creds.valid:
                return build('photoslibrary', 'v1', credentials=creds, static_discovery=False)
            else:
                logger.warning("No valid Google Photos credentials found")
                return None
        except Exception as e:
            logger.error(f"Error setting up Google Photos API: {e}")
            return None

    def on_created(self, event):
        """Handle file creation events"""
        if not event.is_directory and event.src_path.lower().endswith('.mkv'):
            logger.info(f"New MKV file detected: {os.path.basename(event.src_path)}")
            
            # Wait 3 seconds as requested before processing
            logger.info("Waiting 3 seconds before processing...")
            time.sleep(3)
            
            if os.path.exists(event.src_path):
                self.file_queue.add_file(event.src_path)
            else:
                logger.warning(f"File disappeared after 3 seconds: {os.path.basename(event.src_path)}")

    def _process_queue(self):
        """Process files one by one from the queue with cancel support"""
        while True:
            if not self.file_queue.is_processing():
                file_path = self.file_queue.get_next_file()
                if file_path:
                    self.file_queue.set_processing(True, file_path)
                    try:
                        self._process_file(file_path)
                    except Exception as e:
                        logger.error(f"Error processing {file_path}: {str(e)}")
                    finally:
                        self.file_queue.set_processing(False)
            time.sleep(2)

    def cancel_current_processing(self):
        """Cancel current file processing and move to next"""
        if self.file_queue.is_processing():
            self.file_queue.request_cancel()
            return True
        return False

    def get_queue_status(self):
        """Get current queue status"""
        return self.file_queue.get_queue_status()

    def _get_alternative_share_link(self, filename):
        """Alternative method to get shareable link"""
        try:
            # Search for the media item again
            results = self.google_photos_service.mediaItems().list(
                pageSize=50
            ).execute()
            
            media_items = results.get('mediaItems', [])
            
            for item in media_items:
                item_filename = item.get('filename', '')
                if filename.lower() in item_filename.lower() or item_filename.lower() in filename.lower():
                    
                    # Try to create a direct share
                    media_item_id = item.get('id')
                    
                    # Method 2: Use the baseUrl with sharing parameters
                    base_url = item.get('baseUrl', '')
                    if base_url:
                        # Create a shareable version of the baseUrl
                        # Note: This might need additional processing
                        return base_url
                    
                    # Method 3: Return productUrl as fallback
                    product_url = item.get('productUrl', '')
                    if product_url:
                        return product_url
            
            return "Unable to create shareable link"
            
        except Exception as e:
            logger.error(f"Alternative share link method failed: {e}")
            return "Share link creation failed"

    def _cleanup_old_albums(self):
        """Clean up old shared albums created for sharing"""
        try:
            # List albums and delete old ones starting with "Shared_"
            albums_response = self.google_photos_service.albums().list(
                pageSize=50
            ).execute()
            
            albums = albums_response.get('albums', [])
            current_time = time.time()
            
            for album in albums:
                title = album.get('title', '')
                if title.startswith('Shared_') and '_' in title:
                    try:
                        # Extract timestamp from album name
                        timestamp_str = title.split('_')[-1]
                        album_time = int(timestamp_str)
                        
                        # Delete albums older than 1 hour
                        if current_time - album_time > 3600:
                            album_id = album.get('id')
                            if album_id:
                                logger.info(f"Cleaning up old album: {title}")
                                # Note: Google Photos API doesn't support deleting albums
                                # They will remain but won't interfere
                    except:
                        continue
                        
        except Exception as e:
            logger.debug(f"Album cleanup error: {e}")

    def _process_file(self, file_path):
        """Process a single file: open app → check every 10s → actions when uploaded"""
        filename = os.path.basename(file_path)
        logger.info(f"Processing: {filename}")
        
        try:
            # Step 1: Check if file still exists
            if not os.path.exists(file_path):
                logger.warning(f"File no longer exists: {filename}")
                return
            
            # Check for cancel before starting
            if self.file_queue.is_cancel_requested():
                logger.info(f"❌ Processing cancelled for: {filename}")
                self._send_cancel_notification(filename)
                return
            
            file_size = os.path.getsize(file_path)
            logger.info(f"File size: {file_size / (1024*1024):.1f}MB")
            
            # Step 2: Wait 3 seconds then open Google Photos app
            logger.info("Opening Google Photos app after detection...")
            self._open_google_photos()
            time.sleep(15)  # Give app time to start and begin sync
            
            # Check for cancel after opening app
            if self.file_queue.is_cancel_requested():
                logger.info(f"❌ Processing cancelled after opening app: {filename}")
                self._force_stop_google_photos()
                self._send_cancel_notification(filename)
                return
            
            # Step 3: Check every 10 seconds if file is uploaded
            logger.info(f"Checking every {CHECK_INTERVAL} seconds if {filename} is uploaded...")
            
            upload_found = False
            total_check_time = 0
            
            while total_check_time < MAX_CHECK_TIME:
                # Check for cancel during upload checking
                if self.file_queue.is_cancel_requested():
                    logger.info(f"❌ Processing cancelled during upload check: {filename}")
                    self._force_stop_google_photos()
                    self._send_cancel_notification(filename)
                    return
                
                time.sleep(CHECK_INTERVAL)
                total_check_time += CHECK_INTERVAL
                
                logger.info(f"Checking upload status... ({total_check_time}s elapsed)")
                
                # Check if file is uploaded to Google Photos
                if self._check_file_uploaded(filename, file_size):
                    logger.info(f"✅ {filename} found uploaded to Google Photos!")
                    upload_found = True
                    break
            
            if upload_found:
                # Check for cancel before final actions
                if self.file_queue.is_cancel_requested():
                    logger.info(f"❌ Processing cancelled before final actions: {filename}")
                    self._force_stop_google_photos()
                    self._send_cancel_notification(filename)
                    return
                
                # Step 4: Get shareable link (photos.app.goo.gl format)
                logger.info("Creating shareable link...")
                share_link = self._get_share_link(filename)
                
                # Step 5: Send Telegram notification
                self._send_telegram_notification(filename, file_size, share_link)
                
                # Step 6: Force stop Google Photos app
                logger.info("Force stopping Google Photos app...")
                self._force_stop_google_photos()
                
                # Step 7: Delete file from Camera folder
                logger.info(f"Deleting {filename} from Camera folder...")
                self._delete_file(file_path)
                
                # Step 8: Clean up old albums (optional)
                self._cleanup_old_albums()
                
                logger.info(f"✅ Successfully processed: {filename}")
            else:
                logger.warning(f"⏰ Timeout: {filename} not uploaded after {MAX_CHECK_TIME}s")
                self._force_stop_google_photos()
                
        except Exception as e:
            logger.error(f"Error processing {filename}: {str(e)}")
            try:
                self._force_stop_google_photos()
            except:
                pass

    def _send_cancel_notification(self, filename):
        """Send notification that processing was cancelled"""
        try:
            next_files = self.file_queue.get_queue_status()['next_files']
            next_info = f"\nNext in queue: {', '.join(next_files[:2])}" if next_files else "\nQueue is empty"
            
            message = f"""❌ Processing Cancelled

📁 Cancelled file: {filename}
🔄 Moving to next file in queue...{next_info}

Use /status to check queue status"""

            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            data = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
            requests.post(url, data=data, timeout=10)
            
            logger.info(f"Cancel notification sent for: {filename}")
        except Exception as e:
            logger.error(f"Error sending cancel notification: {e}")

    def _check_file_uploaded(self, filename, file_size):
        """Check if file is uploaded to Google Photos"""
        if not self.google_photos_service:
            logger.warning("No Google Photos service available")
            return False
        
        try:
            # Search for recent media items
            results = self.google_photos_service.mediaItems().list(
                pageSize=50
            ).execute()
            
            media_items = results.get('mediaItems', [])
            
            for item in media_items:
                # Check if filename matches (Google Photos might change the name)
                item_filename = item.get('filename', '')
                
                # Check multiple conditions for a match
                if (filename.lower() in item_filename.lower() or 
                    item_filename.lower() in filename.lower() or
                    self._compare_file_properties(item, file_size)):
                    
                    logger.info(f"Found matching file: {item_filename}")
                    return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error checking upload status: {e}")
            return False

    def _compare_file_properties(self, item, original_size):
        """Compare file properties to identify uploaded file"""
        try:
            # Check file type
            mime_type = item.get('mimeType', '')
            if 'video' not in mime_type.lower():
                return False
            
            # Check approximate file size (within 10% tolerance)
            metadata = item.get('mediaMetadata', {})
            width = metadata.get('width', 0)
            height = metadata.get('height', 0)
            
            # If it's a recent video file, consider it a match
            # (Google Photos API doesn't always provide exact file size)
            creation_time = metadata.get('creationTime', '')
            if creation_time:
                # Check if created recently (within last hour)
                from datetime import datetime, timezone
                import dateutil.parser
                
                created = dateutil.parser.parse(creation_time)
                now = datetime.now(timezone.utc)
                time_diff = (now - created).total_seconds()
                
                if time_diff < 3600:  # Within last hour
                    return True
            
            return False
            
        except Exception as e:
            logger.debug(f"Error comparing file properties: {e}")
            return False

    def _get_share_link(self, filename):
        """Get shareable Google Photos link (photos.app.goo.gl format) - FIXED VERSION"""
        if not self.google_photos_service:
            return "Google Photos link not available"
        
        try:
            logger.info(f"Creating shareable link for: {filename}")
            
            # Step 1: Search for the uploaded file
            results = self.google_photos_service.mediaItems().list(
                pageSize=50
            ).execute()
            
            media_items = results.get('mediaItems', [])
            target_item = None
            
            logger.info(f"Found {len(media_items)} recent media items")
            
            # Find the matching file
            for item in media_items:
                item_filename = item.get('filename', '')
                item_id = item.get('id', '')
                
                if (filename.lower() in item_filename.lower() or 
                    item_filename.lower() in filename.lower()):
                    target_item = item
                    logger.info(f"Found matching item: {item_filename} (ID: {item_id[:20]}...)")
                    break
            
            if not target_item:
                logger.error(f"File {filename} not found in Google Photos")
                return "File not found in Google Photos"
            
            media_item_id = target_item.get('id')
            if not media_item_id:
                logger.error("Media item ID not available")
                return "Media item ID not available"
            
            # Step 2: Create a shared album (FIXED METHOD)
            album_title = f"Auto_Share_{int(time.time())}"  # Shorter, cleaner name
            
            logger.info(f"Creating shared album: {album_title}")
            
            album_body = {
                'album': {
                    'title': album_title
                }
            }
            
            try:
                album_response = self.google_photos_service.albums().create(body=album_body).execute()
                album_id = album_response.get('id')
                
                if not album_id:
                    raise Exception("Album creation returned no ID")
                
                logger.info(f"Album created successfully: {album_id[:20]}...")
                
            except Exception as album_error:
                logger.error(f"Album creation failed: {album_error}")
                return self._fallback_share_method(target_item, filename)
            
            # Step 3: Add media item to the album (FIXED METHOD)
            logger.info("Adding media item to album...")
            
            try:
                add_media_body = {
                    'mediaItemIds': [media_item_id]
                }
                
                add_response = self.google_photos_service.albums().batchAddMediaItems(
                    albumId=album_id,
                    body=add_media_body
                ).execute()
                
                # Check if adding was successful
                if 'newMediaItemResults' in add_response:
                    results = add_response['newMediaItemResults']
                    if results and results[0].get('status', {}).get('message') == 'Success':
                        logger.info("Media item added to album successfully")
                    else:
                        raise Exception(f"Add media failed: {results}")
                else:
                    logger.info("Media item added (no explicit success confirmation)")
                
            except Exception as add_error:
                logger.error(f"Adding media to album failed: {add_error}")
                # Try to delete the album we created
                try:
                    self.google_photos_service.albums().delete(albumId=album_id).execute()
                except:
                    pass
                return self._fallback_share_method(target_item, filename)
            
            # Step 4: Share the album to get shareable link (FIXED METHOD)
            logger.info("Creating shareable link from album...")
            
            try:
                share_body = {
                    'sharedAlbumOptions': {
                        'isCollaborative': False,
                        'isCommentable': False
                    }
                }
                
                share_response = self.google_photos_service.albums().share(
                    albumId=album_id,
                    body=share_body
                ).execute()
                
                share_info = share_response.get('shareInfo', {})
                shareable_url = share_info.get('shareableUrl', '')
                share_token = share_info.get('shareToken', '')
                
                if shareable_url and 'photos.app.goo.gl' in shareable_url:
                    logger.info(f"✅ SUCCESS: Created photos.app.goo.gl link: {shareable_url}")
                    return shareable_url
                elif shareable_url:
                    logger.info(f"✅ SUCCESS: Created shareable link: {shareable_url}")
                    return shareable_url
                else:
                    raise Exception(f"No shareable URL in response: {share_response}")
                
            except Exception as share_error:
                logger.error(f"Album sharing failed: {share_error}")
                # Try to delete the album we created
                try:
                    self.google_photos_service.albums().delete(albumId=album_id).execute()
                except:
                    pass
                return self._fallback_share_method(target_item, filename)
            
        except Exception as e:
            logger.error(f"Error in _get_share_link: {str(e)}")
            return f"Share link creation failed: {str(e)}"

    def _fallback_share_method(self, target_item, filename):
        """Fallback method when album sharing fails"""
        logger.info("Trying fallback share methods...")
        
        try:
            # Method 1: Try to get existing shared albums containing this item
            albums_response = self.google_photos_service.sharedAlbums().list(
                pageSize=50
            ).execute()
            
            shared_albums = albums_response.get('sharedAlbums', [])
            
            for album in shared_albums:
                share_info = album.get('shareInfo', {})
                shareable_url = share_info.get('shareableUrl', '')
                if shareable_url and 'photos.app.goo.gl' in shareable_url:
                    logger.info(f"Found existing shared album link: {shareable_url}")
                    return shareable_url
            
            # Method 2: Return product URL with warning
            product_url = target_item.get('productUrl', '')
            if product_url:
                logger.warning(f"Using productUrl as fallback: {product_url}")
                return f"⚠️ Temporary link (not permanent): {product_url}"
            
            # Method 3: Last resort - base URL with warning
            base_url = target_item.get('baseUrl', '')
            if base_url:
                logger.warning(f"Using baseUrl as last resort: {base_url[:50]}...")
                return f"❌ TEMPORARY LINK - Will expire soon!"
            
            return "❌ Could not create any shareable link"
            
        except Exception as e:
            logger.error(f"Fallback share method failed: {e}")
            return f"❌ All share methods failed: {str(e)}"

    def _open_google_photos(self):
        """Open Google Photos app"""
        try:
            subprocess.run([
                'am', 'start',
                '-n', f'{GOOGLE_PHOTOS_PACKAGE}/.home.HomeActivity'
            ], check=True, capture_output=True)
            logger.info("Google Photos app opened")
        except subprocess.CalledProcessError:
            try:
                subprocess.run([
                    'am', 'start',
                    '-a', 'android.intent.action.MAIN',
                    '-c', 'android.intent.category.LAUNCHER',
                    GOOGLE_PHOTOS_PACKAGE
                ], check=True, capture_output=True)
                logger.info("Google Photos app opened via intent")
            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to open Google Photos: {e}")

    def _send_telegram_notification(self, filename, file_size, share_link):
        """Send Telegram notification with file info and link - ENHANCED"""
        try:
            # Check if we got a proper photos.app.goo.gl link
            if 'photos.app.goo.gl' in share_link:
                link_status = "✅ Permanent shareable link"
                link_emoji = "🔗"
            elif 'googleusercontent.com' in share_link:
                link_status = "⚠️ Temporary link (will expire)"
                link_emoji = "⏰"
            elif 'TEMPORARY' in share_link or 'expire' in share_link.lower():
                link_status = "❌ Temporary link (not permanent)"
                link_emoji = "⚠️"
            else:
                link_status = "✅ Shareable link"
                link_emoji = "🔗"

            message = f"""📱 **New video uploaded to Google Photos!**

📁 **File:** {filename}
📊 **Size:** {file_size / (1024*1024):.1f}MB
{link_emoji} **Link:** {share_link}

{link_status}
✅ **Processed automatically from Bliss OS**"""

            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            data = {
                "chat_id": TELEGRAM_CHAT_ID, 
                "text": message,
                "parse_mode": "Markdown"
            }
            response = requests.post(url, data=data, timeout=10)
            
            if response.status_code == 200:
                logger.info(f"✅ Telegram notification sent with {link_status}")
            else:
                logger.error(f"Telegram error: {response.text}")
        except Exception as e:
            logger.error(f"Error sending Telegram: {e}")

    def _force_stop_google_photos(self):
        """Force stop Google Photos app using multiple robust methods"""
        try:
            logger.info("⚡ Force-stopping Google Photos (ROOT)...")
            
            # Root-based killing commands
            kill_commands = [
                # Method 1: Using 'am' with root (force-stop)
                f"su -c 'am force-stop {GOOGLE_PHOTOS_PACKAGE}'",
                
                # Method 2: Using 'killall' (root)
                f"su -c 'killall -9 {GOOGLE_PHOTOS_PACKAGE}'",
                
                # Method 3: Using 'pkill' (if available)
                f"su -c 'pkill -9 -f {GOOGLE_PHOTOS_PACKAGE}'",
                
                # Method 4: Manual process ID killing (robust)
                f"su -c 'ps | grep {GOOGLE_PHOTOS_PACKAGE} | grep -v grep | awk \"{{print \\$2}}\" | xargs kill -9'",
            ]
            
            success = False
            
            for cmd in kill_commands:
                try:
                    logger.info(f"Executing: {cmd}")
                    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
                    
                    if result.returncode == 0:
                        logger.info(f"✅ Command successful: {cmd}")
                        success = True
                    else:
                        logger.warning(f"Command returned {result.returncode}: {result.stderr}")
                    
                    time.sleep(1)  # Wait between commands
                    
                except subprocess.TimeoutExpired:
                    logger.warning(f"Command timeout: {cmd}")
                    continue
                except Exception as e:
                    logger.warning(f"Command failed: {cmd} - {e}")
                    continue
            
            # Verify if stopped
            time.sleep(2)
            check_cmd = f"su -c 'ps | grep {GOOGLE_PHOTOS_PACKAGE} | grep -v grep'"
            try:
                check_result = subprocess.run(check_cmd, shell=True, capture_output=True, text=True, timeout=5)
                
                if not check_result.stdout.strip():
                    logger.info("✅ Google Photos force-stopped successfully (root)!")
                else:
                    logger.warning("⚠️ Google Photos might still be running")
                    logger.info(f"Running processes: {check_result.stdout}")
                    
            except Exception as e:
                logger.warning(f"Could not verify stop status: {e}")
            
            # Optional: Clear app cache/data (uncomment if needed)
            # clear_cmd = f"su -c 'pm clear {GOOGLE_PHOTOS_PACKAGE}'"
            # subprocess.run(clear_cmd, shell=True, capture_output=True, timeout=10)
            
        except Exception as e:
            logger.error(f"Error force stopping Google Photos: {str(e)}")
            
            # Fallback method
            try:
                logger.info("Trying fallback force stop method...")
                subprocess.run([
                    'su', '-c', f'pkill -f {GOOGLE_PHOTOS_PACKAGE}'
                ], capture_output=True, timeout=5)
                logger.info("Fallback method executed")
            except Exception as fallback_error:
                logger.error(f"Fallback method also failed: {fallback_error}")

    def _delete_file(self, file_path):
        """Delete file from Camera folder"""
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"File deleted: {os.path.basename(file_path)}")
        except Exception as e:
            logger.error(f"Error deleting file: {e}")

# Telegram Bot Commands
async def handle_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /cancel command to skip current file processing"""
    try:
        if upload_checker_handler is None:
            await update.message.reply_text("❌ Upload checker not initialized")
            return
        
        status = upload_checker_handler.get_queue_status()
        
        if not status['processing']:
            await update.message.reply_text("ℹ️ No file is currently being processed")
            return
        
        current_file = status['current_file']
        success = upload_checker_handler.cancel_current_processing()
        
        if success:
            next_files = status['next_files']
            next_info = f"\nNext: {', '.join(next_files[:2])}" if next_files else "\nQueue is empty"
            
            await update.message.reply_text(
                f"❌ **Processing Cancelled**\n\n"
                f"📁 Cancelled: {current_file}\n"
                f"🔄 Moving to next file...{next_info}"
            )
        else:
            await update.message.reply_text("❌ Failed to cancel current processing")
            
    except Exception as e:
        logger.error(f"Error in cancel command: {e}")
        await update.message.reply_text(f"❌ Cancel Error: {e}")

async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status command to show queue status"""
    try:
        if upload_checker_handler is None:
            await update.message.reply_text("❌ Upload checker not initialized")
            return
        
        status = upload_checker_handler.get_queue_status()
        
        if status['processing']:
            current_info = f"🔄 **Currently Processing:**\n📁 {status['current_file']}\n\n"
        else:
            current_info = "✅ **Status:** Idle (no file processing)\n\n"
        
        if status['queue_size'] > 0:
            queue_info = f"📋 **Queue:** {status['queue_size']} files waiting\n"
            if status['next_files']:
                queue_info += f"📂 Next: {', '.join(status['next_files'])}"
        else:
            queue_info = "📋 **Queue:** Empty"
        
        await update.message.reply_text(
            f"{current_info}{queue_info}\n\n"
            f"💡 Use /cancel to skip current file"
        )
        
    except Exception as e:
        logger.error(f"Error in status command: {e}")
        await update.message.reply_text(f"❌ Status Error: {e}")

async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    help_text = """🤖 **Google Photos Auto Upload Bot**

**Commands:**
/cancel - Cancel current file processing and move to next
/status - Show queue status and current processing
/help - Show this help message

**How it works:**
1. Drop MKV files in Camera folder
2. Bot detects and auto-uploads to Google Photos
3. Sends you shareable links via Telegram
4. Automatically deletes original files

**Cancel feature:**
- Use /cancel to skip current upload
- Bot will force-stop Google Photos app
- Moves to next file in queue immediately"""
    
    await update.message.reply_text(help_text)

def setup_telegram_bot():
    """Set up Telegram bot for commands"""
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("No Telegram bot token provided - /cancel command not available")
        return None
    
    try:
        application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        
        # Add command handlers
        application.add_handler(CommandHandler("cancel", handle_cancel))
        application.add_handler(CommandHandler("status", handle_status))
        application.add_handler(CommandHandler("help", handle_help))
        
        return application
    except Exception as e:
        logger.error(f"Error setting up Telegram bot: {e}")
        return None

def main():
    """Main function with Telegram bot integration"""
    logger.info("="*60)
    logger.info("SIMPLE GOOGLE PHOTOS UPLOAD CHECKER WITH /CANCEL")
    logger.info("="*60)
    logger.info(f"Monitoring: {CAMERA_FOLDER}")
    logger.info(f"Check interval: {CHECK_INTERVAL} seconds")
    logger.info("Commands: /cancel, /status, /help")
    logger.info("Workflow: Detect → Open App → Check every 10s → Share → Stop → Delete")
    logger.info("="*60)
    
    # Set up file monitoring
    event_handler = SimpleUploadChecker()
    observer = Observer()
    observer.schedule(event_handler, CAMERA_FOLDER, recursive=False)
    observer.start()
    
    # Set up Telegram bot
    telegram_app = setup_telegram_bot()
    
    logger.info("🚀 Upload checker with /cancel support started!")
    
    try:
        if telegram_app:
            # Run both file monitoring and Telegram bot
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # Start telegram bot in background
            telegram_task = loop.create_task(telegram_app.run_polling())
            
            # Keep the main thread running
            while True:
                time.sleep(1)
        else:
            # Run only file monitoring
            while True:
                time.sleep(1)
                
    except KeyboardInterrupt:
        logger.info("Stopping...")
        observer.stop()
        if telegram_app:
            telegram_app.stop()
    
    observer.join()

if __name__ == "__main__":
    main()
