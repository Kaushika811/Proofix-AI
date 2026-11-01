// @vitest-environment jsdom
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, renderHook, act } from "@testing-library/react";
import { LatestChatExchange } from "./LatestChatExchange";
import { ChatHistoryDrawer } from "./ChatHistoryDrawer";
import { VsCodeSidebarRightIcon } from "./VsCodeSidebarRightIcon";
import { useChatConversations, type Conversation } from "./useChatConversations";

describe("LatestChatExchange", () => {
  it("renders nothing visible when latestUserMsg is null", () => {
    const { container } = render(
      <LatestChatExchange latestUserMsg={null} latestAIMsg={null} isAnswering={false} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders only the latest interaction (User Question + AI Answer)", () => {
    render(
      <LatestChatExchange
        latestUserMsg="Which files changed?"
        latestAIMsg="No patches were generated for this run."
        isAnswering={false}
      />,
    );

    expect(screen.getByText("Which files changed?")).toBeTruthy();
    expect(screen.getByText("No patches were generated for this run.")).toBeTruthy();
    expect(screen.queryByText("U")).toBeNull();
    expect(screen.queryByText("AI")).toBeNull();
  });

  it("can be minimized to a compact pill and expanded back", () => {
    render(
      <LatestChatExchange
        latestUserMsg="Show the root cause"
        latestAIMsg="Null pointer exception in auth service."
        isAnswering={false}
      />,
    );

    expect(screen.getByText("Null pointer exception in auth service.")).toBeTruthy();

    // Click minimize button
    const minimizeBtn = screen.getByLabelText("Minimize answer");
    fireEvent.click(minimizeBtn);

    // AI message body is hidden in minimized mode, compact preview is shown
    expect(screen.queryByText("Null pointer exception in auth service.")).toBeNull();
    expect(screen.getByText("Latest Q:")).toBeTruthy();

    // Click expand button
    const expandBtn = screen.getAllByLabelText("Expand answer")[0];
    fireEvent.click(expandBtn);

    // Fully expanded again
    expect(screen.getByText("Null pointer exception in auth service.")).toBeTruthy();
  });

  it("can be dismissed with the close button", () => {
    render(
      <LatestChatExchange
        latestUserMsg="How was the fix validated?"
        latestAIMsg="Validation passed."
        isAnswering={false}
      />,
    );

    expect(screen.getByText("Validation passed.")).toBeTruthy();

    // Click close button
    const closeBtn = screen.getByLabelText("Close answer");
    fireEvent.click(closeBtn);

    // Component is dismissed
    expect(screen.queryByText("Validation passed.")).toBeNull();
  });
});

describe("VsCodeSidebarRightIcon", () => {
  it("renders hollow split layout without fill when active is false", () => {
    const { container } = render(<VsCodeSidebarRightIcon active={false} />);
    const paths = container.querySelectorAll("path");
    // When active is false, no filled right partition path is rendered
    expect(paths.length).toBe(0);
    expect(container.querySelector("rect")).toBeTruthy();
    expect(container.querySelector("line")).toBeTruthy();
  });

  it("renders right partition path fill when active is true", () => {
    const { container } = render(<VsCodeSidebarRightIcon active={true} />);
    const path = container.querySelector("path");
    expect(path).toBeTruthy();
    expect(path?.getAttribute("fill")).toBe("currentColor");
  });
});

describe("ChatHistoryDrawer", () => {
  const sampleConversations: Conversation[] = [
    {
      id: "conv-1",
      title: "How was the fix validated?",
      createdAt: Date.now() - 1000,
      updatedAt: Date.now() - 1000,
      messages: [
        {
          id: "m1",
          role: "user",
          content: "How was the fix validated?",
          createdAt: Date.now() - 1000,
        },
        {
          id: "m2",
          role: "assistant",
          content: "Validation passed via mutation testing.",
          createdAt: Date.now() - 500,
        },
      ],
    },
    {
      id: "conv-2",
      title: "Which files changed?",
      createdAt: Date.now() - 60000 * 2,
      updatedAt: Date.now() - 60000 * 2,
      messages: [
        {
          id: "m3",
          role: "user",
          content: "Which files changed?",
          createdAt: Date.now() - 60000 * 2,
        },
      ],
    },
  ];

  it("renders conversation list view with titles and timestamps in embedded mode", () => {
    render(
      <ChatHistoryDrawer
        open={true}
        onClose={vi.fn()}
        conversations={sampleConversations}
        activeConversationId={null}
        onSelectConversation={vi.fn()}
        onNewChat={vi.fn()}
        embedded={true}
      />,
    );

    expect(screen.getByText("Chats")).toBeTruthy();
    expect(screen.getByText("How was the fix validated?")).toBeTruthy();
    expect(screen.getByText("Which files changed?")).toBeTruthy();
  });

  it("navigates to conversation detail view and allows sending a message from the in-drawer composer", async () => {
    const onSelect = vi.fn();
    const onSend = vi.fn().mockResolvedValue("Answer");
    render(
      <ChatHistoryDrawer
        open={true}
        onClose={vi.fn()}
        conversations={sampleConversations}
        activeConversationId={null}
        onSelectConversation={onSelect}
        onNewChat={vi.fn()}
        onSendMessage={onSend}
      />,
    );

    fireEvent.click(screen.getByText("How was the fix validated?"));
    expect(onSelect).toHaveBeenCalledWith("conv-1");

    // Full thread content is displayed inside the drawer
    expect(screen.getByText("Validation passed via mutation testing.")).toBeTruthy();

    // In-drawer input is present in detail view
    const input = screen.getByPlaceholderText("Ask a follow up question...");
    expect(input).toBeTruthy();

    fireEvent.change(input, { target: { value: "Show the mutation score" } });
    fireEvent.submit(input.closest("form")!);

    expect(onSend).toHaveBeenCalledWith("Show the mutation score");
  });

  it("allows sending a question via suggestion chips inside the drawer", () => {
    const onSend = vi.fn().mockResolvedValue("Answer");
    render(
      <ChatHistoryDrawer
        open={true}
        onClose={vi.fn()}
        conversations={sampleConversations}
        activeConversationId="conv-1"
        onSelectConversation={vi.fn()}
        onNewChat={vi.fn()}
        onSendMessage={onSend}
      />,
    );

    const suggestionBtn = screen.getByText("Show the root cause");
    expect(suggestionBtn).toBeTruthy();

    fireEvent.click(suggestionBtn);
    expect(onSend).toHaveBeenCalledWith("Show the root cause");
  });

  it("renders typing indicator when isAnswering is true", () => {
    render(
      <ChatHistoryDrawer
        open={true}
        onClose={vi.fn()}
        conversations={sampleConversations}
        activeConversationId="conv-1"
        onSelectConversation={vi.fn()}
        onNewChat={vi.fn()}
        isAnswering={true}
      />,
    );

    const input = screen.getByPlaceholderText("Ask a follow up question...") as HTMLInputElement;
    expect(input.disabled).toBe(true);
  });

  it("returns to conversation list view when back arrow is clicked", () => {
    render(
      <ChatHistoryDrawer
        open={true}
        onClose={vi.fn()}
        conversations={sampleConversations}
        activeConversationId={null}
        onSelectConversation={vi.fn()}
        onNewChat={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByText("How was the fix validated?"));
    expect(screen.getByText("Validation passed via mutation testing.")).toBeTruthy();

    const backBtn = screen.getByLabelText("Back to conversation list");
    fireEvent.click(backBtn);

    expect(screen.getByText("Chats")).toBeTruthy();
  });
});

describe("useChatConversations", () => {
  it("manages Scenarios A, B, C, D, E correctly", async () => {
    const { result } = renderHook(() => useChatConversations());
    const mockAnswerer = vi.fn(async (q: string) => `Answer to: ${q}`);

    // Initial state: no messages above bar, no conversations
    expect(result.current.latestUserMsg).toBeNull();
    expect(result.current.latestAIMsg).toBeNull();
    expect(result.current.conversations).toHaveLength(0);

    // Scenario A: Ask "How was the fix validated?"
    await act(async () => {
      await result.current.sendMessage("How was the fix validated?", mockAnswerer);
    });

    expect(result.current.latestUserMsg).toBe("How was the fix validated?");
    expect(result.current.latestAIMsg).toBe("Answer to: How was the fix validated?");
    expect(result.current.conversations).toHaveLength(1);
    expect(result.current.conversations[0].messages).toHaveLength(2);

    // Scenario B: Ask "Which files changed?"
    // Expected: Latest exchange is replaced. History stores both exchanges.
    await act(async () => {
      await result.current.sendMessage("Which files changed?", mockAnswerer);
    });

    expect(result.current.latestUserMsg).toBe("Which files changed?");
    expect(result.current.latestAIMsg).toBe("Answer to: Which files changed?");
    expect(result.current.conversations).toHaveLength(1);
    expect(result.current.conversations[0].messages).toHaveLength(4);

    // Scenario C & D: Select conversation
    // Expected: Latest exchange above bar is cleared (not populated with full history)
    act(() => {
      result.current.selectConversation(result.current.conversations[0].id);
    });
    expect(result.current.latestUserMsg).toBeNull();
    expect(result.current.latestAIMsg).toBeNull();

    // Scenario E: Click New Chat
    // Expected: Clears active conversation & latest exchange
    act(() => {
      result.current.newChat();
    });
    expect(result.current.activeConversationId).toBeNull();
    expect(result.current.latestUserMsg).toBeNull();
    expect(result.current.latestAIMsg).toBeNull();

    // Ask in new conversation
    await act(async () => {
      await result.current.sendMessage("New question in new chat", mockAnswerer);
    });
    expect(result.current.latestUserMsg).toBe("New question in new chat");
    expect(result.current.conversations).toHaveLength(2);

    // Scenario: Send message from side chat bar (updateLatestExchange: false)
    await act(async () => {
      await result.current.sendMessage("Side bar question", mockAnswerer, {
        updateLatestExchange: false,
      });
    });
    // latestUserMsg is NOT overwritten by the side chat question
    expect(result.current.latestUserMsg).toBe("New question in new chat");
    // Conversation receives the message
    expect(result.current.conversations[0].messages).toHaveLength(4);
  });
});

describe("CodexMessageRenderer", () => {
  it("renders structured markdown with headers, bold text, inline code, and code blocks", async () => {
    const { CodexMessageRenderer } = await import("./CodexMessageRenderer");
    render(
      <CodexMessageRenderer
        content={`### Agent Pipeline Summary\n• **Repository**: \`vulnapi\`\n\`\`\`\n[A1] ──► [A2]\n\`\`\``}
      />,
    );

    expect(screen.getByText("Agent Pipeline Summary")).toBeTruthy();
    expect(screen.getByText("Repository")).toBeTruthy();
    expect(screen.getByText("vulnapi")).toBeTruthy();
    expect(screen.getByText("[A1] ──► [A2]")).toBeTruthy();
  });
});
