import { createRef, useMemo, type RefObject } from 'react'
import { useScroll, useTransform } from 'framer-motion'
import { usePageScrollState } from '@/hooks/usePageScrollState'
import { PatchChallenge } from './pipeline/PatchChallenge'
import { RepairScope } from './pipeline/RepairScope'
import { FailureTrace } from './pipeline/FailureTrace'
import { StageVisual } from './pipeline/StageVisual'
import { stages } from './pipeline/stageData'
import { useReducedMotionPreference } from '@/hooks/useReducedMotionPreference'
import './pipeline/repair-story.css'

const moments = [
  {
    title: 'Reproduce the failure',
    question: 'Does the bug actually happen?',
    description:
      'An expired token should be rejected. Run the behavior and capture the assertion that shows what went wrong.',
    takeaway: 'A concrete failure gives the repair a behavior to change.',
    visual: 5,
    agents: [0, 1, 2, 3, 4, 5]
  },
  {
    title: 'Constrain the repair',
    question: 'What needs to change—and what could it affect?',
    description:
      'The validator checks the subject but skips expiry. Follow its importers, then keep the target, failing test, and immediate caller in context.',
    takeaway: 'One file to patch. Connected files to inspect.',
    visual: 7,
    agents: [6, 7, 8, 9]
  },
  {
    title: 'Generate, then challenge',
    question: 'Would the tests catch a faulty repair?',
    description:
      'Replace unconditional acceptance with an expiry check. Then deliberately damage the candidate to see whether the tests notice.',
    takeaway:
      'Passing behavior checks is followed by a challenge to the tests themselves.',
    visual: 10,
    agents: [10, 11]
  },
  {
    title: 'Review the evidence',
    question: 'What supports this decision—and what is still unresolved?',
    description:
      'Bring the failure, patch, test results, and security comparison together. The unrelated SQL issue remains visible for the reviewer.',
    takeaway: 'Inspect the evidence. The developer decides whether to merge.',
    visual: 13,
    agents: [12, 13]
  }
]

function Moment({ index, active, track }: { index: number; active: boolean; track: RefObject<HTMLDivElement> }) {
  const moment = moments[index]
  const { scrollYProgress } = useScroll({
    target: track,
    offset: ['start .38', 'end .38']
  })
  // Hold the opening and completed states before handing over to the next scene.
  // The last track also includes the space needed to unpin the presentation.
  const progress = useTransform(scrollYProgress, [index === 0 ? 0.28 : 0.08, index === 3 ? 0.65 : 0.88], [0, 1])
  return (
    <section
      id={`scene-${index + 1}`}
      data-stage={index + 1}
      data-active={active}
      hidden={!active}
      aria-labelledby={`scene-title-${index + 1}`}
      className="repair-moment"
    >
      <div className="repair-moment-frame">
        <div className="repair-moment-copy">
          <p className="font-mono text-[10px] uppercase tracking-[.2em] text-sky-300">
            0{index + 1} / 04 · {moment.title}
          </p>
          <h3 id={`scene-title-${index + 1}`} className="mt-4 font-display text-4xl leading-none tracking-tight md:text-5xl">
            {moment.question}
          </h3>
          <p className="mt-5 max-w-md text-sm leading-relaxed text-muted-foreground">
            {moment.description}
          </p>
          <p className="repair-takeaway mt-6 border-l border-sky-300/40 pl-4 text-sm text-sky-100">
            {moment.takeaway}
          </p>
        </div>
        <div className="repair-scene-visual" tabIndex={active ? 0 : -1} role="region" aria-label={`${moment.title} interactive example`}>
          {index === 0 ? (
            <FailureTrace progress={progress} />
          ) : index === 1 ? (
            <RepairScope progress={progress} />
          ) : index === 2 ? (
            <PatchChallenge progress={progress} />
          ) : (
            <StageVisual index={moment.visual} progress={progress} />
          )}
        </div>
      </div>
    </section>
  )
}

export function Pipeline() {
  const { activeMoment, activeAgent, scrolling } = usePageScrollState()
  const tracks = useMemo(() => moments.map(() => createRef<HTMLDivElement>()), [])
  const reduced = useReducedMotionPreference()
  const shown = activeMoment ? Number(activeMoment.replace('moment-', '')) - 1 : 0
  return (
    <>
      {activeMoment && (
        <nav className="timeline-position" aria-label="Timeline position" data-scrolling={scrolling}>
          {moments.map((m, i) => (
            <a
              key={m.title}
              href={`#moment-${i + 1}`}
              aria-label={`${i + 1}. ${m.title}`}
              title={m.title}
              aria-current={
                activeMoment === `moment-${i + 1}` ? 'step' : undefined
              }
            >
              <span />
            </a>
          ))}
        </nav>
      )}
      <div id="pipeline" className={`repair-walkthrough ${reduced ? 'repair-reduced' : ''}`}>
        {moments.map((m, i) => (
          <div
            key={m.title}
            ref={tracks[i]}
            id={`moment-${i + 1}`}
            data-moment={i + 1}
            data-active={activeMoment === `moment-${i + 1}`}
            className="repair-scroll-track"
            style={{ top: `calc(${i} * var(--scene-distance))` }}
            aria-hidden="true"
          />
        ))}
        <div className="repair-viewport">
          <div className="repair-stage-container">
            {moments.map((m, i) => (
              <Moment key={m.title} index={i} active={shown === i} track={tracks[i]} />
            ))}
          </div>
          <p className="repair-scroll-hint">{reduced ? 'Scroll to the next stage' : 'Scroll to explore this stage'} · {String(shown + 1).padStart(2, '0')} / 04</p>
        </div>
      </div>
      <section
        id="architecture"
        className="mx-auto max-w-6xl px-4 py-12 sm:px-6"
      >
        <details className="rounded-xl border border-border bg-secondary/10 p-5 md:p-7">
          <summary className="cursor-pointer text-sm text-foreground">
            Behind the investigation: 14 specialized agents{' '}
            <span className="ml-2 text-xs text-muted-foreground">
              Explore the architecture
            </span>
          </summary>
          <p className="mt-4 max-w-2xl text-sm text-muted-foreground">
            The four moments summarize the workflow. These agents handle its
            individual responsibilities.
          </p>
          <ol className="architecture-agents" data-has-active={activeAgent !== null}>
            {stages.map((stage, i) => {
              const parent = moments.findIndex((m) => m.agents.includes(i))
              return (
                <li
                  key={stage.n}
                  id={`agent-${stage.n}`}
                  data-agent={stage.n}
                  data-parent-moment={parent + 1}
                  className="architecture-agent"
                  aria-current={
                    activeAgent === `agent-${stage.n}` ? 'step' : undefined
                  }
                >
                  <span className="architecture-agent-number">{stage.n}</span>
                  <div>
                    <strong>{stage.name}</strong>
                    <p>{stage.does.join(' → ')}</p>
                  </div>
                  <a href={`#moment-${parent + 1}`}>
                    View {moments[parent].title.toLowerCase()} ↗
                  </a>
                </li>
              )
            })}
          </ol>
        </details>
      </section>
    </>
  )
}
