import os
import subprocess
import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify

app = Flask(__name__)

# BlissOS Google Photos sync folder
PHOTOS_SYNC_FOLDER = "/mnt/shared/DCIM/Camera"  # Change this path if your BlissOS sync folder is different

# ================================
# 1. /upload → Download and move file
# ================================
@app.route('/upload', methods=['POST'])
def upload():
    data = request.get_json()
    drive_url = data.get('url')
    filename = data.get('name')

    if not drive_url or not filename:
        return jsonify({'status': 'error', 'message': 'Missing url or filename'}), 400

    filename = filename.strip().replace(" ", "_")
    tmp_path = f"/tmp/{filename}"

    # Download using aria2c
    subprocess.run(["aria2c", "-o", filename, drive_url], cwd="/tmp", check=True)

    # Move to Google Photos folder
    final_path = os.path.join(PHOTOS_SYNC_FOLDER, filename)
    os.rename(tmp_path, final_path)

    return jsonify({'status': 'success', 'filename': filename})


# ================================
# 2. /save_link → Save Google Photos link to Cloudflare KV
# ================================
CLOUDFLARE_KV_WRITE_API = "https://api.cloudflare.com/client/v4/accounts/fd136511df3e15f09706a22ea2feaddf/storage/kv/namespaces/19830dfab80043d1afd4400f8eb8ccce/values/"
CLOUDFLARE_AUTH_HEADERS = {
    "Authorization": "Bearer cozLDCHPbp0jsB7w3ExNvmGU0lcsiT0ctItxfpbX",
    "Content-Type": "text/plain"
}

@app.route('/save_link', methods=['POST'])
def save_link():
    data = request.get_json()
    link_id = data.get('id')
    share_link = data.get('link')

    if not link_id or not share_link:
        return jsonify({'status': 'error', 'message': 'Missing data'}), 400

    # Save link to Cloudflare KV
    resp = requests.put(
        CLOUDFLARE_KV_WRITE_API + link_id,
        headers=CLOUDFLARE_AUTH_HEADERS,
        data=share_link
    )

    if resp.status_code == 200:
        return jsonify({'status': 'saved'})
    else:
        return jsonify({'status': 'error', 'message': resp.text}), 500


# ================================
# 3. /extract → Get direct video-download link from shared Photos URL
# ================================
@app.route('/extract')
def extract():
    share_url = request.args.get("url")
    if not share_url:
        return jsonify({"error": "Missing url"}), 400

    headers = {'User-Agent': 'Mozilla/5.0'}
    res = requests.get(share_url, headers=headers)

    soup = BeautifulSoup(res.text, 'html.parser')
    video_tag = soup.find("video")

    if not video_tag or not video_tag.get("src"):
        return jsonify({"error": "Direct link not found"}), 404

    return jsonify({"direct_url": video_tag["src"]})


# ================================
# Flask run
# ================================
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
