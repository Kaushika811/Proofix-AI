/**
 * Unified-diff parsing.
 *
 * The classification order is the whole correctness story: `+++` and `---`
 * headers begin with the same characters as added and removed lines, so testing
 * for content first labels every file header as a change and reports two extra
 * modified lines per file.
 *
 * Line numbers come from the hunk headers and advance per side. A reviewer
 * cross-referencing `auth.py:27` needs the file's own numbering; an index into
 * the diff would point at the wrong line in anything with two hunks.
 */
import { describe, expect, it } from "vitest";
import { diffStats, parseUnifiedDiff, splitDiffByFile } from "./DiffView";

const DIFF = `--- a/pkg/auth.py
+++ b/pkg/auth.py
@@ -1,4 +1,5 @@
 import time
 
 def validate(token):
-    return True
+    if token.exp < time.time():
+        return False
`;

describe("parseUnifiedDiff", () => {
  it("classifies file headers as headers, not as changes", () => {
    const lines = parseUnifiedDiff(DIFF);
    expect(lines[0].kind).toBe("file");
    expect(lines[1].kind).toBe("file");
    // Two content lines removed/added, and no more: the `---`/`+++` pair must
    // not inflate the count.
    expect(diffStats(lines)).toEqual({ added: 2, removed: 1 });
  });

  it("numbers lines from the hunk header, per side", () => {
    const lines = parseUnifiedDiff(DIFF);
    const content = lines.filter((l) => ["add", "del", "context"].includes(l.kind));

    expect(content[0]).toMatchObject({ text: "import time", oldNumber: 1, newNumber: 1 });
    // A removed line has no line in the patched file, and vice versa.
    const removed = content.find((l) => l.kind === "del");
    expect(removed?.oldNumber).toBe(4);
    expect(removed?.newNumber).toBeNull();
    const added = content.filter((l) => l.kind === "add");
    expect(added[0].oldNumber).toBeNull();
    expect(added[0].newNumber).toBe(4);
    expect(added[1].newNumber).toBe(5);
  });

  it("restarts numbering at every hunk", () => {
    const twoHunks = `--- a/x.py
+++ b/x.py
@@ -1,2 +1,2 @@
 a
-b
+B
@@ -40,2 +40,2 @@
 c
-d
+D
`;
    const lines = parseUnifiedDiff(twoHunks);
    const second = lines.filter((l) => l.kind === "del")[1];
    expect(second.oldNumber).toBe(41);
  });

  it("keeps the no-newline marker out of the content", () => {
    const lines = parseUnifiedDiff(
      "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n\\ No newline at end of file\n",
    );
    const marker = lines.find((l) => l.text.startsWith("\\"));
    expect(marker?.kind).toBe("meta");
    expect(diffStats(lines)).toEqual({ added: 1, removed: 1 });
  });

  it("returns nothing for an empty diff rather than a blank line", () => {
    expect(parseUnifiedDiff("")).toEqual([]);
  });

  it("strips the marker column from the rendered text", () => {
    const lines = parseUnifiedDiff(DIFF);
    const added = lines.find((l) => l.kind === "add");
    expect(added?.text).toBe("    if token.exp < time.time():");
    expect(added?.text.startsWith("+")).toBe(false);
  });
});

describe("splitDiffByFile", () => {
  const TWO_FILE_DIFF = `--- a/pkg/auth.py
+++ b/pkg/auth.py
@@ -1,2 +1,2 @@
 def validate(token):
-    return True
+    return False
--- a/pkg/utils.py
+++ b/pkg/utils.py
@@ -1,2 +1,2 @@
 def helper():
-    pass
+    return None
`;

  it("partitions a multi-file diff into one chunk per file", () => {
    const chunks = splitDiffByFile(TWO_FILE_DIFF);
    expect([...chunks.keys()]).toEqual(["pkg/auth.py", "pkg/utils.py"]);
  });

  it("each chunk parses to only that file's own change, not the whole diff", () => {
    const chunks = splitDiffByFile(TWO_FILE_DIFF);
    const authLines = parseUnifiedDiff(chunks.get("pkg/auth.py")!);
    expect(diffStats(authLines)).toEqual({ added: 1, removed: 1 });
    expect(authLines.some((l) => l.text.includes("helper"))).toBe(false);

    const utilsLines = parseUnifiedDiff(chunks.get("pkg/utils.py")!);
    expect(utilsLines.some((l) => l.text.includes("return None"))).toBe(true);
    expect(utilsLines.some((l) => l.text.includes("validate"))).toBe(false);
  });

  it("a single-file diff yields exactly one chunk equal to the whole text", () => {
    const chunks = splitDiffByFile(DIFF);
    expect(chunks.size).toBe(1);
    expect(chunks.get("pkg/auth.py")).toBe(DIFF);
  });

  it("returns nothing for an empty diff", () => {
    expect(splitDiffByFile("").size).toBe(0);
  });
});
