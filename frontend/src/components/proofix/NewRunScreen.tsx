import { useEffect, useMemo, useState } from "react";
import { ArrowRight, Check, Github, Info, Loader2, AlertCircle } from "lucide-react";
import { isLive, parseGithubUrl, validateRepository } from "@/lib/runService";
import type { RepoMetadata } from "@/mocks";

const SAMPLE_REPOSITORIES = {
  Repo_bot: "https://github.com/CHEZHIYAN0277/Repo_bot.git",
  OrbitBoard: "https://github.com/Kaushika811/orbitboard.git",
  NovaAuth: "https://github.com/Kaushika811/NovaAuth.git",
  "Secure-auth": "https://github.com/Kaushika811/Secure-auth.git",
  TaskForge: "https://github.com/Kaushika811/TaskForge.git",
} as const;

export function NewRunScreen({ onAnalyze, initialUrl = "", launchError }: {
  onAnalyze: (url: string) => void | Promise<unknown>;
  initialUrl?: string;
  launchError?: string | null;
}) {
  const [url, setUrl] = useState(initialUrl);
  const [meta, setMeta] = useState<RepoMetadata | null>(null);
  const [loadingMeta, setLoadingMeta] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [launching, setLaunching] = useState(false);

  const parsed = useMemo(() => parseGithubUrl(url), [url]);
  const sampleRepositories = useMemo(
    () => Object.entries(SAMPLE_REPOSITORIES).filter(([, repositoryUrl]) => repositoryUrl),
    [],
  );
  // Against a live backend a local repository path (e.g. `vulnapi`) is a valid
  // target too, so launching is not gated on a parseable GitHub URL.
  const canSubmit = !!parsed || (isLive && url.trim().length > 0);

  useEffect(() => {
    setError(null);
    if (!parsed) {
      setMeta(null);
      setLoadingMeta(false);
      return;
    }
    let cancelled = false;
    const t = setTimeout(async () => {
      setLoadingMeta(true);
      try {
        const result = await validateRepository(url);
        if (cancelled) return;
        if (!result) {
          setMeta(null);
          setError("Repository not found. Check the URL and try again.");
        } else {
          setMeta(result);
        }
      } catch {
        if (cancelled) return;
        setMeta(null);
        setError("Could not reach the repository service. You can still launch analysis.");
      } finally {
        if (!cancelled) setLoadingMeta(false);
      }
    }, 350);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [parsed?.owner, parsed?.name, url, parsed]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit || launching) return;
    setLaunching(true);
    try {
      await new Promise((resolve) => setTimeout(resolve, 450));
      await onAnalyze(url.trim());
    } finally {
      // Fast API failures may leave this component mounted. Always restore
      // the control, including consecutive failures with the same message.
      setLaunching(false);
    }
  };

  const canLaunch = canSubmit && !launching;

  return (
    <div className="relative min-h-[calc(100vh-3rem)] overflow-hidden px-6 py-20 sm:py-24">
      {/* Ambient glow */}
      <div
        aria-hidden
        className="pointer-events-none absolute left-1/2 top-[18%] -z-0 h-[520px] w-[720px] -translate-x-1/2 rounded-full opacity-60 blur-3xl"
        style={{
          background: "radial-gradient(closest-side, hsl(var(--ink) / 0.04), transparent 70%)",
        }}
      />
      <div
        aria-hidden
        className="pointer-events-none absolute left-1/2 top-[42%] -z-0 h-[360px] w-[520px] -translate-x-1/2 rounded-full opacity-50 blur-3xl"
        style={{
          background: "radial-gradient(closest-side, hsl(var(--ink) / 0.03), transparent 70%)",
        }}
      />

      <div className="relative mx-auto w-full max-w-[960px]">
        <div
          className={`rounded-2xl border border-border bg-surface/80 p-8 shadow-[0_8px_40px_-12px_rgba(0,0,0,0.25)] backdrop-blur-sm transition-all duration-500 ease-out sm:p-14 ${
            launching ? "scale-[0.985] opacity-70 blur-[1px]" : "scale-100 opacity-100"
          }`}
        >
          {/* Header */}
          <div className="text-center">
            <h1 className="text-[34px] font-semibold tracking-[-0.02em] text-ink sm:text-[42px] sm:leading-[1.1]">
              Start Autonomous Repository Analysis
            </h1>
            <p className="mx-auto mt-5 max-w-2xl text-[15px] leading-relaxed text-ink-soft sm:text-base">
              Analyze repositories with autonomous AI agents that detect, repair, validate, and
              explain software issues.
            </p>
          </div>

          {/* Input */}
          <form onSubmit={submit} className="mx-auto mt-10 max-w-xl">
            {launchError && (
              <div role="alert" className="mb-4 rounded-lg border border-status-failed/30 bg-status-failed-bg/40 px-3 py-2 text-sm text-status-failed">
                Could not start the run: {launchError} Try launching again.
              </div>
            )}
            <div
              className={`group flex items-center gap-2.5 rounded-xl border bg-surface px-4 py-3.5 transition-[border-color,box-shadow,transform] duration-500 ease-out ${
                parsed
                  ? "border-primary/50 shadow-[0_0_0_5px_hsl(var(--primary)/0.10)]"
                  : "border-border focus-within:border-primary/40 focus-within:shadow-[0_0_0_5px_hsl(var(--primary)/0.08)]"
              }`}
            >
              <Github className="h-4 w-4 text-ink-soft" strokeWidth={1.75} />
              <input
                autoFocus
                // `text`, not `url`: live backends also accept local paths,
                // which the browser's url validator would reject.
                type="text"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder={
                  isLive
                    ? "https://github.com/owner/repo — or a local path"
                    : "https://github.com/owner/repository"
                }
                className="flex-1 bg-transparent font-mono text-sm text-ink outline-none placeholder:text-ink-soft/70"
                disabled={launching}
              />
              {loadingMeta && <Loader2 className="h-4 w-4 animate-spin text-ink-soft" />}
              {meta && !loadingMeta && (
                <span
                  key={`${meta.owner}/${meta.name}`}
                  className="inline-flex h-4 w-4 animate-line-in items-center justify-center rounded-full border-2 border-status-completed text-status-completed"
                >
                  <Check className="h-2.5 w-2.5" strokeWidth={2} />
                </span>
              )}
            </div>

            {/* Inline error */}
            {error && !meta && (
              <div className="mt-3 flex animate-line-in items-center gap-2 rounded-lg border border-status-failed/30 bg-status-failed-bg/40 px-3 py-2 text-sm text-status-failed">
                <AlertCircle className="h-4 w-4" />
                <span>{error}</span>
              </div>
            )}

            {/* Repo preview */}
            {meta && <RepoPreview meta={meta} />}

            {/* Launch button */}
            <button
              type="submit"
              disabled={!canLaunch}
              className="group/btn relative mt-8 flex w-full items-center justify-center gap-2 overflow-hidden rounded-xl bg-primary px-4 py-4 text-[15px] font-medium text-primary-foreground shadow-[0_1px_2px_hsl(var(--primary)/0.08),0_6px_16px_-8px_hsl(var(--primary)/0.35)] transition-all duration-[250ms] ease-out hover:-translate-y-[1px] hover:bg-primary/90 hover:shadow-[0_1px_2px_hsl(var(--primary)/0.08),0_10px_24px_-10px_hsl(var(--primary)/0.40)] active:translate-y-0 active:scale-[0.995] active:duration-150 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:translate-y-0"
            >
              {launching ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  <span className="animate-line-in">Launching Autonomous Engineer…</span>
                </>
              ) : (
                <>
                  Launch Autonomous Analysis
                  <ArrowRight
                    className="h-4 w-4 transition-transform duration-[250ms] group-hover/btn:translate-x-0.5"
                    strokeWidth={2}
                  />
                </>
              )}
            </button>

            <div className="mt-5 rounded-xl border border-border bg-surface-muted/30 px-4 py-4 text-left">
              <div className="flex items-center gap-2 text-[11px] font-medium uppercase tracking-[0.14em] text-ink">
                <Info className="h-3.5 w-3.5 text-ink-soft" strokeWidth={1.75} />
                Quick Start
              </div>
              <div className="mt-3 space-y-1.5 text-[13px] leading-relaxed text-ink-soft">
                <p>Currently supports Python repositories.</p>
                <p>Use a public GitHub repository containing Python code.</p>
                <p>For the fastest demo, choose one of the verified sample repositories below.</p>
              </div>

              {sampleRepositories.length > 0 && (
                <>
                  <div className="mt-4 text-[10px] font-medium uppercase tracking-[0.14em] text-ink-soft">
                    Sample Repositories
                  </div>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {sampleRepositories.map(([name, repositoryUrl]) => (
                      <button
                        key={name}
                        type="button"
                        disabled={launching}
                        onClick={() => setUrl(repositoryUrl)}
                        className="rounded-lg border border-border bg-surface px-3 py-1.5 text-xs font-medium text-ink transition-colors hover:border-primary/40 hover:bg-surface-muted disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {name}
                      </button>
                    ))}
                  </div>
                </>
              )}

              <p className="mt-4 text-[12px] leading-relaxed text-ink-soft">
                Recommended: a repository with tests and reproducible bugs.
              </p>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
}

