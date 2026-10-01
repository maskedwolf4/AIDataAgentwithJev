"""
Jev System One decision engine.

Provides two main functions:
  - triage_request(): Routes user queries (sql | etl | unsupported), picks Groq
    model tier, checks safety and prompt curation need — all in ONE Jev API call.
  - verify_sql(): Validates generated SQL for safety, injection, hallucination,
    and decides whether to escalate to a bigger Groq model — ONE Jev API call.
"""

import os
import re
import logging
from dataclasses import dataclass, field
from typing import Any

from dotenv import load_dotenv
from typesafe_sdk import (
    Choice, Noul, NoulCriteria, Score, TypeSafeClient,
    TypeSafeAPIError, TypeSafeError,
)

from utils.llm_pick import TIER_ORDER, escalate_tier

load_dotenv()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Jev client  (reads TYPESAFE_API_KEY from environment)
# ---------------------------------------------------------------------------
_client: TypeSafeClient | None = None


def _get_client() -> TypeSafeClient:
    global _client
    if _client is None:
        _client = TypeSafeClient()  # uses TYPESAFE_API_KEY env var
    return _client


# ---------------------------------------------------------------------------
# Complexity Score -> Groq tier mapping
# ---------------------------------------------------------------------------
def _score_to_tier(score: float) -> str:
    """Map a 0-3 complexity score to a Groq model tier."""
    if score < 0.75:
        return "low"
    if score < 1.65:
        return "medium"
    if score < 2.35:
        return "high"
    return "top"


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------
@dataclass
class TriageDecision:
    route: str = "unsupported"          # sql | etl | unsupported
    route_confidence: float = 0.0
    etl_operation: str = ""              # extract_load | transform_load | full_pipeline
    etl_operation_confidence: float = 0.0
    complexity_score: float = 0.0
    complexity_confidence: float = 0.0
    model_tier: str = "medium"
    needs_curation: bool = True
    curation_confidence: float = 0.0
    is_unsafe: bool = False
    unsafe_confidence: float = 0.0
    blocked_reason: str = ""
    telemetry: dict[str, Any] = field(default_factory=dict)


@dataclass
class SQLVerificationDecision:
    is_safe: str = "Yes"                # "Yes" or "No"
    should_escalate_model: bool = False
    escalated_tier: str = ""
    block_reason: str = ""
    telemetry: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Triage (1 Jev API call with speculative fan-out)
