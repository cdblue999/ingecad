# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Regenerate the model on several cores (#31).

A full regen is ezdxf's drawing frontend -- pure Python, one entity after
another -- and nearly all of it: on Plaza Yanque (10 000 objects) 4.26 s of
the frontend against 0.11 s of packing. Python threads share one GIL, so the
cores sat idle while the window waited.

Each worker is a fork of this process: it inherits the drawing as it is in
memory (nothing to serialise, nothing to re-read), draws ITS share of the
entities through the very same ``draw_layout`` -- a filter lets through only
its handles, so the redraw order, the background, the annotation scale and
every other rule are the ones a serial regen applies -- and sends back its
buckets. The parent stitches them in order and packs as before.

The shares are runs of consecutive entities in redraw order, cut by an
estimate of what each costs -- an INSERT costs what its block holds, nested
blocks included -- four per worker, each handed to whichever worker is
free. What cannot be split (one block of 15 000 objects) bounds the
speed-up.

Linux only (fork); anywhere else, for a small drawing, or if anything goes
wrong -- a worker that fails or takes too long -- the caller draws serially,
as it always did. ``INGECAD_SERIAL_REGEN=1`` forces the serial path.
"""
from __future__ import annotations

import logging
import os
import sys
import warnings
from dataclasses import fields
from typing import Optional

logger = logging.getLogger(__name__)

#: Below this estimated cost a fork costs more than it saves (measured: the
#: pool alone is ~0.1 s; a 3 000-object plan draws in well under a second).
MIN_COST = 20000
#: Cost of an entity that is not a block reference.
BASE_COST = {"HATCH": 40, "MTEXT": 6, "DIMENSION": 12, "ARC_DIMENSION": 12,
             "LEADER": 4, "MULTILEADER": 8, "MLEADER": 8, "SPLINE": 4,
             "TEXT": 3, "ATTRIB": 3}
#: Runs per worker: each takes the next free run when it finishes one.
SHARES_PER_WORKER = 4
#: A worker that has not answered in this long is given up on.
TIMEOUT_S = 60.0

_BUCKET_LISTS = None          # the list fields of Bucket, read once


def available() -> bool:
    return (sys.platform.startswith("linux") and hasattr(os, "fork")
            and os.environ.get("INGECAD_SERIAL_REGEN", "") not in ("1", "true"))


def workers() -> int:
    # Cap parallel workers to 4 to prevent memory explosion during large drawing regens
    # while maintaining maximum multi-core throughput.
    return max(1, min(4, (os.cpu_count() or 1) - 1))


def cost_model(doc):
    """``cost(entity)``: the estimated work of drawing one entity -- a
    block reference costs its whole content, nested blocks included."""
    memo: dict[str, int] = {}

    def block_cost(name: str, depth: int = 0) -> int:
        if name in memo:
            return memo[name]
        memo[name] = 1                       # a cycle costs little
        block = doc.blocks.get(name) if depth < 16 else None
        total = 1
        if block is not None:
            for e in block:
                total += cost(e, depth + 1)
        memo[name] = total
        return total

    def cost(entity, depth: int = 0) -> int:
        kind = entity.dxftype()
        if kind == "INSERT":
            return 1 + block_cost(entity.dxf.name, depth)
        return BASE_COST.get(kind, 1)

    return cost


def edit_cost(entities) -> int:
    """The estimated work of drawing ``entities`` (live ones only)."""
    entities = [e for e in entities if e.is_alive and e.doc is not None]
    if not entities:
        return 0
    cost = cost_model(entities[0].doc)
    return sum(cost(e) for e in entities)


def entity_costs(layout) -> list[tuple[str, int]]:
    """(handle, estimated cost) of every entity, in redraw order."""
    from ezdxf import reorder

    cost = cost_model(layout.doc)
    order = list(layout.get_redraw_order())
    entities = reorder.ascending(layout, order) if order else layout
    return [(e.dxf.handle, cost(e)) for e in entities]


def split(costs: list[tuple[str, int]], parts: int) -> list[frozenset]:
    """Consecutive runs of about the same total cost."""
    total = sum(c for _h, c in costs)
    target = total / parts
    runs, run, acc = [], [], 0.0
    for handle, c in costs:
        run.append(handle)
        acc += c
        if acc >= target * (len(runs) + 1) and len(runs) < parts - 1:
            runs.append(frozenset(run))
            run = []
    runs.append(frozenset(run))
    return [r for r in runs if r]


def draw_parallel(make_frontend, layout, *, min_cost: int = MIN_COST,
                  processes: Optional[int] = None, layout_properties=None):
    """Draw ``layout`` on several processes; the merged ``VertexBackend``
    and the skipped-entity notes -- or None, and the caller draws serially.

    ``make_frontend()`` builds a fresh (frontend, backend) pair, exactly as
    the serial regen does; it is called in each worker after the fork.
    ``layout_properties`` overrides the layout's own, as draw_layout takes
    it (the model resolved against the sheet it will be shown through).
    """
    return _fork_and_merge(make_frontend, layout, min_cost, processes,
                           lambda index, share: (layout, share, None, True,
                                                 layout_properties),
                           per_worker=SHARES_PER_WORKER)


def _fork_and_merge(make_frontend, model, min_cost, processes, plan,
                    per_worker: int):
    """Cut ``model``'s entities into runs and draw each on a forked worker.
    ``plan(index, run)`` says what the worker draws: (layout to draw, filter
    set for draw_layout or None, model share for the viewports or None,
    whether it draws the sheet's own entities)."""
    if not available():
        return None
    processes = processes or workers()
    if processes < 2:
        return None
    try:
        costs = entity_costs(model)
    except Exception as exc:                  # noqa: BLE001 - serial will do
        logger.warning("parallel regen: cannot estimate costs: %s", exc)
        return None
    if sum(c for _h, c in costs) < min_cost:
        return None
    # many small runs, handed to whichever worker is free: the estimate
    # only has to be roughly right (a solid HATCH costs little, an MTEXT
    # more than it looks), and the slowest run no longer sets the pace
    shares = split(costs, processes * per_worker)
    if len(shares) < 2:
        return None
    # the jobs stay here, inherited by the fork; only their NUMBER travels to
    # a worker -- a job holding the layout pickled the whole drawing, 0.65 s
    # per job, and the worker drew a copy instead of what it inherited
    jobs = [plan(index, share) for index, share in enumerate(shares)]

    def work(number):
        layout, only, model_share, paper, properties = jobs[number]
        if model_share is not None:
            _viewports_see_only(model_share)
        frontend, backend = make_frontend()
        frontend._model_share = model_share
        frontend._draws_paper = paper
        if only is not None:
            frontend.draw_layout(layout, filter_func=lambda e: e.dxf.handle in only,
                                 layout_properties=properties)
        else:
            frontend.draw_layout(layout, layout_properties=properties)
        return backend.buckets, backend.images, backend.background, list(frontend.skipped)

    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    global _WORK, _ACTIVE_POOL, _ABORT_REQUESTED
    if _ABORT_REQUESTED:
        return None
    _WORK = work                  # the forked children find it here
    try:
        with warnings.catch_warnings():
            # "this process is multi-threaded, fork() may deadlock": the
            # children run only ezdxf and never Qt (see _run_share), and a
            # child that dies or hangs anyway costs a serial regen, not the
            # drawing: a dead one is noticed at once (BrokenProcessPool),
            # a stuck one at the timeout
            warnings.simplefilter("ignore", DeprecationWarning)
            pool = ProcessPoolExecutor(
                max_workers=min(processes, len(jobs)),
                mp_context=multiprocessing.get_context("fork"))
            _ACTIVE_POOL = pool
            try:
                _spawn_gently(pool)
                if _ABORT_REQUESTED:
                    return None
                # the children fork on the first submit, inside this filter
                parts = list(pool.map(_run_share, range(len(jobs)),
                                      timeout=TIMEOUT_S))
            finally:
                _ACTIVE_POOL = None
                for process in list(getattr(pool, "_processes", {}).values()):
                    process.kill()    # a stuck child must not outlive the regen
                pool.shutdown(wait=False, cancel_futures=True)
    except Exception as exc:                  # noqa: BLE001 - serial will do
        logger.warning("parallel regen failed, drawing serially: %s: %s",
                       type(exc).__name__, exc)
        return None
    finally:
        _WORK = None
    merged = _merge(make_frontend()[1], parts)
    import gc
    gc.collect()
    return merged


_ACTIVE_POOL = None
_ABORT_REQUESTED = False


def abort_parallel() -> None:
    """Abort any running parallel regeneration immediately (e.g. on exit)."""
    global _ACTIVE_POOL, _ABORT_REQUESTED
    _ABORT_REQUESTED = True
    pool = _ACTIVE_POOL
    if pool is not None:
        try:
            for process in list(getattr(pool, "_processes", {}).values()):
                process.kill()
            pool.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        _ACTIVE_POOL = None


def reset_abort() -> None:
    """Reset the abort flag for subsequent regeneration."""
    global _ABORT_REQUESTED
    _ABORT_REQUESTED = False


#: Pause between two forks, so the GUI thread gets the interpreter back.
FORK_BREATH_S = 0.006


def _spawn_gently(pool) -> None:
    """Start the pool's workers one by one with a pause in between.

    A fork of the window's process holds the GIL for ~20 ms (its page
    tables: a real plan open is a gigabyte), and the executor forks all of
    them back to back on the first submit -- eight in a row froze the GUI
    for 160-240 ms at the start of EVERY parallel regen, on every edit of a
    big plan and every sheet switch (measured, Planos Constructivos). Spread
    out, they are eight hiccups of a frame each. ``_spawn_process`` is the
    executor's own (3.9+) and spawns only the missing workers afterwards;
    without it the executor forks the burst as before.
    """
    import time

    spawn = getattr(pool, "_spawn_process", None)
    processes = getattr(pool, "_processes", None)
    if spawn is None or processes is None:
        return
    try:
        while len(processes) < pool._max_workers:
            spawn()
            time.sleep(FORK_BREATH_S)
    except Exception:                         # noqa: BLE001 - the burst, then
        return


def _viewports_see_only(share) -> None:
    """In a worker: every viewport pass looks at this worker's model
    entities only. ezdxf computes a bounding box for EVERY model entity
    before drawing a viewport (filter_vp_entities fills its cache from the
    whole model space); per worker that was the whole model again, and a
    parallel sheet came out ten times slower than a serial one. The worker
    is a process of its own: patching the module touches no one else."""
    from ezdxf.addons.drawing import pipeline

    # a worker may take several jobs: always wrap ezdxf's own function,
    # never the previous job's wrapper (that filtered by BOTH shares, and
    # entities went missing at random)
    original = getattr(pipeline, "_ingecad_filter_vp_entities",
                       pipeline.filter_vp_entities)
    pipeline._ingecad_filter_vp_entities = original

    def filtered(msp, limits, bbox_cache=None):
        mine = [e for e in msp if e.dxf.handle in share]
        return original(mine, limits, bbox_cache)

    pipeline.filter_vp_entities = filtered


def draw_sheet_parallel(make_frontend, layout, *, min_cost: int = MIN_COST,
                        processes: Optional[int] = None):
    """A sheet on several processes: every worker draws the whole sheet
    through the same draw_layout, but in each viewport only ITS runs of the
    model, and only the first also draws the sheet's own entities. The
    viewports clip in ezdxf's pipeline as always. (merged backend,
    skipped notes) or None, as :func:`draw_parallel`."""
    doc = layout.doc
    if not any(e.dxftype() == "VIEWPORT" for e in layout):
        return None
    # one run per worker: each run pays for setting up every viewport
    return _fork_and_merge(make_frontend, doc.modelspace(), min_cost, processes,
                           lambda index, share: (layout, None, share, index == 0,
                                                 None),
                           per_worker=1)


_WORK = None


def _run_share(job):
    # The fork copied every object of the window, Qt wrappers included. A
    # garbage collection here would run their destructors outside Qt's
    # thread -- the crash core/gc_guard exists for. The child lives for
    # seconds: it never collects.
    import gc

    gc.disable()
    return _WORK(job)


def _merge(backend, parts):
    """The workers' buckets, stitched in share order into ``backend``."""
    global _BUCKET_LISTS
    skipped: list[str] = []
    for buckets, images, background, notes in parts:
        for key, bucket in buckets.items():
            mine = backend.buckets.get(key)
            if mine is None:
                backend.buckets[key] = bucket
                continue
            if _BUCKET_LISTS is None:
                _BUCKET_LISTS = [f.name for f in fields(bucket)
                                 if isinstance(getattr(bucket, f.name), list)]
            for name in _BUCKET_LISTS:
                getattr(mine, name).extend(getattr(bucket, name))
            mine.text_height_sum += bucket.text_height_sum
            mine.text_count += bucket.text_count
        backend.images.extend(images)
        if background is not None:
            backend.background = background
        skipped.extend(notes)
    return backend, skipped
