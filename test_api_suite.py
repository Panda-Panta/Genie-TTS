import sys
import os
import time
import requests
import multiprocessing
import uvicorn

os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8000
BASE_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"

def run_server():
    from genie_tts.Server import app
    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT, log_level="info")

def test_api():
    print("=" * 50)
    print("Starting API Server Endpoints Test")
    print("=" * 50)

    # 1. Test /load_character
    print("\n[Test 1] Testing /load_character...")
    load_payload = {
        "character_name": "mika",
        "onnx_model_dir": os.path.abspath("CharacterModels/v2ProPlus/mika/tts_models"),
        "language": "jp"
    }
    r = requests.post(f"{BASE_URL}/load_character", json=load_payload)
    print(f"Status: {r.status_code}, Body: {r.text}")
    assert r.status_code == 200, "Failed to load character"

    # 2. Test /set_reference_audio
    print("\n[Test 2] Testing /set_reference_audio...")
    ref_payload = {
        "character_name": "mika",
        "audio_path": os.path.abspath("CharacterModels/v2ProPlus/mika/prompt_wav/917575.wav"),
        "audio_text": "私も昔、これと似たようなの持ってたなぁ…。",
        "language": "jp"
    }
    r = requests.post(f"{BASE_URL}/set_reference_audio", json=ref_payload)
    print(f"Status: {r.status_code}, Body: {r.text}")
    assert r.status_code == 200, "Failed to set reference audio"

    # 3. Test /tts (Streaming response)
    print("\n[Test 3] Testing /tts streaming response...")
    tts_payload = {
        "character_name": "mika",
        "text": "こんにちは！APIテストは成功しました。",
        "split_sentence": False,
        "save_path": os.path.abspath("api_output.wav")
    }
    with requests.post(f"{BASE_URL}/tts", json=tts_payload, stream=True) as response:
        print(f"Status: {response.status_code}, Headers: {response.headers.get('content-type')}")
        assert response.status_code == 200, "Failed TTS request"
        total_bytes = 0
        with open("stream_received.wav", "wb") as f:
            for chunk in response.iter_content(chunk_size=4096):
                if chunk:
                    f.write(chunk)
                    total_bytes += len(chunk)
        print(f"Received {total_bytes} bytes of audio stream.")
        assert total_bytes > 0, "Audio stream was empty"

    time.sleep(2)
    if os.path.exists("api_output.wav"):
        print(f"Server-side saved file size: {os.path.getsize('api_output.wav')} bytes")

    # 4. Test /clear_reference_audio_cache
    print("\n[Test 4] Testing /clear_reference_audio_cache...")
    r = requests.post(f"{BASE_URL}/clear_reference_audio_cache")
    print(f"Status: {r.status_code}, Body: {r.text}")
    assert r.status_code == 200

    # 5. Test /stop
    print("\n[Test 5] Testing /stop...")
    r = requests.post(f"{BASE_URL}/stop")
    print(f"Status: {r.status_code}, Body: {r.text}")
    assert r.status_code == 200

    # 6. Test /unload_character
    print("\n[Test 6] Testing /unload_character...")
    r = requests.post(f"{BASE_URL}/unload_character", json={"character_name": "mika"})
    print(f"Status: {r.status_code}, Body: {r.text}")
    assert r.status_code == 200

    print("\n" + "=" * 50)
    print("ALL API ENDPOINTS TESTED SUCCESSFULLY!")
    print("=" * 50)

if __name__ == "__main__":
    p = multiprocessing.Process(target=run_server)
    p.start()
    try:
        # Wait for server ready
        for _ in range(15):
            time.sleep(1)
            try:
                r = requests.get(f"{BASE_URL}/docs")
                if r.status_code == 200:
                    break
            except Exception:
                pass
        test_api()
    finally:
        p.terminate()
        p.join()
