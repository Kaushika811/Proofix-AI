export type Evidence = { label: string; value: string }

export type Stage = {
  n: string
  phase: string
  name: string
  desc: string
  does: string[]
  input: string
  output: string
  artifact: string
  evidence: Evidence[]
}

export const stages: Stage[] = [
  {
    n: '01',
    phase: 'Understand',
    name: 'Environment Precheck',
    desc: 'Can this repository run? Probe the environment before asking tests to establish whether the reported token bug is real.',
    does: ['Locate the project', 'Probe pytest', 'Check collection'],
    input: 'Repository + token-expiry report',
    output: 'Environment report',
    artifact: 'Environment report',
    evidence: [
      {
        label: 'project',
        value: 'Python'
      },
      {
        label: 'runner',
        value: 'pytest available'
      },
      {
        label: 'collection',
        value: 'completed'
      },
      {
        label: 'bug reproduced',
        value: 'not tested yet'
      }
    ]
  },
  {
    n: '02',
    phase: 'Understand',
    name: 'Repository Indexing',
    desc: 'Watch the project become a navigable index. Source files, tests, and manifests provide the structure later stages can inspect.',
    does: ['Discover files', 'Extract symbols', 'Build the index'],
    input: 'Repository files',
    output: 'Repository index',
    artifact: 'Repository index',
    evidence: [
      {
        label: 'example files',
        value: '6 indexed'
      },
      {
        label: 'source files',
        value: '3'
      },
      {
        label: 'test files',
        value: '2'
      },
      {
        label: 'manifest',
        value: '1'
      }
    ]
  },
  {
    n: '03',
    phase: 'Understand',
    name: 'Repository Intelligence',
    desc: 'Connect the validator to the code that uses it. Structural context helps investigation stay grounded in the repository.',
    does: ['Parse functions', 'Identify roles', 'Connect imports'],
    input: 'Repository index',
    output: 'Semantic context',
    artifact: 'Semantic context',
    evidence: [
      {
        label: 'target',
        value: 'validate_token'
      },
      {
        label: 'role',
        value: 'auth boundary'
      },
      {
        label: 'caller',
        value: 'auth_middleware'
      },
      {
        label: 'expiry behavior',
        value: 'not verified yet'
      }
    ]
  },
  {
    n: '04',
    phase: 'Understand',
    name: 'Dependency Analyzer',
    desc: 'Check declared packages against advisory data, then qualify what is known. A version range or missing result must not become a safety claim.',
    does: ['Read the manifest', 'Look up advisories', 'Assess reachability'],
    input: 'Package declarations',
    output: 'Dependency evidence',
    artifact: 'Dependency evidence',
    evidence: [
      {
        label: 'ecosystem',
        value: 'PyPI'
      },
      {
        label: 'declaration',
        value: 'flask>=3.0.0'
      },
      {
        label: 'installed version',
        value: 'unknown'
      },
      {
        label: 'safety verdict',
        value: 'not established'
      }
    ]
  },
  {
    n: '05',
    phase: 'Understand',
    name: 'Static Analysis',
    desc: 'Scan source and attach findings to their locations. Keep baseline findings separate from the bug being repaired.',
    does: ['Inspect source', 'Identify findings', 'Record baseline'],
    input: 'Source files',
    output: 'Baseline findings',
    artifact: 'Baseline findings',
    evidence: [
      {
        label: 'example finding',
        value: 'SQL interpolation'
      },
      {
        label: 'location',
        value: 'vulnapi/api.py'
      },
      {
        label: 'introduced by patch',
        value: 'no — baseline'
      },
      {
        label: 'token bug',
        value: 'requires runtime evidence'
      }
    ]
  },
  {
    n: '06',
    phase: 'Prove',
    name: 'Failure Reproduction',
    desc: 'Run the failing behavior. The example test expects an expired token to be rejected, but the current validator accepts it.',
    does: [
      'Run the target test',
      'Capture the assertion',
      'Record the failure'
    ],
    input: 'Runnable repository',
    output: 'Reproduction evidence',
    artifact: 'Reproduction evidence',
    evidence: [
      {
        label: 'test',
        value: 'test_expired_token_rejected'
      },
      {
        label: 'expected',
        value: 'False'
      },
      {
        label: 'observed',
        value: 'True'
      },
      {
        label: 'result',
        value: 'failure reproduced'
      }
    ]
  },
  {
    n: '07',
    phase: 'Prove',
    name: 'Root Cause Analysis',
    desc: 'Bring the failing assertion and source together. The validator checks the subject, then returns True without checking expiry.',
    does: ['Read the failure', 'Inspect source', 'Verify the citation'],
    input: 'Failure + source',
    output: 'Root-cause brief',
    artifact: 'Root-cause brief',
    evidence: [
      {
        label: 'location',
        value: 'vulnapi/auth.py'
      },
      {
        label: 'subject check',
        value: 'present'
      },
      {
        label: 'expiry check',
        value: 'missing'
      },
      {
        label: 'evidence',
        value: 'assertion + source'
      }
    ]
  },
  {
    n: '08',
    phase: 'Prove',
    name: 'Blast Radius',
    desc: 'Trace files connected to the proposed change. Reachability guides inspection; it does not prove that every connected file will break.',
    does: ['Identify origin', 'Traverse importers', 'Constrain edits'],
    input: 'Root cause + imports',
    output: 'Repair scope',
    artifact: 'Repair scope',
    evidence: [
      {
        label: 'origin',
        value: 'auth.py'
      },
      {
        label: 'direct importer',
        value: 'middleware.py'
      },
      {
        label: 'second hop',
        value: 'api.py'
      },
      {
        label: 'planned patch',
        value: 'auth.py only'
      }
    ]
  },
  {
    n: '09',
    phase: 'Repair',
    name: 'Context Engineering',
    desc: 'Keep the target, its failing test, and its immediate caller together. Irrelevant files leave the repair context instead of distracting the model.',
    does: ['Rank context', 'Keep relevant files', 'Package evidence'],
    input: 'Scope + repository index',
    output: 'Focused repair context',
    artifact: 'Focused repair context',
    evidence: [
      {
        label: 'example file pool',
        value: '6'
      },
      {
        label: 'selected',
        value: '3'
      },
      {
        label: 'target',
        value: 'auth.py'
      },
      {
        label: 'support',
        value: 'test + caller'
      }
    ]
  },
  {
    n: '10',
    phase: 'Repair',
    name: 'Repair Planner',
    desc: 'Order the change around a clear behavioral constraint: expired tokens must be rejected while valid-token behavior remains intact.',
    does: ['Define the constraint', 'Order changes', 'Plan validation'],
    input: 'Focused context',
    output: 'Ordered repair plan',
    artifact: 'Ordered repair plan',
    evidence: [
      {
        label: 'plan steps',
        value: '4'
      },
      {
        label: 'edit scope',
        value: '1 file'
      },
      {
        label: 'preserve',
        value: 'subject validation'
      },
      {
        label: 'validation',
        value: 'after generation'
      }
    ]
  },
  {
    n: '11',
    phase: 'Repair',
    name: 'Patch Generator',
    desc: 'Watch the unconditional acceptance turn into a diff. Removed code turns red; the expiry check is written in green. This is a candidate, not a verified fix.',
    does: [
      'Read original code',
      'Generate expiry guard',
      'Produce candidate diff'
    ],
    input: 'Repair plan',
    output: 'Candidate patch',
    artifact: 'Candidate patch',
    evidence: [
      {
        label: 'file',
        value: 'vulnapi/auth.py'
      },
      {
        label: 'added',
        value: '3 lines'
      },
      {
        label: 'removed',
        value: '1 line'
      },
      {
        label: 'validation',
        value: 'still required'
      }
    ]
  },
  {
    n: '12',
    phase: 'Validate',
    name: 'Mutation Validation',
    desc: 'First check the behavior after patching. Then introduce deliberate faults to see whether tests notice the missing guard, reversed comparison, and expiry boundary.',
    does: ['Check target behavior', 'Inject mutants', 'Observe test failures'],
    input: 'Candidate + example tests',
    output: 'Validation evidence',
    artifact: 'Validation evidence',
    evidence: [
      {
        label: 'expired token',
        value: 'rejected'
      },
      {
        label: 'valid token',
        value: 'accepted'
      },
      {
        label: 'example mutants',
        value: '3 / 3 caught'
      },
      {
        label: 'interpretation',
        value: 'tested faults detected'
      }
    ]
  },
  {
    n: '13',
    phase: 'Validate',
    name: 'Security Re-scan',
    desc: 'Compare findings before and after the patch. The unrelated SQL finding remains visible; no new finding is detected in this example.',
    does: ['Load baseline', 'Rescan candidate', 'Compare findings'],
    input: 'Baseline + candidate',
    output: 'Security delta',
    artifact: 'Security delta',
    evidence: [
      {
        label: 'baseline findings',
        value: '1'
      },
      {
        label: 'remaining findings',
        value: '1'
      },
      {
        label: 'new findings',
        value: '0 detected'
      },
      {
        label: 'existing issue',
        value: 'still needs review'
      }
    ]
  },
  {
    n: '14',
    phase: 'Validate',
    name: 'Mergeability Assessment',
    desc: 'Assemble the diff, checks, and limitations for the reviewer. The evidence supports a decision; it does not replace the developer who makes it.',
    does: ['Gather evidence', 'Inspect gates', 'Present review package'],
    input: 'Repair evidence',
    output: 'Human review package',
    artifact: 'Human review package',
    evidence: [
      {
        label: 'reproduction',
        value: 'confirmed in example'
      },
      {
        label: 'target behavior',
        value: 'passes after patch'
      },
      {
        label: 'existing SQL issue',
        value: 'unresolved'
      },
      {
        label: 'next action',
        value: 'human review'
      }
    ]
  }
]
