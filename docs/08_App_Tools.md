# 08 · App Tools

Kiro's agent comes with its own tools: it reads files, runs commands, fetches web pages. **App tools** are tools *you*
add: plain Python functions that run inside your program, on your data. In this chapter we build a to-do list that
lives in a Python list, and the agent manages it through three small functions.

## What you'll learn

- How a typed Python function becomes a tool the agent can call.
- How to set rules for the arguments, and how to say "no" with `ToolError`.
- How to give tools to every session, or to one session only.

## The big idea

You pass your functions as `tools=[...]`, and the agent sees each one as `@app/<function name>`. The agent can't read
your code. It only sees what pyKiro tells it about each function:

| In your function | What the agent gets |
|---|---|
| The name | The tool name: `add_todo` becomes `@app/add_todo`. |
| The docstring | The description. Say what the tool does and when to use it. |
| Type hints, defaults and `Field` rules | A **JSON Schema** of the parameters, so the model knows how to fill them in. |
| The return value | The result: a string goes back as text, anything else as JSON. |

That's the whole deal: typed function in, tool out. No decorator, no registration, no server code on your side.

## The full code

```python
"""A to-do list that lives in your program. Kiro manages it through three app tools."""

import asyncio
import tempfile
import threading
from typing import Annotated

from pydantic import Field

from pykiro import AnsiRenderer, KiroClient, ToolError

MODEL = "claude-haiku-5.5"

# Our data: a plain list of to-dos. Kiro never sees it, only what our functions return.
TODOS: list[dict] = []
LOCK = threading.Lock()  # plain tools can run at the same time, on different threads


# The three tools. The docstring is what tells Kiro when to use each one.
def list_todos() -> list[dict]:
    """List every to-do with its id, title and whether it is done."""
    with LOCK:
        return list(TODOS)


def add_todo(
    title: Annotated[str, Field(min_length=1, max_length=40, description="A short title, at most 40 characters.")],
) -> dict:
    """Add a new to-do and return it, including its new id."""
    with LOCK:
        todo = {"id": len(TODOS) + 1, "title": title, "done": False}
        TODOS.append(todo)
        return todo


def complete_todo(todo_id: int) -> str:
    """Mark the to-do with this id as done."""
    with LOCK:
        for todo in TODOS:
            if todo["id"] == todo_id:
                todo["done"] = True
                return f"Done: {todo['title']}"
    raise ToolError(f"There is no to-do with id {todo_id}. Call list_todos to see the ids.")


TOOLS = [list_todos, add_todo, complete_todo]


async def main() -> None:
    renderer = AnsiRenderer()

    # An empty folder for the agent to work in
    with tempfile.TemporaryDirectory() as workspace:
        # 1. Start Kiro, and hand it our tools
        async with KiroClient(cwd=workspace, tools=TOOLS) as client:
            # 2. Open a session
            session = await client.new_session(model=MODEL)

            # 3. Let Kiro manage the list
            await session.send(
                "Add three to-dos: buy milk, call the dentist, water the plants. Then show me the list.",
                on_event=renderer,
            )
            await session.send(
                "I called the dentist, mark that one as done. "
                "Then try to mark to-do 9 as done, and tell me what happened.",
                on_event=renderer,
            )

    # 4. Look at our own data
    print("\nWhat the program sees in its own data:")
    for todo in TODOS:
        print(f"  {todo['id']}. {todo['title']} (done: {todo['done']})")


if __name__ == "__main__":
    asyncio.run(main())
```

Save it as `todo.py` and run `python todo.py`. One run costs about 0.04 credits.

### What you'll see

```text
⚙ Tool Load  {"tool_ids": ["app::add_todo", "app::list_todos"]}
✓ Tool Load
⚙ @app/add_todo  {"tool_id": "app::add_todo", "arguments": {"title": "Buy milk"}}
✓ @app/add_todo
⚙ @app/add_todo  {"tool_id": "app::add_todo", "arguments": {"title": "Call the dentist"}}
✓ @app/add_todo
⚙ @app/add_todo  {"tool_id": "app::add_todo", "arguments": {"title": "Water the plants"}}
✓ @app/add_todo
⚙ @app/list_todos  {"tool_id": "app::list_todos", "arguments": {}}
✓ @app/list_todos
kiro › Added three to-dos. Current list:
1. [ ] Buy milk
2. [ ] Call the dentist
3. [ ] Water the plants
── end_turn · 0.0224 credits · 10.6 s ──
⚙ Tool Load  {"tool_ids": ["app::complete_todo"]}
✓ Tool Load
⚙ @app/complete_todo  {"tool_id": "app::complete_todo", "arguments": {"todo_id": 2}}
⚙ @app/complete_todo  {"tool_id": "app::complete_todo", "arguments": {"todo_id": 9}}
✓ @app/complete_todo
✗ @app/complete_todo  MCP tool error: There is no to-do with id 9. Call list_todos to see the ids.
kiro › The dentist to-do (id 2) is now marked done.
To-do 9 does not exist. The system returned "There is no to-do with id 9." The list only has ids 1 to 3, so nothing was changed.
── end_turn · 0.0149 credits · 10.3 s ──

What the program sees in its own data:
  1. Buy milk (done: False)
  2. Call the dentist (done: True)
  3. Water the plants (done: False)
```

