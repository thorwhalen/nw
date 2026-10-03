# nw.script_segmentation

`nw.script_segmentation` — narrow LLM-backed helper that converts a
free-form script into a list of storyboard-panel proposals.

This module is intentionally **focused and narrow**: it’s the smallest
possible thing that turns “user pasted some prose” into “n panels with
descriptions and durations” so a downstream UI can render them. It is
*not* a full [`nw.transforms.Transform`](nw.html.md#nw.Transform) — that abstraction will
absorb this work once it lands. For now we keep the surface as a plain
function with a dependency-injection seam (the `llm` arg), so:

- Tests can pass a deterministic stub (or a cassette-wrapped function)
  without needing API keys or network.
- The real implementation can swap between OpenAI / Anthropic / local
  models without callers caring.
- The cost-honesty rule (every billable call should be inspectable) is
  trivially upheld: the seam is the call.

Persisting the proposals as annotations is the *caller’s* job (this
module is pure — no project I/O). See
`reelee_backend.handlers.post_script_segment` for the wiring.

### Module Attributes

| [`LLM`](#nw.script_segmentation.LLM)   | The LLM seam — any function taking a string prompt and returning a string response.   |
|--------------------------------------------------------|---------------------------------------------------------------------------------------|

### Functions

| [`build_prompt`](#nw.script_segmentation.build_prompt)(script, \*, target_panel_count)   | The canonical prompt string sent to the LLM.                |
|-------------------------------------------------------------------------------------------------|-------------------------------------------------------------|
| [`segment_script_into_panels`](#nw.script_segmentation.segment_script_into_panels)(script, \*, ...)    | Segment `script` into `target_panel_count` panel proposals. |

### Classes

| [`PanelProposal`](#nw.script_segmentation.PanelProposal)(\*\*data)   | One storyboard panel proposed by the segmenter.   |
|----------------------------------------------------------------------------|---------------------------------------------------|

### nw.script_segmentation.LLM

The LLM seam — any function taking a string prompt and returning a
string response. Tests pass a cassette-wrapped stub; production passes
`oa.chat` (or whatever’s been wired).

alias of `Callable`[[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)], [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### *class* nw.script_segmentation.PanelProposal(\*\*data)

Bases: `BaseModel`

One storyboard panel proposed by the segmenter.

The shape is intentionally close to `annot://schema/storyboard-panel/v1`
(the lacing body schema) so the caller can promote a proposal into a
real panel annotation with a minimal mapping step.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### nw.script_segmentation.build_prompt(script, , target_panel_count)

The canonical prompt string sent to the LLM. Exposed so callers

+ tests can inspect / version it. **The cassette hashes this string**,
  so any change here invalidates recorded fixtures.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### nw.script_segmentation.segment_script_into_panels(script, , target_panel_count, llm)

Segment `script` into `target_panel_count` panel proposals.

Pure function — no I/O beyond the `llm` callable. The caller is
responsible for choosing / wrapping the LLM (e.g. with a cassette
or with caching).

* **Parameters:**
  * **script** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – Free-form prose. Whitespace is preserved verbatim in
    the prompt, so trimming + canonicalisation is the caller’s
    decision.
  * **target_panel_count** ([`int`](https://docs.python.org/3/builtins/functions.html#int)) – Soft target — the LLM is asked for exactly
    this many. Real-world deviations of ±1 are tolerated.
  * **llm** ([`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)], [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – The text→text seam. Receives the formatted prompt, must
    return a string. The expected response is a JSON array of
    `{description, duration_s, notes}` objects.
* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`PanelProposal`](#nw.script_segmentation.PanelProposal)]
* **Returns:**
  A list of validated [`PanelProposal`](#nw.script_segmentation.PanelProposal) instances.
* **Raises:**
  [**ValueError**](https://docs.python.org/3/builtins/exceptions.html#ValueError) – The LLM response could not be parsed as a JSON
      array of panels, or no valid panels survived validation.
