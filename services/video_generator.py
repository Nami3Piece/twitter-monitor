"""
services/video_generator.py — 播客视频生成器。
输入：音频 + 脚本 + 头像 → 输出：MP4 视频

视频画面 (1080x1080 方形):
  ┌──────────────────────┐
  │     品牌标题           │
  │     日期              │
  │    ┌────────────┐    │
  │    │  圆形头像    │    │
  │    └────────────┘    │
  │   ∿∿∿ 声波动画 ∿∿∿   │
  │  ┌────────────────┐  │
  │  │  逐句字幕       │  │
  │  └────────────────┘  │
  │  dailyxdigest.uk     │
  └──────────────────────┘
"""

import asyncio
import os
import re
import subprocess
import tempfile
from typing import Optional
from loguru import logger


def _split_script_to_sentences(script: str) -> list[str]:
    """将脚本拆分为句子。"""
    raw = re.split(r'(?<=[。！？.!?])\s*', script.strip())
    sentences = [s.strip() for s in raw if s.strip() and len(s.strip()) > 2]
    return sentences


def _generate_ass_subtitles(
    sentences: list[str],
    total_duration: float,
    video_width: int = 1080,
    video_height: int = 1080,
) -> str:
    """生成 ASS 字幕，修复溢出问题。"""
    total_chars = sum(len(s) for s in sentences)
    if total_chars == 0:
        return ""

    # 字幕区域宽度（像素），留边距
    max_line_chars = 18  # 中文每行最多字符数
    font_size = 38
    subtitle_y_margin = int(video_height * 0.15)

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
        f"Style: Default,Noto Sans CJK SC,{font_size},&H00FFFFFF,&H000000FF,"
        f"&H00000000,&H80000000,0,0,0,0,100,100,1,0,1,2.5,0,"
        f"2,60,60,{subtitle_y_margin},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )

    current_time = 0.3
    usable_duration = total_duration - 0.8

    for sentence in sentences:
        char_ratio = len(sentence) / total_chars
        duration = max(char_ratio * usable_duration, 1.2)

        start = current_time
        end = min(start + duration, total_duration - 0.3)

        def _fmt(t: float) -> str:
            h = int(t // 3600)
            m = int((t % 3600) // 60)
            s = t % 60
            return f"{h}:{m:02d}:{s:05.2f}"

        # 智能断行：每行不超过 max_line_chars 个字符
        display = _wrap_subtitle(sentence, max_line_chars)

        ass += f"Dialogue: 0,{_fmt(start)},{_fmt(end)},Default,,0,0,0,,{display}\n"
        current_time = end

    return ass


def _wrap_subtitle(text: str, max_chars: int = 18) -> str:
    """将长字幕断行，避免溢出。"""
    if len(text) <= max_chars:
        return text

    lines = []
    remaining = text
    while len(remaining) > max_chars:
        # 在 max_chars 范围内找最近的标点断行
        break_at = max_chars
        for offset in range(min(6, max_chars)):
            pos = max_chars - offset
            if 0 < pos < len(remaining) and remaining[pos] in '，、；。！？,;.!? ':
                break_at = pos + 1
                break
        lines.append(remaining[:break_at].rstrip())
        remaining = remaining[break_at:].lstrip()
    if remaining:
        lines.append(remaining)

    return "\\N".join(lines)


def _create_avatar_overlay(avatar_path: str, size: int = 200) -> Optional[str]:
    """将头像裁剪为圆形 PNG。"""
    try:
        from PIL import Image, ImageDraw

        img = Image.open(avatar_path).convert("RGBA")
        w, h = img.size
        crop_size = min(w, h)
        left = (w - crop_size) // 2
        top = (h - crop_size) // 2
        img = img.crop((left, top, left + crop_size, top + crop_size))
        img = img.resize((size, size), Image.LANCZOS)

        mask = Image.new("L", (size, size), 0)
        draw = ImageDraw.Draw(mask)
        draw.ellipse([0, 0, size, size], fill=255)

        result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        result.paste(img, (0, 0), mask)

        border_draw = ImageDraw.Draw(result)
        border_draw.ellipse([2, 2, size - 3, size - 3], outline=(255, 255, 255, 180), width=3)

        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        result.save(tmp.name, "PNG")
        return tmp.name

    except Exception as e:
        logger.error(f"Avatar processing error: {e}")
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


def _generate_bgm(duration: float, output_path: str) -> bool:
    """用 ffmpeg 生成柔和的背景音乐（正弦波和弦 + 低通滤波）。"""
    try:
        # 生成柔和的环境音：多个低频正弦波叠加 + 低通滤波
        filter_str = (
            f"sine=frequency=220:duration={duration}:sample_rate=44100[s1];"
            f"sine=frequency=330:duration={duration}:sample_rate=44100[s2];"
            f"sine=frequency=440:duration={duration}:sample_rate=44100[s3];"
            f"[s1][s2][s3]amix=inputs=3:duration=longest[mixed];"
            f"[mixed]lowpass=f=400,volume=0.03,afade=t=in:st=0:d=3,afade=t=out:st={duration-3}:d=3[bgm]"
        )
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", filter_str.split(";")[0].split("[")[0],
             "-t", str(duration), "-af", "lowpass=f=400,volume=0.03",
             output_path],
            capture_output=True, timeout=30,
        )
        # 简化：直接生成一个安静的 ambient tone
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi",
             "-i", f"sine=frequency=174:duration={duration}:sample_rate=44100",
             "-f", "lavfi",
             "-i", f"sine=frequency=261:duration={duration}:sample_rate=44100",
             "-filter_complex",
             f"[0:a][1:a]amix=inputs=2[m];[m]lowpass=f=300,volume=0.025,afade=t=in:st=0:d=2,afade=t=out:st={max(0,duration-3)}:d=3[out]",
             "-map", "[out]", "-ar", "44100", output_path],
            capture_output=True, timeout=30,
        )
        return os.path.exists(output_path) and os.path.getsize(output_path) > 100
    except Exception as e:
        logger.warning(f"BGM generation failed: {e}")
        return False


