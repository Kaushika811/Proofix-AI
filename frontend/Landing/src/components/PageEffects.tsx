import { useEffect } from 'react'
import { motion, useScroll, useSpring } from 'framer-motion'
import { useReducedMotionPreference } from '@/hooks/useReducedMotionPreference'
import '@/styles/page-effects.css'

export function PageEffects() {
  const reduced = useReducedMotionPreference()
  const { scrollYProgress } = useScroll()
  const smooth = useSpring(scrollYProgress, {
    stiffness: 180,
    damping: 35,
    mass: 0.2
  })
  useEffect(() => {
    const elements = document.querySelectorAll<HTMLElement>(
      '#architecture details, #trust > div, #innovations h2'
    )
    if (reduced) {
      elements.forEach((el) => el.classList.remove('will-reveal'))
      return
    }
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add('is-revealed')
            observer.unobserve(entry.target)
          }
        })
      },
      { threshold: 0.08, rootMargin: '0px 0px -20px 0px' }
    )
    elements.forEach((el) => {
      el.classList.add('will-reveal')
      observer.observe(el)
    })
    return () => {
      observer.disconnect()
      elements.forEach((el) =>
        el.classList.remove('will-reveal', 'is-revealed')
      )
    }
  }, [reduced])
  return (
    <motion.div
      className="page-reading-progress"
      aria-hidden
      style={{ scaleX: reduced ? scrollYProgress : smooth }}
    />
  )
}
