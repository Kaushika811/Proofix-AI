import { useState, useRef, useEffect, useCallback, type RefObject } from "react";
import { ArrowUp, AudioLines, X } from "lucide-react";
import { MOCK_CHAT_SUGGESTIONS, mockAnswerer } from "@/mocks";
import { DATA_SOURCE } from "@/lib/api";
import { transcribeAudio } from "@/lib/speechService";

const isLive = DATA_SOURCE === "api";

/**
 * Suggestion chips for a real run.
 */
const LIVE_CHAT_SUGGESTIONS = [
  "What did the agents find?",
  "Show the root cause",
  "Which files changed?",
  "How was the fix validated?",
];

type Mode = "idle" | "hover";

/** Internal voice-recording lifecycle. */
type VoiceState = "idle" | "recording" | "transcribing";

/** Maximum recording duration before we auto-stop (Sarvam REST limit: 30 s). */
const MAX_RECORDING_MS = 29_000;

/** Waveform bar count and sizing. */
const NUM_BARS = 48;
const MIN_BAR_H = 4;
const MAX_BAR_H = 44;

/** Prefer webm/opus; fall back to any supported type. */
function preferredMimeType(): string {
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/ogg;codecs=opus",
    "audio/ogg",
    "audio/mp4",
    "",
  ];
  for (const type of candidates) {
    if (!type || MediaRecorder.isTypeSupported(type)) return type;
  }
  return "";
}

