import json
import re
import time
import zipfile
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import requests
from cog import BasePredictor, Input, Path as CogPath

# ------------------ Paths (match your repo) ------------------

FONT_DIR = Path("public/fonts")
TEMP_DIR = Path("/tmp/clipforge")
TEMP_DIR.mkdir(parents=True, exist_ok=True)

BRAND_PREFIX = "luna.fun/memes/"

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}

FONTS = {
    "english": FONT_DIR / "NotoSans-Bold.ttf",
    "chinese": FONT_DIR / "NotoSansSC-Bold.ttf",
    "japanese": FONT_DIR / "NotoSansJP-Bold.ttf",
    "korean": FONT_DIR / "NotoSansKR-Bold.ttf",
    "arabic": FONT_DIR / "NotoSansArabic-Bold.ttf",
    "bengali": FONT_DIR / "NotoSansBengali-Bold.ttf",
    "tamil": FONT_DIR / "NotoSansTamil-Bold.ttf",
    "thai": FONT_DIR / "NotoSansThai-Bold.ttf",
    "tagalog": FONT_DIR / "NotoSansTagalog-Regular.ttf",
}

# ------------------ Helpers ------------------

def run(cmd: list[str]) -> None:
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        err = p.stderr.decode("utf-8", errors="ignore")[-3000:]
        raise RuntimeError(f"Command failed:\n{err}")

def ffprobe_json(path: Path) -> dict:
    cmd = [
        "ffprobe", "-v", "error",
        "-print_format", "json",
        "-show_format", "-show_streams",
        str(path)
    ]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        err = p.stderr.decode("utf-8", errors="ignore")[-2000:]
        raise RuntimeError(f"ffprobe failed:\n{err}")
    return json.loads(p.stdout.decode("utf-8", errors="ignore"))

def get_video_dims(path: Path) -> tuple[int, int]:
    meta = ffprobe_json(path)
    for s in meta.get("streams", []):
        if s.get("codec_type") == "video":
            return int(s["width"]), int(s["height"])
    raise RuntimeError("No video stream found")

def get_duration(path: Path) -> float:
    meta = ffprobe_json(path)
    d = meta.get("format", {}).get("duration", None)
    return float(d) if d else 0.0

def has_audio_stream(path: Path) -> bool:
    meta = ffprobe_json(path)
    return any(s.get("codec_type") == "audio" for s in meta.get("streams", []))

def url_ext(url: str) -> str:
    try:
        return Path(urlparse(url).path).suffix.lower()
    except Exception:
        return ""

def safe_slug(s: str, fallback="clipforge") -> str:
    s = (s or "").strip()
    if not s:
        return fallback
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-zA-Z0-9._-]", "", s)
    s = s.strip("._-")
    return s[:80] if s else fallback

def download(url: str, out: Path, timeout=40) -> None:
    r = requests.get(url, stream=True, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 1024):
            if chunk:
                f.write(chunk)
    if not out.exists() or out.stat().st_size == 0:
        raise RuntimeError("Downloaded file is empty or missing")

def detect_language(text: str) -> str:
    if not text:
        return "english"
    for ch in text:
        code = ord(ch)
        if (0x4E00 <= code <= 0x9FFF) or (0x3400 <= code <= 0x4DBF):
            return "chinese"
        if (0x3040 <= code <= 0x309F) or (0x30A0 <= code <= 0x30FF):
            return "japanese"
        if 0xAC00 <= code <= 0xD7AF:
            return "korean"
        if 0x0E00 <= code <= 0x0E7F:
            return "thai"
        if 0x0B80 <= code <= 0x0BFF:
            return "tamil"
        if 0x0980 <= code <= 0x09FF:
            return "bengali"
        if 0x0600 <= code <= 0x06FF:
            return "arabic"
    return "english"

def font_for_text(text: str, fallback="english") -> Path:
    lang = detect_language(text)
    fp = FONTS.get(lang, FONTS.get(fallback))
    if fp and fp.exists():
        return fp
    return FONTS["english"]

def escape_drawtext(text: str) -> str:
    if not text:
        return ""
    s = text
    s = s.replace("\\", "\\\\")
    s = s.replace("'", "\\\\'")
    s = s.replace(":", "\\:")
    s = s.replace("[", "\\[")
    s = s.replace("]", "\\]")
    s = s.replace(",", "\\,")
    s = s.replace(";", "\\;")
    s = s.replace("\n", "\\n")
    return s

