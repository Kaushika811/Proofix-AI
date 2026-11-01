"""Organizational Learning System.

Sits after Enterprise Security and before the LLM Gateway. It makes the
*platform* smarter, not the model: every value here is derived by counting,
matching or aggregating observations that already happened.

    repair_memory       structured metadata for every completed repair
    style_learning      deterministic coding conventions
    framework_learning  framework detection and implied conventions
    pattern_mining      recurring repairs generalised into templates
    outcome_learning    what happened to a repair after it was suggested
    review_learning     what human reviewers keep asking for
    organization_memory preferences aggregated across repositories
    knowledge_index     the per-repository view A5.5 and A7 consume
    learning_engine     post-run extraction and profile updates
    metrics             analytics

Hard constraints, enforced by the absence of the relevant imports: no LLM call,
no embedding, no vector store, no training. Nothing in this package stores a
prompt, a patch body, a source file, a secret or a personal identifier.
"""
