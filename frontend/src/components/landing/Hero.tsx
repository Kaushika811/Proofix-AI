import { HeroSignalGrid } from './HeroSignalGrid'
import { motion } from 'framer-motion'
import { useReducedMotionPreference } from '@/components/landing/hooks/useReducedMotionPreference'

export function Hero() {
  const reduced = useReducedMotionPreference()
  return (
    <section className="relative min-h-screen w-full overflow-hidden flex items-center py-32">
      {/* Ambient gradient backdrop */}
      <div className="absolute inset-0 -z-10">
        <div className="absolute inset-0 bg-[radial-gradient(120%_80%_at_50%_-10%,var(--secondary)_0%,var(--background)_55%)]" />
        <div className="absolute inset-0 bg-[radial-gradient(60%_50%_at_80%_120%,color-mix(in_srgb,var(--border)_60%,transparent)_0%,transparent_60%)]" />
      </div>

      <HeroSignalGrid />

      {/* Content */}
      <div className="relative z-10 w-full max-w-7xl mx-auto px-4 sm:px-6 md:px-12 lg:px-16">
        <motion.div
          initial={reduced ? false : { opacity: 0, y: 40 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8, ease: 'easeOut' }}
        >
          {/* Eyebrow */}
          <div className="flex items-center gap-3 mb-8">
            <span className="inline-block w-2 h-2 rounded-full bg-foreground animate-pulse" />
            <span className="text-xs text-muted-foreground tracking-[0.3em] uppercase">
              AI-generated changes. Human review.
            </span>
          </div>

          {/* Title */}
          <h1 className="font-display leading-[0.9] tracking-tighter text-[clamp(3.5rem,10vw,9rem)] text-foreground">
            <span className="block">Evidence</span>
            <span className="block">before merge.</span>
          </h1>

          {/* One-liner */}
          <motion.p
            initial={reduced ? false : { opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.4, duration: 0.8 }}
            className="mt-6 md:mt-8 text-lg md:text-xl lg:text-2xl text-muted-foreground font-light max-w-2xl leading-relaxed"
          >
            An AI-generated fix needs more than a convincing diff. ProoFix
            reproduces failures, constrains repairs, and challenges the
            candidate—so you can inspect the evidence before deciding to merge.
          </motion.p>

          {/* Workflow and repository links */}
          <motion.div
            initial={reduced ? false : { opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.6, duration: 0.8 }}
            className="mt-10 md:mt-12 flex flex-col sm:flex-row items-stretch sm:items-center justify-start gap-4"
          >
            <a
              href="/workspace"
              className="inline-flex items-center justify-center min-w-[220px] px-8 py-4 bg-foreground text-background font-medium text-sm tracking-wide rounded-lg hover:bg-foreground/90 transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-foreground"
            >
              Open workspace
            </a>
            <a
              href="#pipeline"
              className="inline-flex items-center justify-center min-w-[220px] px-8 py-4 border border-border text-foreground font-medium text-sm tracking-wide rounded-lg hover:bg-secondary transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-foreground"
            >
              See the repair unfold
            </a>
          </motion.div>
          <motion.p
            initial={reduced ? false : { opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.8, duration: 0.8 }}
            className="mt-4 text-left text-xs text-muted-foreground/80 tracking-wide"
          >
            Follow an example repair from reproduced failure to review decision.
          </motion.p>
        </motion.div>
      </div>
    </section>
  )
}
