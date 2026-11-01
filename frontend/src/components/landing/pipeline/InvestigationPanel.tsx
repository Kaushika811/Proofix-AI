import { useEffect, useState } from 'react'
import { cn } from '@/lib/utils'
import { stages } from './stageData'

type Props = {
  activeIndex: number
  visible: boolean
  onSelect: (index: number) => void
}

export function InvestigationPanel({ activeIndex, visible, onSelect }: Props) {
  const [scrolling, setScrolling] = useState(false)

  useEffect(() => {
    if (!visible) return
    let timeout: ReturnType<typeof setTimeout>
    const onScroll = () => {
      setScrolling(true)
      clearTimeout(timeout)
      timeout = setTimeout(() => setScrolling(false), 200)
    }
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => {
      window.removeEventListener('scroll', onScroll)
      clearTimeout(timeout)
    }
  }, [visible])

  const shown = visible && !scrolling

  return (
    <nav
      className={cn(
        'hidden lg:block fixed bottom-0 right-0 z-50 p-6 md:p-10',
        'transition-all duration-500 motion-reduce:transition-none',
        shown ? 'opacity-100 translate-x-0' : 'opacity-0 translate-x-8 pointer-events-none'
      )}
      aria-label="Pipeline stages"
    >
      <div className="flex flex-col items-end gap-1.5">
        {stages.map((s, i) => {
          const active = i === activeIndex
          return (
            <button
              key={s.n}
              onClick={() => onSelect(i)}
              className={cn(
                'text-sm text-foreground mix-blend-difference transition-all duration-300 relative py-1',
                'hover:opacity-60',
                active
                  ? 'opacity-100 after:absolute after:bottom-0 after:left-0 after:w-full after:h-px after:bg-foreground'
                  : 'opacity-40'
              )}
            >
              {s.name}
            </button>
          )
        })}
      </div>
    </nav>
  )
}
