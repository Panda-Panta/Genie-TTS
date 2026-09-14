import asyncio
import os
import struct
from typing import AsyncIterator, Optional, Callable, Union, Dict
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
SUPPORTED_AUDIO_EXTS = {'.wav', '.flac', '.ogg', '.aiff', '.aif'}

def set_server_reference_audio(character_name: str, audio_path: str, audio_text: str, language: str):
    _reference_audios[character_name] = {
        'audio_path': audio_path,
        'audio_text': audio_text,
        'language': normalize_language(language),
    }

def get_server_reference_audios() -> Dict[str, dict]:
    return _reference_audios

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
    _reference_audios[payload.character_name] = {
        'audio_path': payload.audio_path,
        'audio_text': payload.audio_text,
        'language': normalize_language(payload.language),
    }
    return {"status": "success", "message": f"Reference audio for '{payload.character_name}' set."}


def run_tts_in_background(
        character_name: str,
        text: str,
        split_sentence: bool,
        save_path: Optional[str],
        chunk_callback: Callable[[Optional[bytes]], None]
):
    try:
        context.current_speaker = character_name
        context.current_prompt_audio = ReferenceAudio(
            prompt_wav=_reference_audios[character_name]['audio_path'],
            prompt_text=_reference_audios[character_name]['audio_text'],
            language=_reference_audios[character_name]['language'],
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
    return list(_reference_audios.keys())


@app.post("/tts")
async def tts_endpoint(payload: TTSPayload):
    char_name = payload.character_name.strip() if payload.character_name else ""
    if not char_name or char_name not in _reference_audios:
        if len(_reference_audios) == 1:
            char_name = next(iter(_reference_audios.keys()))
        elif len(_reference_audios) > 1 and char_name in ("", "Default", "default"):
            char_name = next(iter(_reference_audios.keys()))
        elif not _reference_audios:
            raise HTTPException(status_code=400, detail="No character is loaded or reference audio set in server.")
        else:
            raise HTTPException(
                status_code=404,
                detail=f"Character '{char_name}' not found. Available characters: {list(_reference_audios.keys())}"
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


if __name__ == "__main__":
    start_server(host="0.0.0.0", port=8000, workers=1)
