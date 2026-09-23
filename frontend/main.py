"""
Frontend Microservice — FastAPI web application providing a sleek UI
for the multi-agent code generator and reviewer system.
"""

import os
import logging
import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

CODER_AGENT_URL = os.getenv("CODER_AGENT_URL", "http://coder-agent:8000")
REVIEWER_AGENT_URL = os.getenv("REVIEWER_AGENT_URL", "http://reviewer-agent:8001")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("frontend")

app = FastAPI(
    title="Multi-Agent Code Studio UI",
    description="Frontend UI microservice for Code Studio",
    version="1.0.0",
)

# Setup templates and static directories
base_dir = os.path.dirname(os.path.abspath(__file__))
static_dir = os.path.join(base_dir, "static")
templates_dir = os.path.join(base_dir, "templates")

os.makedirs(static_dir, exist_ok=True)
os.makedirs(templates_dir, exist_ok=True)

app.mount("/static", StaticFiles(directory=static_dir), name="static")
templates = Jinja2Templates(directory=templates_dir)


class GenerateProxyRequest(BaseModel):
    spec: str


class ReviewProxyRequest(BaseModel):
    spec_id: str
    spec: str
    code: str


@app.get("/", response_class=HTMLResponse)
def read_root(request: Request):
    """Serve the main web UI page."""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/health")
def health():
    """Liveness probe for the frontend microservice."""
    return {"status": "ok", "service": "frontend"}


@app.get("/api/system-status")
async def system_status():
    """Check health of downstream microservices."""
    coder_status = "offline"
    reviewer_status = "offline"

    async with httpx.AsyncClient(timeout=3.0) as client:
        try:
            r = await client.get(f"{CODER_AGENT_URL}/health")
            if r.status_code == 200:
                coder_status = "online"
        except Exception:
            pass

        try:
            r = await client.get(f"{REVIEWER_AGENT_URL}/health")
            if r.status_code == 200:
                reviewer_status = "online"
        except Exception:
            pass

    return {
        "frontend": "online",
        "coder_agent": coder_status,
        "reviewer_agent": reviewer_status,
    }


@app.post("/api/generate-only")
async def generate_only_proxy(payload: GenerateProxyRequest):
    """Proxy request to coder-agent to generate code only."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.post(
                f"{CODER_AGENT_URL}/generate-only",
                json={"spec": payload.spec},
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            logger.error("Failed to generate code: %s", exc)
            raise HTTPException(status_code=503, detail=f"Coder Agent unavailable: {exc}")


@app.post("/api/review-only")
async def review_only_proxy(payload: ReviewProxyRequest):
    """Proxy request directly to reviewer-agent to review generated code."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.post(
                f"{REVIEWER_AGENT_URL}/review",
                json={
                    "spec_id": payload.spec_id,
                    "spec": payload.spec,
                    "code": payload.code,
                },
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            logger.error("Failed to review code: %s", exc)
            raise HTTPException(status_code=503, detail=f"Reviewer Agent unavailable: {exc}")


@app.post("/api/generate")
async def generate_proxy(payload: GenerateProxyRequest):
    """Proxy code generation request to coder-agent microservice."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.post(
                f"{CODER_AGENT_URL}/generate",
                json={"spec": payload.spec},
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            logger.error("Coder agent error: %s", exc)
            raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text)
        except Exception as exc:
            logger.error("Failed to reach coder-agent: %s", exc)
            raise HTTPException(status_code=503, detail=f"Coder Agent unavailable: {exc}")


class RefactorProxyRequest(BaseModel):
    spec_id: str
    original_spec: str
    code: str
    review_comments: str


@app.post("/api/refactor")
async def refactor_proxy(payload: RefactorProxyRequest):
    """Proxy request to coder-agent to refactor code based on reviewer suggestions."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.post(
                f"{CODER_AGENT_URL}/refactor",
                json={
                    "spec_id": payload.spec_id,
                    "original_spec": payload.original_spec,
                    "code": payload.code,
                    "review_comments": payload.review_comments,
                },
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            logger.error("Failed to refactor code: %s", exc)
            raise HTTPException(status_code=503, detail=f"Coder Agent unavailable for refactoring: {exc}")


@app.get("/api/records")
async def records_proxy():
    """Proxy request to retrieve history records from coder-agent."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get(f"{CODER_AGENT_URL}/records")
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            logger.error("Failed to fetch records: %s", exc)
            raise HTTPException(status_code=503, detail="Unable to fetch history records")
