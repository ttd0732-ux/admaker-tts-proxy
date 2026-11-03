"""Free TTS proxy for the ad-maker app (Vercel serverless function).

The app cannot reach Microsoft Edge TTS directly from a browser
(the browser connection is blocked), so this small server asks Edge TTS
for the audio and hands the MP3 back to the app. No API key, no paid
service: it uses the same free Edge voices the app already uses.

GET /api/tts?voice=ko-KR-SunHiNeural&text=...&rate=0&pitch=1
  voice: one of the allow-listed Korean Edge voices below
  text:  one sentence, up to 1000 characters
  rate:  -50 .. +50 (percent, from the app speed slider; 0 = normal)
  pitch: 0.5 .. 2.0 (multiplier, from the app tone slider; 1 = normal)

Response: audio/mpeg with Access-Control-Allow-Origin: * so the app
(in a browser, on any origin) can fetch and decode it.
"""

import asyncio
import json
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

ALLOWED_VOICES = {
    "ko-KR-SunHiNeural",
    "ko-KR-JiMinNeural",
    "ko-KR-SeoHyeonNeural",
    "ko-KR-YuJinNeural",
    "ko-KR-SoonBokNeural",
    "ko-KR-InJoonNeural",
    "ko-KR-HyunjunNeural",
    "ko-KR-BongJinNeural",
    "ko-KR-HyunsuMultilingualNeural",
}


async def collect_audio(text, voice, rate_str, pitch_str):
    from edge_tts import Communicate

    audio = bytearray()
    communicate = Communicate(
        text, voice, rate=rate_str, pitch=pitch_str,
        connect_timeout=8, receive_timeout=25,
    )
    async for chunk in communicate.stream():
        if chunk.get("type") == "audio" and chunk.get("data"):
            audio.extend(chunk["data"])
    if not audio:
        raise RuntimeError("no audio received from Edge TTS")
    return bytes(audio)


class handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, status, body, content_type):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        try:
            query = parse_qs(urlparse(self.path).query)
            voice = (query.get("voice", [""])[0] or "").strip()
            text = (query.get("text", [""])[0] or "").strip()
            if voice not in ALLOWED_VOICES:
                self._send(400, "unknown voice", "text/plain; charset=utf-8")
                return
            if not text or len(text) > 1000:
                self._send(400, "text must be 1..1000 characters", "text/plain; charset=utf-8")
                return
            try:
                rate_pct = int(float(query.get("rate", ["0"])[0] or 0))
            except (TypeError, ValueError):
                rate_pct = 0
            rate_pct = max(-50, min(50, rate_pct))
            rate_str = ("+" if rate_pct >= 0 else "") + str(rate_pct) + "%"
            try:
                pitch_val = float(query.get("pitch", ["1"])[0] or 1)
            except (TypeError, ValueError):
                pitch_val = 1.0
            pitch_val = max(0.5, min(2.0, pitch_val))
            pitch_hz = max(-100, min(100, int(round((pitch_val - 1) * 100))))
            pitch_str = ("+" if pitch_hz >= 0 else "") + str(pitch_hz) + "Hz"
            audio = asyncio.run(collect_audio(text, voice, rate_str, pitch_str))
            self._send(200, audio, "audio/mpeg")
        except Exception as exc:  # Edge failure, timeout, etc.
            self._send(502, "tts failed: " + str(exc)[:200], "text/plain; charset=utf-8")
