/**
 * speechService.ts
 *
 * Thin client for the backend speech-to-text endpoint.
 * The frontend never contacts Sarvam AI directly — all STT traffic is
 * proxied through our own FastAPI backend so the Sarvam API key stays
 * server-side.
 *
 * Usage:
 *   import { transcribeAudio } from "@/lib/speechService";
 *   const text = await transcribeAudio(blob);
 */

import { API_BASE_URL, ENDPOINTS } from "./api";

export interface TranscriptResult {
  transcript: string;
  language_code?: string;
}

/**
 * POST the recorded audio Blob to /api/v1/speech-to-text and return the
 * transcript string.
 *
 * Uses raw `fetch` with FormData so the browser can auto-generate the
 * multipart boundary. Do NOT set Content-Type manually; let the browser
 * construct the correct `multipart/form-data; boundary=...` header.
 *
 * @param blob    - Recorded audio (webm, wav, mp3, etc.)
 * @param filename - Hint for the server (default: "recording.webm")
 * @throws Error with a user-friendly message on any failure.
 */
export async function transcribeAudio(
  blob: Blob,
  filename = "recording.webm",
): Promise<string> {
  if (blob.size === 0) {
    throw new Error("No audio was recorded. Please try again.");
  }

  const formData = new FormData();
  // Field name "audio" matches the FastAPI UploadFile parameter name.
  formData.append("audio", blob, filename);

  const url = `${API_BASE_URL}${ENDPOINTS.speechToText()}`;

  let response: Response;
  try {
    // Do NOT pass Content-Type — let the browser set the multipart boundary.
    response = await fetch(url, {
      method: "POST",
      body: formData,
    });
  } catch (networkErr) {
    throw new Error("Could not reach the server. Check your connection and try again.");
  }

  if (!response.ok) {
    let detail = "Speech transcription failed.";
    try {
      const body = await response.json();
      // FastAPI returns { detail: string } or { detail: [{msg, ...}] }
      if (typeof body?.detail === "string" && body.detail) {
        detail = body.detail;
      } else if (Array.isArray(body?.detail) && body.detail.length > 0) {
        // FastAPI validation errors: [{ msg: "...", type: "...", loc: [...] }]
        detail = body.detail.map((e: { msg?: string }) => e.msg ?? "").join("; ") || detail;
      } else if (typeof body?.message === "string" && body.message) {
        detail = body.message;
      }
    } catch {
      // Response body was not JSON — use the default message
    }
    console.error(`[speechService] STT error: HTTP ${response.status} — ${detail}`);
    throw new Error(detail);
  }

  const result = (await response.json()) as TranscriptResult;
  const transcript = result.transcript?.trim() ?? "";
  if (!transcript) {
    throw new Error("No speech was detected. Please try again.");
  }
  return transcript;
}
