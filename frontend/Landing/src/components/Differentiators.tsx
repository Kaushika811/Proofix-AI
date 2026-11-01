import { motion } from 'framer-motion'
import { useReducedMotionPreference } from '@/hooks/useReducedMotionPreference'

const strengths = [
  [
    '01',
    'A failure you can point to',
    'Reproduction ties the investigation to an observed behavior. Source citations connect the explanation to the code that needs attention.',
    'Failure → assertion → source'
  ],
  [
    '02',
    'A repair with boundaries',
    'Repository relationships and impact analysis guide which files to inspect. Focused context keeps the repair tied to the target, test, and caller.',
    'Root cause → scope → context'
  ],
  [
    '03',
    'Tests that face a challenge',
    'Mutation testing deliberately changes the candidate. If the tests miss those faults, a passing test run is not the end of the investigation.',
    'Candidate → deliberate fault → test response'
  ],
  [
    '04',
    'A decision you can inspect',
    'The review package brings together checks and limitations. Unavailable evidence stays unverified; existing findings remain visible.',
    'Evidence → limitations → human decision'
  ]
]
export function Differentiators() {
  const reduced = useReducedMotionPreference()
  return (
    <section
      id="innovations"
      className="scroll-mt-24 px-4 py-20 sm:px-6 md:px-12 lg:px-16"
    >
      <div className="mx-auto max-w-6xl">
        <p className="font-mono text-[10px] uppercase tracking-[.25em] text-sky-300">
          What makes ProoFix different
        </p>
        <h2 className="mt-5 max-w-3xl font-display text-4xl tracking-tight md:text-6xl">
          The patch is connected
          <br />
          to its evidence.
        </h2>
        <p className="mt-5 max-w-2xl text-base leading-relaxed text-muted-foreground">
          ProoFix brings investigation, constrained repair, and validation into
          one reviewable workflow. These are the mechanisms behind that
          approach.
        </p>
        <div className="mt-10 grid gap-x-12 md:grid-cols-2">
          {strengths.map(([n, title, body, chain]) => (
            <motion.article
              key={n}
              initial={reduced ? false : { opacity: 0, y: 15 }}
              animate={reduced ? { opacity: 1, y: 0 } : undefined}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              className="border-t border-border py-7"
            >
              <span className="font-mono text-[10px] text-sky-300">{n}</span>
              <h3 className="mt-3 text-xl">{title}</h3>
              <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
                {body}
              </p>
              <p className="mt-5 font-mono text-[10px] text-sky-200">{chain}</p>
            </motion.article>
          ))}
        </div>
      </div>
    </section>
  )
}
