import type { CSSProperties, ReactNode } from 'react'
import type { Evidence } from './stageData'
import './agent-evidence.css'

/**
 * The four evidence cards (2×2) inside an open agent in the "Behind the
 * investigation" timeline. Each agent gets its own compact visual language
 * (terminal, index slots, dependency nodes, diff gutter, …); the cards keep a
 * fixed size so nothing in the timeline moves.
 */

type Tone = 'ok' | 'fail' | 'warn' | 'pending' | 'info' | 'muted'

type Cell = {
  /** Small visual on the label row (right-aligned). */
  mark?: ReactNode
  /** Small visual leading the value. */
  lead?: ReactNode
  /** Replaces the plain value text (the plain value is still used as the tooltip). */
  value?: ReactNode
  tone?: Tone
  mono?: boolean
  /** Extra emphasis for the one card that carries the agent's key finding. */
  focus?: boolean
}

type Variant = { kind: string; cells: (ev: Evidence[]) => Cell[] }

/* ---------- tiny visual primitives ---------- */

const i = (n: number) => ({ '--i': n }) as CSSProperties

function Led({ tone = 'ok', pulse = false }: { tone?: Tone; pulse?: boolean }) {
  return <span className="aev-led" data-tone={tone} data-pulse={pulse} />
}

/** n filled slots out of total, e.g. files indexed. */
function Slots({ filled, total, tone = 'info' }: { filled: number; total: number; tone?: Tone }) {
  return (
    <span className="aev-slots" data-tone={tone}>
      {Array.from({ length: total }, (_, k) => (
        <span key={k} data-on={k < filled} style={i(k)} />
      ))}
    </span>
  )
}

/** Horizontal bar made of units (diff lines, findings). */
function Bars({ count, tone }: { count: number; tone: Tone }) {
  return (
    <span className="aev-bars" data-tone={tone}>
      {Array.from({ length: count }, (_, k) => (
        <span key={k} style={i(k)} />
      ))}
    </span>
  )
}

/** Severity meter: lit of 4. */
function Meter({ lit, tone }: { lit: number; tone: Tone }) {
  return (
    <span className="aev-meter" data-tone={tone}>
      {[0, 1, 2, 3].map((k) => (
        <span key={k} data-on={k < lit} style={{ ...i(k), height: 4 + k * 2 }} />
      ))}
    </span>
  )
}

function Chip({ children, tone = 'info', dashed = false }: { children: ReactNode; tone?: Tone; dashed?: boolean }) {
  return (
    <span className="aev-chip" data-tone={tone} data-dashed={dashed}>
      {children}
    </span>
  )
}

function Glyph({ children, tone = 'info' }: { children: ReactNode; tone?: Tone }) {
  return (
    <span className="aev-glyph" data-tone={tone}>
      {children}
    </span>
  )
}

/** Dependency-node pictograms for repository intelligence. */
function Node({ shape }: { shape: 'fn' | 'boundary' | 'edge' | 'unknown' }) {
  return (
    <svg className="aev-svg" viewBox="0 0 28 12" width="28" height="12">
      {shape === 'fn' && (
        <>
          <line x1="2" y1="6" x2="10" y2="6" className="aev-stroke-dim" />
          <line x1="18" y1="6" x2="26" y2="6" className="aev-stroke-dim" />
          <circle cx="14" cy="6" r="4" className="aev-fill-sky" />
        </>
      )}
      {shape === 'boundary' && (
        <>
          <rect x="6.5" y="0.5" width="15" height="11" rx="2" className="aev-stroke-sky" strokeDasharray="2 2" />
          <circle cx="14" cy="6" r="2.5" className="aev-fill-sky" />
        </>
      )}
      {shape === 'edge' && (
        <>
          <circle cx="5" cy="6" r="3.5" className="aev-stroke-sky" />
          <line x1="9" y1="6" x2="18" y2="6" className="aev-stroke-sky aev-draw" pathLength={1} />
          <path d="M16 3.5 L19 6 L16 8.5" className="aev-stroke-sky" />
          <circle cx="23" cy="6" r="3.5" className="aev-fill-sky" />
        </>
      )}
      {shape === 'unknown' && <circle cx="14" cy="6" r="4.5" className="aev-stroke-dim" strokeDasharray="2 2" />}
    </svg>
  )
}

/** Concentric hop rings for blast radius: which ring this card describes. */
function Rings({ hop, patch = false }: { hop: 0 | 1 | 2; patch?: boolean }) {
  return (
    <svg className="aev-svg" viewBox="0 0 14 14" width="14" height="14">
      <circle cx="7" cy="7" r="6.25" className={hop === 2 ? 'aev-stroke-sky' : 'aev-stroke-dim'} />
      <circle cx="7" cy="7" r="3.75" className={hop === 1 ? 'aev-stroke-sky' : 'aev-stroke-dim'} />
      <circle cx="7" cy="7" r="1.6" className={hop === 0 || patch ? 'aev-fill-sky' : 'aev-fill-dim'} />
      {patch && <circle cx="7" cy="7" r="3.75" className="aev-stroke-ok" strokeDasharray="1.5 2" />}
    </svg>
  )
}

