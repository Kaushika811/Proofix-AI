import { useEffect, useState } from 'react'
import { useMotionValueEvent, type MotionValue } from 'framer-motion'
import { useReducedMotionPreference } from '@/hooks/useReducedMotionPreference'
import './patch-challenge.css'

const mutations = [
  {
    name: 'Remove expiry guard',
    code: '    return True',
    test: 'test_expired_token_rejected',
    assertion: 'assert True is False',
    start: 0.7,
    end: 0.79
  },
  {
    name: 'Reverse comparison',
    code: '    return expires_at < time.time()',
    test: 'test_valid_token_accepted',
    assertion: 'assert False is True',
    start: 0.8,
    end: 0.89
  },
  {
    name: 'Accept expiry boundary',
    code: '    return expires_at >= time.time()',
    test: 'test_token_at_expiry_rejected',
    assertion: 'assert True is False',
    start: 0.9,
    end: 0.98
  }
]
const typed = (text: string, p: number, start: number, end: number) =>
  text.slice(
    0,
    Math.ceil(
      text.length * Math.max(0, Math.min(1, (p - start) / (end - start)))
    )
  )
export function PatchChallenge({
  progress
}: {
  progress: MotionValue<number>
}) {
  const reduced = useReducedMotionPreference()
  const [position, setPosition] = useState(progress.get())
  const [inspection, setInspection] = useState<number | null>(null)
  useMotionValueEvent(progress, 'change', (v) =>
    setPosition(Math.round(v * 1000) / 1000)
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
  const mutation = mutations.find((m) => p >= m.start && p < m.end)
  const caught = mutation && p >= mutation.start + 0.045
  const completed = p >= 0.99
  const removed = p >= 0.16
  const status = completed
    ? 'Candidate restored'
    : mutation
      ? 'Temporary mutation'
      : p >= 0.62
        ? 'Candidate checks'
        : p >= 0.28
          ? 'Writing patch'
          : removed
            ? 'Remove faulty return'
            : 'Original code'
  const additions = [
    { text: 'import time', start: 0.28, end: 0.35 },
    { text: '    expires_at = payload.get("exp", 0)', start: 0.39, end: 0.47 },
    { text: '    return expires_at > time.time()', start: 0.51, end: 0.59 }
  ]
  return (
    <figure
      className="patch-lab"
      data-testid="patch-challenge"
      data-progress={Math.round(p * 1000)}
      data-mutation={mutation?.name ?? 'none'}
    >
      <figcaption className="patch-lab-caption">
        <span>
          <i /> Illustrative patch & test run
        </span>
        <span>{status}</span>
      </figcaption>
      <div className="patch-lab-file">
        <code>vulnapi/auth.py</code>
        <span>
          {mutation
            ? 'mutant copy'
            : p >= 0.59
              ? '+3 / −1 · candidate'
              : 'working diff'}
        </span>
      </div>
      <div className="patch-lab-editor" aria-label="Candidate patch">
        <div
          className="lab-line lab-added"
          style={{ visibility: p > 0.28 ? 'visible' : 'hidden' }}
        >
          <span>+</span>
          <code>{typed(additions[0].text, p, 0.28, 0.35)}</code>
        </div>
        {[
          'def validate_token(token):',
          '    payload = decode_token(token)',
          '    if not payload or "sub" not in payload:',
          '        return False'
        ].map((line) => (
          <div className="lab-line" key={line}>
            <span />
            <code>{line}</code>
          </div>
        ))}
        <div className={`lab-line ${removed ? 'lab-removed' : ''}`}>
          <span>{removed ? '−' : ''}</span>
          <code style={{ textDecoration: removed ? 'line-through' : 'none' }}>
            {' '}
            return True
          </code>
        </div>
        <div
          className="lab-line lab-added"
          style={{ visibility: p > 0.39 ? 'visible' : 'hidden' }}
        >
          <span>+</span>
          <code>{typed(additions[1].text, p, 0.39, 0.47)}</code>
        </div>
        <div
          className={`lab-line ${mutation ? 'lab-mutant' : 'lab-added'}`}
          style={{ visibility: p > 0.51 ? 'visible' : 'hidden' }}
        >
          <span>{mutation ? '!' : '+'}</span>
          <code>
            {mutation?.code ?? typed(additions[2].text, p, 0.51, 0.59)}
          </code>
        </div>
      </div>
      <div className="patch-lab-runner">
        <div className="lab-runner-heading">
          <span>
            {mutation
              ? mutation.name
              : completed
                ? 'Mutation challenge complete'
                : 'Behavior checks'}
          </span>
          <b
            className={
              mutation
                ? caught
                  ? 'lab-caught'
                  : 'lab-running'
                : p >= 0.65
                  ? 'lab-passed'
                  : ''
            }
          >
            {mutation
              ? caught
                ? 'FAULT CAUGHT'
                : 'RUNNING'
              : p >= 0.65
                ? '2 CHECKS PASS'
                : 'PENDING'}
          </b>
        </div>
        <div className="lab-run-output">
          {mutation ? (
            <>
              <code>{mutation.test}</code>
              <p className={caught ? 'lab-assertion' : ''}>
                {caught
                  ? mutation.assertion
                  : 'Executing the check against the mutant…'}
              </p>
              <small>The test must fail on this deliberate fault.</small>
            </>
          ) : (
            <>
              <code>
                {p >= 0.65
                  ? '✓ expired rejected · ✓ valid accepted'
                  : 'Waiting for the complete candidate diff.'}
              </code>
              <p>
                {completed
                  ? 'Original candidate restored after all three challenges.'
                  : p >= 0.65
                    ? 'Next: inject faults into isolated copies of the candidate.'
                    : 'The repair has not been validated yet.'}
              </p>
              <small>Expiry-boundary check uses a fixed clock.</small>
            </>
          )}
        </div>
        <div className="lab-mutant-results">
          {mutations.map((m, i) => (
            <span key={m.name} className={p >= m.end ? 'is-caught' : ''}>
              {String(i + 1).padStart(2, '0')}{' '}
              {['Guard', 'Direction', 'Boundary'][i]}
              <b>
                {p >= m.end ? 'caught' : p >= m.start ? 'testing' : 'pending'}
              </b>
            </span>
          ))}
        </div>
      </div>
      <div className="lab-replay">
        <label htmlFor="patch-challenge-progress">
          {reduced
            ? 'Completed example'
            : inspection !== null
              ? 'Inspecting · scroll to resume'
              : 'Scroll to generate, then challenge'}
        </label>
        <input
          id="patch-challenge-progress"
          aria-label="Inspect patch and mutation progress"
          type="range"
          min="0"
          max="1000"
          value={Math.round(p * 1000)}
          disabled={reduced}
          onChange={(e) => setInspection(Number(e.target.value) / 1000)}
        />
      </div>
      <p className="lab-limit">
        Catching these faults is evidence, not a correctness guarantee.
      </p>
    </figure>
  )
}
