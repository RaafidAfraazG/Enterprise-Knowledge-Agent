"""Deterministic providers that never use a network or credentials."""

import hashlib
import json
import math
import re

from kagent.llm.base import ChatResult, EmbeddingResult


class FakeEmbeddings:
    """Normalized, stable hashed bag-of-words embeddings."""

    dim = 64

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts using word counts, excluding common stopwords."""
        stopwords = {
            "the",
            "a",
            "an",
            "of",
            "in",
            "to",
            "is",
            "what",
            "how",
            "many",
            "are",
            "and",
            "or",
            "for",
            "on",
            "with",
            "from",
            "their",
            "this",
            "that",
            "it",
            "be",
            "by",
            "as",
            "at",
            "must",
        }
        vectors = []
        for text in texts:
            vector = [0.0] * self.dim
            for word in re.findall(r"[a-z0-9]+", text.lower()):
                if word not in stopwords:
                    digest = hashlib.sha256(word.encode()).digest()
                    vector[int.from_bytes(digest[:4], "big") % self.dim] += (
                        1 if digest[4] % 2 else -1
                    )
            norm = math.sqrt(sum(value * value for value in vector)) or 1
            vectors.append([value / norm for value in vector])
        return vectors

    def embed_with_usage(self, texts: list[str]) -> EmbeddingResult:
        """Report deterministic fake token usage without real inference."""
        return EmbeddingResult(self.embed(texts), sum(len(text) // 4 for text in texts), "fake")


class FakeLLM:
    """Keyword routing and SQL templates for the demo schema."""

    def invoke(self, system: str, user: str) -> str:
        """Respond to the graph's explicit prompt types."""
        if system.startswith("ROUTER"):
            question = user.lower().strip()
            if any(
                term in question
                for term in ("how many", "total", "average", "count", "list employees")
            ):
                return "sql"
            normalized = " ".join(re.findall(r"[a-z]+", question))
            vague = {
                "what s the policy",
                "what is the policy",
                "what policy",
                "can you explain",
                "tell me more",
                "how does it work",
                "help me please",
                "what should i do",
            }
            if len(question.split()) < 3 or normalized in vague:
                return "clarify"
            return "doc"
        if system.startswith("SQL_GENERATION"):
            question = user.lower()
            if "ticket" in question:
                where = " WHERE status = 'closed'" if "closed" in question else ""
                return "SELECT COUNT(*) AS ticket_count FROM tickets" + where
            if "sale" in question:
                aggregate = "AVG" if "average" in question else "SUM"
                return f"SELECT {aggregate}(amount) AS sales_amount FROM sales"
            if "leave" in question:
                return "SELECT COUNT(*) AS leave_count FROM leave_requests"
            if "employee" in question or "salary" in question:
                if "average" in question and "salary" in question:
                    return "SELECT AVG(salary) AS average_salary FROM employees"
                if "list" in question:
                    return "SELECT name, department, role FROM employees"
                return "SELECT COUNT(*) AS employee_count FROM employees"
            raise ValueError("No demo SQL template matches this question")
        if system.startswith("ANSWER_SQL"):
            result = json.loads(user)["result"]
            return (
                f"The query returned {json.dumps(result['rows'])} ({', '.join(result['columns'])})."
            )
        if system.startswith("ANSWER_DOC"):
            context = json.loads(user)["context"]
            return "According to the company documents: " + "\n\n".join(context)
        if system.startswith("CLARIFY"):
            return "Which policy or business topic would you like to know about?"
        raise ValueError("Unknown prompt type")

    def invoke_with_usage(
        self, system: str, user: str, *, max_tokens: int | None = None
    ) -> ChatResult:
        """Preserve fake answers and count reproducible approximate tokens."""
        text = self.invoke(system, user)
        return ChatResult(text, (len(system) + len(user)) // 4, len(text) // 4, "fake")
