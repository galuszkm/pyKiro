# Releasing pyKiro

pyKiro isn't published to PyPI. A release is a version tag on GitHub, and users install it straight from the
repository (`pip install git+https://github.com/galuszkm/pyKiro@v0.1.0` pins one release).

Versions follow [Semantic Versioning](https://semver.org/) and come from the commit messages
([Conventional Commits](https://www.conventionalcommits.org/)) through [commitizen](https://commitizen-tools.github.io/commitizen/).

```bash
uv run just release-dry          # preview the next version and its changelog
uv run just release              # checks, tests, then bump the version, update CHANGELOG.md and tag
git push origin main --tags      # publish the commit and its tag on GitHub
```

| Commit | Bump while the version is 0.x |
|---|---|
| `fix: ...` | patch |
| `feat: ...` | minor |
| `feat!: ...` or a `BREAKING CHANGE:` footer | minor (major from 1.0) |

A release candidate: `uv run cz bump --prerelease rc`, then push the tag.

Before a release, run the live tests once against the current kiro-cli (`uv run just test-live`): Kiro ships
often, and they are the record of what the library relies on.
