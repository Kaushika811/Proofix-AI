import { useEffect, useRef, useState } from "react";
import { X, Plus, ArrowLeft, ArrowUp } from "lucide-react";
import { type Conversation, type ChatMessage, formatRelativeTime } from "./useChatConversations";
import { CodexMessageRenderer } from "./CodexMessageRenderer";
import { DATA_SOURCE } from "@/lib/api";
import { MOCK_CHAT_SUGGESTIONS } from "@/mocks";

const isLive = DATA_SOURCE === "api";

const LIVE_CHAT_SUGGESTIONS = [
  "What did the agents find?",
  "Show the root cause",
  "Which files changed?",
  "How was the fix validated?",
];

interface Props {
  open: boolean;
  onClose: () => void;
  conversations: Conversation[];
  activeConversationId: string | null;
  onSelectConversation: (id: string) => void;
  onNewChat: () => void;
  onSendMessage?: (question: string) => Promise<string> | void;
  isAnswering?: boolean;
  suggestions?: string[];
  /** When true, renders inline in the workspace side column without a fullscreen backdrop */
  embedded?: boolean;
}

/**
 * ChatHistoryDrawer
 *
 * Side chat panel formatted to match the original Codex UI typography and layout:
 *  - Font: Clean neo-grotesque sans-serif with 14px size, leading 1.65, and subpixel antialiasing
 *  - List View: Clean conversation rows with titles and relative timestamps (3d, 1w, 4w)
 *  - Detail View: Clean header without extra symbols, speech pills for user questions,
 *    rich Codex formatted AI answers, quick suggestion chips, and sleek pill input composer
 */
