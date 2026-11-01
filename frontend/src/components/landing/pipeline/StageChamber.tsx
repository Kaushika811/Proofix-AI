import { useRef } from 'react'
import {
  motion,
  useScroll,
  useSpring,
  useTransform,
  type MotionValue
} from 'framer-motion'
import { cn } from '@/lib/utils'
import { useReducedMotionPreference } from '@/components/landing/hooks/useReducedMotionPreference'
import { StageVisual } from './StageVisual'
import type { Evidence, Stage } from './stageData'

type Props = {
  stage: Stage
  index: number
  active: boolean
  registerRef: (el: HTMLDivElement | null) => void
}

const fragments = [
  'vulnapi/api.py',
  'example repair',
  'vulnapi/auth.py',
  'pytest tests/test_auth.py',
  'payload.get("exp", 0)',
  'vulnapi/middleware.py',
  'test_expired_token_rejected',
  'pyproject.toml'
]

function EvidenceRow({
  evidence,
  index,
  progress,
  reduced
}: {
  evidence: Evidence
  index: number
  progress: MotionValue<number>
  reduced: boolean
}) {
  const opacity = useTransform(
    progress,
    [0.5 + index * 0.055, 0.65 + index * 0.055],
    [0.2, 1]
  )
  return (
    <motion.div
      className="flex items-baseline justify-between gap-4"
      style={{ opacity: reduced ? 1 : opacity }}
    >
      <span className="text-muted-foreground">{evidence.label}</span>
      <span className="text-foreground text-right">{evidence.value}</span>
    </motion.div>
  )
}

