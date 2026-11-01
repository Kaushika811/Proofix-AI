import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'

type ScrollState = {
  activeSection: string | null
  activeMoment: string | null
  activeAgent: string | null
  scrolling: boolean
}

const initial: ScrollState = {
  activeSection: null, activeMoment: null, activeAgent: null, scrolling: false
}
const ScrollContext = createContext(initial)

/** Nearest visible item to the reading line; hidden details never participate. */
function visibleStage(selector: string) {
  const line = innerHeight * 0.38
  let closest: { id: string; distance: number } | null = null
  for (const element of document.querySelectorAll<HTMLElement>(selector)) {
    if (element.closest('details:not([open])') || !element.getClientRects().length) continue
    const { top, bottom, height } = element.getBoundingClientRect()
    if (height <= 0 || bottom <= 80 || top >= innerHeight) continue
    const distance = top <= line && bottom > line
      ? 0 : Math.min(Math.abs(top - line), Math.abs(bottom - line))
    if (!closest || distance < closest.distance) closest = { id: element.id, distance }
  }
  return closest?.id ?? null
}

export function PageScrollProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState(initial)
  useEffect(() => {
    let frame = 0
    let idle: ReturnType<typeof setTimeout> | undefined
    let scrolling = false
    let disposed = false
    let lastY = scrollY
    const measure = () => {
      frame = 0
      if (disposed) return
      const next: ScrollState = {
        activeSection: visibleStage('#pipeline, #architecture, #innovations, #trust'),
        activeMoment: visibleStage('[data-moment]'),
        activeAgent: visibleStage('[data-agent]'),
        scrolling
      }
      setState(previous => Object.keys(next).every(
        key => next[key as keyof ScrollState] === previous[key as keyof ScrollState]
      ) ? previous : next)
    }
    const schedule = () => {
      if (!disposed && !frame) frame = requestAnimationFrame(measure)
    }
    const settle = () => {
      clearTimeout(idle)
      scrolling = false
      // Remeasure before revealing; never restore an old active item from a timer.
      schedule()
    }
    const onScroll = () => {
      if (scrollY !== lastY) scrolling = true
      lastY = scrollY
      clearTimeout(idle)
      idle = setTimeout(settle, 150)
      schedule()
    }
    const resize = new ResizeObserver(schedule)
    resize.observe(document.body)
    document.querySelectorAll('[data-moment], [data-agent]').forEach(el => resize.observe(el))
    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('resize', schedule)
    window.addEventListener('hashchange', schedule)
    window.addEventListener('pageshow', schedule)
    document.addEventListener('toggle', schedule, true)
    document.fonts.ready.then(schedule)
    schedule()
    return () => {
      disposed = true
      clearTimeout(idle)
      cancelAnimationFrame(frame)
      resize.disconnect()
      window.removeEventListener('scroll', onScroll)
      window.removeEventListener('resize', schedule)
      window.removeEventListener('hashchange', schedule)
      window.removeEventListener('pageshow', schedule)
      document.removeEventListener('toggle', schedule, true)
    }
  }, [])
  return <ScrollContext.Provider value={state}>{children}</ScrollContext.Provider>
}

// The provider and hook intentionally share this small context module.
// eslint-disable-next-line react-refresh/only-export-components
export function usePageScrollState() {
  return useContext(ScrollContext)
}
