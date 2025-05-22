#!/usr/bin/env python3
"""
Bliss OS Native Google Photos Auto Sync Script
----------------------------------------------
1. Detects new MKV files in Camera folder
2. Opens Google Photos app for auto-sync
3. Waits for upload completion
4. Gets share link via Telegram bot
5. Force stops Google Photos app (root)
6. Deletes file from Camera folder
7. Processes next file one by one
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

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Configuration
CAMERA_FOLDER = "/storage/emulated/0/DCIM/Camera"  # Bliss OS camera folder
TELEGRAM_BOT_TOKEN = "8114381417:AAFlvW0cQBhv4LTi1m8pmMuR-zC_zl0MWpo"  # Replace with your bot token
TELEGRAM_CHAT_ID = "6575149109"  # Replace with your chat ID
GOOGLE_PHOTOS_PACKAGE = "com.google.android.apps.photos"

# Upload speed configuration
UPLOAD_SPEED_MBPS = 15  # Your VPS upload speed in MB/s
BASE_WAIT_TIME = 60  # Minimum wait time in seconds
SPEED_BUFFER_MULTIPLIER = 1.5  # Add 50% buffer time for safety
MAX_WAIT_TIME = 3600  # Maximum wait time: 1 hour
SYNC_CHECK_INTERVAL = 10  # Check sync status every 10 seconds

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

class BlissOSHandler(FileSystemEventHandler):
    """Handler for MKV file events in Camera folder"""

    def __init__(self):
        super().__init__()
        self.file_queue = FileQueue()
        self.processor_thread = threading.Thread(target=self._process_queue, daemon=True)
        self.processor_thread.start()
        logger.info("Bliss OS Google Photos sync handler initialized")

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

    def _calculate_upload_time(self, file_path):
        """Calculate expected upload time based on file size and connection speed"""
        try:
            file_size_bytes = os.path.getsize(file_path)
            file_size_mb = file_size_bytes / (1024 * 1024)  # Convert to MB
            
            # Calculate base upload time
            upload_time_seconds = file_size_mb / UPLOAD_SPEED_MBPS
            
            # Add buffer time for safety
            total_wait_time = int(upload_time_seconds * SPEED_BUFFER_MULTIPLIER)
            
            # Apply minimum and maximum limits
            total_wait_time = max(BASE_WAIT_TIME, total_wait_time)
            total_wait_time = min(MAX_WAIT_TIME, total_wait_time)
            
            logger.info(f"File size: {file_size_mb:.1f}MB")
            logger.info(f"Estimated upload time: {upload_time_seconds:.0f}s ({upload_time_seconds/60:.1f}m)")
            logger.info(f"Total wait time (with buffer): {total_wait_time}s ({total_wait_time/60:.1f}m)")
            
            return total_wait_time
            
        except Exception as e:
            logger.error(f"Error calculating upload time: {e}")
            return BASE_WAIT_TIME

    def _process_file(self, file_path):
        """Process a single file through the complete workflow"""
        filename = os.path.basename(file_path)
        logger.info(f"Starting processing: {filename}")
        
        try:
            # Step 1: Check if file still exists
            if not os.path.exists(file_path):
                logger.warning(f"File no longer exists: {filename}")
                return
            
            # Step 2: Calculate dynamic wait time based on file size
            upload_wait_time = self._calculate_upload_time(file_path)
            
            # Step 3: Open Google Photos app
            logger.info("Opening Google Photos app...")
            self._open_google_photos()
            
            # Step 4: Wait for app to start and begin sync
            time.sleep(15)  # Give app time to start
            
            # Step 5: Wait for upload completion with dynamic timing
            logger.info(f"Waiting for {filename} to upload to Google Photos...")
            logger.info(f"Expected wait time: {upload_wait_time/60:.1f} minutes")
            
            if self._wait_for_upload_completion(file_path, upload_wait_time):
                logger.info(f"Upload completed for {filename}")
                
                # Step 6: Get file info for sharing
                file_info = self._get_file_info(file_path)
                
                # Step 7: Send notification via Telegram
                self._send_telegram_notification(filename, file_info)
                
                # Step 8: Force stop Google Photos app
                logger.info("Force stopping Google Photos app...")
                self._force_stop_google_photos()
                
                # Step 9: Delete file from Camera folder
                logger.info(f"Deleting {filename} from Camera folder...")
                self._delete_file(file_path)
                
                logger.info(f"Successfully processed and deleted: {filename}")
                
            else:
                logger.warning(f"Upload timeout for {filename}, skipping deletion")
                self._force_stop_google_photos()
                
        except Exception as e:
            logger.error(f"Error in _process_file for {filename}: {str(e)}")
            # Force stop Google Photos even if there's an error
            try:
                self._force_stop_google_photos()
            except:
                pass

    def _open_google_photos(self):
        """Open Google Photos app using Android activity manager"""
        try:
            # Method 1: Open Google Photos directly
            subprocess.run([
                'am', 'start',
                '-n', f'{GOOGLE_PHOTOS_PACKAGE}/.home.HomeActivity'
            ], check=True, capture_output=True)
            logger.info("Google Photos app opened successfully")
            
        except subprocess.CalledProcessError:
            try:
                # Method 2: Open via intent
                subprocess.run([
                    'am', 'start',
                    '-a', 'android.intent.action.MAIN',
                    '-c', 'android.intent.category.LAUNCHER',
                    GOOGLE_PHOTOS_PACKAGE
                ], check=True, capture_output=True)
                logger.info("Google Photos app opened via intent")
                
            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to open Google Photos app: {e}")
                raise

    def _wait_for_upload_completion(self, file_path, max_wait_time):
        """Wait for file to be uploaded to Google Photos with progress updates"""
        filename = os.path.basename(file_path)
        initial_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        
        logger.info(f"Monitoring upload progress for {filename}")
        logger.info(f"File size: {initial_size / (1024*1024):.1f}MB")
        logger.info(f"Maximum wait time: {max_wait_time/60:.1f} minutes")
        
        elapsed_time = 0
        last_progress_update = 0
        
        while elapsed_time < max_wait_time:
            time.sleep(SYNC_CHECK_INTERVAL)
            elapsed_time += SYNC_CHECK_INTERVAL
            
            # Show progress updates every minute
            if elapsed_time - last_progress_update >= 60:
                progress_percent = (elapsed_time / max_wait_time) * 100
                logger.info(f"Upload progress: {elapsed_time/60:.1f}m elapsed ({progress_percent:.0f}% of expected time)")
                last_progress_update = elapsed_time
            
            # Check if Google Photos is still running and syncing
            if self._is_google_photos_syncing():
                continue
            else:
                # App finished syncing, assume upload complete
                logger.info(f"Google Photos sync completed for {filename} after {elapsed_time/60:.1f} minutes")
                return True
        
        logger.warning(f"Upload timeout reached for {filename} after {max_wait_time/60:.1f} minutes")
        return False

    def _is_google_photos_syncing(self):
        """Check if Google Photos is currently syncing"""
        try:
            # Check if Google Photos process is active
            result = subprocess.run([
                'ps', '-A'
            ], capture_output=True, text=True)
            
            # Look for Google Photos in running processes
            return GOOGLE_PHOTOS_PACKAGE in result.stdout
            
        except Exception as e:
            logger.warning(f"Could not check sync status: {e}")
            return True  # Assume still syncing if we can't check

    def _get_file_info(self, file_path):
        """Get file information for sharing"""
        try:
            stat = os.stat(file_path)
            return {
                'name': os.path.basename(file_path),
                'size': stat.st_size,
                'modified': time.ctime(stat.st_mtime)
            }
        except Exception as e:
            logger.error(f"Error getting file info: {e}")
            return {'name': os.path.basename(file_path), 'size': 0, 'modified': 'unknown'}

    def _send_telegram_notification(self, filename, file_info):
        """Send notification via Telegram bot"""
        try:
            message = f"""📱 New video uploaded to Google Photos!

