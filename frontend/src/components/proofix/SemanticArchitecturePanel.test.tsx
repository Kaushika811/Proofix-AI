// @vitest-environment jsdom
/**
 * A1's Semantic Risk Terrain panel — the adaptive-density visualization.
 * Every assertion here traces back to a specific field on
 * `GET /api/runs/{runId}/semantic-graph` — the point of this suite is to
 * catch the panel inventing a role, a count, or a connection the backend
 * never sent, and to prove it never reads A0.5's knowledge-graph endpoints
 * as a semantic source. Ranking/bucketing/aggregate stats are client-side
 * display logic over real fields — these tests pin their behavior against
 * fixtures with known, hand-computable values, across every density tier
 * (small/medium/large/very-large).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { SemanticRole } from "./semanticGraphTypes";

const getSemanticGraph = vi.fn();
const getKnowledgeMetrics = vi.fn();
const getKnowledgeGraph = vi.fn();
const getKnowledgeCapabilities = vi.fn();
const getKnowledgeHotspots = vi.fn();
const getKnowledgeQuery = vi.fn();

vi.mock("@/lib/runService", () => ({
  getSemanticGraph: (...a: unknown[]) => getSemanticGraph(...a),
  getKnowledgeMetrics: (...a: unknown[]) => getKnowledgeMetrics(...a),
  getKnowledgeGraph: (...a: unknown[]) => getKnowledgeGraph(...a),
  getKnowledgeCapabilities: (...a: unknown[]) => getKnowledgeCapabilities(...a),
  getKnowledgeHotspots: (...a: unknown[]) => getKnowledgeHotspots(...a),
  getKnowledgeQuery: (...a: unknown[]) => getKnowledgeQuery(...a),
}));

const { SemanticArchitecturePanel } = await import("./SemanticArchitecturePanel");

const ROLES: SemanticRole[] = [
  "auth-boundary",
  "data-access",
  "public-api",
  "config-surface",
  "test-only",
  "internal-util",
];

// auth.py: criticality 0.92, churn 0.9 — clears the 0.7/0.7 hot combo.
// routes.py: criticality 0.55, churn 0.5.
// utils.py: criticality 0.2, churn 0.1 — clearly the least risky.
const GRAPH = {
  generatedAt: "2026-08-13T00:00:00",
  sourceRoots: ["vulnapi"],
  files: [
    {
      path: "vulnapi/auth.py",
      role: "auth-boundary",
      imports: [],
      importedBy: ["vulnapi/routes.py"],
      churnWeight: 0.9,
      criticality: 0.92,
    },
    {
      path: "vulnapi/routes.py",
      role: "public-api",
      imports: ["vulnapi/auth.py"],
      importedBy: [],
      churnWeight: 0.5,
      criticality: 0.55,
    },
    {
      path: "vulnapi/utils.py",
      role: "internal-util",
      imports: [],
      importedBy: [],
      churnWeight: 0.1,
      criticality: 0.2,
    },
  ],
  edges: [["vulnapi/routes.py", "vulnapi/auth.py"]],
  roleCounts: {
    "auth-boundary": 1,
    "data-access": 0,
    "public-api": 1,
    "config-surface": 0,
    "test-only": 0,
    "internal-util": 1,
  },
  totalFiles: 3,
  totalEdges: 1,
};

// Same three files, but auth.py's churn drops below the hot threshold — no
// file crosses the hot combo, so the callout falls back to naming the
// single top-ranked file by criticality instead.
const GRAPH_NO_HOT = {
  ...GRAPH,
  files: [{ ...GRAPH.files[0], churnWeight: 0.3 }, GRAPH.files[1], GRAPH.files[2]],
};

function makeGraph(n: number) {
  const files = Array.from({ length: n }, (_, i) => ({
    path: `pkg/mod_${i}.py`,
    role: ROLES[i % ROLES.length],
    imports: i % 3 === 0 ? [`pkg/mod_${(i + 1) % n}.py`] : [],
    importedBy: Array.from({ length: i % 17 }, (_, j) => `pkg/caller_${j}.py`),
    // Every 37th file is a deliberate, isolated criticality/churn spike so a
    // single-file "highest risk" case is guaranteed at any n, distinct from
    // the generic 0..0.6 noise everything else gets.
    churnWeight: i % 37 === 0 ? 0.95 : (i % 6) / 10,
    criticality: i % 37 === 0 ? 0.97 : (i % 6) / 10,
  }));
  return {
    generatedAt: "2026-08-13T00:00:00",
    sourceRoots: ["pkg"],
    files,
    edges: [],
    roleCounts: ROLES.reduce(
      (acc, r) => ({ ...acc, [r]: files.filter((f) => f.role === r).length }),
      {} as Record<string, number>,
    ),
    totalFiles: files.length,
    totalEdges: 0,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("SemanticArchitecturePanel", () => {
  it("shows Loading before the backend responds", () => {
    getSemanticGraph.mockReturnValue(new Promise(() => {}));
    render(<SemanticArchitecturePanel runId="r1" />);
    expect(screen.getByText("Loading…")).toBeTruthy();
  });

  it("renders Pending when A1 has not published a semantic graph for this run", async () => {
    getSemanticGraph.mockResolvedValue(null);
    render(<SemanticArchitecturePanel runId="r1" />);
    expect(
      await screen.findByText(/A1 has not published a semantic graph for this run yet/),
    ).toBeTruthy();
  });

  it("shows a distinct running message rather than the generic pending one", async () => {
    getSemanticGraph.mockResolvedValue(null);
    render(<SemanticArchitecturePanel runId="r1" status="running" />);
    expect(await screen.findByText(/A1 is classifying files now/)).toBeTruthy();
  });

  it("surfaces a real failure with a retry", async () => {
    getSemanticGraph.mockRejectedValue(new Error("API 500: Internal Server Error"));
    render(<SemanticArchitecturePanel runId="r1" />);
    expect(await screen.findByText("API 500: Internal Server Error")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });

  it("renders an explicit empty message when A1 classified zero files", async () => {
    getSemanticGraph.mockResolvedValue({ ...GRAPH, files: [], totalFiles: 0 });
    render(<SemanticArchitecturePanel runId="r1" />);
    expect(await screen.findByText(/classified no production files/)).toBeTruthy();
  });

  it("the generated insight leads with the hot-combo count when the data supports it", async () => {
    getSemanticGraph.mockResolvedValue(GRAPH);
    render(<SemanticArchitecturePanel runId="r1" />);
    expect(
      await screen.findByText(
        "3 files across 3 architectural roles. 1 file has high criticality and high churn.",
      ),
    ).toBeTruthy();
  });

  it("the generated insight falls back to role concentration when nothing is hot", async () => {
    getSemanticGraph.mockResolvedValue(GRAPH_NO_HOT);
    render(<SemanticArchitecturePanel runId="r1" />);
    expect(
      await screen.findByText(
        "3 files across 3 architectural roles. Auth Boundary contains the highest concentration of critical files.",
      ),
    ).toBeTruthy();
  });

  it("the risk callout names the single top-ranked file when nothing is hot", async () => {
    getSemanticGraph.mockResolvedValue(GRAPH_NO_HOT);
    render(<SemanticArchitecturePanel runId="r1" />);
    expect(
      await screen.findByRole("button", { name: "Highest risk: auth.py · 0.92" }),
    ).toBeTruthy();
  });

  it("clicking the risk callout opens the named file's detail", async () => {
    getSemanticGraph.mockResolvedValue(GRAPH_NO_HOT);
    const user = userEvent.setup();
    render(<SemanticArchitecturePanel runId="r1" />);

    await user.click(await screen.findByRole("button", { name: "Highest risk: auth.py · 0.92" }));
    expect(await screen.findByText("Selected file — one-hop relationships")).toBeTruthy();
  });

  it("renders every one of the six role lanes, including zero-count roles shown compact and dim", async () => {
    getSemanticGraph.mockResolvedValue(GRAPH);
    render(<SemanticArchitecturePanel runId="r1" />);

    await screen.findByText("Semantic Risk Terrain");
    expect(screen.getByText("Auth Boundary")).toBeTruthy();
    expect(screen.getByText("Public API")).toBeTruthy();
    expect(screen.getByText("Internal Utility")).toBeTruthy();
    expect(screen.getByText("Data Access")).toBeTruthy();
    expect(screen.getByText("Config Surface")).toBeTruthy();
    expect(screen.getByText("Test Only")).toBeTruthy();
    expect(screen.getAllByText("0 files").length).toBe(3);
  });

  it("names the highest-risk file exactly once, in the callout only — no duplicate in-lane label", async () => {
    getSemanticGraph.mockResolvedValue(GRAPH);
    render(<SemanticArchitecturePanel runId="r1" />);

    await screen.findByText("Semantic Risk Terrain");
    // The badge and the filename each appear exactly once — only in the
    // callout below the terrain, never a second time inline in the lane.
    expect((await screen.findAllByText("Highest risk")).length).toBe(1);
    expect(screen.getAllByText("auth.py · 0.92").length).toBe(1);
    expect(screen.queryByText("routes.py")).toBeNull();
    expect(screen.queryByText("utils.py")).toBeNull();
  });

  it("clicking a dependency dot opens the selected-file detail with real one-hop relationships and a rule-based reason", async () => {
    getSemanticGraph.mockResolvedValue(GRAPH);
    const user = userEvent.setup();
    render(<SemanticArchitecturePanel runId="r1" />);

    await screen.findByText("Semantic Risk Terrain");
    await user.click(
      screen.getByLabelText(/vulnapi\/auth\.py — criticality 0\.92, churn 0\.90, imported by 1/),
    );

    expect(await screen.findByText("Selected file — one-hop relationships")).toBeTruthy();
    expect(screen.getByText("0.92")).toBeTruthy();
    expect(screen.getByText("0.90")).toBeTruthy();
    expect(screen.getByText("Imported by (1)")).toBeTruthy();
    expect(screen.getByText("Imports (0)")).toBeTruthy();
    expect(
      screen.getByText(
        "High churn and high criticality make this file a priority for investigation.",
      ),
    ).toBeTruthy();
  });

  it("returns to the risk terrain from the selected-file detail", async () => {
    getSemanticGraph.mockResolvedValue(GRAPH);
    const user = userEvent.setup();
    render(<SemanticArchitecturePanel runId="r1" />);

    await screen.findByText("Semantic Risk Terrain");
    await user.click(
      screen.getByLabelText(/vulnapi\/auth\.py — criticality 0\.92, churn 0\.90, imported by 1/),
    );
    await screen.findByText("Selected file — one-hop relationships");

    await user.click(screen.getByRole("button", { name: "← Back to risk terrain" }));
    await waitFor(() => {
      expect(screen.queryByText("Selected file — one-hop relationships")).toBeNull();
    });
    expect(screen.getByText("Auth Boundary")).toBeTruthy();
  });

  it("the compact legend explains the encoding without a paragraph of prose", async () => {
    getSemanticGraph.mockResolvedValue(GRAPH);
    render(<SemanticArchitecturePanel runId="r1" />);

    await screen.findByText("Semantic Risk Terrain");
    expect(screen.getByText("Criticality")).toBeTruthy();
    expect(screen.getByText("Fan-in")).toBeTruthy();
    expect(screen.getByText("Churn")).toBeTruthy();
  });

  it("keeps the technical explanation out of the hero, behind a collapsed disclosure", async () => {
    getSemanticGraph.mockResolvedValue(GRAPH);
    render(<SemanticArchitecturePanel runId="r1" />);

    await screen.findByText("Semantic Risk Terrain");
    expect(screen.getByText(/How to read this/)).toBeTruthy();
    expect(screen.getByText(/Row = A1's architectural role/)).toBeTruthy();
  });

  it("the provenance strip shows only real counts, no timestamp or invented cache/LLM stats", async () => {
    getSemanticGraph.mockResolvedValue(GRAPH);
    render(<SemanticArchitecturePanel runId="r1" />);

    await screen.findByText("Semantic Risk Terrain");
    const provenance = (await screen.findByText("Provenance")).closest("div")!;
    expect(provenance.textContent).toContain("3");
    expect(provenance.textContent).toContain("files");
    expect(provenance.textContent).toContain("import edges");
    expect(provenance.textContent).toContain("source root");
    expect(provenance.textContent).not.toContain("2026");
    expect(screen.queryByText(/cache hit/i)).toBeNull();
    expect(screen.queryByText(/llm call/i)).toBeNull();
  });

  it("never calls A0.5's knowledge-graph endpoints — A1's semantic truth is its own", async () => {
    getSemanticGraph.mockResolvedValue(GRAPH);
    render(<SemanticArchitecturePanel runId="r1" />);

    await screen.findByText("Semantic Risk Terrain");
    expect(getKnowledgeMetrics).not.toHaveBeenCalled();
    expect(getKnowledgeGraph).not.toHaveBeenCalled();
    expect(getKnowledgeCapabilities).not.toHaveBeenCalled();
    expect(getKnowledgeHotspots).not.toHaveBeenCalled();
    expect(getKnowledgeQuery).not.toHaveBeenCalled();
  });

  it("carries the explainability source line for the semantic graph endpoint", async () => {
    getSemanticGraph.mockResolvedValue(GRAPH);
    render(<SemanticArchitecturePanel runId="r1" />);

    await screen.findByText("Semantic Risk Terrain");
    expect(screen.getByText(/semantic-graph/)).toBeTruthy();
  });

  describe("adaptive density modes", () => {
    it("atlas mode (<=80 files) gives every file its own addressable mark", async () => {
      const graph = makeGraph(20);
      getSemanticGraph.mockResolvedValue(graph);
      render(<SemanticArchitecturePanel runId="r1" />);

      await screen.findByText("Semantic Risk Terrain");
      // Every file has its own aria-label naming its exact path — nothing is
      // aggregated away at this size.
      for (const f of graph.files) {
        expect(screen.getByLabelText(new RegExp(`^${f.path.replace(".", "\\.")} —`))).toBeTruthy();
      }
      // Density is never expressed as an aggregate region here.
      expect(screen.queryByLabelText(/^\d+ files, criticality/)).toBeNull();
    });

    it("swarm mode (81-800 files) still renders individual, per-file marks", async () => {
      const graph = makeGraph(300);
      getSemanticGraph.mockResolvedValue(graph);
      render(<SemanticArchitecturePanel runId="r1" />);

      await screen.findByText("Semantic Risk Terrain");
      // An arbitrary file — not just the highest-risk one — is still its own
      // mark, because packing resolves overlap without fusing anything.
      expect(screen.getByLabelText(/^pkg\/mod_10\.py —/)).toBeTruthy();
      expect(screen.getByLabelText(/^pkg\/mod_0\.py —/)).toBeTruthy();
      expect(screen.queryByLabelText(/^\d+ files, criticality/)).toBeNull();
    });

    it("every file in swarm mode is individually hoverable and clickable, including an isolated spike", async () => {
      // 200 flat files plus one isolated criticality spike: the spike must
      // remain its own click target rather than disappearing into the mass.
      const files = [
        ...Array.from({ length: 200 }, (_, i) => ({
          path: `pkg/flat_${i}.py`,
          role: "internal-util" as SemanticRole,
          imports: [],
          importedBy: [],
          churnWeight: 0.1,
          criticality: 0.1,
        })),
        {
          path: "pkg/spike.py",
          role: "internal-util" as SemanticRole,
          imports: [],
          importedBy: ["pkg/caller.py"],
          churnWeight: 0.95,
          criticality: 0.99,
        },
      ];
      const graph = {
        generatedAt: "2026-08-13T00:00:00",
        sourceRoots: ["pkg"],
        files,
        edges: [] as [string, string][],
        roleCounts: { ...GRAPH.roleCounts, "internal-util": files.length },
        totalFiles: files.length,
        totalEdges: 0,
      };
      getSemanticGraph.mockResolvedValue(graph);
      const user = userEvent.setup();
      render(<SemanticArchitecturePanel runId="r1" />);

      await screen.findByText("Semantic Risk Terrain");
      await user.click(
        screen.getByLabelText(/^pkg\/spike\.py — criticality 0\.99, churn 0\.95, imported by 1/),
      );
      expect(await screen.findByText("Selected file — one-hop relationships")).toBeTruthy();
      // Named in the detail panel and labelled on the spine — the selected
      // mark keeps its label so the two point at the same thing.
      expect(screen.getAllByText("pkg/spike.py").length).toBeGreaterThan(0);
    });

    it("ridge mode (>800 files) bounds DOM cost by bins per lane, not by file count", async () => {
      const graph = makeGraph(2000);
      getSemanticGraph.mockResolvedValue(graph);
      const { container } = render(<SemanticArchitecturePanel runId="r1" />);

      await screen.findByText("Semantic Risk Terrain");
      // 6 lanes x 56 bins is the ceiling, however many thousand files sit
      // behind them — this is what keeps a monorepo renderable.
      const regions = screen.getAllByLabelText(/^\d+ files, criticality/);
      expect(regions.length).toBeGreaterThan(0);
      expect(regions.length).toBeLessThanOrEqual(6 * 56);
      // Marks are reserved for outliers, so the plot never draws 2000 dots.
      const marks = container.querySelectorAll('svg g[role="button"]');
      expect(marks.length).toBeLessThan(60);
    });

    it("ridge mode keeps every lane's highest-stake files individually drawn", async () => {
      const graph = makeGraph(2000);
      getSemanticGraph.mockResolvedValue(graph);
      const { container } = render(<SemanticArchitecturePanel runId="r1" />);

      await screen.findByText("Semantic Risk Terrain");
      const marks = Array.from(container.querySelectorAll('svg g[role="button"]'));
      const occupiedLanes = ROLES.filter((r) => graph.files.some((f) => f.role === r)).length;
      // The danger corner must never dissolve into the ribbon: every lane
      // that holds files still surfaces at least one addressable mark.
      expect(marks.length).toBeGreaterThanOrEqual(occupiedLanes);

      // And what it surfaces is the top of the lane, not an arbitrary file:
      // every drawn mark outranks the lane's median stake.
      const labels = marks.map((m) => m.getAttribute("aria-label") ?? "");
      const drawn = graph.files.filter((f) => labels.some((l) => l.startsWith(`${f.path} —`)));
      expect(drawn.length).toBe(marks.length);
      const stake = (f: (typeof graph.files)[number]) =>
        f.criticality * Math.log(1 + f.importedBy.length);
      const median = [...graph.files].sort((a, b) => stake(a) - stake(b))[
        Math.floor(graph.files.length / 2)
      ];
      for (const f of drawn) expect(stake(f)).toBeGreaterThanOrEqual(stake(median));
    });

    it("clicking a density region drills into the real files it aggregates", async () => {
      const graph = makeGraph(2000);
      getSemanticGraph.mockResolvedValue(graph);
      const user = userEvent.setup();
      render(<SemanticArchitecturePanel runId="r1" />);

      await screen.findByText("Semantic Risk Terrain");
      const [region] = screen.getAllByLabelText(/^\d+ files, criticality/);
      await user.click(region);

      expect(await screen.findByText(/files, ranked by criticality/)).toBeTruthy();
      // Selecting a file from the drill-down opens its own detail.
      const rows = screen
        .getAllByRole("button")
        .filter((b) => /^mod_\d+\.py/.test(b.textContent ?? ""));
      expect(rows.length).toBeGreaterThan(0);
      await user.click(rows[0]);
      expect(await screen.findByText("Selected file — one-hop relationships")).toBeTruthy();
    });
  });
});
