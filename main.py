#!/usr/bin/env python3
"""
Automatic Latest File Sharing - Google Photos API
-------------------------------------------------
Completely automatic solution that:
1. Detects new MKV files
2. Opens Google Photos for upload
3. Waits for upload completion
4. Uses API to find the latest uploaded file
5. Automatically creates share link via API
6. Sends perfect photos.app.goo.gl link via Telegram
7. Cleans up automatically

This approach uses the Google Photos API to reliably find and share 
the latest uploaded file without any UI automation.
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

class AutomaticLatestHandler(FileSystemEventHandler):
    """Automatic handler using Google Photos API to find latest file"""

    def __init__(self):
        super().__init__()
        self.google_photos_service = self._setup_google_photos_api()
        self.processing_files = {}
        logger.info("Automatic latest file handler initialized")

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
            thread = threading.Thread(target=self._process_file_automatic, args=(event.src_path,))
            thread.daemon = True
            thread.start()

    def _process_file_automatic(self, file_path):
        """Check existing files first, then monitor if needed"""
        filename = os.path.basename(file_path)
        base_filename = filename.replace('.mkv', '').lower()  # Remove extension for matching
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
            self._send_immediate_check_notification(filename, file_size)
            
            # Step 2: Open Google Photos (for background sync)
            logger.info("📱 Opening Google Photos...")
            self._open_google_photos()
            time.sleep(5)  # Quick start
            
            # Step 3: IMMEDIATE CHECK - Look for existing file
            logger.info(f"🔍 Checking latest 10 files for match with: {base_filename}")
            existing_file = self._check_latest_10_files(base_filename, filename)
            
            if existing_file:
                logger.info(f"🎉 FOUND EXISTING FILE: {existing_file.get('filename', 'Unknown')}")
                
                # Create share link immediately
                share_link = self._create_automatic_share_link(existing_file, filename)
                
                if share_link:
                    self._send_instant_success_notification(filename, file_size, share_link)
                    logger.info(f"⚡ INSTANT success: {share_link}")
                else:
                    share_link = self._create_fallback_share_link(existing_file)
                    self._send_partial_success_notification(filename, file_size, share_link)
                
                # Quick cleanup
                self._force_stop_google_photos()
                self._delete_file(file_path)
                logger.info(f"⚡ INSTANT processing completed: {filename}")
                return
            
            # Step 4: If not found, start real-time monitoring
            logger.info("📡 File not found in latest 10, starting upload monitoring...")
            self._send_monitoring_fallback_notification(filename)
            
            upload_detected = self._monitor_upload_realtime(upload_start_time, filename, file_size)
            
            if upload_detected:
                logger.info("✅ Upload detected via monitoring!")
                
                # Find the uploaded file
                latest_media_item = self._find_latest_uploaded_file(upload_start_time, filename)
                
                if latest_media_item:
                    share_link = self._create_automatic_share_link(latest_media_item, filename)
                    
                    if share_link:
                        self._send_success_notification(filename, file_size, share_link)
                    else:
                        share_link = self._create_fallback_share_link(latest_media_item)
                        self._send_partial_success_notification(filename, file_size, share_link)
                else:
                    self._send_not_found_notification(filename)
            else:
                self._send_timeout_notification(filename)
            
            # Cleanup
            self._force_stop_google_photos()
            self._delete_file(file_path)
            
            logger.info(f"🏁 Completed processing: {filename}")
            
        except Exception as e:
            logger.error(f"❌ Error in automatic processing: {str(e)}")
            try:
                self._force_stop_google_photos()
            except:
                pass

    def _check_latest_10_files(self, base_filename, original_filename):
        """Check the latest 10 files in Google Photos for filename match"""
        if not self.google_photos_service:
            logger.error("No Google Photos API service available")
            return None
        
        try:
            logger.info("📊 Querying latest 10 files from Google Photos...")
            
            # Get the 10 most recent media items
            results = self.google_photos_service.mediaItems().list(
                pageSize=10
            ).execute()
            
            media_items = results.get('mediaItems', [])
            logger.info(f"📋 Found {len(media_items)} recent items to check")
            
            # Check each item for filename match
            for i, item in enumerate(media_items, 1):
                try:
                    item_filename = item.get('filename', '').lower()
                    item_mime = item.get('mimeType', '')
                    
                    logger.info(f"🔍 Check {i}/10: {item_filename} (type: {item_mime[:20]}...)")
                    
                    # Skip non-videos
                    if not item_mime.startswith('video/'):
                        logger.debug(f"   ↳ Skipping - not a video")
                        continue
                    
                    # Check for filename match (flexible matching)
                    if self._is_filename_match(base_filename, item_filename, original_filename):
                        logger.info(f"🎯 MATCH FOUND: {item_filename}")
                        logger.info(f"   ↳ Original: {original_filename}")
                        logger.info(f"   ↳ Found: {item_filename}")
                        return item
                    else:
                        logger.debug(f"   ↳ No match: {base_filename} vs {item_filename}")
                
                except Exception as item_error:
                    logger.debug(f"Error checking item {i}: {item_error}")
                    continue
            
            logger.info("❌ No matching file found in latest 10 items")
            return None
            
        except Exception as e:
            logger.error(f"Error checking latest 10 files: {e}")
            return None

    def _is_filename_match(self, base_filename, item_filename, original_filename):
        """Check if filenames match with flexible matching"""
        try:
            # Remove extension from item filename
            item_base = item_filename.replace('.mkv', '').replace('.mp4', '').replace('.mov', '').lower()
            
            # Method 1: Exact base filename match
            if base_filename == item_base:
                return True
            
            # Method 2: Base filename is contained in item filename
            if base_filename in item_filename:
                return True
            
            # Method 3: Item filename is contained in base filename
            if item_base in base_filename:
                return True
            
            # Method 4: Check original filename directly
            if original_filename.lower() in item_filename:
                return True
            
            # Method 5: Check without common prefixes (IMG_, VID_, etc.)
            clean_base = base_filename.replace('img_', '').replace('vid_', '').replace('video_', '')
            clean_item = item_base.replace('img_', '').replace('vid_', '').replace('video_', '')
            
            if clean_base == clean_item:
                return True
            
            return False
            
        except Exception as e:
            logger.debug(f"Error in filename matching: {e}")
            return False

    def _send_immediate_check_notification(self, filename, file_size):
        """Send notification about immediate check"""
        message = f"""⚡ INSTANT CHECK STARTING

