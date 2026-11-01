import { useState, useCallback } from "react";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  createdAt: number;
  error?: boolean;
}

export interface Conversation {
  id: string;
  /** First user question, truncated if needed. */
  title: string;
  createdAt: number;
  updatedAt: number;
  messages: ChatMessage[];
}

type Answerer = (q: string) => string | Promise<string>;

function makeId() {
  return `conv-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
}

function makeMessageId() {
  return `msg-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
}

function makeTitle(question: string): string {
  const trimmed = question.trim();
  return trimmed.length > 50 ? trimmed.slice(0, 47) + "…" : trimmed;
}

export function formatRelativeTime(ms: number): string {
  const diff = Date.now() - ms;
  const minute = 60_000;
  const hour = 60 * minute;
  const day = 24 * hour;
  const week = 7 * day;
  const month = 30 * day;

  if (diff < minute) return "just now";
  if (diff < hour) return `${Math.max(1, Math.floor(diff / minute))}m`;
  if (diff < day) return `${Math.floor(diff / hour)}h`;
  if (diff < week) return `${Math.floor(diff / day)}d`;
  if (diff < month) return `${Math.floor(diff / week)}w`;
  return `${Math.floor(diff / month)}mo`;
}

export interface UseChatConversationsReturn {
  conversations: Conversation[];
  activeConversationId: string | null;
  activeConversation: Conversation | null;
  /**
   * The question from the latest submitted exchange.
   * Replaced (not accumulated) on each new submission.
   * Null = no question has been submitted yet in the current view.
   */
  latestUserMsg: string | null;
  /**
   * The AI answer from the latest submitted exchange.
   * Null while isAnswering (loading dots) or before any exchange.
   */
  latestAIMsg: string | null;
  /** True while the answerer is in flight for the current exchange. */
  isAnswering: boolean;
  /** Appends the user message, calls answerer, appends the AI reply, returns the reply text. */
  sendMessage: (
    question: string,
    answerer: Answerer,
    options?: { updateLatestExchange?: boolean },
  ) => Promise<string>;
  selectConversation: (id: string) => void;
  newChat: () => void;
}

export function useChatConversations(): UseChatConversationsReturn {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [isAnswering, setIsAnswering] = useState(false);

  /**
   * Dedicated latest-exchange state for the area above ChatPanel.
   * Shows ONLY the single latest interaction (User Question + AI Answer).
   * Selecting an old conversation leaves this empty until a new question is sent.
   */
  const [latestUserMsg, setLatestUserMsg] = useState<string | null>(null);
  const [latestAIMsg, setLatestAIMsg] = useState<string | null>(null);

  const activeConversation = conversations.find((c) => c.id === activeConversationId) ?? null;

  const sendMessage = useCallback(
    async (
      question: string,
      answerer: Answerer,
      options?: { updateLatestExchange?: boolean },
    ): Promise<string> => {
      const q = question.trim();
      if (!q) return "";

      const shouldUpdateLatest = options?.updateLatestExchange !== false;
      const now = Date.now();
      let convId = activeConversationId;

      // 1. If triggered from the bottom bar, show question above the chat bar and clear previous answer.
      if (shouldUpdateLatest) {
        setLatestUserMsg(q);
        setLatestAIMsg(null);
      }
      setIsAnswering(true);

      const userMsg: ChatMessage = {
        id: makeMessageId(),
        role: "user",
        content: q,
        createdAt: now,
      };

      // If no active conversation exists, create one with the user question as title.
      if (!convId || !conversations.some((c) => c.id === convId)) {
        const newConv: Conversation = {
          id: makeId(),
          title: makeTitle(q),
          createdAt: now,
          updatedAt: now,
          messages: [userMsg],
        };
        convId = newConv.id;
        setActiveConversationId(newConv.id);
        setConversations((prev) => [newConv, ...prev]);
      } else {
        // Append user message to existing active conversation
        setConversations((prev) =>
          prev.map((c) =>
            c.id === convId
              ? {
                  ...c,
                  updatedAt: now,
                  messages: [...c.messages, userMsg],
                }
              : c,
          ),
        );
      }

      let replyText = "";
      try {
        const reply = await Promise.resolve(answerer(q));
        // Brief delay so loading animation is smooth
        await new Promise<void>((resolve) => window.setTimeout(resolve, 350));

        replyText = reply;
        if (shouldUpdateLatest) {
          setLatestAIMsg(reply);
        }

        const aiMsg: ChatMessage = {
          id: makeMessageId(),
          role: "assistant",
          content: reply,
          createdAt: Date.now(),
        };

        setConversations((prev) =>
          prev.map((c) =>
            c.id === convId
              ? {
                  ...c,
                  updatedAt: Date.now(),
                  messages: [...c.messages, aiMsg],
                }
              : c,
          ),
        );
      } catch (err) {
        const errorText =
          err instanceof Error ? err.message : "Something went wrong. Please try again.";
        replyText = errorText;
        if (shouldUpdateLatest) {
          setLatestAIMsg(errorText);
        }

        const aiMsg: ChatMessage = {
          id: makeMessageId(),
          role: "assistant",
          content: errorText,
          createdAt: Date.now(),
          error: true,
        };

        setConversations((prev) =>
          prev.map((c) =>
            c.id === convId
              ? {
                  ...c,
                  updatedAt: Date.now(),
                  messages: [...c.messages, aiMsg],
                }
              : c,
          ),
        );
      } finally {
        setIsAnswering(false);
      }

      return replyText;
    },
    [activeConversationId, conversations],
  );

  /**
   * Selecting an old conversation makes it active in the sidebar.
   * As per requirement: do NOT copy previous messages to LatestChatExchange.
   * LatestChatExchange remains empty until the user sends a new question in that conversation.
   */
  const selectConversation = useCallback((id: string) => {
    setActiveConversationId(id);
    setLatestUserMsg(null);
    setLatestAIMsg(null);
  }, []);

  /**
   * New Chat resets active conversation and clears the latest exchange panel.
   * The new conversation is persisted once the first message is sent.
   */
  const newChat = useCallback(() => {
    setActiveConversationId(null);
    setLatestUserMsg(null);
    setLatestAIMsg(null);
  }, []);

  return {
    conversations,
    activeConversationId,
    activeConversation,
    latestUserMsg,
    latestAIMsg,
    isAnswering,
    sendMessage,
    selectConversation,
    newChat,
  };
}
