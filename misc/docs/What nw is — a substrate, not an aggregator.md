# What nw is — a substrate, not an aggregator

*Record of decision for nw#96 ("Lighten nw"). Written 2026-10-01, measured against nw 0.0.62 and its three dependents at their `main`.*

## The question

nw requires `falaw`, `lacing` and `artful`. Is it a core tool that happens to have grown heavy dependencies, or an aggregator that bundles other packages? And whichever it is, it should be that fully, not something in between.

## The answer

**nw is a substrate: a core tool.** It is the layer where lacing's annotation graph meets falaw's costed execution, and those two are not things nw *bundles*. They are the two halves it *joins*. Every surface nw has is written in them:

| dependency | modules that import it | what nw uses it for |
|---|---|---|
| `lacing` | 26, every surface | the annotation model, stores, provenance, body-schema registry, digests |
| `falaw` | 15 | the `Plan` IR that every `Transform.plan()` returns, cost estimates, the content-addressed cache, `plan_hash` (job idempotency) |
| `pydantic`, `dol`, `xdol` | throughout | the vocabulary those contracts are written in (models, mappings, registries) |
| `au` | 1, `nw/jobs.py` | the durable job backend under `nw.jobs` |
| `artful` | 1, `nw/storyboard.py` | the storyboard data layer under the storyboard bridge |

Making `lacing` or `falaw` optional would not make nw lighter. It would make nw redefine a graph or a plan of its own, which is to become one of them. A core tool without its core is the "something in between" the issue warns about.

The aggregator smell was real, though, and it had two concrete causes:

1. **`artful` was a required dependency for one module that nothing depends on.** `nw.storyboard` is the only importer, and none of reelee, braidio or muvid imports it.
2. **`import nw` imported everything.** `nw.jobs` imports `au`, and `au`'s package import pulls its HTTP surface (`fastapi`, `flask`) whenever those are installed. That was the largest single share of `import nw`: about a third of 760 ms, paid by every caller that never enqueues a job.

## What changed

- `artful` moved to an extra: `pip install 'nw[storyboard]'`. Without it, nw imports and works, a project someone else storyboarded is still walkable (`iter_all_annotations` reads the panels), and reaching for the storyboard fails at the reach with an `ImportError` that names the extra.
- `nw.jobs` and `nw.storyboard` load on first touch, through a module `__getattr__` in `nw/__init__.py` (`_LAZY_SUBMODULES`, `_LAZY_ATTRS`). Every spelling callers use still works: `nw.jobs.enqueue`, `nw.open_storyboard`, `from nw import save_storyboard`, `import nw.storyboard`. `dir(nw)` lists the lazy names without loading them. They stay out of `__all__`, so `from nw import *` never reaches for an extra that is not installed.
- When the extra is missing, `import nw.storyboard` raises `nw._extras.MissingExtra` (an `ImportError`) naming the extra, and `nw.open_storyboard` raises an `AttributeError` carrying the same message, chained to it. The attribute lookup has to answer `AttributeError`: `hasattr`, `getattr(nw, name, default)`, `help(nw)` and `inspect.getmembers(nw)` treat nothing else as "not there", and they crashed on an `ImportError` (found in review). The cost is one spelling: `from nw import save_storyboard` without the extra gives Python's generic "cannot import name", because the import machinery drops the attribute error's message. A broken install (a dependency *of* artful, or a submodule of an installed artful, failing to import) is never relabelled as a missing extra.
- **Not everything moved with zero effect.** `artful` registers four lacing body schemas at import (`storyboard-panel/v1`, `storyboard-meta/v1`, `model-sheet/v1`, `shot-schedule/v1`). After a plain `import nw` they are no longer registered until something touches `nw.storyboard` or imports `artful`. No dependent relied on it (reelee imports artful itself), but a caller that exports or lists body schemas and wants those four imports the feature first.
- The published API docs build installs nw without extras, so the `nw.storyboard` page can no longer import `artful` there and will be thin. That is the price of the extra and not worth a docs-only dependency.
- `import nw` went from about 760 ms to about 275 ms on the reference Mac, and no longer loads `au`, `artful`, `fastapi` or `flask`.
- `au` stays required. The host contract uses `nw.jobs` (reelee and muvid enqueue through it), and `au` installs nothing of its own, so making it an extra would break two dependents to save nothing on disk. What it cost was import time, and laziness removes that.

## The rule from here on

1. **A dependency is required only if the substrate's contract is written in it**, or if it backs a surface the dependents use as part of the host contract *and* installs nothing further.
2. **A dependency that one feature needs goes in an extra**, named after the feature, and the feature's module raises an `ImportError` naming the extra.
3. **`import nw` loads the contract.** A feature module whose dependency is not free to import is added to `_LAZY_SUBMODULES` (and its re-exported names to `_LAZY_ATTRS`) instead of being imported eagerly in `nw/__init__.py`. The values are full module paths, so a module that later moves out of nw (nw#97) can leave a forwarding entry and its callers unchanged.

`tests/test_import_footprint.py` pins all three (and the root `conftest.py` keeps the suite collectable without an extra): the required set is exact (adding one fails the suite and points here), `import nw` loads none of the feature dependencies, and nw works with `artful` absent.

## What this does not settle

- **The shot render path is application code in a substrate.** `nw.workflow`, `nw.renderers` and `nw.script_segmentation` are the music-video pipeline nw started as. None of the three dependents calls them directly, but nw's own shot Transform (`shot_to_render_result.fal.<name>`) is built on them. They add no dependency, so they do not make nw heavier; whether they move out, and to where, is a design decision: nw#97.
- **falaw's install weight.** falaw requires `fal-client` (and with it an HTTP stack) for every caller, including ones that only build and price plans. That is falaw's coupling of its IR to one backend client, and falaw's decision.
- **`au`'s own import cost** for callers that do touch `nw.jobs`: i2mint/au#4.
- **The host media-catalog writer (nw#92) and the shared media store (nw#95).** Under the rule above, a writer that needs only the standard library and `lacing` may live in the core; a backend with a dependency of its own is a `store=` seam whose default needs none.