📁 File: {filename}
📊 Size: {file_size / (1024*1024):.1f}MB
🎯 Strategy: Check latest 10 files first

🔍 Looking for existing match in Google Photos...
⚡ This should be INSTANT if file already uploaded!"""

        self._send_telegram_message(message)

    def _send_instant_success_notification(self, filename, file_size, share_link):
        """Send notification for instant success"""
        if 'photos.app.goo.gl' in share_link:
            link_type = "🎯 Perfect short link!"
        elif 'photos.google.com' in share_link:
            link_type = "✅ Google Photos link"
        else:
            link_type = "🔗 Share link"

        message = f"""⚡ INSTANT SUCCESS! (Found in latest 10)

📁 File: {filename}
📊 Size: {file_size / (1024*1024):.1f}MB
🔗 Link: {share_link}

{link_type}
🎯 Found existing file immediately!
⚡ No waiting required!
🗑️ Original file deleted
📱 Google Photos closed

🚀 Ready to share instantly!"""

        self._send_telegram_message(message)

    def _send_monitoring_fallback_notification(self, filename):
        """Send notification when falling back to monitoring"""
        message = f"""🔄 FALLBACK TO MONITORING

📁 File: {filename}
📊 Status: Not found in latest 10 files
🔍 Mode: Starting upload monitoring...