function RepoPreview({ meta }: { meta: RepoMetadata }) {
  const rows: { label: string; value: string }[] = [
    { label: "Language", value: meta.language ?? "—" },
    { label: "Branch", value: meta.branch ?? "—" },
    { label: "Visibility", value: meta.visibility },
    { label: "Status", value: "Ready to Analyze" },
  ];
  return (
    <div className="mt-5 animate-line-in rounded-xl border border-border bg-surface p-5">
      <div className="flex items-center gap-3">
        <div className="text-[11px] font-medium uppercase tracking-[0.14em] text-ink-soft">
          Repository Preview
        </div>
        <span className="inline-flex items-center gap-1.5 rounded-full bg-status-completed-bg px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-status-completed">
          <span className="h-1.5 w-1.5 rounded-full bg-current" />
          Detected
        </span>
      </div>

      <div className="mt-3 flex items-center gap-3">
        <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-surface-muted">
          <Github className="h-4 w-4 text-ink" />
        </span>
        <div className="min-w-0">
          <div className="truncate font-mono text-[15px] font-semibold tracking-tight text-ink">
            {meta.name}
          </div>
          <div className="truncate font-mono text-xs text-ink-soft">
            github.com/{meta.owner}/{meta.name}
          </div>
        </div>
      </div>
      <dl className="mt-5 grid grid-cols-2 divide-x divide-y divide-border overflow-hidden rounded-lg border border-border bg-surface-muted/40 sm:grid-cols-4 sm:divide-y-0">
        {rows.map((r) => (
          <div key={r.label} className="px-3.5 py-2.5">
            <dt className="text-[10px] font-medium uppercase tracking-[0.12em] text-ink-soft">
              {r.label}
            </dt>
            <dd className="mt-1 truncate text-[13px] font-semibold text-ink" title={r.value}>
              {r.value}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
