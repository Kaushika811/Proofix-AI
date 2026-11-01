export function ProofSection() {
  return (
    <section
      id="trust"
      className="scroll-mt-24 border-t border-border px-4 py-20 sm:px-6 md:px-12 lg:px-16"
    >
      <div className="mx-auto grid max-w-6xl gap-10 lg:grid-cols-[.8fr_1.2fr]">
        <div>
          <p className="font-mono text-[10px] uppercase tracking-[.25em] text-sky-300">
            What the developer receives
          </p>
          <h2 className="mt-5 font-display text-4xl tracking-tight md:text-6xl">
            Inspect the repair.
            <br />
            Make the decision.
          </h2>
          <p className="mt-5 text-sm leading-relaxed text-muted-foreground">
            Review the candidate alongside the behavior it changes, the checks
            that challenged it, and the findings that still need attention.
          </p>
          <p className="mt-5 text-xs text-muted-foreground">
            Illustrative review package. Results here are examples, not a live
            run or a guarantee of correctness.
          </p>
        </div>
        <div className="rounded-xl border border-border bg-secondary/20 p-5 md:p-7">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-4">
            <span className="font-mono text-xs">Token-expiry repair</span>
            <span className="rounded-full border border-sky-300/30 px-3 py-1 text-[10px] text-sky-200">
              Human review
            </span>
          </div>
          <dl className="mt-2 text-xs">
            {[
              ['Candidate', 'auth.py · +3 / −1'],
              ['Before patch', 'Expired token accepted · failure reproduced'],
              ['After patch', 'Expired rejected · valid accepted'],
              ['Mutation checks', '3 / 3 example faults caught'],
              ['Security comparison', '0 new findings detected']
            ].map(([label, value]) => (
              <div
                key={label}
                className="grid grid-cols-[.7fr_1.3fr] gap-4 border-b border-border py-4"
              >
                <dt className="text-muted-foreground">{label}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
          <div className="mt-5 rounded-lg border border-amber-300/25 bg-amber-300/5 p-4">
            <p className="text-xs text-amber-200">
              Still unresolved: SQL interpolation in api.py
            </p>
            <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
              This baseline finding is outside the token repair. Passing the
              illustrated checks does not establish that the repository is free
              of defects.
            </p>
          </div>
        </div>
      </div>
    </section>
  )
}
