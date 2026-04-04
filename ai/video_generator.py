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


# ── Tweet card + subtitle helpers ─────────────────────────────────────────────

_FW = 1920
_FH = 1080
_PDF_W = 1150   # left panel width
_TWEET_W = 720  # right panel width (gap = 50px)
_SUB_H = 110    # subtitle bar height at bottom
_GAP = 50       # gap between panels
_BG = (10, 14, 26)
_CARD_BG = (20, 28, 46)
_CARD_BORDER = (45, 65, 100)
_TWEET_TEXT = (220, 230, 245)
_HANDLE_COLOR = (100, 140, 200)
_SUB_BG = (0, 0, 0, 185)       # RGBA semi-transparent
_SUB_TEXT = (255, 255, 255)


def _render_tweet_card(tweet: dict, card_w: int) -> "Optional[object]":
    """Render a tweet as a styled PIL Image card. Returns Image or None."""
    try:
        from PIL import Image, ImageDraw
        text = (tweet.get("text") or "").strip()
        author = tweet.get("author_name") or tweet.get("username") or "Unknown"
        handle = tweet.get("username") or ""
        likes = tweet.get("likes") or tweet.get("like_count") or 0
        rts = tweet.get("retweets") or tweet.get("retweet_count") or 0

        # Fonts
        f_author = _get_font(22, bold=True)
        f_handle = _get_font(18)
        f_text   = _get_font(20)
        f_stats  = _get_font(17)

        PADDING = 18
        AVATAR  = 44
        text_w  = card_w - PADDING * 2 - AVATAR - 12

        # Dummy draw for measurement
        dummy = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        wrapped = _wrap_text(text, f_text, text_w, dummy)
        # Cap at 5 lines to keep card compact
        wrapped = wrapped[:5]
        if len(wrapped) == 5 and len(text) > sum(len(l) for l in wrapped):
            wrapped[-1] = wrapped[-1][:-1] + "…"

        bb = dummy.textbbox((0, 0), "Ag", font=f_text)
        lh = bb[3] - bb[1] + 5
        content_h = len(wrapped) * lh + 8
        card_h = PADDING + AVATAR + 8 + content_h + 28 + PADDING

        img  = Image.new("RGB", (card_w, card_h), _CARD_BG)
        draw = ImageDraw.Draw(img)

        # Border
        draw.rounded_rectangle([0, 0, card_w - 1, card_h - 1], radius=10,
                                outline=_CARD_BORDER, width=1)

        # Avatar circle
        ax, ay = PADDING, PADDING
        draw.ellipse([ax, ay, ax + AVATAR, ay + AVATAR], fill=(45, 65, 120))
        initial = (author[0] if author else "?").upper()
        fi = _get_font(22, bold=True)
        iw = draw.textlength(initial, font=fi)
        draw.text((ax + (AVATAR - iw) / 2, ay + 10), initial, font=fi, fill=(160, 190, 240))

        # Author + handle
        tx = ax + AVATAR + 12
        draw.text((tx, ay + 4), author[:22], font=f_author, fill=_TWEET_TEXT)
        draw.text((tx, ay + 28), f"@{handle}"[:26], font=f_handle, fill=_HANDLE_COLOR)

        # Tweet text
        ty = ay + AVATAR + 8
        for line in wrapped:
            draw.text((PADDING, ty), line, font=f_text, fill=_TWEET_TEXT)
            ty += lh

        # Stats
        stats = f"♥ {likes:,}   🔁 {rts:,}"
        draw.text((PADDING, ty + 4), stats, font=f_stats, fill=_HANDLE_COLOR)

        return img
    except Exception as e:
        logger.warning(f"tweet card render error: {e}")
        return None


