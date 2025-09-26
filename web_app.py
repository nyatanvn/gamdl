#!/usr/bin/env python3
"""
GAMDL Web Interface
A Flask-based web frontend for gamdl with basic and advanced options.
"""

import os
import subprocess
import tempfile
import json
import re
import sys
import secrets
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file, flash, redirect, url_for
from werkzeug.utils import secure_filename
import threading
import time
import uuid
from datetime import datetime

# Add the gamdl module to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from gamdl.apple_music_api import AppleMusicApi
    from gamdl.downloader import Downloader
    from gamdl.models import UrlInfo
    from gamdl.itunes_api import ItunesApi
    import urllib.parse
except ImportError as e:
    print(f"Warning: Could not import gamdl modules: {e}")
    AppleMusicApi = None
    Downloader = None
    ItunesApi = None

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Global variables to track downloads
active_downloads = {}
download_results = {}
download_progress = {}

# Ensure upload directory exists
UPLOAD_FOLDER = 'uploads'
DOWNLOAD_FOLDER = 'downloads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

def get_metadata_from_urls(urls, cookies_path=None):
    """Get real metadata for URLs from Apple Music API"""
    try:
        metadata = []
        
        # Initialize Apple Music API
        if AppleMusicApi and cookies_path and os.path.exists(cookies_path):
            try:
                api = AppleMusicApi.from_netscape_cookies(Path(cookies_path))
            except Exception as e:
                print(f"Could not initialize Apple Music API with cookies: {e}")
                api = None
        else:
            api = None
        
        if isinstance(urls, str):
            urls = [url.strip() for url in urls.split('\n') if url.strip()]
        
        for url in urls:
            try:
                # First try our basic URL parsing
                url_info = parse_apple_music_url(url)
                
                # Try to get real metadata if API is available
                if api and url_info['type'] != 'error':
                    real_metadata = get_real_metadata_with_gamdl(api, url_info, url)
                    if real_metadata:
                        metadata.append(real_metadata)
                        continue
                
                # If gamdl modules are available, try gamdl's URL parsing too
                if Downloader and url_info['type'] == 'error':
                    try:
                        # Create a minimal downloader for URL parsing
                        downloader = Downloader(
                            apple_music_api=api,
                            final_path=Path("/tmp"),
                            itunes_api=None,
                            exclude_tags=[],
                            template_folder_album="",
                            template_folder_compilation="",
                            template_file_single="",
                            template_file_album="",
                            template_date=""
                        )
                        gamdl_url_info = downloader.parse_url_info(url)
                        if gamdl_url_info:
                            # Convert gamdl UrlInfo to our dict format
                            url_info = {
                                'url': url,
                                'type': gamdl_url_info.type,
                                'id': gamdl_url_info.id,
                                'title': f"{gamdl_url_info.type.title()} (ID: {gamdl_url_info.id})",
                                'estimated_tracks': 1 if gamdl_url_info.type == 'song' else 'Unknown'
                            }
                    except Exception as e:
                        print(f"Error with gamdl URL parsing: {e}")
                
                metadata.append(url_info)
                    
            except Exception as e:
                metadata.append({
                    'url': url,
                    'type': 'error',
                    'title': f'Error processing URL: {str(e)}',
                    'id': 'error',
                    'estimated_tracks': 0,
                    'tracks': []
                })
        
        return metadata
        
    except Exception as e:
        print(f"Error getting metadata: {e}")
        return []

