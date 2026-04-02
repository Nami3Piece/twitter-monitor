"""
services/tts_service.py — OpenAI TTS 语音合成，edge-tts 作为 fallback。
"""

import asyncio
import os
import re
import tempfile
from typing import Optional
from loguru import logger


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


async def synthesize_openai(text: str, output_path: str, lang: str = "zh") -> bool:
    """使用 OpenAI TTS 生成音频。长文本自动分片拼接。"""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.warning("OPENAI_API_KEY not set, falling back to edge-tts")
        return await synthesize_edge_tts(text, output_path, lang)

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
            # 多片：分别生成，ffmpeg 拼接
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
        logger.error(f"OpenAI TTS error: {e}, falling back to edge-tts")
        return await synthesize_edge_tts(text, output_path, lang)


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
        "ffmpeg", "-f", "concat", "-safe", "0", "-i", list_path,
        "-c", "copy", output_path, "-y",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg concat failed: {stderr.decode()[:500]}")

    if os.path.exists(list_path):
        os.unlink(list_path)


async def normalize_audio(input_path: str, output_path: str) -> bool:
    """ffmpeg 响度标准化 + 高通滤波。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-i", input_path,
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
