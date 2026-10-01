"""LangGraph state schemas and FastAPI request/response models."""

from typing import Annotated, Any, Literal
from operator import add
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# LangGraph State Schemas
# ---------------------------------------------------------------------------

class AgentSchema(BaseModel):
    """State for the SQL analyst LangGraph."""
    messages: Annotated[list, add] = Field(default_factory=list, description="Chat message history")
    user_question: str = Field(default="", description="Original user question")
    curated_ques: str = Field(default="", description="Curated/rewritten question")
    prompt_query_context: str = Field(default="", description="Full prompt with DB schema context")
    generated_sql_query: str = Field(default="", description="Generated SQL query")
    is_safe: Literal["Yes", "No", "escalate"] = Field(default="No", description="SQL safety verdict")
    comments: str = Field(default="", description="Safety judge comments")
    sql_query_execution_result: str = Field(default="", description="SQL execution result")
    final_answer: str = Field(default="", description="Natural language answer")
    # --- Jev metadata ---
    model_tier: str = Field(default="medium", description="Groq model tier selected by Jev")
    selected_groq_model: str = Field(default="", description="Actual Groq model name")
    route_confidence: float = Field(default=0.0, description="Jev route confidence")
    complexity_score: float = Field(default=0.0, description="Jev complexity score (0-3)")
    needs_curation: bool = Field(default=True, description="Whether Jev says prompt needs curation")
    escalated: bool = Field(default=False, description="Whether model was escalated by Jev cascade")
    jev_telemetry: dict[str, Any] = Field(default_factory=dict, description="Full Jev decision telemetry")


class JudgeSchema(BaseModel):
    """Structured output for the SQL safety judge (legacy compatibility)."""
    answer: Literal["Yes", "No"] = Field(..., description="Whether the SQL is safe")
    comments: str = Field(..., description="Judge comments")


class ETLAgentSchema(BaseModel):
    """State for the ETL analyst LangGraph."""
    messages: Annotated[list, add] = Field(default_factory=list, description="Chat message history")
    # --- Jev metadata ---
    model_tier: str = Field(default="medium", description="Groq model tier selected by Jev")
    selected_groq_model: str = Field(default="", description="Actual Groq model name")
    etl_operation: str = Field(default="", description="Jev-selected ETL operation")
    complexity_score: float = Field(default=0.0, description="Jev complexity score")
    jev_telemetry: dict[str, Any] = Field(default_factory=dict, description="Full Jev telemetry")


class RouterSchema(BaseModel):
    """Structured output for the legacy LLM-based router (kept for reference)."""
    answer: Literal["sql", "etl"] = Field(..., description="Route classification")
    comments: str = Field(..., description="Router comments")


class DataAgentSchema(BaseModel):
    """State for the top-level data agent LangGraph."""
    messages: Annotated[list, add] = Field(default_factory=list, description="Chat history")
    route_response: str = Field(default="", description="Jev route decision")
    # --- Jev metadata ---
    model_tier: str = Field(default="medium", description="Selected Groq tier")
    selected_groq_model: str = Field(default="", description="Actual Groq model name")
    route_confidence: float = Field(default=0.0, description="Jev route confidence")
    complexity_score: float = Field(default=0.0, description="Jev complexity score")
    needs_curation: bool = Field(default=True, description="Prompt needs curation")
    etl_operation: str = Field(default="", description="Jev ETL operation choice")
    jev_telemetry: dict[str, Any] = Field(default_factory=dict, description="Full Jev telemetry")
    final_answer: str = Field(default="", description="Final answer from sub-agent")


# ---------------------------------------------------------------------------
# FastAPI Request / Response Models
# ---------------------------------------------------------------------------

class AgentQueryRequest(BaseModel):
    """POST /api/v1/agent/query"""
    message: str = Field(..., description="Natural language query", min_length=1)
    model_tier: str | None = Field(default=None, description="Optional manual tier override (low/medium/high/top)")


class AgentQueryResponse(BaseModel):
    """Unified response for all agent endpoints."""
    status: str = Field(default="success")
    route: str = Field(default="", description="Agent route taken")
    model_tier: str = Field(default="", description="Groq model tier used")
    groq_model: str = Field(default="", description="Actual Groq model name")
    answer: str = Field(default="", description="Final answer or result")
    sql_query: str | None = Field(default=None, description="Generated SQL (if SQL route)")
    jev_telemetry: dict[str, Any] = Field(default_factory=dict, description="Jev decision details")


class SQLQueryRequest(BaseModel):
    """POST /api/v1/sql/query"""
    question: str = Field(..., description="Natural language SQL question", min_length=1)
    model_tier: str | None = Field(default=None, description="Optional tier override")


class ETLRunRequest(BaseModel):
    """POST /api/v1/etl/run"""
    message: str = Field(..., description="ETL task description", min_length=1)
    model_tier: str | None = Field(default=None, description="Optional tier override")


class SQLVerifyRequest(BaseModel):
    """POST /api/v1/jev/verify-sql"""
    question: str = Field(..., description="Original user question")
    sql_query: str = Field(..., description="SQL query to verify")
    current_tier: str = Field(default="medium", description="Current Groq tier")


class TriageRequest(BaseModel):
    """POST /api/v1/jev/triage"""
    message: str = Field(..., description="User message to triage", min_length=1)