# ---------------------------------------------------------------------------
def triage_request(user_message: str) -> TriageDecision:
    """
    Route a user message using Jev System One.

    One API call evaluates 5 questions in parallel:
      1. agent_route   (Choice: sql | etl | unsupported)
      2. etl_operation  (Choice: extract_load | transform_load | full_pipeline)
      3. task_complexity (Score 0-3 rubric)
      4. needs_curation  (Noul)
      5. unsafe_intent   (Noul)
    """
    client = _get_client()
    decision = TriageDecision()

    state = (
        f"User request: {user_message}\n\n"
        "Available capabilities:\n"
        "- SQL: Query a SQLite rideshare database (users, vehicles, rides, payments, ratings).\n"
        "- ETL: Extract data from REST APIs and load to disk, or transform CSV/JSON/Parquet files with Pandas.\n"
    )

    questions = {
        "agent_route": Choice(
            instructions="Classify whether this request needs SQL database querying, ETL data pipeline operations, or is unsupported.",
            criteria={
                "sql": "The user wants to query, analyze, or retrieve data from the rideshare SQLite database.",
                "etl": "The user wants to extract data from an API, load files, transform CSV/JSON/Parquet data, or run data pipelines.",
                "unsupported": "The request is unrelated to data engineering, is ambiguous, or cannot be handled by SQL or ETL agents.",
            },
        ),
        "etl_operation": Choice(
            instructions="If this is an ETL task, classify the specific operation needed.",
            criteria={
                "extract_load": "Extract data from a REST API endpoint and save to a file.",
                "transform_load": "Read an existing data file, apply Pandas transformations, and save the result.",
                "full_pipeline": "Both extract from an API AND transform the extracted data.",
            },
        ),
        "task_complexity": Score(
            instructions=(
                "Rate the complexity of this data task on a 0-3 scale. "
                "Consider number of tables, joins, aggregations, subqueries, "
                "window functions, or multi-step ETL orchestration."
            ),
            criteria=[
                "Simple: single table lookup, basic filter, or straightforward API extraction.",
                "Moderate: 1-2 joins, basic aggregation (COUNT, SUM), simple Pandas filters.",
                "Complex: multi-table joins, GROUP BY with HAVING, subqueries, multi-step ETL.",
                "Very complex: window functions, CTEs, correlated subqueries, complex Pandas code synthesis.",
            ],
        ),
        "needs_curation": Noul(
            instructions=(
                "Does the user's question need rewriting/curation by an LLM before processing? "
                "Return true if the prompt is vague, has typos, or is poorly structured. "
                "Return false if the question is already clear and well-formed."
            ),
            criteria=NoulCriteria(
                true="The question is vague, ambiguous, has grammatical errors, or needs clarification before processing.",
                false="The question is clear, specific, well-structured, and ready for direct processing.",
            ),
        ),
        "unsafe_intent": Noul(
            instructions=(
                "Does this request attempt to modify/delete database data, inject SQL, "
                "or perform any destructive operation? Return true if unsafe."
            ),
            criteria=NoulCriteria(
                true="The request attempts DROP, DELETE, UPDATE, INSERT, ALTER, TRUNCATE, or SQL injection.",
                false="The request is a safe read-only data query or standard ETL operation.",
            ),
        ),
    }

    try:
        response = client.system_one(state=state, questions=questions)

        # --- Extract answers ---
        route_ans = response.answers["agent_route"]
        etl_ans = response.answers["etl_operation"]
        complexity_ans = response.answers["task_complexity"]
        curation_ans = response.answers["needs_curation"]
        unsafe_ans = response.answers["unsafe_intent"]

        decision.route = route_ans.choice
        decision.route_confidence = route_ans.confidence
        decision.etl_operation = etl_ans.choice
        decision.etl_operation_confidence = etl_ans.confidence
        decision.complexity_score = complexity_ans.score
        decision.complexity_confidence = complexity_ans.confidence
        decision.model_tier = _score_to_tier(complexity_ans.score)
        decision.needs_curation = curation_ans.noul >= 0.5
        decision.curation_confidence = curation_ans.noul  # NoulAnswer has no .confidence; .noul IS the probability
        decision.is_unsafe = unsafe_ans.noul >= 0.75
        decision.unsafe_confidence = unsafe_ans.noul

        # Confidence gating
        if decision.is_unsafe:
            decision.route = "unsupported"
            decision.blocked_reason = "Jev detected unsafe/destructive intent."
        elif decision.route_confidence < 0.55:
            decision.route = "unsupported"
            decision.blocked_reason = (
                f"Route confidence too low ({decision.route_confidence:.2f} < 0.55). "
                "Please clarify your request."
            )

        # Build telemetry
        decision.telemetry = {
            "jev_call": "triage_request",
            "agent_route": {
                "choice": route_ans.choice,
                "confidence": route_ans.confidence,
                "probabilities": getattr(route_ans, "probabilities", {}),
            },
            "etl_operation": {
                "choice": etl_ans.choice,
                "confidence": etl_ans.confidence,
                "probabilities": getattr(etl_ans, "probabilities", {}),
            },
            "task_complexity": {
                "score": complexity_ans.score,
                "confidence": complexity_ans.confidence,
            },
            "needs_curation": {
                "noul": curation_ans.noul,
            },
            "unsafe_intent": {
                "noul": unsafe_ans.noul,
            },
            "selected_model_tier": decision.model_tier,
            "blocked_reason": decision.blocked_reason,
        }

    except TypeSafeAPIError as e:
        logger.error("Jev API error during triage: status=%s id=%s", e.status, e.request_id)
        decision.route = "sql"  # safe fallback
        decision.model_tier = "medium"
        decision.needs_curation = True
        decision.telemetry = {"error": str(e), "fallback": True}
    except TypeSafeError as e:
        logger.error("Jev SDK error during triage: %s", e)
        decision.route = "sql"
        decision.model_tier = "medium"
        decision.needs_curation = True
        decision.telemetry = {"error": str(e), "fallback": True}

    return decision


# ---------------------------------------------------------------------------
# SQL Verification (1 Jev API call)
# ---------------------------------------------------------------------------
_MUTATING_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|REPLACE|GRANT|REVOKE)\b",
    re.IGNORECASE,
)