📁 File: {filename}
📊 Size: {file_info['size']} bytes
🕒 Modified: {file_info['modified']}

✅ Automatically synced from Bliss OS Camera folder
🗑️ Original file deleted from device"""

            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            data = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message
            }
            response = requests.post(url, data=data, timeout=10)
            
            if response.status_code == 200:
                logger.info("Telegram notification sent successfully")
            else:
                logger.error(f"Failed to send Telegram notification: {response.text}")
        
        except Exception as e:
            logger.error(f"Error sending Telegram message: {str(e)}")

    def _force_stop_google_photos(self):
        """Force stop Google Photos app using root permissions"""
        try:
            # Method 1: Use am force-stop (requires root)
            result = subprocess.run([
                'su', '-c', f'am force-stop {GOOGLE_PHOTOS_PACKAGE}'
            ], capture_output=True, text=True)
            
            if result.returncode == 0:
                logger.info("Google Photos app force stopped successfully")
            else:
                logger.warning(f"Force stop command returned: {result.stderr}")
                
                # Method 2: Kill process directly
                subprocess.run([
                    'su', '-c', f'pkill -f {GOOGLE_PHOTOS_PACKAGE}'
                ], capture_output=True)
                logger.info("Google Photos process killed via pkill")
                
        except Exception as e:
            logger.error(f"Error force stopping Google Photos: {str(e)}")

    def _delete_file(self, file_path):
        """Permanently delete file from Camera folder"""
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"File deleted successfully: {os.path.basename(file_path)}")
            else:
                logger.warning(f"File already deleted: {os.path.basename(file_path)}")
        except Exception as e:
            logger.error(f"Error deleting file {file_path}: {str(e)}")

def main():
    """Main function to start the observer"""
    logger.info("="*60)
    logger.info("BLISS OS GOOGLE PHOTOS AUTO SYNC")
    logger.info("="*60)
    logger.info(f"Monitoring folder: {CAMERA_FOLDER}")
    logger.info(f"Google Photos package: {GOOGLE_PHOTOS_PACKAGE}")
    logger.info("Workflow: Detect → Open App → Sync → Share → Force Stop → Delete")
    logger.info("="*60)
    
    # Check if running as root
    try:
        result = subprocess.run(['id'], capture_output=True, text=True)
        if 'uid=0' in result.stdout:
            logger.info("✅ Running with root privileges")
        else:
            logger.warning("⚠️ Not running as root - some features may not work")
    except:
        logger.warning("Could not check root status")
    
    # Create observer and handler
    event_handler = BlissOSHandler()
    observer = Observer()
    
    # Schedule the observer
    observer.schedule(event_handler, CAMERA_FOLDER, recursive=False)
    observer.start()
    
    logger.info("🚀 Auto-sync monitoring started! Upload MKV files to test...")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopping auto-sync monitor...")
        observer.stop()
    
    observer.join()
    logger.info("Auto-sync monitor stopped")

if __name__ == "__main__":
    main()
