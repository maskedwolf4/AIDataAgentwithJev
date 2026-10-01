"""
Top-level Data Agent LangGraph — Jev-routed.

Routes user requests through Jev System One (0 Groq tokens) to either:
  - sql_node  (SQL Analyst subgraph)
  - etl_node  (ETL Analyst subgraph)
  - clarify_node (when confidence is low or intent is unsafe)
"""

import logging

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import StateGraph, START, END

from utils.llm_pick import get_model_name
from utils.jev_router import triage_request
from models.schema import DataAgentSchema
from agent.sql_analyst import sql_analyst
from agent.etl_analyst import etl_analyst

logger = logging.getLogger(__name__)


# ========================== NODES ==========================


def router_node(state: DataAgentSchema) -> DataAgentSchema:
    """
    Use Jev System One to classify the request — 0 Groq LLM tokens consumed.

    Sets route, model_tier, needs_curation, and full telemetry on state.
    If the caller set a manual model_tier override, it is preserved.
    """
    message = state.messages[-1].content if state.messages else ""
    manual_tier = state.model_tier  # may be a user override from the API

    decision = triage_request(message)

    state.route_response = decision.route
    # Only use Jev's tier if no manual override was provided
    if not manual_tier or manual_tier == "medium":
        state.model_tier = decision.model_tier
    state.selected_groq_model = get_model_name(state.model_tier)
    state.route_confidence = decision.route_confidence
    state.complexity_score = decision.complexity_score
    state.needs_curation = decision.needs_curation
    state.etl_operation = decision.etl_operation
    state.jev_telemetry = decision.telemetry

    return state


def clarify_node(state: DataAgentSchema) -> DataAgentSchema:
    """Return a clarification message when Jev blocks or confidence is low."""
    blocked = state.jev_telemetry.get("blocked_reason", "")
    msg = (
        blocked
        if blocked
        else (
            "I wasn't confident enough in how to route your request. "
            "Could you please rephrase or provide more detail?"
        )
    )
    state.final_answer = msg
    state.messages = [AIMessage(content=msg)]
    return state


def sql_node(state: DataAgentSchema) -> DataAgentSchema:
    """Run the SQL Analyst subgraph with Jev-selected model tier."""
    message = state.messages[-1].content if state.messages else ""

    input_schema = {
        "messages": [],
        "user_question": message,
        "curated_ques": "",
        "prompt_query_context": "",
        "generated_sql_query": "",
        "is_safe": "No",
        "comments": "",
        "sql_query_execution_result": "",
        "final_answer": "",
        "model_tier": state.model_tier,
        "selected_groq_model": state.selected_groq_model,
        "route_confidence": state.route_confidence,
        "complexity_score": state.complexity_score,
        "needs_curation": state.needs_curation,
        "escalated": False,
        "jev_telemetry": state.jev_telemetry,
    }

    result = sql_analyst.invoke(input_schema)
    state.final_answer = result.get("final_answer", "")

    # Merge SQL telemetry
    jev_tel = state.jev_telemetry.copy()
    jev_tel["sql_agent"] = {
        "generated_sql": result.get("generated_sql_query", ""),
        "is_safe": result.get("is_safe", ""),
        "escalated": result.get("escalated", False),
        "model_tier_used": result.get("model_tier", ""),
    }
    if "sql_verification" in result.get("jev_telemetry", {}):
        jev_tel["sql_verification"] = result["jev_telemetry"]["sql_verification"]
    state.jev_telemetry = jev_tel

    state.messages = [AIMessage(content=state.final_answer)]
    return state


def etl_node(state: DataAgentSchema) -> DataAgentSchema:
    """Run the ETL Analyst subgraph with Jev-selected model tier."""
    message = state.messages[-1].content if state.messages else ""

    input_schema = {
        "messages": [HumanMessage(content=message)],
        "model_tier": state.model_tier,
        "selected_groq_model": state.selected_groq_model,
        "etl_operation": state.etl_operation,
        "complexity_score": state.complexity_score,
        "jev_telemetry": state.jev_telemetry,
    }

    result = etl_analyst.invoke(input_schema)

    # Extract final answer from ETL agent messages
    etl_messages = result.get("messages", [])
    final_msg = ""
    for msg in reversed(etl_messages):
        if hasattr(msg, "content") and msg.content:
            final_msg = msg.content
            break

    state.final_answer = final_msg
    state.messages = [AIMessage(content=final_msg)]
    return state


# ========================== GRAPH ==========================


def _build_data_agent_graph() -> StateGraph:
    graph = StateGraph(DataAgentSchema)

    graph.add_node("router_node", router_node)
    graph.add_node("clarify_node", clarify_node)
    graph.add_node("sql_node", sql_node)
    graph.add_node("etl_node", etl_node)

    graph.add_edge(START, "router_node")

    def route_edge(state: DataAgentSchema) -> str:
        if state.route_response == "sql":
            return "sql_node"
        elif state.route_response == "etl":
            return "etl_node"
        else:
            return "clarify_node"

    graph.add_conditional_edges(
        "router_node",
        route_edge,
        {
            "sql_node": "sql_node",
            "etl_node": "etl_node",
            "clarify_node": "clarify_node",
        },
    )

    graph.add_edge("sql_node", END)
    graph.add_edge("etl_node", END)
    graph.add_edge("clarify_node", END)

    return graph


data_agent = _build_data_agent_graph().compile()
