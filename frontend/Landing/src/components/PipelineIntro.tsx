import { motion } from 'framer-motion'
import { useReducedMotionPreference } from '@/hooks/useReducedMotionPreference'

export function PipelineIntro() {
  const reduced = useReducedMotionPreference()
  return (
    <section className="px-4 sm:px-6 md:px-12 lg:px-16 pt-16 md:pt-20 pb-8 md:pb-12">
      <motion.div
        initial={reduced ? false : { opacity: 0, y: 30 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, margin: '-15%' }}
        transition={{ duration: 0.8, ease: 'easeOut' }}
        className="max-w-4xl mx-auto text-center"
      >
        <span className="font-mono text-[10px] md:text-xs text-muted-foreground tracking-[0.35em] uppercase">
          The unanswered questions
        </span>
        <h2 className="mt-6 font-display text-[11vw] md:text-[6.5vw] leading-[0.92] tracking-tighter text-foreground">
          A plausible patch.
          <br />
          Enough evidence?
        </h2>
        <p className="mt-8 font-mono text-[11px] md:text-xs tracking-[0.3em] uppercase text-muted-foreground">
          One repair. Four moments that matter.
        </p>
        <p className="mt-8 text-base md:text-lg text-muted-foreground leading-relaxed max-w-2xl mx-auto">
          A convincing diff can leave the hardest questions unanswered: did it
          change the failing behavior, could it affect other code, and would the
          tests catch a bad repair? Follow an expired-token bug through those
          questions.
        </p>
        <p className="mt-4 text-xs text-muted-foreground leading-relaxed max-w-xl mx-auto">
          Illustrative walkthrough. The code, checks, and results below are
          examples, not a live repository run. Missing or failed checks remain
          part of the review decision.
        </p>
      </motion.div>
    </section>
  )
}
