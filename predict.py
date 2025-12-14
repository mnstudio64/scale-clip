import time
import subprocess
from pathlib import Path
import requests
from cog import BasePredictor, Input, Path as CogPath


TEMP_DIR = Path("/tmp/clipforge")
TEMP_DIR.mkdir(parents=True, exist_ok=True)

FONT_DIR = Path("public/fonts")

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


def run(cmd):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode("utf-8", errors="ignore"))


def download(url: str, out: Path):
    r = requests.get(url, stream=True, allow_redirects=True, timeout=60)
    r.raise_for_status()
    with out.open("wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 1024):
            if chunk:
                f.write(chunk)


def detect_language(text: str):
    for ch in text:
        code = ord(ch)
        if 0x4E00 <= code <= 0x9FFF:
            return "chinese"
        if 0x3040 <= code <= 0x30FF:
            return "japanese"
        if 0xAC00 <= code <= 0xD7AF:
            return "korean"
        if 0x0600 <= code <= 0x06FF:
            return "arabic"
        if 0x0E00 <= code <= 0x0E7F:
            return "thai"
        if 0x0B80 <= code <= 0x0BFF:
            return "tamil"
        if 0x0980 <= code <= 0x09FF:
            return "bengali"
    return "english"


def escape(text: str):
    if not text:
        return ""
    return (
        text.replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace(":", "\\:")
            .replace("[", "\\[")
            .replace("]", "\\]")
            .replace(",", "\\,")
            .replace("\n", "\\n")
    )


class Predictor(BasePredictor):
    def predict(
        self,
        video_url: str = Input(
            description="Video URL",
            default=""
        ),
        top_text: str = Input(
            description="Top text (optional)",
            default=""
        ),
        bottom_text: str = Input(
            description="Bottom text (optional)",
            default=""
        ),
        brand_name: str = Input(
            description="Brand name (optional)",
            default=""
        ),
        include_branding: bool = Input(
            description="Draw brand name at bottom-left",
            default=True
        ),
        music_url: str = Input(
            description="Music URL (optional)",
            default=""
        ),
        dialogue_url: str = Input(
            description="Dialogue URL (optional)",
            default=""
        ),
    ) -> CogPath:

        if not video_url.strip():
            raise ValueError("Video URL is required")

        job_id = str(int(time.time()))
        work = TEMP_DIR / job_id
        work.mkdir(parents=True, exist_ok=True)

        in_video = work / "input.mp4"
        out_video = work / "output.mp4"

        # optional audio
        music_path = work / "music.mp3"
        dialogue_path = work / "dialogue.mp3"
        has_music = bool(music_url.strip())
        has_dialogue = bool(dialogue_url.strip())

        download(video_url, in_video)
        if has_music:
            download(music_url, music_path)
        if has_dialogue:
            download(dialogue_url, dialogue_path)

        combined_text = f"{top_text} {bottom_text} {brand_name}".strip()
        lang = detect_language(combined_text)
        font_path = str(FONTS.get(lang, FONTS["english"])).replace(":", "\\:")

        # ---------- video filters (text overlay) ----------
        filters = []
        label = "0:v"

        if top_text.strip():
            filters.append(
                f"[{label}]drawtext=fontfile='{font_path}':"
                f"text='{escape(top_text)}':"
                "fontcolor=white:fontsize=48:borderw=3:bordercolor=black:"
                "x=(w-text_w)/2:y=40[v1]"
            )
            label = "v1"

        if bottom_text.strip():
            filters.append(
                f"[{label}]drawtext=fontfile='{font_path}':"
                f"text='{escape(bottom_text)}':"
                "fontcolor=white:fontsize=48:borderw=3:bordercolor=black:"
                "x=(w-text_w)/2:y=h-120[v2]"
            )
            label = "v2"

        if include_branding and brand_name.strip():
            filters.append(
                f"[{label}]drawtext=fontfile='{font_path}':"
                f"text='{escape(brand_name)}':"
                "fontcolor=white:fontsize=18:borderw=1:bordercolor=black:"
                "x=20:y=h-40[outv]"
            )
        else:
            filters.append(f"[{label}]null[outv]")

        filter_complex = ";".join(filters)

        # ---------- build ffmpeg command ----------
        cmd = ["ffmpeg", "-y", "-i", str(in_video)]

        # add optional audio inputs
        if has_dialogue:
            cmd += ["-i", str(dialogue_path)]
        if has_music:
            cmd += ["-i", str(music_path)]

        cmd += ["-filter_complex", filter_complex]

        # audio mixing
        if has_dialogue or has_music:
            # Build audio mix filter: include original audio + dialogue + music if present
            # 0:a? is original audio
            audio_filters = []
            audio_inputs = []

            # original audio (if exists)
            audio_inputs.append("[0:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[a0]")

            idx = 1
            if has_dialogue:
                audio_inputs.append(f"[{idx}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[a1]")
                idx += 1
            if has_music:
                audio_inputs.append(f"[{idx}:a]volume=0.3,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[a2]")

            mix_labels = ["[a0]"]
            if has_dialogue:
                mix_labels.append("[a1]")
            if has_music:
                mix_labels.append("[a2]")

            audio_filters = audio_inputs + [f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=longest:dropout_transition=0[outa]"]
            cmd += ["-filter_complex", filter_complex + ";" + ";".join(audio_filters)]

            cmd += ["-map", "[outv]", "-map", "[outa]", "-c:a", "aac", "-b:a", "192k"]
        else:
            cmd += ["-map", "[outv]", "-map", "0:a?", "-c:a", "copy"]

        cmd += ["-c:v", "libx264", "-preset", "fast", "-crf", "18", str(out_video)]

        run(cmd)

        return CogPath(str(out_video))
