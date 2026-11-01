import { useEffect, useState } from 'react'
import { useMotionValueEvent, type MotionValue } from 'framer-motion'
import { useReducedMotionPreference } from '@/hooks/useReducedMotionPreference'
import './failure-trace.css'

const clamp = (n: number) => Math.max(0, Math.min(1, n))
const lines = [
  { text: 'def validate_token(token):', at: 0 },
  { text: '    payload = decode_token(token)', at: 0.22 },
  { text: '    if not payload or "sub" not in payload:', at: 0.4 },
  { text: '        return False', at: -1 },
  { text: '    return True', at: 0.68 }
]

export function FailureTrace({ progress }: { progress: MotionValue<number> }) {
  const reduced = useReducedMotionPreference()
  const [position, setPosition] = useState(progress.get())
  const [inspection, setInspection] = useState<number | null>(null)
  useMotionValueEvent(progress, 'change', (v) =>
    setPosition(Math.round(clamp(v) * 100) / 100)
  )
  useEffect(() => {
    if (inspection === null) return
    const resume = () => setInspection(null)
    window.addEventListener('wheel', resume, { passive: true })
    window.addEventListener('touchmove', resume, { passive: true })
    return () => {
      window.removeEventListener('wheel', resume)
      window.removeEventListener('touchmove', resume)
    }
  }, [inspection])
  const p = reduced ? 1 : (inspection ?? position)
  const returned = p >= 0.68
  const failed = p >= 0.84
  const active = p < 0.22 ? 0 : p < 0.4 ? 1 : p < 0.68 ? 2 : 4
  const travelled = clamp((p - 0.1) / 0.62)
  const stage =
    p < 0.22
      ? 'Expired input'
      : p < 0.4
        ? 'Decode payload'
        : p < 0.68
          ? 'Subject check'
          : failed
            ? 'Failure reproduced'
            : 'Unexpected acceptance'
  return (
    <figure
      className="failure-trace"
      data-testid="failure-trace"
      data-progress={Math.round(p * 100)}
      data-trace-state={failed ? 'failed' : returned ? 'returned' : 'executing'}
      aria-label="Illustrative execution of an expired token through validate_token"
    >
      <figcaption className="trace-caption">
        <span>
          <i /> Illustrative execution
        </span>
        <span>{stage}</span>
      </figcaption>
      <div className="trace-token">
        <div className="trace-token-label">
          <span>INPUT / TOKEN</span>
          <code>sub: "user_42"</code>
          <b>Expired 1 hour ago</b>
        </div>
        <div
          className="trace-clock"
          aria-label="Token expiry at 12:00; current time 13:00"
        >
          <span>
            <i />
            12:00 <small>expires</small>
          </span>
          <div className="trace-time-gap">
            <span>+1 hour</span>
          </div>
          <span>
            <i />
            13:00 <small>now</small>
          </span>
        </div>
      </div>
      <div className="trace-code-heading">
        <span>vulnapi/auth.py</span>
        <span>{p >= 0.4 ? 'payload.sub exists' : 'validate_token(token)'}</span>
      </div>
      <div className="trace-code">
        <div className="trace-execution-rail" aria-hidden>
          <div style={{ height: `${travelled * 100}%` }} />
          <i style={{ top: `${(active / 4) * 100}%` }} />
        </div>
        {lines.map((line, i) => (
          <div
            key={line.text}
            data-line={i + 1}
            className={`trace-code-line ${active === i ? 'is-current' : ''} ${i === 4 && returned ? 'is-fault' : ''} ${i === 3 ? 'is-skipped' : ''}`}
          >
            <span className="trace-line-number" aria-hidden>
              {i + 1}
            </span>
            <code>{line.text}</code>
            {i === 3 && p >= 0.4 && (
              <span className="trace-skipped-label">not taken</span>
            )}
          </div>
        ))}
      </div>
      <div
        className="trace-missing-check"
        style={{ opacity: p >= 0.54 ? 1 : 0 }}
        aria-hidden={p < 0.54}
      >
        <span aria-hidden>↳</span> No expiry comparison on this execution path.
      </div>
      <div className="trace-verdict" style={{ opacity: returned ? 1 : 0.28 }}>
        <div>
          <span>EXPECTED</span>
          <strong>Reject token</strong>
          <code>False</code>
        </div>
        <span className="trace-not-equal" aria-hidden>
          {returned ? '≠' : '→'}
        </span>
        <div className={returned ? 'trace-observed-failure' : ''}>
          <span>OBSERVED</span>
          <strong>{returned ? 'Accept token' : 'Awaiting return'}</strong>
          <code>{returned ? 'True' : '—'}</code>
        </div>
      </div>
      <div
        className="trace-assertion"
        style={{ visibility: failed ? 'visible' : 'hidden' }}
        aria-hidden={!failed}
      >
        <span>FAIL</span>
        <code>assert True is False</code>
        <small>test_expired_token_rejected</small>
      </div>
      <div className="trace-scrubber">
        <label htmlFor="failure-trace-position">
          {reduced
            ? 'Complete trace'
            : inspection === null
              ? 'Scroll to trace execution'
              : 'Scroll to resume · inspecting'}
        </label>
        <input
          id="failure-trace-position"
          aria-label="Inspect execution progress"
          type="range"
          min="0"
          max="100"
          value={Math.round(p * 100)}
          disabled={reduced}
          onChange={(e) => setInspection(Number(e.target.value) / 100)}
        />
      </div>
    </figure>
  )
}