export function StageChamber({ stage, index, active, registerRef }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const patchTrackRef = useRef<HTMLDivElement>(null)
  const isPatch = index === 10
  const reduced = useReducedMotionPreference()
  const { scrollYProgress } = useScroll({
    target: wrapRef,
    offset: ['start end', 'end start']
  })
  const { scrollYProgress: patchProgress } = useScroll({
    target: patchTrackRef,
    offset: ['start 0.15', 'end end']
  })

  const smooth = useSpring(scrollYProgress, {
    stiffness: 120,
    damping: 30,
    mass: 0.35
  })
  // Finish the worked example while the stage is centered, before its exit fade.
  const stageProgress = useTransform(scrollYProgress, [0.16, 0.52], [0, 1])
  const demoProgress = isPatch ? patchProgress : stageProgress
  const bgY = useTransform(smooth, [0, 1], reduced ? [0, 0] : [28, -28])
  const midY = useTransform(smooth, [0, 1], reduced ? [0, 0] : [56, -56])
  const fgY = useTransform(smooth, [0, 1], reduced ? [0, 0] : [12, -12])

  // Blur + fade out as the stage leaves the top of the viewport (next stage arrives)
  const exitBlur = useTransform(
    scrollYProgress,
    [0.55, 0.85],
    reduced ? ['blur(0px)', 'blur(0px)'] : ['blur(0px)', 'blur(14px)']
  )
  const exitScale = useTransform(
    scrollYProgress,
    [0.55, 0.9],
    reduced ? [1, 1] : [1, 0.96]
  )
  // Subtle entrance: incoming stage rises into place
  const enterY = useTransform(
    scrollYProgress,
    [0.05, 0.35],
    reduced ? [0, 0] : [60, 0]
  )
  const opacity = useTransform(
    scrollYProgress,
    [0.05, 0.3, 0.55, 0.9],
    reduced ? [1, 1, 1, 1] : [0, 1, 1, 0]
  )

  return (
    <div
      ref={(el) => {
        wrapRef.current = el
        registerRef(el)
      }}
      data-stage={index}
      style={isPatch ? { scrollSnapAlign: 'start' } : undefined}
      className="stage-snap relative min-h-[100svh] flex items-center py-20 md:py-0"
    >
      {/* Layer 1 — background texture */}
      <motion.div
        style={{ y: bgY }}
        aria-hidden
        className={cn(
          'pointer-events-none absolute inset-0 overflow-hidden font-mono text-[10px] text-muted-foreground/[0.07]',
          'transition-opacity duration-700 motion-reduce:transition-none',
          active ? 'opacity-100' : 'opacity-30'
        )}
      >
        {fragments.map((f, i) => (
          <span
            key={f}
            className="absolute whitespace-nowrap"
            style={{ top: `${8 + i * 11}%`, left: `${(i % 3) * 28 + 8}%` }}
          >
            {f}
          </span>
        ))}
      </motion.div>

      <motion.div
        style={{
          filter: isPatch ? undefined : exitBlur,
          opacity: isPatch ? 1 : opacity,
          scale: isPatch ? undefined : exitScale,
          y: isPatch ? undefined : enterY,
          willChange: isPatch ? undefined : 'filter, opacity, transform'
        }}
        className="relative w-full pr-0 lg:pr-56"
      >
        {/* Header */}
        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 font-mono text-[10px] tracking-[0.3em] uppercase">
          <span
            className={
              active ? 'text-[var(--brand)]' : 'text-muted-foreground'
            }
          >
            {stage.n} / 14
          </span>
          <span className="text-muted-foreground">{stage.phase}</span>
        </div>
        <h3 className="mt-4 font-display text-4xl md:text-5xl lg:text-6xl tracking-tighter text-foreground">
          {stage.name}
        </h3>
        <div className="mt-5 h-px w-full max-w-3xl bg-border" />
        <p className="mt-5 text-sm md:text-base text-muted-foreground max-w-xl leading-relaxed">
          {stage.desc}
        </p>

        {/* Three zones */}
        <div className="mt-10 grid gap-8 md:gap-10 lg:grid-cols-[minmax(0,0.7fr)_minmax(0,1.3fr)_minmax(0,0.9fr)] items-start">
          {/* What it does */}
          <div className="font-mono text-[11px] text-muted-foreground">
            <div className="tracking-[0.25em] text-[10px] uppercase text-muted-foreground/70">
              What it does
            </div>
            <div className="mt-4 space-y-1">
              {stage.does.map((d, i) => (
                <div key={d}>
                  <div
                    className={
                      i === stage.does.length - 1 ? 'text-foreground' : ''
                    }
                  >
                    {d}
                  </div>
                  {i < stage.does.length - 1 && (
                    <div className="text-foreground/30">↓</div>
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* Layer 2 — illustrative investigation */}
          <motion.div
            ref={patchTrackRef}
            data-patch-track={isPatch || undefined}
            style={{ y: isPatch ? undefined : midY }}
            className={cn(
              'min-w-0',
              isPatch && !reduced && 'relative min-h-[220svh]'
            )}
          >
            <div className={cn(isPatch && !reduced && 'sticky top-[15svh]')}>
              <div className="font-mono tracking-[0.25em] text-[10px] uppercase text-muted-foreground/70 mb-4">
                Example investigation
              </div>
              <StageVisual index={index} progress={demoProgress} />
            </div>
          </motion.div>

          {/* Layer 3 — evidence */}
          <motion.div
            style={{ y: fgY }}
            className="rounded-xl border border-border bg-secondary/30 p-4 sm:p-5 font-mono text-[11px]"
          >
            <div className="tracking-[0.25em] text-[10px] uppercase text-muted-foreground/70">
              Example evidence
            </div>
            <div className="mt-4 space-y-2">
              {stage.evidence.map((e, i) => (
                <EvidenceRow
                  key={e.label}
                  evidence={e}
                  index={i}
                  progress={demoProgress}
                  reduced={!!reduced}
                />
              ))}
            </div>
          </motion.div>
        </div>

        {/* Input / output chain */}
        <div className="mt-10 flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-6 font-mono text-[10px] uppercase tracking-[0.2em]">
          <span className="text-muted-foreground/70">
            input{' '}
            <span className="ml-2 text-muted-foreground normal-case tracking-normal text-[11px]">
              {stage.input}
            </span>
          </span>
          <span className="hidden sm:block text-foreground/30">→</span>
          <span className="text-muted-foreground/70">
            output{' '}
            <span className="ml-2 text-foreground normal-case tracking-normal text-[11px]">
              {stage.output}
            </span>
          </span>
        </div>
      </motion.div>
    </div>
  )
}
