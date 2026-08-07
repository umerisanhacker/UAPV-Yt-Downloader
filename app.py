import os
import threading
import uuid
import re
import base64
from flask import Flask, render_template, request, jsonify, send_from_directory
import yt_dlp

app = Flask(__name__)
download_progress = {}

# ==============================================================================
# 🛡️ THE BASE64 COOKIE RECONSTRUCTOR
# Render destroys file formatting in env vars. This securely decodes a Base64
# string back into a pristine, perfectly formatted Netscape cookies file in the 
# /tmp directory (which Render cannot block or corrupt).
# ==============================================================================
COOKIE_PATH = '/tmp/youtube_cookies.txt'

encoded_cookies = os.environ.get('YOUTUBE_COOKIES_B64', '')
if encoded_cookies:
    try:
        decoded_bytes = base64.b64decode(encoded_cookies)
        with open(COOKIE_PATH, 'wb') as f:
            f.write(decoded_bytes)
        print("✅ SUCCESS: Base64 Cookies Decoded and Written.")
    except Exception as e:
        print(f"❌ ERROR: Failed to decode Base64 cookies: {e}")
# ==============================================================================

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/get-info', methods=['POST'])
def get_info():
    data = request.get_json()
    url = data.get('url')
    
    if not url:
        return jsonify({'error': 'No URL provided'}), 400

    try:
        # AGGRESSIVE BOT BYPASS
        ydl_opts = {
            'quiet': True, 
            'no_warnings': True,
            'extract_flat': False,
            'extractor_args': {
                'youtube': {
                    'player_skip': ['web', 'web_embedded'],
                    'player_client': ['android', 'ios', 'tv_embedded']
                }
            },
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                'Accept-Language': 'en-US,en;q=0.9',
            }
        }
        
        # Inject the perfectly reconstructed cookies file
        if os.path.exists(COOKIE_PATH):
            ydl_opts['cookiefile'] = COOKIE_PATH

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            formats = []
            seen = set()
            
            for f in info.get('formats', []):
                if f.get('vcodec') != 'none' and f.get('height'):
                    ext = f.get('ext', 'mp4')
                    format_id = f.get('format_id')
                    height = f.get('height')
                    fps = f.get('fps') or 30
                    vcodec = f.get('vcodec', '').lower()
                    filesize = f.get('filesize') or f.get('filesize_approx') or 0
                    
                    if 'av01' in vcodec: 
                        codec_name = 'AV1 (High Quality)'
                    elif 'vp09' in vcodec: 
                        codec_name = 'VP9 (Standard)'
                    elif 'avc' in vcodec or 'h264' in vcodec: 
                        codec_name = 'H.264 (Most Compatible)'
                    else: 
                        codec_name = 'MP4'

                    if filesize > 1024 * 1024:
                        size_str = f"{filesize / (1024 * 1024):.1f} MB"
                    elif filesize > 1024:
                        size_str = f"{filesize / 1024:.1f} KB"
                    else:
                        size_str = "Dynamic Size"

                    key = (height, int(fps), codec_name)
                    if key not in seen:
                        seen.add(key)
                        label = f"{height}p{int(fps)} - {codec_name} - {size_str}"
                        formats.append({
                            'format_id': format_id,
                            'height': height,
                            'fps': fps,
                            'ext': ext,
                            'label': label
                        })
            
            formats.sort(key=lambda x: (x['height'], x['fps']), reverse=True)

            return jsonify({
                'title': info.get('title'),
                'thumbnail': info.get('thumbnail'),
                'duration': info.get('duration_string'),
                'formats': formats,
                'webpage_url': info.get('webpage_url')
            })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def run_download(task_id, url, format_id, file_type):
    downloads_dir = os.path.join(os.getcwd(), 'downloads')
    os.makedirs(downloads_dir, exist_ok=True)

    def progress_hook(d):
        if d['status'] == 'downloading':
            p_str = d.get('_percent_str', '0%').replace('%', '').strip()
            p_str_clean = re.sub(r'\x1b\[[0-9;]*m', '', p_str)
            try:
                percent = float(p_str_clean)
            except:
                percent = 0.0
            download_progress[task_id] = {'progress': percent, 'status': 'downloading'}
        elif d['status'] in ['finished', 'postprocessing']:
            download_progress[task_id] = {'progress': 100.0, 'status': 'processing'}

    try:
        ydl_opts = {
            'outtmpl': os.path.join(downloads_dir, f'%(title)s_{task_id}.%(ext)s'),
            'progress_hooks': [progress_hook],
            'concurrent_fragment_downloads': 6, 
            'quiet': True,
            'no_warnings': True,
            'extractor_args': {
                'youtube': {
                    'player_skip': ['web', 'web_embedded'],
                    'player_client': ['android', 'ios', 'tv_embedded']
                }
            },
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
            }
        }
        
        # Inject the perfectly reconstructed cookies file
        if os.path.exists(COOKIE_PATH):
            ydl_opts['cookiefile'] = COOKIE_PATH

        if file_type == 'audio':
            ydl_opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            })
        else:
            ydl_opts.update({
                'format': f'{format_id}+bestaudio/{format_id}',
                'merge_output_format': 'mp4',
            })

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            
            if file_type == 'audio':
                filename = os.path.splitext(filename)[0] + '.mp3'
            else:
                base, ext = os.path.splitext(filename)
                if not os.path.exists(filename):
                    for ext_candidate in ['.mp4', '.mkv', '.webm']:
                        if os.path.exists(base + ext_candidate):
                            filename = base + ext_candidate
                            break
            
            final_clean_name = f"{info.get('title', 'Video')} [{info.get('height', 'Audio')}p].{filename.split('.')[-1]}"
            final_clean_name = "".join([c for c in final_clean_name if c.isalpha() or c.isdigit() or c in " .-_[]()"]).rstrip()
            final_path = os.path.join(downloads_dir, final_clean_name)
            
            counter = 1
            while os.path.exists(final_path):
                name, ext = os.path.splitext(final_clean_name)
                final_path = os.path.join(downloads_dir, f"{name}_{counter}{ext}")
                counter += 1
                
            os.rename(filename, final_path)
            
            download_progress[task_id] = {
                'progress': 100.0,
                'status': 'completed',
                'filename': os.path.basename(final_path)
            }
            
    except Exception as e:
        download_progress[task_id] = {
            'progress': 0,
            'status': 'error',
            'error': str(e)
        }

@app.route('/start-download', methods=['POST'])
def start_download():
    data = request.get_json()
    url = data.get('url')
    format_id = data.get('format_id')
    file_type = data.get('type')

    if not url:
        return jsonify({'error': 'No URL provided'}), 400

    task_id = str(uuid.uuid4())
    download_progress[task_id] = {'progress': 0, 'status': 'starting'}

    thread = threading.Thread(target=run_download, args=(task_id, url, format_id, file_type))
    thread.start()

    return jsonify({'task_id': task_id})

@app.route('/progress/<task_id>')
def get_progress(task_id):
    status = download_progress.get(task_id, {'progress': 0, 'status': 'not_found'})
    return jsonify(status)

@app.route('/get-file/<task_id>')
def get_file(task_id):
    task = download_progress.get(task_id)
    if not task or task.get('status') != 'completed':
        return "File not ready or error", 400
    
    downloads_dir = os.path.join(os.getcwd(), 'downloads')
    filename = task.get('filename')
    return send_from_directory(directory=downloads_dir, path=filename, as_attachment=True)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
