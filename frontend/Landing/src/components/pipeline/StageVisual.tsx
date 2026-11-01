import { useEffect, useState, type ReactNode } from 'react'
import { useMotionValueEvent, type MotionValue } from 'framer-motion'
import { EnvironmentPrecheck } from './EnvironmentPrecheck'
import { cn } from '@/lib/utils'
import { useReducedMotionPreference } from '@/hooks/useReducedMotionPreference'

type Props = { index: number; progress: MotionValue<number> }
type Tone = 'neutral' | 'good' | 'bad' | 'warn' | 'blue'
const tones: Record<Tone, string> = {
  neutral: 'text-muted-foreground border-border bg-secondary/30',
  good: 'text-emerald-400 border-emerald-400/25 bg-emerald-400/10',
  bad: 'text-rose-400 border-rose-400/25 bg-rose-400/10',
  warn: 'text-amber-300 border-amber-300/25 bg-amber-300/10',
  blue: 'text-sky-300 border-sky-300/25 bg-sky-300/10'
}
const clamp = (n: number) => Math.max(0, Math.min(1, n))
const segment = (p: number, start: number, end = start + 0.16) =>
  clamp((p - start) / (end - start))
const typed = (text: string, p: number) =>
  text.slice(0, Math.ceil(text.length * clamp(p)))

function Badge({
  children,
  tone = 'neutral'
}: {
  children: ReactNode
  tone?: Tone
}) {
  return (
    <span
      className={cn(
        'inline-flex rounded border px-1.5 py-0.5 text-[9px] leading-relaxed',
        tones[tone]
      )}
    >
      {children}
    </span>
  )
}

function Reveal({
  p,
  at,
  children,
  className
}: {
  p: number
  at: number
  children: ReactNode
  className?: string
}) {
  const shown = segment(p, at, at + 0.1)
  return (
    <div
      aria-hidden={shown === 0}
      className={className}
      style={{ opacity: shown, transform: `translateY(${(1 - shown) * 7}px)` }}
    >
      {children}
    </div>
  )
}

function Code({
  children,
  tone = 'neutral',
  sign = ' ',
  line
}: {
  children: ReactNode
  tone?: Tone
  sign?: string
  line?: number
}) {
  return (
    <div
      className={cn(
        'flex min-h-7 items-start gap-2 rounded-sm border-l-2 px-2 py-1',
        tones[tone]
      )}
    >
      {line !== undefined && (
        <span
          aria-hidden
          className="w-4 shrink-0 select-none text-right opacity-40"
        >
          {line}
        </span>
      )}
      <span aria-hidden className="w-2 shrink-0 select-none">
        {sign}
      </span>
      <code className="min-w-0 whitespace-pre-wrap break-words">
        {children || '\u00a0'}
      </code>
    </div>
  )
}

function Outcome({
  p,
  children,
  tone = 'good'
}: {
  p: number
  children: ReactNode
  tone?: Tone
}) {
  return (
    <Reveal
      p={p}
      at={0.82}
      className={cn(
        'mt-4 rounded-lg border px-3 py-2.5 leading-relaxed',
        tones[tone]
      )}
    >
      {children}
    </Reveal>
  )
}

const scenes = [
  ['Locate', 'Probe', 'Ready'],
  ['Discover', 'Index', 'Mapped'],
  ['Parse', 'Connect', 'Understand'],
  ['Manifest', 'Lookup', 'Reachability'],
  ['Read', 'Scan', 'Findings'],
  ['Run test', 'Trace', 'Failure'],
  ['Gather', 'Correlate', 'Locate'],
  ['Origin', 'Expand', 'Scope'],
  ['Gather', 'Select', 'Package'],
  ['Constraints', 'Order', 'Plan'],
  ['Original', 'Rewrite', 'Candidate'],
  ['Tests', 'Mutate', 'Challenge'],
  ['Baseline', 'Rescan', 'Compare'],
  ['Evidence', 'Gates', 'Review']
]

