"""
services/tts_service.py — TTS 语音合成。
优先级：MiniMax > OpenAI > edge-tts
"""

import asyncio
import base64
import os
import re
import tempfile
from typing import Optional
from loguru import logger
import httpx


def _clean_for_tts(text: str, lang: str = "en") -> str:
    """移除 URL、emoji、Markdown 标记，修正专有名词发音。"""
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(
        r'[\U00010000-\U0010ffff\u2600-\u26FF\u2700-\u27BF]',
        '', text, flags=re.UNICODE,
    )
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'\*{1,3}|_{1,2}|~~|#{1,6}\s*', '', text)

    if lang == "zh":
        text = re.sub(r'(?<![a-zA-Z])(ARKREEN|Arkreen)(?![a-zA-Z])', '阿克林', text)
        text = re.sub(r'(?<![a-zA-Z])(GREENBTC|GreenBTC|Greenbtc)(?![a-zA-Z])', '绿色比特币', text)
        text = re.sub(r'(?<![a-zA-Z])(TLAY|Tlay)(?![a-zA-Z])', 'T Lay', text)
        text = re.sub(r'(?<![a-zA-Z])BTC(?![a-zA-Z])', 'B T C', text)
        text = re.sub(r'(?<![a-zA-Z])NFT(?![a-zA-Z])', 'N F T', text)
        text = re.sub(r'(?<![a-zA-Z])DAO(?![a-zA-Z])', '道', text)
        text = re.sub(r'(?<![a-zA-Z])(DeFi|DEFI)(?![a-zA-Z])', 'dee-fye', text)
        text = re.sub(r'(?<![a-zA-Z])(Web3|WEB3)(?![a-zA-Z])', 'Web three', text)
    else:
        text = re.sub(r'\bARKREEN\b|\bArkreen\b', 'ark-reen', text)
        text = re.sub(r'\bGREENBTC\b|\bGreenBTC\b', 'Green B T C', text)
        text = re.sub(r'\bTLAY\b|\bTlay\b', 'T-lay', text)
        text = re.sub(r'\bBTC\b', 'B T C', text)
        text = re.sub(r'\bNFT\b', 'N F T', text)
        text = re.sub(r'\bDAO\b', 'dow', text)
        text = re.sub(r'\bDeFi\b|\bDEFI\b', 'dee-fye', text)
        text = re.sub(r'\bWeb3\b|\bWEB3\b', 'web three', text)
    return text.strip()


