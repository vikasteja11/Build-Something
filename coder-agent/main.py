"""
Coder-Agent — FastAPI service that generates code from a plain-text spec.

Flow:
  1. Receive a spec via POST /generate
  2. Call the OpenRouter API to generate a code snippet
  3. Forward the result to reviewer-agent for review (service-to-service)
  4. Save everything to Postgres
  5. Return the combined result to the caller
"""

import json
import logging
import os
import traceback

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from database import SessionLocal, Task, init_db

# ---------------------------------------------------------------------------
# Configuration (all from environment variables)
# ---------------------------------------------------------------------------
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")
REVIEWER_URL = os.getenv("REVIEWER_URL", "http://localhost:8001")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("coder-agent")

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Coder Agent",
    description="Generates code from a plain-text spec and requests a review.",
    version="1.0.0",
)


@app.on_event("startup")
def on_startup():
    """Initialize DB tables on first run."""
    logger.info("Initializing database tables …")
    init_db()
    logger.info("Database ready.")


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------
class GenerateRequest(BaseModel):
    spec: str


class RefactorRequest(BaseModel):
    spec_id: str
    original_spec: str
    code: str
    review_comments: str


class GenerateResponse(BaseModel):
    spec_id: str
    spec: str
    generated_code: str
    review_comments: str | None = None
    verdict: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _call_openrouter(spec: str) -> str:
    """Call the OpenRouter chat-completions API to generate code from a spec.

    Returns the generated code string, or an error message if the call fails.
    """
    if not OPENROUTER_API_KEY:
        return "# ERROR: OPENROUTER_API_KEY is not set. Cannot generate code."

    try:
        # Use httpx to call the OpenRouter API (OpenAI-compatible).
        response = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENROUTER_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a helpful coding assistant. "
                            "Return ONLY the requested code with no extra explanation. "
                            "Use clear variable names and add brief inline comments."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Write code for the following specification:\n\n{spec}",
                    },
                ],
                "temperature": 0.2,
                "max_tokens": 1024,
            },
            timeout=30.0,
        )
        data = response.json()
        if "error" in data:
            err_msg = data["error"].get("message", str(data["error"]))
            return f"# ERROR: LLM returned error — {err_msg}"

        choices = data.get("choices", [])
        if not choices:
            return "# ERROR: LLM returned no choices."

        content = choices[0].get("message", {}).get("content", "")
        return content or "# ERROR: LLM returned empty content."
    except Exception as exc:
        logger.error("OpenRouter call failed: %s", exc)
        return f"# ERROR: LLM call failed — {exc}"


def _call_reviewer(spec_id: str, spec: str, code: str) -> dict:
    """Forward the generated code to the reviewer-agent for review.

    Returns a dict with 'review_comments' and 'verdict', or error info.
    """
    try:
        response = httpx.post(
            f"{REVIEWER_URL}/review",
            json={"spec_id": spec_id, "spec": spec, "code": code},
            timeout=60.0,
        )
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        logger.error("Reviewer call failed: %s", exc)
        return {
            "review_comments": f"Reviewer unavailable — {exc}",
            "verdict": "error",
        }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    """Liveness / readiness probe."""
    return {"status": "ok", "service": "coder-agent"}


@app.post("/generate-only")
def generate_only(req: GenerateRequest):
    """Generate code from a spec without immediately calling the reviewer."""
    spec = req.spec.strip()
    if not spec:
        raise HTTPException(status_code=400, detail="spec must not be empty")

    logger.info("Generating code (only) for spec: %s", spec[:80])
    generated_code = _call_openrouter(spec)

    db = SessionLocal()
    try:
        task = Task(spec=spec, generated_code=generated_code)
        db.add(task)
        db.commit()
        db.refresh(task)
        spec_id = str(task.id)
    except Exception:
        db.rollback()
        logger.error("DB write failed:\n%s", traceback.format_exc())
        raise HTTPException(status_code=500, detail="Database error")
    finally:
        db.close()

    return {
        "spec_id": spec_id,
        "spec": spec,
        "generated_code": generated_code,
    }


@app.post("/refactor")
def refactor_code(req: RefactorRequest):
    """Refactor code based on reviewer suggestions and return improved code."""
    spec_prompt = (
        f"Refactor and improve the code below according to the reviewer's feedback.\n\n"
        f"## Original Specification\n{req.original_spec}\n\n"
        f"## Reviewer Feedback & Suggestions to Implement\n{req.review_comments}\n\n"
        f"## Current Code\n```python\n{req.code}\n```"
    )

    logger.info("Refactoring code based on reviewer feedback for spec_id=%s", req.spec_id)
    improved_code = _call_openrouter(spec_prompt)

    # Persist refactored code as a new turn in PostgreSQL
    db = SessionLocal()
    try:
        refactored_spec_title = f"[Refactored based on review] {req.original_spec}"
        task = Task(spec=refactored_spec_title, generated_code=improved_code)
        db.add(task)
        db.commit()
        db.refresh(task)
        new_spec_id = str(task.id)
    except Exception:
        db.rollback()
        logger.error("DB write failed during refactor:\n%s", traceback.format_exc())
        raise HTTPException(status_code=500, detail="Database write error")
    finally:
        db.close()

    return {
        "spec_id": new_spec_id,
        "spec": refactored_spec_title,
        "generated_code": improved_code,
    }


@app.post("/generate", response_model=GenerateResponse)
def generate(req: GenerateRequest):
    """Generate code from a spec, have it reviewed, and persist everything."""
    spec = req.spec.strip()
    if not spec:
        raise HTTPException(status_code=400, detail="spec must not be empty")

    # Step 1 — generate code via LLM
    logger.info("Generating code for spec: %s", spec[:80])
    generated_code = _call_openrouter(spec)

    # Step 2 — persist initial record
    db = SessionLocal()
    try:
        task = Task(spec=spec, generated_code=generated_code)
        db.add(task)
        db.commit()
        db.refresh(task)
        spec_id = str(task.id)
    except Exception:
        db.rollback()
        logger.error("DB write failed:\n%s", traceback.format_exc())
        raise HTTPException(status_code=500, detail="Database error")
    finally:
        db.close()

    # Step 3 — call reviewer-agent
    review = _call_reviewer(spec_id, spec, generated_code)

    # The reviewer-agent updates the DB row itself; read back the latest state.
    review_comments = review.get("review_comments", "")
    verdict = review.get("verdict", "unknown")

    # If the reviewer couldn't reach the DB, we update locally as a fallback.
    db = SessionLocal()
    try:
        task = db.query(Task).filter(Task.id == spec_id).first()
        if task and not task.review_comments:
            task.review_comments = review_comments
            task.verdict = verdict
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()

    return GenerateResponse(
        spec_id=spec_id,
        spec=spec,
        generated_code=generated_code,
        review_comments=review_comments,
        verdict=verdict,
    )


@app.get("/records")
def records():
    """Return all stored tasks (most recent first)."""
    db = SessionLocal()
    try:
        tasks = db.query(Task).order_by(Task.created_at.desc()).all()
        return [
            {
                "spec_id": str(t.id),
                "spec": t.spec,
                "generated_code": t.generated_code,
                "review_comments": t.review_comments,
                "verdict": t.verdict,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "updated_at": t.updated_at.isoformat() if t.updated_at else None,
            }
            for t in tasks
        ]
    finally:
        db.close()
