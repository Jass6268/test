#!/usr/bin/env python3
"""
Bliss OS Auto Upload Script
--------------------------
Monitors DCIM/camera folder for new MKV files
Uploads them to Google Photos
Sends share link via Telegram bot
"""

import os
import time
import json
import requests
import logging
import webbrowser
import subprocess
import platform
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Configuration
DCIM_FOLDER = "/sdcard/DCIM/Camera/"  # Replace with your actual path
SCOPES = ['https://www.googleapis.com/auth/photoslibrary']
TOKEN_FILE = 'token.json'
CREDENTIALS_FILE = 'credentials.json'
TELEGRAM_BOT_TOKEN = "8114381417:AAFlvW0cQBhv4LTi1m8pmMuR-zC_zl0MWpo"  # Replace with your bot token
TELEGRAM_CHAT_ID = "6575149109"  # Replace with your chat ID

class MkvHandler(FileSystemEventHandler):
    """Handler for MKV file events in the DCIM folder"""

    def __init__(self):
        super().__init__()
        self.google_photos_service = self._authenticate_google_photos()
        logger.info("Google Photos authentication successful")

    def on_created(self, event):
        """Handle file creation events"""
        if not event.is_directory and event.src_path.lower().endswith('.mkv'):
            logger.info(f"New MKV file detected: {event.src_path}")
            # Allow a small delay to ensure file is completely written
            time.sleep(3)
            self._upload_to_google_photos(event.src_path)

    def _open_browser(self, url):
        """Try to open URL in system browser using multiple methods"""
        try:
            # Method 1: Termux-specific command
            if os.path.exists('/data/data/com.termux'):
                try:
                    # Termux uses am (activity manager) to open browsers
                    subprocess.run([
                        'am', 'start', 
                        '-a', 'android.intent.action.VIEW', 
                        '-d', url
                    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return True
                except Exception as termux_error:
                    logger.warning(f"Termux am command failed: {termux_error}")
                    
                    # Try termux-open if available
                    try:
                        subprocess.run(['termux-open', url], check=True, 
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        return True
                    except Exception as termux_open_error:
                        logger.warning(f"termux-open failed: {termux_open_error}")
            
            # Method 2: Python's webbrowser module
            webbrowser.open(url)
            return True
            
        except Exception as e1:
            logger.warning(f"webbrowser.open failed: {e1}")
            
            try:
                # Method 3: Platform-specific commands
                system = platform.system().lower()
                
                if system == "linux":
                    # Try different Linux browsers and commands
                    browsers = ['xdg-open', 'google-chrome', 'firefox', 'chromium-browser', 'opera']
                    for browser in browsers:
                        try:
                            subprocess.run([browser, url], check=True, 
                                         stdout=subprocess.DEVNULL, 
                                         stderr=subprocess.DEVNULL)
                            return True
                        except (subprocess.CalledProcessError, FileNotFoundError):
                            continue
                
                elif system == "darwin":  # macOS
                    subprocess.run(['open', url], check=True)
                    return True
                    
                elif system == "windows":
                    subprocess.run(['start', url], shell=True, check=True)
                    return True
                
            except Exception as e2:
                logger.warning(f"Platform-specific browser opening failed: {e2}")
        
        return False

    def _authenticate_google_photos(self):
        """Authenticate with Google Photos API"""
        creds = None
        
        # Check if token file exists
        if os.path.exists(TOKEN_FILE):
            creds = Credentials.from_authorized_user_info(
                json.loads(open(TOKEN_FILE).read()), SCOPES)
        
        # If credentials don't exist or are invalid, get new ones
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                try:
                    flow = InstalledAppFlow.from_client_secrets_file(
                        CREDENTIALS_FILE, SCOPES)
                    
                    # Generate authorization URL
                    auth_url, _ = flow.authorization_url(
                        access_type='offline',
                        include_granted_scopes='true'
                    )
                    
                    print("\n" + "="*60)
                    print("GOOGLE AUTHENTICATION REQUIRED")
                    print("="*60)
                    print("Attempting to open browser automatically...")
                    
                    # Try to open browser automatically
                    browser_opened = self._open_browser(auth_url)
                    
                    if browser_opened:
                        print("✓ Browser opened successfully!")
                        print("Complete the authorization in your browser, then return here.")
                    else:
                        print("✗ Could not open browser automatically.")
                        print("Please manually copy and open this URL:")
                        print(f"\n{auth_url}\n")
                    
                    print("\nAfter authorization:")
                    print("1. Complete the authorization in your browser")
                    print("2. Copy the authorization code from the browser")
                    print("3. Paste it below:")
                    print("="*60)
                    
                    code = input("Enter authorization code: ").strip()
                    
                    # Clean the code (remove any extra whitespace or characters)
                    code = code.replace(' ', '').replace('\n', '').replace('\r', '')
                    
                    if not code:
                        raise ValueError("No authorization code provided")
                    
                    flow.fetch_token(code=code)
                    creds = flow.credentials
                    
                except Exception as e:
                    logger.error(f"Authentication failed: {str(e)}")
                    logger.error("Please check your credentials.json file and try again")
                    raise
            
            # Save credentials for next run
            with open(TOKEN_FILE, 'w') as token:
                token.write(creds.to_json())
            
            logger.info("Authentication successful!")
        
        # Build the Google Photos service
        return build('photoslibrary', 'v1', credentials=creds, static_discovery=False)

    def _upload_to_google_photos(self, file_path):
        """Upload file to Google Photos and send share link via Telegram"""
        try:
            # 1. Upload the media item
            logger.info(f"Uploading {file_path} to Google Photos...")
            
            # First, get an upload token
            filename = os.path.basename(file_path)
            mime_type = 'video/x-matroska'
            
            upload_url = 'https://photoslibrary.googleapis.com/v1/uploads'
            headers = {
                'Authorization': f'Bearer {self.google_photos_service._credentials.token}',
                'Content-Type': 'application/octet-stream',
                'X-Goog-Upload-File-Name': filename,
                'X-Goog-Upload-Protocol': 'raw',
            }
            
            with open(file_path, 'rb') as file_data:
                upload_token = requests.post(upload_url, headers=headers, data=file_data).text
            
            # 2. Create the media item in Google Photos
            body = {
                'newMediaItems': [{
                    'description': f'Uploaded from Bliss OS: {filename}',
                    'simpleMediaItem': {
                        'uploadToken': upload_token
                    }
                }]
            }
            
            response = self.google_photos_service.mediaItems().batchCreate(body=body).execute()
            
            if 'newMediaItemResults' in response:
                item = response['newMediaItemResults'][0]['mediaItem']
                media_id = item['id']
                product_url = item['productUrl']
                
                logger.info(f"Upload successful! Media ID: {media_id}")
                logger.info(f"Share link: {product_url}")
                
                # 3. Send share link via Telegram with file name
                self._send_telegram_message(f"New video uploaded to Google Photos!\nFile: {filename}\nLink: {product_url}")
            else:
                logger.error("Upload failed: Response didn't contain expected format")
                logger.error(f"Response: {response}")
        
        except Exception as e:
            logger.error(f"Error uploading to Google Photos: {str(e)}")
    
    def _send_telegram_message(self, message):
        """Send message via Telegram bot"""
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            data = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message
            }
            response = requests.post(url, data=data)
            
            if response.status_code == 200:
                logger.info("Telegram notification sent successfully")
            else:
                logger.error(f"Failed to send Telegram notification: {response.text}")
        
        except Exception as e:
            logger.error(f"Error sending Telegram message: {str(e)}")

def main():
    """Main function to start the observer"""
    logger.info(f"Starting to monitor folder: {DCIM_FOLDER}")
    
    # Create observer and handler
    event_handler = MkvHandler()
    observer = Observer()
    
    # Schedule the observer
    observer.schedule(event_handler, DCIM_FOLDER, recursive=False)
    observer.start()
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    
    observer.join()

if __name__ == "__main__":
    main()
