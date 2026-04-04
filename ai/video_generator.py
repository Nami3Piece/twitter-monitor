"""
ai/video_generator.py — 核心洞察海报 + 音频 → MP4
分章节布局，无 Good Morning 问候语。
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


def _get_ffmpeg() -> str:
    """Return bundled ffmpeg (imageio-ffmpeg) or fall back to system."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"
FONT_PATH_BOLD = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"

# Palette
_BG_WARM    = (252, 250, 248)
_PURPLE     = (109,  40, 217)
_PURPLE_LT  = (237, 233, 254)
_PURPLE_DK  = ( 76,  29, 149)
_GOLD       = (180,  90,   5)
_GOLD_LT    = (254, 243, 199)
_GOLD_BD    = (217, 119,   6)
_BODY       = ( 30,  41,  59)
_MUTED      = (100, 116, 139)
_BORDER     = (226, 232, 240)
_RULE       = (203, 213, 225)
_SECTION_BG = (245, 243, 255)

W = 1080


def _get_font(size: int, bold: bool = False):
    from PIL import ImageFont
    path = FONT_PATH_BOLD if bold else FONT_PATH
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def _tlen(draw, text: str, font) -> float:
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
    text = text.replace("\n", " ").strip()
    is_cjk = sum(1 for c in text if unicodedata.east_asian_width(c) in ('W', 'F')) > len(text) * 0.3
    if is_cjk:
        lines, current = [], ""
        for ch in text:
            test = current + ch
            if _tlen(draw, test, font) > max_width and current:
                lines.append(current); current = ch
            else:
                current = test
        if current:
            lines.append(current)
    else:
        words = text.split()
        lines, current = [], ""
        for word in words:
            test = (current + " " + word).strip()
            if _tlen(draw, test, font) > max_width and current:
                lines.append(current); current = word
            else:
                current = test
        if current:
            lines.append(current)
    return lines


