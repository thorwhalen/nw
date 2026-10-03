# nw.secrets

Execution secrets — credentials that reach `execute` and nothing else.

A caller’s bring-your-own API key has to reach the one place that spends it
([`nw.Transform.execute()`](nw.html.md#nw.Transform.execute), or the render callable behind a
[`nw.jobs.enqueue()`](nw.jobs.html.md#nw.jobs.enqueue)) **without** going through the graph: a key must
never be persisted in a node body, provenance, a `falaw.Plan`, a cache
key, a run record, the job index or a log line. This module is the seam that
carries it, and [`Secrets`](#nw.secrets.Secrets) is what makes the invariant enforced rather
than promised.

**The shape.** `execute(..., *, secrets=...)` is a keyword-only argument,
passed accepts-it-or-not by [`nw.fan_out_execute()`](nw.html.md#nw.fan_out_execute) and by
[`nw.jobs.enqueue()`](nw.jobs.html.md#nw.jobs.enqueue)’s dispatch — the same seam `on_failure` (nw#25) and
`unit_instance_id` (nw#44) use — so a Transform that spends a caller’s
credential declares the keyword, and one that does not never sees it.
Secrets are keyed by **provider name** (`"fal"`, `"elevenlabs"`, …): nw
owns [`FAL_SECRET`](#nw.secrets.FAL_SECRET), an app owns the names of the providers it calls.

**Why a type, not a dict.** A [`Secrets`](#nw.secrets.Secrets) is a read-only
[`Mapping`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Mapping) whose `repr`/`str` redact every value,
that refuses to be pickled, and that is deliberately *not* a `dict` — so
`json.dumps` (and pydantic) of anything that accidentally holds one raises
instead of writing the key. Absent values are dropped at construction, so a
boundary can pass an optional header value straight through:
`Secrets(elevenlabs=request_header)` is empty — and falsy — when the header
was not sent, which every consumer reads as “use the process environment”.

```pycon
>>> s = Secrets(elevenlabs="sk-live-…", fal=None)
>>> sorted(s)
['elevenlabs']
>>> s
Secrets(<1 redacted: elevenlabs>)
>>> bool(Secrets(fal=None))
False
>>> import json
>>> json.dumps({"secrets": s})  # a record can never carry one by accident
Traceback (most recent call last):
    ...
TypeError: Object of type Secrets is not JSON serializable
```

### Module Attributes

| [`FAL_SECRET`](#nw.secrets.FAL_SECRET)   | `nw.BaseTransform.execute()` and the [`nw.jobs`](nw.jobs.html.md#module-nw.jobs) worker bind it as the fal credential (`falaw.using_fal_credentials()`) for the duration of the call.   |
|---------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|

### Functions

| [`as_secrets`](#nw.secrets.as_secrets)(secrets)              | Coerce a caller-supplied mapping to [`Secrets`](#nw.secrets.Secrets); empty → `None`.   |
|-----------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------|
| [`using_secrets`](#nw.secrets.using_secrets)(secrets)           | Bind the secrets nw itself knows how to use, for the duration of a block.                                       |
| [`redact`](#nw.secrets.redact)(text, secrets)            | `text` with every secret value replaced by `<redacted:name>`.                                                   |
| [`redact_exception`](#nw.secrets.redact_exception)(error, secrets) | The exception to re-raise so that nothing it *renders* carries a secret.                                        |

### Classes

| [`Secrets`](#nw.secrets.Secrets)([mapping])   | A read-only `{provider_name: key}` mapping that never prints or persists.   |
|-----------------------------------------------------------------------|-----------------------------------------------------------------------------|

### Exceptions

| [`RedactedError`](#nw.secrets.RedactedError)(message, \*, original_type)   | An exception re-raised in place of one whose rendered text quoted a secret and whose type could not be rebuilt with the scrubbed text.   |
|----------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------|

### nw.secrets.FAL_SECRET *= 'fal'*

`nw.BaseTransform.execute()` and the
[`nw.jobs`](nw.jobs.html.md#module-nw.jobs) worker bind it as the fal credential
(`falaw.using_fal_credentials()`) for the duration of the call.

* **Type:**
  The secret name nw itself consumes

### *exception* nw.secrets.RedactedError(message, , original_type)

Bases: [`RuntimeError`](https://docs.python.org/3/builtins/exceptions.html#RuntimeError)

An exception re-raised in place of one whose rendered text quoted a secret
and whose type could not be rebuilt with the scrubbed text.

`original_type` names what it stood in for, so a caller classifying on
the falaw hierarchy still learns what happened; `str()` is the scrubbed
rendering. The typed fallback of [`redact_exception()`](#nw.secrets.redact_exception).

### *class* nw.secrets.Secrets(mapping=None, , \*\*named)

Bases: [`Mapping`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Mapping)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

A read-only `{provider_name: key}` mapping that never prints or persists.

Construct from a mapping, keywords, or both; `None`/empty values are
dropped (absent means “not supplied”), a non-`str` key or value is a
`TypeError` — a secret is text, and an int or a bytes object here is a
caller bug worth failing on.

#### with_(\*\*named)

A copy with `named` layered on top (a boundary adding a provider).

* **Return type:**
  [`Secrets`](#nw.secrets.Secrets)

### nw.secrets.as_secrets(secrets)

Coerce a caller-supplied mapping to [`Secrets`](#nw.secrets.Secrets); empty → `None`.

The nw entry points — `nw.BaseTransform.execute()`,
[`nw.fan_out_execute()`](nw.html.md#nw.fan_out_execute), [`nw.jobs.enqueue()`](nw.jobs.html.md#nw.jobs.enqueue) — run every incoming
`secrets` through this, so below *them* a Transform only ever sees the
redacting type. A Transform that **overrides** `execute` and is called
directly gets whatever the caller passed: an override that logs or
formats its `secrets` should `as_secrets` first (or the caller should
hand it a [`Secrets`](#nw.secrets.Secrets)), because a plain `dict` prints its values.

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`Secrets`](#nw.secrets.Secrets)]

```pycon
>>> as_secrets(None) is None
True
>>> as_secrets({"fal": None}) is None
True
>>> as_secrets({"fal": "k"})
Secrets(<1 redacted: fal>)
```

### nw.secrets.redact(text, secrets)

`text` with every secret value replaced by `<redacted:name>`.

For the places nw persists free text it did not author — an exception
message, a failure reason — while holding the values that must not land
there. Cheap, exact-substring, and a no-op with no secrets.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

```pycon
>>> redact("boom: key sk-1 rejected", {"fal": "sk-1"})
'boom: key <redacted:fal> rejected'
>>> redact("nothing here", None)
'nothing here'
```

### nw.secrets.redact_exception(error, secrets)

The exception to re-raise so that nothing it *renders* carries a secret.

Scrubs `args` and `__notes__` in place and, when `str(error)` is
still not clean — an exception whose message is built from a non-string
arg (`RuntimeError({"detail": key})`, `OSError(2, msg, path)`) or a
custom `__str__` — rebuilds it as `type(error)(scrubbed_text)`, falling
back to [`RedactedError`](#nw.secrets.RedactedError) when the type will not construct that way
or still renders the secret. The cause/context chain is scrubbed the same
way. Returns the object to raise: the original when it was already clean.

Applied where nw lets an exception escape toward a store it does not own
(the job worker: au persists the rendered text) or files it into a record
it does (a fan-out unit’s `reason`).

* **Return type:**
  [`BaseException`](https://docs.python.org/3/builtins/exceptions.html#BaseException)

### nw.secrets.using_secrets(secrets)

Bind the secrets nw itself knows how to use, for the duration of a block.

Today that is [`FAL_SECRET`](#nw.secrets.FAL_SECRET): when present it becomes the fal
credential (`falaw.using_fal_credentials()`) so every `call_fal`
inside the block authenticates with the caller’s key instead of the
server’s `FAL_KEY`. Anything else in `secrets` is left for the
Transform that declared it. With no fal secret this is a `nullcontext`,
so the `with` shape stays uniform.

* **Return type:**
  [`AbstractContextManager`](https://docs.python.org/3/library/contextlib.html#contextlib.AbstractContextManager)[[`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]