export function StageVisual({ index, progress }: Props) {
  const reduced = useReducedMotionPreference()
  const [position, setPosition] = useState(() => progress.get())
  const [manualPosition, setManualPosition] = useState<number | null>(null)
  useEffect(() => {
    if (manualPosition === null) return
    const resume = () => setManualPosition(null)
    const onKeyDown = (event: KeyboardEvent) => {
      if (
        ['ArrowDown', 'ArrowUp', 'PageDown', 'PageUp', 'Home', 'End'].includes(
          event.key
        )
      )
        resume()
    }
    // Focus and layout can scroll the page after a step is selected. Resume only
    // on user input so those adjustments do not overwrite the selected step.
    window.addEventListener('wheel', resume, { passive: true })
    window.addEventListener('touchmove', resume, { passive: true })
    window.addEventListener('pointerdown', resume)
    window.addEventListener('keydown', onKeyDown)
    return () => {
      window.removeEventListener('wheel', resume)
      window.removeEventListener('touchmove', resume)
      window.removeEventListener('pointerdown', resume)
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [manualPosition])
  useMotionValueEvent(progress, 'change', (value) => {
    // Quantize React updates; movement still follows the scroll in either direction.
    setPosition(Math.round(clamp(value) * 100) / 100)
  })
  const p = reduced ? 1 : (manualPosition ?? position)
  const labels = scenes[index]
  const phase =
    index === 10
      ? p < 0.2
        ? 0
        : p < 0.94
          ? 1
          : 2
      : p < 0.3
        ? 0
        : p < 0.8
          ? 1
          : 2

  if (index === 0)
    return (
      <div
        data-stage-visual="1"
        data-progress={Math.round(p * 100)}
        className="rounded-xl border border-border bg-background p-3 font-mono text-[11px] leading-relaxed sm:p-5"
      >
        <div className="mb-4 flex justify-between border-b border-border pb-2 text-[9px] text-muted-foreground">
          <span>ILLUSTRATIVE INSPECTION</span>
          <span>{reduced ? 'Static view' : 'Scroll to inspect'}</span>
        </div>
        <EnvironmentPrecheck progress={p} />
        <div
          role="group"
          aria-label="Inspection steps"
          className="mt-4 flex justify-between border-t border-border pt-2"
        >
          {labels.map((label, i) => (
            <button
              key={label}
              type="button"
              disabled={reduced}
              aria-pressed={phase === i}
              onClick={() => setManualPosition([0.05, 0.5, 1][i])}
              className={cn(
                'min-h-8 px-2 text-[9px] focus-visible:outline focus-visible:outline-sky-300',
                phase === i ? 'text-sky-300' : 'text-muted-foreground'
              )}
            >
              0{i + 1} {label}
            </button>
          ))}
        </div>
      </div>
    )

  return (
    <div
      data-stage-visual={index + 1}
      data-progress={Math.round(p * 100)}
      className="overflow-hidden rounded-xl border border-border bg-background/80 font-mono text-[11px] leading-relaxed shadow-sm"
    >
      <div className="flex items-center justify-between gap-2 border-b border-border bg-secondary/30 px-3 py-2">
        <span className="text-[9px] uppercase tracking-[0.16em] text-muted-foreground">
          Illustrative repair
        </span>
        <span className="text-[9px] text-muted-foreground">
          {reduced
            ? 'Static view'
            : manualPosition !== null
              ? 'Scroll to resume'
              : 'Scroll to explore'}
        </span>
      </div>
      <div
        role="group"
        aria-label="Animation steps"
        className="grid grid-cols-3 border-b border-border"
      >
        {labels.map((label, i) => (
          <button
            key={label}
            type="button"
            disabled={!!reduced}
            aria-pressed={phase === i}
            onClick={() => setManualPosition([0.1, 0.55, 1][i])}
            className={cn(
              'min-h-10 px-1 py-2 text-[9px] transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-sky-400 disabled:cursor-default',
              phase === i
                ? 'bg-sky-400/10 text-sky-300'
                : 'text-muted-foreground hover:text-foreground'
            )}
          >
            <span className="mr-1 opacity-50">0{i + 1}</span>
            {label}
          </button>
        ))}
      </div>
      <div className="relative min-h-[320px] p-3 sm:p-4">
        <Scene index={index} p={p} />
      </div>
      <div aria-hidden className="h-0.5 bg-border">
        <div
          className="h-full origin-left bg-sky-400/70"
          style={{ transform: `scaleX(${p})` }}
        />
      </div>
    </div>
  )
}

function Scene({ index, p }: { index: number; p: number }) {
  switch (index) {
    case 0:
      return <EnvironmentPrecheck progress={p} />
    case 1: {
      const files = [
        'vulnapi/auth.py',
        'vulnapi/middleware.py',
        'vulnapi/api.py',
        'tests/test_auth.py',
        'tests/test_api.py',
        'pyproject.toml'
      ]
      const scanned = Math.min(files.length, Math.floor(p * 8))
      return (
        <>
          <div className="mb-3 flex justify-between">
            <span>vulnapi/</span>
            <Badge tone="blue">{scanned} / 6 example files</Badge>
          </div>
          {files.map((file, i) => (
            <Reveal
              key={file}
              p={p}
              at={i * 0.1}
              className="flex items-center gap-2 border-b border-border/50 py-2"
            >
              <span className="text-muted-foreground">
                {i === 5 ? '└' : '├'}
              </span>
              <span className="min-w-0 flex-1 break-all">{file}</span>
              <span
                className={scanned > i ? 'text-emerald-400' : 'text-sky-300'}
              >
                {scanned > i ? '✓' : '…'}
              </span>
            </Reveal>
          ))}
          <Outcome p={p}>
            Files → symbols → a reusable repository index.
          </Outcome>
        </>
      )
    }
    case 2:
      return (
        <>
          <Code>def validate_token(token):</Code>
          <div className="my-3 border-l border-sky-400/30 pl-3">
            {[
              ['decode_token()', 'Reads the payload'],
              ['validate_token()', 'Checks the subject'],
              ['auth_middleware()', 'Uses the validation result']
            ].map(([symbol, meaning], i) => (
              <Reveal
                key={symbol}
                p={p}
                at={0.12 + i * 0.19}
                className="mb-2 rounded border border-border bg-secondary/20 p-2"
              >
                <span className="text-sky-300">{symbol}</span>
                <div className="mt-1 text-[10px] text-muted-foreground">
                  {meaning}
                </div>
              </Reveal>
            ))}
          </div>
          <Outcome p={p} tone="blue">
            Auth boundary identified. Role and relationships become context for
            investigation.
          </Outcome>
        </>
      )
    case 3:
      return (
        <>
          <p className="mb-3 text-muted-foreground">
            pyproject.toml → advisory lookup
          </p>
          <Code>dependencies = ["flask&gt;=3.0.0"]</Code>
          <Reveal p={p} at={0.2} className="my-4 flex items-center gap-3">
            <Badge tone="blue">PyPI package</Badge>
            <span>→</span>
            <Badge>OSV lookup</Badge>
          </Reveal>
          <Reveal
            p={p}
            at={0.42}
            className="rounded-lg border border-amber-300/25 bg-amber-300/5 p-3"
          >
            <div className="text-amber-300">
              Exact installed version unknown
            </div>
            <p className="mt-2 text-muted-foreground">
              A version range is not an installed version. An empty lookup does
              not establish safety.
            </p>
          </Reveal>
          <Outcome p={p} tone="warn">
            Dependency evidence stays qualified. Continue investigating the
            observed token failure.
          </Outcome>
        </>
      )
    case 4:
      return (
        <>
          <p className="mb-3 text-muted-foreground">
            vulnapi/api.py · baseline scan
          </p>
          <Code line={1}>def lookup_user(user_id):</Code>
          <Code line={2} tone={p > 0.4 ? 'bad' : 'neutral'}>
            {'    query = f"SELECT * FROM users WHERE id = {user_id}"'}
          </Code>
          <Code line={3}> return conn.execute(query)</Code>
          <div
            aria-hidden
            className="my-4 h-1 overflow-hidden rounded bg-border"
          >
            <div
              className="h-full bg-amber-300"
              style={{ width: `${segment(p, 0.12, 0.48) * 100}%` }}
            />
          </div>
          <Reveal p={p} at={0.45}>
            <Badge tone="warn">SQL interpolation · baseline finding</Badge>
          </Reveal>
          <Outcome p={p} tone="warn">
            Record this existing finding. It is separate from the token-expiry
            failure.
          </Outcome>
        </>
      )
    case 5:
      return (
        <>
          <Code sign="$" tone="blue">
            {typed('pytest tests/test_auth.py -k expired', segment(p, 0, 0.26))}
          </Code>
          <Reveal p={p} at={0.28} className="my-4">
            <Code>token = make_token(exp=now - 3600)</Code>
            <Code>assert validate_token(token) is False</Code>
          </Reveal>
          <Reveal p={p} at={0.5}>
            <Code tone="bad" sign="E">
              assert True is False
            </Code>
            <div className="mt-2 break-all text-rose-400">
              FAILED test_expired_token_rejected
            </div>
          </Reveal>
          <Outcome p={p} tone="bad">
            Failure reproduced: an expired token is accepted. This is the
            behavior the patch must change.
          </Outcome>
        </>
      )
    case 6:
      return (
        <>
          <div className="grid grid-cols-2 gap-2">
            {['Failing assertion', 'Source citation'].map((label, i) => (
              <Reveal
                key={label}
                p={p}
                at={0.1 + i * 0.18}
                className="rounded border border-border p-2 text-sky-300"
              >
                {label}
              </Reveal>
            ))}
          </div>
          <Reveal p={p} at={0.32} className="my-4">
            <Code>payload = decode_token(token)</Code>
            <Code>if "sub" not in payload:</Code>
            <Code> return False</Code>
            <Code tone={p > 0.55 ? 'bad' : 'neutral'}>return True</Code>
          </Reveal>
          <Outcome p={p} tone="warn">
            Subject checked. Expiry unchecked. The failure and source point to
            the same missing condition.
          </Outcome>
        </>
      )
    case 7:
      return (
        <>
          <p className="text-muted-foreground">
            Import reachability · not proof of breakage
          </p>
          <div className="relative mt-4 space-y-0">
            {[
              ['auth.py', 'Change origin', 0],
              ['middleware.py', 'Direct importer · hop 1', 0.27],
              ['api.py', 'Reachable importer · hop 2', 0.53]
            ].map(([file, label, at], i) => (
              <div key={file} style={{ paddingLeft: i * 20 }}>
                {i > 0 && (
                  <div
                    aria-hidden
                    className="ml-4 w-px bg-sky-400/40"
                    style={{ height: 22 * segment(p, Number(at) - 0.12) }}
                  />
                )}
                <Reveal
                  p={p}
                  at={Number(at)}
                  className={cn(
                    'rounded-lg border p-3',
                    i === 0 ? tones.blue : tones.neutral
                  )}
                >
                  <div>{file}</div>
                  <div className="mt-1 text-[9px] text-muted-foreground">
                    {label}
                  </div>
                </Reveal>
              </div>
            ))}
          </div>
          <Outcome p={p} tone="blue">
            Patch auth.py. Inspect connected files; do not widen the edit
            automatically.
          </Outcome>
        </>
      )
    case 8:
      return (
        <>
          <p className="mb-3 text-muted-foreground">
            Select context for the expiry check
          </p>
          {[
            'auth.py',
            'tests/test_auth.py',
            'middleware.py',
            'api.py',
            'tests/test_api.py',
            'pyproject.toml'
          ].map((file, i) => {
            const relevant = i < 3
            const filtered = p > 0.35 && !relevant
            return (
              <div
                key={file}
                className={cn(
                  'mb-1 flex items-center justify-between gap-1 rounded border px-2 py-1.5',
                  p > 0.35 && relevant ? tones.blue : tones.neutral
                )}
                style={{ opacity: filtered ? 0.3 : 1 }}
              >
                <span className={cn('break-all', filtered && 'line-through')}>
                  {file}
                </span>
                <span className="shrink-0 text-[9px]">
                  {p > 0.35 ? (relevant ? 'KEEP' : 'OMIT') : 'CANDIDATE'}
                </span>
              </div>
            )
          })}
          <Outcome p={p} tone="blue">
            6 example files → 3 relevant files. Target + failing test +
            immediate caller.
          </Outcome>
        </>
      )
    case 9:
      return (
        <>
          <Badge tone="blue">Constraint: preserve valid-token behavior</Badge>
          <div className="mt-4 space-y-3">
            {[
              'Read the expiry timestamp',
              'Reject expired tokens',
              'Preserve subject validation',
              'Validate the candidate'
            ].map((task, i) => (
              <Reveal
                key={task}
                p={p}
                at={0.06 + i * 0.18}
                className="flex items-center gap-3"
              >
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-sky-400/30 text-sky-300">
                  {i + 1}
                </span>
                <span className="border-b border-border pb-2">{task}</span>
              </Reveal>
            ))}
          </div>
          <Outcome p={p} tone="blue">
            Ordered plan ready. Validation remains a separate check after
            generation.
          </Outcome>
        </>
      )
    case 10: {
      const replacing = p >= 0.2
      const additions = [
        ['import time', 0.42, 0.53],
        ['    expires_at = payload.get("exp", 0)', 0.58, 0.73],
        ['    return expires_at > time.time()', 0.78, 0.93]
      ] as const
      return (
        <>
          <div className="mb-3 flex items-center justify-between gap-1">
            <span>vulnapi/auth.py</span>
            <Badge
              tone={p >= 0.94 ? 'good' : replacing && p < 0.42 ? 'bad' : 'blue'}
            >
              {p >= 0.94
                ? 'Candidate diff'
                : p >= 0.42
                  ? 'Generating'
                  : replacing
                    ? 'Remove faulty line'
                    : 'Original'}
            </Badge>
          </div>
          <div className="space-y-0.5" data-testid="patch-code">
            <div
              style={{ visibility: p > additions[0][1] ? 'visible' : 'hidden' }}
              aria-hidden={p <= additions[0][1]}
            >
              <Code tone="good" sign="+">
                {typed(
                  additions[0][0],
                  segment(p, additions[0][1], additions[0][2])
                )}
              </Code>
            </div>
            <Code>def validate_token(token):</Code>
            <Code> payload = decode_token(token)</Code>
            <Code> if not payload or "sub" not in payload:</Code>
            <Code> return False</Code>
            <Code
              tone={replacing ? 'bad' : 'neutral'}
              sign={replacing ? '−' : ' '}
            >
              <span className={replacing ? 'line-through' : ''}>
                {' '}
                return True
              </span>
            </Code>
            {additions.slice(1).map(([code, at, end]) => (
              <div
                key={code}
                style={{ visibility: p > at ? 'visible' : 'hidden' }}
                aria-hidden={p <= at}
              >
                <Code tone="good" sign="+">
                  {typed(code, segment(p, at, end))}
                </Code>
              </div>
            ))}
          </div>
          <Outcome p={segment(p, 0.93, 1)}>
            +3 / −1 · one file. Candidate generated; correctness still needs
            validation.
          </Outcome>
        </>
      )
    }
    case 11:
      return (
        <>
          <p className="mb-3 text-muted-foreground">
            Check the patch. Then challenge the tests.
          </p>
          <Reveal p={p} at={0.08}>
            <Code tone="good">✓ expired token rejected</Code>
            <Code tone="good">✓ valid token still accepted</Code>
          </Reveal>
          <Reveal p={p} at={0.32} className="my-3">
            <Badge tone="warn">Inject deliberate faults</Badge>
          </Reveal>
          {[
            'Remove expiry guard',
            'Reverse the comparison',
            'Accept the expiry boundary'
          ].map((label, i) => (
            <Reveal
              key={label}
              p={p}
              at={0.38 + i * 0.12}
              className="flex items-center justify-between gap-2 border-b border-border py-2"
            >
              <span>{label}</span>
              <Badge tone={p > 0.52 + i * 0.12 ? 'good' : 'warn'}>
                {p > 0.52 + i * 0.12 ? 'KILLED' : 'TESTING'}
              </Badge>
            </Reveal>
          ))}
          <Outcome p={p}>
            3 / 3 example mutants caught. Tests detect these faults; this is not
            a correctness guarantee.
          </Outcome>
        </>
      )
    case 12:
      return (
        <>
          <div className="grid grid-cols-2 gap-2">
            <div className="rounded-lg border border-border p-3">
              <div className="mb-3 text-[9px] text-muted-foreground">
                BASELINE
              </div>
              <Badge tone="warn">SQL interpolation</Badge>
            </div>
            <div className="rounded-lg border border-border p-3">
              <div className="mb-3 text-[9px] text-muted-foreground">
                AFTER PATCH
              </div>
              <Reveal p={p} at={0.35}>
                <Badge tone="warn">SQL interpolation</Badge>
              </Reveal>
            </div>
          </div>
          <Reveal p={p} at={0.52} className="my-4 space-y-2">
            <div className="flex justify-between">
              <span>Existing finding</span>
              <span className="text-amber-300">1 unchanged</span>
            </div>
            <div className="flex justify-between">
              <span>New findings</span>
              <span className="text-emerald-400">0 detected</span>
            </div>
          </Reveal>
          <Outcome p={p}>
            No new finding in this example scan. The existing SQL issue remains
            visible for review.
          </Outcome>
        </>
      )
    default:
      return (
        <>
          {[
            ['Original failure', 'Reproduced'],
            ['Target behavior', 'Passes after patch'],
            ['Mutation checks', '3 / 3 caught'],
            ['Security delta', '0 new findings'],
            ['Existing SQL issue', 'Still needs review']
          ].map(([label, value], i) => (
            <Reveal
              key={label}
              p={p}
              at={i * 0.14}
              className="flex items-start justify-between gap-2 border-b border-border py-2"
            >
              <span>{label}</span>
              <span className={i === 4 ? 'text-amber-300' : 'text-emerald-400'}>
                {value}
              </span>
            </Reveal>
          ))}
          <Outcome p={p} tone="blue">
            <strong className="block text-sm">Ready for human review</strong>
            <span className="mt-1 block">
              Inspect the diff, evidence, and remaining finding. The developer
              decides whether to merge.
            </span>
          </Outcome>
        </>
      )
  }
}