def wrap_text_simple(text: str, max_chars=35) -> str:
    if not text:
        return ""
    words = text.split(" ")
    if len(words) <= 1:
        return "\n".join([text[i:i+max_chars] for i in range(0, len(text), max_chars)])
    lines = []
    cur = ""
    for w in words:
        test = (cur + " " + w).strip() if cur else w
        if len(test) > max_chars and cur:
            lines.append(cur)
            cur = w
        else:
            cur = test
    if cur:
        lines.append(cur)
    return "\n".join(lines)

def make_concat_list(video_paths: list[Path], list_path: Path) -> None:
    content = "\n".join([f"file '{str(p)}'" for p in video_paths]) + "\n"
    list_path.write_text(content, encoding="utf-8")

# ------------------ Core FFmpeg ops ------------------

def concat_videos(video_paths: list[Path], out: Path) -> None:
    # re-encode each to consistent settings, then concat demuxer
    tmp_re = []
    for i, vp in enumerate(video_paths):
        rec = out.parent / f"rec_{i}.mp4"
        run([
            "ffmpeg","-y","-i", str(vp),
            "-c:v","libx264","-preset","fast","-crf","18",
            "-pix_fmt","yuv420p","-r","30",
            "-c:a","aac","-b:a","192k",
            str(rec)
        ])
        tmp_re.append(rec)

    list_path = out.parent / "concat_list.txt"
    make_concat_list(tmp_re, list_path)

    run([
        "ffmpeg","-y",
        "-f","concat","-safe","0",
        "-i", str(list_path),
        "-c","copy",
        str(out)
    ])

    for p in tmp_re:
        p.unlink(missing_ok=True)
    list_path.unlink(missing_ok=True)

def mix_audio(video_in: Path, dialogue: Path | None, music: Path | None, out: Path, music_volume=0.3) -> None:
    if not dialogue and not music:
        run(["ffmpeg","-y","-i", str(video_in), "-c","copy", str(out)])
        return

    duration = get_duration(video_in)
    inputs = ["-i", str(video_in)]
    filter_parts = []
    mix_labels = []

    if has_audio_stream(video_in):
        filter_parts.append("[0:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[orig]")
        mix_labels.append("[orig]")

    idx = 1
    if dialogue:
        inputs += ["-i", str(dialogue)]
        filter_parts.append(f"[{idx}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[dlg]")
        mix_labels.append("[dlg]")
        idx += 1

    if music:
        inputs += ["-i", str(music)]
        filter_parts.append(f"[{idx}:a]atrim=duration={duration},volume={music_volume},aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[msc]")
        mix_labels.append("[msc]")
        idx += 1

    if len(mix_labels) == 0:
        run(["ffmpeg","-y","-i", str(video_in), "-c","copy", str(out)])
        return

    if len(mix_labels) == 1:
        filter_parts.append(f"{mix_labels[0]}anull[outa]")
    else:
        filter_parts.append("".join(mix_labels) + f"amix=inputs={len(mix_labels)}:duration=longest:dropout_transition=0[outa]")

    filter_complex = ";".join(filter_parts)

    run([
        "ffmpeg","-y", *inputs,
        "-filter_complex", filter_complex,
        "-map","0:v:0",
        "-map","[outa]",
        "-c:v","copy",
        "-c:a","aac","-b:a","192k",
        "-t", str(duration),
        str(out)
    ])

