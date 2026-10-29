# 06 · Structured Output

A program usually needs *data*, not prose: a name, a date, a list of tasks. Parsing reply text is fragile. In this
chapter we hand Kiro some messy meeting notes and get back a validated Python object with the action items.

## What you'll learn

- How to get a typed Python object from a turn with `result_type=`.
- How to describe the shape you want with a Pydantic model.
- Why "valid" doesn't mean "true".

## The big idea

[Pydantic](https://docs.pydantic.dev/) is a popular library where you describe data as a class with typed fields,
and it checks the data for you. Pass your class as `result_type=` and the turn gives you an instance of it in
`TurnResult.output`.

Under the hood, pyKiro gives the agent one extra tool, `submit_result`, whose argument is described by your model's
**JSON Schema** (a machine-readable description of the fields, their types and limits). It also adds one sentence to
your prompt: call `submit_result` once with the final result. When the agent calls it, pyKiro validates the
arguments. If they don't fit, the agent gets an error saying what's wrong and tries again in the same turn. TL;DR:
text in, typed object out.

## The full code

```python
"""Turn messy meeting notes into validated action items."""

import sys
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from pykiro import KiroError, ask_sync

MODEL = "claude-haiku-5.5"

NOTES = """
Weekly sync, Monday 2 March 2026. Present: Ana, Ben, Carla.

Ben says the checkout page is still slow on mobile. Ana thinks it's the image
sizes. Decision: we ship the smaller images first and look at the code later.
Ben will resize the product images by Friday, that's urgent because of the sale.

Carla wants to move the support inbox to the new tool. Everybody agreed, but
nobody is in a hurry. She'll write the migration plan before 13 March.

Also: Ana to book the team lunch, whenever. And we decided to skip the
retrospective this month, we are too busy.
"""


# 1. Describe the shape of the answer we want
class ActionItem(BaseModel):
    owner: str = Field(description="First name of the person who does the task")
    task: str = Field(description="What has to be done, in one short sentence", max_length=80)
    priority: Literal["high", "normal", "low"]
    due: date | None = Field(default=None, description="Due date. Leave it empty if the notes give none.")


class MeetingSummary(BaseModel):
    title: str
    decisions: list[str]
    action_items: list[ActionItem]


# 2. Ask Kiro, and say which shape the answer must have
prompt = f"Summarise these meeting notes:\n{NOTES}"
try:
    result = ask_sync(prompt, model=MODEL, result_type=MeetingSummary)
except KiroError as error:
    print(f"No valid summary: {error}", file=sys.stderr)
    sys.exit(1)

# 3. Get the typed object out of the result
summary = result.output
if not isinstance(summary, MeetingSummary):
    print(f"No summary: the turn ended with {result.stop_reason}", file=sys.stderr)
    sys.exit(1)

# 4. Use it like any other Python object
print(summary.title)

print("\nDecisions:")
for decision in summary.decisions:
    print(f"  - {decision}")

print("\nTo do:")
for item in summary.action_items:
    print(f"  {item.owner}: {item.task} ({item.priority}, due {item.due})")

print(f"\nCredits spent: {result.credits:.4f}")
```

Save it as `action_items.py` and run `python action_items.py`. One run cost about 0.04 credits.

### What you'll see

```text
Weekly sync, Monday 2 March 2026 (Ana, Ben, Carla)

Decisions:
  - Ship smaller product images first to speed up the slow mobile checkout page; review the code later
  - Move the support inbox to the new tool (agreed by everyone, no rush)
  - Skip the retrospective this month because the team is too busy

To do:
  Ben: Resize the product images for the sale (high, due 2026-03-06)
  Carla: Write the support inbox migration plan (normal, due 2026-03-13)
  Ana: Book the team lunch (low, due None)

Credits spent: 0.0359
```

## How it works

### The shape you want

```python
class ActionItem(BaseModel):
    owner: str = Field(description="First name of the person who does the task")
    task: str = Field(description="What has to be done, in one short sentence", max_length=80)
    priority: Literal["high", "normal", "low"]
    due: date | None = Field(default=None, description="Due date. Leave it empty if the notes give none.")
```

- **`Literal["high", "normal", "low"]`** means exactly one of these words. The agent can't invent "ASAP-ish".
- **`date | None`** means a date, or nothing. Notes without a date get `None`, not a made-up date.
- **`max_length=80`** limits the text length.
- **`Field(description=...)`** is a note for the agent. The agent never sees your Python class, only the schema, and
  the descriptions are part of it. Write them like short instructions to a new colleague.

Models can be nested as deep as you need: `MeetingSummary` holds a list of `ActionItem`s.

### Asking for a typed result

```python
result = ask_sync(prompt, model=MODEL, result_type=MeetingSummary)
```

The prompt says nothing about the format. The schema does that job. `result_type=` works on `ask()`, `ask_sync()` and
`client.new_session()`. On a session, *every* turn asks for a result.

### Getting the result out

```python
summary = result.output
if not isinstance(summary, MeetingSummary):
    ...
```

`result.output` is "some Pydantic model, or `None`". The `isinstance` check tells your editor it's a
`MeetingSummary`, and it handles `None`, which you get when the turn was cancelled or the model refused. If the turn
ended normally but the agent never submitted a valid result, pyKiro raises a `KiroError` instead. That's the
`except` around the call.

After that the code is boring, and that's the good news. `item.due` is a real `datetime.date`: you can compare and
sort it. No `json.loads`, no regex.

## Gotchas, side effects and risks

- **Valid is not the same as true.** Validation checks the *shape*, not the facts. In two runs, "before 13 March"
  became `2026-03-13` once and `2026-03-12` the other time. Check business rules in your own code.
- **Give the model what it needs.** "By Friday" only works because the notes name the meeting date.
- **Retries and schemas cost credits.** A rejected result is another round trip, and the schema travels with every
  turn. Keep models small and descriptions clear.
- **`submit_result` and `app` are reserved names.** A tool of your own called `submit_result` gives a `ValueError`.

## Where to next

Previous: [05 · Models and Credits](05_Models_and_Credits.md). Next:
[07 · Images and Documents](07_Images_and_Documents.md), where pictures and files go into the prompt. Back to
[all chapters](README.md).
