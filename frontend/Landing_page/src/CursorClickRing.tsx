import { useEffect, useRef } from 'react'

/** One short, decorative response to a primary mouse click. */
export function CursorClickRing() {
  const ringRef = useRef<HTMLSpanElement>(null)

  useEffect(() => {
    const ring = ringRef.current
    const landing = ring?.parentElement
    if (!ring || !landing) return

    const enabled = window.matchMedia('(hover: hover) and (pointer: fine) and (prefers-reduced-motion: no-preference)')
    let animation: Animation | undefined
    const cancel = () => animation?.cancel()
    const onPointerDown = (event: PointerEvent) => {
      if (!enabled.matches || event.pointerType !== 'mouse' || event.button !== 0 || !event.isPrimary) return
      cancel()
      ring.style.left = `${event.clientX}px`
      ring.style.top = `${event.clientY}px`
      animation = ring.animate(
        [
          { opacity: 0.75, transform: 'translate(-50%, -50%) scale(0.5)', offset: 0 },
          { opacity: 0.65, transform: 'translate(-50%, -50%) scale(0.7)', offset: 0.3 },
          { opacity: 0, transform: 'translate(-50%, -50%) scale(1)' },
        ],
        { duration: 360, easing: 'ease-out' },
      )
    }
    landing.addEventListener('pointerdown', onPointerDown, { passive: true })
    enabled.addEventListener('change', cancel)
    return () => {
      cancel()
      landing.removeEventListener('pointerdown', onPointerDown)
      enabled.removeEventListener('change', cancel)
    }
  }, [])

  return <span ref={ringRef} className="cursor-click-ring" aria-hidden="true" />
}
