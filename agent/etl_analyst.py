"""
ETL Analyst LangGraph — Jev-governed with dynamic Groq tier.

Flow: llm_node (tool dispatch) <-> tool_node (execute tools)
"""

import os
import logging

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import StateGraph, START, END
from langchain.tools import tool

from utils.llm_pick import pick_llm, get_model_name
from utils.etl_tools import ETLTools
from models.schema import ETLAgentSchema

logger = logging.getLogger(__name__)


# ========================== TOOLS ==========================


@tool
def extract_load_tool(url: str, output_folder: str, format: str) -> str:
    """
    Extract data from an API endpoint and save to disk.

    Args:
        url: The API endpoint URL.
        output_folder: Destination folder (relative to project root).
        format: Output format — csv, json, or parquet.

    Returns:
        Success or failure message.
    """
    etl = ETLTools()
    return etl.extract_load(url, output_folder, format)


@tool
def transform_load_tool(
    input_file_path: str,
    output_folder: str,
    output_format: str,
    user_question: str,
) -> str:
    """
    Transform data from a file using Pandas and save the result.

    Args:
        input_file_path: Path to the source data file.
        output_folder: Destination folder for transformed data.
        output_format: Output format — csv, json, or parquet.
        user_question: The user's transformation instruction.

    Returns:
        Summary of transformation including executed Pandas code.
    """
    etl = ETLTools()
    top_3_rows = etl.transform_load_context(input_file_path)

    # Use the model tier from the outer scope or default to "top" for code gen
    llm = pick_llm("top")

    prompt = f"""You are a Python Data Analyst who uses Pandas to analyze data.
Provide ONLY the Pandas code that will perform the ETL operation on the data stored
in the file: {input_file_path}

RULES:
- Output ONLY executable Python/Pandas code — no explanation, no markdown, no comments.
- Read the data from: {input_file_path}
- Save the transformed result to: {output_folder}
- Use the correct file format: {output_format}
- Create output directories with os.makedirs if needed.

User's question: {user_question}
Context (first 3 rows):
{top_3_rows}
"""
    response = llm.invoke(prompt).content

    # Clean markdown code fences
    pandas_code = response.strip().strip("```").strip().lstrip("python").strip()

    result = etl.execute_code(pandas_code)

    return (
        f"Data transformed and saved at {output_folder} in {output_format}.\n\n"
        f"Pandas Code:\n{pandas_code}\n\nExecution Result: {result}"
    )


# ========================== TOOLKIT ==========================

tools = [extract_load_tool, transform_load_tool]


# ========================== NODES ==========================


def llm_node(state: ETLAgentSchema) -> ETLAgentSchema:
    """Invoke the Groq LLM with tool binding to dispatch ETL operations."""
    messages = state.messages
    tier = state.model_tier or "medium"
    state.selected_groq_model = get_model_name(tier)

    llm = pick_llm(tier)
    llm_bind = llm.bind_tools(tools)

    prompt = f"""You are a Python Data Analyst with access to ETL tools:
1. extract_load_tool: Extract data from REST APIs and save to disk.
2. transform_load_tool: Read data files, apply Pandas transformations, and save results.

Perform the right ETL operation based on the user's request.
Once the operation is complete, inform the user with a summary.

Chat history: {messages}
"""
    final_answer = llm_bind.invoke(prompt)
    state.messages = [final_answer]
    return state


def tool_node(state: ETLAgentSchema) -> ETLAgentSchema:
    """Execute tool calls from the LLM response."""
    tool_results = []
    tools_by_name = {t.name: t for t in tools}
    tool_calls = state.messages[-1].tool_calls

    for tool_call in tool_calls:
        t = tools_by_name[tool_call["name"]]
        # Fix: was tool.invoke(tool_call['name']) in original — should be args
        observation = t.invoke(tool_call["args"])
        tool_results.append(
            ToolMessage(content=str(observation), tool_call_id=tool_call["id"])
        )

    state.messages = tool_results
    return state


# ========================== GRAPH ==========================


def _build_etl_graph() -> StateGraph:
    graph = StateGraph(ETLAgentSchema)

    graph.add_node("llm_node", llm_node)
    graph.add_node("tool_node", tool_node)

    graph.add_edge(START, "llm_node")

    def is_tool_call(state: ETLAgentSchema) -> str:
        if hasattr(state.messages[-1], "tool_calls") and state.messages[-1].tool_calls:
            return "tool_node"
        return "end"

    graph.add_conditional_edges(
        "llm_node",
        is_tool_call,
        {"tool_node": "tool_node", "end": END},
    )

    graph.add_edge("tool_node", "llm_node")
    return graph


etl_analyst = _build_etl_graph().compile()
