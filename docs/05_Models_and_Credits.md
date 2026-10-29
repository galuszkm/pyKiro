# 05 · Models and Credits

Every turn spends credits from your Kiro plan, and the model you pick decides how many. In this chapter we read
Kiro's price list for free, pick a model and an effort level, and see what one question costs.

## What you'll learn

- How to see which modes, models and effort levels Kiro offers you (`session.config_options`).
- How to read Kiro's "rate multiplier", a rough price tag per model.
- What `model=` and `effort=` do, and why a typo fails before any credits are spent.

## The big idea

When pyKiro creates a session, Kiro sends back the **options** it supports for it. pyKiro keeps them in
`session.config_options`. Each one is a `ConfigOption` with an `id`, the `current` value and the `values` you can
choose from. Three of them matter:

| Option `id` | What it is | Read it with |
|---|---|---|
| `mode` | Which Kiro agent works for you (like `vibe` or `plan`) | `session.mode` |
| `model` | Which AI model answers | `session.model` |
| `effortLevel` | How hard the model thinks before it answers | `session.effort` |

You choose model and effort when you open the session: `client.new_session(model=..., effort=...)`. More effort can
help on hard problems, but it takes longer and spends more. Reading the options is free: no turn, no credits.

## The full code

```python
"""See what your account offers for free, then ask one question with a chosen model and effort."""

import asyncio
import tempfile

from pykiro import KiroClient

MODEL = "claude-haiku-5.5"
EFFORT = "low"
PROMPT = "A bat and ball cost $1.10. The bat costs $1.00 more than the ball. How much is the ball? One short sentence."


def rate_of(entry: dict) -> str:
    """Kiro's rate multiplier for one model entry, or "?" when Kiro doesn't give one."""
    meta = entry.get("_meta", {})
    kiro = meta.get("kiro", {})
    rate = kiro.get("rateMultiplier")
    if rate is None:
        return "?"
    return f"{rate}x"


async def main() -> None:
    # An empty folder for the agent to work in
    with tempfile.TemporaryDirectory() as workspace:
        # 1. Start Kiro
        async with KiroClient(cwd=workspace) as client:
            # 2. Open a session with the model and effort we want
            session = await client.new_session(model=MODEL, effort=EFFORT)

            # 3. Read the price list (free: no question is sent)
            options = {option.id: option for option in session.config_options}
            print("Modes:", ", ".join(options["mode"].values))
            print(f"Efforts for {session.model}:", ", ".join(options["effortLevel"].values))
            print("Models (rate multiplier):")
            for entry in options["model"].raw["options"]:
                print(f"  {entry['value']:<28} {rate_of(entry)}")

            # 4. One small question
            result = await session.send(PROMPT)
            print()
            print(f"{session.model} at effort {session.effort}: {result.text.strip()}")
            print(f"That turn cost {result.credits:.4f} credits.")

            # 5. A typo in the model name is refused before any question is sent
            try:
                await client.new_session(model="gpt-1")
            except ValueError as error:
                message = str(error)
                print(f"Refused: {message[:60]}...")


if __name__ == "__main__":
    asyncio.run(main())
```

Save it as `price_list.py` and run `python price_list.py`. The price list costs nothing; the one question
cost about 0.01 credits.

### What you'll see

```text
Modes: vibe, spec, quick-spec, bug-fix, plan, autonomous, semantic_reviewer
Efforts for claude-haiku-5.5: low, medium, high, xhigh, max
Models (rate multiplier):
  auto                         1x
  claude-opus-5.5              2x
  claude-sonnet-5.5            1.3x
  claude-haiku-5.5             0.06x
  gpt-5.6-sol                  4.4x
  ...
  qwen3-coder-next             0.05x

claude-haiku-5.5 at effort low: The ball costs $0.05.
That turn cost 0.0094 credits.
Refused: 'gpt-1' is not a value of 'model': ['auto', 'claude-opus-5.5...
```

Your lists will differ: they depend on your account, plan, region and Kiro version. We cut the model list short.

## How it works

### The free price list

```python
options = {option.id: option for option in session.config_options}
print("Modes:", ", ".join(options["mode"].values))
print(f"Efforts for {session.model}:", ", ".join(options["effortLevel"].values))
```

`config_options` is a tuple, so we turn it into a dictionary by `id` to pick options by name. **The effort list
belongs to the model**: pyKiro sets your model first, Kiro re-sends the options, and only then does `effortLevel`
show your model's levels. Another model may have other levels, or none.

### The rate multiplier

```python
for entry in options["model"].raw["options"]:
    print(f"  {entry['value']:<28} {rate_of(entry)}")
```

`values` is only a list of names. Kiro's original entry has more in it, and pyKiro keeps it in `option.raw`. Observed
on kiro-cli 2.34.0: each model entry carries a `rateMultiplier` under `_meta.kiro`. That shape is Kiro's, not
pyKiro's, so `rate_of` digs in with `.get()` and prints `?` when something is missing.

Kiro's docs say the rate is *relative to Auto* at 1x ([Kiro: models](https://kiro.dev/docs/models/)). So haiku at
0.06x is far cheaper than `auto`, and that's why these docs use it. It's a rate, not a price per task: a model that
thinks longer or reads more spends more.

### Choosing model and effort

```python
session = await client.new_session(model=MODEL, effort=EFFORT)
```

pyKiro checks `model=` and `effort=` against what the session advertises. Observed on kiro-cli 2.34.0: Kiro itself
accepts any model id and only fails at the next prompt. With pyKiro's check, the `gpt-1` typo fails right away with a
`ValueError` that lists the valid choices, and no credits are spent. You can't switch the model of a live session:
open a new one.

### Where credits come from

There are three counters: `result.credits` (one turn), `session.credits` (one session) and `client.credits` (all
sessions of the client). All of them add up what Kiro reports after each turn. pyKiro can't see your bill.

## Gotchas, side effects and risks

- **Read the lists at runtime.** Don't hardcode another account's models in a shared script.
- **Pin your model in automation.** `auto` lets Kiro pick per task, which makes cost and behavior harder to predict.
- **The first turn can cost more.** In our runs the first turn on a fresh client cost more than later ones. Compare
  like with like.
- **Multiply before you loop.** Rate times number of calls. A big model in a batch is a big bill.

## Where to next

Previous: [04 · Streaming Events](04_Streaming_Events.md). Next: [06 · Structured Output](06_Structured_Output.md),
where the answer becomes a validated Python object. Back to [all chapters](README.md).
