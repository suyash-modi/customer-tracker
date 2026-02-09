from __future__ import annotations

"""
Video source handling for local demo:
- YouTube URL: resolved to a direct stream URL via yt-dlp, opened with OpenCV
- Webcam index (e.g. "0")
- Local file path

We treat everything as a single CCTV feed.

For HLS/m3u8 live streams, OpenCV's FFMPEG backend is used with appropriate
settings for low-latency streaming.
"""

import atexit
import queue
import re
import socket
import struct
import subprocess
import sys
import threading
import time
from typing import Optional, Union

import cv2
import numpy as np




# Global registry for FFmpeg processes (to clean up on exit)
_ffmpeg_processes: list[subprocess.Popen] = []
_ffmpeg_lock = threading.Lock()


def _cleanup_ffmpeg_processes() -> None:
    """Clean up any running FFmpeg processes on exit."""
    with _ffmpeg_lock:
        for proc in _ffmpeg_processes[:]:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            except Exception:
                pass
        _ffmpeg_processes.clear()


atexit.register(_cleanup_ffmpeg_processes)


def _find_free_port() -> int:
    """Find a free port for the local TCP server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def _check_ffmpeg_available() -> bool:
    """Check if FFmpeg is available on the system."""
    try:
        result = subprocess.run(
            ['ffmpeg', '-version'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        return False


class FFmpegVideoCapture:
    """
    A VideoCapture-like wrapper that reads frames from FFmpeg's stdout.
    
    FFmpeg outputs raw BGR24 video frames to stdout, which we read and decode.
    This allows us to handle DASH/HLS streams that OpenCV cannot open directly.
    """
    
    def __init__(self, ffmpeg_proc: subprocess.Popen, width: int, height: int):
        self.proc = ffmpeg_proc
        self.width = width
        self.height = height
        self.frame_size = width * height * 3  # BGR24 = 3 bytes per pixel
        self._opened = True
        self._last_frame: Optional[np.ndarray] = None
        
    def read(self):
        """Read a frame from FFmpeg's stdout."""
        if not self._opened or self.proc.poll() is not None:
            return False, None
        
        try:
            # Read raw frame data (BGR24 format)
            # Use read1() for unbuffered reading, or read() with exact size
            # For live streams, we want to read exactly frame_size bytes
            frame_data = b''
            remaining = self.frame_size
            
            # Read in chunks until we have a full frame
            # This handles cases where data arrives slowly
            while remaining > 0:
                chunk = self.proc.stdout.read(remaining)
                if not chunk:
                    # No more data available
                    return False, None
                frame_data += chunk
                remaining -= len(chunk)
            
            if len(frame_data) != self.frame_size:
                # Shouldn't happen, but safety check
                return False, None
            
            # Convert bytes to numpy array
            frame = np.frombuffer(frame_data, dtype=np.uint8)
            frame = frame.reshape((self.height, self.width, 3))
            
            self._last_frame = frame
            return True, frame
            
        except Exception as e:
            # Log the exception for debugging (but don't expose to user)
            return False, None
    
    def isOpened(self) -> bool:
        """Check if the capture is opened."""
        return self._opened and self.proc.poll() is None
    
    def release(self):
        """Release the capture and stop FFmpeg."""
        self._opened = False
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        
        with _ffmpeg_lock:
            if self.proc in _ffmpeg_processes:
                _ffmpeg_processes.remove(self.proc)
    
    def get(self, propId: int) -> float:
        """Get property (for compatibility with cv2.VideoCapture)."""
        if propId == cv2.CAP_PROP_FRAME_WIDTH:
            return float(self.width)
        elif propId == cv2.CAP_PROP_FRAME_HEIGHT:
            return float(self.height)
        elif propId == cv2.CAP_PROP_FPS:
            return 30.0  # Default FPS
        return 0.0