def _draw_poster(date: str, lang: str, text: str) -> Optional[bytes]:
    """Draw a sectioned Core Insight poster. Returns PNG bytes."""
    try:
        from PIL import Image, ImageDraw

        MARGIN      = 72
        BAR_W       = 5
        INDENT      = 20
        BODY_W      = W - MARGIN * 2 - BAR_W - INDENT
        SECTION_PAD = 18
        PARA_GAP    = 40

        # ── 段落分割（跳过首行 header）────────────────────────────────────
        raw_paras = [p.strip() for p in text.split('\n\n') if p.strip()]
        paragraphs = []
        for p in raw_paras:
            first = p.split('\n')[0].strip()
            if first.startswith('📰') or (len(first) < 70 and '\u00b7' in first and len(p.split('\n')) == 1):
                continue
            paragraphs.append(p.replace('\n', ' ').strip())
        if not paragraphs:
            paragraphs = [text.strip()]

        dummy_img  = Image.new("RGB", (W, 100))
        dummy_draw = ImageDraw.Draw(dummy_img)

        # ── 自动字号（段落总高度 ≤ 1050px）──────────────────────────────
        f_body, para_data, line_h = None, [], 0
        for fs in (34, 30, 27, 24, 21, 18):
            fb   = _get_font(fs)
            bb   = dummy_draw.textbbox((0, 0), "测Ag", font=fb)
            lh   = bb[3] - bb[1] + 10
            pd   = [_wrap_text(p, fb, BODY_W, dummy_draw) for p in paragraphs]
            tot  = sum(len(ls) * lh for ls in pd) + (len(pd) - 1) * PARA_GAP
            tot += len(pd) * SECTION_PAD * 2
            if tot <= 1050:
                f_body, para_data, line_h = fb, pd, lh
                break
        if f_body is None:
            f_body    = _get_font(18)
            para_data = [_wrap_text(p, f_body, BODY_W, dummy_draw) for p in paragraphs]
            bb        = dummy_draw.textbbox((0, 0), "测Ag", font=f_body)
            line_h    = bb[3] - bb[1] + 10

        body_h = (sum(len(ls) * line_h for ls in para_data)
                  + (len(para_data) - 1) * PARA_GAP
                  + len(para_data) * SECTION_PAD * 2)

        # ── 画布尺寸 ──────────────────────────────────────────────────────
        TOP_PAD    = 64
        TITLE_H    = 90
        DATE_H     = 50
        RULE1_H    = 36
        BODY_PAD_T = 36
        BODY_PAD_B = 52
        CTA_H      = 80
        RULE2_H    = 30
        TAG_H      = 56
        BOT_PAD    = 52

        canvas_h = (TOP_PAD + TITLE_H + DATE_H + RULE1_H
                    + BODY_PAD_T + body_h + BODY_PAD_B
                    + CTA_H + RULE2_H + TAG_H + BOT_PAD)
        canvas_h += canvas_h % 2

        img  = Image.new("RGB", (W, canvas_h), _BG_WARM)
        draw = ImageDraw.Draw(img)

        f_title = _get_font(62, bold=True)
        f_brand = _get_font(22)
        f_date  = _get_font(22)
        f_cta   = _get_font(21)
        f_tag   = _get_font(19)

        # ── 顶部装饰条（三段渐变）────────────────────────────────────────
        draw.rectangle([0, 0, W // 3,     8], fill=_PURPLE_DK)
        draw.rectangle([W // 3, 0, W * 2 // 3, 8], fill=_PURPLE)
        draw.rectangle([W * 2 // 3, 0, W, 8], fill=(168, 85, 247))

        y = TOP_PAD

        # ── 主标题 ────────────────────────────────────────────────────────
        title = "核心洞察" if lang == "zh" else "Core Insight"
        draw.text((MARGIN, y), title, font=f_title, fill=_PURPLE)

        # Brand badge（右上）
        badge = "Daily X Digest"
        bw = _tlen(draw, badge, f_brand)
        bx = W - MARGIN - int(bw) - 24
        by = y + 16
        draw.rounded_rectangle([bx - 14, by, bx + int(bw) + 14, by + 34], radius=17, fill=_PURPLE_LT)
        draw.text((bx, by + 7), badge, font=f_brand, fill=_PURPLE)

        y += TITLE_H

        # ── 日期 ─────────────────────────────────────────────────────────
        draw.text((MARGIN, y), date, font=f_date, fill=_MUTED)
        y += DATE_H

        # ── 分隔线 ───────────────────────────────────────────────────────
        draw.rectangle([MARGIN, y, W - MARGIN, y + 2], fill=_RULE)
        y += RULE1_H + BODY_PAD_T

        # ── 章节段落 ──────────────────────────────────────────────────────
        for idx, lines in enumerate(para_data):
            sec_h = len(lines) * line_h + SECTION_PAD * 2

            # 淡紫背景块
            draw.rounded_rectangle([MARGIN, y, W - MARGIN, y + sec_h], radius=10, fill=_SECTION_BG)
            # 左侧紫竖条
            draw.rounded_rectangle([MARGIN, y, MARGIN + BAR_W, y + sec_h], radius=10, fill=_PURPLE)

            # 正文
            ty = y + SECTION_PAD
            for line in lines:
                draw.text((MARGIN + BAR_W + INDENT, ty), line, font=f_body, fill=_BODY)
                ty += line_h

            y += sec_h
            if idx < len(para_data) - 1:
                y += PARA_GAP

        # ── CTA 胶囊 ─────────────────────────────────────────────────────
        y += BODY_PAD_B
        draw.rounded_rectangle([MARGIN, y, W - MARGIN, y + 64], radius=32, fill=_GOLD_LT)
        draw.rounded_rectangle([MARGIN, y, W - MARGIN, y + 64], radius=32, outline=_GOLD_BD, width=1)
        cta = ("monitor.dailyxdigest.uk  ·  免费订阅  ·  早八点准时播报" if lang == "zh"
               else "monitor.dailyxdigest.uk  ·  Free  ·  Daily UTC 0:00")
        cta_w = _tlen(draw, cta, f_cta)
        draw.text((W // 2 - int(cta_w) // 2, y + 18), cta, font=f_cta, fill=_GOLD)

        # ── 底部分隔 + 免责声明 ───────────────────────────────────────────
        y += CTA_H + RULE2_H
        draw.rectangle([MARGIN, y, W - MARGIN, y + 1], fill=_BORDER)
        y += 20
        tagline = ("以上内容仅供参考，不构成任何投资建议。投资有风险，决策需谨慎。" if lang == "zh"
                   else "For reference only. Not financial advice. Invest at your own risk.")
        tl_w = _tlen(draw, tagline, f_tag)
        draw.text((W // 2 - int(tl_w) // 2, y), tagline, font=f_tag, fill=_MUTED)

        # ── 底部装饰条 ────────────────────────────────────────────────────
        draw.rectangle([0, canvas_h - 8, W * 2 // 3, canvas_h], fill=_PURPLE)
        draw.rectangle([W * 2 // 3, canvas_h - 8, W, canvas_h], fill=(168, 85, 247))

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        buf.seek(0)
        return buf.read()

    except Exception as e:
        logger.error(f"poster: draw error: {e}")
        return None


async def generate_video_from_pdf(
    pdf_bytes: bytes,
    audio_path: Optional[str],
    on_progress=None,
) -> Optional[bytes]:
    """
    Render each PDF page as a 1920×1080 image, then combine with audio into MP4.
    Pages are distributed evenly over the audio duration (min 3s per page).
    Returns raw MP4 bytes, or None on failure.
    """
    async def _p(pct: int, msg: str):
        if on_progress:
            await on_progress(pct, msg)

    await _p(5, "解析PDF...")
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.error("pdf-video: PyMuPDF not installed")
        return None

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        n_pages = len(doc)
        if n_pages == 0:
            logger.error("pdf-video: empty PDF")
            return None
    except Exception as e:
        logger.error(f"pdf-video: failed to open PDF: {e}")
        return None

    # Determine per-page duration
    audio_duration = 0.0
    if audio_path and os.path.exists(audio_path):
        audio_duration = _get_audio_duration(audio_path)
    if audio_duration <= 0:
        audio_duration = n_pages * 8.0  # fallback: 8s per page

    per_page = max(3.0, audio_duration / n_pages)

    await _p(20, f"渲染 {n_pages} 页PDF...")

    with tempfile.TemporaryDirectory() as tmpdir:
        frame_paths = []
        for i, page in enumerate(doc):
            # Render at 2x scale → ~1920px wide for 16:9 PDF pages
            mat = fitz.Matrix(2.0, 2.0)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            frame_path = os.path.join(tmpdir, f"frame_{i:04d}.png")
            pix.save(frame_path)
            frame_paths.append(frame_path)
            await _p(20 + int(40 * (i + 1) / n_pages), f"渲染第 {i+1}/{n_pages} 页...")

        doc.close()

        await _p(65, "合成视频...")
        out_mp4 = os.path.join(tmpdir, "output.mp4")
        ffmpeg = _get_ffmpeg()

        # Build concat demuxer file: each frame shown for per_page seconds
        concat_txt = os.path.join(tmpdir, "concat.txt")
        with open(concat_txt, "w") as f:
            for fp in frame_paths:
                f.write(f"file '{fp}'\n")
                f.write(f"duration {per_page:.3f}\n")
            # ffmpeg concat needs the last file listed again without duration
            f.write(f"file '{frame_paths[-1]}'\n")

        if audio_path and os.path.exists(audio_path):
            cmd = [
                ffmpeg, "-y", "-loglevel", "error",
                "-f", "concat", "-safe", "0", "-i", concat_txt,
                "-i", audio_path,
                "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                "-shortest",
                out_mp4,
            ]
        else:
            cmd = [
                ffmpeg, "-y", "-loglevel", "error",
                "-f", "concat", "-safe", "0", "-i", concat_txt,
                "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                out_mp4,
            ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=300)
        except asyncio.TimeoutError:
            proc.kill()
            logger.error("pdf-video: ffmpeg timed out after 300s")
            return None
        if proc.returncode != 0:
            logger.error(f"pdf-video: ffmpeg failed:\n{stderr_bytes.decode()[-600:]}")
            return None

        await _p(95, "打包完成...")
        with open(out_mp4, "rb") as f:
            data = f.read()

    logger.info(f"pdf-video: done {len(data)//1024}KB, {n_pages} pages, {audio_duration:.1f}s audio")
    return data


def _get_audio_duration(audio_path: str) -> float:
    """Get audio duration using ffmpeg -i (imageio_ffmpeg bundles ffmpeg but not ffprobe)."""
    import re as _re
    ffmpeg = _get_ffmpeg()
    try:
        result = subprocess.run(
            [ffmpeg, "-i", audio_path],
            capture_output=True, text=True, timeout=10,
        )
        # ffmpeg prints duration to stderr: "Duration: HH:MM:SS.xx"
        m = _re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", result.stderr)
        if m:
            h, mn, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
            return h * 3600 + mn * 60 + s
    except Exception:
        pass
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

        ffmpeg = _get_ffmpeg()
        if audio_path:
            cmd = [
                ffmpeg, "-y", "-loglevel", "error",
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
                ffmpeg, "-y", "-loglevel", "error",
                "-loop", "1", "-t", "60", "-i", poster_path,
                "-c:v", "libx264", "-tune", "stillimage",
                "-preset", "ultrafast", "-crf", "28",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                out_mp4,
            ]

        # Use asyncio subprocess to avoid pipe-buffer deadlock from capture_output=True
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=300)
        except asyncio.TimeoutError:
            proc.kill()
            logger.error("poster: ffmpeg timed out after 300s")
            return None
        if proc.returncode != 0:
            logger.error(f"poster: ffmpeg failed:\n{stderr_bytes.decode()[-600:]}")
            return None

        await _p(95, "打包完成..." if lang == "zh" else "Finalizing...")
        with open(out_mp4, "rb") as f:
            data = f.read()

    logger.info(f"poster: done {len(data)//1024}KB for {date}/{lang}")
    return data
