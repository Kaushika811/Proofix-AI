import { useEffect, useState } from 'react'
import { useMotionValueEvent, type MotionValue } from 'framer-motion'
import { useReducedMotionPreference } from '@/components/landing/hooks/useReducedMotionPreference'
import './repair-scope.css'

const files = [
  {
    name: 'auth.py',
    path: 'vulnapi/auth.py',
    role: 'Patch target',
    symbol: 'validate_token',
    relationship: 'Root cause',
    line: 1,
    focus: [4],
    tone: 'fault',
    code: [
      'def validate_token(token):',
      '    payload = decode_token(token)',
      '    if not payload or "sub" not in payload:',
      '        return False',
      '    return True'
    ],
    explanation:
      'The subject is checked, but expiry never is. Keep the edit in this function.',
    citation: 'Unconditional acceptance · line 5'
  },
  {
    name: 'middleware.py',
    path: 'vulnapi/middleware.py',
    role: 'Direct importer',
    symbol: 'validate_token',
    relationship: 'Imported by · hop 1',
    line: 1,
    focus: [0, 3],
    tone: 'reference',
    code: [
      'from .auth import validate_token',
      '',
      'def auth_middleware(token):',
      '    return validate_token(token)'
    ],
    explanation:
      'This caller consumes the validator’s boolean result. Inspect its behavior; no edit is planned here.',
    citation: 'Import at line 1 → call at line 4'
  },
  {
    name: 'api.py',
    path: 'vulnapi/api.py',
    role: 'Indirect importer',
    symbol: 'auth_middleware',
    relationship: 'Imported by · hop 2',
    line: 1,
    focus: [0, 3],
    tone: 'reference',
    code: [
      'from .middleware import auth_middleware',
      '',
      'def protected_endpoint(token):',
      '    if not auth_middleware(token):',
      '        return {"error": "unauthorized"}, 401',
      '    return {"ok": True}, 200'
    ],
    explanation:
      'The result reaches the request boundary through middleware. Reachability suggests inspection, not proven breakage.',
    citation: 'Import at line 1 → guard at line 4'
  },
  {
    name: 'test_auth.py',
    path: 'tests/test_auth.py',
    role: 'Regression check',
    symbol: 'validate_token',
    relationship: 'Test reference · separate from import hops',
    line: 1,
    focus: [0, 4],
    tone: 'test',
    code: [
      'from vulnapi.auth import validate_token',
      '',
      'def test_expired_token_rejected(make_token, now):',
      '    token = make_token(exp=now - 3600)',
      '    assert validate_token(token) is False'
    ],
    explanation:
      'Carry this failing assertion into repair context. It defines the behavior the candidate must change.',
    citation: 'Target reference at line 5 · expected False'
  }
]
export function RepairScope({ progress }: { progress: MotionValue<number> }) {
  const [position, setPosition] = useState(progress.get())
  const [inspection, setInspection] = useState<number | null>(null)
  const reduced = useReducedMotionPreference()
  useMotionValueEvent(progress, 'change', (v) =>
    setPosition(Math.round(v * 100) / 100)
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
  const p = reduced ? 1 : position
  const automatic = p < 0.24 ? 0 : p < 0.48 ? 1 : p < 0.72 ? 2 : 3
  const selected = inspection ?? automatic
  const file = files[selected]
  const scoped = p >= 0.9
  const select = (index: number) => {
    setInspection(index)
  }
  return (
    <figure
      className="repair-scope"
      data-testid="repair-scope"
      data-progress={Math.round(p * 100)}
      data-scope-complete={scoped}
      data-selected-file={file.name}
    >
      <figcaption className="scope-editor-toolbar">
        <span>
          <i /> Repair investigation
        </span>
        <span>Example source excerpts</span>
      </figcaption>
      <div className="scope-symbol-query">
        <span>FIND REFERENCES</span>
        <code>validate_token</code>
        <span>Python</span>
      </div>
      <div className="scope-editor-workspace">
        <div
          className="scope-references"
          role="tablist"
          aria-label="Inspect source references"
        >
          {files.map((item, i) => (
            <button
              type="button"
              key={item.name}
              role="tab"
              id={`scope-file-${i}`}
              aria-controls="scope-source-panel"
              aria-selected={selected === i}
              tabIndex={selected === i ? 0 : -1}
              onClick={() => select(i)}
              onKeyDown={(e) => {
                if (
                  [
                    'ArrowDown',
                    'ArrowRight',
                    'ArrowUp',
                    'ArrowLeft',
                    'Home',
                    'End'
                  ].includes(e.key)
                ) {
                  e.preventDefault()
                  const next =
                    e.key === 'Home'
                      ? 0
                      : e.key === 'End'
                        ? 3
                        : (i +
                            (['ArrowDown', 'ArrowRight'].includes(e.key)
                              ? 1
                              : 3)) %
                          4
                  select(next)
                  document.getElementById(`scope-file-${next}`)?.focus()
                }
              }}
            >
              <span className="scope-file-icon" aria-hidden>
                {i === 0 ? '⌖' : i === 3 ? 'T' : '↳'}
              </span>
              <span>
                <strong>{item.name}</strong>
                <small>{item.role}</small>
              </span>
              <span className="scope-file-mark">
                {i === 0 ? 'edit' : i === 3 ? 'test' : `H${i}`}
              </span>
            </button>
          ))}
        </div>
        <div
          id="scope-source-panel"
          role="tabpanel"
          aria-labelledby={`scope-file-${selected}`}
          tabIndex={0}
          className="scope-source-panel"
        >
          <div className="scope-source-path">
            <code>{file.path}</code>
            <span>{selected === 0 ? 'Planned edit' : 'Read only'}</span>
          </div>
          <div className="scope-relationship">{file.relationship}</div>
          <div className="scope-source-code">
            {file.code.map((line, i) => (
              <div
                key={`${selected}-${i}`}
                className={`scope-source-line ${file.focus.includes(i) ? `scope-highlight-${file.tone}` : ''}`}
                data-source-highlight={file.focus.includes(i) || undefined}
              >
                <span aria-hidden>{file.line + i}</span>
                <code>{line || ' '}</code>
              </div>
            ))}
          </div>
          <p className="scope-source-citation">↳ {file.citation}</p>
        </div>
      </div>
      <div className="scope-source-explanation">
        <span>WHY THIS FILE</span>
        <p>{file.explanation}</p>
      </div>
      <div className="scope-change-set" data-final-scope={scoped}>
        <div>
          <span>EDIT SCOPE</span>
          <strong>{scoped ? '1 file' : 'Proposed · 1 file'}</strong>
          <code>vulnapi/auth.py</code>
        </div>
        <div>
          <span>INSPECT</span>
          <strong>2 connected files</strong>
          <small>Import reachability ≠ breakage</small>
        </div>
      </div>
      <div className="scope-editor-footer">
        <span>
          {reduced
            ? 'Select a file to inspect'
            : inspection !== null
              ? 'Inspecting source · scroll to resume'
              : 'Scroll to follow the references'}
        </span>
        <span>Target + failing test + caller</span>
      </div>
    </figure>
  )
}
