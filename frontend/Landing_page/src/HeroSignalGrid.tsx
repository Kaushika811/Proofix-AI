import { useRef } from 'react'
import { useInView } from 'framer-motion'
import './hero-signal-grid.css'

/** A decorative background only; never represents live engine activity. */
export function HeroSignalGrid() {
  const ref = useRef<HTMLDivElement>(null)
  const visible = useInView(ref)
  return (
    <div ref={ref} className="hero-signal-grid" aria-hidden="true" data-visible={visible}>
      <div className="hero-signal-grid-field">
        <div className="hero-signal-grid-dots" />
        <span className="hero-signal-grid-cross hero-signal-grid-cross-a" />
        <span className="hero-signal-grid-cross hero-signal-grid-cross-b" />
        <span className="hero-signal-grid-packet" />
      </div>
    </div>
  )
}
