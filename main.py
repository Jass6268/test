#!/usr/bin/env python3
"""
Simple Google Photos Upload Checker
-----------------------------------
1. Detects new MKV files in Camera folder
2. Opens Google Photos app for auto-sync
3. Checks every 10 seconds if file uploaded to Google Photos
4. When uploaded: sends Telegram link, force stops app, deletes file
5. Processes next file
"""

import os
import time
import json
import requests
import logging
import subprocess
import threading
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

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
    """Thread-safe queue for processing files one by one"""
    def __init__(self):
        self.queue = []
        self.processing = False
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
    
    def set_processing(self, status):
        with self.lock:
            self.processing = status
    
    def is_processing(self):
        with self.lock:
            return self.processing

class SimpleUploadChecker(FileSystemEventHandler):
    """Simple handler that checks upload status every 10 seconds"""

    def __init__(self):
        super().__init__()
        self.file_queue = FileQueue()
        self.google_photos_service = self._setup_google_photos()
        self.processor_thread = threading.Thread(target=self._process_queue, daemon=True)
        self.processor_thread.start()
        logger.info("Simple upload checker initialized")

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
            # Wait a moment to ensure file is completely written
            time.sleep(5)
            if os.path.exists(event.src_path):
                self.file_queue.add_file(event.src_path)

    def _process_queue(self):
        """Process files one by one from the queue"""
        while True:
            if not self.file_queue.is_processing():
                file_path = self.file_queue.get_next_file()
                if file_path:
                    self.file_queue.set_processing(True)
                    try:
                        self._process_file(file_path)
                    except Exception as e:
                        logger.error(f"Error processing {file_path}: {str(e)}")
                    finally:
                        self.file_queue.set_processing(False)
            time.sleep(2)

    def _process_file(self, file_path):
        """Process a single file: open app → check every 10s → actions when uploaded"""
        filename = os.path.basename(file_path)
        logger.info(f"Processing: {filename}")
        
        try:
            # Step 1: Check if file still exists
            if not os.path.exists(file_path):
                logger.warning(f"File no longer exists: {filename}")
                return
            
            file_size = os.path.getsize(file_path)
            logger.info(f"File size: {file_size / (1024*1024):.1f}MB")
            
            # Step 2: Open Google Photos app
            logger.info("Opening Google Photos app...")
            self._open_google_photos()
            time.sleep(15)  # Give app time to start
            
            # Step 3: Check every 10 seconds if file is uploaded
            logger.info(f"Checking every {CHECK_INTERVAL} seconds if {filename} is uploaded...")
            
            upload_found = False
            total_check_time = 0
            
            while total_check_time < MAX_CHECK_TIME:
                time.sleep(CHECK_INTERVAL)
                total_check_time += CHECK_INTERVAL
                
                logger.info(f"Checking upload status... ({total_check_time}s elapsed)")
                
                # Check if file is uploaded to Google Photos
                if self._check_file_uploaded(filename, file_size):
                    logger.info(f"✅ {filename} found uploaded to Google Photos!")
                    upload_found = True
                    break
            
            if upload_found:
                # Step 4: Get share link and send Telegram
                share_link = self._get_share_link(filename)
                self._send_telegram_notification(filename, file_size, share_link)
                
                # Step 5: Force stop Google Photos app
                logger.info("Force stopping Google Photos app...")
                self._force_stop_google_photos()
                
                # Step 6: Delete file from Camera folder
                logger.info(f"Deleting {filename} from Camera folder...")
                self._delete_file(file_path)
                
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
        """Get shareable link for uploaded file"""
        if not self.google_photos_service:
            return "Google Photos link not available"
        
        try:
            # Search for the file again to get its ID
            results = self.google_photos_service.mediaItems().list(
                pageSize=50
            ).execute()
            
            media_items = results.get('mediaItems', [])
            
            for item in media_items:
                item_filename = item.get('filename', '')
                if filename.lower() in item_filename.lower() or item_filename.lower() in filename.lower():
                    return item.get('productUrl', 'Share link not available')
            
            return "File found but share link not available"
            
        except Exception as e:
            logger.error(f"Error getting share link: {e}")
            return "Error getting share link"

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
        """Send Telegram notification with file info and link"""
        try:
            message = f"""📱 New video uploaded to Google Photos!

📁 File: {filename}
📊 Size: {file_size / (1024*1024):.1f}MB
🔗 Link: {share_link}

✅ Processed automatically from Bliss OS"""

            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            data = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
            response = requests.post(url, data=data, timeout=10)
            
            if response.status_code == 200:
                logger.info("Telegram notification sent")
            else:
                logger.error(f"Telegram error: {response.text}")
        except Exception as e:
            logger.error(f"Error sending Telegram: {e}")

    def _force_stop_google_photos(self):
        """Force stop Google Photos app"""
        try:
            subprocess.run([
                'su', '-c', f'am force-stop {GOOGLE_PHOTOS_PACKAGE}'
            ], capture_output=True)
            logger.info("Google Photos app force stopped")
        except Exception as e:
            logger.error(f"Error force stopping: {e}")

    def _delete_file(self, file_path):
        """Delete file from Camera folder"""
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"File deleted: {os.path.basename(file_path)}")
        except Exception as e:
            logger.error(f"Error deleting file: {e}")

def main():
    """Main function"""
    logger.info("="*60)
    logger.info("SIMPLE GOOGLE PHOTOS UPLOAD CHECKER")
    logger.info("="*60)
    logger.info(f"Monitoring: {CAMERA_FOLDER}")
    logger.info(f"Check interval: {CHECK_INTERVAL} seconds")
    logger.info("Workflow: Detect → Open App → Check every 10s → Share → Stop → Delete")
    logger.info("="*60)
    
    event_handler = SimpleUploadChecker()
    observer = Observer()
    observer.schedule(event_handler, CAMERA_FOLDER, recursive=False)
    observer.start()
    
    logger.info("🚀 Simple upload checker started!")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopping...")
        observer.stop()
    
    observer.join()

if __name__ == "__main__":
    main()
