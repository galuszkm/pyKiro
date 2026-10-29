# Contributing to pyKiro

Bug reports, fixes, features and documentation are all welcome. Please open an issue before significant work, so
we can agree on the approach first.

## Development environment

You need Python 3.11–3.14, [uv](https://docs.astral.sh/uv/) and git. For the live tests and for running the code in
`docs/` you also need [kiro-cli](https://kiro.dev/docs/getting-started/installation) with the V3 engine, logged in
(`kiro-cli login`).

```bash
uv run just install    # every dependency group, plus the git hooks
```

The hooks run on `git commit`: file hygiene, ruff (lint and format), and commitizen, which checks the commit
message. Fresh clones need `uv run just install` once, because `.git/hooks/`
isn't tracked.

## Checks

```bash
uv run just check               # format, lint (with fixes), type-check, security scan
uv run just test                # unit and integration tests with coverage: no Kiro, no credits
uv run just test-live -k <name> # against your real kiro-cli; spends a few credits
```

Live tests pin the facts about Kiro that the library relies on: each fact has a direct assertion, and related facts
may share one focused scenario to save credits. They use a cheap model (`KIRO_LIVE_MODEL`, default
`claude-haiku-5.5`); run only the ones your change needs.

The coding standards (principles, conventions, what not to do) are in [AGENTS.md](AGENTS.md) and the skills under
`.kiro/skills/`.

## Pull requests

1. Branch from `main` and keep the change focused on one concern.
2. Run `uv run just check` and `uv run just test`.
3. Commit with [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`,
   `refactor:`, `test:`, `chore:`); `uv run just commit` walks you through it. The messages build the changelog
   and the next version.
4. Open the PR with what changed and why, and keep an eye on CI.

## Security issues

Please don't open a public issue; see [SECURITY.md](SECURITY.md).

## License

Contributions are licensed under the [Apache License 2.0](LICENSE).
