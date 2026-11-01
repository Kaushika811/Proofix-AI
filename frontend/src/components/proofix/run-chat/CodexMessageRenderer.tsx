import { type ReactNode } from "react";

interface Props {
  content: string;
  isError?: boolean;
}

/**
 * CodexMessageRenderer
 *
 * Renders AI responses with exact Codex typography, font family, font size and styling:
 * - Font family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Inter", "Söhne", Roboto, sans-serif
 * - Font size: 14px (leading 1.65) for body text and lists, 13px for inline code
 * - High-contrast text (#ececec) on deep dark background
 * - Code chips: rounded-[5px] bg-[#24272e] border border-white/[0.08] font-mono text-[13px]
 * - Code blocks: rounded-xl bg-[#101216] border border-white/[0.08] font-mono text-[12.5px]
 * - Blue section indicators & file reference links (🐍 python, 📄 config/doc)
 */
export function CodexMessageRenderer({ content, isError }: Props) {
  if (isError) {
    return <div className="text-[14px] font-medium text-[#ef4444] leading-relaxed">{content}</div>;
  }

  // Split into paragraphs / code blocks
  const blocks = splitContentIntoBlocks(content);

  return (
    <div className="space-y-3.5 font-sans text-[14px] leading-[1.65] text-[#ececec] antialiased">
      {blocks.map((block, idx) => {
        if (block.type === "code") {
          return (
            <div
              key={idx}
              className="my-3 overflow-x-auto rounded-xl border border-white/[0.08] bg-[#101216] p-3.5 font-mono text-[12.5px] text-[#e5e7eb] shadow-inner"
            >
              <pre className="leading-relaxed">{block.text}</pre>
            </div>
          );
        }

        if (block.type === "header") {
          return (
            <div
              key={idx}
              className="mt-4 mb-2 flex items-center gap-2 font-semibold text-white text-[14.5px] tracking-normal"
            >
              <span className="h-1.5 w-1.5 rounded-full bg-[#3b82f6] shrink-0" />
              <span>{block.text}</span>
            </div>
          );
        }

        if (block.type === "list") {
          return (
            <ul key={idx} className="my-2 space-y-2 pl-1">
              {block.items?.map((item, itemIdx) => (
                <li key={itemIdx} className="flex items-start gap-2.5">
                  <span className="mt-2.5 h-1 w-1 shrink-0 rounded-full bg-gray-400" />
                  <span className="leading-[1.65] text-[#ececec]">{renderInlineFormatting(item)}</span>
                </li>
              ))}
            </ul>
          );
        }

        // Regular paragraph
        return (
          <p key={idx} className="leading-[1.65] text-[#ececec]">
            {renderInlineFormatting(block.text)}
          </p>
        );
      })}
    </div>
  );
}

interface Block {
  type: "text" | "code" | "header" | "list";
  text: string;
  items?: string[];
}

function splitContentIntoBlocks(raw: string): Block[] {
  const lines = raw.split("\n");
  const blocks: Block[] = [];
  let inCodeBlock = false;
  let codeLines: string[] = [];
  let currentListItems: string[] = [];

  const flushList = () => {
    if (currentListItems.length > 0) {
      blocks.push({
        type: "list",
        text: "",
        items: [...currentListItems],
      });
      currentListItems = [];
    }
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // Code block fences (``` or ~~~)
    if (line.trim().startsWith("```") || line.trim().startsWith("~~~")) {
      flushList();
      if (inCodeBlock) {
        blocks.push({
          type: "code",
          text: codeLines.join("\n"),
        });
        codeLines = [];
        inCodeBlock = false;
      } else {
        inCodeBlock = true;
      }
      continue;
    }

    if (inCodeBlock) {
      codeLines.push(line);
      continue;
    }

    // Markdown Headers (e.g. ### Header or ## Header)
    const headerMatch = line.match(/^#{1,4}\s+(.+)$/);
    if (headerMatch) {
      flushList();
      blocks.push({
        type: "header",
        text: headerMatch[1].trim(),
      });
      continue;
    }

    // Bullet points (• or - or *)
    const listMatch = line.match(/^(\s*[-•*]|\s*\d+\.)\s+(.+)$/);
    if (listMatch) {
      currentListItems.push(listMatch[2].trim());
      continue;
    }

    // Blank line
    if (!line.trim()) {
      flushList();
      continue;
    }

    // Regular line / text
    flushList();
    blocks.push({
      type: "text",
      text: line,
    });
  }

  flushList();
  if (inCodeBlock && codeLines.length > 0) {
    blocks.push({
      type: "code",
      text: codeLines.join("\n"),
    });
  }

  return blocks;
}

/**
 * Handles inline code (`code`), bold (**text**), and file links (e.g., `path/to/file.py (line N)`)
 */
function renderInlineFormatting(text: string): ReactNode[] {
  // Regex to match code blocks `...`, bold text **...**, and file link patterns
  const regex = /(`[^`]+`|\*\*[^*]+\*\*|(?:\b[\w.-]+\/[\w./-]+(?:\s*\([^)]+\))?))/g;
  const parts = text.split(regex);

  return parts.map((part, i) => {
    if (part.startsWith("`") && part.endsWith("`") && part.length >= 2) {
      return (
        <code
          key={i}
          className="rounded-[5px] bg-[#24272e] px-1.5 py-0.5 font-mono text-[13px] text-[#e5e7eb] border border-white/[0.08]"
        >
          {part.slice(1, -1)}
        </code>
      );
    }
    if (part.startsWith("**") && part.endsWith("**") && part.length >= 4) {
      return (
        <strong key={i} className="font-semibold text-white">
          {part.slice(2, -2)}
        </strong>
      );
    }
    // File link pattern (e.g. backend/services/mci_verifier.py (line 78) or frontend/src/Workspace.tsx)
    if (/\b[\w.-]+\/[\w./-]+\.(py|ts|tsx|js|jsx|json|yml|yaml|env|md)\b/i.test(part)) {
      const isPython = /\.py\b/i.test(part);
      return (
        <span key={i} className="inline-flex items-center gap-1 text-[#60a5fa] hover:underline cursor-pointer font-normal">
          <span>{isPython ? "🐍" : "📄"}</span>
          <span>{part}</span>
        </span>
      );
    }
    return part;
  });
}
