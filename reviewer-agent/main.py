"""
Reviewer-Agent — FastAPI service that reviews generated code.

Accepts code + the original spec, calls the OpenRouter API for a structured
code review, updates the shared Postgres row, and returns the review.
"""

import json
import logging
import os
import traceback

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx

from database import SessionLocal, Task, init_db

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("reviewer-agent")

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Reviewer Agent",
    description="Reviews generated code for bugs and style issues.",
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
class ReviewRequest(BaseModel):
    spec_id: str
    spec: str
    code: str


class ReviewResponse(BaseModel):
    spec_id: str
    review_comments: str
    verdict: str
    error: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _call_openrouter(spec: str, code: str) -> dict:
    """Call the OpenRouter API to review the given code against the spec.

    Returns a dict with 'review_comments' (str) and 'verdict' (str).
    """
    if not OPENROUTER_API_KEY:
        return {
            "review_comments": "ERROR: OPENROUTER_API_KEY is not set. Cannot review code.",
            "verdict": "error",
        }

    try:
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
                            "You are a senior code reviewer. "
                            "Review the code below against the given specification. "
                            "List any bugs, style issues, or improvements as bullet points. "
                            "Then give a final verdict: either 'pass' if the code is acceptable "
                            "or 'needs-work' if it has significant issues.\n\n"
                            "Respond in this exact JSON format:\n"
                            '{"comments": ["comment 1", "comment 2"], "verdict": "pass"}'
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"## Specification\n{spec}\n\n"
                            f"## Code to review\n```\n{code}\n```"
                        ),
                    },
                ],
                "temperature": 0.1,
                "max_tokens": 1024,
            },
            timeout=30.0,
        )
        data = response.json()
        if "error" in data:
            err_msg = data["error"].get("message", str(data["error"]))
            return {"review_comments": f"LLM error: {err_msg}", "verdict": "error"}

        choices = data.get("choices", [])
        if not choices:
            return {"review_comments": "LLM returned no response choices.", "verdict": "error"}

        content = choices[0].get("message", {}).get("content", "")
        if not content:
            return {"review_comments": "LLM returned empty content.", "verdict": "error"}

        # Clean markdown ```json wrappers if present
        clean_content = content.strip()
        if clean_content.startswith("```"):
            lines = clean_content.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            clean_content = "\n".join(lines).strip()

        # Try to parse structured JSON from the LLM response
        try:
            parsed = json.loads(clean_content)
            comments = parsed.get("comments", [])
            verdict = parsed.get("verdict", "pass")
            if isinstance(comments, list):
                comments = "\n".join(f"- {c}" for c in comments)
            return {"review_comments": comments, "verdict": verdict}
        except Exception:
            # LLM didn't return valid JSON — use the raw text
            return {"review_comments": content, "verdict": "pass"}

    except Exception as exc:
        logger.error("OpenRouter call failed: %s", exc)
        return {
            "review_comments": f"LLM review call failed — {exc}",
            "verdict": "error",
        }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    """Liveness / readiness probe."""
    return {"status": "ok", "service": "reviewer-agent"}


@app.post("/review", response_model=ReviewResponse)
def review(req: ReviewRequest):
    """Review generated code and persist the result."""
    spec_id = req.spec_id
    spec = req.spec.strip()
    code = req.code.strip()

    if not code:
        raise HTTPException(status_code=400, detail="code must not be empty")

    # Call LLM for review
    logger.info("Reviewing code for spec_id=%s", spec_id)
    result = _call_openrouter(spec, code)
    review_comments = result["review_comments"]
    verdict = result["verdict"]

    # Update the existing DB row created by the coder-agent
    db = SessionLocal()
    try:
        task = db.query(Task).filter(Task.id == spec_id).first()
        if task:
            task.review_comments = review_comments
            task.verdict = verdict
            db.commit()
            logger.info("Updated task %s with review.", spec_id)
        else:
            logger.warning("Task %s not found in DB — skipping DB update.", spec_id)
    except Exception:
        db.rollback()
        logger.error("DB update failed:\n%s", traceback.format_exc())
    finally:
        db.close()

    return ReviewResponse(
        spec_id=spec_id,
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