This is a real run, trimmed: we cut the "thinking" lines and a first try where the agent called `complete_todo`
before loading it. Yours will differ, because the agent decides the order of its calls.

## How it works

### What the agent sees

```python
def add_todo(
    title: Annotated[str, Field(min_length=1, max_length=40, description="A short title, at most 40 characters.")],
) -> dict:
    """Add a new to-do and return it, including its new id."""
```

The **docstring** isn't just a comment here. It's the tool's description, and it's the only thing that tells the
agent *when* to use the tool. Write `"""Does stuff."""` and expect the agent to guess.

The type hints work the same way. `todo_id: int` tells the agent to send a whole number. `Annotated[str, Field(...)]`
is plain Python typing for "a string, with these extra rules": here at least 1 and at most 40 characters. Other
handy rules are `ge=`/`le=` (number limits), `pattern=` (a regular expression) and `Literal["a", "b"]` (only these
values). Anything [Pydantic](https://docs.pydantic.dev/) can describe works as a parameter type.

### Three ways a call can fail

| What happens | What the agent sees | Your function |
|---|---|---|
| **Invalid arguments** (the call breaks the signature's rules) | `Invalid arguments: title: ...` and the reason | never runs |
| Your function raises **`ToolError`** | your message, word for word | ran and said no |
| Your function raises **anything else** | only "The tool X failed." | crashed, traceback in the `pykiro.tools` log |

`complete_todo` uses the middle one. Some rules don't fit in a signature, like "this id must exist", so you check them
in the body and raise a `ToolError`. Write the message for the agent: say what's wrong *and* what would work. In the
sample, the agent read it and told us that to-do 9 doesn't exist.

Any other exception is treated as a crash. The agent gets no details, because exception texts often carry internal
paths, hostnames or SQL. The full traceback stays on your side, in the log.

### Your data, and a lock

```python
TODOS: list[dict] = []
LOCK = threading.Lock()  # plain tools can run at the same time, on different threads
```

Kiro never sees `TODOS`. It only sees what your functions *return*. And the lock? A plain `def` tool runs on a
**worker thread**, and the agent may call several tools at once. Two threads changing one list at the same time can
trip over each other (a **race condition**). `with LOCK:` makes them take turns.

An `async def` tool runs on the event loop instead. That's great for `await`ing a network call, but it must never
block: one `time.sleep(5)` there freezes everything.

### Handing the tools over

```python
async with KiroClient(cwd=workspace, tools=TOOLS) as client:
```

pyKiro checks every function right here. A function that can't be a tool (a lambda, `*args`, a parameter type with no
JSON Schema) raises a `TypeError` now, not in the middle of a turn.

Behind the curtain, pyKiro runs a tiny **MCP server** inside your program, on `127.0.0.1` only and under a secret
random URL. When the agent calls `@app/add_todo`, Kiro sends the call there, pyKiro checks the arguments, runs your
function and sends back the result. pyKiro approves its own tools by itself, so **your permission policy is never
asked about them** ([chapter 10](10_Permissions.md)).

In the transcript, the `Tool Load` lines are Kiro loading a tool before its first use (observed on kiro-cli 2.34.0).
And the arguments arrive wrapped, like `{"tool_id": "app::add_todo", "arguments": {...}}`. Your function only gets
what's inside `arguments`.

### Tools for one session only

Tools on `KiroClient(tools=...)` reach every session on that client. When a tool belongs to one conversation only,
like "the orders of the customer in *this* chat", pass it to `new_session` instead. A small factory function builds a
fresh set per customer. This is a variation, not part of the program above:

```python
def make_customer_tools(customer: str) -> list:
    def list_my_orders() -> list[str]:
        """List the orders of the customer you are helping."""
        return ORDERS[customer]

    return [list_my_orders]


ada = await client.new_session(model=MODEL, tools=make_customer_tools("Ada"))
```

`list_my_orders` remembers `customer` (a **closure**) and has no customer parameter, so the agent in Ada's session
can't ask for anybody else's orders, whatever the prompt says. That's a wall, not a request.

### The program has the last word

After the session closes, we print the list from our own data. When it matters, trust your data, not the model's
summary of it.

## Gotchas, side effects and risks

- **App tools run without asking.** Your policy never sees them. Expose only functions that are fine to run without
  a human saying yes.
- **The agent decides when, and how often.** It may call a tool twice, or not at all. Two `add_todo("Buy milk")` calls
  give two to-dos.
- **No time limit.** pyKiro doesn't time your function. A tool that hangs makes the turn hang, so put a timeout
  inside it.
- **Results travel.** Whatever a tool returns, and every `ToolError` message, goes to the model and to Kiro's service.
  No secrets in there.

## Where to next

Previous: [07 · Images and Documents](07_Images_and_Documents.md). Next: [09 · Custom Agents](09_Custom_Agents.md),
where we decide which of Kiro's own tools the agent gets. Back to [all chapters](README.md).
