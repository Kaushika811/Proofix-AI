import { useRef } from 'react'
import { useScroll, useTransform } from 'framer-motion'
import { useReducedMotionPreference } from '@/components/landing/hooks/useReducedMotionPreference'
import { StageVisual } from './StageVisual'
import './precheck-stage.css'

export function PrecheckStage({
  registerRef
}: {
  registerRef: (el: HTMLDivElement | null) => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const reduced = useReducedMotionPreference()
  const { scrollYProgress } = useScroll({
    target: ref,
    offset: ['start start', 'end end']
  })
  // Leave an initial inspection state after arrival, and a final reading pause.
  const progress = useTransform(scrollYProgress, [0.08, 0.88], [0, 1])
  return (
    <div
      ref={(el) => {
        ref.current = el
        registerRef(el)
      }}
      data-stage="0"
      className={`precheck-stage ${reduced ? 'precheck-static' : ''}`}
    >
      <div className="precheck-stage-content">
        <div className="precheck-story">
          <p className="font-mono text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
            <span className="text-sky-300">01 / 14</span> · Understand
          </p>
          <p className="mt-4 font-mono text-[11px] text-sky-200">
            Environment Precheck
          </p>
          <h3 className="mt-3 font-display text-4xl leading-none tracking-tight md:text-5xl">
            Can this
            <br className="hidden lg:block" /> repository run?
          </h3>
          <p className="precheck-description mt-4 max-w-xs text-sm leading-relaxed text-muted-foreground">
            Trace the project to a working test runner. Establish the
            environment before investigating the bug.
          </p>
          <div className="precheck-handoff mt-8 border-l border-sky-300/40 pl-4 font-mono text-[10px] leading-relaxed text-muted-foreground">
            <span className="block text-foreground">
              Repository → execution readiness
            </span>
            Scroll to inspect each connection.
          </div>
        </div>
        <StageVisual index={0} progress={progress} />
      </div>
    </div>
  )
}
