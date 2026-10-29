# 07 · Images and Documents

Until now our prompts were plain strings. But you can also hand Kiro a picture or a text file. In this chapter we
send a bar chart and a short report with one deliberate mistake, and let Kiro spot the mismatch, with a typed answer.

## What you'll learn

- How to put images and text files into a prompt with `Image` and `Resource`.
- That a prompt can be a *list* of parts, and that the order matters.
- When to embed a file and when to let the agent read it from its folder.

## The big idea

Pass a list as the prompt, and each item becomes one **content block** (ACP's word for one piece of a message):

| You pass | It becomes | Use it for |
|---|---|---|
| a `str` | a text block | your instructions and questions |
| an `Image` | the image bytes plus a MIME type | pictures, charts, screenshots |
| a `Resource` | an embedded text document | text files the agent should read |

A **MIME type** is a short label for the kind of data, like `image/png` or `text/plain`. The key word is
**embedded**: the content travels *inside* the prompt, so the agent sees exactly what you gave it, nothing more.
Everything a prompt can be (a string, an `Image`, a `Resource`, or a list of them) has one type name in pyKiro:
`PromptContent`.

## The full code

```python
"""Check a bar chart against a written report: send Kiro the picture and the text."""

import base64
import sys
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from pykiro import Image, KiroError, Resource, ask_sync

MODEL = "claude-haiku-5.5"

# A tiny PNG picture of three bars (red is tallest, then green, then blue), pasted in as text
CHART_PNG = base64.b64decode("""
iVBORw0KGgoAAAANSUhEUgAAASwAAADICAIAAADdvUsCAAAC6ElEQVR42u3TAQ0AIAhFQZKQxLAG
o4vGQNm9/QSMiyOptXACCUIJQkkQShBKglCCUBKEEoSSIJQglAShBKEkCCUIJUEoQSgJQglCSRBK
EEqCUIJQEoQShJIglCCUBKEEoaQHEFbmsHkIQQihIIRQghBCQQihBCGEghBCCUIIBSGEEoQQCkII
JQghFIQQShBCKAghlCCEUBBCKEEIoSCEUIIQQkEIoQQhhIIQQglCCAUhhBKEEApCCCUIIRSEEEoQ
QigIIZQghFAQQihBCKEghFCCEEJBCKEEIYSCEEIJQggFIYQShBAKQgglCCEUhBBKEEIoCCGUIIRQ
EEIoQQihIIRQghBCQQihBCGEghBCCUIIBSGEEoQQCkIIBSGEEApCCAUhhBAKQggFIYQQCkIIBSGE
EoQQCkIIJQghFIQQShD+hzD3GjY8IIQQQgghhFAQQgghhBBCKAghhBBCCCEUhBBCCCGEEApCCCGE
EEIIBSGEEEIIIYSCEEIIIYQQQkEIIYQQQgihIIQQQgghhFAQQgghhBBCKAghhBBCCCEUhBBCCCGE
EApCCCGEEEIIBSGEEEIIIYSCEEIIIYQQQkEIIYQQQgihIIQQQgghhFAQQgghhBBCKAghhBBCCCEU
hBBCCCGEEApCCCGEEEIIBSGEEEIIIYSCEEIIIYQQQkEIIYQQQgihIIQQQgghnIswVw0bhBBCCCGE
EEIIIYQQQgghhBBCCCGEEEIIIYQQQgghhBBCCCGEEEIIIYQQQgghhBBCCCGEEEIIIYQQQgghhBBC
CCGEEEIIIYQQQgghhBBCCCGEEEIIIYQQQgghhBBCCCGEEEIIIYQQQgghhBBCCCGEEEIIIYQQQggh
hBBCCCGEEEIIIYQQQgghhBBCCCGEEEIIIYQQQgghhBBCCCGEEEIIIYQQQgghhBBCCCGEEEIIIYQQ
QgghhBBCCCGEEEIIIYQQQgghhBBCCCGEEEIIIYQQQgghhBBC2IbwAoNqIHR/+aQwAAAAAElFTkSu
QmCC
""")

# A report with one deliberate mistake: red is the tallest bar, not blue
REPORT = """\
Quarterly sales report

Blue was our best region this quarter and clearly led the other two.
Red came second, and green had the weakest quarter.
"""


class ChartCheck(BaseModel):
    tallest_bar_colour: Literal["red", "green", "blue"] = Field(description="Colour of the tallest bar in the chart")
    report_matches_chart: bool = Field(description="True only if the report describes the chart correctly")
    why: str = Field(description="One short sentence that explains the answer", max_length=150)


# 1. Save the picture and the report as files, in a throwaway folder
with tempfile.TemporaryDirectory() as folder:
    chart = Path(folder) / "sales.png"
    chart.write_bytes(CHART_PNG)

    report = Path(folder) / "report.txt"
    report.write_text(REPORT, encoding="utf-8")

    # 2. Build the prompt as a list: instructions first, then the material
    prompt = [
        "Look at the chart image, then read the report. Does the report describe the chart correctly?",
        Image.from_file(chart),
        Resource.from_file(report),
    ]

# 3. Ask Kiro for a typed answer
try:
    result = ask_sync(prompt, model=MODEL, result_type=ChartCheck)
except KiroError as error:
    print(f"No answer: {error}", file=sys.stderr)
    sys.exit(1)

# 4. Read the answer
check = result.output
if not isinstance(check, ChartCheck):
    print(f"No answer: the turn ended with {result.stop_reason}", file=sys.stderr)
    sys.exit(1)

print(f"Tallest bar: {check.tallest_bar_colour}")
print(f"Report matches: {check.report_matches_chart}")
print(f"Why: {check.why}")
print(f"Credits spent: {result.credits:.4f}")
```

Save it as `chart_check.py` and run `python chart_check.py`. One run cost about 0.02 credits.

### What you'll see

```text
Tallest bar: red
Report matches: False
Why: The chart shows red tallest, green middle, and blue shortest, but the report says blue was best and green weakest.
Credits spent: 0.0194
```

The program never tells Kiro which bar is the tallest. Kiro only gets the picture and the report.

## How it works

### The picture is just text

`CHART_PNG` is a tiny 300x200 PNG written as **base64**, a way to write bytes as plain text. `b64decode` turns it
back into bytes, and we save it as `sales.png`. That's only there so you can copy the program and run it with no
extra files. With your own pictures, you skip this part.

### A prompt that is a list

```python
prompt = [
    "Look at the chart image, then read the report. Does the report describe the chart correctly?",
    Image.from_file(chart),
    Resource.from_file(report),
]
```

The items go to Kiro in this order, and the model reads them top to bottom. Instructions first, material after, is a
good habit.

- **`Image.from_file(path)`** reads the bytes and guesses the MIME type from the **file name** (`.png` gives
  `image/png`). Already have the bytes in memory? Use `Image(data, "image/jpeg")`.
- **`Resource.from_file(path)`** reads the file as **UTF-8 text** and builds a `Resource` with a `file://` URI, the
  text and a MIME type. For text that doesn't come from a file: `Resource("file:///notes.txt", "some text")`.

Then `result_type=ChartCheck` works exactly like in [chapter 06](06_Structured_Output.md). The temp folder can
disappear before we ask, because `from_file` has already read both files into the prompt.

### Embed it, or let the agent read it?

| | Embed it (this chapter) | Put it in the workspace |
|---|---|---|
| How | `Image.from_file`, `Resource.from_file` | save it in the agent's `cwd`, say "read report.txt" |
| Who decides what is read | you | the agent |
| Size | all of it goes into the prompt | the agent can read parts |

`ask_sync()` runs the agent in its own empty temp folder, so here only what we embed reaches it. For the workspace
route, pass `cwd=folder` and the agent can read *everything* in that folder.

## Gotchas, side effects and risks

- **`Resource` is UTF-8 text only.** A PDF or a Word file will most likely fail to decode. Extract the text first.
- **Images cost room.** The bytes travel inside the prompt, about a third bigger as base64. Resize screenshots.
- **Models misread images.** Our chart is simple. A busy chart or a photo of a table is a different story.
- **The URI shows your folder path to the model.** `from_file` uses the absolute path. Build the `Resource` by hand
  if that path contains names you'd rather not send.

## Where to next

Previous: [06 · Structured Output](06_Structured_Output.md). Next: [08 · App Tools](08_App_Tools.md), where the
agent calls *your* Python functions. Back to [all chapters](README.md).
