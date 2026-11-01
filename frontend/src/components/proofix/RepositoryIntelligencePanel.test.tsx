// @vitest-environment jsdom
/**
 * A0.5's Repository Knowledge Graph panel. Every assertion here traces back to
 * a specific backend field — the point of this suite is to catch the panel
 * inventing a count, a category, or a connection the backend never sent.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const getKnowledgeMetrics = vi.fn();
const getKnowledgeGraph = vi.fn();
const getKnowledgeCapabilities = vi.fn();
const getKnowledgeHotspots = vi.fn();
const getKnowledgeQuery = vi.fn();

vi.mock("@/lib/runService", () => ({
  getKnowledgeMetrics: (...a: unknown[]) => getKnowledgeMetrics(...a),
  getKnowledgeGraph: (...a: unknown[]) => getKnowledgeGraph(...a),
  getKnowledgeCapabilities: (...a: unknown[]) => getKnowledgeCapabilities(...a),
  getKnowledgeHotspots: (...a: unknown[]) => getKnowledgeHotspots(...a),
  getKnowledgeQuery: (...a: unknown[]) => getKnowledgeQuery(...a),
}));

const { RepositoryIntelligencePanel } = await import("./RepositoryIntelligencePanel");

const METRICS = {
  repository_hash: "abc123",
  node_count: 4,
  edge_count: 3,
  average_degree: 1.5,
  nodes_by_type: { file: 2, package: 1, test: 1 },
  edges_by_type: { CONTAINS: 2, IMPORTS: 1 },
  graph_build_ms: 12,
  incremental_update_ms: 0,
  index_build_ms: 120,
  query_count: 0,
  average_query_ms: 0,
  cache_hit_rate: 0,
  cache_hits: 0,
  cache_misses: 1,
  memory_bytes: 4096,
  index_size_bytes: 8192,
  repository_coverage: 1,
  files_total: 2,
  files_represented: 2,
  workspace: { kind: "single", packages: [], nested_repositories: [], languages: { Python: 2 } },
};

const REPO_NODE = {
  id: "repository:root",
  type: "repository",
  label: "repo",
  file: "",
  qualname: "",
  color: "#4C566A",
  attributes: {},
};
const PKG_NODE = {
  id: "package:pkg",
  type: "package",
  label: "pkg",
  file: "pkg",
  qualname: "",
  color: "#5E81AC",
  attributes: {},
};
const FILE_NODE = {
  id: "file:pkg/a.py",
  type: "file",
  label: "a.py",
  file: "pkg/a.py",
  qualname: "",
  color: "#81A1C1",
  attributes: { is_test: false, docstring: "Module A." },
};
const TEST_NODE = {
  id: "test:pkg/test_a.py",
  type: "test",
  label: "test_a.py",
  file: "pkg/test_a.py",
  qualname: "",
  color: "#88C0D0",
  attributes: { is_test: true },
};

const REPOSITORY_VIEW = {
  name: "repository_map",
  truncated: false,
  total_nodes: 4,
  nodes: [REPO_NODE, PKG_NODE, FILE_NODE, TEST_NODE],
  edges: [
    {
      source: "repository:root",
      target: "package:pkg",
      type: "CONTAINS",
      weight: 1,
      provenance: "repository_graph",
      evidence: "contains relation",
    },
    {
      source: "package:pkg",
      target: "file:pkg/a.py",
      type: "CONTAINS",
      weight: 1,
      provenance: "repository_graph",
      evidence: "contains relation",
    },
    {
      source: "package:pkg",
      target: "test:pkg/test_a.py",
      type: "CONTAINS",
      weight: 1,
      provenance: "repository_graph",
      evidence: "contains relation",
    },
  ],
};

const DEPENDENCY_VIEW = {
  name: "dependency_map",
  truncated: false,
  total_nodes: 2,
  nodes: [FILE_NODE, TEST_NODE],
  edges: [
    {
      source: "test:pkg/test_a.py",
      target: "file:pkg/a.py",
      type: "IMPORTS",
      weight: 1,
      provenance: "repository_graph",
      evidence: "imports pkg.a",
    },
  ],
};

const CAPABILITIES = [
  {
    name: "Auth",
    slug: "auth",
    confidence: 0.8,
    files: ["pkg/a.py"],
    entry_points: [],
    signals: {},
    why: "",
    evidence: [],
  },
];

function backendReturns(over: Record<string, unknown> = {}) {
  getKnowledgeMetrics.mockResolvedValue("metrics" in over ? over.metrics : METRICS);
  getKnowledgeGraph.mockImplementation((_runId: string, view: string) => {
    if ("repository" in over && view === "repository") return Promise.resolve(over.repository);
    if ("dependency" in over && view === "dependency") return Promise.resolve(over.dependency);
    return Promise.resolve(view === "repository" ? REPOSITORY_VIEW : DEPENDENCY_VIEW);
  });
  getKnowledgeCapabilities.mockResolvedValue(
    "capabilities" in over ? over.capabilities : CAPABILITIES,
  );
  getKnowledgeHotspots.mockResolvedValue("hotspots" in over ? over.hotspots : []);
  getKnowledgeQuery.mockResolvedValue(
    "query" in over
      ? over.query
      : {
          query: "functions_in_file",
          file: "pkg/a.py",
          qualname: null,
          count: 0,
          results: [],
          query_ms: 0.1,
        },
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("RepositoryIntelligencePanel", () => {
  it("shows Loading before the backend responds", () => {
    getKnowledgeMetrics.mockReturnValue(new Promise(() => {}));
    getKnowledgeGraph.mockReturnValue(new Promise(() => {}));
    getKnowledgeCapabilities.mockReturnValue(new Promise(() => {}));
    getKnowledgeHotspots.mockReturnValue(new Promise(() => {}));

    render(<RepositoryIntelligencePanel runId="r1" />);

    expect(screen.getByText("Loading…")).toBeTruthy();
  });

  it("renders Pending when the layer has not published a graph for this run", async () => {
    backendReturns({ repository: null });

    render(<RepositoryIntelligencePanel runId="r1" />);

    expect(await screen.findByText(/has not published a graph for this run yet/)).toBeTruthy();
  });

  it("surfaces a real failure with a retry", async () => {
    getKnowledgeMetrics.mockRejectedValue(new Error("API 500: Internal Server Error"));
    getKnowledgeGraph.mockResolvedValue(REPOSITORY_VIEW);
    getKnowledgeCapabilities.mockResolvedValue(CAPABILITIES);
    getKnowledgeHotspots.mockResolvedValue([]);

    render(<RepositoryIntelligencePanel runId="r1" />);

    expect(await screen.findByText("API 500: Internal Server Error")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });

  it("renders real DNA counts from nodes_by_type/edges_by_type — never invented", async () => {
    backendReturns();
    render(<RepositoryIntelligencePanel runId="r1" />);

    await screen.findByText("Repository Knowledge Graph");
    const filesChip = screen.getByText("Files").closest("div");
    expect(within(filesChip!.parentElement!).getByText("2")).toBeTruthy();
    expect(screen.getByText("Python 2")).toBeTruthy();
  });

  it("shows 'Not measured' rather than a zero when capabilities/hotspots are unavailable", async () => {
    backendReturns({ capabilities: null, hotspots: null });
    render(<RepositoryIntelligencePanel runId="r1" />);

    await screen.findByText("Repository Knowledge Graph");
    expect(screen.getAllByText("Not measured").length).toBeGreaterThan(0);
  });

  it("renders a cache-hit index status verbatim from A0.5's own event", async () => {
    backendReturns();
    render(
      <RepositoryIntelligencePanel
        runId="r1"
        intelligence={{
          mode: "cache hit",
          modeDetail: "Index reused from a previous run on this repository.",
          phases: [],
          totalMs: 40,
          metrics: { nodes: 4, edges: 3, callables: 1, commits: 2, rememberedRepairs: 0 },
          capabilities: [],
        }}
      />,
    );

    expect(await screen.findByText("● Cache hit")).toBeTruthy();
    expect(screen.getByText(/Index reused from a previous run/)).toBeTruthy();
  });

  it("renders a full-rebuild index status distinctly from a cache hit", async () => {
    backendReturns();
    render(
      <RepositoryIntelligencePanel
        runId="r1"
        intelligence={{
          mode: "full rebuild",
          modeDetail: "New repository analysis.",
          phases: [],
          totalMs: 340,
          metrics: { nodes: 4, edges: 3, callables: 1, commits: 2, rememberedRepairs: 0 },
          capabilities: [],
        }}
      />,
    );

    expect(await screen.findByText("● full rebuild")).toBeTruthy();
    expect(screen.queryByText("● Cache hit")).toBeNull();
  });

  it("renders the real indexing phases A0.5 measured, not a fixed illustration", async () => {
    backendReturns();
    render(
      <RepositoryIntelligencePanel
        runId="r1"
        intelligence={{
          mode: "full rebuild",
          modeDetail: "New repository analysis.",
          phases: [
            { label: "Graph", ms: 40 },
            { label: "History", ms: 60 },
          ],
          totalMs: 100,
          metrics: { nodes: 4, edges: 3, callables: 1, commits: 2, rememberedRepairs: 0 },
          capabilities: [],
        }}
      />,
    );

    await screen.findByText("Repository Knowledge Graph");
    expect(screen.getByText("Indexing pipeline")).toBeTruthy();
    expect(screen.getByText("40ms")).toBeTruthy();
    expect(screen.getByText("60ms")).toBeTruthy();
    // A phase A0.5 did not measure (e.g. "Docs") must not appear invented.
    expect(screen.queryByText("Docs")).toBeNull();
  });

  it("omits the pipeline entirely when no phase measured any time", async () => {
    backendReturns();
    render(
      <RepositoryIntelligencePanel
        runId="r1"
        intelligence={{
          mode: "cache hit",
          modeDetail: "Index reused from a previous run.",
          phases: [],
          totalMs: 2,
          metrics: { nodes: 4, edges: 3, callables: 1, commits: 2, rememberedRepairs: 0 },
          capabilities: [],
        }}
      />,
    );

    await screen.findByText("Repository Knowledge Graph");
    expect(screen.queryByText("Indexing pipeline")).toBeNull();
  });

  it("opens on the Structure view by default once expanded — a labelled tree, not abstract dots", async () => {
    backendReturns();
    const user = userEvent.setup();
    render(<RepositoryIntelligencePanel runId="r1" />);

    await screen.findByText("Repository Knowledge Graph");
    // The explorer sits behind a closed disclosure — the hero already tells
    // the at-a-glance story, this is the drill-down view.
    await user.click(screen.getByText("Repository structure"));
    // The structure tree renders every node's label unconditionally.
    expect(screen.getByText("a.py")).toBeTruthy();
    expect(screen.getByText("test_a.py")).toBeTruthy();
  });

  it("renders an empty graph honestly rather than crashing or inventing rows", async () => {
    backendReturns({
      repository: {
        name: "repository_map",
        truncated: false,
        total_nodes: 0,
        nodes: [],
        edges: [],
      },
      metrics: { ...METRICS, nodes_by_type: {}, edges_by_type: {} },
    });

    render(<RepositoryIntelligencePanel runId="r1" />);

    await screen.findByText("Repository Knowledge Graph");
    // Files metric falls back to 0, not an invented positive count.
    const filesChip = screen.getByText("Files").closest("div");
    expect(within(filesChip!.parentElement!).getByText("0")).toBeTruthy();
  });

  it("switches to the table fallback and lists real nodes", async () => {
    backendReturns();
    const user = userEvent.setup();
    render(<RepositoryIntelligencePanel runId="r1" />);

    await screen.findByText("Repository Knowledge Graph");
    await user.click(screen.getByText("Repository structure"));
    await user.click(screen.getByRole("button", { name: "Table" }));

    expect(screen.getByText("a.py")).toBeTruthy();
    expect(screen.getByText("test_a.py")).toBeTruthy();
    expect(screen.getByText("pkg/a.py")).toBeTruthy();
  });

  it("selects a node from the table and opens the Node Inspector with sourced fields", async () => {
    backendReturns();
    const user = userEvent.setup();
    render(<RepositoryIntelligencePanel runId="r1" />);

    await screen.findByText("Repository Knowledge Graph");
    await user.click(screen.getByText("Repository structure"));
    await user.click(screen.getByRole("button", { name: "Table" }));
    await user.click(screen.getByText("a.py"));

    await waitFor(() => expect(getKnowledgeQuery).toHaveBeenCalled());
    expect(screen.getAllByText("pkg/a.py").length).toBeGreaterThan(0);
    expect(screen.getByText("Module A.")).toBeTruthy();
  });

  it("never fabricates connection counts when the dependency graph is unavailable", async () => {
    backendReturns({ dependency: null });
    const user = userEvent.setup();
    render(<RepositoryIntelligencePanel runId="r1" />);

    await screen.findByText("Repository Knowledge Graph");
    await user.click(screen.getByText("Repository structure"));
    await user.click(screen.getByRole("button", { name: "Table" }));
    await user.click(screen.getByText("a.py"));

    const imports = screen.getByText("Imports").closest("div");
    expect(within(imports!.parentElement!).getByText("Not measured")).toBeTruthy();
  });

  it("computes real import/imported-by counts from the dependency view", async () => {
    backendReturns();
    const user = userEvent.setup();
    render(<RepositoryIntelligencePanel runId="r1" />);

    await screen.findByText("Repository Knowledge Graph");
    await user.click(screen.getByText("Repository structure"));
    await user.click(screen.getByRole("button", { name: "Table" }));
    await user.click(screen.getByText("a.py"));

    const importedBy = screen.getByText("Imported by").closest("div");
    expect(within(importedBy!.parentElement!).getByText("1")).toBeTruthy();
  });

  it("search accepts input without scanning the repository client-side", async () => {
    backendReturns();
    const user = userEvent.setup();
    render(<RepositoryIntelligencePanel runId="r1" />);

    await screen.findByText("Repository Knowledge Graph");
    await user.click(screen.getByText("Repository structure"));
    const input = screen.getByPlaceholderText(/Search files, functions, classes/);
    await user.type(input, "a.py");

    expect((input as HTMLInputElement).value).toBe("a.py");
    // No new graph fetch was triggered by typing — search only filters what
    // was already returned by the server.
    expect(getKnowledgeGraph).toHaveBeenCalledTimes(2);
  });

  it("only offers filters for node types the backend actually returned", async () => {
    backendReturns();
    const user = userEvent.setup();
    render(<RepositoryIntelligencePanel runId="r1" />);

    await screen.findByText("Repository Knowledge Graph");
    await user.click(screen.getByText("Repository structure"));
    expect(screen.getByRole("group", { name: "Filter by node type" })).toBeTruthy();
    // Every present type has a chip; nothing invented (no "class"/"api" chip
    // when the fixture graph carries neither).
    expect(screen.queryByText("api")).toBeNull();
    expect(screen.queryByText("class")).toBeNull();
  });

  it("does not show a Lines row for a node with no line span", async () => {
    backendReturns();
    const user = userEvent.setup();
    render(<RepositoryIntelligencePanel runId="r1" />);

    await screen.findByText("Repository Knowledge Graph");
    await user.click(screen.getByText("Repository structure"));
    await user.click(screen.getByRole("button", { name: "Table" }));
    await user.click(screen.getAllByText("pkg")[0]);

    expect(screen.queryByText("Lines")).toBeNull();
  });
});