/** Connected plan steps. */
function Steps({ count }: { count: number }) {
  const w = (count - 1) * 9 + 6
  return (
    <svg className="aev-svg" viewBox={`0 0 ${w} 6`} width={w} height="6">
      <line x1="3" y1="3" x2={w - 3} y2="3" className="aev-stroke-dim" />
      {Array.from({ length: count }, (_, k) => (
        <circle key={k} cx={3 + k * 9} cy="3" r="2.5" className="aev-fill-sky aev-pop" style={i(k)} />
      ))}
    </svg>
  )
}

/** Two pieces of evidence chained together. */
function Link() {
  return (
    <svg className="aev-svg" viewBox="0 0 26 8" width="26" height="8">
      <rect x="1" y="1" width="6" height="6" transform="rotate(45 4 4)" className="aev-fill-sky" />
      <line x1="8" y1="4" x2="18" y2="4" className="aev-stroke-sky aev-draw" pathLength={1} />
      <rect x="19" y="1" width="6" height="6" transform="rotate(45 22 4)" className="aev-fill-sky" />
    </svg>
  )
}

/** Open-ended version range: >= 3.0.0 with no upper bound. */
function Range() {
  return (
    <svg className="aev-svg" viewBox="0 0 30 8" width="30" height="8">
      <line x1="2" y1="1" x2="2" y2="7" className="aev-stroke-sky" />
      <line x1="2" y1="4" x2="27" y2="4" className="aev-stroke-sky" strokeDasharray="3 2" />
      <path d="M24 1.5 L28 4 L24 6.5" className="aev-stroke-sky" />
    </svg>
  )
}

/** Before / after comparison bar. */
function Compare({ value, max = 2, tone }: { value: number; max?: number; tone: Tone }) {
  return (
    <span className="aev-compare" data-tone={tone}>
      <span style={{ width: `${(value / max) * 100}%` }} />
    </span>
  )
}

const chips = (value: string, tone: Tone = 'info') =>
  value.split(' + ').map((part, k, all) => (
    <span key={part} className="aev-chips">
      <Chip tone={tone}>{part}</Chip>
      {k < all.length - 1 && <span className="aev-plus">+</span>}
    </span>
  ))

/* ---------- one visual language per agent ---------- */