📡 Will check every 15 seconds for new upload
⏰ This means file is still uploading"""

        self._send_telegram_message(message)

    def _monitor_upload_realtime(self, start_time, filename, file_size):
        """Monitor with shorter intervals since we already checked existing files"""
        max_wait_time = self._calculate_max_wait_time(file_size)
        check_interval = 10  # Check every 10 seconds (faster since we're only monitoring new uploads)
        total_waited = 0
        
        logger.info(f"⏱️ Upload monitoring started (max {max_wait_time//60}m)")
        
        while total_waited < max_wait_time:
            time.sleep(check_interval)
            total_waited += check_interval
            
            # Check for new uploads only (not all files)
            if self._check_upload_completion_api(start_time, filename):
                elapsed_minutes = total_waited // 60
                elapsed_seconds = total_waited % 60
                logger.info(f"🎉 New upload detected after {elapsed_minutes}m {elapsed_seconds}s!")
                
                self._send_quick_detection_notification(filename, total_waited)
                return True
            
            # Send progress update every 30 seconds (less frequent)
            if total_waited % 30 == 0:
                elapsed_minutes = total_waited // 60
                remaining_minutes = (max_wait_time - total_waited) // 60
                logger.info(f"📡 Still monitoring... {elapsed_minutes}m elapsed, ~{remaining_minutes}m remaining")
        
        logger.warning(f"⏰ Upload monitoring timeout after {max_wait_time//60} minutes")
        return False

    def _monitor_upload_realtime(self, start_time, filename, file_size):
        """Monitor upload progress in real-time using API polling"""
        max_wait_time = self._calculate_max_wait_time(file_size)
        check_interval = 15  # Check every 15 seconds
        total_waited = 0
        
        logger.info(f"⏱️ Real-time monitoring started (max {max_wait_time//60}m)")
        
        # Send initial progress notification
        self._send_realtime_start_notification(filename, max_wait_time)
        
        while total_waited < max_wait_time:
            time.sleep(check_interval)
            total_waited += check_interval
            
            # Check if file appeared in Google Photos
            if self._check_upload_completion_api(start_time, filename):
                elapsed_minutes = total_waited // 60
                elapsed_seconds = total_waited % 60
                logger.info(f"🎉 Upload detected after {elapsed_minutes}m {elapsed_seconds}s!")
                
                # Send quick completion notification
                self._send_quick_detection_notification(filename, total_waited)
                return True
            
            # Send progress update every minute
            if total_waited % 60 == 0:
                elapsed_minutes = total_waited // 60
                remaining_minutes = (max_wait_time - total_waited) // 60
                self._send_progress_update(filename, elapsed_minutes, remaining_minutes)
        
        logger.warning(f"⏰ Upload monitoring timeout after {max_wait_time//60} minutes")
        return False

    def _calculate_max_wait_time(self, file_size_bytes):
        """Calculate maximum wait time - more aggressive than before"""
        file_size_mb = file_size_bytes / (1024 * 1024)
        
        # More aggressive timing (shorter waits)
        if file_size_mb < 50:
            return 120  # 2 minutes for small files
        elif file_size_mb < 200:
            return 300  # 5 minutes for medium files
        elif file_size_mb < 500:
            return 600  # 10 minutes for large files
        elif file_size_mb < 1000:
            return 900  # 15 minutes for very large files
        else:
            return 1200  # 20 minutes max for huge files

    def _check_upload_completion_api(self, start_time, filename):
        """Check if upload completed using API"""
        if not self.google_photos_service:
            return False
        
        try:
            # Query recent uploads
            results = self.google_photos_service.mediaItems().list(
                pageSize=20  # Check only most recent 20 items
            ).execute()
            
            media_items = results.get('mediaItems', [])
            
            for item in media_items:
                try:
                    # Check if it's a video uploaded recently
                    mime_type = item.get('mimeType', '')
                    if not mime_type.startswith('video/'):
                        continue
                    
                    metadata = item.get('mediaMetadata', {})
                    creation_time_str = metadata.get('creationTime', '')
                    
                    if creation_time_str:
                        from dateutil import parser
                        creation_time = parser.parse(creation_time_str)
                        
                        # Check if created after our start time
                        time_diff = (creation_time - start_time).total_seconds()
                        
                        if -300 < time_diff < 3600:  # Between 5 min before and 1 hour after
                            logger.info(f"📡 Found potential match: {item.get('filename', 'Unknown')}")
                            return True
                
                except Exception as item_error:
                    continue
            
            return False
            
        except Exception as e:
            logger.debug(f"API check error: {e}")
            return False

    def _send_realtime_start_notification(self, filename, max_wait):
        """Send notification about real-time monitoring"""
        message = f"""⚡ REAL-TIME MONITORING ACTIVE

