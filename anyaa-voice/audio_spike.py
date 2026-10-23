"""Phase 0 microphone -> Silero/Deepgram diagnostic; no LLM or TTS."""

import argparse
import asyncio
import os
import time
from pathlib import Path
from unittest.mock import patch

import aiohttp
import numpy as np
import sounddevice as sd
from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import stt, vad
from livekit.plugins import deepgram, silero


async def diagnose(device: int | None, seconds: int, language: str) -> bool:
    device = sd.default.device[0] if device is None else device
    info = sd.query_devices(device, "input")
    host = sd.query_hostapis(info["hostapi"])["name"]
    print(f"INPUT id={device} name={info['name']} host={host} rate=16000 mono int16", flush=True)
    sd.check_input_settings(device=device, channels=1, dtype="int16", samplerate=16000)
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()
    received = sent = speech_starts = 0
    wire_bytes = 0
    max_probability = 0.0
    transcripts = []

    def on_audio(indata, frames, timing, status):
        if status:
            print(f"MIC status={status}", flush=True)
        loop.call_soon_threadsafe(queue.put_nowait, indata.copy())

    original_send = aiohttp.ClientWebSocketResponse.send_bytes

    async def audit_send(socket, data, *args, **kwargs):
        nonlocal wire_bytes
        result = await original_send(socket, data, *args, **kwargs)
        wire_bytes += len(data)
        return result

    async with aiohttp.ClientSession() as http:
        recognizer = deepgram.STT(model="nova-3", language=language, http_session=http)
        detector = silero.VAD.load()
        speech = recognizer.stream()
        activity = detector.stream()

        async def read_speech():
            async for event in speech:
                if event.type in (stt.SpeechEventType.INTERIM_TRANSCRIPT, stt.SpeechEventType.FINAL_TRANSCRIPT):
                    text = event.alternatives[0].text if event.alternatives else ""
                    if text:
                        print(f"DEEPGRAM {event.type.value}: {text}", flush=True)
                        if event.type == stt.SpeechEventType.FINAL_TRANSCRIPT:
                            transcripts.append(text)

        async def read_activity():
            nonlocal max_probability, speech_starts
            async for event in activity:
                max_probability = max(max_probability, event.probability)
                if event.type == vad.VADEventType.START_OF_SPEECH:
                    speech_starts += 1
                    print("VAD start_of_speech", flush=True)
                elif event.type == vad.VADEventType.END_OF_SPEECH:
                    print("VAD end_of_speech", flush=True)

        tasks = [asyncio.create_task(read_speech()), asyncio.create_task(read_activity())]
        try:
            with patch.object(aiohttp.ClientWebSocketResponse, "send_bytes", audit_send):
                with sd.InputStream(device=device, channels=1, samplerate=16000,
                                    dtype="int16", blocksize=1600, callback=on_audio):
                    print(f"LISTENING ({seconds}s). Speak one short sentence now.", flush=True)
                    deadline = time.monotonic() + seconds
                    last_log = 0.0
                    while time.monotonic() < deadline:
                        try:
                            data = await asyncio.wait_for(queue.get(), timeout=3)
                        except TimeoutError:
                            print("FAIL: input stream opened but no microphone callbacks arrived in 3s.", flush=True)
                            break
                        received += 1
                        frame = rtc.AudioFrame(data.tobytes(), 16000, 1, len(data))
                        activity.push_frame(frame)
                        speech.push_frame(frame)
                        sent += 1
                        if time.monotonic() - last_log >= 1:
                            signal = data.astype(np.float64) / 32768
                            rms = float(np.sqrt(np.mean(signal * signal)))
                            peak = int(np.max(np.abs(data.astype(np.int32))))
                            dbfs = 20 * np.log10(max(rms, 1e-10))
                            print(f"MIC frames={received} peak={peak} rms_dbfs={dbfs:.1f} "
                                  f"VAD_max={max_probability:.3f} "
                                  f"Deepgram_pushed={sent} ws_audio_bytes={wire_bytes}", flush=True)
                            last_log = time.monotonic()
                        for task in tasks:
                            if task.done():
                                task.result()
                    speech.end_input()
                    activity.end_input()
                    try:
                        await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)
                    except TimeoutError:
                        pass
        finally:
            await speech.aclose()
            await activity.aclose()
            await recognizer.aclose()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    print(f"RESULT frames={received} wire_audio_bytes={wire_bytes} "
          f"VAD_speech_starts={speech_starts} final_transcripts={transcripts}", flush=True)
    return bool(transcripts)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", type=int)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--language", choices=["en", "kn"], default="en")
    args = parser.parse_args()
    load_dotenv(Path(__file__).with_name(".env"))
    if not os.getenv("DEEPGRAM_API_KEY", "").strip():
        raise SystemExit("Missing DEEPGRAM_API_KEY in .env")
    raise SystemExit(0 if asyncio.run(diagnose(args.device, args.seconds, args.language)) else 1)
