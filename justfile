# https://just.systems/man/en/

SOURCES := "src/pykiro"

# List available tasks
default:
    @just --list

# Install every dependency group and the git hooks
[group('install')]
install:
    uv sync --all-groups
    uv run pre-commit install --install-hooks
    uv run pre-commit install --hook-type commit-msg

# Format, lint (with fixes), type-check and scan for security issues (changes files)
[group('check')]
check:
    uv run ruff format .
    uv run ruff check --fix .
    uv run ty check
    uv run bandit -c pyproject.toml -r {{SOURCES}} -q

# The same checks without changing files (CI)
[group('check')]
lint:
    uv run ruff format --check .
    uv run ruff check .
    uv run ty check
    uv run bandit -c pyproject.toml -r {{SOURCES}} -q

# Run the git hooks on every file
[group('check')]
hooks:
    uv run pre-commit run --all-files

# Unit and integration tests with coverage: no Kiro, no credits. Pass --no-cov for a subset
[group('test')]
[positional-arguments]  # "$@" keeps a quoted argument such as -k "a or b" whole
test *args:
    uv run pytest --cov "$@"

# Live tests against your real kiro-cli login (spends a few credits; KIRO_LIVE_MODEL picks the model)
[group('test')]
[positional-arguments]
test-live *args:
    KIRO_LIVE_TESTS=1 uv run pytest tests/live "$@"

# Commit with a conventional-commit message, interactively
[group('release')]
commit:
    uv run cz commit

# Preview the next version and its changelog
[group('release')]
release-dry:
    uv run cz bump --dry-run

# Bump the version, update CHANGELOG.md and tag it; then: git push origin main --tags
[group('release')]
release: lint test
    uv run cz bump

# Build the wheel and sdist into dist/
[group('release')]
build:
    rm -rf dist
    uv build --out-dir dist

# Remove caches and build output
[group('clean')]
clean:
    find . \( -name .venv -o -name .git \) -prune -o -name __pycache__ -type d -prune -exec rm -rf {} +
    rm -rf .pytest_cache .ruff_cache .hypothesis .coverage htmlcov dist build
