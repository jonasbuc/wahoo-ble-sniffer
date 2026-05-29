# Contributing

Thank you for contributing to the Wahoo BLE Sniffer / VR Cycling Simulator project.

---

## Project structure overview

```
starters/           One-click install & launch scripts
live_analytics/     FastAPI analytics server, Streamlit dashboard, questionnaire, system check
bridge/             BLE bridge (Wahoo TICKR FIT → WebSocket) and data tools
unity/              Unity C# scripts (telemetry publisher, DBSender, bike controller)
tests/              pytest — bridge, collector, parser, VRSF
docs/               Additional documentation
```

See the root `README.md` for the full annotated file tree and architecture diagrams.

---

## Development setup

```bash
# 1. Clone and install
git clone <repo>
cd "Blu Sniffer"
python -m venv .venv
source .venv/bin/activate        # macOS/Linux
# .venv\Scripts\Activate.ps1    # Windows PowerShell

pip install -e ".[dev]"          # installs runtime + pytest + coverage deps

# 2. Initialise the databases
python live_analytics/scripts/init_db.py

# 3. Run all tests
pytest
```

---

## Running tests

```bash
# All tests
pytest

# Only analytics pipeline tests
pytest live_analytics/tests/

# Only bridge/collector/VRSF tests
pytest tests/

# With coverage report
pytest --cov --cov-report=term-missing
```

Tests use **in-memory SQLite** where possible and do not require any running services.

---

## Code conventions

### Python
- **Python 3.11+** — use `match`/`case`, `asyncio.TaskGroup`, and `tomllib` where appropriate
- **Type hints** on all public functions and class attributes
- **Docstrings** on all public modules, classes, and functions (Google style)
- **Pydantic v2** for all data models — use `model_validator` and `field_validator`, not `@validator`
- **`async`/`await`** throughout the FastAPI layer; never block the event loop with sync I/O
- **SQLite in WAL mode** — always use the connection pool in `sqlite_store.py`, never open raw connections in handlers

### C# (Unity)
- Follow existing Unity naming conventions (`PascalCase` for public members, `_camelCase` for private fields)
- New Unity scripts go in `unity/` with a matching entry in the root `README.md` file tree
- Never modify `BikeMovementController.cs` or VRSF logging scripts without explicit discussion

---

## Adding a new API endpoint

1. Add the route to the appropriate `api_*.py` file in `live_analytics/app/`
2. Add a Pydantic request/response model in `live_analytics/app/models/`
3. Write at least one happy-path test and one error-path test in `live_analytics/tests/`
4. Update the **REST API reference** table in `README.md`

---

## Adding new tests

- Place **analytics pipeline** tests in `live_analytics/tests/`
- Place **bridge/collector/VRSF** tests in `tests/`
- Use `pytest-asyncio` with `@pytest.mark.asyncio` for async tests
- Use `pytest.fixture` with `scope="function"` (default) for isolation
- Name test files `test_<module>.py` and test functions `test_<behaviour>`
- Keep fixtures in `conftest.py` — one per directory

---

## Commit message style

Use a short prefix followed by a concise description (imperative mood):

| Prefix | When to use |
|---|---|
| `feat:` | New feature or capability |
| `fix:` | Bug fix |
| `refactor:` | Code change with no functional difference |
| `test:` | Adding or updating tests |
| `docs:` | Documentation only |
| `chore:` | Build, CI, dependency, or tooling changes |

**Examples:**
```
feat: add GET /api/participants/oldest-unlinked endpoint
fix: prevent SESSION_END being written twice on reconnect
docs: translate live_analytics/README.md to English
test: add integration test for FIFO participant auto-linking
```

---

## What NOT to modify without discussion

- `unity/BikeMovementController.cs` — gameplay controller, touches physics
- `unity/VrsLogging/` — existing VRSF binary session logging
- `bridge/collector_tail.py` — VRSF binary collector; schema changes break existing data
- Scene files and prefabs unrelated to analytics

---

## Ports summary (for reference)

| Service | Port |
|---|---|
| Analytics API (HTTP) | 8080 |
| Analytics WS ingest | 8766 |
| Streamlit dashboard | 8501 |
| Questionnaire API | 8090 |
| System Check GUI | 8095 |
| BLE bridge WebSocket | 8765 |
