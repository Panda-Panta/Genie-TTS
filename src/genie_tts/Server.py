import asyncio
import os
import sys
import json
import struct
import threading
from typing import AsyncIterator, Optional, Callable, Union, Dict, List
import logging

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .Audio.ReferenceAudio import ReferenceAudio
from .Core.TTSPlayer import tts_player
from .ModelManager import model_manager
from .Utils.Shared import context
from .Utils.Language import normalize_language

logger = logging.getLogger(__name__)

_reference_audios: Dict[str, dict] = {}
_server_tts_lock = threading.Lock()
SUPPORTED_AUDIO_EXTS = {'.wav', '.flac', '.ogg', '.aiff', '.aif'}


def find_config_file_path() -> Optional[str]:
    candidates = [
        os.path.abspath("./UserData/GenieGuiConfig.json"),
    ]
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
        candidates.append(os.path.join(exe_dir, "UserData", "GenieGuiConfig.json"))
        candidates.append(os.path.abspath("UserData/GenieGuiConfig.json"))
    current_dir = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.abspath(os.path.join(current_dir, "..", "..", "UserData", "GenieGuiConfig.json")))
    candidates.append(r"D:\Tools\Genie-TTS\UserData\GenieGuiConfig.json")

    for cand in candidates:
        if os.path.isfile(cand):
            return cand
    return None


def auto_load_presets_from_config() -> int:
    """
    自动从 UserData/GenieGuiConfig.json 发现并批量注册说话人预设到 _reference_audios。
    返回成功注册的预设数量。
    """
    config_path = find_config_file_path()
    if not config_path:
        return 0

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            presets = json.load(f)
    except Exception as e:
        logger.error(f"Failed to read config file {config_path}: {e}")
        return 0

    base_dir = os.path.dirname(os.path.dirname(config_path))  # e.g. D:\Tools\Genie-TTS
    loaded_count = 0

    def _resolve(p: str) -> str:
        if not p:
            return ""
        if os.path.isabs(p) and os.path.exists(p):
            return p
        cand = os.path.normpath(os.path.join(base_dir, p))
        if os.path.exists(cand):
            return cand
        cand_cwd = os.path.abspath(p)
        if os.path.exists(cand_cwd):
            return cand_cwd
        return p

    for char_name, data in presets.items():
        if not isinstance(data, dict):
            continue
        m_dir = _resolve(data.get("genie_dir", ""))
        r_audio = _resolve(data.get("ref_audio", ""))
        r_text = data.get("ref_text", "")
        r_lang = data.get("lang", "Japanese")

        if os.path.exists(m_dir) and os.path.exists(r_audio) and r_text:
            set_server_reference_audio(
                character_name=char_name,
                audio_path=r_audio,
                audio_text=r_text,
                language=r_lang,
                model_dir=m_dir
            )
            loaded_count += 1

    if loaded_count > 0:
        logger.info(f"Auto-loaded {loaded_count} character presets from {config_path}.")
    return loaded_count


def set_server_reference_audio(
        character_name: str,
        audio_path: str,
        audio_text: str,
        language: str,
        model_dir: Optional[str] = None
):
    _reference_audios[character_name] = {
        'audio_path': audio_path,
        'audio_text': audio_text,
        'language': normalize_language(language),
        'model_dir': model_dir,
    }


def get_server_reference_audios() -> Dict[str, dict]:
    if not _reference_audios:
        auto_load_presets_from_config()
    return _reference_audios


def ensure_character_loaded(character_name: str) -> bool:
    """确保指定角色模型已载入 model_manager 缓存中，如果未载入则动态从 model_dir 载入"""
    if model_manager.get(character_name) is not None:
        return True

    info = _reference_audios.get(character_name)
    if not info:
        for k, v in _reference_audios.items():
            if k.lower() == character_name.lower():
                info = v
                break

    if not info:
        return False

    model_dir = info.get('model_dir')
    lang = info.get('language', 'Japanese')
    if model_dir and os.path.exists(model_dir):
        logger.info(f"Dynamically loading model for character '{character_name}' from: {model_dir}")
        success = model_manager.load_character(character_name=character_name, model_dir=model_dir, language=lang)
        return success
    return False


app = FastAPI()


class CharacterPayload(BaseModel):
    character_name: str
    onnx_model_dir: str
    language: str


class UnloadCharacterPayload(BaseModel):
    character_name: str


class ReferenceAudioPayload(BaseModel):
    character_name: str
    audio_path: str
    audio_text: str
    language: str


class TTSPayload(BaseModel):
    character_name: str
    text: str
    split_sentence: bool = False
    save_path: Optional[str] = None