def _open_with_ffmpeg_pipe(stream_url: str, original_url: str) -> Union[cv2.VideoCapture, FFmpegVideoCapture]:
    """
    Use FFmpeg to transcode DASH/HLS streams to MPEG-TS format served via TCP.
    
    FFmpeg outputs MPEG-TS format to a TCP server, which OpenCV can connect to.
    This allows OpenCV to read DASH/HLS streams in real-time.
    """
    if not _check_ffmpeg_available():
        raise RuntimeError(
            f"FFmpeg is not available. DASH/HLS streams require FFmpeg.\n\n"
            f"Please install FFmpeg:\n"
            f"  Windows: Download from https://ffmpeg.org/download.html\n"
            f"  Or use: winget install ffmpeg\n\n"
            f"Alternatively, download the video first:\n"
            f"  yt-dlp -o video.mp4 \"{original_url}\"\n"
            f"Then use the local file path as the source."
        )
    
    # FFmpeg command to transcode DASH/HLS to raw BGR24 video frames
    # We output raw frames to stdout so we can read them directly in Python
    # First, we need to probe the stream to get dimensions
    # Try ffprobe first (more reliable)
    width, height = None, None
    
    try:
        probe_cmd = [
            'ffprobe',
            '-v', 'error',
            '-select_streams', 'v:0',
            '-show_entries', 'stream=width,height',
            '-of', 'csv=p=0',
            stream_url,
        ]
        probe_out = subprocess.check_output(probe_cmd, stderr=subprocess.DEVNULL, timeout=10, text=True).strip()
        if probe_out:
            parts = probe_out.split(',')
            if len(parts) == 2:
                width, height = int(parts[0]), int(parts[1])
    except Exception:
        pass
    
    # If ffprobe failed, try using FFmpeg to probe
    if width is None or height is None:
        try:
            ffmpeg_probe_cmd = [
                'ffmpeg',
                '-i', stream_url,
                '-t', '0.1',  # Very short duration just to probe
                '-f', 'null',
                '-',
            ]
            probe_proc = subprocess.run(
                ffmpeg_probe_cmd,
                stderr=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                timeout=5
            )
            # Parse dimensions from stderr (FFmpeg outputs stream info there)
            stderr_text = probe_proc.stderr.decode('utf-8', errors='ignore')
            # Look for "Stream #0:0: Video: ... 1920x1080" pattern
            match = re.search(r'(\d{3,5})x(\d{3,5})', stderr_text)
            if match:
                width, height = int(match.group(1)), int(match.group(2))
        except Exception:
            pass
    
    # If we still don't have dimensions, use defaults
    # FFmpeg will handle scaling if needed
    if width is None or height is None:
        width, height = 1920, 1080  # Common HD resolution
    
    # FFmpeg command to output raw BGR24 frames to stdout
    # Add flags for better live stream handling with low latency
    ffmpeg_cmd = [
        'ffmpeg',
        '-fflags', 'nobuffer',  # Reduce buffering for low latency
        '-flags', 'low_delay',  # Low delay mode
        '-strict', 'experimental',  # Allow experimental codecs
        '-analyzeduration', '1000000',  # Reduce analysis time (1 second in microseconds)
        '-probesize', '1000000',  # Reduce probe size for faster startup
        '-i', stream_url,  # Input DASH/HLS stream
        '-f', 'rawvideo',  # Raw video format
        '-pix_fmt', 'bgr24',  # BGR24 pixel format (OpenCV's native format)
        '-loglevel', 'error',  # Only show errors to reduce stderr noise
        '-',  # Output to stdout
    ]
    
    try:
        # Start FFmpeg process with stdout as pipe
        # Use unbuffered mode for faster frame delivery
        proc = subprocess.Popen(
            ffmpeg_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            bufsize=0,  # Unbuffered for real-time streaming
        )
        
        # Register for cleanup
        with _ffmpeg_lock:
            _ffmpeg_processes.append(proc)
        
        # Create our custom VideoCapture wrapper
        cap = FFmpegVideoCapture(proc, width, height)
        
        # Wait a moment for FFmpeg to start producing frames
        # For live streams, FFmpeg might need a few seconds to connect and start streaming
        max_retries = 15  # Increased retries for slow streams
        retry_delay = 0.5
        
        # Give FFmpeg time to connect to the stream and start producing frames
        # For live streams, this can take a few seconds
        # Check stderr in a separate thread to see if FFmpeg is making progress
        stderr_queue = queue.Queue()
        
        def read_stderr():
            """Read FFmpeg stderr in background to check for errors/progress."""
            try:
                if proc.stderr:
                    while proc.poll() is None:
                        line = proc.stderr.readline()
                        if line:
                            stderr_queue.put(line.decode('utf-8', errors='ignore'))
            except Exception:
                pass
        
        stderr_thread = threading.Thread(target=read_stderr, daemon=True)
        stderr_thread.start()
        
        # Wait a bit longer for live streams to connect
        # Also check if stdout has any data available (indicates FFmpeg is producing output)
        time.sleep(2.0)
        
        # Check if stdout has data available (non-blocking check)
        # This helps us know if FFmpeg is actually producing frames
        stdout_has_data = False
        try:
            # On Windows, we can't easily check if data is available without reading
            # So we'll just proceed and let the read() handle it
            stdout_has_data = True  # Assume data will be available
        except Exception:
            pass
        
        # Check if there are any error messages in stderr
        error_messages = []
        while not stderr_queue.empty():
            try:
                error_messages.append(stderr_queue.get_nowait())
            except queue.Empty:
                break
        
        for attempt in range(max_retries):
            # Check if FFmpeg process is still running
            if proc.poll() is not None:
                # FFmpeg exited, get error message
                stderr_output = "Unknown error"
                try:
                    if proc.stderr:
                        # Try to read available stderr output
                        # Note: This might block briefly, but since the process has exited, it should be quick
                        try:
                            stderr_bytes = proc.stderr.read(8192)  # Read up to 8KB
                            if stderr_bytes:
                                stderr_output = stderr_bytes.decode('utf-8', errors='ignore')
                        except Exception:
                            pass
                except Exception:
                    stderr_output = "Could not read error output"
                
                cap.release()
                raise RuntimeError(
                    f"FFmpeg process exited unexpectedly:\n{stderr_output[:500]}\n\n"
                    f"Common causes:\n"
                    f"- Invalid stream URL\n"
                    f"- Network connectivity issues\n"
                    f"- Stream format not supported\n\n"
                    f"Try downloading the video instead:\n"
                    f"  yt-dlp -o video.mp4 \"{original_url}\""
                )
            
            # Try reading a frame
            ok, frame = cap.read()
            if ok and frame is not None:
                # Success! Frame read successfully
                # Verify frame dimensions match what we expect
                if frame.shape[0] != height or frame.shape[1] != width:
                    # Dimensions don't match - this shouldn't happen but handle it
                    cap.release()
                    raise RuntimeError(
                        f"Frame dimensions mismatch: expected {width}x{height}, got {frame.shape[1]}x{frame.shape[0]}\n\n"
                        f"This may indicate an issue with the stream format.\n"
                        f"Try downloading the video instead:\n"
                        f"  yt-dlp -o video.mp4 \"{original_url}\""
                    )
                break
            
            # If this is not the last attempt, wait and retry
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                # Check for new error messages
                while not stderr_queue.empty():
                    try:
                        error_messages.append(stderr_queue.get_nowait())
                    except queue.Empty:
                        break
            else:
                # Last attempt failed - check if FFmpeg is still running
                if proc.poll() is None:
                    # FFmpeg is still running but not producing frames
                    # Collect any remaining error messages
                    while not stderr_queue.empty():
                        try:
                            error_messages.append(stderr_queue.get_nowait())
                        except queue.Empty:
                            break
                    
                    error_info = ""
                    if error_messages:
                        error_info = f"\n\nFFmpeg output:\n{''.join(error_messages[-5:])}"  # Last 5 messages
                    
                    cap.release()
                    raise RuntimeError(
                        f"FFmpeg is running but no frames received after {max_retries * retry_delay:.1f} seconds.\n\n"
                        f"This may be due to:\n"
                        f"- Very slow network connection\n"
                        f"- Stream buffering delay\n"
                        f"- Stream format issues\n"
                        f"- FFmpeg unable to decode the stream{error_info}\n\n"
                        f"Try downloading the video instead:\n"
                        f"  yt-dlp -o video.mp4 \"{original_url}\""
                    )
                else:
                    # FFmpeg exited
                    cap.release()
                    raise RuntimeError(
                        f"FFmpeg process exited while trying to read frames.\n\n"
                        f"Try downloading the video instead:\n"
                        f"  yt-dlp -o video.mp4 \"{original_url}\""
                    )
        
        # Success! Return our custom VideoCapture
        return cap
        
    except RuntimeError:
        # Re-raise RuntimeError as-is
        raise
    except Exception as e:
        # Clean up on error
        try:
            if 'proc' in locals():
                proc.terminate()
                with _ffmpeg_lock:
                    if proc in _ffmpeg_processes:
                        _ffmpeg_processes.remove(proc)
        except Exception:
            pass
        
        raise RuntimeError(
            f"Failed to set up FFmpeg piping: {e}\n\n"
            f"Try downloading the video instead:\n"
            f"  yt-dlp -o video.mp4 \"{original_url}\""
        ) from e


