"""
ai/video_generator.py — 按需生成核心洞察 MP4 视频（PPT 风格 + 字幕 + 语音）
不存服务器，生成后直接流式返回，临时文件用完即删。
"""

import asyncio
import os
import re
import subprocess
import tempfile
import textwrap
from typing import Optional
from loguru import logger

AUDIO_DIR = os.getenv("AUDIO_DIR", "data/audio")

FONT_PATH = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
FONT_PATH_BOLD = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"

# Brand colors (hex → RGB)
_BG        = (15, 23, 42)     # #0f172a
_HEADER_BG = (30, 27, 75)     # #1e1b4b
_ACCENT    = (124, 58, 237)   # #7c3aed
_TEXT_HI   = (241, 245, 249)  # #f1f5f9
_TEXT_BODY = (226, 232, 240)  # #e2e8f0
_TEXT_SUB  = (148, 163, 184)  # #94a3b8
_TEXT_DIM  = (71, 85, 105)    # #475569
_FOOTER_BG = (10, 15, 30)     # #0a0f1e
_ACCENT2   = (79, 70, 229)    # #4f46e5

W, H = 1080, 1080


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


def _split_paragraphs(text: str) -> list[str]:
    """Split insight text into paragraphs."""
    # Remove markdown bullets, clean up
    text = re.sub(r'^[•\-\*]\s*', '', text, flags=re.MULTILINE)
    paras = [p.strip() for p in re.split(r'\n{2,}', text) if p.strip()]
    if not paras:
        paras = [p.strip() for p in text.split('\n') if p.strip()]
    return paras


