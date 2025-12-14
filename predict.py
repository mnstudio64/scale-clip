import time
import subprocess
from pathlib import Path
import requests
from cog import BasePredictor, Input, Path as CogPath

# ------------------ Paths ------------------

FONT_DIR = Path("public/fonts")
TEMP_DIR = Path("/tmp/clipforge")
TEMP_DIR.mkdir(parents=True, exist_ok=True)

BRAND_PREFIX = "luna.fun/memes/"

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

def run(cmd):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode("utf-8", errors="ignore"))

def download(url: str, out: Path):
    r = requests.get(url, stream=True, allow_redirects=True, timeout=40)
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
    return (
        text.replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace(":", "\\:")
            .replace("[", "\\[")
            .replace("]", "\\]")
            .replace(",", "\\,")
            .replace("\n", "\\n")
    )

# ------------------ Predictor ------------------

class Predictor(BasePredictor):
    def predict(
        self,
        video: str = Input(
            description="Video File / URL (MP4, MOV, WebM)",
            default=""
        ),
        meme_top_text: str = Input(description="Top meme text", default=""),
        meme_bottom_text: str = Input(description="Bottom meme text", default=""),
        meme_project_name: str = Input(description="Brand name after luna.fun/memes/", default=""),
        include_branding: bool = Input(description="Include branding watermark", default=True),
    ) -> CogPath:

        if not video.strip():
            raise ValueError("Video File / URL is required")

        job_id = str(int(time.time()))
        work = TEMP_DIR / job_id
        work.mkdir(parents=True, exist_ok=True)

        input_video = work / "input.mp4"
        output_video = work / "output.mp4"

        download(video, input_video)

        combined_text = f"{meme_top_text} {meme_bottom_text}"
        font = FONTS.get(detect_language(combined_text), FONTS["english"])
        font = str(font).replace(":", "\\:")

        filters = []
        label = "0:v"

        if meme_top_text:
            filters.append(
                f"[{label}]drawtext=fontfile='{font}':"
                f"text='{escape(meme_top_text)}':"
                "fontcolor=white:fontsize=48:borderw=3:bordercolor=black:"
                "x=(w-text_w)/2:y=40[v1]"
            )
            label = "v1"

        if meme_bottom_text:
            filters.append(
                f"[{label}]drawtext=fontfile='{font}':"
                f"text='{escape(meme_bottom_text)}':"
                "fontcolor=white:fontsize=48:borderw=3:bordercolor=black:"
                "x=(w-text_w)/2:y=h-120[v2]"
            )
            label = "v2"

        if include_branding:
            brand_text = BRAND_PREFIX + meme_project_name
            filters.append(
                f"[{label}]drawtext=fontfile='{FONTS['english']}':"
                f"text='{escape(brand_text)}':"
                "fontcolor=white:fontsize=18:borderw=1:bordercolor=black:"
                "x=20:y=h-40[outv]"
            )
        else:
            filters.append(f"[{label}]null[outv]")

        run([
            "ffmpeg", "-y",
            "-i", str(input_video),
            "-filter_complex", ";".join(filters),
            "-map", "[outv]",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "18",
            "-c:a", "copy",
            str(output_video)
        ])

        return CogPath(str(output_video))