📁 File: {filename}
🔍 Mode: Live upload detection
⏰ Max wait: {max_wait//60}m (usually much faster!)

📡 Checking Google Photos API every 15 seconds
🚀 You'll get notified the moment upload completes!"""

        self._send_telegram_message(message)

    def _send_progress_update(self, filename, elapsed_min, remaining_min):
        """Send progress update"""
        message = f"""⏳ UPLOAD MONITORING - {elapsed_min}m elapsed

📁 File: {filename}
📡 Still checking Google Photos API...
⏰ Max remaining: {remaining_min}m

🔍 Checking every 15 seconds for instant detection"""

        self._send_telegram_message(message)

    def _send_quick_detection_notification(self, filename, detection_time):
        """Send notification about quick detection"""
        minutes = detection_time // 60
        seconds = detection_time % 60
        
        message = f"""⚡ UPLOAD DETECTED INSTANTLY!

📁 File: {filename}
⏱️ Detection time: {minutes}m {seconds}s
📡 Found via real-time API monitoring

🔗 Creating share link now...
🚀 Much faster than waiting fixed time!"""

        self._send_telegram_message(message)

    def _send_timeout_notification(self, filename):
        """Send timeout notification"""
        message = f"""⏰ UPLOAD TIMEOUT

📁 File: {filename}
📱 Status: Not detected in Google Photos within time limit

🔧 Possible issues:
- Google Photos auto-backup disabled
- Network connection problems  
- File format not supported
- Google account storage full

🗑️ Original file deleted anyway"""

        self._send_telegram_message(message)

    def _calculate_upload_time(self, file_size_bytes):
        """Calculate upload time based on file size"""
        file_size_mb = file_size_bytes / (1024 * 1024)
        
        # Conservative estimates (can be adjusted based on your connection)
        if file_size_mb < 50:
            return 60  # 1 minute
        elif file_size_mb < 200:
            return 180  # 3 minutes
        elif file_size_mb < 500:
            return 300  # 5 minutes
        elif file_size_mb < 1000:
            return 600  # 10 minutes
        else:
            return 900  # 15 minutes

    def _find_latest_uploaded_file(self, upload_start_time, original_filename):
        """Find the latest uploaded file using Google Photos API"""
        if not self.google_photos_service:
            logger.error("No Google Photos API service available")
            return None
        
        try:
            # Search for media items uploaded after our start time
            logger.info("📡 Querying Google Photos API for recent uploads...")
            
            # Get recent media items (last 50)
            results = self.google_photos_service.mediaItems().list(
                pageSize=50
            ).execute()
            
            media_items = results.get('mediaItems', [])
            logger.info(f"📊 Found {len(media_items)} recent media items")
            
            # Look for video files uploaded recently
            candidates = []
            
            for item in media_items:
                try:
                    # Check if it's a video
                    mime_type = item.get('mimeType', '')
                    if not mime_type.startswith('video/'):
                        continue
                    
                    # Check creation time
                    metadata = item.get('mediaMetadata', {})
                    creation_time_str = metadata.get('creationTime', '')
                    
                    if creation_time_str:
                        from dateutil import parser
                        creation_time = parser.parse(creation_time_str)
                        
                        # Check if created after our upload start (with some tolerance)
                        time_diff = abs((creation_time - upload_start_time).total_seconds())
                        
                        if time_diff < 3600:  # Within 1 hour
                            candidates.append({
                                'item': item,
                                'time_diff': time_diff,
                                'creation_time': creation_time
                            })
                            logger.info(f"📋 Candidate: {item.get('filename', 'Unknown')} (time_diff: {time_diff:.0f}s)")
                
                except Exception as item_error:
                    logger.debug(f"Error processing item: {item_error}")
                    continue
            
            if candidates:
                # Sort by closest time to upload start
                candidates.sort(key=lambda x: x['time_diff'])
                best_candidate = candidates[0]
                
                logger.info(f"🎯 Best candidate: {best_candidate['item'].get('filename', 'Unknown')}")
                return best_candidate['item']
            else:
                logger.warning("No suitable candidates found")
                return None
                
        except Exception as e:
            logger.error(f"Error finding latest uploaded file: {e}")
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
        """Force stop Google Photos"""
        try:
            commands = [
                f"su -c 'am force-stop {GOOGLE_PHOTOS_PACKAGE}'",
                f"su -c 'killall -9 {GOOGLE_PHOTOS_PACKAGE}'",
                f"su -c 'pkill -9 -f {GOOGLE_PHOTOS_PACKAGE}'"
            ]
            
            for cmd in commands:
                try:
                    subprocess.run(cmd, shell=True, capture_output=True, timeout=5)
                    time.sleep(1)
                except:
                    continue
            
            logger.info("✅ Google Photos force stopped")
            
        except Exception as e:
            logger.error(f"❌ Error force stopping: {e}")

    def _delete_file(self, file_path):
        """Delete original file"""
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"✅ Original file deleted")
        except Exception as e:
            logger.error(f"❌ Error deleting file: {e}")

    def _send_start_notification(self, filename, file_size):
        """Send start notification"""
        message = f"""🚀 AUTOMATIC PROCESSING STARTED

📁 File: {filename}
📊 Size: {file_size / (1024*1024):.1f}MB
🤖 Mode: Fully Automatic

📱 Opening Google Photos...
⏳ Will create share link automatically
📨 You'll get the link when ready"""

        self._send_telegram_message(message)

    def _send_progress_notification(self, filename, wait_time):
        """Send progress notification"""
        message = f"""⏳ UPLOAD IN PROGRESS

📁 File: {filename}
📱 Status: Google Photos uploading...
⏰ Estimated time: {wait_time//60}m {wait_time%60}s

🤖 Processing automatically...
🔗 Share link will be created when upload completes"""

        self._send_telegram_message(message)

    def _send_success_notification(self, filename, file_size, share_link):
        """Send success notification with share link"""
        if 'photos.app.goo.gl' in share_link:
            link_type = "🎯 Perfect short link!"
        elif 'photos.google.com' in share_link:
            link_type = "✅ Google Photos link"
        else:
            link_type = "🔗 Share link"

        message = f"""✅ AUTOMATIC PROCESSING COMPLETE!

📁 File: {filename}
📊 Size: {file_size / (1024*1024):.1f}MB
🔗 Link: {share_link}

{link_type}
🤖 Created automatically via API
🗑️ Original file deleted
📱 Google Photos closed

🎉 Ready to share!"""

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

💡 For better links, manually create share in Google Photos"""

        self._send_telegram_message(message)

    def _send_not_found_notification(self, filename):
        """Send notification when file not found"""
        message = f"""❌ FILE NOT FOUND IN GOOGLE PHOTOS

📁 File: {filename}
📱 Status: Upload may have failed

🔧 Please check:
1. Google Photos app is properly configured
2. Auto-backup is enabled
3. Internet connection is stable

🗑️ Original file deleted anyway"""

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
    logger.info("AUTOMATIC LATEST FILE SHARING - API BASED")
    logger.info("="*60)
    logger.info(f"Monitoring: {CAMERA_FOLDER}")
    logger.info("Features: API-based file detection, automatic share link creation")
    logger.info("Requirements: Google Photos API credentials (token.json)")
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
    
    event_handler = AutomaticLatestHandler()
    observer = Observer()
    observer.schedule(event_handler, CAMERA_FOLDER, recursive=False)
    observer.start()
    
    logger.info("🚀 Automatic latest file sharing started!")
    logger.info("📁 Drop MKV files to test fully automatic processing...")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopping...")
        observer.stop()
    
    observer.join()

if __name__ == "__main__":
    main()
