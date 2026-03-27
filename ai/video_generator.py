"""
ai/video_generator.py — 静态海报 + 音频 → MP4
一张精美早安海报图，配上语音，合成视频。无字幕。
"""

import asyncio
import io
import os
import subprocess
import tempfile
from typing import Optional
from loguru import logger

AUDIO_DIR      = os.getenv("AUDIO_DIR", "data/audio")
FONT_PATH      = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
FONT_PATH_BOLD = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"

# Light palette
_BG_WARM    = (252, 250, 248)
_PURPLE     = (109, 40,  217)
_PURPLE_LT  = (237, 233, 254)
_GOLD       = (217, 119, 6)
_GOLD_LT    = (254, 243, 199)
_INK        = (15,  23,  42)
_BODY       = (30,  41,  59)
_MUTED      = (100, 116, 139)
_BORDER     = (226, 232, 240)
_RULE       = (203, 213, 225)

W = 1080


def _get_font(size: int, bold: bool = False):
    from PIL import ImageFont
    path = FONT_PATH_BOLD if bold else FONT_PATH
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def _tlen(draw, text: str, font) -> float:
    """Safe textlength: strip newlines, fall back to textbbox on error."""
    text = text.replace("\n", " ").strip()
    try:
        return draw.textlength(text, font=font)
    except Exception:
        try:
            bb = draw.textbbox((0, 0), text, font=font)
            return float(bb[2] - bb[0])
        except Exception:
            return float(len(text) * (font.size if hasattr(font, "size") else 20))


def _wrap_text(text: str, font, max_width: int, draw) -> list[str]:
    import unicodedata
    # Normalize: replace newlines with space so single-line measuring works
    text = text.replace("\n", " ").strip()
    is_cjk = sum(1 for c in text if unicodedata.east_asian_width(c) in ('W', 'F')) > len(text) * 0.3
    if is_cjk:
        lines, current = [], ""
        for ch in text:
            test = current + ch
            if _tlen(draw, test, font) > max_width and current:
                lines.append(current)
                current = ch
            else:
                current = test
        if current:
            lines.append(current)
        return lines
    else:
        words = text.split()
        lines, current = [], ""
        for word in words:
            test = (current + " " + word).strip()
            if _tlen(draw, test, font) > max_width and current:
                lines.append(current)
                current = word
            else:
                current = test
        if current:
            lines.append(current)
        return lines


