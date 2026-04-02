"""
services/video_generator.py — 播客视频生成器。
输入：音频 + 脚本 + 头像 → 输出：MP4 视频（头像 + 声波 + 字幕）

视频布局 (1080x1920 竖屏 / 1080x1080 方形可选):
  ┌──────────────────────┐
  │     品牌标题区        │
  │                      │
  │    ┌────────────┐    │
  │    │  圆形头像    │    │
  │    └────────────┘    │
  │                      │
  │   ∿∿∿ 声波动画 ∿∿∿   │
  │                      │
  │  ┌────────────────┐  │
  │  │  逐句字幕       │  │
  │  └────────────────┘  │
  │                      │
  │  DailyX Digest 2026  │
  └──────────────────────┘
"""

import asyncio
import math
import os
import tempfile
from typing import Optional
from loguru import logger


def _split_script_to_sentences(script: str) -> list[str]:
    """将脚本拆分为句子（用于字幕）。"""
    import re
    # 按中文句号、问号、叹号、英文句号分割
    raw = re.split(r'(?<=[。！？.!?])\s*', script.strip())
    sentences = [s.strip() for s in raw if s.strip() and len(s.strip()) > 2]
    return sentences


def _generate_ass_subtitles(
    sentences: list[str],
    total_duration: float,
    video_width: int = 1080,
    video_height: int = 1080,
) -> str:
    """
    生成 ASS 字幕文件内容。
    按句子均匀分配时间，底部居中显示。
    """
    # 按字符数加权分配时间
    total_chars = sum(len(s) for s in sentences)
    if total_chars == 0:
        return ""

    # ASS header
    ass = (
        "[Script Info]\n"
        f"PlayResX: {video_width}\n"
        f"PlayResY: {video_height}\n"
        "ScriptType: v4.00+\n"
        "WrapStyle: 0\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,"
        "OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,"
        "ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
        "Alignment,MarginL,MarginR,MarginV,Encoding\n"
        "Style: Default,Noto Sans CJK SC,42,&H00FFFFFF,&H000000FF,"
        "&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,0,"
        f"2,40,40,{int(video_height * 0.18)},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )

    current_time = 0.5  # 0.5s 延迟开始
    usable_duration = total_duration - 1.0  # 留 1s 尾部

    for sentence in sentences:
        char_ratio = len(sentence) / total_chars
        duration = max(char_ratio * usable_duration, 1.5)  # 至少 1.5s

        start = current_time
        end = min(start + duration, total_duration - 0.5)

        def _fmt_time(t: float) -> str:
            h = int(t // 3600)
            m = int((t % 3600) // 60)
            s = t % 60
            return f"{h}:{m:02d}:{s:05.2f}"

        # 每行最多 20 个中文字符或 40 个英文字符
        display_text = sentence
        if len(sentence) > 22:
            mid = len(sentence) // 2
            # 找最近的标点或空格断行
            break_at = mid
            for offset in range(min(8, mid)):
                for pos in [mid + offset, mid - offset]:
                    if 0 < pos < len(sentence) and sentence[pos] in '，、 ,;；':
                        break_at = pos + 1
                        break
                else:
                    continue
                break
            display_text = sentence[:break_at] + "\\N" + sentence[break_at:]

        ass += (
            f"Dialogue: 0,{_fmt_time(start)},{_fmt_time(end)},"
            f"Default,,0,0,0,,{display_text}\n"
        )
        current_time = end

    return ass


def _create_avatar_overlay(avatar_path: str, size: int = 200) -> Optional[str]:
    """将头像裁剪为圆形 PNG，返回临时文件路径。"""
    try:
        from PIL import Image, ImageDraw

        img = Image.open(avatar_path).convert("RGBA")
        # 裁剪为正方形
        w, h = img.size
        crop_size = min(w, h)
        left = (w - crop_size) // 2
        top = (h - crop_size) // 2
        img = img.crop((left, top, left + crop_size, top + crop_size))
        img = img.resize((size, size), Image.LANCZOS)

        # 圆形蒙版
        mask = Image.new("L", (size, size), 0)
        draw = ImageDraw.Draw(mask)
        draw.ellipse([0, 0, size, size], fill=255)

        # 应用蒙版
        result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        result.paste(img, (0, 0), mask)

        # 画边框
        border_draw = ImageDraw.Draw(result)
        border_draw.ellipse([0, 0, size - 1, size - 1], outline=(255, 255, 255, 200), width=3)

        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        result.save(tmp.name, "PNG")
        return tmp.name

    except Exception as e:
        logger.error(f"Avatar processing error: {e}")
        return None


def _get_audio_duration(audio_path: str) -> float:
    """获取音频时长（秒）。"""
    import subprocess
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
        capture_output=True, text=True, timeout=10,
    )
    try:
        return float(result.stdout.strip())
    except Exception:
        return 60.0


async def generate_podcast_video(
    audio_path: str,
    script: str,
    avatar_path: Optional[str] = None,
    date: str = "",
    lang: str = "zh",
    format: str = "square",  # "square" (1080x1080) or "portrait" (1080x1920)
) -> Optional[str]:
    """
    生成播客视频。

    参数：
      audio_path  — 音频文件路径
      script      — 播客脚本文字（用于字幕）
      avatar_path — 头像图片路径（可选）
      date        — 日期文字
      lang        — zh/en
      format      — square/portrait

    返回：输出 MP4 文件路径，失败返回 None。
    """
    if not os.path.exists(audio_path):
        logger.error(f"Audio not found: {audio_path}")
        return None

    width = 1080
    height = 1080 if format == "square" else 1920

    duration = await asyncio.to_thread(_get_audio_duration, audio_path)
    logger.info(f"Video generation: {duration:.1f}s audio, {width}x{height}")

    tmpdir = tempfile.mkdtemp(prefix="podcast_video_")

    try:
        # 1. 生成字幕文件
        sentences = _split_script_to_sentences(script)
        ass_content = _generate_ass_subtitles(sentences, duration, width, height)
        ass_path = os.path.join(tmpdir, "subtitles.ass")
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write(ass_content)

        # 2. 处理头像
        avatar_overlay = None
        if avatar_path and os.path.exists(avatar_path):
            avatar_size = 180 if format == "square" else 240
            avatar_overlay = await asyncio.to_thread(
                _create_avatar_overlay, avatar_path, avatar_size
            )

        # 3. 构建 ffmpeg filter_complex
        #
        # 布局计算:
        #   - 头像: 居中，距顶部 15% 位置
        #   - 声波: 头像下方，居中
        #   - 字幕: 底部 18% 位置 (ASS 控制)
        #   - 品牌: 顶部和底部文字 (drawtext)

        avatar_y = int(height * 0.12)
        wave_y = int(height * 0.40)
        avatar_size = 180 if format == "square" else 240
        avatar_x = (width - avatar_size) // 2

        # 背景色 + 声波 + 头像 + 字幕 + 文字水印
        filters = []

        # 背景：深色渐变
        filters.append(
            f"color=c=0x0f0f13:s={width}x{height}:d={duration}:r=30[bg]"
        )

        # 声波可视化
        filters.append(
            f"[1:a]showwaves=s={width - 120}x100:mode=cline:rate=30:"
            f"colors=0x4f46e5@0.8|0x7c3aed@0.6:scale=sqrt[waves]"
        )

        # 声波放到背景上
        filters.append(
            f"[bg][waves]overlay=60:{wave_y}:shortest=1[bg_wave]"
        )

        # 头像叠加
        if avatar_overlay:
            filters.append(
                f"[bg_wave][2:v]overlay={avatar_x}:{avatar_y}[bg_avatar]"
            )
            last_label = "bg_avatar"
        else:
            last_label = "bg_wave"

        # 品牌标题 (顶部)
        title = "DailyX Digest" if lang == "en" else "DailyX Digest"
        # ffmpeg drawtext 需要转义冒号和反斜杠
        safe_date = date.replace(":", "\\:")
        filters.append(
            f"[{last_label}]drawtext=text='{title}':"
            f"fontsize=36:fontcolor=0xa5b4fc:"
            f"x=(w-text_w)/2:y={int(height * 0.05)}:"
            f"fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            f"[with_title]"
        )

        # 日期 (头像上方)
        filters.append(
            f"[with_title]drawtext=text='{safe_date}':"
            f"fontsize=24:fontcolor=0x888888:"
            f"x=(w-text_w)/2:y={int(height * 0.09)}:"
            f"fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
            f"[with_date]"
        )

        # 底部品牌水印
        footer = "dailyxdigest.uk"
        filters.append(
            f"[with_date]drawtext=text='{footer}':"
            f"fontsize=20:fontcolor=0x555555:"
            f"x=(w-text_w)/2:y=h-{int(height * 0.04)}:"
            f"fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
            f"[with_footer]"
        )

        # 字幕叠加 (ASS)
        # 需要转义路径中的特殊字符
        safe_ass = ass_path.replace("\\", "/").replace(":", "\\:").replace("'", "'\\''")
        filters.append(
            f"[with_footer]ass='{safe_ass}'[final]"
        )

        filter_complex = ";".join(filters)

        # 4. 构建 ffmpeg 命令
        output_path = os.path.join(tmpdir, "podcast.mp4")

        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c=0x0f0f13:s={width}x{height}:d={duration}:r=30",
            "-i", audio_path,
        ]

        if avatar_overlay:
            cmd.extend(["-i", avatar_overlay])

        cmd.extend([
            "-filter_complex", filter_complex,
            "-map", "[final]",
            "-map", "1:a",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "128k",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            "-t", str(duration),
            output_path,
        ])

        logger.debug(f"ffmpeg cmd: {' '.join(cmd)}")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            logger.error(f"ffmpeg failed: {stderr.decode()[-800:]}")
            return None

        # 移动到持久目录
        audio_dir = os.getenv("AUDIO_DIR", "data/audio")
        os.makedirs(audio_dir, exist_ok=True)
        final_path = os.path.join(
            audio_dir, f"podcast_{date}_{lang}.mp4"
        )

        import shutil
        shutil.move(output_path, final_path)

        logger.info(f"Podcast video generated: {final_path} ({os.path.getsize(final_path) // 1024}KB)")
        return final_path

    except Exception as e:
        logger.error(f"Video generation error: {e}")
        return None

    finally:
        # 清理头像临时文件
        if avatar_overlay and os.path.exists(avatar_overlay):
            os.unlink(avatar_overlay)
