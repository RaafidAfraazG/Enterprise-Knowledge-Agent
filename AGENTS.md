# AGENTS.md

## Project
Enterprise Knowledge Agent: a LangGraph multi-agent RAG assistant that routes
questions to (1) document retrieval, (2) SQL lookup, or (3) clarification.
Stack: Python 3.11, LangGraph, LangChain, FastAPI, Qdrant, SQLite, pytest.

## Environment rules (IMPORTANT)
- You run in a cloud sandbox. Assume NO Docker, NO internet during tasks, and NO real API keys.
- NEVER call real LLM or embedding APIs in tests. Use the fakes in `src/kagent/llm/fakes.py`.
- Tests use Qdrant in-memory (`QdrantClient(":memory:")`) and a temporary SQLite file.
- Never write secrets into code. Read config from environment variables via `src/kagent/config.py`.
- Do not try to build or run Docker.
- Eval, Docker and GitHub Actions files are written here but only executed by GitHub. Validate YAML with a parser; never try to build Docker or run az commands

## Code rules
- Python 3.11, type hints everywhere, small functions, docstrings on public functions.
- Source lives in `src/kagent/`, tests in `tests/`.
- Run `pytest -q` before finishing any task. All tests must pass.
- Run `ruff check .` and fix issues.
- The SQL branch must be strictly read-only: a single SELECT statement only.
- If a requirement is ambiguous, pick the simplest option and note it in the README under "Assumptions".

## Definition of done
- `pytest -q` passes offline.
- README updated with what was built and how to run it.
