# Known failure cases

The plan calls for three published failures alongside the scoreboard. Two of these are real failure modes we hit during the build (with file:line) — the third becomes concrete once the real eval runs.

## 1. Citation pulled from a semantically-similar-but-wrong chunk (factuality)

**Mode:** Writer cites `[src/foo/bar.py:N-M]` for a behavioral claim, but the cited range belongs to a different module that happens to use overlapping vocabulary. The Critic catches it as `ungrounded` and the Editor revises — but the *first* draft was wrong.

**Why it happens:** Pure-semantic retrieval via `bge-small-en-v1.5`. When two modules use similar vocabulary (e.g. "queue" appearing in 5 unrelated places — a job queue, a UI message queue, a worker pool, a retry buffer, a publish/subscribe channel), the top-K hits can favour the wrong one. Function-level chunking and the file-path-prefix filter in the Reader (`agents/reader.py:_module_prefix`) reduce but don't eliminate this.

**Critic save:** see [src/docforge/agents/critic.py:judge_grounding](../src/docforge/agents/critic.py) — the LLM verdict on `claim ↔ chunk` text recovers the right answer in cycle 2.

**Fix candidate for v2:** BM25 + reranker hybrid retrieval. Pure-semantic embedding is the biggest single quality lever we haven't pulled yet.

## 2. Coverage false positive on substring match (coverage)

**Mode:** A public symbol like `Bar` was flagged as "missing" because Writer mentioned `BarChart` in the docs. The deterministic coverage check would have failed an otherwise-good doc.

**Why it happened:** First version of `compute_coverage` in [src/docforge/agents/critic.py](../src/docforge/agents/critic.py) used substring `in` instead of a word-boundary regex. Caught by `tests/test_critic_loop.py::test_coverage_word_boundary` before it ever shipped.

**Fix shipped:** word-boundary regex (`re.search(rf"\b{re.escape(s)}\b", ...)`). Test is now a regression guard.

**Lesson:** any "is this symbol mentioned?" heuristic must be word-boundary by default. Same logic applies to `find_uncited_factual_claims` — currently flags code-token-shaped lines without citations; a symbol-vs-prose disambiguation pass would tighten it further.

## 3. Diagrammer LLM beautify produces malformed Mermaid (diagram)

**Mode:** The LLM beautify pass occasionally introduces typos in `subgraph` blocks — unmatched `end`, stray quotes inside labels, or a label containing `]` that escapes the node syntax.

**Why it happens:** No `mermaid-cli` in the runtime container (Node-free Python image). We can't render the diagram to confirm it parses, so we rely on a structural validator.

**What ships now:** [src/docforge/agents/diagrammer.py:validate_mermaid](../src/docforge/agents/diagrammer.py) checks (a) a `flowchart`/`graph` declaration is present, (b) `subgraph` and `end` counts balance, (c) at least one node or edge declaration exists. On failure, the deterministic version produced by `mermaid_from_architecture` is kept — guaranteeing valid Mermaid is always emitted.

**Trade-off:** the user loses the nicer LLM-refined layout when validation rejects. Real `mmdc` validation (Node in the image) could let us retry the beautify with the error appended to the prompt and accept it after one re-roll. Postponed to v2.

## To be added once the real eval has run

The next three to publish are the concrete instances of the categories above as judged by Claude Sonnet 4.6 against the testset. Once `docforge-eval` produces `eval/scoreboard_data.json` with the 5×20 testset, each repo's lowest-scoring claim per category gets pinned here with the judge's reasoning, the cited chunk, and the actual code it should have cited. That gap is on the user, not the code.
