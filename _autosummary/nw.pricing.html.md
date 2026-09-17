# nw.pricing

Re-quoting a persisted plan at today’s rates (nw#74).

nw writes cost figures into places that are **read back later**: a
render-decision payload, a `RenderResultBodyV1` body, a job’s cost gate.
Each of those numbers is a `falaw.CallPlan.estimated_cost_usd` frozen at
plan time. falaw’s rate tables move — 0.0.46 re-quoted every premium LLM call
*tenfold upward* — so a figure nw stored before a table moves under-quotes the
run it is later used to describe or gate. Under-quoting is the one direction a
spend decision must never err in.

falaw 0.0.49 (falaw#60) supplies the two halves of the fix:
`falaw.CostBasis` (which pricer, what it priced, the quantity hints,
the rate table and its content digest) and `falaw.reprice_plan()` (pure
data: no network, no billing API, no cache peek). This module is nw’s single
adoption point for them, and it enforces one rule:

**A stored figure is never reported as current.** Reading a persisted cost
means calling [`current_quote()`](#nw.pricing.current_quote) first and reporting
[`PlanQuote.total_usd`](#nw.pricing.PlanQuote.total_usd) with [`PlanQuote.status`](#nw.pricing.PlanQuote.status) beside it. A plan
whose calls carry no basis re-prices to `None` — *unknown*, never its stale
number, and never zero.

Two things this module deliberately does **not** do:

- **It does not touch cache identity.** `cost_basis` is descriptive: falaw
  omits it from the serialized call when unset and keeps it out of
  `plan_hash` and the per-call cache key. So a resumed render still dedups
  on the same digest, and [`nw.jobs.enqueue()`](nw.jobs.html.md#nw.jobs.enqueue)’s idempotency key is unmoved
  by anything here — `tests/test_pricing.py` pins that.
- **It does not re-quote money already spent.**
  [`nw.Project.total_spend_usd()`](nw.html.md#nw.Project.total_spend_usd) sums what was *billed*, and a receipt is
  > not a quote: re-pricing it at today’s rates would rewrite history. Its
  > estimate-based fallback is an as-of figure and says so.

```pycon
>>> from falaw import CallPlan, Plan
>>> stale = CallPlan(tool="text_to_video", application="fal-ai/x",
...                  arguments={}, output_kind="video",
...                  estimated_cost_usd=0.25)
>>> quote = current_quote(Plan(calls=(stale,)))
>>> quote.status, quote.total_usd, quote.as_of_total_usd
('unknown', None, 0.25)
```

### Module Attributes

| [`QuoteStatus`](#nw.pricing.QuoteStatus)                 | What a re-quote was able to say about a whole plan.                           |
|------------------------------------------------------------------------------|-------------------------------------------------------------------------------|
| [`TOTAL_AGREEMENT_ABS_TOL_USD`](#nw.pricing.TOTAL_AGREEMENT_ABS_TOL_USD) | How far a payload's stored total may sit from its calls' sum and still agree. |
| [`DISAGREEING_TOTAL_REASON`](#nw.pricing.DISAGREEING_TOTAL_REASON)    | Why a payload with an internally inconsistent total re-prices as unknown.     |

### Functions

| [`cost_records`](#nw.pricing.cost_records)(plan)                              | The JSON-able per-call cost rows nw persists in a decision payload.                                           |
|--------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------|
| [`current_quote`](#nw.pricing.current_quote)(plan, \*[, pricers])              | Re-quote `plan` at today's rates and report the result honestly.                                              |
| [`plan_from_cost_records`](#nw.pricing.plan_from_cost_records)(records)                 | Rebuild a re-quotable `falaw.Plan` from [`cost_records()`](#nw.pricing.cost_records) rows. |
| [`quote_from_cost_records`](#nw.pricing.quote_from_cost_records)(records, \*[, pricers]) | Today's price for the calls stored in a decision payload.                                                     |
| [`quote_render_decision`](#nw.pricing.quote_render_decision)(payload, \*[, pricers])   | Today's price for a `render_shot` decision payload.                                                           |
| [`unquotable`](#nw.pricing.unquotable)(reason)                              | A quote for something that could not be re-quoted at all.                                                     |

### Classes

| [`PlanQuote`](#nw.pricing.PlanQuote)(\*, total_usd, status, ...[, reason])   | Today's price for a persisted plan, with the stale figure alongside.   |
|----------------------------------------------------------------------------------------------------|------------------------------------------------------------------------|

### nw.pricing.DISAGREEING_TOTAL_REASON *= "the payload's total_estimated_cost_usd does not match the sum of its calls, so neither figure can be trusted as this render's price"*

Why a payload with an internally inconsistent total re-prices as unknown.

### *class* nw.pricing.PlanQuote(, total_usd, status, as_of_total_usd, repriced, reason='')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Today’s price for a persisted plan, with the stale figure alongside.

The stale figure is kept — as [`as_of_total_usd`](#nw.pricing.PlanQuote.as_of_total_usd), explicitly named
“as of then” — because an audit surface wants to show the movement. What
it must never do is *present* it as current; that is what
[`total_usd`](#nw.pricing.PlanQuote.total_usd) and [`status`](#nw.pricing.PlanQuote.status) are for.

#### as_of_total_usd *: [float](https://docs.python.org/3/builtins/functions.html#float) | [None](https://docs.python.org/3/builtins/constants.html#None)*

What the persisted plan said, `None` if it already said unknown.

A fact about the moment it was written. Render it labelled as such, or
not at all.

#### *property* basis_changed *: [bool](https://docs.python.org/3/builtins/functions.html#bool)*

True when a rate table moved underneath at least one call.

The audit answer the frozen number could never give: it separates “the
price changed because the *table* changed” from “the price changed
because the plan did”. Read it beside [`status`](#nw.pricing.PlanQuote.status) — a `changed`
with this `False` is a caller quoting different quantities, not a
repricing event.

#### *property* delta_usd *: [float](https://docs.python.org/3/builtins/functions.html#float) | [None](https://docs.python.org/3/builtins/constants.html#None)*

`total_usd - as_of_total_usd`, or `None` when either is unknown.

`None` rather than `0.0`: a plan that lost its price did not move
by zero.

#### *property* has_unknown_costs *: [bool](https://docs.python.org/3/builtins/functions.html#bool)*

True when the total cannot be known — the gate’s refusal condition.

The same judgement as `falaw.Plan.has_unknown_costs`, made at
re-quote time rather than at plan time.

#### reason *: [str](https://docs.python.org/3/builtins/stdtypes.html#str)*

Why the *whole* quote is unknown, when that is the situation.

Set by [`unquotable()`](#nw.pricing.unquotable) (the thing handed in was not a plan) and by
[`quote_render_decision()`](#nw.pricing.quote_render_decision) (the payload contradicts itself). Empty for
an ordinary re-quote, where the per-call reasons live on
[`repriced`](#nw.pricing.PlanQuote.repriced) instead.

#### repriced *: RepricedPlan*

falaw’s per-call diff — `status`, `basis_changed`, `reason` per
call. Read it for the audit view; [`total_usd`](#nw.pricing.PlanQuote.total_usd) is the headline.

#### status *: [Literal](https://docs.python.org/3/library/typing.html#typing.Literal)['unchanged', 'changed', 'unknown']*

Which of the three cases this plan fell into — see [`QuoteStatus`](#nw.pricing.QuoteStatus).

#### to_dict()

JSON-able headline for a surface (an API response, a job record).

Deliberately not the whole per-call diff: a spend surface needs the
number, whether it is knowable, and whether it moved. Reach into
[`repriced`](#nw.pricing.PlanQuote.repriced) for the rest.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

#### total_usd *: [float](https://docs.python.org/3/builtins/functions.html#float) | [None](https://docs.python.org/3/builtins/constants.html#None)*

Today’s billable total, or `None` when any billable call is
unpriceable today. `None` means unknown, never free (nw invariant #2).

### nw.pricing.QuoteStatus

What a re-quote was able to say about a whole plan.

- `"unchanged"` — every billable call re-quoted, and the total did not move.
- `"changed"` — re-quoted, and at least one call’s price moved. The total in
  [`PlanQuote.total_usd`](#nw.pricing.PlanQuote.total_usd) is today’s; the stored one was yesterday’s.
- `"unknown"` — at least one billable call could not be re-quoted (no basis,
  no pricer, a model that left the catalogue, a table that moved out from
  under it). [`PlanQuote.total_usd`](#nw.pricing.PlanQuote.total_usd) is `None`: unknown, never free, and
  never the frozen figure.

alias of [`Literal`](https://docs.python.org/3/library/typing.html#typing.Literal)[‘unchanged’, ‘changed’, ‘unknown’]

### nw.pricing.TOTAL_AGREEMENT_ABS_TOL_USD *= 1e-09*

How far a payload’s stored total may sit from its calls’ sum and still agree.

Floating-point slack on a sum of a handful of costs, nothing more — far below
the smallest sub-cent figure any rate table quotes, so it can never absorb a
real disagreement.

### nw.pricing.cost_records(plan)

The JSON-able per-call cost rows nw persists in a decision payload.

A serialized call cannot be re-quoted from `application` and
`arguments` alone — the quantity hints that priced it are estimator-only
and never reach the wire arguments. Carrying `cost_basis` alongside the
frozen figure is what makes the row re-quotable later by
[`quote_from_cost_records()`](#nw.pricing.quote_from_cost_records).

`cost_basis` is omitted when unset, exactly as falaw omits it, so a row
written by a caller that records no basis is byte-identical to what nw
wrote before nw#74.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]]

```pycon
>>> from falaw import CallPlan, Plan
>>> call = CallPlan(tool="t", application="a", arguments={},
...                 output_kind="video", estimated_cost_usd=1.0)
>>> sorted(cost_records(Plan(calls=(call,)))[0])
['application', 'cache_status', 'estimated_cost_usd', 'tool']
```

### nw.pricing.current_quote(plan, \*, pricers={'llm_rates': Pricer(quote=<function \_quote_from_llm_rates>, table='falaw/data/llm_rates.json', version=<functools._lru_cache_wrapper object>), 'model_catalogue': Pricer(quote = <function \_quote_from_catalogue>, table='falaw/data/models.json', version=<functools._lru_cache_wrapper object>)})

Re-quote `plan` at today’s rates and report the result honestly.

Pure data — `falaw.reprice_plan()` reads the committed rate tables and
does arithmetic. No network, no billing API, no cache peek, so this is
safe to call anywhere a `plan()` is (nw invariant #1).

* **Parameters:**
  * **plan** (`Plan`) – The plan to re-quote — typically one just rebuilt from a stored
    payload with [`plan_from_cost_records()`](#nw.pricing.plan_from_cost_records).
  * **pricers** ([`Mapping`](https://docs.python.org/3/library/typing.html#typing.Mapping)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), `Pricer`]) – Pricing rules by `falaw.CostBasis.pricer`. The seam for
    a caller with reconciled numbers of their own; see
    `falaw.reprice.Pricer`.
* **Return type:**
  [`PlanQuote`](#nw.pricing.PlanQuote)

```pycon
>>> from falaw import Plan
>>> current_quote(Plan(calls=())).status
'unchanged'
```

### nw.pricing.plan_from_cost_records(records)

Rebuild a re-quotable `falaw.Plan` from [`cost_records()`](#nw.pricing.cost_records) rows.

The result is a **pricing** plan, not an executable one: `arguments` is
empty and `output_kind` is a placeholder, because a stored cost row does
not carry the wire payload and re-pricing does not read it. Never hand one
of these to `falaw.execute_plan()` — build a fresh plan for that.

Rows missing `cost_basis` come back basis-free, which is exactly what
makes them re-price as `"no_basis"` (unknown) rather than as their
frozen number.

* **Return type:**
  `Plan`

### nw.pricing.quote_from_cost_records(records, \*, pricers={'llm_rates': Pricer(quote=<function \_quote_from_llm_rates>, table='falaw/data/llm_rates.json', version=<functools._lru_cache_wrapper object>), 'model_catalogue': Pricer(quote = <function \_quote_from_catalogue>, table='falaw/data/models.json', version=<functools._lru_cache_wrapper object>)})

Today’s price for the calls stored in a decision payload.

The read-back half of [`cost_records()`](#nw.pricing.cost_records). `None` or a non-sequence
(a payload that recorded no calls at all) yields an empty plan’s quote —
`total_usd == 0.0`, `status == "unchanged"` — because “no calls” is a
known zero, not an unknown.

* **Return type:**
  [`PlanQuote`](#nw.pricing.PlanQuote)

### nw.pricing.quote_render_decision(payload, \*, pricers={'llm_rates': Pricer(quote=<function \_quote_from_llm_rates>, table='falaw/data/llm_rates.json', version=<functools._lru_cache_wrapper object>), 'model_catalogue': Pricer(quote = <function \_quote_from_catalogue>, table='falaw/data/models.json', version=<functools._lru_cache_wrapper object>)})

Today’s price for a `render_shot` decision payload.

The counterpart to what `nw.workflow._record_render_decision()` wrote.
Read this — never `payload["total_estimated_cost_usd"]` — whenever a
stored render cost is about to be shown or gated on as a *current* figure.

A payload written before nw#74 carries no per-call basis, so it re-quotes
as `"unknown"` with `total_usd` `None`. That is the point: nobody
can say what it costs today, and saying so is better than repeating a
number that has since moved.

The stored `total_estimated_cost_usd` is the payload’s own headline, so
it — not the calls’ sum — is reported as [`PlanQuote.as_of_total_usd`](#nw.pricing.PlanQuote.as_of_total_usd).
When the two **disagree**, the whole payload is *unknown*: a total of $3
over a payload whose calls sum to $0 is a broken record, and answering
“$0, unchanged” would report a stored $3 as a known zero. nw’s own writer
never produces such a payload; a hand-edited or truncated one can, and
unknown is the only honest reading of it.

* **Return type:**
  [`PlanQuote`](#nw.pricing.PlanQuote)

```pycon
>>> broken = quote_render_decision(
...     {"calls": [], "total_estimated_cost_usd": 3.0})
>>> broken.status, broken.total_usd, broken.as_of_total_usd
('unknown', None, 3.0)
>>> stale = quote_render_decision(
...     {"calls": [{"tool": "t", "application": "a",
...                 "estimated_cost_usd": 3.0}],
...      "total_estimated_cost_usd": 3.0})
>>> stale.status, stale.total_usd, stale.as_of_total_usd
('unknown', None, 3.0)
```

### nw.pricing.unquotable(reason)

A quote for something that could not be re-quoted at all.

For the caller who was handed a *plan-shaped* thing that turned out not to
be a plan — an unparseable payload, an object of the wrong type. The honest
answer is `None` (unknown), not the frozen figure that came with it, and
not `0.0`.

[`PlanQuote.repriced`](#nw.pricing.PlanQuote.repriced) is an empty `falaw.RepricedPlan`: there
were no calls to diff. Read [`PlanQuote.reason`](#nw.pricing.PlanQuote.reason) for what went wrong.

* **Return type:**
  [`PlanQuote`](#nw.pricing.PlanQuote)

```pycon
>>> q = unquotable("params['plan'] is not a falaw Plan")
>>> q.status, q.total_usd, q.has_unknown_costs
('unknown', None, True)
```
