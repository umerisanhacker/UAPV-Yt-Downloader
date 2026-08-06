import os
import threading
import uuid
import re
from flask import Flask, render_template, request, jsonify, send_from_directory
import yt_dlp

app = Flask(__name__)
download_progress = {}

@app.route('/')
def index():
    return render_template('index.html')

def get_standard_resolution(h):
    """Maps non-standard container heights to standard YouTube resolution tiers."""
    if h >= 1800: return 2160
    elif h >= 1200: return 1440
    elif h >= 900: return 1080
    elif h >= 600: return 720
    elif h >= 400: return 480
    elif h >= 300: return 360
    elif h >= 200: return 240
    else: return 144

@app.route('/get-info', methods=['POST'])
def get_info():
    data = request.get_json()
    url = data.get('url')
    
    if not url:
        return jsonify({'error': 'No URL provided'}), 400

    try:
        ydl_opts = {'quiet': True, 'no_warnings': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            formats = []
            seen = set()
            
            # Read every available format and extract technical specs
            for f in info.get('formats', []):
                if f.get('vcodec') != 'none' and f.get('height'):
                    ext = f.get('ext', 'mp4')
                    # Skip WEBM formats completely as requested
                    if ext.lower() == 'webm':
                        continue
                        
                    format_id = f.get('format_id')
                    raw_height = f.get('height')
                    std_height = get_standard_resolution(raw_height)
                    fps = f.get('fps') or 30
                    vcodec = f.get('vcodec', '').lower()
                    filesize = f.get('filesize') or f.get('filesize_approx') or 0
                    
                    # Normalize codec label
                    if 'av01' in vcodec:
                        codec_name = 'AV1'
                    elif 'vp09' in vcodec:
                        codec_name = 'VP9'
                    elif 'avc1' in vcodec or 'h264' in vcodec:
                        codec_name = 'AVC'
                    else:
                        codec_name = 'Video'

                    # Format file size neatly
                    if filesize > 1024 * 1024:
                        size_str = f"{filesize / (1024 * 1024):.1f} MB"
                    elif filesize > 1024:
                        size_str = f"{filesize / 1024:.1f} KB"
                    else:
                        size_str = "Estimated size"

                    key = (std_height, int(fps), ext, codec_name)
                    if key not in seen:
                        seen.add(key)
                        label = f"{std_height}p{int(fps)} - {ext.upper()} ({codec_name}) - {size_str}"
                        formats.append({
                            'format_id': format_id,
                            'height': std_height,
                            'fps': fps,
                            'ext': ext,
                            'codec': codec_name,
                            'filesize': size_str,
                            'label': label
                        })
            
            # Sort by resolution descending, then fps descending
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
        if file_type == 'audio':
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': os.path.join(downloads_dir, '%(title)s.%(ext)s'),
                'progress_hooks': [progress_hook],
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            }
        else:
            # Download exact selected format id and merge with best available audio safely
            ydl_opts = {
                'format': f'{format_id}+bestaudio/best',
                'outtmpl': os.path.join(downloads_dir, '%(title)s [%(height)sp].%(ext)s'),
                'merge_output_format': 'mp4',
                'progress_hooks': [progress_hook],
                'concurrent_fragment_downloads': 4,
            }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            
            filename = ydl.prepare_filename(info)
            if file_type == 'audio':
                filename = os.path.splitext(filename)[0] + '.mp3'
            else:
                if 'requested_downloads' in info and info['requested_downloads']:
                    filename = info['requested_downloads'][0].get('filepath', filename)
                elif 'filepath' in info:
                    filename = info['filepath']
                
                base, ext = os.path.splitext(filename)
                if not os.path.exists(filename):
                    for ext_candidate in ['.mp4', '.mkv', '.webm']:
                        if os.path.exists(base + ext_candidate):
                            filename = base + ext_candidate
                            break

            basename = os.path.basename(filename)
            download_progress[task_id] = {
                'progress': 100.0,
                'status': 'completed',
                'filename': basename
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
