# Known failure cases

The plan calls for three published failures alongside the scoreboard. They're discovered (and updated) once the real eval has been run against the testset. Pre-seeded categories so reviewers can see what we'll look for:

## 1. Hallucinated behavior (factuality miss)

> Example placeholder — replace with a real failure once the eval has run.
>
> Symptom: Writer cites `src/foo/bar.py:42-58` for a claim about behavior the chunk doesn't actually exhibit. Critic flags ungrounded; Editor either drops the claim or re-cites a less-relevant chunk.
>
> Root cause: bge-small can pull the wrong chunk under semantic-only retrieval when multiple modules use similar vocabulary (e.g. "queue" appears in 5 places).
>
> Fix candidate: BM25 + reranker hybrid retrieval (Week-4 stretch).

## 2. Missed key concept (coverage miss)

> Placeholder. The Critic's coverage check only word-boundary matches symbol names. If the doc references a concept by paraphrase (e.g. talks about "task queue" but never names `JobQueue`), coverage will fire false positives.

## 3. Broken Mermaid (diagram miss)

> Placeholder. The LLM beautify pass occasionally introduces typos in `subgraph` blocks or quotes that fail the structural check; we fall back to the deterministic diagram, but the user loses the nicer layout.