@app.post("/load_character")
def load_character_endpoint(payload: CharacterPayload):
    try:
        model_manager.load_character(
            character_name=payload.character_name,
            model_dir=payload.onnx_model_dir,
            language=normalize_language(payload.language),
        )
        return {"status": "success", "message": f"Character '{payload.character_name}' loaded."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/unload_character")
def unload_character_endpoint(payload: UnloadCharacterPayload):
    try:
        model_manager.remove_character(character_name=payload.character_name)
        return {"status": "success", "message": f"Character '{payload.character_name}' unloaded."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/set_reference_audio")
def set_reference_audio_endpoint(payload: ReferenceAudioPayload):
    ext = os.path.splitext(payload.audio_path)[1].lower()
    if ext not in SUPPORTED_AUDIO_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"Audio format '{ext}' is not supported. Supported formats: {SUPPORTED_AUDIO_EXTS}",
        )
    set_server_reference_audio(
        character_name=payload.character_name,
        audio_path=payload.audio_path,
        audio_text=payload.audio_text,
        language=payload.language,
    )
    return {"status": "success", "message": f"Reference audio for '{payload.character_name}' set."}


def run_tts_in_background(
        character_name: str,
        text: str,
        split_sentence: bool,
        save_path: Optional[str],
        chunk_callback: Callable[[Optional[bytes]], None]
):
    with _server_tts_lock:
        try:
            ensure_character_loaded(character_name)
            ref_info = _reference_audios.get(character_name)
            if not ref_info:
                for k, v in _reference_audios.items():
                    if k.lower() == character_name.lower():
                        ref_info = v
                        character_name = k
                        break

            if not ref_info:
                logger.error(f"Cannot run TTS: character '{character_name}' reference audio not found.")
                return

            context.current_speaker = character_name
            context.current_prompt_audio = ReferenceAudio(
                prompt_wav=ref_info['audio_path'],
                prompt_text=ref_info['audio_text'],
                language=ref_info['language'],
            )
            tts_player.start_session(
                play=False,
                split=split_sentence,
                save_path=save_path,
                chunk_callback=chunk_callback,
            )
            tts_player.feed(text)
            tts_player.end_session()
            tts_player.wait_for_tts_completion()
        except Exception as e:
            logger.error(f"Error in TTS background task: {e}", exc_info=True)
        finally:
            chunk_callback(None)


def make_wav_header(sample_rate: int = 32000, num_channels: int = 1, bits_per_sample: int = 16) -> bytes:
    byte_rate = sample_rate * num_channels * bits_per_sample // 8
    block_align = num_channels * bits_per_sample // 8
    data_size = 0x7fffffff  # max for streaming WAV
    riff_size = data_size + 36
    return struct.pack(
        '<4sI4s4sIHHIIHH4sI',
        b'RIFF',
        riff_size,
        b'WAVE',
        b'fmt ',
        16,
        1,  # PCM
        num_channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b'data',
        data_size
    )


async def audio_stream_generator(queue: asyncio.Queue) -> AsyncIterator[bytes]:
    yield make_wav_header(sample_rate=32000, num_channels=1, bits_per_sample=16)
    while True:
        chunk = await queue.get()
        if chunk is None:
            break
        yield chunk


@app.get("/characters")
def list_characters_endpoint():
    if not _reference_audios:
        auto_load_presets_from_config()
    return list(_reference_audios.keys())


@app.post("/tts")
async def tts_endpoint(payload: TTSPayload):
    if not _reference_audios:
        auto_load_presets_from_config()

    raw_char = payload.character_name.strip() if payload.character_name else ""
    char_name = None

    # 1. 优先精确匹配
    if raw_char in _reference_audios:
        char_name = raw_char
    # 2. 不区分大小写匹配
    elif raw_char:
        for k in _reference_audios:
            if k.lower() == raw_char.lower():
                char_name = k
                break

    # 3. 客户端指定默认值 ("Default", "default", "") 时的智能解析
    if not char_name and (not raw_char or raw_char in ("", "Default", "default")):
        if context.current_speaker and context.current_speaker in _reference_audios:
            char_name = context.current_speaker
        elif _reference_audios:
            char_name = next(iter(_reference_audios.keys()))

    # 4. 如果仍未找到且当前只有单一角色，自动回退到该角色
    if not char_name and len(_reference_audios) == 1:
        char_name = next(iter(_reference_audios.keys()))
        logger.warning(f"Requested character '{raw_char}' not found. Falling back to '{char_name}'.")

    # 5. 如果仍然没有任何可用角色
    if not _reference_audios:
        raise HTTPException(
            status_code=400,
            detail="No character is loaded or reference audio set in server, and no valid preset found in UserData/GenieGuiConfig.json."
        )

    # 6. 如果指定的角色确实不存在
    if not char_name:
        raise HTTPException(
            status_code=404,
            detail=f"Character '{raw_char}' not found. Available characters: {list(_reference_audios.keys())}"
        )

    # 确保模型已按需加载
    loaded = ensure_character_loaded(char_name)
    if not loaded and model_manager.get(char_name) is None:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load ONNX model for character '{char_name}'."
        )

    loop = asyncio.get_running_loop()
    stream_queue: asyncio.Queue[Union[bytes, None]] = asyncio.Queue()

    def tts_chunk_callback(chunk: Optional[bytes]):
        loop.call_soon_threadsafe(stream_queue.put_nowait, chunk)

    loop.run_in_executor(
        None,
        run_tts_in_background,
        char_name,
        payload.text,
        payload.split_sentence,
        payload.save_path,
        tts_chunk_callback
    )

    return StreamingResponse(audio_stream_generator(stream_queue), media_type="audio/wav")


@app.post("/stop")
def stop_endpoint():
    try:
        tts_player.stop()
        return {"status": "success", "message": "TTS stopped."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/clear_reference_audio_cache")
def clear_reference_audio_cache_endpoint():
    try:
        ReferenceAudio.clear_cache()
        return {"status": "success", "message": "Reference audio cache cleared."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def start_server(host: str = "127.0.0.1", port: int = 8000, workers: int = 1):
    uvicorn.run(app, host=host, port=port, workers=workers)


# 模块导入时自动执行一次预设探测与发现
auto_load_presets_from_config()


if __name__ == "__main__":
    start_server(host="0.0.0.0", port=8000, workers=1)