def _draw_poster(date: str, lang: str, text: str) -> Optional[bytes]:
    """Draw a single Good Morning insight poster. Returns PNG bytes."""
    try:
        from PIL import Image, ImageDraw

        MARGIN  = 72
        BODY_W  = W - MARGIN * 2

        # Auto-size body font to fit in ~900px
        dummy_img  = Image.new("RGB", (W, 100))
        dummy_draw = ImageDraw.Draw(dummy_img)
        MAX_BODY_H = 900

        chosen_fs = 34
        for fs in (38, 34, 30, 27, 24, 21):
            fb = _get_font(fs)
            lines = _wrap_text(text, fb, BODY_W, dummy_draw)
            bb = dummy_draw.textbbox((0, 0), "测Ag", font=fb)
            lh = bb[3] - bb[1] + 12
            if len(lines) * lh <= MAX_BODY_H:
                chosen_fs = fs
                break
        else:
            chosen_fs = 21

        f_body     = _get_font(chosen_fs)
        body_lines = _wrap_text(text, f_body, BODY_W, dummy_draw)
        bb         = dummy_draw.textbbox((0, 0), "测Ag", font=f_body)
        line_h     = bb[3] - bb[1] + 12
        body_h     = len(body_lines) * line_h

        TOP_PAD     = 64
        GREET_H     = 120
        DATE_H      = 52
        RULE1_H     = 32
        BODY_PAD_T  = 40
        BODY_PAD_B  = 48
        CTA_H       = 80
        RULE2_H     = 28
        TAG_H       = 60
        BOT_PAD     = 48

        canvas_h = (TOP_PAD + GREET_H + DATE_H + RULE1_H
                    + BODY_PAD_T + body_h + BODY_PAD_B
                    + CTA_H + RULE2_H + TAG_H + BOT_PAD)
        canvas_h += canvas_h % 2  # libx264 requires even dimensions

        img  = Image.new("RGB", (W, canvas_h), _BG_WARM)
        draw = ImageDraw.Draw(img)

        f_greeting = _get_font(52, bold=True)
        f_brand    = _get_font(24)
        f_date     = _get_font(24)
        f_cta_sub  = _get_font(22)
        f_tag      = _get_font(20)

        # Top accent bar
        draw.rectangle([0, 0, W, 6], fill=_PURPLE)

        # Greeting
        y = TOP_PAD
        greeting = "早安 · 今日核心洞察" if lang == "zh" else "Good Morning · Core Insight"
        draw.text((MARGIN, y), greeting, font=f_greeting, fill=_INK)

        # Brand badge right-aligned
        badge = "Daily X Digest"
        bw = _tlen(draw, badge, f_brand)
        bx = W - MARGIN - int(bw) - 24
        by = y + 10
        draw.rounded_rectangle([bx - 12, by, bx + int(bw) + 12, by + 36], radius=18, fill=_PURPLE_LT)
        draw.text((bx, by + 8), badge, font=f_brand, fill=_PURPLE)

        # Date
        y += GREET_H
        draw.text((MARGIN, y), date, font=f_date, fill=_MUTED)

        # Rule 1
        y += DATE_H
        draw.rectangle([MARGIN, y, W - MARGIN, y + 2], fill=_RULE)

        # Body
        y += RULE1_H + BODY_PAD_T
        for line in body_lines:
            draw.text((MARGIN, y), line, font=f_body, fill=_BODY)
            y += line_h

        # CTA pill
        y += BODY_PAD_B
        draw.rounded_rectangle([MARGIN, y, W - MARGIN, y + 64], radius=32, fill=_GOLD_LT)
        draw.rounded_rectangle([MARGIN, y, W - MARGIN, y + 64], radius=32, outline=_GOLD, width=1)
        cta = ("🌐  monitor.dailyxdigest.uk  ·  免费订阅，每日 8 点" if lang == "zh"
               else "🌐  monitor.dailyxdigest.uk  ·  Free · Daily at 8AM")
        cta_w = _tlen(draw, cta, f_cta_sub)
        draw.text((W // 2 - int(cta_w) // 2, y + 18), cta, font=f_cta_sub, fill=_GOLD)

        # Rule 2
        y += CTA_H + RULE2_H
        draw.rectangle([MARGIN, y, W - MARGIN, y + 1], fill=_BORDER)

        # Footer tagline
        y += 20
        tagline = ("Web3 核心信号 · AI 精选分析 · 中英双语" if lang == "zh"
                   else "Web3 signals · AI-curated · ZH & EN")
        tl_w = _tlen(draw, tagline, f_tag)
        draw.text((W // 2 - int(tl_w) // 2, y), tagline, font=f_tag, fill=_MUTED)

        # Bottom accent bar
        draw.rectangle([0, canvas_h - 6, W, canvas_h], fill=_PURPLE)

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        buf.seek(0)
        return buf.read()

    except Exception as e:
        logger.error(f"poster: draw error: {e}")
        return None


def _get_audio_duration(audio_path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
        capture_output=True, text=True, timeout=10,
    )
    try:
        return float(result.stdout.strip())
    except Exception:
        return 60.0


async def generate_insight_video(
    date: str,
    lang: str,
    text: str,
    audio_fn: str = "",
    on_progress=None,
) -> Optional[bytes]:
    """
    Generate MP4: static poster image + audio. No subtitles.
    Returns raw MP4 bytes, or None on failure.
    """
    async def _p(pct: int, msg: str):
        if on_progress:
            await on_progress(pct, msg)

    await _p(5, "解析内容..." if lang == "zh" else "Parsing...")
    if not text.strip():
        logger.error("poster: empty text")
        return None

    audio_path = os.path.join(AUDIO_DIR, audio_fn) if audio_fn else None
    if audio_path and not os.path.exists(audio_path):
        logger.error(f"poster: audio not found: {audio_path}")
        audio_path = None

    await _p(30, "绘制海报..." if lang == "zh" else "Drawing poster...")
    png_bytes = await asyncio.to_thread(_draw_poster, date, lang, text)
    if not png_bytes:
        return None

    await _p(60, "合成视频..." if lang == "zh" else "Encoding video...")

    with tempfile.TemporaryDirectory() as tmpdir:
        poster_path = os.path.join(tmpdir, "poster.png")
        out_mp4     = os.path.join(tmpdir, "output.mp4")

        with open(poster_path, "wb") as f:
            f.write(png_bytes)

        if audio_path:
            cmd = [
                "ffmpeg", "-y",
                "-loop", "1", "-i", poster_path,
                "-i", audio_path,
                "-c:v", "libx264", "-tune", "stillimage",
                "-preset", "ultrafast", "-crf", "28",
                "-c:a", "aac", "-b:a", "96k",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                "-shortest",
                out_mp4,
            ]
        else:
            # No audio: 60s silent video
            cmd = [
                "ffmpeg", "-y",
                "-loop", "1", "-t", "60", "-i", poster_path,
                "-c:v", "libx264", "-tune", "stillimage",
                "-preset", "ultrafast", "-crf", "28",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                out_mp4,
            ]

        proc = await asyncio.to_thread(
            subprocess.run, cmd, capture_output=True, text=True, timeout=120
        )
        if proc.returncode != 0:
            logger.error(f"poster: ffmpeg failed:\n{proc.stderr[-600:]}")
            return None

        await _p(95, "打包完成..." if lang == "zh" else "Finalizing...")
        with open(out_mp4, "rb") as f:
            data = f.read()

    logger.info(f"poster: done {len(data)//1024}KB for {date}/{lang}")
    return data