def add_branding_and_meme_text(
    video_in: Path,
    out: Path,
    project_name: str,
    top_text: str,
    bottom_text: str,
    include_branding: bool,
) -> None:
    w, h = get_video_dims(video_in)

    # Single font for meme text (mixed languages)
    meme_font = font_for_text(f"{top_text} {bottom_text}")

    # Branding fonts: prefix always English, project uses detected font
    eng_font = FONTS["english"]
    proj_font = font_for_text(project_name or "")

    # wrap text
    top_wrapped = wrap_text_simple(top_text, 35)
    bot_wrapped = wrap_text_simple(bottom_text, 35)

    # meme sizing
    max_lines = max(
        len([x for x in top_wrapped.split("\n") if x.strip()]) if top_text else 1,
        len([x for x in bot_wrapped.split("\n") if x.strip()]) if bottom_text else 1,
        1,
    )
    font_size = max(18, int(h / (14 + (max_lines - 1) * 2)))
    stroke = max(2, int(font_size / 10))
    line_h = int(font_size * 1.3)
    top_pad = int(h * 0.04)
    bottom_offset = int(h * 0.08)

    # branding placement (bottom-left)
    brand_size = 18
    brand_x = 20
    brand_y = h - brand_size - 20

    prefix = BRAND_PREFIX
    prefix_esc = escape_drawtext(prefix)
    proj_esc = escape_drawtext(project_name or "")

    prefix_width = int(len(prefix) * 0.58 * brand_size)
    proj_x = brand_x + prefix_width
    proj_y = brand_y if detect_language(project_name or "") == "english" else brand_y - 2

    parts = []
    cur = "[0:v]"
    label_idx = 1

    # TOP lines
    if top_text.strip():
        for i, line in enumerate([ln for ln in top_wrapped.split("\n") if ln.strip()]):
            line_esc = escape_drawtext(line)
            y = top_pad + i * line_h
            nxt = f"[v{label_idx}]"
            parts.append(
                f"{cur}drawtext=fontfile='{meme_font}':text='{line_esc}':"
                f"fontcolor=white:fontsize={font_size}:bordercolor=black:borderw={stroke}:"
                f"shadowcolor=black@0.5:shadowx=2:shadowy=2:x=(w-text_w)/2:y={y}{nxt}"
            )
            cur = nxt
            label_idx += 1

    # BOTTOM lines
    if bottom_text.strip():
        bot_lines = [ln for ln in bot_wrapped.split("\n") if ln.strip()]
        total_h = len(bot_lines) * line_h
        for i, line in enumerate(bot_lines):
            line_esc = escape_drawtext(line)
            y = h - total_h - bottom_offset + i * line_h
            nxt = f"[v{label_idx}]"
            parts.append(
                f"{cur}drawtext=fontfile='{meme_font}':text='{line_esc}':"
                f"fontcolor=white:fontsize={font_size}:bordercolor=black:borderw={stroke}:"
                f"shadowcolor=black@0.5:shadowx=2:shadowy=2:x=(w-text_w)/2:y={y}{nxt}"
            )
            cur = nxt
            label_idx += 1

    # Branding
    if include_branding:
        nxt = f"[v{label_idx}]"
        parts.append(
            f"{cur}drawtext=fontfile='{eng_font}':text='{prefix_esc}':"
            f"fontcolor=white:fontsize={brand_size}:bordercolor=black:borderw=1:"
            f"shadowcolor=black@0.5:shadowx=2:shadowy=2:x={brand_x}:y={brand_y}:line_spacing=0{nxt}"
        )
        cur = nxt
        label_idx += 1

        if (project_name or "").strip():
            parts.append(
                f"{cur}drawtext=fontfile='{proj_font}':text='{proj_esc}':"
                f"fontcolor=white:fontsize={brand_size}:bordercolor=black:borderw=1:"
                f"shadowcolor=black@0.5:shadowx=2:shadowy=2:x={proj_x}:y={proj_y}:line_spacing=0[outv]"
            )
        else:
            parts.append(f"{cur}null[outv]")
    else:
        parts.append(f"{cur}null[outv]")

    filter_complex = ";".join(parts)

    run([
        "ffmpeg","-y",
        "-i", str(video_in),
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-map", "0:a?",
        "-c:v","libx264","-preset","fast","-crf","18",
        "-c:a","copy",
        str(out)
    ])

# ------------------ Predictor ------------------

