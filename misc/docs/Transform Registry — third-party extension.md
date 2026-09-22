# Transform registry — third-party extension

*Record of decision. Written 2026-09-22, resolving [nw#29](https://github.com/thorwhalen/nw/issues/29). The registry itself is unchanged in shape; only `tags` moved from latent to threaded.*

## The question

`nw.transforms` (`nw/transforms/__init__.py`) is documented as the extension point apps use to add their own Transforms without modifying nw. Every registrant today is first-party (nw, reelee, muvid, braidio — all owned by the same person). [nw#29](https://github.com/thorwhalen/nw/issues/29) asked, before any third party arrives: does the registry accept third-party registrations, and if so under what gate?

## The decision

**(a) Stay closed, for now.** Registration remains a first-party contract — nw and the apps built by the same owner. This is not a new restriction, just making explicit what was already true: nothing in nw advertises the registry as an open plugin surface, and nobody should build a third-party plugin against it yet.

**(b) is designed, not built.** If the registry opens later, [nw#29](https://github.com/thorwhalen/nw/issues/29)'s research (measured against the ComfyUI node ecosystem) is specific about what a gate needs:

- entry-point discovery with declared dependencies, rather than import-time side effects reaching into a shared namespace;
- a mandatory SPDX licence tag, validated against a closed enum — not a free-text field, because free text is how a real-world registry ends up with 76 spellings of "MIT";
- `output_kind` and `impl_version` already required at registration (landed independently of #29 — see the `register_transform` docstring);
- `Registry.__setitem__`'s silent last-writer-wins bypass of `on_conflict="error"` closed before any external registrant can shadow a built-in this way (tracked upstream: [i2mint/xdol#5](https://github.com/i2mint/xdol/issues/5));
- the licence vocabulary itself defined once, in [thorwhalen/falaw#16](https://github.com/thorwhalen/falaw/issues/16)'s licence/terms ledger, not invented separately here.

## What shipped now, regardless of (a) vs (b)

`register_transform(name, impl=None, *, tags=())` threads `tags` straight through to `xdol.Registry.register`, so a licence, a capability class, or a cost class has somewhere to live before it is ever enforced. Nothing validates the tag vocabulary yet — that enforcement is (b)'s job, not this change's. Doing this now costs nothing (the field already exists one layer down, in `xdol.Registry`) and removing the retrofit risk was the whole point of filing #29 before the registry had any external registrants to break.

## What this is not

Not a decision that the registry ever opens. Not an implementation of the licence enum, the entry-point loader, or the `__setitem__` fix — those stay wherever `nw#29`, `xdol#5`, and `falaw#16` are, unbuilt, until there is a real third party asking to register something.
