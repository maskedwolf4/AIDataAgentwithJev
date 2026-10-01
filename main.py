"""
FastAPI backend for the Jev-routed AI Data Agent.

Endpoints:
  GET  /health                  — Health check with API key status
  POST /api/v1/agent/query      — Main unified agent endpoint
  POST /api/v1/jev/triage       — Standalone Jev triage test
  POST /api/v1/sql/query        — Direct SQL analyst endpoint
  POST /api/v1/etl/run          — Direct ETL analyst endpoint
  POST /api/v1/jev/verify-sql   — Standalone Jev SQL guardrail test
"""

import os
import logging

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import HumanMessage

from models.schema import (
    AgentQueryRequest,
    AgentQueryResponse,
    ETLRunRequest,
    SQLQueryRequest,
    SQLVerifyRequest,
    TriageRequest,
)
from utils.llm_pick import GROQ_MODEL_MAP, get_model_name
from utils.jev_router import triage_request, verify_sql
from utils.database import DatabaseUtil
from agent.data_agent import data_agent
from agent.sql_analyst import sql_analyst
from agent.etl_analyst import etl_analyst

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="AI Data Agent — Jev + Groq",
    description=(
        "Production AI Data Engineering Agent with TypeSafe Jev routing "
        "and Groq LLMs. Test all endpoints via Postman."
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
_DB_PATH = os.path.join(_PROJECT_ROOT, "rideshare.db")


def _db_ok() -> bool:
    return os.path.exists(_DB_PATH)


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    """Health check — shows API key presence, DB status, and model mapping."""
    return {
        "status": "healthy",
        "groq_api_key_set": bool(os.getenv("GROQ_API_KEY")),
        "typesafe_api_key_set": bool(os.getenv("TYPESAFE_API_KEY")),
        "database_exists": _db_ok(),
        "database_path": _DB_PATH,
        "groq_model_map": GROQ_MODEL_MAP,
    }


# ---------------------------------------------------------------------------
# POST /api/v1/agent/query  — Main unified endpoint
# ---------------------------------------------------------------------------

@app.post("/api/v1/agent/query", response_model=AgentQueryResponse)
def agent_query(req: AgentQueryRequest):
    """
    Send any natural-language request → Jev triages & selects Groq model
    → executes SQL or ETL agent → returns answer + full Jev telemetry.
    """
    try:
        input_state = {
            "messages": [HumanMessage(content=req.message)],
            "route_response": "",
            "model_tier": req.model_tier or "medium",
            "final_answer": "",
        }

        result = data_agent.invoke(input_state)

        # Extract SQL query if available
        sql_query = None
        jev_tel = result.get("jev_telemetry", {})
        sql_agent_info = jev_tel.get("sql_agent", {})
        if sql_agent_info:
            sql_query = sql_agent_info.get("generated_sql", None)

        return AgentQueryResponse(
            status="success",
            route=result.get("route_response", ""),
            model_tier=result.get("model_tier", ""),
            groq_model=result.get("selected_groq_model", ""),
            answer=result.get("final_answer", ""),
            sql_query=sql_query,
            jev_telemetry=jev_tel,
        )

    except Exception as e:
        logger.exception("Agent query failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# POST /api/v1/jev/triage  — Standalone Jev triage
# ---------------------------------------------------------------------------

@app.post("/api/v1/jev/triage")
def jev_triage(req: TriageRequest):
    """
    Test Jev's routing decision without running any Groq LLM.
    Returns raw Choice/Score/Noul probabilities and confidence scores.
    """
    try:
        decision = triage_request(req.message)
        return {
            "status": "success",
            "route": decision.route,
            "route_confidence": decision.route_confidence,
            "etl_operation": decision.etl_operation,
            "etl_operation_confidence": decision.etl_operation_confidence,
            "complexity_score": decision.complexity_score,
            "model_tier": decision.model_tier,
            "groq_model": get_model_name(decision.model_tier),
            "needs_curation": decision.needs_curation,
            "is_unsafe": decision.is_unsafe,
            "blocked_reason": decision.blocked_reason,
            "jev_telemetry": decision.telemetry,
        }
    except Exception as e:
        logger.exception("Jev triage failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# POST /api/v1/sql/query  — Direct SQL analyst
# ---------------------------------------------------------------------------

@app.post("/api/v1/sql/query", response_model=AgentQueryResponse)
def sql_query(req: SQLQueryRequest):
    """
    Run a natural-language SQL question directly against the SQL analyst.
    Optionally override the Groq model tier.
    """
    try:
        # Quick Jev triage for complexity + curation
        decision = triage_request(req.question)
        tier = req.model_tier or decision.model_tier

        input_schema = {
            "messages": [],
            "user_question": req.question,
            "curated_ques": "",
            "prompt_query_context": "",
            "generated_sql_query": "",
            "is_safe": "No",
            "comments": "",
            "sql_query_execution_result": "",
            "final_answer": "",
            "model_tier": tier,
            "selected_groq_model": get_model_name(tier),
            "route_confidence": decision.route_confidence,
            "complexity_score": decision.complexity_score,
            "needs_curation": decision.needs_curation,
            "escalated": False,
            "jev_telemetry": decision.telemetry,
        }

        result = sql_analyst.invoke(input_schema)

        return AgentQueryResponse(
            status="success",
            route="sql",
            model_tier=result.get("model_tier", tier),
            groq_model=result.get("selected_groq_model", ""),
            answer=result.get("final_answer", ""),
            sql_query=result.get("generated_sql_query", ""),
            jev_telemetry=result.get("jev_telemetry", {}),
        )

    except Exception as e:
        logger.exception("SQL query failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# POST /api/v1/etl/run  — Direct ETL analyst
# ---------------------------------------------------------------------------

@app.post("/api/v1/etl/run", response_model=AgentQueryResponse)
def etl_run(req: ETLRunRequest):
    """Run an ETL task directly via the ETL analyst."""
    try:
        decision = triage_request(req.message)
        tier = req.model_tier or decision.model_tier

        input_schema = {
            "messages": [HumanMessage(content=req.message)],
            "model_tier": tier,
            "selected_groq_model": get_model_name(tier),
            "etl_operation": decision.etl_operation,
            "complexity_score": decision.complexity_score,
            "jev_telemetry": decision.telemetry,
        }

        result = etl_analyst.invoke(input_schema)

        # Get final message from ETL agent
        etl_msgs = result.get("messages", [])
        final = ""
        for msg in reversed(etl_msgs):
            if hasattr(msg, "content") and msg.content:
                final = msg.content
                break

        return AgentQueryResponse(
            status="success",
            route="etl",
            model_tier=tier,
            groq_model=get_model_name(tier),
            answer=final,
            jev_telemetry=result.get("jev_telemetry", {}),
        )

    except Exception as e:
        logger.exception("ETL run failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# POST /api/v1/jev/verify-sql  — Standalone SQL guardrail test
# ---------------------------------------------------------------------------

@app.post("/api/v1/jev/verify-sql")
def jev_verify_sql_endpoint(req: SQLVerifyRequest):
    """Test Jev's SQL verification battery against any SQL query."""
    try:
        result = verify_sql(
            user_question=req.question,
            sql_query=req.sql_query,
            current_tier=req.current_tier,
            already_escalated=False,
        )
        return {
            "status": "success",
            "is_safe": result.is_safe,
            "should_escalate": result.should_escalate_model,
            "escalated_tier": result.escalated_tier,
            "block_reason": result.block_reason,
            "jev_telemetry": result.telemetry,
        }
    except Exception as e:
        logger.exception("SQL verification failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