def get_real_metadata_with_gamdl(api, url_info, url):
    """Get real metadata from Apple Music API using gamdl's structures"""
    try:
        # Handle both dict and UrlInfo object
        if hasattr(url_info, 'type'):
            content_type = url_info.type
            content_id = url_info.sub_id or url_info.id or url_info.library_id
        else:
            content_type = url_info.get('type')
            content_id = url_info.get('id')
        
        if content_type == 'album':
            album_data = api.get_album(content_id)
            if album_data:
                tracks = album_data.get('relationships', {}).get('tracks', {}).get('data', [])
                return {
                    'url': url,
                    'type': 'album',
                    'title': album_data.get('attributes', {}).get('name', 'Unknown Album'),
                    'artist': album_data.get('attributes', {}).get('artistName', 'Unknown Artist'),
                    'id': content_id,
                    'estimated_tracks': len(tracks),
                    'actual_tracks': len(tracks),
                    'release_date': album_data.get('attributes', {}).get('releaseDate'),
                    'genre': ', '.join(album_data.get('attributes', {}).get('genreNames', [])),
                    'tracks': [
                        {
                            'name': track.get('attributes', {}).get('name', 'Unknown'),
                            'duration': format_duration(track.get('attributes', {}).get('durationInMillis', 0)),
                            'track_number': track.get('attributes', {}).get('trackNumber', 0),
                            'artist': track.get('attributes', {}).get('artistName', 'Unknown')
                        }
                        for track in tracks[:15]  # Show first 15 tracks
                    ],
                    'total_duration': format_duration(sum(
                        track.get('attributes', {}).get('durationInMillis', 0) 
                        for track in tracks
                    )),
                    'has_more_tracks': len(tracks) > 15
                }
                
        elif content_type == 'playlist':
            playlist_data = api.get_playlist(content_id)
            if playlist_data:
                tracks = playlist_data.get('relationships', {}).get('tracks', {}).get('data', [])
                return {
                    'url': url,
                    'type': 'playlist',
                    'title': playlist_data.get('attributes', {}).get('name', 'Unknown Playlist'),
                    'curator': playlist_data.get('attributes', {}).get('curatorName', 'Unknown'),
                    'id': content_id,
                    'estimated_tracks': len(tracks),
                    'actual_tracks': len(tracks),
                    'description': playlist_data.get('attributes', {}).get('description', {}).get('standard', ''),
                    'tracks': [
                        {
                            'name': track.get('attributes', {}).get('name', 'Unknown'),
                            'artist': track.get('attributes', {}).get('artistName', 'Unknown'),
                            'album': track.get('attributes', {}).get('albumName', 'Unknown'),
                            'duration': format_duration(track.get('attributes', {}).get('durationInMillis', 0))
                        }
                        for track in tracks[:15]  # Show first 15 tracks
                    ],
                    'total_duration': format_duration(sum(
                        track.get('attributes', {}).get('durationInMillis', 0) 
                        for track in tracks
                    )),
                    'has_more_tracks': len(tracks) > 15
                }
                
        elif content_type == 'song':
            song_data = api.get_song(content_id)
            if song_data:
                return {
                    'url': url,
                    'type': 'song',
                    'title': song_data.get('attributes', {}).get('name', 'Unknown Song'),
                    'artist': song_data.get('attributes', {}).get('artistName', 'Unknown Artist'),
                    'album': song_data.get('attributes', {}).get('albumName', 'Unknown Album'),
                    'id': content_id,
                    'estimated_tracks': 1,
                    'actual_tracks': 1,
                    'duration': format_duration(song_data.get('attributes', {}).get('durationInMillis', 0)),
                    'genre': ', '.join(song_data.get('attributes', {}).get('genreNames', [])),
                    'release_date': song_data.get('attributes', {}).get('releaseDate'),
                    'track_number': song_data.get('attributes', {}).get('trackNumber', 1)
                }
                
        elif content_type == 'artist':
            artist_data = api.get_artist(content_id)
            if artist_data:
                return {
                    'url': url,
                    'type': 'artist',
                    'title': artist_data.get('attributes', {}).get('name', 'Unknown Artist'),
                    'id': content_id,
                    'estimated_tracks': 'Many albums/songs',
                    'actual_tracks': 'Variable (albums and singles)',
                    'genre': ', '.join(artist_data.get('attributes', {}).get('genreNames', [])),
                    'note': 'Artist pages may contain multiple albums and singles'
                }
        
        return None
        
    except Exception as e:
        print(f"Error getting real metadata for {content_type} {content_id}: {e}")
        return None

def format_duration(duration_ms):
    """Format duration from milliseconds to MM:SS or HH:MM:SS"""
    if not duration_ms:
        return "0:00"
    
    seconds = duration_ms // 1000
    minutes = seconds // 60
    hours = minutes // 60
    
    if hours > 0:
        return f"{hours}:{minutes % 60:02d}:{seconds % 60:02d}"
    else:
        return f"{minutes}:{seconds % 60:02d}"

def create_download_folder(base_path, metadata_list, download_id):
    """Create a unique folder for this download session"""
    try:
        # Generate folder name based on content
        if len(metadata_list) == 1:
            # Single item download
            item = metadata_list[0]
            if item.get('type') == 'song':
                folder_name = f"{item.get('artist', 'Unknown')} - {item.get('title', 'Unknown')} ({download_id[:8]})"
            else:
                folder_name = f"{item.get('title', 'Unknown')} ({download_id[:8]})"
        else:
            # Multiple items
            folder_name = f"Multiple Downloads ({len(metadata_list)} items) - {download_id[:8]}"
        
        # Clean folder name (remove invalid characters)
        folder_name = re.sub(r'[<>:"/\\|?*]', '_', folder_name)
        folder_name = folder_name.replace('..', '_')
        
        # Add timestamp for uniqueness
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        folder_name = f"{timestamp}_{folder_name}"
        
        # Create the full path
        download_folder = os.path.join(base_path, folder_name)
        os.makedirs(download_folder, exist_ok=True)
        
        return download_folder
        
    except Exception as e:
        print(f"Error creating download folder: {e}")
        # Fallback to basic folder
        fallback_folder = os.path.join(base_path, f"gamdl_download_{download_id[:8]}")
        os.makedirs(fallback_folder, exist_ok=True)
        return fallback_folder