def _make_srt(paragraphs: list[str], total_duration: float) -> str:
    """Build SRT subtitle file, one paragraph per cue."""
    total_chars = sum(len(p) for p in paragraphs) or 1

    def _fmt(t: float) -> str:
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = int(t % 60)
        ms = int((t % 1) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines = []
    cur = 0.0
    for i, para in enumerate(paragraphs):
        dur = total_duration * len(para) / total_chars
        end = cur + dur
        # subtitle text: max 2 lines × 28 chars
        wrapped = textwrap.wrap(para, width=28)[:2]
        lines.append(f"{i+1}\n{_fmt(cur)} --> {_fmt(end)}\n{chr(10).join(wrapped)}\n")
        cur = end
    return "\n".join(lines)


def _draw_cta_slide(lang: str, out_path: str) -> bool:
    """Final slide: brand CTA — unobtrusive but memorable."""
    try:
        from PIL import Image, ImageDraw, ImageFont

        img = Image.new("RGB", (W, H), _BG)
        draw = ImageDraw.Draw(img)

        def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
            path = FONT_PATH_BOLD if bold else FONT_PATH
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                return ImageFont.load_default()

        # Full gradient-ish header
        draw.rectangle([0, 0, W, 110], fill=_HEADER_BG)
        draw.rectangle([0, 108, W, 114], fill=_ACCENT)
        draw.text((44, 18), "📰 Daily X Digest", font=font(38, bold=True), fill=_TEXT_HI)
        draw.text((46, 68), "Your daily signal from X", font=font(26), fill=_TEXT_SUB)

        # Center block
        center_y = 260
        if lang == "zh":
            lines = [
                ("每天 8 点", 46, True, _TEXT_HI),
                ("准时收到今日 Web3 核心信号", 34, False, _TEXT_BODY),
                ("", 20, False, _TEXT_DIM),
                ("算法精选 · AI 分析 · 中英双语", 30, False, _TEXT_SUB),
                ("", 20, False, _TEXT_DIM),
                ("免费订阅，不错过任何重要动态", 30, False, _TEXT_BODY),
            ]
        else:
            lines = [
                ("Every morning at 8AM Beijing", 36, True, _TEXT_HI),
                ("The signals that matter in Web3", 32, False, _TEXT_BODY),
                ("", 20, False, _TEXT_DIM),
                ("Algorithm-curated · AI insights · ZH & EN", 26, False, _TEXT_SUB),
                ("", 20, False, _TEXT_DIM),
                ("Free to follow. Never miss a signal.", 30, False, _TEXT_BODY),
            ]

        for text_line, size, bold, color in lines:
            if text_line:
                draw.text((W // 2, center_y), text_line, font=font(size, bold=bold),
                          fill=color, anchor="mm")
            center_y += size + 18

        # URL pill
        url_y = H - 220
        draw.rounded_rectangle([W // 2 - 310, url_y, W // 2 + 310, url_y + 80],
                                radius=40, fill=_ACCENT)
        draw.text((W // 2, url_y + 40),
                  "monitor.dailyxdigest.uk",
                  font=font(32, bold=True), fill=_TEXT_HI, anchor="mm")

        # Footer
        draw.rectangle([0, H - 72, W, H], fill=_FOOTER_BG)
        draw.rectangle([0, H - 74, W, H - 72], fill=_ACCENT2)
        tagline = "关注我们 · 获取每日播报" if lang == "zh" else "Follow us · Get daily briefings"
        draw.text((44, H - 52), tagline, font=font(24), fill=_TEXT_DIM)

        img.save(out_path, format="PNG")
        return True
    except Exception as e:
        logger.error(f"video_gen: CTA slide error: {e}")
        return False


def _draw_slide(
    para: str,
    date: str,
    slide_idx: int,
    total_slides: int,
    lang: str,
    out_path: str,
) -> bool:
    try:
        from PIL import Image, ImageDraw, ImageFont

        img = Image.new("RGB", (W, H), _BG)
        draw = ImageDraw.Draw(img)

        def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
            path = FONT_PATH_BOLD if bold else FONT_PATH
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                return ImageFont.load_default()

        # ── Header ──────────────────────────────────────────────────
        draw.rectangle([0, 0, W, 110], fill=_HEADER_BG)
        draw.rectangle([0, 108, W, 114], fill=_ACCENT)

        draw.text((44, 18), "📰 Daily X Digest", font=font(38, bold=True), fill=_TEXT_HI)
        sub_label = f"{date}  ·  {'今日核心判断' if lang == 'zh' else 'Core Intelligence'}"
        draw.text((46, 68), sub_label, font=font(26), fill=_TEXT_SUB)

        # Slide indicator pill
        indicator = f"{slide_idx} / {total_slides}"
        draw.rounded_rectangle([W - 110, 38, W - 36, 76], radius=16, fill=_ACCENT)
        draw.text((W - 108, 42), indicator, font=font(24), fill=_TEXT_HI)

        # ── Body text ────────────────────────────────────────────────
        MARGIN = 54
        y = 140
        wrap_w = 22 if lang == "zh" else 40
        lines = textwrap.wrap(para, width=wrap_w)
        line_h = 52
        for line in lines[:13]:
            draw.text((MARGIN, y), line, font=font(34), fill=_TEXT_BODY)
            y += line_h

        # ── Footer ───────────────────────────────────────────────────
        draw.rectangle([0, H - 72, W, H], fill=_FOOTER_BG)
        draw.rectangle([0, H - 74, W, H - 72], fill=_ACCENT2)
        draw.text((44, H - 52), "monitor.dailyxdigest.uk", font=font(24), fill=_TEXT_DIM)

        img.save(out_path, format="PNG")
        return True
    except Exception as e:
        logger.error(f"video_gen: slide draw error: {e}")
        return False


async def generate_insight_video(
    date: str,
    lang: str,
    text: str,
    audio_fn: str,
    on_progress=None,          # async callable(pct: int, msg: str)
) -> Optional[bytes]:
    """
    Generate MP4 video bytes on demand.
    Returns raw MP4 bytes, or None on failure.
    on_progress — optional async callable(pct: int, msg: str)
    """
    async def _p(pct: int, msg: str):
        if on_progress:
            await on_progress(pct, msg)

    audio_path = os.path.join(AUDIO_DIR, audio_fn)
    if not os.path.exists(audio_path):
        logger.error(f"video_gen: audio not found: {audio_path}")
        return None

    await _p(5, "解析文字..." if lang == "zh" else "Parsing text...")
    paragraphs = _split_paragraphs(text)
    if not paragraphs:
        logger.error("video_gen: no paragraphs")
        return None

    await _p(10, "读取音频..." if lang == "zh" else "Reading audio...")
    duration = await asyncio.to_thread(_get_audio_duration, audio_path)
    logger.info(f"video_gen: {date}/{lang} dur={duration:.1f}s slides={len(paragraphs)}")

    total_chars = sum(len(p) for p in paragraphs) or 1

    with tempfile.TemporaryDirectory() as tmpdir:
        # 1. Generate content slide images
        slide_files = []
        for i, para in enumerate(paragraphs):
            png = os.path.join(tmpdir, f"slide_{i:03d}.png")
            ok = await asyncio.to_thread(_draw_slide, para, date, i + 1, len(paragraphs), lang, png)
            if ok:
                slide_files.append((png, para))
            pct = 15 + int(35 * (i + 1) / len(paragraphs))
            await _p(pct, f"生成幻灯片 {i+1}/{len(paragraphs)}..." if lang == "zh" else f"Slide {i+1}/{len(paragraphs)}...")

        if not slide_files:
            logger.error("video_gen: no slide images generated")
            return None

        # 1b. CTA slide at the end (3 seconds)
        cta_png = os.path.join(tmpdir, "slide_cta.png")
        await asyncio.to_thread(_draw_cta_slide, lang, cta_png)
        CTA_DURATION = 3.0
        await _p(55, "生成字幕..." if lang == "zh" else "Building subtitles...")

        # 2. Build ffmpeg concat file (content slides + CTA)
        concat_txt = os.path.join(tmpdir, "concat.txt")
        with open(concat_txt, "w") as f:
            for png, para in slide_files:
                slide_dur = duration * len(para) / total_chars
                f.write(f"file '{png}'\n")
                f.write(f"duration {slide_dur:.4f}\n")
            # CTA slide
            f.write(f"file '{cta_png}'\n")
            f.write(f"duration {CTA_DURATION:.4f}\n")
            # trailing entry (required by ffmpeg concat)
            f.write(f"file '{cta_png}'\n")

        # 3. Build SRT subtitle file (only for main content duration)
        srt_path = os.path.join(tmpdir, "subs.srt")
        srt_content = _make_srt([p for _, p in slide_files], duration)
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write(srt_content)

        # 4. ffmpeg: concat images + audio + burned-in subtitles → MP4
        await _p(65, "合成视频中，请稍候..." if lang == "zh" else "Encoding video...")
        out_mp4 = os.path.join(tmpdir, "output.mp4")

        # subtitle drawtext filter via libass (uses the Noto font)
        subtitle_filter = (
            f"subtitles='{srt_path}'"
            f":force_style='FontName=Noto Sans CJK SC,FontSize=28,"
            f"PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
            f"Outline=2,Shadow=1,Alignment=2,MarginV=16'"
        )

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", concat_txt,
            "-i", audio_path,
            "-vf", subtitle_filter,
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-c:a", "aac", "-b:a", "128k",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            "-shortest",
            out_mp4,
        ]

        logger.info(f"video_gen: running ffmpeg ...")
        proc = await asyncio.to_thread(
            subprocess.run, cmd, capture_output=True, text=True, timeout=300
        )
        if proc.returncode != 0:
            logger.error(f"video_gen: ffmpeg failed:\n{proc.stderr[-800:]}")
            return None

        await _p(95, "打包完成..." if lang == "zh" else "Finalizing...")

        with open(out_mp4, "rb") as f:
            data = f.read()

        logger.info(f"video_gen: done {len(data)//1024}KB for {date}/{lang}")
        return data
