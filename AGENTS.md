# pyKiro — Agent Instructions

**pyKiro** (`pykiro`) lets Python programs on a user's own machine use that user's Kiro subscription as their AI
agent. It drives the Kiro CLI V3 engine (`kiro-cli acp --agent-engine=v3`) over the Agent Client Protocol (ACP):
an async API (blocking callers use `asyncio.run`, or `ask_sync` for one question) and app tools (plain typed Python functions served to Kiro by the
library itself). The live tests (`tests/live/`) record the verified facts about Kiro: each fact the library relies
on has a direct assertion, and related facts may share one focused scenario to save credits.

**Stack:** Python 3.11–3.14 · `agent-client-protocol` (JSON-RPC layer only) · `pydantic` v2 · uv · ruff · ty ·
bandit · pytest (+ anyio plugin, hypothesis) · just · pre-commit · commitizen. Documentation is `docs/`: a `README.md`
index and numbered Markdown chapters (`NN_Title.md`); each example chapter has one full program (a few add a second
file). There are no example `.py` files; ruff formats the Python blocks in `docs/*.md` (`just check`).

---

## Response Style

Lead with the answer or decision. Short summary, then 3-5 bullets or a small table if needed. Report the outcome,
not the journey. No praise, filler or recaps. Brevity never overrides correctness: say what was verified and what
was assumed, and flag risk before anything hard to reverse.

---

## Read the Skill First — MANDATORY

| Area | Load |
|------|------|
| Library source (`src/pykiro/`) | `.kiro/skills/library-development/SKILL.md` (+ `references/project-map.md`) |
| Tests (`tests/`) | `.kiro/skills/library-testing/SKILL.md` (+ `references/test-patterns.md`) |

The skills are the standard; follow them, not whatever older code happens to do.

---

## Core Principles

1. **Simple is better than complex.** The dullest code that correctly solves the problem wins. No speculative
   features, options, layers or abstractions "for later". If a feature isn't needed now, don't build it.
2. **Transparency over performance.** Code should plainly show what it does. Optimise only with measured
   evidence.
3. **Don't reinvent the wheel.** Use the stdlib (asyncio), the ACP SDK and Pydantic for what they already do. Write
   our own code only where they demonstrably don't fit (e.g. the ACP SDK's typed client drops Kiro's extensions,
   so we use its JSON-RPC connection with raw dicts) and say why in one line.
4. **Kiro is the source of truth.** Docs can be wrong. Verify behaviour against the real engine with a live test
   before relying on it; the live test is the record of that fact.
5. **Explicit over implicit.** No hidden magic, global state or auto-registration. Pass things as arguments.
6. **Comments are short and informative.** Explain only what isn't obvious from the code (a Kiro quirk, a
   protocol rule, a non-obvious reason). Straightforward code needs no comment. Never write *when* or *how*
   something changed.

---

## Behaviour Rules

- Smallest reasonable change; don't refactor unrelated code to land a feature.
- Read before writing; match the shape of a sibling file that plays the same role.
- Fix broken things you find in the area you touch; never leave dead or commented-out code.
- No secrets in code. Never log credentials: the tool server's secret path travels in `session/new`.
- Live tests spend the user's credits: keep prompts tiny, use the cheap model (`KIRO_LIVE_MODEL`, default
  `claude-haiku-5.5`) at low effort (`KIRO_LIVE_EFFORT`, default `low`), and run only what the change needs.
- Never write into the user's `~/.kiro`, and never read Kiro's credential, session or log files.
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`,
  `refactor:`, `test:`, `chore:`); commitizen builds `CHANGELOG.md` and the version from them.

---

## Verify Before Done

```bash
uv run just check    # ruff format + ruff lint (fix) + ty + bandit
uv run just test     # unit + integration tests, no Kiro, no credits
uv run just test-live -k <name>   # only when Kiro behaviour is involved; spends credits
```