def parse_apple_music_url(url):
    """Parse Apple Music URL to extract basic info"""
    try:
        # Extract type and ID from Apple Music URLs
        patterns = {
            'album': r'music\.apple\.com/[^/]+/album/([^/]+)/(\d+)',
            'playlist': r'music\.apple\.com/[^/]+/playlist/([^/]+)/pl\.([^/?]+)',
            'song': r'music\.apple\.com/[^/]+/album/[^/]+/(\d+)\?i=(\d+)',
            'artist': r'music\.apple\.com/[^/]+/artist/([^/]+)/(\d+)'
        }
        
        for content_type, pattern in patterns.items():
            match = re.search(pattern, url)
            if match:
                return {
                    'url': url,
                    'type': content_type,
                    'title': match.group(1).replace('-', ' ').title() if len(match.groups()) > 0 else 'Unknown',
                    'id': match.group(2) if len(match.groups()) > 1 else match.group(1),
                    'estimated_tracks': estimate_track_count(content_type)
                }
        
        return {
            'url': url,
            'type': 'unknown',
            'title': 'Unknown Content',
            'id': 'unknown',
            'estimated_tracks': 1
        }
        
    except Exception as e:
        return {
            'url': url,
            'type': 'error',
            'title': f'Error parsing URL: {str(e)}',
            'id': 'error',
            'estimated_tracks': 0
        }

def estimate_track_count(content_type):
    """Estimate number of tracks based on content type"""
    estimates = {
        'song': 1,
        'album': '5-15',
        'playlist': '10-100',
        'artist': '20-200',
        'unknown': '1+'
    }
    return estimates.get(content_type, '1+')

def update_progress_from_line(download_id, line):
    """Update download progress based on output line"""
    try:
        if download_id not in download_progress:
            return
        
        progress = download_progress[download_id]
        
        # Look for different patterns in the output
        if 'Downloading' in line and 'track' in line.lower():
            # Extract track info
            progress['current_track'] = line
            progress['status'] = 'downloading'
        elif 'Downloaded' in line or 'Finished' in line:
            progress['completed_tracks'] += 1
            progress['status'] = 'downloading'
        elif 'Error' in line or 'Failed' in line:
            progress['status'] = 'error'
            progress['current_track'] = f"Error: {line}"
        elif 'tracks found' in line.lower():
            # Try to extract total number
            numbers = re.findall(r'\d+', line)
            if numbers:
                progress['total_tracks'] = int(numbers[0])
        elif 'Processing' in line:
            progress['current_track'] = line
            progress['status'] = 'processing'
        
    except Exception as e:
        print(f"Error updating progress: {e}")

