"""State carried through the three-branch graph."""

from typing import Any, Literal, TypedDict

from kagent.retrieval.search import SearchHit

Route = Literal["doc", "sql", "clarify"]


class AgentState(TypedDict, total=False):
    """Question, branch outputs, and safe user-facing errors."""

    cache_hit: bool
    question: str
    route: Route
    retrieved: list[SearchHit]
    sql: str | None
    sql_result: dict[str, Any]
    answer: str
    sources: list[str]
    clarification_needed: bool
    error: str | None
