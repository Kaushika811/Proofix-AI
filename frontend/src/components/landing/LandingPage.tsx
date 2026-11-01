import { CursorClickRing } from './CursorClickRing'
import './precision-cursor.css'
import { PageEffects } from '@/components/landing/PageEffects'
import { PageScrollProvider } from '@/components/landing/hooks/usePageScrollState'
import { Navigation } from '@/components/landing/Navigation'
import { GrainOverlay } from '@/components/landing/GrainOverlay'
import { Hero } from '@/components/landing/Hero'
import { PipelineIntro } from '@/components/landing/PipelineIntro'
import { Pipeline } from '@/components/landing/Pipeline'
import { Differentiators } from '@/components/landing/Differentiators'
import { ProofSection } from '@/components/landing/ProofSection'
import { BrandStatement } from '@/components/landing/BrandStatement'

export default function LandingPage() {
  return (
    <PageScrollProvider>
      <div
        id="top"
        className="landing-page min-h-screen bg-background text-foreground overflow-x-clip"
      >
        <CursorClickRing />
        <PageEffects />
        <GrainOverlay />
        <Navigation />
        <main>
          <Hero />
          <PipelineIntro />
          <Pipeline />
          <Differentiators />
          <ProofSection />
          <BrandStatement />
        </main>
      </div>
    </PageScrollProvider>
  )
}