def run_gamdl_command(download_id, urls, options):
    """Run gamdl command in a separate thread with progress tracking"""
    try:
        # Progress tracking is already initialized in the download route
        if download_id not in download_progress:
            download_progress[download_id] = {
                'total_tracks': 0,
                'completed_tracks': 0,
                'current_track': 'Initializing...',
                'status': 'starting',
                'output_lines': [],
                'download_folder': options.get('output_path', DOWNLOAD_FOLDER)
            }
        
        # Update status to running
        download_progress[download_id]['status'] = 'running'
        download_progress[download_id]['current_track'] = 'Building command...'
        
        # Handle artist URLs by expanding them
        processed_urls = []
        if isinstance(urls, str):
            url_list = [url.strip() for url in urls.split('\n') if url.strip()]
        else:
            url_list = urls
        
        # Initialize API for artist expansion
        api = None
        cookies_path = options.get('cookies_path')
        if cookies_path and os.path.exists(cookies_path):
            try:
                api = AppleMusicApi.from_netscape_cookies(Path(cookies_path))
            except Exception as e:
                print(f"Could not initialize API for artist expansion: {e}")
        
        for url in url_list:
            if 'artist' in url and api:
                # Extract artist ID from URL
                artist_id_match = re.search(r'artist/([^/]+)/(\d+)', url)
                if artist_id_match:
                    artist_id = artist_id_match.group(2)
                    try:
                        artist_data = api.get_artist(artist_id)
                        download_type = options.get('artist_download_type', 'albums')
                        
                        if download_type == 'albums' and 'albums' in artist_data.get('relationships', {}):
                            # Add all album URLs
                            for album in artist_data['relationships']['albums']['data']:
                                album_url = f"https://music.apple.com/album/{album['id']}"
                                processed_urls.append(album_url)
                        elif download_type == 'music-videos' and 'music-videos' in artist_data.get('relationships', {}):
                            # Add all music video URLs
                            for mv in artist_data['relationships']['music-videos']['data']:
                                mv_url = f"https://music.apple.com/music-video/{mv['id']}"
                                processed_urls.append(mv_url)
                        else:
                            # Fallback to original URL if expansion fails
                            processed_urls.append(url)
                    except Exception as e:
                        print(f"Error expanding artist URL {url}: {e}")
                        processed_urls.append(url)
                else:
                    processed_urls.append(url)
            else:
                processed_urls.append(url)
        
        # Build command
        cmd = ['python3', '-m', 'gamdl']
        
        # Add options with performance optimizations
        cookies_path = options.get('cookies_path')
        
        # If no cookies path in options, check for cookies.txt in current directory
        if not cookies_path:
            default_cookies = os.path.join(os.getcwd(), 'cookies.txt')
            if os.path.exists(default_cookies):
                cookies_path = default_cookies
        
        # Add cookies parameter if we have a valid path
        if cookies_path and os.path.exists(cookies_path):
            cmd.extend(['-c', cookies_path])
        if options.get('output_path'):
            cmd.extend(['-o', options['output_path']])
        else:
            cmd.extend(['-o', DOWNLOAD_FOLDER])
        if options.get('language'):
            cmd.extend(['-l', options['language']])
        if options.get('cover_format'):
            cmd.extend(['--cover-format', options['cover_format']])
        if options.get('codec_song'):
            cmd.extend(['--codec-song', options['codec_song']])
        if options.get('quality_post'):
            cmd.extend(['--quality-post', options['quality_post']])
        if options.get('log_level'):
            cmd.extend(['--log-level', options['log_level']])
        
        # Performance optimizations (using valid options only)
        cmd.extend(['--no-exceptions'])  # Reduce exception verbosity for better performance
        
        # Boolean options
        if options.get('save_cover'):
            cmd.append('--save-cover')
        if options.get('save_playlist'):
            cmd.append('--save-playlist')
        if options.get('overwrite'):
            cmd.append('--overwrite')
        if options.get('no_synced_lyrics'):
            cmd.append('--no-synced-lyrics')
        if options.get('synced_lyrics_only'):
            cmd.append('--synced-lyrics-only')
        if options.get('disable_music_video_skip'):
            cmd.append('--disable-music-video-skip')
        
        # Add processed URLs
        cmd.extend(processed_urls)
        
        active_downloads[download_id] = {
            'status': 'running',
            'command': ' '.join(cmd),
            'start_time': time.time(),
            'download_folder': options.get('output_path', DOWNLOAD_FOLDER)
        }
        
        download_progress[download_id]['current_track'] = 'Starting download process...'
        
        # Run command with real-time output processing and timeout
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        
        output_lines = []
        start_time = time.time()
        timeout_seconds = 600  # 10 minute timeout
        
        while True:
            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break
                
            # Check for timeout
            if time.time() - start_time > timeout_seconds:
                process.terminate()
                process.wait()
                download_results[download_id] = {
                    'status': 'timeout',
                    'error': f'Download timed out after {timeout_seconds} seconds',
                    'command': ' '.join(cmd),
                    'end_time': time.time()
                }
                if download_id in active_downloads:
                    del active_downloads[download_id]
                if download_id in download_progress:
                    download_progress[download_id]['status'] = 'timeout'
                return
            
            if line:
                line = line.strip()
                output_lines.append(line)
                
                # Update progress based on output
                update_progress_from_line(download_id, line)
                
                # Keep only last 100 lines to prevent memory issues
                if len(output_lines) > 100:
                    output_lines = output_lines[-100:]
                
                download_progress[download_id]['output_lines'] = output_lines
        
        # Wait for process to complete
        process.wait()
        
        # Final result
        download_results[download_id] = {
            'status': 'completed' if process.returncode == 0 else 'failed',
            'returncode': process.returncode,
            'stdout': '\n'.join(output_lines),
            'stderr': '',
            'command': ' '.join(cmd),
            'end_time': time.time(),
            'progress': download_progress.get(download_id, {})
        }
        
        if download_id in active_downloads:
            del active_downloads[download_id]
        if download_id in download_progress:
            download_progress[download_id]['status'] = 'completed'
            
    except Exception as e:
        download_results[download_id] = {
            'status': 'error',
            'error': str(e),
            'command': ' '.join(cmd) if 'cmd' in locals() else 'Command not built',
            'end_time': time.time()
        }
        if download_id in active_downloads:
            del active_downloads[download_id]
        if download_id in download_progress:
            download_progress[download_id]['status'] = 'error'

