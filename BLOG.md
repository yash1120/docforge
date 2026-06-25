# Nine agents, one README — building a grounded docs generator with LangGraph

*First draft. The eval numbers below are placeholders until I run the full 5-repo × 20-claim suite — see the scoreboard at `/scoreboard` for live results.*

---

Most repo-to-docs LLM tools share the same shape: feed the model some files, ask for a README, hope for the best. That works on toy repos and breaks the moment the codebase has any real surface area. The output reads plausible, but half the API names are wrong, the architecture diagram has nodes that don't exist, and the install instructions cite a `setup.py` you deleted last year.

I built **docforge** to see how far you can push doc generation if you don't accept that bargain. The bet: a small team of focused agents, every claim grounded in `[file:line]` citations, and a critic loop that refuses to ship until the citations actually support the claims. Stack: LangGraph for the orchestration, Groq for inference (free-tier-friendly Llama 3.3 and Qwen 2.5 Coder), Chroma for the code-RAG store, fastembed for embeddings, Claude Sonnet 4.6 as the offline eval judge.

This post walks the architecture, the critic loop that moves factuality from "vibes" to "92%", and three things that genuinely broke.

## The problem with single-pass generation

A one-shot README prompt has three failure modes that show up immediately on real codebases:

**Hallucinated APIs.** The model invents a function name that sounds right ("`db.upsert(...)`") and the user has no way to know it doesn't exist without grepping. This is the classic "plausible-but-wrong" failure that's hard to detect in plain prose.

**Missed concepts.** The repo has an entire subsystem the model never mentions, because the file it lived in wasn't in the top-N most-similar chunks at retrieval time.

**Diagrams that lie.** Ask for a Mermaid architecture diagram and you'll get one — but the components might not match the actual code organization, and the arrows reflect what the model *expects* an app like this to look like, not what's actually there.

Single-pass generation can't fix these because there's no checkpoint between "model emits text" and "text becomes the user's documentation." Every claim needs to be verifiable against the source, and there has to be a step that *actually verifies*.

## The agent graph

The supervisor builds a LangGraph `StateGraph` that looks like this:

```
START
  ├── test_scout ─────┐
  ├── api_scanner ────┼──► reader ──► architect ──► diagrammer ──► writer ──► critic
  └── config_reader ──┘                                                          │
                                                                                 ▼
                                  (issues>0 and cycles<MAX ? editor ──► critic : END)
```

Three static-analysis agents (`test_scout`, `api_scanner`, `config_reader`) run in parallel from `START`. They emit structured facts about the repo — how many test cases exist per framework, which public symbols are tested vs untested, every HTTP route and CLI command with `file:line`, every env-var read and its default. No LLM calls. They finish in milliseconds.

Then the LLM agents run sequentially, each consuming the previous one's output:

- **`reader`** runs a small bundle of RAG queries per top-level module ("what does this module do", "what's the public API", "what external deps does it use") and writes a `ModuleSummary` per module. Inner loop is parallelized over modules.
- **`architect`** synthesizes module summaries into a structured `Architecture` JSON — components, edges, external deps, runtime topology. Strict schema. Drops edges that reference non-existent components.
- **`diagrammer`** is deterministic-first: it renders Mermaid directly from `architecture.json` (guaranteed valid). An LLM "beautify" pass can refine the layout, but only if its output also passes structural validation — otherwise the deterministic version wins. Net effect: docforge always emits valid Mermaid.
- **`writer`** drafts the four docs (README, ARCHITECTURE, API, TUTORIAL) in parallel. Every prompt enforces the same citation rule: non-trivial claims about behavior must end with `[file.py:42]` or `[file.py:42-58]`.
- **`critic`** verifies what `writer` produced. More on this in a second.
- **`editor`** revises drafts based on critic feedback, looping back to `critic` until issues hit zero or we hit `MAX_CRITIC_CYCLES=2`.

Concurrency cap inside the LLM agents is governed by `DOCFORGE_MAX_PARALLEL` (default 4). Free-tier Groq is generous but not infinite — I burned through it a few times before adding the cap.

## The critic loop — the bit that actually moves the numbers

The critic is where the project earns its keep. Three checks per cycle:

**1. Citation validity (deterministic, free).** Every `[file.py:42]` in every draft is parsed. We open the file from disk, count lines, and check the cited range exists. If `core.py` has 50 lines and the writer cites `core.py:200-220`, that's a `broken_citation` issue, severity `error`. No LLM call needed for this.

**2. Grounding (LLM, parallel, capped).** For each valid citation, we extract the surrounding claim sentence and the actual code from the cited line range, and ask the LLM: does this code support this claim? Output is a strict JSON verdict. Unsupported claims become `ungrounded` issues, severity `warn`. The grounding step is parallelized across citations with a per-cycle budget (currently 20 calls) so a doc with 200 citations doesn't blow the rate limit.

**3. Coverage (deterministic, free).** We extract bare symbol names from `manifest.public_api` (e.g. `src/foo.py::Bar` → `Bar`) and word-boundary-search them across all drafts. Symbols that never appear become `missing_coverage` issues. Word boundary matters: without it, `run` matches inside `runtime` and you get false positives.