def _get_ffmpeg() -> str:
    """Return path to ffmpeg: bundled (imageio-ffmpeg) > system."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def _split_text(text: str, max_chars: int = 4000) -> list[str]:
    """按段落拆分长文本，每片不超过 max_chars。"""
    paragraphs = text.split("\n\n")
    chunks = []
    current = ""
    for p in paragraphs:
        if len(current) + len(p) + 2 > max_chars:
            if current:
                chunks.append(current.strip())
            current = p
        else:
            current = current + "\n\n" + p if current else p
    if current:
        chunks.append(current.strip())
    return chunks if chunks else [text]


# ── MiniMax TTS（首选）──────────────────────────────────────


async def synthesize_minimax(text: str, output_path: str, lang: str = "zh") -> bool:
    """使用 MiniMax TTS 生成音频。"""
    api_key = os.getenv("MINIMAX_API_KEY")
    group_id = os.getenv("MINIMAX_GROUP_ID")
    if not api_key or not group_id:
        logger.warning("MINIMAX_API_KEY or MINIMAX_GROUP_ID not set")
        return False

    clean_text = _clean_for_tts(text, lang)
    base_url = os.getenv("MINIMAX_BASE_URL", "https://api.minimaxi.chat")

    # 中文用 presenter_male，英文用 Friendly_Person（English_male 不存在）
    voice_zh = os.getenv("MINIMAX_VOICE_ZH", "presenter_male")
    voice_en = os.getenv("MINIMAX_VOICE_EN", "Friendly_Person")
    voice_id = voice_zh if lang == "zh" else voice_en
    model = os.getenv("MINIMAX_TTS_MODEL", "speech-02-hd")

    chunks = _split_text(clean_text)

    try:
        if len(chunks) == 1:
            audio_data = await _minimax_tts_call(
                base_url, api_key, group_id, model, voice_id, chunks[0]
            )
            if audio_data:
                with open(output_path, "wb") as f:
                    f.write(audio_data)
        else:
            tmp_files = []
            for i, chunk in enumerate(chunks):
                audio_data = await _minimax_tts_call(
                    base_url, api_key, group_id, model, voice_id, chunk
                )
                if not audio_data:
                    raise RuntimeError(f"MiniMax TTS failed for chunk {i}")
                tmp_path = os.path.join(
                    tempfile.gettempdir(), f"minimax_chunk_{i}_{os.getpid()}.mp3"
                )
                with open(tmp_path, "wb") as f:
                    f.write(audio_data)
                tmp_files.append(tmp_path)

            await _concat_audio(tmp_files, output_path)
            for f in tmp_files:
                if os.path.exists(f):
                    os.unlink(f)

        logger.info(f"MiniMax TTS saved: {output_path} ({len(chunks)} chunk(s))")
        return True

    except Exception as e:
        logger.error(f"MiniMax TTS error: {e}")
        return False


async def _minimax_tts_call(
    base_url: str, api_key: str, group_id: str,
    model: str, voice_id: str, text: str,
) -> Optional[bytes]:
    """调用 MiniMax T2A v2 API，返回音频 bytes。"""
    url = f"{base_url}/v1/t2a_v2?GroupId={group_id}"
    payload = {
        "model": model,
        "text": text,
        "voice_setting": {
            "voice_id": voice_id,
            "speed": 1.0,
            "vol": 1.0,
            "pitch": 0,
        },
        "audio_setting": {
            "sample_rate": 32000,
            "bitrate": 128000,
            "format": "mp3",
        },
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    if data.get("base_resp", {}).get("status_code", 0) != 0:
        error_msg = data.get("base_resp", {}).get("status_msg", "unknown error")
        raise RuntimeError(f"MiniMax API error: {error_msg}")

    audio_hex = data.get("data", {}).get("audio", "")
    if not audio_hex:
        raise RuntimeError("MiniMax returned empty audio")

    return bytes.fromhex(audio_hex)


# ── 统一入口 ────────────────────────────────────────────────


async def synthesize(text: str, output_path: str, lang: str = "zh") -> bool:
    """统一 TTS 入口：MiniMax > OpenAI > edge-tts。"""
    # 1. 尝试 MiniMax
    if os.getenv("MINIMAX_API_KEY"):
        ok = await synthesize_minimax(text, output_path, lang)
        if ok:
            return True
        logger.warning("MiniMax TTS failed, trying next...")

    # 2. 尝试 OpenAI
    if os.getenv("OPENAI_API_KEY"):
        ok = await synthesize_openai(text, output_path, lang)
        if ok:
            return True
        logger.warning("OpenAI TTS failed, trying next...")

    # 3. fallback: edge-tts
    return await synthesize_edge_tts(text, output_path, lang)


async def synthesize_openai(text: str, output_path: str, lang: str = "zh") -> bool:
    """使用 OpenAI TTS 生成音频。"""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return False

    clean_text = _clean_for_tts(text, lang)
    model = os.getenv("OPENAI_TTS_MODEL", "tts-1")
    voice_zh = os.getenv("OPENAI_TTS_VOICE_ZH", "nova")
    voice_en = os.getenv("OPENAI_TTS_VOICE_EN", "nova")
    voice = voice_zh if lang == "zh" else voice_en

    chunks = _split_text(clean_text)

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key)

        if len(chunks) == 1:
            response = await client.audio.speech.create(
                model=model, voice=voice, input=chunks[0],
            )
            content = await response.aread()
            with open(output_path, "wb") as f:
                f.write(content)
        else:
            tmp_files = []
            for i, chunk in enumerate(chunks):
                tmp_path = os.path.join(
                    tempfile.gettempdir(), f"tts_chunk_{i}_{os.getpid()}.mp3"
                )
                response = await client.audio.speech.create(
                    model=model, voice=voice, input=chunk,
                )
                content = await response.aread()
                with open(tmp_path, "wb") as f:
                    f.write(content)
                tmp_files.append(tmp_path)

            await _concat_audio(tmp_files, output_path)
            for f in tmp_files:
                if os.path.exists(f):
                    os.unlink(f)

        logger.info(f"OpenAI TTS saved: {output_path} ({len(chunks)} chunk(s))")
        return True

    except Exception as e:
        logger.error(f"OpenAI TTS error: {e}")
        return False


async def synthesize_edge_tts(text: str, output_path: str, lang: str = "zh") -> bool:
    """edge-tts fallback（免费）。"""
    try:
        import edge_tts
        voice = "zh-CN-YunyangNeural" if lang == "zh" else "en-US-AriaNeural"
        clean_text = _clean_for_tts(text, lang)
        communicate = edge_tts.Communicate(clean_text, voice)
        await communicate.save(output_path)
        logger.info(f"edge-tts saved: {output_path}")
        return True
    except Exception as e:
        logger.error(f"edge-tts error: {e}")
        return False


async def _concat_audio(files: list[str], output_path: str) -> None:
    """用 ffmpeg 拼接多个音频文件。"""
    list_path = os.path.join(tempfile.gettempdir(), f"concat_{os.getpid()}.txt")
    with open(list_path, "w") as f:
        for fp in files:
            f.write(f"file '{fp}'\n")

    proc = await asyncio.create_subprocess_exec(
        _get_ffmpeg(), "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", list_path,
        "-c", "copy", output_path, "-y",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("ffmpeg concat timed out after 120s")
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg concat failed: {stderr.decode()[:500]}")

    if os.path.exists(list_path):
        os.unlink(list_path)


async def normalize_audio(input_path: str, output_path: str) -> bool:
    """ffmpeg 响度标准化 + 高通滤波。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            _get_ffmpeg(), "-i", input_path,
            "-af", "highpass=f=80,loudnorm=I=-16.6:TP=-1.5:LRA=11",
            "-ar", "44100", "-b:a", "192k",
            output_path, "-y",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(stderr.decode()[:500])
        logger.info(f"Audio normalized: {output_path}")
        return True
    except Exception as e:
        logger.error(f"Audio normalization error: {e}")
        return False