def _split_subtitle_chunks(text: str, n_chunks: int) -> list:
    """Split insight text into ~n_chunks subtitle segments by sentence."""
    import re as _re
    # Split on Chinese/English sentence endings
    sentences = _re.split(r'(?<=[。！？.!?])\s*', text.strip())
    sentences = [s.strip() for s in sentences if s.strip()]
    if not sentences:
        return [text.strip()]

    # Group sentences into n_chunks buckets
    total_chars = sum(len(s) for s in sentences)
    target = max(1, total_chars // max(1, n_chunks))
    chunks, current = [], ""
    for s in sentences:
        if len(current) + len(s) > target and current:
            chunks.append(current.strip())
            current = s
        else:
            current = (current + "　" + s).strip() if current else s
    if current:
        chunks.append(current.strip())
    return chunks if chunks else [text.strip()]


def _score_tweets_for_paragraph(para: str, tweets: list) -> list:
    """Score tweets against a paragraph by keyword overlap. Returns top 3."""
    import re as _re
    # Extract CJK words (3+ chars) and English tokens (4+ chars) as keywords
    cjk = _re.findall(r'[\u4e00-\u9fff]{2,}', para)
    eng = _re.findall(r'[a-zA-Z]{4,}', para.lower())
    keywords = set(cjk + eng)
    if not keywords:
        return []

    scored = []
    for tw in tweets:
        tw_text = (tw.get("text") or "").lower()
        score = sum(1 for kw in keywords if kw.lower() in tw_text)
        if score > 0:
            scored.append((score, tw))
    scored.sort(key=lambda x: -x[0])
    return [t for _, t in scored[:3]]


def _composite_frame(
    pdf_page_img: "object",  # PIL Image of the PDF page
    tweet_imgs: list,         # list of PIL Image tweet cards
    subtitle: str,
) -> bytes:
    """Composite PDF page + tweet cards + subtitle text into 1920×1080 PNG bytes."""
    from PIL import Image, ImageDraw

    frame = Image.new("RGB", (_FW, _FH), _BG)

    # ── PDF page (left panel) ────────────────────────────────────────────────
    avail_h = _FH - _SUB_H - 20
    scale = min(_PDF_W / pdf_page_img.width, avail_h / pdf_page_img.height)
    new_w = int(pdf_page_img.width * scale)
    new_h = int(pdf_page_img.height * scale)
    pdf_resized = pdf_page_img.resize((new_w, new_h), Image.LANCZOS)
    px = (_PDF_W - new_w) // 2
    py = (avail_h - new_h) // 2
    frame.paste(pdf_resized, (px, py))

    # ── Tweet cards (right panel) ────────────────────────────────────────────
    if tweet_imgs:
        rx = _PDF_W + _GAP
        avail_tweet_h = _FH - _SUB_H - 20
        n = len(tweet_imgs)
        gap_between = 12
        total_cards_h = sum(img.height for img in tweet_imgs) + gap_between * (n - 1)
        ry = max(10, (avail_tweet_h - total_cards_h) // 2)
        for card_img in tweet_imgs:
            if ry + card_img.height > avail_tweet_h:
                break
            frame.paste(card_img, (rx, ry))
            ry += card_img.height + gap_between

    # ── Subtitle bar ─────────────────────────────────────────────────────────
    if subtitle:
        # Semi-transparent overlay strip at bottom
        overlay = Image.new("RGBA", (_FW, _SUB_H), _SUB_BG)
        frame_rgba = frame.convert("RGBA")
        frame_rgba.paste(overlay, (0, _FH - _SUB_H), overlay)
        frame = frame_rgba.convert("RGB")

        draw = ImageDraw.Draw(frame)
        f_sub = _get_font(30)
        # Wrap subtitle text to fit full width with padding
        dummy = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        sub_lines = _wrap_text(subtitle, f_sub, _FW - 80, dummy)[:3]
        bb = dummy.textbbox((0, 0), "测Ag", font=f_sub)
        lh = bb[3] - bb[1] + 6
        total_h = len(sub_lines) * lh
        ty = _FH - _SUB_H + (_SUB_H - total_h) // 2
        for line in sub_lines:
            lw = draw.textlength(line, font=f_sub)
            draw.text((_FW // 2 - lw // 2, ty), line, font=f_sub, fill=_SUB_TEXT)
            ty += lh

    buf = io.BytesIO()
    frame.save(buf, format="PNG", optimize=False)
    buf.seek(0)
    return buf.read()


async def generate_video_from_pdf(
    pdf_bytes: bytes,
    audio_path: Optional[str],
    insight_text: str = "",
    tweets: Optional[list] = None,
    on_progress=None,
) -> Optional[bytes]:
    """
    Render PDF pages into a 1920×1080 video with:
      - Left panel: PDF slide
      - Right panel: matched tweet cards
      - Bottom bar: rolling subtitle from insight_text
      - Audio track

    Returns raw MP4 bytes, or None on failure.
    """
    async def _p(pct: int, msg: str):
        if on_progress:
            await on_progress(pct, msg)

    await _p(5, "解析PDF...")
    try:
        import fitz
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

    audio_duration = 0.0
    if audio_path and os.path.exists(audio_path):
        audio_duration = _get_audio_duration(audio_path)
    if audio_duration <= 0:
        audio_duration = n_pages * 20.0

    # ── Subtitle chunks: ~1 chunk every 6 seconds ────────────────────────────
    n_sub_chunks = max(n_pages, int(audio_duration / 6))
    sub_chunks = _split_subtitle_chunks(insight_text, n_sub_chunks) if insight_text else [""] * n_pages
    n_sub = len(sub_chunks)
    sub_duration = audio_duration / n_sub  # seconds per subtitle chunk

    # ── Match tweets to PDF pages ─────────────────────────────────────────────
    tweets = tweets or []
    # Split insight text into paragraphs (one per page roughly)
    paras = [p.strip() for p in insight_text.split("\n\n") if p.strip()] if insight_text else []
    page_tweets: list[list] = []
    for i in range(n_pages):
        para = paras[i] if i < len(paras) else (insight_text or "")
        matched = _score_tweets_for_paragraph(para, tweets) if tweets else []
        page_tweets.append(matched)

    await _p(15, "渲染推文卡片...")

    # Pre-render tweet cards for each page (run in thread to avoid blocking)
    def _render_page_cards(matched_tweets):
        cards = []
        for tw in matched_tweets[:3]:
            img = _render_tweet_card(tw, _TWEET_W)
            if img:
                cards.append(img)
        return cards

    page_tweet_imgs = []
    for matched in page_tweets:
        imgs = await asyncio.to_thread(_render_page_cards, matched)
        page_tweet_imgs.append(imgs)

    await _p(25, f"渲染 {n_pages} 页PDF + {n_sub} 段字幕...")

    # Render PDF pages to PIL Images
    from PIL import Image as _PILImage
    pdf_page_imgs = []
    for i, page in enumerate(doc):
        mat = fitz.Matrix(2.0, 2.0)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = _PILImage.frombytes("RGB", [pix.width, pix.height], pix.samples)
        pdf_page_imgs.append(img)
        await _p(25 + int(20 * (i + 1) / n_pages), f"解析第 {i+1}/{n_pages} 页...")
    doc.close()

    await _p(50, "合成复合帧...")

    # Build frames: one frame per subtitle chunk, showing appropriate PDF page
    def _build_frame(sub_idx: int) -> bytes:
        t = sub_idx * sub_duration
        page_idx = min(int(t / (audio_duration / n_pages)), n_pages - 1)
        subtitle = sub_chunks[sub_idx] if sub_idx < len(sub_chunks) else ""
        return _composite_frame(pdf_page_imgs[page_idx], page_tweet_imgs[page_idx], subtitle)

    with tempfile.TemporaryDirectory() as tmpdir:
        frame_paths = []
        for i in range(n_sub):
            frame_bytes = await asyncio.to_thread(_build_frame, i)
            fp = os.path.join(tmpdir, f"frame_{i:05d}.png")
            with open(fp, "wb") as f:
                f.write(frame_bytes)
            frame_paths.append(fp)
            if i % 5 == 0:
                await _p(50 + int(25 * i / n_sub), f"合成帧 {i+1}/{n_sub}...")

        await _p(78, "ffmpeg 编码视频...")
        out_mp4 = os.path.join(tmpdir, "output.mp4")
        ffmpeg = _get_ffmpeg()

        concat_txt = os.path.join(tmpdir, "concat.txt")
        with open(concat_txt, "w") as f:
            for fp in frame_paths:
                f.write(f"file '{fp}'\n")
                f.write(f"duration {sub_duration:.3f}\n")
            f.write(f"file '{frame_paths[-1]}'\n")

        if audio_path and os.path.exists(audio_path):
            cmd = [
                ffmpeg, "-y", "-loglevel", "error",
                "-f", "concat", "-safe", "0", "-i", concat_txt,
                "-i", audio_path,
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
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
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
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
            _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=600)
        except asyncio.TimeoutError:
            proc.kill()
            logger.error("pdf-video: ffmpeg timed out after 600s")
            return None
        if proc.returncode != 0:
            logger.error(f"pdf-video: ffmpeg failed:\n{stderr_bytes.decode()[-600:]}")
            return None

        await _p(95, "打包完成...")
        with open(out_mp4, "rb") as f:
            data = f.read()

    logger.info(
        f"pdf-video: done {len(data)//1024}KB, {n_pages} pages, "
        f"{n_sub} subtitle frames, {audio_duration:.1f}s audio, "
        f"{len(tweets)} tweets matched"
    )
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
