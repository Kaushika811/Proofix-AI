import { useEffect, useRef, useState, type RefObject } from "react";
import { X, Minus, ChevronUp } from "lucide-react";
import { CodexMessageRenderer } from "./CodexMessageRenderer";

interface Props {
  /** The latest user question. Null = no question asked yet; panel is hidden. */
  latestUserMsg: string | null;
  /** The latest AI answer. Null while the answerer is still in flight. */
  latestAIMsg: string | null;
  /** True while the answerer is in flight — shows loading dots for AI slot. */
  isAnswering: boolean;
  /**
   * The same anchorRef passed to ChatPanel. Its measured left/width drives
   * the strip's position so it always aligns to the content column —
   * sidebar collapsed or not, report panel open or not.
   */
  anchorRef?: RefObject<HTMLDivElement | null>;
}

/**
 * LatestChatExchange
 *
 * Displays the most recent user question + AI answer directly above
 * the existing ChatPanel in clean Codex layout and typography.
 * Constrained to viewport height with scrollable content so headers/controls
 * never go offscreen. Supports minimization and full dismissal.
 */
export function LatestChatExchange({
  latestUserMsg,
  latestAIMsg,
  isAnswering,
  anchorRef,
}: Props) {
  const [bottomOffset, setBottomOffset] = useState<number>(76);
  const [bounds, setBounds] = useState<{ left: number; width: number } | null>(null);
  const [isMinimized, setIsMinimized] = useState(false);
  const [isDismissed, setIsDismissed] = useState(false);
  const lastMsgRef = useRef<string | null>(null);

  // When a new message arrives or answering starts, automatically reset minimized & dismissed states
  useEffect(() => {
    if (latestUserMsg && latestUserMsg !== lastMsgRef.current) {
      lastMsgRef.current = latestUserMsg;
      setIsDismissed(false);
      setIsMinimized(false);
    }
  }, [latestUserMsg]);

  // Track the anchor column (left + width) — mirrors ChatPanel's effect.
  useEffect(() => {
    const anchor = anchorRef?.current;
    if (!anchor) return;

    const apply = () => {
      const r = anchor.getBoundingClientRect();
      setBounds({ left: r.left, width: r.width });
    };

    apply();
    const ro = new ResizeObserver(apply);
    ro.observe(anchor);
    window.addEventListener("resize", apply);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", apply);
    };
  }, [anchorRef]);

  // Track ChatPanel's actual height to stay directly above it with an 8px gap.
  useEffect(() => {
    const GAP = 8;

    const measurePanel = () => {
      const panelEl = document.querySelector<HTMLElement>('[data-chat-panel="true"]');
      if (panelEl) {
        const h = panelEl.getBoundingClientRect().height;
        if (h > 0) {
          setBottomOffset(h + GAP);
        }
      }
    };

    measurePanel();
    const interval = setInterval(measurePanel, 100);
    window.addEventListener("resize", measurePanel);

    const panelEl = document.querySelector<HTMLElement>('[data-chat-panel="true"]');
    let ro: ResizeObserver | null = null;
    if (panelEl) {
      ro = new ResizeObserver(measurePanel);
      ro.observe(panelEl);
    }

    return () => {
      clearInterval(interval);
      window.removeEventListener("resize", measurePanel);
      ro?.disconnect();
    };
  }, []);

  // Panel is only visible once the user has submitted at least one question and has not dismissed it.
  const visible = latestUserMsg !== null && !isDismissed;

  // Dismiss on Escape key
  useEffect(() => {
    if (!visible) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setIsDismissed(true);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [visible]);

  if (!visible) return null;

  const maxCardHeight = `calc(100vh - ${bottomOffset + 24}px)`;

  return (
    <div
      aria-live="polite"
      aria-atomic="true"
      className="pointer-events-none fixed z-30 px-4 transition-all duration-[200ms] sm:px-6"
      style={{
        bottom: `${bottomOffset}px`,
        ...(bounds ? { left: bounds.left, width: bounds.width } : { left: 0, right: 0 }),
      }}
    >
      <div className="pointer-events-auto mx-auto w-full max-w-2xl">
        {isMinimized ? (
          /* Minimized Compact Chip */
          <div className="flex items-center justify-between gap-3 rounded-full border border-white/10 bg-[#0d0f12]/95 px-4 py-2 text-white shadow-[0_12px_32px_-8px_rgba(0,0,0,0.6)] backdrop-blur font-sans antialiased">
            <button
              type="button"
              onClick={() => setIsMinimized(false)}
              className="flex min-w-0 flex-1 items-center gap-2 text-left transition hover:text-white"
              aria-label="Expand answer"
            >
              <span className="truncate text-[13px] font-medium text-gray-300">
                <span className="font-semibold text-white">Latest Q:</span> {latestUserMsg}
              </span>
            </button>
            <div className="flex shrink-0 items-center gap-1">
              <button
                type="button"
                onClick={() => setIsMinimized(false)}
                className="flex h-6 w-6 items-center justify-center rounded-full text-gray-400 transition hover:bg-white/10 hover:text-white cursor-pointer"
                aria-label="Expand answer"
                title="Expand"
              >
                <ChevronUp className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                onClick={() => setIsDismissed(true)}
                className="flex h-6 w-6 items-center justify-center rounded-full text-gray-400 transition hover:bg-white/10 hover:text-white cursor-pointer"
                aria-label="Close answer"
                title="Close"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
        ) : (
          /* Full Expanded Card with Constrained Height & Sticky Header */
          <div
            className="flex flex-col overflow-hidden rounded-[18px] border border-white/10 bg-[#0d0f12]/95 shadow-[0_16px_40px_-16px_rgba(0,0,0,0.8)] backdrop-blur font-sans antialiased"
            style={{ maxHeight: maxCardHeight }}
          >
            {/* Header: pinned at top of card so minimize and close are ALWAYS reachable */}
            <div className="flex shrink-0 items-center justify-between border-b border-white/10 bg-[#0d0f12] px-4 py-2 text-[12px] font-medium text-gray-400">
              <div className="flex items-center gap-2">
                <span className="h-1.5 w-1.5 rounded-full bg-[#3b82f6]" />
                <span className="text-[12px] font-medium text-gray-300">Answer</span>
              </div>
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => setIsMinimized(true)}
                  className="flex h-6 w-6 items-center justify-center rounded-md text-gray-400 transition hover:bg-white/10 hover:text-white cursor-pointer"
                  aria-label="Minimize answer"
                  title="Minimize"
                >
                  <Minus className="h-3.5 w-3.5" strokeWidth={2} />
                </button>
                <button
                  type="button"
                  onClick={() => setIsDismissed(true)}
                  className="flex h-6 w-6 items-center justify-center rounded-md text-gray-400 transition hover:bg-white/10 hover:text-white cursor-pointer"
                  aria-label="Close answer"
                  title="Close"
                >
                  <X className="h-3.5 w-3.5" strokeWidth={2} />
                </button>
              </div>
            </div>

            {/* Scrollable Content Body */}
            <div className="flex-1 overflow-y-auto p-4 space-y-3.5 overscroll-contain">
              {/* User message (Right-aligned pill) */}
              {latestUserMsg && (
                <div className="flex justify-end">
                  <div className="max-w-[85%] rounded-2xl bg-[#1e222b] border border-white/5 px-3.5 py-2 text-[14px] font-normal leading-relaxed text-white shadow-sm font-sans">
                    {latestUserMsg}
                  </div>
                </div>
              )}

              {/* AI answer */}
              <div className="flex justify-start">
                <div className="w-full text-[14px] leading-relaxed text-white font-sans">
                  {isAnswering ? (
                    <span className="inline-flex items-center gap-1.5 py-0.5 text-gray-400">
                      <span className="h-1.5 w-1.5 animate-soft-pulse rounded-full bg-[#3b82f6]" />
                      <span
                        className="h-1.5 w-1.5 animate-soft-pulse rounded-full bg-[#3b82f6]"
                        style={{ animationDelay: "150ms" }}
                      />
                      <span
                        className="h-1.5 w-1.5 animate-soft-pulse rounded-full bg-[#3b82f6]"
                        style={{ animationDelay: "300ms" }}
                      />
                    </span>
                  ) : latestAIMsg ? (
                    <CodexMessageRenderer content={latestAIMsg} />
                  ) : null}
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
