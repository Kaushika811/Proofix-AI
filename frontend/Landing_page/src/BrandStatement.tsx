export function BrandStatement() {
  return (
    <section className="border-t border-border px-4 py-20 sm:px-6 md:px-12 lg:px-16">
      <div className="mx-auto max-w-6xl">
        <h2 className="font-display text-5xl tracking-tight md:text-7xl">
          Bring evidence
          <br />
          to your next repair.
        </h2>
        <p className="mt-5 max-w-xl text-base leading-relaxed text-muted-foreground">
          Explore the project, inspect the workflow, and see how ProoFix
          assembles a repair for human review.
        </p>
        <div className="mt-8 flex flex-wrap gap-4">
          <a
            href="/workspace"
            className="rounded-lg bg-foreground px-6 py-3 text-sm font-medium text-background"
          >
            Open workspace →
          </a>
          <a
            href="#pipeline"
            className="rounded-lg border border-border px-6 py-3 text-sm"
          >
            Replay the walkthrough
          </a>
        </div>
        <footer className="mt-16 flex flex-wrap justify-between gap-5 border-t border-border pt-6 text-xs text-muted-foreground">
          <p>© {new Date().getFullYear()} ProoFix</p>
          <div className="flex gap-6">
            <a href="https://github.com/CHEZHIYAN0277/Proofix">GitHub</a>
            <a href="https://github.com/CHEZHIYAN0277/Proofix#readme">Docs</a>
            <a href="#architecture">Architecture</a>
          </div>
        </footer>
      </div>
    </section>
  )
}