async def generate_podcast_video(
    audio_path: str,
    script: str,
    avatar_path: Optional[str] = None,
    date: str = "",
    lang: str = "zh",
    format: str = "square",
) -> Optional[str]:
    """
    生成播客视频。
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
        # 1. 生成字幕
        sentences = _split_script_to_sentences(script)
        ass_content = _generate_ass_subtitles(sentences, duration, width, height)
        ass_path = os.path.join(tmpdir, "subtitles.ass")
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write(ass_content)

        # 2. 处理头像
        avatar_overlay = None
        if avatar_path and os.path.exists(avatar_path):
            avatar_size = 160 if format == "square" else 220
            avatar_overlay = await asyncio.to_thread(
                _create_avatar_overlay, avatar_path, avatar_size
            )

        # 3. 生成背景音乐
        bgm_path = os.path.join(tmpdir, "bgm.wav")
        has_bgm = await asyncio.to_thread(_generate_bgm, duration, bgm_path)

        # 4. 混合音频（语音 + BGM）
        if has_bgm:
            mixed_audio = os.path.join(tmpdir, "mixed.mp3")
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y",
                "-i", audio_path,
                "-i", bgm_path,
                "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=first:dropout_transition=2[out]",
                "-map", "[out]", "-b:a", "192k", mixed_audio,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            if proc.returncode == 0:
                audio_input = mixed_audio
            else:
                audio_input = audio_path
        else:
            audio_input = audio_path

        # 5. 构建 ffmpeg 视频命令
        avatar_size = 160 if format == "square" else 220
        avatar_y = int(height * 0.15)
        avatar_x = (width - avatar_size) // 2
        wave_y = int(height * 0.42)

        filters = []

        # 背景
        filters.append(
            f"color=c=0x0f0f13:s={width}x{height}:d={duration}:r=30[bg]"
        )

        # 声波
        filters.append(
            f"[1:a]showwaves=s={width - 120}x80:mode=cline:rate=30:"
            f"colors=0x4f46e5@0.7|0x7c3aed@0.5:scale=sqrt[waves]"
        )
        filters.append(
            f"[bg][waves]overlay=60:{wave_y}:shortest=1[bg_wave]"
        )

        # 头像
        if avatar_overlay:
            filters.append(
                f"[bg_wave][2:v]overlay={avatar_x}:{avatar_y}[bg_avatar]"
            )
            last = "bg_avatar"
        else:
            last = "bg_wave"

        # 品牌标题
        filters.append(
            f"[{last}]drawtext=text='DailyX Digest':"
            f"fontsize=32:fontcolor=0xa5b4fc:"
            f"x=(w-text_w)/2:y={int(height * 0.05)}:"
            f"fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            f"[t1]"
        )

        # 日期
        safe_date = date.replace(":", "\\:")
        filters.append(
            f"[t1]drawtext=text='{safe_date}':"
            f"fontsize=22:fontcolor=0x666666:"
            f"x=(w-text_w)/2:y={int(height * 0.09)}:"
            f"fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
            f"[t2]"
        )

        # 底部水印
        filters.append(
            f"[t2]drawtext=text='dailyxdigest.uk':"
            f"fontsize=18:fontcolor=0x444444:"
            f"x=(w-text_w)/2:y=h-{int(height * 0.04)}:"
            f"fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
            f"[t3]"
        )

        # 字幕
        safe_ass = ass_path.replace("\\", "/").replace(":", "\\:").replace("'", "'\\''")
        filters.append(
            f"[t3]ass='{safe_ass}'[final]"
        )

        filter_complex = ";".join(filters)
        output_path = os.path.join(tmpdir, "podcast.mp4")

        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c=0x0f0f13:s={width}x{height}:d={duration}:r=30",
            "-i", audio_input,
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
        final_path = os.path.join(audio_dir, f"podcast_{date}_{lang}.mp4")

        import shutil
        shutil.move(output_path, final_path)

        size_kb = os.path.getsize(final_path) // 1024
        logger.info(f"Podcast video generated: {final_path} ({size_kb}KB)")
        return final_path

    except Exception as e:
        logger.error(f"Video generation error: {e}")
        return None

    finally:
        if avatar_overlay and os.path.exists(avatar_overlay):
            os.unlink(avatar_overlay)
