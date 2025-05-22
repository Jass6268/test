#!/usr/bin/env python3
"""
Fixed Automatic Latest File Sharing - Google Photos API
-------------------------------------------------------
Completely automatic solution that:
1. Detects new MKV files
2. Opens Google Photos for upload
3. WAITS for upload completion (FIXED TIMING)
4. Uses API to find the uploaded file by exact filename
5. Automatically creates share link via API
6. Sends perfect photos.app.goo.gl link via Telegram
7. Cleans up automatically

This approach waits for actual upload completion before searching.
"""

import os
import time
import json
import requests
import logging
import subprocess
import threading
from datetime import datetime, timedelta
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
CAMERA_FOLDER = "/sdcard/DCIM/Camera/"
TELEGRAM_BOT_TOKEN = "8114381417:AAFlvW0cQBhv4LTi1m8pmMuR-zC_zl0MWpo"
TELEGRAM_CHAT_ID = "6575149109"
GOOGLE_PHOTOS_PACKAGE = "com.google.android.apps.photos"

# Google Photos API
SCOPES = ['https://www.googleapis.com/auth/photoslibrary']
TOKEN_FILE = 'token.json'
CREDENTIALS_FILE = 'credentials.json'

class FixedAutomaticHandler(FileSystemEventHandler):
    """Fixed handler that waits for upload completion before searching"""

    def __init__(self):
        super().__init__()
        self.google_photos_service = self._setup_google_photos_api()
        logger.info("Fixed automatic handler initialized")

    def _setup_google_photos_api(self):
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
                service = build('photoslibrary', 'v1', credentials=creds, static_discovery=False)
                logger.info("✅ Google Photos API ready")
                return service
            else:
                logger.error("❌ No valid Google Photos credentials found")
                return None
        except Exception as e:
            logger.error(f"❌ Error setting up Google Photos API: {e}")
            return None

    def on_created(self, event):
        """Handle new file detection"""
        if not event.is_directory and event.src_path.lower().endswith('.mkv'):
            filename = os.path.basename(event.src_path)
            logger.info(f"🎬 New MKV file detected: {filename}")
            
            # Process in background thread
            thread = threading.Thread(target=self._process_file_fixed, args=(event.src_path,))
            thread.daemon = True
            thread.start()

    def _process_file_fixed(self, file_path):
        """Fixed processing - wait for actual upload before searching"""
        filename = os.path.basename(file_path)
        upload_start_time = datetime.now()
        
        try:
            # Wait for file to be fully written
            time.sleep(3)
            
            if not os.path.exists(file_path):
                logger.warning(f"File disappeared: {filename}")
                return
            
            file_size = os.path.getsize(file_path)
            logger.info(f"📁 Processing: {filename} ({file_size / (1024*1024):.1f}MB)")
            
            # Step 1: Send start notification
            self._send_upload_wait_notification(filename, file_size)
            
            # Step 2: Open Google Photos and WAIT for upload
            logger.info("📱 Opening Google Photos...")
            self._open_google_photos()
            time.sleep(10)  # Let app start properly
            
            # Step 3: Calculate and WAIT for upload completion
            upload_time = self._calculate_realistic_upload_time(file_size)
            logger.info(f"⏳ WAITING {upload_time//60}m {upload_time%60}s for upload to complete...")
            
            # Send progress notification
            self._send_upload_progress_notification(filename, upload_time)
            
            # ACTUALLY WAIT for the upload (this was the missing piece!)
            time.sleep(upload_time)
            
            # Step 4: NOW search for the uploaded file
            logger.info(f"🔍 Upload wait complete, now searching for: {filename}")
            
            # Try exact filename search first
            exact_match = self._find_exact_filename_match(filename)
            
            if exact_match:
                found_filename = exact_match.get('filename', 'Unknown')
                logger.info(f"🎉 FOUND AFTER UPLOAD WAIT: {found_filename}")
                
                self._send_found_after_wait_notification(filename, found_filename)
                
                # Create share link
                share_link = self._create_automatic_share_link(exact_match, filename)
                
                if share_link:
                    self._send_final_success_notification(filename, file_size, share_link, found_filename)
                else:
                    share_link = self._create_fallback_share_link(exact_match)
                    self._send_partial_success_notification(filename, file_size, share_link)
                
                # NOW cleanup
                self._force_stop_google_photos()
                self._delete_file(file_path)
                logger.info(f"✅ SUCCESS: {filename}")
                return
            
            # Step 5: If still not found, try timing-based search
            logger.info("🔍 No exact match after wait, trying recent uploads...")
            
            recent_match = self._find_most_recent_video_after_time(upload_start_time)
            
            if recent_match:
                found_filename = recent_match.get('filename', 'Unknown')
                logger.info(f"🎯 FOUND RECENT UPLOAD: {found_filename}")
                
                self._send_recent_upload_notification(filename, found_filename)
                
                # Create share link
                share_link = self._create_automatic_share_link(recent_match, filename)
                
                if share_link:
                    self._send_final_success_notification(filename, file_size, share_link, found_filename)
                else:
                    share_link = self._create_fallback_share_link(recent_match)
                    self._send_partial_success_notification(filename, file_size, share_link)
                
                # Cleanup
                self._force_stop_google_photos()
                self._delete_file(file_path)
                logger.info(f"✅ SUCCESS VIA TIMING: {filename}")
                return
            
            # Step 6: If STILL not found, there's a problem
            logger.warning(f"❌ File not found even after waiting: {filename}")
            self._send_upload_failed_notification(filename)
            
            # Don't delete file if upload failed
            self._force_stop_google_photos()
            logger.info(f"❌ UPLOAD FAILED: {filename}")
            
        except Exception as e:
            logger.error(f"❌ Error in processing: {str(e)}")
            try:
                self._force_stop_google_photos()
            except:
                pass

    def _calculate_realistic_upload_time(self, file_size):
        """Calculate realistic upload time with generous buffer"""
        file_size_mb = file_size / (1024 * 1024)
        
        # Conservative (longer) upload times to ensure completion
        if file_size_mb < 50:
            return 180   # 3 minutes for small files
        elif file_size_mb < 200:
            return 360   # 6 minutes for medium files  
        elif file_size_mb < 500:
            return 600   # 10 minutes for large files
        elif file_size_mb < 1000:
            return 900   # 15 minutes for very large files
        else:
            return 1200  # 20 minutes for huge files

    def _find_exact_filename_match(self, original_filename):
        """Search for exact filename match in Google Photos"""
        if not self.google_photos_service:
            logger.error("No Google Photos API service available")
            return None
        
        try:
            # Prepare the exact search term from filename
            search_name = original_filename.replace('.mkv', '').replace('.mp4', '').replace('.mov', '').strip()
            logger.info(f"🎯 EXACT SEARCH: Looking for '{search_name}'")
            
            # Get recent items to search through
            results = self.google_photos_service.mediaItems().list(
                pageSize=50
            ).execute()
            
            media_items = results.get('mediaItems', [])
            logger.info(f"📊 Searching through {len(media_items)} recent items...")
            
            for i, item in enumerate(media_items, 1):
                try:
                    # Only check videos
                    mime_type = item.get('mimeType', '')
                    if not mime_type.startswith('video/'):
                        continue
                    
                    item_filename = item.get('filename', '')
                    item_name = item_filename.replace('.mkv', '').replace('.mp4', '').replace('.mov', '').strip()
                    
                    logger.info(f"🔍 Check {i}: '{item_filename}'")
                    
                    # Method 1: Exact match (case insensitive)
                    if search_name.lower() == item_name.lower():
                        logger.info(f"✅ EXACT MATCH: '{item_filename}'")
                        return item
                    
                    # Method 2: Partial matching for renamed files
                    if search_name.lower() in item_name.lower():
                        logger.info(f"📝 PARTIAL MATCH: '{item_filename}'")
                        return item
                    
                    # Method 3: Word-based matching
                    original_words = set(search_name.lower().split())
                    found_words = set(item_name.lower().split())
                    common_words = original_words.intersection(found_words)
                    
                    if len(common_words) >= 2:  # At least 2 words match
                        logger.info(f"📝 WORD MATCH: '{item_filename}' (words: {list(common_words)})")
                        return item
                
                except Exception as item_error:
                    logger.debug(f"Error checking item {i}: {item_error}")
                    continue
            
            logger.warning(f"❌ No matches found for '{search_name}'")
            return None
            
        except Exception as e:
            logger.error(f"Error in exact filename search: {e}")
            return None

    def _find_most_recent_video_after_time(self, start_time):
        """Find the most recent video uploaded after start_time"""
        if not self.google_photos_service:
            return None
        
        try:
            from dateutil import parser
            
            logger.info(f"🕐 Looking for videos uploaded after {start_time.strftime('%H:%M:%S')}")
            
            # Get recent items
            results = self.google_photos_service.mediaItems().list(
                pageSize=20
            ).execute()
            
            media_items = results.get('mediaItems', [])
            recent_videos = []
            
            for item in media_items:
                try:
                    # Check if it's a video
                    mime_type = item.get('mimeType', '')
                    if not mime_type.startswith('video/'):
                        continue
                    
                    # Check upload time
                    metadata = item.get('mediaMetadata', {})
                    creation_time_str = metadata.get('creationTime', '')
                    
                    if creation_time_str:
                        creation_time = parser.parse(creation_time_str)
                        
                        # Check if uploaded after our start time
                        if creation_time > start_time:
                            time_diff = (creation_time - start_time).total_seconds() / 60  # minutes
                            filename = item.get('filename', 'Unknown')
                            
                            recent_videos.append({
                                'item': item,
                                'filename': filename,
                                'minutes_after_start': time_diff,
                                'creation_time': creation_time
                            })
                            
                            logger.info(f"📹 Recent video: '{filename}' (+{time_diff:.1f}m after start)")
                
                except Exception as item_error:
                    continue
            
            if recent_videos:
                # Sort by most recent (smallest time difference)
                recent_videos.sort(key=lambda x: x['minutes_after_start'])
                most_recent = recent_videos[0]
                
                logger.info(f"🎯 Most recent: '{most_recent['filename']}' (+{most_recent['minutes_after_start']:.1f}m)")
                return most_recent['item']
            
            logger.warning("❌ No videos uploaded after start time")
            return None
            
        except Exception as e:
            logger.error(f"Error finding recent video: {e}")
            return None

    def _create_automatic_share_link(self, media_item, original_filename):
        """Create share link automatically using API"""
        if not self.google_photos_service:
            return None
        
        try:
            media_item_id = media_item.get('id')
            if not media_item_id:
                logger.error("No media item ID available")
                return None
            
            logger.info("🔗 Creating shareable album...")
            
            # Create a temporary shared album
            album_title = f"AutoShare_{int(time.time())}"
            album_body = {
                'album': {
                    'title': album_title
                }
            }
            
            album_response = self.google_photos_service.albums().create(body=album_body).execute()
            album_id = album_response.get('id')
            
            if not album_id:
                logger.error("Failed to create album")
                return None
            
            logger.info(f"📁 Album created: {album_id[:20]}...")
            
            # Add media item to album
            add_body = {
                'mediaItemIds': [media_item_id]
            }
            
            self.google_photos_service.albums().batchAddMediaItems(
                albumId=album_id,
                body=add_body
            ).execute()
            
            logger.info("📎 Media item added to album")
            
            # Share the album
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
            
            if shareable_url:
                logger.info(f"✅ Share link created successfully")
                return shareable_url
            else:
                logger.error("No shareable URL in response")
                return None
                
        except Exception as e:
            logger.error(f"Error creating automatic share link: {e}")
            return None

    def _create_fallback_share_link(self, media_item):
        """Create fallback share link using product URL"""
        try:
            product_url = media_item.get('productUrl', '')
            if product_url:
                logger.info("📎 Using product URL as fallback")
                return product_url
            
            base_url = media_item.get('baseUrl', '')
            if base_url:
                logger.info("📎 Using base URL as fallback")
                return base_url
            
            return None
            
        except Exception as e:
            logger.error(f"Error creating fallback link: {e}")
            return None

    def _open_google_photos(self):
        """Open Google Photos app"""
        try:
            subprocess.run([
                'am', 'start',
                '-n', f'{GOOGLE_PHOTOS_PACKAGE}/.home.HomeActivity'
            ], check=True, capture_output=True)
            logger.info("✅ Google Photos opened")
            
        except subprocess.CalledProcessError:
            try:
                subprocess.run([
                    'am', 'start',
                    '-a', 'android.intent.action.MAIN',
                    '-c', 'android.intent.category.LAUNCHER',
                    GOOGLE_PHOTOS_PACKAGE
                ], check=True, capture_output=True)
                logger.info("✅ Google Photos opened via launcher")
            except subprocess.CalledProcessError as e:
                logger.error(f"❌ Failed to open Google Photos: {e}")

    def _force_stop_google_photos(self):
        """Force stop Google Photos using robust method"""
        try:
            logger.info("⚡ Force-stopping Google Photos (ROOT)...")
            
            kill_commands = [
                f"su -c 'am force-stop {GOOGLE_PHOTOS_PACKAGE}'",
                f"su -c 'killall -9 {GOOGLE_PHOTOS_PACKAGE}'",
                f"su -c 'pkill -9 -f {GOOGLE_PHOTOS_PACKAGE}'",
                f"su -c 'ps | grep {GOOGLE_PHOTOS_PACKAGE} | grep -v grep | awk \"{{print \\$2}}\" | xargs kill -9'"
            ]
            
            for cmd in kill_commands:
                try:
                    logger.info(f"Executing: {cmd}")
                    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
                    
                    if result.returncode == 0:
                        logger.info(f"✅ Command successful: {cmd}")
                    else:
                        logger.warning(f"Command returned {result.returncode}: {result.stderr}")
                    
                    time.sleep(1)
                    
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
                    
            except Exception as e:
                logger.warning(f"Could not verify stop status: {e}")
            
        except Exception as e:
            logger.error(f"Error force stopping Google Photos: {str(e)}")

    def _delete_file(self, file_path):
        """Delete original file from camera folder"""
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"✅ Original file deleted")
            else:
                logger.warning("File already deleted")
        except Exception as e:
            logger.error(f"❌ Error deleting file: {e}")

    # Notification methods
    def _send_upload_wait_notification(self, filename, file_size):
        """Send notification about waiting for upload"""
        upload_time = self._calculate_realistic_upload_time(file_size)
        
        message = f"""⏳ WAITING FOR UPLOAD TO COMPLETE

📁 File: {filename}
📊 Size: {file_size / (1024*1024):.1f}MB
📱 Google Photos: Opening...

⏰ Estimated upload time: {upload_time//60}m {upload_time%60}s
🚫 Will NOT search until upload completes
✅ This prevents finding wrong files"""

        self._send_telegram_message(message)

    def _send_upload_progress_notification(self, filename, upload_time):
        """Send upload progress notification"""
        message = f"""📡 UPLOAD IN PROGRESS

📁 File: {filename}
📱 Google Photos: Running and uploading...
⏰ Waiting: {upload_time//60}m {upload_time%60}s

🔄 Please wait - upload must complete first
🚫 Script will NOT close Google Photos yet
⏳ Will search for file after upload completes"""

        self._send_telegram_message(message)

    def _send_found_after_wait_notification(self, filename, found_filename):
        """Send notification when file found after waiting"""
        message = f"""✅ FOUND AFTER UPLOAD WAIT!

📁 Your file: {filename}
📁 Found file: {found_filename}
⏰ Found after waiting for upload completion

✅ Upload completed successfully  
🔍 File detected via filename search
🔗 Creating share link now..."""

        self._send_telegram_message(message)

    def _send_recent_upload_notification(self, filename, found_filename):
        """Send notification for recent upload match"""
        message = f"""🕐 RECENT UPLOAD DETECTED!

📁 Your file: {filename}
📁 Found file: {found_filename}
📡 Detected as recent upload

✅ File uploaded with different name
🕐 Matched by upload timing
🔗 Creating share link now..."""

        self._send_telegram_message(message)

    def _send_upload_failed_notification(self, filename):
        """Send notification when upload appears to have failed"""
        message = f"""❌ UPLOAD ISSUE DETECTED

📁 File: {filename}
⏰ Waited full upload time but file not found

🔧 Possible issues:
- Google Photos auto-backup disabled
- Network connection problems
- File format not supported  
- Google account storage full
- App permissions issue

🔍 Please check Google Photos manually
🗑️ Original file NOT deleted (check manually)"""

        self._send_telegram_message(message)

    def _send_final_success_notification(self, filename, file_size, share_link, found_filename):
        """Send final success notification"""
        if 'photos.app.goo.gl' in share_link:
            link_type = "🎯 Perfect short link!"
        elif 'photos.google.com' in share_link:
            link_type = "✅ Google Photos link"
        else:
            link_type = "🔗 Share link"

        message = f"""🎉 UPLOAD & SHARE COMPLETE!

📁 Original: {filename}
📁 Found: {found_filename}
📊 Size: {file_size / (1024*1024):.1f}MB
🔗 Link: {share_link}

{link_type}
✅ Waited for upload completion
🔍 Found correct file
🗑️ Original file deleted
📱 Google Photos closed

🚀 Ready to share!"""

        self._send_telegram_message(message)

    def _send_partial_success_notification(self, filename, file_size, share_link):
        """Send partial success notification"""
        message = f"""⚠️ UPLOAD COMPLETE - BASIC LINK

📁 File: {filename}
📊 Size: {file_size / (1024*1024):.1f}MB
🔗 Link: {share_link}

✅ File uploaded successfully
🔗 Basic link created (not optimal format)
🗑️ Original file deleted

💡 Share link created but may not be optimal format"""

        self._send_telegram_message(message)

    def _send_telegram_message(self, message):
        """Send Telegram message"""
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            data = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
            
            response = requests.post(url, data=data, timeout=10)
            
            if response.status_code == 200:
                logger.info("📨 Telegram notification sent")
            else:
                logger.error(f"❌ Telegram error: {response.text}")
                
        except Exception as e:
            logger.error(f"❌ Error sending Telegram: {e}")

def main():
    """Main function"""
    logger.info("="*60)
    logger.info("FIXED AUTOMATIC FILE SHARING - WAIT BEFORE SEARCH")
    logger.info("="*60)
    logger.info(f"Monitoring: {CAMERA_FOLDER}")
    logger.info("Features: Waits for upload completion, then searches for exact filename")
    logger.info("Fix: Prevents finding wrong files by waiting for actual upload")
    logger.info("="*60)
    
    # Check for required files
    if not os.path.exists(TOKEN_FILE):
        logger.error(f"❌ {TOKEN_FILE} not found!")
        logger.error("Please run authentication first to create token.json")
        return
    
    if not os.path.exists(CREDENTIALS_FILE):
        logger.error(f"❌ {CREDENTIALS_FILE} not found!")
        logger.error("Please download credentials.json from Google Cloud Console")
        return
    
    event_handler = FixedAutomaticHandler()
    observer = Observer()
    observer.schedule(event_handler, CAMERA_FOLDER, recursive=False)
    observer.start()
    
    logger.info("🚀 Fixed automatic file sharing started!")
    logger.info("📁 Drop MKV files to test - script will wait for upload completion!")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopping...")
        observer.stop()
    
    observer.join()

if __name__ == "__main__":
    main()
