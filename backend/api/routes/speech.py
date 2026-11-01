"""Speech-to-Text endpoint powered by Sarvam AI.

Accepts a multipart/form-data upload of an audio file, forwards it to the
Sarvam Saaras v3 STT model, and returns the plain transcript.

The Sarvam API key is read from the `SARVAM_API_KEY` environment variable
via ``backend.config.Settings``.  It is NEVER forwarded to the frontend.
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel

from backend.api.deps import get_settings_dep
from backend.config import Settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["speech"])

_SARVAM_STT_URL = "https://api.sarvam.ai/speech-to-text"
_SARVAM_MODEL = "saaras:v3"
_SARVAM_MODE = "transcribe"

# Sarvam's synchronous STT endpoint supports recordings up to ~30 s.
# We add 10 s of network headroom.
_HTTP_TIMEOUT = 40.0


class TranscriptResponse(BaseModel):
    transcript: str
    language_code: str | None = None


@router.post("/speech-to-text", response_model=TranscriptResponse)
async def speech_to_text(
    audio: UploadFile,
    settings: Settings = Depends(get_settings_dep),
) -> TranscriptResponse:
    """Transcribe an uploaded audio file using Sarvam AI Saaras v3.

    Request (multipart/form-data):
        audio: audio file (webm, wav, mp3, ogg, flac, aac, mp4, …)

    Response:
        { "transcript": "Which files changed?", "language_code": "en-IN" }
    """
    # ── 1. Guard: API key must be configured ─────────────────────────────
    if not settings.sarvam_api_key:
        logger.error("sarvam_api_key is not configured")
        raise HTTPException(
            status_code=503,
            detail="Speech transcription is not configured on this server.",
        )

    # ── 2. Guard: file must be present and non-empty ──────────────────────
    if audio is None:
        raise HTTPException(status_code=400, detail="No audio file provided.")

    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Audio file is empty.")

    filename = audio.filename or "recording.webm"
    content_type = audio.content_type or "audio/webm"

    # ── 3. Forward to Sarvam ──────────────────────────────────────────────
    # Authentication: Sarvam uses api-subscription-key header (NOT Bearer).
    # The multipart field for the file must be named "file" (Sarvam contract).
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            response = await client.post(
                _SARVAM_STT_URL,
                headers={"api-subscription-key": settings.sarvam_api_key},
                files={
                    "file": (filename, audio_bytes, content_type),
                },
                data={
                    "model": _SARVAM_MODEL,
                    "mode": _SARVAM_MODE,
                },
            )
    except httpx.TimeoutException:
        logger.exception("sarvam_stt_timeout")
        raise HTTPException(
            status_code=504,
            detail="Speech transcription timed out. Please try again.",
        )
    except httpx.RequestError as exc:
        logger.exception("sarvam_stt_network_error: %s", exc)
        raise HTTPException(
            status_code=502,
            detail="Could not reach the speech transcription service.",
        )

    # ── 4. Map Sarvam error codes to useful client errors ─────────────────
    if response.status_code == 401 or response.status_code == 403:
        logger.error("sarvam_stt_auth_error status=%d", response.status_code)
        raise HTTPException(
            status_code=503,
            detail="Speech transcription is not available. (Auth error.)",
        )
    if response.status_code == 429:
        logger.warning("sarvam_stt_rate_limited")
        raise HTTPException(
            status_code=429,
            detail="Speech transcription rate limit reached. Please wait and try again.",
        )
    if response.status_code == 422:
        logger.warning("sarvam_stt_unprocessable status=%d body=%s", response.status_code, response.text[:200])
        raise HTTPException(
            status_code=422,
            detail="The audio file could not be processed. Check the format and try again.",
        )
    if not response.is_success:
        logger.error(
            "sarvam_stt_error status=%d body=%s",
            response.status_code,
            response.text[:200],
        )
        raise HTTPException(
            status_code=502,
            detail="Speech transcription failed.",
        )

    # ── 5. Parse Sarvam response ──────────────────────────────────────────
    try:
        payload = response.json()
    except Exception:
        logger.exception("sarvam_stt_invalid_json body=%s", response.text[:200])
        raise HTTPException(
            status_code=502,
            detail="Received an unexpected response from the transcription service.",
        )

    transcript: str = payload.get("transcript", "").strip()
    language_code: str | None = payload.get("language_code")

    if not transcript:
        logger.warning("sarvam_stt_empty_transcript payload=%s", payload)
        raise HTTPException(
            status_code=422,
            detail="No speech was detected. Please try again.",
        )

    logger.info(
        "sarvam_stt_success chars=%d language=%s",
        len(transcript),
        language_code or "unknown",
    )
    return TranscriptResponse(transcript=transcript, language_code=language_code)
