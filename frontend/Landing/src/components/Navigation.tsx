import { useReducedMotionPreference } from '@/hooks/useReducedMotionPreference'
import { useState } from 'react'
import { usePageScrollState } from '@/hooks/usePageScrollState'
import { cn } from '@/lib/utils'

const navItems = [
  { id: 'pipeline', label: 'Walkthrough' },
  { id: 'innovations', label: 'Why ProoFix' },
  { id: 'architecture', label: 'Architecture' },
  { id: 'trust', label: 'Review package' }
]

const socialLinks = [
  { label: 'GitHub', href: 'https://github.com/CHEZHIYAN0277/Proofix' },
  { label: 'Docs', href: 'https://github.com/CHEZHIYAN0277/Proofix#readme' }
]

export function Navigation() {
  const reduced = useReducedMotionPreference()
  const { activeSection, activeMoment, scrolling } = usePageScrollState()
  const isVisible = !scrolling
  const pipelineInView = activeMoment !== null
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)

  const scrollToSection = (sectionId: string) => {
    const element = document.getElementById(sectionId)
    if (element) {
      element.scrollIntoView({ behavior: reduced ? 'instant' : 'smooth' })
      setMobileMenuOpen(false)
    }
  }

  return (
    <>
      {/* Mobile Header - Only visible on mobile */}
      <div className="mobile-site-header fixed top-0 left-0 right-0 z-50 p-6 flex justify-between items-start md:hidden">
        {/* Menu Button - Left */}
        <div className="relative">
          <button
            aria-expanded={mobileMenuOpen}
            aria-controls="mobile-page-menu"
            onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
            className="text-sm text-foreground mix-blend-difference"
          >
            {mobileMenuOpen ? 'Close' : 'Menu'}
          </button>

          {/* Mobile Menu Dropdown */}
          <div
            id="mobile-page-menu"
            hidden={!mobileMenuOpen}
            className={cn(
              'flex flex-col items-start gap-3 mt-6 transition-all duration-300',
              mobileMenuOpen
                ? 'opacity-100 translate-y-0'
                : 'opacity-0 -translate-y-4 pointer-events-none'
            )}
          >
            {navItems.map((item) => (
              <button
                key={item.id}
                aria-current={
                  activeSection === item.id ? 'location' : undefined
                }
                onClick={() => scrollToSection(item.id)}
                className={cn(
                  'text-sm text-foreground mix-blend-difference transition-all duration-300 relative py-1',
                  'hover:opacity-60',
                  activeSection === item.id &&
                    'after:absolute after:bottom-0 after:left-0 after:w-full after:h-px after:bg-foreground'
                )}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>

        {/* Brand */}
        <button
          onClick={() =>
            window.scrollTo({
              top: 0,
              behavior: reduced ? 'instant' : 'smooth'
            })
          }
          className="text-sm font-display tracking-tight text-foreground mix-blend-difference"
        >
          PROOFIX
        </button>

        {/* Social Links - Right (Mobile) */}
        <div className="flex items-center gap-4">
          {socialLinks.map((link) => (
            <a
              key={link.label}
              href={link.href}
              className="text-sm text-foreground mix-blend-difference hover:opacity-60 transition-opacity"
            >
              {link.label}
            </a>
          ))}
        </div>
      </div>

      {/* Desktop Brand - Fixed Top Left (hidden on mobile) */}
      <div className="hidden md:block fixed top-0 left-0 z-50 p-6 md:p-10">
        <button
          onClick={() =>
            window.scrollTo({
              top: 0,
              behavior: reduced ? 'instant' : 'smooth'
            })
          }
          className="text-sm font-display tracking-tight text-foreground mix-blend-difference"
        >
          PROOFIX
        </button>
      </div>

      {/* Desktop Social Links - Fixed Top Right (hidden on mobile) */}
      <div className="hidden md:block fixed top-0 right-0 z-50 p-6 md:p-10">
        <div className="flex items-center gap-6 justify-end">
          {socialLinks.map((link) => (
            <a
              key={link.label}
              href={link.href}
              className="text-sm text-foreground mix-blend-difference hover:opacity-60 transition-opacity"
            >
              {link.label}
            </a>
          ))}
        </div>
      </div>

      {/* Desktop Navigation Items - Fixed Bottom Right (hidden on mobile) */}
      <nav
        aria-label="Page sections"
        data-scrolling={scrolling}
        aria-hidden={!isVisible || pipelineInView}
        className={cn(
          'hidden md:block fixed bottom-0 right-0 z-50 p-6 md:p-10 transition-all duration-500',
          isVisible && !pipelineInView
            ? 'opacity-100 translate-x-0'
            : 'opacity-0 translate-x-8 pointer-events-none'
        )}
      >
        <div className="flex flex-col items-end gap-3">
          {navItems.map((item) => (
            <button
              key={item.id}
              tabIndex={isVisible && !pipelineInView ? 0 : -1}
              aria-current={activeSection === item.id ? 'location' : undefined}
              onClick={() => scrollToSection(item.id)}
              className={cn(
                'text-sm text-foreground mix-blend-difference transition-all duration-300 relative py-1',
                'hover:opacity-60',
                activeSection === item.id &&
                  'after:absolute after:bottom-0 after:left-0 after:w-full after:h-px after:bg-foreground'
              )}
            >
              {item.label}
            </button>
          ))}
        </div>
      </nav>
    </>
  )
}