/** Formats elapsed seconds as "0:05", "0:29", etc. */
function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export function ChatPanel({
  suggestions = isLive ? LIVE_CHAT_SUGGESTIONS : MOCK_CHAT_SUGGESTIONS,
  answerer = mockAnswerer,
  anchorRef,
}: {
  /** Suggestion chips. Override per-run from the backend if desired. */
  suggestions?: string[];
  /** Resolver for user questions. Wire to `runService.askChat(runId, q)` once the backend is live. */
  answerer?: (q: string) => string | Promise<string>;
  /**
   * The content column this bar should track. Its measured viewport rect
   * (left + width) drives the fixed bar's position, so the composer stays
   * aligned to the real content column — sidebar collapsed or not, report
   * panel open or not — instead of guessing pixel offsets per breakpoint.
   */
  anchorRef?: RefObject<HTMLDivElement | null>;
} = {}) {
  const [input, setInput] = useState("");
  const [mode, setMode] = useState<Mode>("idle");
  const [bounds, setBounds] = useState<{ left: number; width: number } | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // ── Voice state ──────────────────────────────────────────────────────
  const [voiceState, setVoiceState] = useState<VoiceState>("idle");
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const autoStopTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── Recording duration ──────────────────────────────────────────────
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const durationIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── Audio analysis for real-time waveform ───────────────────────────
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const animFrameRef = useRef<number>(0);
  const barsContainerRef = useRef<HTMLDivElement>(null);

  // ── Recording duration timer ────────────────────────────────────────
  useEffect(() => {
    if (voiceState === "recording") {
      setRecordingSeconds(0);
      durationIntervalRef.current = setInterval(() => {
        setRecordingSeconds((prev) => prev + 1);
      }, 1000);
    } else {
      if (durationIntervalRef.current !== null) {
        clearInterval(durationIntervalRef.current);
        durationIntervalRef.current = null;
      }
    }
    return () => {
      if (durationIntervalRef.current !== null) {
        clearInterval(durationIntervalRef.current);
        durationIntervalRef.current = null;
      }
    };
  }, [voiceState]);

  // ── Waveform animation loop (direct DOM updates at 60 fps) ──────────
  useEffect(() => {
    if (voiceState !== "recording" || !analyserRef.current) {
      cancelAnimationFrame(animFrameRef.current);
      return;
    }

    const analyser = analyserRef.current;
    const dataArray = new Uint8Array(analyser.frequencyBinCount);
    const binStep = Math.max(1, Math.floor(dataArray.length / NUM_BARS));

    const animate = () => {
      analyser.getByteFrequencyData(dataArray);
      const container = barsContainerRef.current;
      if (container) {
        const children = container.children;
        for (let i = 0; i < children.length && i < NUM_BARS; i++) {
          const el = children[i] as HTMLElement;
          const idx = Math.min(i * binStep, dataArray.length - 1);
          const val = dataArray[idx] / 255;
          const h = Math.max(MIN_BAR_H, val * MAX_BAR_H);
          el.style.height = `${h}px`;
        }
      }
      animFrameRef.current = requestAnimationFrame(animate);
    };
    animFrameRef.current = requestAnimationFrame(animate);

    return () => cancelAnimationFrame(animFrameRef.current);
  }, [voiceState]);

  // ── Auto-dismiss voice errors after 5 s ─────────────────────────────
  useEffect(() => {
    if (!voiceError) return;
    const t = setTimeout(() => setVoiceError(null), 5000);
    return () => clearTimeout(t);
  }, [voiceError]);

  // ── Anchor tracking (unchanged) ──────────────────────────────────────
  useEffect(() => {
    const el = anchorRef?.current;
    if (!el) return;
    const measure = () => {
      const r = el.getBoundingClientRect();
      setBounds({ left: r.left, width: r.width });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    window.addEventListener("resize", measure);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [anchorRef]);

  // ── Cleanup audio analysis resources ────────────────────────────────
  const cleanupAudio = useCallback(() => {
    cancelAnimationFrame(animFrameRef.current);
    if (audioContextRef.current) {
      audioContextRef.current.close().catch(() => {});
      audioContextRef.current = null;
    }
    analyserRef.current = null;
  }, []);

  // ── Stop mic tracks and clear refs ──────────────────────────────────
  const stopMicrophone = useCallback(() => {
    if (autoStopTimerRef.current !== null) {
      clearTimeout(autoStopTimerRef.current);
      autoStopTimerRef.current = null;
    }
    mediaStreamRef.current?.getTracks().forEach((t) => t.stop());
    mediaStreamRef.current = null;
    mediaRecorderRef.current = null;
    // NOTE: audioChunksRef is NOT cleared here — onstop reads it after this runs.
    // Chunks are cleared at the start of each new recording session instead.
  }, []);

  // ── Cleanup on unmount ───────────────────────────────────────────────
  useEffect(() => {
    return () => {
      stopMicrophone();
      cleanupAudio();
    };
  }, [stopMicrophone, cleanupAudio]);

  // ── Existing send (unchanged logic, stable ref via useCallback) ──────
  const send = useCallback(
    async (text: string) => {
      const q = text.trim();
      if (!q) return;
      setInput("");
      setMode("idle");
      await Promise.resolve(answerer(q));
    },
    [answerer],
  );

  // ── Submit transcript through the existing send() path ───────────────
  const submitTranscript = useCallback(
    async (blob: Blob) => {
      setVoiceState("transcribing");
      setVoiceError(null);
      try {
        const transcript = await transcribeAudio(blob);
        // Feed the transcript into the existing send() — identical to typing.
        await send(transcript);
      } catch (err) {
        const msg = err instanceof Error ? err.message : "Voice input failed — try again";
        setVoiceError(msg);
        console.error("[ChatPanel] STT error:", err);
      } finally {
        setVoiceState("idle");
        audioChunksRef.current = [];
      }
    },
    [send],
  );

  // ── Cancel recording without transcribing ───────────────────────────
  const cancelRecording = useCallback(() => {
    const recorder = mediaRecorderRef.current;
    if (recorder) {
      recorder.onstop = null; // Prevent transcription
      if (recorder.state !== "inactive") recorder.stop();
    }
    stopMicrophone();
    cleanupAudio();
    audioChunksRef.current = [];
    setVoiceState("idle");
  }, [stopMicrophone, cleanupAudio]);

  // ── Stop recording → transcribe ─────────────────────────────────────
  const stopRecording = useCallback(() => {
    const recorder = mediaRecorderRef.current;
    if (recorder && recorder.state !== "inactive") {
      recorder.stop(); // onstop fires, collects chunks, calls submitTranscript
    }
    cleanupAudio();
  }, [cleanupAudio]);

  // ── Mic button handler (starts recording) ───────────────────────────
  const handleMicClick = useCallback(async () => {
    if (voiceState === "transcribing") return;

    // Safety: if somehow still recording, stop it
    if (voiceState === "recording") {
      stopRecording();
      return;
    }

    setVoiceError(null);

    // Browser support guard
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setVoiceError("Your browser does not support voice input.");
      return;
    }

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      const msg =
        err instanceof Error && err.name === "NotAllowedError"
          ? "Microphone permission denied."
          : "Could not access the microphone.";
      setVoiceError(msg);
      console.error("[ChatPanel] Mic permission error:", err);
      return;
    }

    mediaStreamRef.current = stream;
    audioChunksRef.current = [];

    // Set up Web Audio analysis for waveform (requires user-gesture context)
    try {
      const audioCtx = new AudioContext();
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 256;
      analyser.smoothingTimeConstant = 0.75;
      const source = audioCtx.createMediaStreamSource(stream);
      source.connect(analyser);
      audioContextRef.current = audioCtx;
      analyserRef.current = analyser;
    } catch {
      // Audio analysis is optional — recording still works without visualization
      console.warn("[ChatPanel] Audio analysis unavailable — waveform disabled");
    }

    const mimeType = preferredMimeType();
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    mediaRecorderRef.current = recorder;

    recorder.ondataavailable = (e) => {
      if (e.data.size > 0) audioChunksRef.current.push(e.data);
    };

    recorder.onstop = () => {
      const chunks = [...audioChunksRef.current];
      stopMicrophone();
      cleanupAudio();
      const blob = new Blob(chunks, { type: mimeType || "audio/webm" });
      if (blob.size === 0) {
        setVoiceError("No audio was recorded. Please try again.");
        setVoiceState("idle");
        return;
      }
      void submitTranscript(blob);
    };

    recorder.start();
    setVoiceState("recording");

    // Auto-stop at MAX_RECORDING_MS to stay within Sarvam's REST limit
    autoStopTimerRef.current = setTimeout(() => {
      if (mediaRecorderRef.current?.state !== "inactive") {
        mediaRecorderRef.current?.stop();
      }
    }, MAX_RECORDING_MS);
  }, [voiceState, stopMicrophone, cleanupAudio, submitTranscript, stopRecording]);

  const expanded = mode === "hover";

  return (
    <div
      data-chat-panel="true"
      className={`pointer-events-none fixed bottom-0 z-30 px-4 pb-4 sm:px-6 ${
        bounds ? "" : "left-0 right-0"
      }`}
      style={bounds ? { left: bounds.left, width: bounds.width } : undefined}
    >
      <section
        onMouseEnter={() => setMode("hover")}
        onMouseLeave={() => setMode("idle")}
        className="pointer-events-auto mx-auto w-full max-w-2xl overflow-hidden rounded-[18px] border border-border bg-surface/95 backdrop-blur shadow-[0_16px_40px_-16px_rgba(15,23,42,0.28)] transition-all duration-[250ms]"
      >
        {voiceState === "recording" ? (
          /* ─────────────────────────────────────────────────────────
           * RECORDING MODE — full panel takeover (ChatGPT style)
           * ───────────────────────────────────────────────────────── */
          <div className="px-5 py-5">
            {/* Real-time audio waveform */}
            <div
              ref={barsContainerRef}
              className="flex items-center justify-center gap-[2px] h-14 mb-4"
            >
              {Array.from({ length: NUM_BARS }, (_, i) => {
                const center = NUM_BARS / 2;
                const dist = Math.abs(i - center) / center;
                const opacity = 1 - dist * 0.45;
                return (
                  <div
                    key={i}
                    className="w-[3px] rounded-full bg-primary"
                    style={{
                      height: `${MIN_BAR_H}px`,
                      opacity,
                      transition: "height 80ms ease-out",
                    }}
                  />
                );
              })}
            </div>

            {/* Controls: Cancel — Timer — Stop */}
            <div className="flex items-center justify-between px-1">
              {/* Cancel */}
              <button
                type="button"
                onClick={cancelRecording}
                className="flex h-9 w-9 items-center justify-center rounded-full text-ink-soft hover:text-ink hover:bg-surface-muted/80 transition-colors"
                aria-label="Cancel recording"
              >
                <X className="h-4.5 w-4.5" strokeWidth={2} />
              </button>

              {/* Timer */}
              <span className="text-[14px] font-mono text-ink-soft tabular-nums tracking-wide">
                {formatDuration(recordingSeconds)}
              </span>

              {/* Stop (red circle with white square, like ChatGPT) */}
              <button
                type="button"
                onClick={stopRecording}
                className="flex h-11 w-11 items-center justify-center rounded-full bg-red-500 text-white shadow-lg shadow-red-500/25 hover:bg-red-600 active:scale-95 transition-all"
                aria-label="Stop recording and send"
              >
                <div className="h-4 w-4 rounded-[3px] bg-white" />
              </button>
            </div>
          </div>
        ) : voiceState === "transcribing" ? (
          /* ─────────────────────────────────────────────────────────
           * TRANSCRIBING MODE — centered spinner
           * ───────────────────────────────────────────────────────── */
          <div className="flex flex-col items-center justify-center gap-3 px-4 py-8">
            <div className="h-10 w-10 rounded-full border-2 border-primary/20 border-t-primary animate-spin" />
            <p className="text-[13px] text-ink-soft">Transcribing your message…</p>
          </div>
        ) : (
          /* ─────────────────────────────────────────────────────────
           * NORMAL MODE — suggestion chips + prompt bar
           * ───────────────────────────────────────────────────────── */
          <>
            {/* Expanded content (hover: initial greeting & suggestion chips) */}
            <div
              className={`grid transition-all duration-[250ms] ease-out ${
                expanded ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"
              }`}
            >
              <div className="min-h-0 overflow-hidden">
                <div className="px-4 pt-3 pb-2">
                  <p className="mb-2 text-[13px] text-ink-soft">
                    I'm reading the current evidence for this run. Ask me anything about what the
                    agents found.
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {suggestions.map((s) => (
                      <button
                        key={s}
                        type="button"
                        onMouseDown={(e) => e.preventDefault()}
                        onClick={() => void send(s)}
                        className="rounded-full border border-border bg-surface px-2.5 py-1 text-[12px] text-ink-soft transition hover:border-primary/30 hover:text-ink"
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            </div>

            {/* Voice error banner */}
            {voiceError && (
              <div className="flex items-center justify-between gap-2 px-4 py-1.5 text-[12px]">
                <span className="text-red-400">{voiceError}</span>
                <button
                  type="button"
                  onClick={() => setVoiceError(null)}
                  className="text-ink-soft hover:text-ink text-[11px] shrink-0"
                >
                  Dismiss
                </button>
              </div>
            )}

            {/* Prompt bar */}
            <div className="flex items-end gap-1.5 px-2 py-2">
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  void send(input);
                }}
                className="flex min-h-[36px] flex-1 items-center gap-1.5 rounded-full bg-surface-muted/60 pl-3.5 pr-1 transition"
                onClick={() => {
                  inputRef.current?.focus();
                }}
              >
                <input
                  ref={inputRef}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  placeholder="Ask about this run..."
                  className="min-w-0 flex-1 bg-transparent text-[13px] text-ink placeholder:text-ink-soft focus:outline-none"
                />
                {input.trim() ? (
                  <button
                    type="submit"
                    className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary text-white transition hover:brightness-110"
                    aria-label="Send"
                  >
                    <ArrowUp className="h-3.5 w-3.5 text-white" strokeWidth={2.25} />
                  </button>
                ) : (
                  <button
                    type="button"
                    title="Voice input"
                    aria-label="Voice input"
                    onClick={(e) => {
                      e.stopPropagation();
                      void handleMicClick();
                    }}
                    className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary text-white transition hover:brightness-110"
                  >
                    <AudioLines className="h-3.5 w-3.5 text-white" strokeWidth={2.25} />
                  </button>
                )}
              </form>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
