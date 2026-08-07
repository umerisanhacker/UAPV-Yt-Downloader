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

@app.route('/get-info', methods=['POST'])
def get_info():
    data = request.get_json()
    url = data.get('url')
    
    if not url:
        return jsonify({'error': 'No URL provided'}), 400

    try:
        ydl_opts = {
            'quiet': True, 
            'no_warnings': True,
            'extract_flat': False,
        }
        # Check for cookies to bypass age-restrictions or bot-checks
        if os.path.exists('cookies.txt'):
            ydl_opts['cookiefile'] = 'cookies.txt'
            ydl_opts['extractor_args'] = {'youtube': {'player_client': ['tv_embedded', 'android_vr']}}

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            formats = []
            seen = set()
            
            for f in info.get('formats', []):
                # Filter out audio-only tracks and live stream fragments
                if f.get('vcodec') != 'none' and f.get('height'):
                    ext = f.get('ext', 'mp4')
                    format_id = f.get('format_id')
                    height = f.get('height')
                    fps = f.get('fps') or 30
                    vcodec = f.get('vcodec', '').lower()
                    filesize = f.get('filesize') or f.get('filesize_approx') or 0
                    
                    # Identify codec for user clarity
                    if 'av01' in vcodec: 
                        codec_name = 'AV1 (High Quality)'
                    elif 'vp09' in vcodec: 
                        codec_name = 'VP9 (Standard)'
                    elif 'avc' in vcodec or 'h264' in vcodec: 
                        codec_name = 'H.264 (Most Compatible)'
                    else: 
                        codec_name = 'MP4'

                    # Clean filesize mapping
                    if filesize > 1024 * 1024:
                        size_str = f"{filesize / (1024 * 1024):.1f} MB"
                    elif filesize > 1024:
                        size_str = f"{filesize / 1024:.1f} KB"
                    else:
                        size_str = "Dynamic Size"

                    # Group by exact height, FPS, and Codec to prevent duplicates
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
            
            # Sort from Absolute Highest Resolution down to 144p
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
        # Base options for pure beast mode (6 threads)
        ydl_opts = {
            'outtmpl': os.path.join(downloads_dir, f'%(title)s_{task_id}.%(ext)s'),
            'progress_hooks': [progress_hook],
            'concurrent_fragment_downloads': 6, 
            'quiet': True,
            'no_warnings': True
        }
        
        if os.path.exists('cookies.txt'):
            ydl_opts['cookiefile'] = 'cookies.txt'
            ydl_opts['extractor_args'] = {'youtube': {'player_client': ['tv_embedded', 'android_vr']}}

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
            # STRICT FORMAT ENFORCEMENT: Exactly the requested video ID.
            # If it needs audio, add it. If it already has audio or fails to merge, fallback strictly to the ID itself.
            ydl_opts.update({
                'format': f'{format_id}+bestaudio/{format_id}',
                'merge_output_format': 'mp4',
            })

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            
            filename = ydl.prepare_filename(info)
            
            # Correct extensions after FFmpeg processing
            if file_type == 'audio':
                filename = os.path.splitext(filename)[0] + '.mp3'
            else:
                base, ext = os.path.splitext(filename)
                if not os.path.exists(filename):
                    for ext_candidate in ['.mp4', '.mkv', '.webm']:
                        if os.path.exists(base + ext_candidate):
                            filename = base + ext_candidate
                            break
            
            # Rename the file to remove the ugly task UUID before giving it to the user
            final_clean_name = f"{info.get('title', 'Video')} [{info.get('height', 'Audio')}p].{filename.split('.')[-1]}"
            # Sanitize special characters
            final_clean_name = "".join([c for c in final_clean_name if c.isalpha() or c.isdigit() or c in " .-_[]()"]).rstrip()
            final_path = os.path.join(downloads_dir, final_clean_name)
            
            # Ensure we don't accidentally overwrite a similarly named file
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

    # Spawn background thread so the UI doesn't hang
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
