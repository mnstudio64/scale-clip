import os
import subprocess
import tempfile
import uuid
import shutil
from typing import Optional

import requests
from cog import BasePredictor, Input, Path


def _is_url(s: str) -> bool:
    return s.startswith("http://") or s.startswith("https://")


def _download(url: str, dst: str) -> None:
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(dst, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)


def _ffprobe_has_audio(video_path: str) -> bool:
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=index",
        "-of", "csv=p=0",
        video_path
    ]
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode == 0 and p.stdout.strip() != ""


def _ffprobe_remove_audio(video_path: str, out_path: str) -> None:
    # Strip audio (used when we need clean video before mixing)
    cmd = ["ffmpeg", "-y", "-i", video_path, "-c:v", "copy", "-an", out_path]
    subprocess.check_call(cmd)


def _draw_text(video_in: str, video_out: str, top_text: str, bottom_text: str, name: str, include_branding: bool) -> None:
    # Simple, safe-ish escaping for drawtext
    def esc(t: str) -> str:
        return (
            t.replace("\\", "\\\\")
             .replace(":", "\\:")
             .replace("'", "\\'")
             .replace("\n", "\\n")
        )

    filters = []

    # Top text
    if top_text.strip():
        filters.append(
            "drawtext="
            "fontfile=/src/public/font/NotoSans-Bold.ttf:"
            f"text='{esc(top_text)}':"
            "fontsize=h/14:"
            "fontcolor=white:"
            "borderw=3:bordercolor=black:"
            "x=(w-text_w)/2:"
            "y=h*0.04"
        )

    # Bottom text
    if bottom_text.strip():
        filters.append(
            "drawtext="
            "fontfile=/src/public/font/NotoSans-Bold.ttf:"
            f"text='{esc(bottom_text)}':"
            "fontsize=h/14:"
            "fontcolor=white:"
            "borderw=3:bordercolor=black:"
            "x=(w-text_w)/2:"
            "y=h-(text_h+h*0.06)"
        )

    # Branding (ONLY name, no luna.fun/memes)
    if include_branding and name.strip():
        filters.append(
            "drawtext="
            "fontfile=/src/public/font/NotoSans-Bold.ttf:"
            f"text='{esc(name)}':"
            "fontsize=h/28:"
            "fontcolor=white:"
            "borderw=2:bordercolor=black:"
            "x=20:"
            "y=h-(text_h+20)"
        )

    vf = ",".join(filters) if filters else "null"

    cmd = [
        "ffmpeg", "-y",
        "-i", video_in,
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-map", "0:v:0",
        "-map", "0:a?",
        "-c:a", "aac",
        "-b:a", "192k",
        video_out
    ]
    subprocess.check_call(cmd)


def _mix_music(video_in: str, music_path: str, out_path: str) -> None:
    # Mix original audio (if any) + bg music (trim to video)
    has_audio = _ffprobe_has_audio(video_in)

    if has_audio:
        # [0:a] + [1:a] -> amix
        cmd = [
            "ffmpeg", "-y",
            "-i", video_in,
            "-i", music_path,
            "-filter_complex",
            "[1:a]volume=0.30[a1];[0:a][a1]amix=inputs=2:duration=longest:dropout_transition=0[outa]",
            "-map", "0:v:0",
            "-map", "[outa]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            out_path
        ]
    else:
        # no original audio -> just add music
        cmd = [
            "ffmpeg", "-y",
            "-i", video_in,
            "-i", music_path,
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            out_path
        ]

    subprocess.check_call(cmd)


class Predictor(BasePredictor):
    def predict(
        self,
        video: str = Input(description="Video File/URL (paste a direct .mp4 URL)."),
        top_text: str = Input(description="Top text (optional)", default=""),
        bottom_text: str = Input(description="Bottom text (optional)", default=""),
        name: str = Input(description="Branding name (optional)", default=""),
        include_branding: bool = Input(description="Draw name at bottom-left", default=True),
        music_url: str = Input(description="Music URL (optional)", default=""),
    ) -> Path:
        work = tempfile.mkdtemp(prefix="clipforge_")
        try:
            in_path = os.path.join(work, "input.mp4")
            mid_path = os.path.join(work, "text.mp4")
            out_path = os.path.join(work, f"output_{uuid.uuid4().hex}.mp4")

            # Get video
            if _is_url(video):
                _download(video, in_path)
            else:
                # If you later decide to support file upload, change this input to Path.
                raise ValueError("This model currently expects a direct video URL.")

            # Draw texts + branding
            _draw_text(in_path, mid_path, top_text, bottom_text, name, include_branding)

            # Optional music
            if music_url.strip():
                music_path = os.path.join(work, "music.mp3")
                _download(music_url, music_path)
                _mix_music(mid_path, music_path, out_path)
            else:
                shutil.copyfile(mid_path, out_path)

            return Path(out_path)

        finally:
            # Cog will copy the returned file out; safe to cleanup after.
            shutil.rmtree(work, ignore_errors=True)