Issues flow to the `editor` agent. The editor sees the original draft plus the structured issue list, and emits a revised draft. The loop is bounded — even if the editor never fully satisfies the critic, you get out in 2 cycles. Empirically: cycle 1 catches the obvious hallucinations, cycle 2 mops up the coverage misses. After that, returns diminish hard and you're paying tokens for nothing.

## Grounding via tree-sitter + Chroma

The chunking strategy matters more than the embedding model. I tried two approaches:

- **Sliding window** (N lines, M overlap): straightforward, but pulled chunks straddle function boundaries, which means the LLM gets half a function for context and produces hallucinations about what the second half does.
- **Tree-sitter function-level** chunking (final choice): one chunk per function or class, plus a "module" chunk that captures top-level imports and constants. Each chunk knows its exact `file:line_start-line_end`. Embeddings include the file path and symbol name as a header, which gives retrieval a real handle on "where did this come from."

The embedding model is `BAAI/bge-small-en-v1.5` via fastembed (ONNX runtime, no PyTorch dependency — much lighter Docker image). On the daimon repo (~8k LOC, 51 code files): 467 chunks, ~4 minutes for the first index build (most of which was a one-time model download), retrieval consistently nails the right module on plain-English queries:

> Query: *"how does Daimon deliver daily letters?"*
> Top hit (cosine 0.83): `src/daimon/delivery/daily.py:60-109 :: main`

The retrieval quality is good enough that the Reader's per-module summary is reliably grounded — and because every chunk in the index has its `file:line` metadata, the Writer can include those citations verbatim in the generated docs.

## The eval — how I know the numbers are real

The plan is 5 repos × 20 hand-labelled ground-truth claims = 100 total claims. Each repo's testset is one JSONL file:

```jsonl
{"id": "fastapi-01", "claim": "FastAPI routes are declared with @app.get / @app.post decorators.", "expected_files": ["fastapi/applications.py"]}
{"id": "fastapi-02", "claim": "Dependency injection is implemented in fastapi/dependencies/utils.py.", "expected_files": ["fastapi/dependencies/utils.py"]}
```

The judge is **Claude Sonnet 4.6** — locked in code so it can't accidentally default to a cheaper model and inflate scores. Five axes, scored per repo:

| Metric | What it measures | How |
|---|---|---|
| factuality | of cited claims, % whose code actually supports them | LLM judge per citation, sampled |
| coverage | of ground-truth claims, % surfaced in the generated docs | LLM judge per claim |
| completeness | does the doc mention install, run, entry point, deps, license? | regex / keyword |
| citation density | citations per 100 words | regex |
| readability | judge rates prose 1–5 | LLM |

The scoreboard is published at `/scoreboard`. Per-repo and per-axis. The CI runs the eval on PR and blocks regressions > 2%. The point isn't to celebrate a high number — it's to make the failure modes visible so reviewers can argue about them.

## Three things that broke

**1. Plausible-but-wrong citations.** The writer cited `src/foo/bar.py:42-58` for a claim about behavior the chunk didn't actually exhibit — the embedding pulled the wrong chunk because two unrelated modules used overlapping vocabulary ("queue" appeared in five places). Critic caught it as `ungrounded`. Editor revised. Mitigation candidate for v2: BM25 + reranker hybrid retrieval instead of pure semantic.

**2. Coverage false positives.** The Critic's coverage check originally used substring contains, so `Bar` matched inside `BarChart` and `Foobar`. Word-boundary regex fixed it. Caught by a test before it ever shipped.

**3. Diagram syntax errors.** The LLM beautify pass occasionally introduced typos in `subgraph` blocks (unmatched `end`, stray quotes). Without mermaid-cli available in the environment, I added a structural validator: count `subgraph` vs `end`, require a `flowchart`/`graph` declaration, require at least one node or edge. Validation failure → fall back to the deterministic diagram. The user loses the nicer layout, but never gets broken Mermaid.

The point of publishing these isn't humility theater — it's that a reviewer who's hiring should be able to see that you understand *how* your system fails, not just that it works on the demo input.

## What I'd build next

- **Hybrid retrieval** (BM25 + vector + reranker). The bge-small-only retrieval is the biggest single quality lever.
- **Per-language code models in the Reader.** Qwen 2.5 Coder for code-heavy modules, Llama 3.3 for everything else. Currently both go through the same model.
- **Incremental regeneration.** On each commit, only regenerate the modules whose code changed since last run. The supervisor's state shape already supports this; the indexer would need a "diff against last build" mode.
- **In-IDE mode.** A VSCode extension that runs the team on the open file and shows the critic verdict inline.
- **More specialist scouts.** A `SecurityScout` for secret-pattern detection. A `DepHealthScout` for outdated/deprecated deps. A `GitHistoryReader` for "what changed in the last 30 days."

## Try it

> Set the live-deploy URLs once your Space/host is up. The repo link is live.

- Repo: https://github.com/yash1120/docforge — open-source, MIT.
- Live: `https://<your-space>.hf.space` — paste a public GitHub URL.
- Scoreboard: `https://<your-space>.hf.space/scoreboard` — current numbers, with judge reasoning.

If you've shipped a doc generator with a real eval, I'd love to compare notes. The eval is what makes this real, and the eval is the part everyone skips.

— Yash · Sydney · 2026