def verify_sql(
    user_question: str,
    sql_query: str,
    current_tier: str = "medium",
    already_escalated: bool = False,
) -> SQLVerificationDecision:
    """
    Validate generated SQL with a fast regex pre-check + Jev System One battery.

    Returns whether the SQL is safe to execute and whether to escalate the
    Groq model tier (cascade pattern).
    """
    decision = SQLVerificationDecision()

    # --- Fast regex pre-check ---
    if _MUTATING_RE.search(sql_query):
        decision.is_safe = "No"
        decision.block_reason = (
            f"SQL contains mutating keyword: {_MUTATING_RE.search(sql_query).group()}"
        )
        decision.telemetry = {"jev_call": "verify_sql", "regex_blocked": True,
                              "block_reason": decision.block_reason}
        return decision

    # --- Jev verification battery ---
    client = _get_client()

    state = (
        f"User question: {user_question}\n\n"
        f"Generated SQL query:\n{sql_query}\n\n"
        "Database: SQLite rideshare database with tables: "
        "users, vehicles, rides, payments, ratings."
    )

    questions = {
        "mutates_data": Noul(
            instructions="Does this SQL query modify, insert, delete, or alter any data or schema?",
            criteria=NoulCriteria(
                true="The query modifies data (INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE/CREATE).",
                false="The query is read-only (SELECT, EXPLAIN, PRAGMA).",
            ),
        ),
        "injection_risk": Noul(
            instructions="Does this SQL query contain SQL injection patterns, union-based attacks, or suspicious concatenation?",
            criteria=NoulCriteria(
                true="The query has injection patterns like UNION SELECT, OR 1=1, comment injection, or stacked queries.",
                false="The query is a standard well-formed SQL statement.",
            ),
        ),
        "off_target": Noul(
            instructions=(
                "Does the generated SQL query correctly address the user's question? "
                "Return true if the SQL seems wrong, hallucinates columns/tables, "
                "or misinterprets the question."
            ),
            criteria=NoulCriteria(
                true="The SQL query does NOT correctly answer the user question, uses wrong tables/columns, or has logical errors.",
                false="The SQL query correctly and accurately addresses the user's question.",
            ),
        ),
        "risk_severity": Score(
            instructions="Rate the overall risk severity of executing this SQL query.",
            criteria=[
                "No risk: safe read-only query that correctly addresses the question.",
                "Low risk: query is safe but might return suboptimal results.",
                "Medium risk: query has potential issues (wrong logic, missing filters).",
                "High risk: query is dangerous, destructive, or completely wrong.",
            ],
        ),
    }

    try:
        response = client.system_one(state=state, questions=questions)

        mutates = response.answers["mutates_data"]
        injection = response.answers["injection_risk"]
        off_target = response.answers["off_target"]
        severity = response.answers["risk_severity"]

        # Block unsafe
        if mutates.noul >= 0.5 or severity.score >= 1.5:
            decision.is_safe = "No"
            reasons = []
            if mutates.noul >= 0.5:
                reasons.append(f"mutates_data={mutates.noul:.2f}")
            if injection.noul >= 0.5:
                reasons.append(f"injection_risk={injection.noul:.2f}")
            if severity.score >= 1.5:
                reasons.append(f"risk_severity={severity.score:.2f}")
            decision.block_reason = "Jev flagged: " + ", ".join(reasons)
        # Cascade escalation if off-target
        elif off_target.noul >= 0.70 and not already_escalated:
            next_tier = escalate_tier(current_tier)
            if next_tier != current_tier:
                decision.should_escalate_model = True
                decision.escalated_tier = next_tier
        # Otherwise safe

        decision.telemetry = {
            "jev_call": "verify_sql",
            "mutates_data": {"noul": mutates.noul},
            "injection_risk": {"noul": injection.noul},
            "off_target": {"noul": off_target.noul},
            "risk_severity": {"score": severity.score, "confidence": severity.confidence},
            "decision": decision.is_safe,
            "escalated": decision.should_escalate_model,
            "escalated_tier": decision.escalated_tier,
        }

    except (TypeSafeAPIError, TypeSafeError) as e:
        logger.error("Jev SQL verification error: %s", e)
        # Conservative fallback: allow execution but don't escalate
        decision.is_safe = "Yes"
        decision.telemetry = {"error": str(e), "fallback": True}

    return decision
