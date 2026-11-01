import { CursorClickRing } from './CursorClickRing'
import './precision-cursor.css'
import { PageEffects } from './PageEffects'
import { PageScrollProvider } from './hooks/usePageScrollState'
import { Navigation } from './Navigation'
import { GrainOverlay } from './GrainOverlay'
import { Hero } from './Hero'
import { PipelineIntro } from './PipelineIntro'
import { Pipeline } from './Pipeline'
import { Differentiators } from './Differentiators'
import { ProofSection } from './ProofSection'
import { BrandStatement } from './BrandStatement'

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
