# =====================================================================
#  VIDEO SERVICE
#  Downloads, concatenates, and cleans up temporary video files.
# =====================================================================

import os
import tempfile

import requests

try:
    from moviepy.editor import VideoFileClip, concatenate_videoclips
except ImportError:
    from moviepy import VideoFileClip, concatenate_videoclips


# ── Download ────────────────────────────────────────────────────────

def download(url: str, filename: str) -> str | None:
    """Downloads a video from a URL to a temp directory."""
    filepath = os.path.join(tempfile.gettempdir(), filename)
    try:
        print(f"[INFO] Downloading video: {filename}...")
        resp = requests.get(url, stream=True, timeout=120)
        resp.raise_for_status()
        with open(filepath, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        print(f"[OK] Downloaded: {filename}")
        return filepath
    except requests.RequestException as e:
        print(f"[ERROR] Failed to download video: {e}")
        return None


# ── Combine ─────────────────────────────────────────────────────────

def combine(video1_path: str, video2_path: str, output_filename: str) -> str | None:
    """Concatenates two videos into one using MoviePy."""
    output_path = os.path.join(tempfile.gettempdir(), output_filename)
    try:
        print("[INFO] Combining videos with MoviePy...")
        clip1 = VideoFileClip(video1_path)
        clip2 = VideoFileClip(video2_path)
        final = concatenate_videoclips([clip1, clip2], method="compose")
        final.write_videofile(output_path, codec="libx264", audio_codec="aac", logger=None)
        clip1.close()
        clip2.close()
        final.close()
        print(f"[OK] Videos combined: {output_filename}")
        return output_path
    except Exception as e:
        print(f"[ERROR] Failed to combine videos: {e}")
        return None


# ── Cleanup ─────────────────────────────────────────────────────────

def cleanup_temp_files(*filepaths: str):
    """Silently removes temporary files."""
    for fp in filepaths:
        if fp and os.path.exists(fp):
            try:
                os.remove(fp)
            except OSError:
                pass
