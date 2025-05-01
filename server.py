import os
import subprocess
from flask import Flask, request, jsonify

app = Flask(__name__)

# BlissOS shared sync path (adjust as per your Android-x86 mount)
PHOTOS_SYNC_FOLDER = "/mnt/shared/DCIM/Camera"

@app.route('/upload', methods=['POST'])
def upload():
    data = request.get_json()
    drive_url = data.get('url')
    filename = data.get('name')

    if not drive_url or not filename:
        return jsonify({'status': 'error', 'message': 'Missing url or filename'}), 400

    # Safe filename
    filename = filename.strip().replace(" ", "_")
    tmp_path = f"/tmp/{filename}"

    # Download via aria2c
    subprocess.run(["aria2c", "-o", filename, drive_url], cwd="/tmp", check=True)

    # Move to BlissOS Google Photos sync folder
    final_path = os.path.join(PHOTOS_SYNC_FOLDER, filename)
    os.rename(tmp_path, final_path)

    return jsonify({'status': 'success', 'filename': filename})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