const variants: Record<string, Variant> = {
  // Environment Precheck — terminal probe with status LEDs.
  '01': {
    kind: 'terminal',
    cells: () => [
      { mark: <Led />, mono: true, tone: 'ok' },
      { mark: <Led />, mono: true, tone: 'ok' },
      { mark: <Led />, mono: true, tone: 'ok' },
      { mark: <Led tone="warn" pulse />, mono: true, tone: 'warn' }
    ]
  },
  // Repository Indexing — files filling index slots.
  '02': {
    kind: 'index',
    cells: (ev) =>
      [6, 3, 2, 1].map((n, k) => ({
        mark: <Slots filled={n} total={6} tone={k === 0 ? 'info' : 'muted'} />,
        mono: true,
        value: ev[k].value
      }))
  },
  // Repository Intelligence — dependency nodes.
  '03': {
    kind: 'graph',
    cells: (ev) => [
      { lead: <Node shape="fn" />, mono: true, value: `${ev[0].value}()` },
      { lead: <Node shape="boundary" /> },
      { lead: <Node shape="edge" />, mono: true },
      { lead: <Node shape="unknown" />, tone: 'pending' }
    ]
  },
  // Dependency Analyzer — registry lookup with unknowns kept visible.
  '04': {
    kind: 'advisory',
    cells: (ev) => [
      { value: <Chip>{ev[0].value}</Chip> },
      { mark: <Range />, mono: true, tone: 'info' },
      { mark: <Glyph tone="muted">?.?.?</Glyph>, tone: 'muted' },
      { value: <span className="aev-hatch">{ev[3].value}</span>, tone: 'warn', focus: true }
    ]
  },
  // Static Analysis — finding with severity, location gutter, baseline tag.
  '05': {
    kind: 'scan',
    cells: () => [
      { mark: <Meter lit={3} tone="warn" />, tone: 'warn', focus: true },
      { lead: <span className="aev-gutter">L·</span>, mono: true },
      { value: <Chip tone="muted">no — baseline</Chip> },
      { lead: <Glyph tone="pending">⇢</Glyph>, tone: 'pending' }
    ]
  },
  // Failure Reproduction — pytest output.
  '06': {
    kind: 'pytest',
    cells: (ev) => [
      { mark: <Chip tone="fail">fail</Chip>, mono: true },
      { mark: <Glyph tone="muted">==</Glyph>, mono: true, tone: 'ok' },
      { mark: <Glyph tone="fail">≠</Glyph>, mono: true, tone: 'fail' },
      { lead: <Glyph tone="fail">✕</Glyph>, tone: 'fail', focus: true, value: ev[3].value }
    ]
  },
  // Root Cause Analysis — evidence chain with the missing check singled out.
  '07': {
    kind: 'chain',
    cells: () => [
      { lead: <Glyph tone="muted">⌗</Glyph>, mono: true },
      { lead: <Glyph tone="ok">✓</Glyph>, tone: 'ok' },
      { lead: <Glyph tone="fail">✕</Glyph>, tone: 'fail', focus: true },
      { mark: <Link /> }
    ]
  },
  // Blast Radius — hop rings around the origin.
  '08': {
    kind: 'radius',
    cells: () => [
      { lead: <Rings hop={0} />, mono: true },
      { lead: <Rings hop={1} />, mono: true },
      { lead: <Rings hop={2} />, mono: true },
      { lead: <Rings hop={0} patch />, mono: true, tone: 'ok', focus: true }
    ]
  },
  // Context Engineering — the file pool narrowed to what matters.
  '09': {
    kind: 'context',
    cells: (ev) => [
      { mark: <Slots filled={6} total={6} tone="muted" />, mono: true },
      { mark: <Slots filled={3} total={6} />, mono: true, tone: 'info' },
      { value: <Chip>{ev[2].value}</Chip> },
      { value: chips(ev[3].value, 'muted') }
    ]
  },
  // Repair Planner — ordered steps and constraints.
  '10': {
    kind: 'plan',
    cells: () => [
      { mark: <Steps count={4} />, mono: true },
      { lead: <Glyph tone="info">▤</Glyph> },
      { lead: <Glyph tone="ok">⊘</Glyph>, tone: 'ok' },
      { lead: <Led tone="pending" />, tone: 'pending' }
    ]
  },
  // Patch Generator — diff gutter.
  '11': {
    kind: 'diff',
    cells: (ev) => [
      { lead: <span className="aev-gutter">@@</span>, mono: true },
      { lead: <span className="aev-gutter" data-tone="ok">+</span>, mark: <Bars count={3} tone="ok" />, mono: true, tone: 'ok' },
      { lead: <span className="aev-gutter" data-tone="fail">−</span>, mark: <Bars count={1} tone="fail" />, mono: true, tone: 'fail' },
      { lead: <Led tone="warn" pulse />, tone: 'warn', value: ev[3].value }
    ]
  },
  // Mutation Validation — behaviour checks and caught mutants.
  '12': {
    kind: 'mutants',
    cells: () => [
      { mark: <Chip tone="ok">✓ pass</Chip>, tone: 'ok' },
      { mark: <Chip tone="ok">✓ pass</Chip>, tone: 'ok' },
      {
        mark: (
          <span className="aev-mutants">
            {[0, 1, 2].map((k) => (
              <span key={k} style={i(k)}>
                ✕
              </span>
            ))}
          </span>
        ),
        mono: true,
        tone: 'ok',
        focus: true
      },
      {}
    ]
  },
  // Security Re-scan — before/after delta.
  '13': {
    kind: 'delta',
    cells: () => [
      { mark: <Compare value={1} tone="warn" />, mono: true },
      { mark: <Compare value={1} tone="warn" />, mono: true },
      { mark: <Chip tone="ok">Δ 0</Chip>, tone: 'ok', focus: true },
      { lead: <Glyph tone="warn">⚑</Glyph>, tone: 'warn' }
    ]
  },
  // Mergeability Assessment — review gates.
  '14': {
    kind: 'gate',
    cells: () => [
      { lead: <Glyph tone="ok">✓</Glyph>, tone: 'ok' },
      { lead: <Glyph tone="ok">✓</Glyph>, tone: 'ok' },
      { lead: <Glyph tone="warn">⚠</Glyph>, tone: 'warn' },
      { value: <Chip dashed>→ human review</Chip>, focus: true }
    ]
  }
}

export function AgentEvidence({ n, evidence }: { n: string; evidence: Evidence[] }) {
  const variant = variants[n]
  const cells = variant?.cells(evidence) ?? []
  return (
    <dl className="aflow-evidence aev" data-kind={variant?.kind ?? 'plain'}>
      {evidence.map((ev, k) => {
        const cell = cells[k] ?? {}
        return (
          <div
            key={ev.label}
            className="aev-cell"
            data-tone={cell.tone}
            data-focus={cell.focus || undefined}
            style={i(k)}
            title={`${ev.label}: ${ev.value}`}
          >
            <dt>
              <span className="aev-label">{ev.label}</span>
              {cell.mark && (
                <span className="aev-mark" aria-hidden="true">
                  {cell.mark}
                </span>
              )}
            </dt>
            <dd data-mono={cell.mono || undefined}>
              {cell.lead && (
                <span className="aev-lead" aria-hidden="true">
                  {cell.lead}
                </span>
              )}
              <span className="aev-value">
                {cell.value ? (
                  <>
                    <span className="sr-only">{ev.value}</span>
                    <span aria-hidden="true">{cell.value}</span>
                  </>
                ) : (
                  ev.value
                )}
              </span>
            </dd>
          </div>
        )
      })}
    </dl>
  )
}
