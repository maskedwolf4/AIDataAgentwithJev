"""
SQL Analyst LangGraph — Jev-governed with cascade escalation.

Flow: [curate_ques?] -> prompt_query_context -> generate_sql -> is_safe_sql
      -> execute_sql -> represent_final_answer
      (or canceled_sql / cascade back to generate_sql)
"""

import os
import re
import logging

from langchain_core.messages import HumanMessage, AIMessage
from langgraph.graph import StateGraph, START, END

from utils.llm_pick import pick_llm, get_model_name, escalate_tier
from utils.database import DatabaseUtil
from utils.jev_router import verify_sql as jev_verify_sql
from models.schema import AgentSchema

logger = logging.getLogger(__name__)

# Resolve DB path relative to project root
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_DB_PATH = os.path.join(_PROJECT_ROOT, "rideshare.db")


# ========================== NODES ==========================


def curate_ques(state: AgentSchema) -> AgentSchema:
    """Rewrite a poorly worded user question (skipped when Jev says not needed)."""
    user_question = state.user_question
    llm = pick_llm("low")
    response = llm.invoke(
        f"Rewrite the following question more clearly and precisely, "
        f"keeping the original intent: {user_question}"
    ).content
    state.curated_ques = response
    state.messages = [HumanMessage(content=response)]
    return state


def skip_curation(state: AgentSchema) -> AgentSchema:
    """Pass through user question as-is when curation is not needed."""
    state.curated_ques = state.user_question
    state.messages = [HumanMessage(content=state.user_question)]
    return state


def prompt_query_context(state: AgentSchema) -> AgentSchema:
    """Build the SQL generation prompt with full DB schema context."""
    curated_question = state.curated_ques

    db_obj = DatabaseUtil(_DB_PATH)
    schema_info = db_obj.schema_details()

    # Fix: prompt says SQLite (was "Postgres" in original)
    prompt = f"""You are an SQL analyst agent. Your task is to convert the user's natural language
query into a **SQLite** SQL query that can be executed on the database.

IMPORTANT RULES:
- Generate ONLY the raw SQL query without any explanation, markdown, or code fences.
- Unless the user explicitly asks for a specific number of rows, LIMIT output to 10 rows.
- Use SQLite syntax (e.g., use || for string concatenation, no ILIKE — use LIKE with LOWER()).
- Do NOT use any DML/DDL statements (INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, TRUNCATE).

User's Question: {curated_question}

Database Schema Details:
{schema_info}
"""
    state.prompt_query_context = prompt
    return state


def generate_sql(state: AgentSchema) -> AgentSchema:
    """Generate SQL using the Groq model tier selected (or escalated) by Jev."""
    prompt = state.prompt_query_context
    tier = state.model_tier
    state.selected_groq_model = get_model_name(tier)

    llm = pick_llm(tier)
    raw = llm.invoke(prompt).content

    # Strip markdown code fences if present
    cleaned = re.sub(r"^```(?:sql)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip())
    state.generated_sql_query = cleaned.strip()
    return state


def is_safe_sql(state: AgentSchema) -> AgentSchema:
    """Validate SQL with Jev guardrail battery + cascade logic."""
    verification = jev_verify_sql(
        user_question=state.user_question,
        sql_query=state.generated_sql_query,
        current_tier=state.model_tier,
        already_escalated=state.escalated,
    )

    state.is_safe = verification.is_safe
    state.comments = verification.block_reason

    # Merge verification telemetry into state
    jev_tel = state.jev_telemetry.copy()
    jev_tel["sql_verification"] = verification.telemetry
    state.jev_telemetry = jev_tel

    # Handle cascade escalation
    if verification.should_escalate_model:
        state.model_tier = verification.escalated_tier
        state.escalated = True
        state.is_safe = "escalate"  # triggers conditional edge

    return state


def canceled_sql(state: AgentSchema) -> AgentSchema:
    """Block unsafe SQL and return a safety message."""
    state.final_answer = (
        f"The generated SQL query was blocked by the Jev safety guardrail. "
        f"Reason: {state.comments}. The query will not be executed."
    )
    state.messages = [AIMessage(content=state.final_answer)]
    return state


def execute_sql(state: AgentSchema) -> AgentSchema:
    """Execute the verified SQL against the SQLite database."""
    obj = DatabaseUtil(_DB_PATH)
    result = obj.execute_sql(state.generated_sql_query)
    state.sql_query_execution_result = result or "No results returned."
    return state


def represent_final_answer(state: AgentSchema) -> AgentSchema:
    """Generate a human-readable answer from the SQL results."""
    llm = pick_llm("low")

    prompt = f"""You are a data analyst. Provide a clear, concise answer to the user's question
based on the SQL query results. Do NOT include SQL code or technical details.
If the result is empty, explain that no matching data was found.

User's question: {state.curated_ques}
SQL query executed: {state.generated_sql_query}
Execution result: {state.sql_query_execution_result}
"""
    response = llm.invoke(prompt).content
    state.final_answer = response
    state.messages = [AIMessage(content=response)]
    return state


# ========================== GRAPH ==========================


def _build_sql_graph() -> StateGraph:
    graph = StateGraph(AgentSchema)

    # Nodes
    graph.add_node("curate_ques", curate_ques)
    graph.add_node("skip_curation", skip_curation)
    graph.add_node("prompt_query_context", prompt_query_context)
    graph.add_node("generate_sql", generate_sql)
    graph.add_node("is_safe_sql", is_safe_sql)
    graph.add_node("canceled_sql", canceled_sql)
    graph.add_node("execute_sql", execute_sql)
    graph.add_node("represent_final_answer", represent_final_answer)

    # Entry: decide whether to curate based on Jev's needs_curation flag
    def curation_gate(state: AgentSchema) -> str:
        return "curate_ques" if state.needs_curation else "skip_curation"

    graph.add_conditional_edges(START, curation_gate, {
        "curate_ques": "curate_ques",
        "skip_curation": "skip_curation",
    })

    graph.add_edge("curate_ques", "prompt_query_context")
    graph.add_edge("skip_curation", "prompt_query_context")
    graph.add_edge("prompt_query_context", "generate_sql")
    graph.add_edge("generate_sql", "is_safe_sql")

    # Safety gate: safe -> execute, unsafe -> cancel, escalate -> re-generate
    def safety_edge(state: AgentSchema) -> str:
        if state.is_safe == "Yes":
            return "execute_sql"
        elif state.is_safe == "escalate":
            return "generate_sql"
        else:
            return "canceled_sql"

    graph.add_conditional_edges("is_safe_sql", safety_edge, {
        "execute_sql": "execute_sql",
        "generate_sql": "generate_sql",
        "canceled_sql": "canceled_sql",
    })

    graph.add_edge("canceled_sql", END)
    graph.add_edge("execute_sql", "represent_final_answer")
    graph.add_edge("represent_final_answer", END)

    return graph


sql_analyst = _build_sql_graph().compile()