@app.route('/')
def index():
    """Main page with basic and advanced options"""
    return render_template('index.html')

@app.route('/preview', methods=['POST'])
def preview_metadata():
    """Preview metadata for URLs before downloading"""
    try:
        data = request.get_json()
        urls = data.get('urls', '').strip()
        cookies_path = data.get('cookies_path')
        
        if not urls:
            return jsonify({'error': 'No URLs provided'}), 400
        
        metadata = get_metadata_from_urls(urls, cookies_path)
        
        # Check if any artist URLs are present
        has_artists = any(item.get('type') == 'artist' for item in metadata)
        
        return jsonify({
            'metadata': metadata,
            'total_estimated_tracks': sum(
                int(item['estimated_tracks']) if isinstance(item['estimated_tracks'], int) 
                else 1 for item in metadata
            ),
            'has_artists': has_artists
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/download', methods=['POST'])
def download():
    """Handle download request"""
    try:
        data = request.get_json()
        urls = data.get('urls', '').strip()
        mode = data.get('mode', 'basic')
        
        if not urls:
            return jsonify({'error': 'No URLs provided'}), 400
        
        # Check for artist URLs and validate options
        url_list = [url.strip() for url in urls.split('\n') if url.strip()]
        has_artists = any('artist' in url for url in url_list)
        
        if has_artists:
            artist_download_type = data.get('artist_download_type')
            if not artist_download_type:
                return jsonify({
                    'error': 'Artist URLs detected. Please specify what to download from artists.',
                    'requires_artist_options': True,
                    'artist_options': ['albums', 'music-videos']
                }), 400
        
        # Generate unique download ID
        download_id = str(uuid.uuid4())
        
        # Get metadata first to create appropriate folder
        cookies_path = data.get('cookies_path')
        
        # If no cookies path provided, check for cookies.txt in current directory
        if not cookies_path:
            default_cookies = os.path.join(os.getcwd(), 'cookies.txt')
            if os.path.exists(default_cookies):
                cookies_path = default_cookies
                
        metadata_list = get_metadata_from_urls(urls, cookies_path)
        
        # Build options based on mode
        options = {}
        base_output_path = data.get('output_path', DOWNLOAD_FOLDER)  # Get output_path for both modes
        
        if mode == 'basic':
            # Basic mode - use sensible defaults
            options = {
                'cookies_path': cookies_path,  # Use the cookies.txt if available
                'save_cover': True,
                'log_level': 'INFO'
            }
        else:
            # Advanced mode - use all provided options
            options = {
                'cookies_path': data.get('cookies_path') or cookies_path,  # Use uploaded cookies.txt if not specified
                'language': data.get('language'),
                'cover_format': data.get('cover_format'),
                'codec_song': data.get('codec_song'),
                'quality_post': data.get('quality_post'),
                'log_level': data.get('log_level', 'INFO'),
                'save_cover': data.get('save_cover', False),
                'save_playlist': data.get('save_playlist', False),
                'overwrite': data.get('overwrite', False),
                'no_synced_lyrics': data.get('no_synced_lyrics', False),
                'synced_lyrics_only': data.get('synced_lyrics_only', False),
                'disable_music_video_skip': data.get('disable_music_video_skip', False)
            }
        
        # Add artist download options if present
        if has_artists and 'artist_download_type' in data:
            options['artist_download_type'] = data['artist_download_type']
        
        # Create unique download folder immediately
        download_folder = create_download_folder(base_output_path, metadata_list, download_id)
        options['output_path'] = download_folder
        
        # Store download folder info for status tracking
        download_progress[download_id] = {
            'total_tracks': 0,
            'completed_tracks': 0,
            'current_track': 'Initializing...',
            'status': 'starting',
            'output_lines': [],
            'download_folder': download_folder,
            'metadata': metadata_list
        }
        
        # Start download in background thread
        thread = threading.Thread(
            target=run_gamdl_command,
            args=(download_id, urls, options)
        )
        thread.daemon = True
        thread.start()
        
        return jsonify({
            'download_id': download_id,
            'status': 'started',
            'message': 'Download started successfully',
            'download_folder': download_folder
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/upload_cookies', methods=['POST'])
def upload_cookies():
    """Handle cookies file upload and move to gamdl directory as cookies.txt"""
    try:
        if 'cookies_file' not in request.files:
            return jsonify({'error': 'No cookies file provided'}), 400
        
        file = request.files['cookies_file']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        
        if file:
            # Define the target path in the current directory (where gamdl runs)
            target_filepath = os.path.join(os.getcwd(), 'cookies.txt')
            
            # Save the file directly as cookies.txt in the gamdl directory
            file.save(target_filepath)
            
            # Verify the file was saved and has content
            if os.path.exists(target_filepath) and os.path.getsize(target_filepath) > 0:
                return jsonify({
                    'message': 'Cookies file uploaded and saved as cookies.txt',
                    'filepath': target_filepath
                })
            else:
                return jsonify({'error': 'Failed to save cookies file'}), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/cookies-status')
def cookies_status():
    """Check current cookies file status"""
    try:
        cookies_path = os.path.join(os.getcwd(), 'cookies.txt')
        current_dir = os.getcwd()
        
        status = {
            'current_directory': current_dir,
            'cookies_path': cookies_path,
            'cookies_exists': os.path.exists(cookies_path),
            'cookies_size': 0,
            'cookies_readable': False
        }
        
        if os.path.exists(cookies_path):
            try:
                status['cookies_size'] = os.path.getsize(cookies_path)
                with open(cookies_path, 'r') as f:
                    content = f.read().strip()
                    status['cookies_readable'] = len(content) > 0
                    status['cookies_lines'] = len(content.split('\n')) if content else 0
            except Exception as e:
                status['error'] = f"Error reading cookies file: {e}"
        
        return jsonify(status)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/status/<download_id>')
def get_status(download_id):
    """Get download status with detailed progress"""
    try:
        if download_id in active_downloads:
            progress_info = download_progress.get(download_id, {})
            return jsonify({
                'status': 'running',
                'details': active_downloads[download_id],
                'progress': progress_info,
                'download_folder': progress_info.get('download_folder', 'Unknown')
            })
        elif download_id in download_results:
            progress_info = download_progress.get(download_id, {})
            return jsonify({
                'status': 'completed',
                'details': download_results[download_id],
                'progress': progress_info,
                'download_folder': progress_info.get('download_folder', 'Unknown')
            })
        else:
            return jsonify({'error': 'Download ID not found'}), 404
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/downloads')
def list_downloads():
    """List all downloads (active and completed)"""
    try:
        downloads = []
        
        # Add active downloads
        for download_id, details in active_downloads.items():
            downloads.append({
                'id': download_id,
                'status': 'running',
                'details': details
            })
        
        # Add completed downloads
        for download_id, details in download_results.items():
            downloads.append({
                'id': download_id,
                'status': details['status'],
                'details': details
            })
        
        return jsonify({'downloads': downloads})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/system-info')
def get_system_info():
    """Get system information for path suggestions"""
    try:
        import platform
        
        info = {
            'current_directory': os.getcwd(),
            'downloads_folder': os.path.abspath(DOWNLOAD_FOLDER),
            'platform': platform.system(),
            'home_directory': os.path.expanduser('~'),
            'suggested_paths': []
        }
        
        # Add platform-specific suggested paths
        if platform.system() == 'Darwin':  # macOS
            info['suggested_paths'] = [
                {'path': '~/Music', 'name': 'Music folder'},
                {'path': '~/Downloads', 'name': 'Downloads folder'},
                {'path': '~/Desktop', 'name': 'Desktop'},
                {'path': './downloads', 'name': 'Local downloads folder'}
            ]
        elif platform.system() == 'Windows':
            info['suggested_paths'] = [
                {'path': '%USERPROFILE%\\Music', 'name': 'Music folder'},
                {'path': '%USERPROFILE%\\Downloads', 'name': 'Downloads folder'},
                {'path': '%USERPROFILE%\\Desktop', 'name': 'Desktop'},
                {'path': '.\\downloads', 'name': 'Local downloads folder'}
            ]
        else:  # Linux and others
            info['suggested_paths'] = [
                {'path': '~/Music', 'name': 'Music folder'},
                {'path': '~/Downloads', 'name': 'Downloads folder'},
                {'path': '~/Desktop', 'name': 'Desktop'},
                {'path': './downloads', 'name': 'Local downloads folder'}
            ]
        
        return jsonify(info)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/check-dependencies')
def check_dependencies():
    """Check system dependencies and report any issues"""
    try:
        results = {
            'ffmpeg': False,
            'ffmpeg_path': None,
            'python_deps': {},
            'performance': {},
            'recommendations': []
        }
        
        # Check FFmpeg
        try:
            result = subprocess.run(['which', 'ffmpeg'], capture_output=True, text=True)
            if result.returncode == 0:
                results['ffmpeg'] = True
                results['ffmpeg_path'] = result.stdout.strip()
                
                # Get FFmpeg version
                version_result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True)
                if version_result.returncode == 0:
                    version_line = version_result.stdout.split('\n')[0]
                    results['ffmpeg_version'] = version_line
            else:
                results['recommendations'].append('Install FFmpeg: brew install ffmpeg (macOS) or apt install ffmpeg (Linux)')
        except:
            results['recommendations'].append('FFmpeg not found - install it for audio processing')
        
        # Check Python dependencies
        critical_deps = ['requests', 'mutagen', 'PIL', 'pywidevine', 'm3u8', 'yt_dlp']
        for dep in critical_deps:
            try:
                if dep == 'PIL':
                    import PIL
                    results['python_deps'][dep] = f"✅ {PIL.__version__}"
                else:
                    module = __import__(dep)
                    version = getattr(module, '__version__', 'unknown')
                    results['python_deps'][dep] = f"✅ {version}"
            except ImportError:
                results['python_deps'][dep] = "❌ Missing"
                results['recommendations'].append(f'Install {dep}: pip install {dep}')
        
        # Quick performance test
        try:
            import tempfile
            import time
            
            with tempfile.NamedTemporaryFile() as tmp:
                test_data = b'0' * (1024 * 1024)  # 1MB
                
                start = time.time()
                tmp.write(test_data)
                tmp.flush()
                write_time = time.time() - start
                
                start = time.time()
                tmp.seek(0)
                tmp.read()
                read_time = time.time() - start
                
                results['performance'] = {
                    'write_speed_mbps': round(1 / write_time, 1) if write_time > 0 else 'N/A',
                    'read_speed_mbps': round(1 / read_time, 1) if read_time > 0 else 'N/A'
                }
                
                if write_time > 1 or read_time > 1:
                    results['recommendations'].append('Slow disk I/O detected - consider using SSD storage')
        except:
            results['performance'] = {'error': 'Could not test disk performance'}
        
        # Network test
        try:
            import requests
            start = time.time()
            response = requests.head('https://amp-api.music.apple.com', timeout=5)
            network_time = time.time() - start
            results['network'] = {
                'apple_music_reachable': response.status_code in [200, 404],
                'response_time_ms': round(network_time * 1000, 1)
            }
            
            if network_time > 2:
                results['recommendations'].append('Slow network detected - check internet connection')
        except:
            results['network'] = {'apple_music_reachable': False}
            results['recommendations'].append('Cannot reach Apple Music API - check internet connection')
        
        return jsonify(results)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/test-cookies', methods=['POST'])
def test_cookies():
    """Test Apple Music cookies validity and connectivity"""
    try:
        data = request.get_json()
        cookies_path = data.get('cookies_path')
        
        # If no cookies path provided, use the default cookies.txt
        if not cookies_path:
            cookies_path = os.path.join(os.getcwd(), 'cookies.txt')
        
        results = {
            'cookies_valid': False,
            'apple_music_connected': False,
            'authentication_status': 'unknown',
            'user_info': {},
            'test_results': [],
            'recommendations': []
        }
        
        if not os.path.exists(cookies_path):
            results['test_results'].append('❌ No cookies file found')
            results['recommendations'].append('Upload a cookies.txt file from your browser using the upload button above')
            return jsonify(results)
        
        # Test 1: Check if cookies file is readable and has content
        try:
            with open(cookies_path, 'r') as f:
                cookies_content = f.read().strip()
                if not cookies_content:
                    results['test_results'].append('❌ Cookies file is empty')
                    results['recommendations'].append('Export cookies again from your browser')
                    return jsonify(results)
                
                lines = cookies_content.split('\n')
                valid_lines = [line for line in lines if line.strip() and not line.startswith('#')]
                
                results['test_results'].append(f'✅ Cookies file readable ({len(valid_lines)} cookie entries)')
                
                # Check for Apple Music specific cookies
                apple_cookies = [line for line in valid_lines if 'apple.com' in line or 'music.apple.com' in line]
                if apple_cookies:
                    results['test_results'].append(f'✅ Found {len(apple_cookies)} Apple Music cookies')
                else:
                    results['test_results'].append('⚠️ No Apple Music cookies found')
                    results['recommendations'].append('Make sure to export cookies from music.apple.com')
                
        except Exception as e:
            results['test_results'].append(f'❌ Error reading cookies file: {str(e)}')
            results['recommendations'].append('Check file permissions and format')
            return jsonify(results)
        
        # Test 2: Try to initialize Apple Music API with cookies
        try:
            if AppleMusicApi:
                api = AppleMusicApi.from_netscape_cookies(Path(cookies_path))
                results['test_results'].append('✅ Apple Music API initialized successfully')
                results['cookies_valid'] = True
                
                # Test 3: Try to make a simple API request
                try:
                    # Try to get storefront info (doesn't require authentication)
                    import requests
                    import time
                    
                    start_time = time.time()
                    
                    # Get session from the API instance
                    session = api.session if hasattr(api, 'session') else requests.Session()
                    
                    # Test basic connectivity
                    response = session.get(
                        'https://amp-api.music.apple.com/v1/me/storefront',
                        timeout=10
                    )
                    
                    response_time = time.time() - start_time
                    
                    if response.status_code == 200:
                        results['test_results'].append(f'✅ Authenticated API request successful ({response_time:.2f}s)')
                        results['apple_music_connected'] = True
                        results['authentication_status'] = 'authenticated'
                        
                        # Try to parse user info
                        try:
                            user_data = response.json()
                            if 'data' in user_data and len(user_data['data']) > 0:
                                storefront = user_data['data'][0]
                                results['user_info'] = {
                                    'storefront': storefront.get('id', 'unknown'),
                                    'name': storefront.get('attributes', {}).get('name', 'unknown')
                                }
                                results['test_results'].append(f"✅ User storefront: {results['user_info']['name']} ({results['user_info']['storefront']})")
                        except:
                            pass
                            
                    elif response.status_code == 401:
                        results['test_results'].append('❌ Authentication failed - cookies may be expired')
                        results['authentication_status'] = 'expired'
                        results['recommendations'].append('Re-export cookies from your browser while logged into Apple Music')
                        
                    elif response.status_code == 403:
                        results['test_results'].append('❌ Access forbidden - account may not have Apple Music subscription')
                        results['authentication_status'] = 'no_subscription'
                        results['recommendations'].append('Ensure you have an active Apple Music subscription')
                        
                    else:
                        results['test_results'].append(f'⚠️ Unexpected response: {response.status_code}')
                        results['authentication_status'] = 'unknown_error'
                        
                except requests.exceptions.Timeout:
                    results['test_results'].append('❌ Request timeout - slow network or server issues')
                    results['recommendations'].append('Check internet connection and try again')
                    
                except requests.exceptions.ConnectionError:
                    results['test_results'].append('❌ Connection error - cannot reach Apple Music servers')
                    results['recommendations'].append('Check internet connection and DNS settings')
                    
                except Exception as e:
                    results['test_results'].append(f'❌ API request failed: {str(e)}')
                    results['recommendations'].append('Check network connectivity and cookies validity')
                
                # Test 4: Try a simple search to test full functionality
                if results['apple_music_connected']:
                    try:
                        search_response = session.get(
                            'https://amp-api.music.apple.com/v1/catalog/us/search',
                            params={'term': 'test', 'types': 'songs', 'limit': 1},
                            timeout=10
                        )
                        
                        if search_response.status_code == 200:
                            results['test_results'].append('✅ Search API test successful - full functionality available')
                        else:
                            results['test_results'].append(f'⚠️ Search test failed ({search_response.status_code}) - limited functionality')
                            
                    except Exception as e:
                        results['test_results'].append(f'⚠️ Search test error: {str(e)}')
                
            else:
                results['test_results'].append('❌ Apple Music API module not available')
                results['recommendations'].append('Check GAMDL installation')
                
        except Exception as e:
            results['test_results'].append(f'❌ Failed to initialize Apple Music API: {str(e)}')
            results['recommendations'].append('Check cookies format and try re-exporting from browser')
        
        # Test 5: Check GAMDL CLI functionality with cookies
        try:
            cmd = ['python3', '-m', 'gamdl', '--cookies-path', cookies_path, '--help']
            process = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            
            if process.returncode == 0:
                results['test_results'].append('✅ GAMDL CLI accepts cookies file')
            else:
                results['test_results'].append(f'⚠️ GAMDL CLI issue: {process.stderr[:100]}...')
                
        except Exception as e:
            results['test_results'].append(f'⚠️ GAMDL CLI test failed: {str(e)}')
        
        # Overall assessment
        if results['cookies_valid'] and results['apple_music_connected']:
            results['overall_status'] = 'excellent'
            results['test_results'].append('🎉 All tests passed - ready for downloads!')
        elif results['cookies_valid']:
            results['overall_status'] = 'good'
            results['test_results'].append('✅ Cookies valid but limited connectivity')
        else:
            results['overall_status'] = 'poor'
            results['test_results'].append('❌ Issues detected - downloads may fail')
        
        return jsonify(results)
        
    except Exception as e:
        return jsonify({'error': str(e), 'test_results': [f'❌ Test failed: {str(e)}']}), 500

if __name__ == '__main__':
    app.run(debug=True, host='127.0.0.1', port=5000)
