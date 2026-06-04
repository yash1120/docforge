"""Bounded parallel execution helper shared by Reader / Writer / Critic.

LLM calls inside the agent team are I/O bound and trivially parallelizable.
We use a ThreadPoolExecutor with a small worker cap to keep within free-tier
rate limits while still cutting wall-clock by 2-4x on real repos.

Tuneable via env: `DOCFORGE_MAX_PARALLEL` (default 4). Set to 1 to force
serial execution — useful for deterministic test ordering and for cheap
debugging when a parallel agent misbehaves.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Iterable, TypeVar

T = TypeVar("T")
R = TypeVar("R")


def _max_workers(default: int = 4) -> int:
    try:
        n = int(os.environ.get("DOCFORGE_MAX_PARALLEL", default))
    except ValueError:
        n = default
    return max(1, n)


def parallel_map(
    fn: Callable[[T], R],
    items: Iterable[T],
    *,
    preserve_order: bool = True,
    default_factory: Callable[[T, BaseException], R] | None = None,
) -> list[R]:
    """Apply `fn` to each item concurrently. Returns results in input order.

    Set `default_factory(item, exception) -> R` to swallow per-item failures —
    the surrounding agent can decide what an error means (e.g. "(reader failed)"
    fallback) rather than tearing down the whole graph.

    With `DOCFORGE_MAX_PARALLEL=1` falls back to serial execution.
    """
    items = list(items)
    if not items:
        return []

    workers = min(_max_workers(), len(items))
    if workers <= 1:
        # Serial path — cheaper than spinning up an executor.
        results: list[R] = []
        for item in items:
            try:
                results.append(fn(item))
            except BaseException as e:  # noqa: BLE001
                if default_factory is None:
                    raise
                results.append(default_factory(item, e))
        return results

    if preserve_order:
        out: list[R | None] = [None] * len(items)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_to_idx = {pool.submit(fn, item): i for i, item in enumerate(items)}
            for fut in as_completed(future_to_idx):
                i = future_to_idx[fut]
                try:
                    out[i] = fut.result()
                except BaseException as e:  # noqa: BLE001
                    if default_factory is None:
                        raise
                    out[i] = default_factory(items[i], e)
        return out  # type: ignore[return-value]

    # Unordered path — slightly faster when caller doesn't care about order.
    out_unordered: list[R] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_item = {pool.submit(fn, item): item for item in items}
        for fut in as_completed(future_to_item):
            try:
                out_unordered.append(fut.result())
            except BaseException as e:  # noqa: BLE001
                if default_factory is None:
                    raise
                out_unordered.append(default_factory(future_to_item[fut], e))
    return out_unordered