class Predictor(BasePredictor):
    def predict(
        self,
        # Single-video mode
        final_stitch_video: str = Input(description="Main video URL (single mode)", default=""),
        final_stitched_video: str = Input(description="Alias of final_stitch_video", default=""),

        # Optional stitch mode (3 scenes)
        scene1_url: str = Input(description="Scene 1 video URL (optional)", default=""),
        scene2_url: str = Input(description="Scene 2 video URL (optional)", default=""),
        scene3_url: str = Input(description="Scene 3 video URL (optional)", default=""),

        # Optional audio
        final_dialogue: str = Input(description="Dialogue audio URL (optional)", default=""),
        final_music_url: str = Input(description="Music URL (optional)", default=""),

        # Meme text + branding
        meme_top_text: str = Input(description="Top meme text (optional)", default=""),
        meme_bottom_text: str = Input(description="Bottom meme text (optional)", default=""),
        meme_project_name: str = Input(description="Project name appended after luna.fun/memes/ (optional)", default=""),

        # Toggles
        include_branding: bool = Input(description="Draw luna.fun/memes/<project> at bottom-left", default=True),

        meme_id: str = Input(description="Used for filenames", default="clipforge"),
        max_duration_seconds: int = Input(description="Safety cap (0 = no cap)", default=0, ge=0, le=120),
    ) -> CogPath:
        # Validate font exists
        if not FONTS["english"].exists():
            raise RuntimeError(f"Missing font: {FONTS['english']}")

        req_id = f"{safe_slug(meme_id)}_{int(time.time())}"
        work = TEMP_DIR / req_id
        work.mkdir(parents=True, exist_ok=True)

        # Decide stitch vs single
        use_stitch = bool(scene1_url and scene2_url and scene3_url)

        def dl_if(url: str, name: str, default_ext: str) -> Path | None:
            if not url.strip():
                return None
            ext = url_ext(url) or default_ext
            p = work / f"{name}{ext}"
            download(url, p)
            return p

        dialogue_p = dl_if(final_dialogue, "dialogue", ".mp3")
        music_p = dl_if(final_music_url, "music", ".mp3")

        # Build base video
        if use_stitch:
            s1 = dl_if(scene1_url, "scene1", ".mp4")
            s2 = dl_if(scene2_url, "scene2", ".mp4")
            s3 = dl_if(scene3_url, "scene3", ".mp4")
            if not (s1 and s2 and s3):
                raise ValueError("scene1_url, scene2_url, scene3_url must all be provided for stitch mode")

            stitched = work / "stitched.mp4"
            concat_videos([s1, s2, s3], stitched)

            base = work / "base.mp4"
            # in stitch mode, keep music overlay if provided
            mix_audio(stitched, None, music_p, base, music_volume=0.3)

        else:
            src_url = final_stitch_video or final_stitched_video
            if not src_url.strip():
                raise ValueError("Provide final_stitch_video (or final_stitched_video), OR provide scene1_url/scene2_url/scene3_url.")
            video_p = dl_if(src_url, "video", ".mp4")
            if not video_p:
                raise RuntimeError("Failed to download video")

            base = work / "base.mp4"
            if max_duration_seconds and max_duration_seconds > 0:
                run(["ffmpeg","-y","-i", str(video_p), "-t", str(max_duration_seconds), "-c","copy", str(base)])
            else:
                run(["ffmpeg","-y","-i", str(video_p), "-c","copy", str(base)])

            if dialogue_p or music_p:
                mixed = work / "mixed.mp4"
                mix_audio(base, dialogue_p, music_p, mixed, music_volume=0.3)
                base = mixed

        # Render final
        out_dir = work / "outputs"
        out_dir.mkdir(parents=True, exist_ok=True)

        final_out = out_dir / f"{safe_slug(meme_id)}.mp4"
        add_branding_and_meme_text(
            video_in=base,
            out=final_out,
            project_name=meme_project_name or "",
            top_text=meme_top_text or "",
            bottom_text=meme_bottom_text or "",
            include_branding=include_branding,
        )

        # Metadata + ZIP
        meta = {
            "meme_id": meme_id,
            "mode": "stitch_3_scenes" if use_stitch else "single_video",
            "include_branding": include_branding,
            "project": meme_project_name,
            "inputs": {
                "final_stitch_video": (final_stitch_video or final_stitched_video) if not use_stitch else None,
                "scene1_url": scene1_url if use_stitch else None,
                "scene2_url": scene2_url if use_stitch else None,
                "scene3_url": scene3_url if use_stitch else None,
                "final_dialogue": final_dialogue or None,
                "final_music_url": final_music_url or None,
                "meme_top_text": meme_top_text or "",
                "meme_bottom_text": meme_bottom_text or "",
            },
            "outputs": {
                "video": final_out.name
            },
            "timestamp": int(time.time()),
        }

        meta_path = out_dir / "metadata.json"
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

        zip_path = work / f"{safe_slug(meme_id)}_pack.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(meta_path, arcname="metadata.json")
            z.write(final_out, arcname=final_out.name)

        return CogPath(str(zip_path))
