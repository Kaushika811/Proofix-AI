import { CursorClickRing } from '@/components/CursorClickRing'
import '@/components/precision-cursor.css'
import { PageEffects } from '@/components/PageEffects'
import { PageScrollProvider } from '@/hooks/usePageScrollState'
import { Navigation } from '@/components/Navigation'
import { GrainOverlay } from '@/components/GrainOverlay'
import { Hero } from '@/components/Hero'
import { PipelineIntro } from '@/components/PipelineIntro'
import { Pipeline } from '@/components/Pipeline'
import { Differentiators } from '@/components/Differentiators'
import { ProofSection } from '@/components/ProofSection'
import { BrandStatement } from '@/components/BrandStatement'

export default function Index() {
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