def _is_int_string(s: str) -> bool:
    try:
        int(s)
        return True
    except Exception:
        return False


def _resolve_youtube_stream_url(url: str) -> str:
    """
    Uses yt_dlp Python library to extract direct stream URL from YouTube.
    
    Handles both regular videos and live streams, preferring HLS/m3u8 for live streams
    which OpenCV can handle with FFMPEG backend.
    """
    try:
        import yt_dlp
    except ImportError:
        raise RuntimeError(
            "yt-dlp is not installed. Install it with: pip install yt-dlp"
        )
    
    try:
        # First, check if it's a live stream
        ydl_opts_check = {
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts_check) as ydl:
            info = ydl.extract_info(url, download=False)
            
            if not info:
                raise RuntimeError("Video information not available. The video might be private, deleted, or restricted.")
            
            if 'availability' in info and info.get('availability') != 'public':
                raise RuntimeError(f"Video is not available: {info.get('availability', 'unknown')}. The video might be private, unlisted, or restricted.")
            
            is_live = info.get('is_live', False)
            live_status = info.get('live_status', 'not_live')
            
            # Handle live streams - use HLS format which OpenCV can handle with FFMPEG backend
            if is_live or live_status in ['is_live', 'was_live']:
                ydl_opts_live = {
                    'format': 'best[protocol=m3u8_native]/best[ext=mp4]/best',
                    'quiet': False,
                    'no_warnings': False,
                    'noplaylist': True,
                }
                
                with yt_dlp.YoutubeDL(ydl_opts_live) as ydl_live:
                    info_live = ydl_live.extract_info(url, download=False)
                    
                    if 'formats' in info_live:
                        # Prefer HLS formats for live streams
                        formats_by_height = []
                        for fmt in info_live['formats']:
                            height = fmt.get('height', 0) or fmt.get('resolution_height', 0) or 0
                            if height > 0:
                                formats_by_height.append((height, fmt))
                        
                        # Sort by preference: HLS > MP4, then by height (prefer 360-480p)
                        formats_by_height.sort(key=lambda x: (
                            0 if x[1].get('protocol') == 'm3u8_native' else 1,  # HLS first
                            0 if 360 <= x[0] <= 480 else (1 if x[0] > 480 else 2),  # 360-480p preferred
                            abs(x[0] - 420),  # Closest to 420p
                            -x[0]  # Then higher resolution
                        ))
                        
                        for height, fmt in formats_by_height:
                            if fmt.get('protocol') == 'm3u8_native' and 'url' in fmt:
                                hls_url = fmt['url']
                                return hls_url
                            elif 'url' in fmt and fmt.get('ext') == 'mp4':
                                mp4_url = fmt['url']
                                return mp4_url
                    
                    # Fallback to best available
                    if 'url' in info_live:
                        return info_live['url']
                    
                    raise RuntimeError("Live stream URL not available. The stream might have ended or is not accessible.")
            
            # For regular videos, prefer 360p-480p MP4
            ydl_opts = {
                'format': 'bestvideo[height>=360][height<=480][ext=mp4][protocol=https]+bestaudio[ext=m4a]/bestvideo[height>=360][height<=480][ext=mp4]+bestaudio/best[height>=360][height<=480][ext=mp4]/best[height>=360][ext=mp4]/best[ext=mp4]/best',
                'quiet': False,
                'no_warnings': False,
                'noplaylist': True,
                'ignoreerrors': False,
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
            
            if 'url' in info:
                return info['url']
            
            if 'requested_formats' in info and len(info['requested_formats']) > 0:
                for fmt in info['requested_formats']:
                    if 'url' in fmt:
                        return fmt['url']
            
            raise RuntimeError("No valid stream URL found in YouTube video info")
            
    except Exception as e:
        error_msg = str(e)
        if 'not available' in error_msg.lower():
            raise RuntimeError("This YouTube video is not available. It might be private, deleted, or the live stream recording has ended.")
        else:
            raise RuntimeError(f"Failed to extract YouTube stream: {error_msg}")


def open_video_source(source: Union[str, int]) -> cv2.VideoCapture:
    """
    Returns an OpenCV VideoCapture for a given source.

    `source`:
    - YouTube URL (https://...)
    - local path
    - webcam index string like "0" (or int 0)
    """
    if isinstance(source, int):
        cap = cv2.VideoCapture(source)
    else:
        src = str(source).strip()
        if _is_int_string(src):
            cap = cv2.VideoCapture(int(src))
        elif src.startswith("http://") or src.startswith("https://"):
            # For YouTube/HTTP streams, resolve URL and try OpenCV with FFMPEG backend
            try:
                stream_url = _resolve_youtube_stream_url(src)
                
                # Check if this is an HLS/m3u8 stream (OpenCV can handle with FFMPEG backend)
                is_hls = (
                    ".m3u8" in stream_url or 
                    "/hls/" in stream_url.lower() or
                    "m3u8" in stream_url.lower()
                )
                
                # Use FFMPEG backend for all HTTP streams (especially HLS)
                # Set appropriate properties for live streaming
                cap = cv2.VideoCapture(stream_url, cv2.CAP_FFMPEG)
                
                if is_hls:
                    # For HLS streams, configure for low latency
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 3)
                    cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 15000)
                    cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 10000)
                else:
                    # For regular HTTP streams
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 15000)
                    cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 10000)
                
                if not cap.isOpened():
                    # Fallback: try default backend
                    cap = cv2.VideoCapture(stream_url)
                    if not cap.isOpened():
                        raise RuntimeError(
                            f"OpenCV could not open the YouTube stream URL: {stream_url[:100]}...\n\n"
                            f"This may happen with some stream formats.\n"
                            f"Try downloading first: yt-dlp -o video.mp4 \"{src}\"\n"
                            f"Then use the local file path as the source."
                        )
                
                # Test read one frame to ensure it's actually working
                # For live streams, this might take a moment
                ok, frame = cap.read()
                if not ok or frame is None:
                    # Retry once after a short delay (live streams need time to start)
                    time.sleep(1.0)
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        cap.release()
                        raise RuntimeError(
                            f"YouTube stream opened but no frames could be read.\n\n"
                            f"This may be a live stream buffering issue.\n"
                            f"Try downloading first: yt-dlp -o video.mp4 \"{src}\"\n"
                            f"Then use the local file path as the source."
                        )
            except RuntimeError:
                # Re-raise RuntimeError as-is (already has good message)
                raise
            except Exception as e:
                # For other errors, provide helpful guidance
                raise RuntimeError(
                    f"Could not open YouTube stream: {e}. "
                    f"Many YouTube live streams use DASH/HLS formats that OpenCV cannot open directly. "
                    f"Workaround: Download the video first with: yt-dlp -o video.mp4 \"{src}\", "
                    f"then use the local file path in the source field."
                )
        else:
            cap = cv2.VideoCapture(src)

    if not cap.isOpened():
        raise RuntimeError(f"Could not open video source: {source}")
    return cap


def probe_video_source(source: Union[str, int]) -> None:
    """
    Lightweight check used by the API to fail fast:
    - Tries to open the source
    - Reads a single frame
    - Raises RuntimeError with a clear message if anything fails
    """
    cap = None
    try:
        cap = open_video_source(source)
        ok, frame = cap.read()
        if not ok or frame is None:
            raise RuntimeError(f"Source opened but no frames could be read: {source}")
    finally:
        if cap is not None:
            cap.release()


