# pyKiro documentation

Welcome! This is the guide to **pyKiro**, a small Python library that lets your own programs use your Kiro
subscription as an AI agent. There are 15 short chapters, each about one topic and built around one real program you
can copy and run. Start with [Introduction](01_Introduction.md), or jump to the chapter you need.

Every chapter has the same shape: what you'll learn, the full code with a real sample of its output, how it works,
and the gotchas.

## Before you start

- **What you need:** Python 3.11–3.14, [Kiro CLI](https://kiro.dev/docs/getting-started/installation) with the
  V3 engine, and a Kiro login. [Chapter 02](02_Quick_Start.md) walks you through the setup.
- **Running a chapter's code:** install pyKiro with `pip install git+https://github.com/galuszkm/pyKiro`, save the
  program from "The full code" under the name the chapter gives, then run it with `python name.py` (or
  `uv run python name.py` in a uv project).
- **Credits are real.** Every turn spends credits from your own Kiro plan. The programs use the small
  `claude-haiku-5.5` model, and each chapter says what its run cost, usually a few hundredths of a credit.
- **Answers vary.** The model words things differently on every run, so your output won't match ours exactly. pyKiro
  makes the *setup* repeatable, not the model's words.
- **Models depend on your account.** If `claude-haiku-5.5` isn't on your list, pick another one
  ([chapter 05](05_Models_and_Credits.md)).
- **Where it was tested:** Linux, Python 3.13, kiro-cli 2.37.0. Kiro can change between versions, so when a chapter
  describes something Kiro does that pyKiro can't promise, it says which kiro-cli version it was observed on.
- **Clean by default:** unless a chapter says otherwise, the agent works in an empty temporary folder with a clean,
  isolated Kiro configuration, so it doesn't touch your projects or your own `~/.kiro`.

## The chapters

| # | Chapter | You'll build |
|---|---|---|
| 01 | [Introduction](01_Introduction.md) | The big picture: what Kiro, ACP and pyKiro are |
| 02 | [Quick Start](02_Quick_Start.md) | `ask.py`, your first question, and what to do when it fails |
| 03 | [Conversations](03_Conversations.md) | A three-turn chat on one Kiro process |
| 04 | [Streaming Events](04_Streaming_Events.md) | Your own live console for a turn, with a deadline |
| 05 | [Models and Credits](05_Models_and_Credits.md) | A free price list and one priced question |
| 06 | [Structured Output](06_Structured_Output.md) | Meeting notes turned into validated action items |
| 07 | [Images and Documents](07_Images_and_Documents.md) | A chart-versus-report checker |
| 08 | [App Tools](08_App_Tools.md) | A to-do list the agent manages through your functions |
| 09 | [Custom Agents](09_Custom_Agents.md) | A read-only code guide |
| 10 | [Permissions](10_Permissions.md) | A docs writer that may write only into `out/` |
| 11 | [Project Config and Profiles](11_Project_Config_and_Profiles.md) | Reuse a project's `.kiro` steering and skill |
| 12 | [MCP Servers](12_MCP_Servers.md) | Plug in `mcp-server-time` |
| 13 | [Saving and Resuming Sessions](13_Saving_and_Resuming_Sessions.md) | A journal that remembers across runs |
| 14 | [Batch Jobs](14_Batch_Jobs.md) | Feedback triage over a CSV, in parallel |
| 15 | [CI/CD](15_CI_CD.md) | An AI review step in GitHub Actions |

## API map

Every public name in `pykiro.__all__`, and the chapter that explains it:

| Name | What it is | Chapter |
|---|---|---|
| `ask` | One question, one answer, for async code | [02](02_Quick_Start.md) |
| `ask_sync` | The same, for plain scripts without an event loop | [01](01_Introduction.md), [02](02_Quick_Start.md) |
| `KiroClient` | One running Kiro process | [03](03_Conversations.md) |
| `Session` | One conversation | [03](03_Conversations.md) |
| `SessionInfo` | A stored session in the list | [13](13_Saving_and_Resuming_Sessions.md) |
| `ConfigOption` | A setting Kiro advertises (model, effort, mode) | [05](05_Models_and_Credits.md) |
| `AnsiRenderer` | Prints a turn as a readable terminal transcript | [03](03_Conversations.md) |
| `Event` | Base type of everything that happens in a turn | [04](04_Streaming_Events.md) |
| `TextDelta` | A chunk of the reply | [04](04_Streaming_Events.md) |
| `ToolCall` | A tool call starting or changing | [04](04_Streaming_Events.md) |
| `Notice` | Everything else Kiro reports | [04](04_Streaming_Events.md) |
| `TurnResult` | How a turn ended (text, stop reason, credits, output) | [02](02_Quick_Start.md), [06](06_Structured_Output.md) |
| `Image` | An image in a prompt | [07](07_Images_and_Documents.md) |
| `Resource` | A text file embedded in a prompt | [07](07_Images_and_Documents.md) |
| `PromptContent` | What a prompt can be: text, images, resources, or a list | [07](07_Images_and_Documents.md) |
| `ToolError` | What a tool raises to tell the agent why it failed | [08](08_App_Tools.md) |
| `PermissionRequest` | One ask from the agent: "may I do this?" | [10](10_Permissions.md) |
| `PermissionOption` | One answer option offered with a request | [10](10_Permissions.md) |
| `PermissionPolicy` | Your function that answers permission requests | [10](10_Permissions.md) |
| `Profile` | Either `Isolated(...)` or `Inherit()` | [11](11_Project_Config_and_Profiles.md) |
| `Isolated` | A clean, library-managed Kiro configuration | [11](11_Project_Config_and_Profiles.md), [13](13_Saving_and_Resuming_Sessions.md) |
| `Inherit` | Your own Kiro configuration | [11](11_Project_Config_and_Profiles.md) |
| `KiroError` | Base class of Kiro-side errors | [02](02_Quick_Start.md) |
| `KiroProcessError` | kiro-cli is missing, not logged in, or exited | [02](02_Quick_Start.md) |
| `KiroProtocolError` | Kiro answered a request with an error | [02](02_Quick_Start.md) |
| `__version__` | The installed pyKiro version | [02](02_Quick_Start.md) |

## More

- The project's [README](../README.md): the short version, install and development notes.
- [CONTRIBUTING.md](../CONTRIBUTING.md): how to work on pyKiro itself.
- [SECURITY.md](../SECURITY.md): how to report a security problem.