export function ChatHistoryDrawer({
  open,
  onClose,
  conversations,
  activeConversationId,
  onSelectConversation,
  onNewChat,
  onSendMessage,
  isAnswering = false,
  suggestions = isLive ? LIVE_CHAT_SUGGESTIONS : MOCK_CHAT_SUGGESTIONS,
  embedded = true,
}: Props) {
  const drawerRef = useRef<HTMLDivElement>(null);
  const threadScrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Tracks which conversation is open in detail view inside the drawer.
  // null = Conversation List View
  const [viewingId, setViewingId] = useState<string | null>(activeConversationId);
  const [input, setInput] = useState("");

  const prevActiveIdRef = useRef<string | null>(activeConversationId);

  const viewingConv = viewingId ? (conversations.find((c) => c.id === viewingId) ?? null) : null;

  // When a new conversation is created or externally selected, switch viewingId to it
  useEffect(() => {
    if (activeConversationId && activeConversationId !== prevActiveIdRef.current) {
      setViewingId(activeConversationId);
    }
    prevActiveIdRef.current = activeConversationId;
  }, [activeConversationId]);

  // Auto-scroll thread view to bottom when opened or when new messages arrive or while answering
  useEffect(() => {
    if (open) {
      requestAnimationFrame(() => {
        const el = threadScrollRef.current;
        if (el) el.scrollTop = el.scrollHeight;
      });
    }
  }, [viewingConv?.messages.length, isAnswering, viewingId, open]);

  // Focus input when detail view opens
  useEffect(() => {
    if (open && (viewingConv || conversations.length === 0)) {
      requestAnimationFrame(() => {
        inputRef.current?.focus();
      });
    }
  }, [open, viewingConv, conversations.length]);

  // Close on Escape key
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose]);

  // Focus trap / restore
  useEffect(() => {
    if (!open) return;
    const prev = document.activeElement as HTMLElement | null;
    drawerRef.current?.focus();
    return () => {
      prev?.focus();
    };
  }, [open]);

  const handleStartNewChat = () => {
    onNewChat();
    setViewingId(null);
    setInput("");
  };

  const handleSelect = (convId: string) => {
    onSelectConversation(convId);
    setViewingId(convId);
  };

  const handleSend = async (textToSend: string) => {
    const q = textToSend.trim();
    if (!q || isAnswering || !onSendMessage) return;
    setInput("");

    // If currently viewing a specific conversation, ensure it's selected as active
    if (viewingId && viewingId !== activeConversationId) {
      onSelectConversation(viewingId);
    }

    try {
      await onSendMessage(q);
    } catch {
      // Handled in parent state
    }
  };

  if (!open && embedded) return null;

  const content = (
    <>
      {/* ── 1. LIST VIEW (Matching Reference Image 2) ───────────── */}
      {!viewingConv ? (
        <>
          {/* Header */}
          <div className="flex items-center justify-between border-b border-white/10 px-4 py-3.5 bg-[#0d0f12]">
            <span className="text-[14.5px] font-medium text-white tracking-normal">
              Chats
            </span>
            <div className="flex items-center gap-1.5">
              <button
                type="button"
                onClick={handleStartNewChat}
                className="flex h-7 w-7 items-center justify-center rounded-lg text-gray-400 transition hover:bg-white/10 hover:text-white"
                title="New chat"
                aria-label="New chat"
              >
                <Plus className="h-4 w-4" strokeWidth={2} />
              </button>
              <button
                type="button"
                onClick={onClose}
                className="flex h-7 w-7 items-center justify-center rounded-lg text-gray-400 transition hover:bg-white/10 hover:text-white"
                aria-label="Close conversation history"
                title="Close side chat"
              >
                <X className="h-4 w-4" strokeWidth={2} />
              </button>
            </div>
          </div>

          {/* Conversation List */}
          <div className="flex-1 overflow-y-auto px-3 py-3">
            {conversations.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full px-4 py-12 text-center text-gray-400">
                <p className="text-[14px] text-gray-300">No conversations yet</p>
                <p className="mt-1 text-[13px] text-gray-500">
                  Ask a question below to start exploring run evidence.
                </p>
                {suggestions.length > 0 && (
                  <div className="mt-6 flex flex-col gap-1.5 w-full text-left">
                    <div className="text-[11px] font-medium uppercase tracking-wider text-gray-500 px-1">
                      Suggested questions
                    </div>
                    {suggestions.map((s) => (
                      <button
                        key={s}
                        type="button"
                        onClick={() => void handleSend(s)}
                        className="rounded-xl border border-white/5 bg-[#16181e] p-2.5 text-left text-[13px] text-gray-300 transition hover:border-white/15 hover:bg-[#1f222a] hover:text-white"
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            ) : (
              <ul role="list" className="space-y-0.5">
                {conversations.map((conv) => {
                  const isActive = conv.id === activeConversationId;
                  return (
                    <li key={conv.id}>
                      <button
                        type="button"
                        onClick={() => handleSelect(conv.id)}
                        className={`flex w-full items-center justify-between gap-3 rounded-lg px-3 py-2.5 text-left transition ${
                          isActive
                            ? "bg-white/10 text-white font-medium"
                            : "text-gray-300 hover:bg-white/5 hover:text-white"
                        }`}
                        aria-current={isActive ? "true" : undefined}
                      >
                        <span className="min-w-0 flex-1 truncate text-[14px] leading-snug">
                          {conv.title}
                        </span>
                        <span className="shrink-0 text-[12px] tabular-nums text-gray-500 font-normal">
                          {formatRelativeTime(conv.updatedAt || conv.createdAt)}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          {/* Bottom In-Drawer Composer for List View */}
          <div className="border-t border-white/10 bg-[#0d0f12] p-3">
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void handleSend(input);
              }}
              className="flex items-center gap-1.5 rounded-full bg-[#161922] border border-white/15 pl-3.5 pr-1.5 py-1.5 transition focus-within:border-primary/60"
            >
              <input
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Ask a question about this run..."
                className="min-w-0 flex-1 bg-transparent text-[14px] text-white placeholder:text-gray-500 focus:outline-none"
                disabled={isAnswering}
              />
              <button
                type="submit"
                disabled={!input.trim() || isAnswering}
                className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full transition ${
                  input.trim() && !isAnswering
                    ? "bg-primary text-white hover:brightness-110"
                    : "bg-white/10 text-gray-500 cursor-not-allowed"
                }`}
                aria-label="Send message"
              >
                <ArrowUp className="h-3.5 w-3.5" strokeWidth={2.25} />
              </button>
            </form>
          </div>
        </>
      ) : (
        /* ── 2. CONVERSATION DETAIL VIEW (Matching Reference Image 1) ── */
        <>
          {/* Detail Header */}
          <div className="flex items-center gap-2 border-b border-white/10 px-3.5 py-3 bg-[#0d0f12]">
            <button
              type="button"
              onClick={() => setViewingId(null)}
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-gray-400 transition hover:bg-white/10 hover:text-white cursor-pointer"
              aria-label="Back to conversation list"
              title="Back to chats"
            >
              <ArrowLeft className="h-4 w-4" strokeWidth={2} />
            </button>
            <span className="min-w-0 flex-1 truncate text-[14.5px] font-medium text-white tracking-normal">
              {viewingConv.title}
            </span>
            <button
              type="button"
              onClick={handleStartNewChat}
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-gray-400 transition hover:bg-white/10 hover:text-white cursor-pointer"
              title="New chat"
              aria-label="New chat"
            >
              <Plus className="h-4 w-4" strokeWidth={2} />
            </button>
            <button
              type="button"
              onClick={onClose}
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-gray-400 transition hover:bg-white/10 hover:text-white cursor-pointer"
              aria-label="Close side chat"
              title="Close"
            >
              <X className="h-4 w-4" strokeWidth={2} />
            </button>
          </div>

          {/* Complete Message History */}
          <div ref={threadScrollRef} className="flex-1 space-y-4 overflow-y-auto p-4 font-sans text-[14px]">
            {viewingConv.messages.map((msg: ChatMessage) => {
              const isUser = msg.role === "user";
              return (
                <div key={msg.id} className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
                  {isUser ? (
                    /* User message speech bubble (Right) */
                    <div className="max-w-[85%] rounded-2xl bg-[#1e222b] border border-white/5 px-3.5 py-2 text-[14px] leading-relaxed text-white shadow-sm font-sans">
                      {msg.content}
                    </div>
                  ) : (
                    /* AI answer text */
                    <div className="w-full font-sans">
                      <CodexMessageRenderer content={msg.content} isError={msg.error} />
                    </div>
                  )}
                </div>
              );
            })}

            {/* Typing indicator while answer is generating */}
            {isAnswering && (
              <div className="flex justify-start">
                <div className="inline-flex items-center gap-1.5 rounded-2xl rounded-tl-sm bg-[#161922] px-3.5 py-2 text-gray-400">
                  <span className="h-1.5 w-1.5 animate-soft-pulse rounded-full bg-[#3b82f6]" />
                  <span
                    className="h-1.5 w-1.5 animate-soft-pulse rounded-full bg-[#3b82f6]"
                    style={{ animationDelay: "150ms" }}
                  />
                  <span
                    className="h-1.5 w-1.5 animate-soft-pulse rounded-full bg-[#3b82f6]"
                    style={{ animationDelay: "300ms" }}
                  />
                </div>
              </div>
            )}
          </div>

          {/* Quick Suggestion Chips above Drawer Composer (Image 1) */}
          {suggestions.length > 0 && !isAnswering && (
            <div className="border-t border-white/5 bg-[#0d0f12] px-3 py-2">
              <div className="flex items-center gap-1.5 overflow-x-auto no-scrollbar pb-0.5">
                {suggestions.map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => void handleSend(s)}
                    className="shrink-0 rounded-full border border-white/10 bg-[#16181e] px-3 py-1 text-[12px] text-gray-300 transition hover:border-white/20 hover:bg-[#1f222a] hover:text-white"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Interactive In-Drawer Composer for Detail View (Image 1) */}
          <div className="border-t border-white/10 bg-[#0d0f12] p-3">
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void handleSend(input);
              }}
              className="flex items-center gap-1.5 rounded-full bg-[#161922] border border-white/15 pl-3.5 pr-1.5 py-1.5 transition focus-within:border-primary/60"
            >
              <input
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Ask a follow up question..."
                className="min-w-0 flex-1 bg-transparent text-[14px] text-white placeholder:text-gray-500 focus:outline-none"
                disabled={isAnswering}
              />
              <button
                type="submit"
                disabled={!input.trim() || isAnswering}
                className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full transition ${
                  input.trim() && !isAnswering
                    ? "bg-primary text-white hover:brightness-110"
                    : "bg-white/10 text-gray-500 cursor-not-allowed"
                }`}
                aria-label="Send message"
              >
                <ArrowUp className="h-3.5 w-3.5" strokeWidth={2.25} />
              </button>
            </form>
          </div>
        </>
      )}
    </>
  );

  if (embedded) {
    return (
      <div
        ref={drawerRef}
        tabIndex={-1}
        aria-label="Conversation history"
        className="flex h-[calc(100vh-1.5rem)] flex-col overflow-hidden rounded-2xl border border-white/10 bg-[#0d0f12] text-white shadow-2xl outline-none font-sans antialiased text-[14px]"
      >
        {content}
      </div>
    );
  }

  return (
    <>
      {/* Subtle backdrop overlay (only for standalone overlay mode) */}
      <div
        aria-hidden
        className={`fixed inset-0 z-40 bg-black/40 backdrop-blur-[2px] transition-opacity duration-[250ms] ${
          open ? "opacity-100" : "pointer-events-none opacity-0"
        }`}
        onClick={open ? onClose : undefined}
      />

      {/* Slide-over Drawer Panel */}
      <aside
        ref={drawerRef}
        tabIndex={-1}
        aria-label="Conversation history"
        className={`fixed right-0 top-0 z-50 flex h-full w-[380px] max-w-[92vw] flex-col border-l border-white/10 bg-[#0d0f12] text-white outline-none shadow-2xl transition-transform duration-[280ms] ease-[cubic-bezier(0.32,0.72,0,1)] font-sans antialiased text-[14px] ${
          open ? "translate-x-0" : "translate-x-full"
        }`}
      >
        {content}
      </aside>
    </>
  );
}
