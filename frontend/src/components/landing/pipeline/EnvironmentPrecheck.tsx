import { cn } from '@/lib/utils'

const advance = (p: number, from: number, to: number) =>
  Math.max(0, Math.min(1, (p - from) / (to - from)))

const probes = [
  {
    file: 'pyproject.toml',
    hint: 'project manifest',
    result: 'Python project',
    detail: 'language identified',
    start: 0.08,
    end: 0.28
  },
  {
    file: 'pytest',
    hint: 'runner probe',
    result: 'Runner available',
    detail: 'command resolved',
    start: 0.3,
    end: 0.5
  },
  {
    file: 'tests/',
    hint: 'test discovery',
    result: 'Collection ready',
    detail: 'test cases discovered',
    start: 0.52,
    end: 0.76
  }
]

/** A source-to-evidence inspection, driven entirely by the timeline's scroll. */
export function EnvironmentPrecheck({ progress: p }: { progress: number }) {
  const completed = probes.filter((probe) => p >= probe.end).length
  const ready = p >= 0.88
  const command = 'python -m pytest --collect-only -q'
  return (
    <div
      data-testid="environment-precheck"
      className="relative mx-auto w-full max-w-[480px]"
    >
      <div className="mb-3 flex items-center justify-between gap-3">
        <div>
          <span className="text-[9px] uppercase tracking-[0.16em] text-muted-foreground">
            Repository inspection
          </span>
          <p className="mt-1 text-sm text-foreground">
            vulnapi<span className="text-muted-foreground"> /</span>
          </p>
        </div>
        <div
          className="flex gap-1.5"
          aria-label={`${completed} of 3 environment checks complete`}
        >
          {probes.map((probe, i) => (
            <span
              key={probe.file}
              className={cn(
                'h-1.5 w-5 rounded-full',
                completed > i ? 'bg-emerald-400' : 'bg-border'
              )}
            />
          ))}
        </div>
      </div>

      <div className="mb-2 grid grid-cols-[minmax(0,1fr)_32px_minmax(0,1fr)] text-[8px] uppercase tracking-[0.12em] text-muted-foreground">
        <span>Inspect</span>
        <span />
        <span>Establish</span>
      </div>
      <div className="relative space-y-2">
        {probes.map((probe) => {
          const progress = advance(p, probe.start, probe.end)
          const done = progress === 1
          const active = progress > 0 && !done
          return (
            <div
              key={probe.file}
              data-probe-state={
                done ? 'complete' : active ? 'inspecting' : 'pending'
              }
              className="grid grid-cols-[minmax(0,1fr)_32px_minmax(0,1fr)] items-center"
            >
              <div
                className={cn(
                  'relative overflow-hidden rounded-l-md border border-r-0 px-2.5 py-2',
                  active
                    ? 'border-sky-300/40 bg-sky-300/5'
                    : 'border-border bg-secondary/20'
                )}
              >
                <div
                  aria-hidden
                  className="absolute inset-y-0 left-0 bg-sky-300/10"
                  style={{ width: `${progress * 100}%`, opacity: done ? 0 : 1 }}
                />
                <span className="relative block truncate text-[10px] text-foreground">
                  {probe.file}
                </span>
                <span className="relative hidden min-[360px]:block text-[8px] text-muted-foreground">
                  {probe.hint}
                </span>
              </div>
              <svg viewBox="0 0 32 40" className="h-10 w-8" aria-hidden>
                <path
                  d="M0 20 H32"
                  stroke="currentColor"
                  className="text-border"
                />
                <path
                  d="M0 20 H32"
                  stroke={done ? '#34d399' : '#7dd3fc'}
                  pathLength="1"
                  strokeDasharray="1"
                  strokeDashoffset={1 - progress}
                />
                {active && (
                  <circle cx={progress * 32} cy="20" r="2.5" fill="#7dd3fc" />
                )}
              </svg>
              <div
                className={cn(
                  'rounded-r-md border px-2.5 py-2',
                  done
                    ? 'border-emerald-400/25 bg-emerald-400/5'
                    : 'border-border border-dashed'
                )}
              >
                <span
                  className={cn(
                    'block text-[10px]',
                    done ? 'text-emerald-300' : 'text-muted-foreground'
                  )}
                >
                  {done
                    ? probe.result
                    : active
                      ? 'Inspecting…'
                      : 'Awaiting probe'}
                </span>
                <span className="hidden min-[360px]:block text-[8px] text-muted-foreground">
                  {done ? probe.detail : 'not established yet'}
                </span>
              </div>
            </div>
          )
        })}
      </div>

      <div
        className="mt-4 border-y border-border py-2.5"
        aria-label="Test collection command"
      >
        <span className="block text-[8px] uppercase tracking-[0.12em] text-muted-foreground">
          Probe · collect tests without running them
        </span>
        <code className="mt-1 block min-h-8 break-words text-[10px] leading-4 text-sky-200">
          <span aria-hidden className="mr-1 text-muted-foreground">
            ›
          </span>
          {command.slice(0, Math.ceil(command.length * advance(p, 0.3, 0.52)))}
          {p >= 0.3 && p < 0.52 && <span aria-hidden>▌</span>}
        </code>
      </div>

      <div
        className="mt-3 flex items-start gap-2.5"
        data-precheck-ready={ready}
      >
        <span
          className={cn(
            'mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[10px]',
            ready
              ? 'border-emerald-400/40 text-emerald-300'
              : 'border-border text-muted-foreground'
          )}
        >
          {ready ? '↗' : '·'}
        </span>
        <div>
          <p
            className={cn(
              'text-[11px]',
              ready ? 'text-emerald-300' : 'text-muted-foreground'
            )}
          >
            {ready
              ? 'Ready to reproduce the failure'
              : 'Establishing execution readiness'}
          </p>
          <p className="mt-1 text-[9px] text-muted-foreground">
            {ready
              ? 'Next: run the reported behavior. Bug still untested.'
              : 'Environment checks do not establish a fix.'}
          </p>
        </div>
      </div>
    </div>
  )
}
