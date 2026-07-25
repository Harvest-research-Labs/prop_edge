"""ProEdge API gateway (Phase 2, Step 1).

Seven synchronous services wrapping the existing models behind stable contracts.
No Redis / workers / websockets / Postgres / multi-sport in this slice.

Run:  uvicorn api.main:app --reload --port 8000
Docs: http://localhost:8000/docs
"""
from fastapi import FastAPI

from . import core, services
from .schemas import (ApiResponse, ExtractRequest, ResolveRequest, ProjectRequest,
                      PriceRequest, RankRequest, SlipRequest, ExplainRequest)

app = FastAPI(title="ProEdge API", version=core.SERVICE_VERSION,
              description="Stable service contracts around the existing PropEdge models.")


def _wrap(result):
    """result: dict from a service -> ApiResponse envelope."""
    m = core.meta(source=result["source"], status=result["status"],
                  warnings=result["warnings"], errors=result["errors"],
                  confidence=result.get("confidence"))
    return ApiResponse(**m, data=result["data"])


@app.get("/health")
def health():
    return {"status": "ok", "service_version": core.SERVICE_VERSION,
            "model_version": core.MODEL_VERSION, "timestamp": core.now_iso()}


@app.get("/version")
def version():
    return {"service_version": core.SERVICE_VERSION, "model_version": core.MODEL_VERSION,
            "endpoints": ["/extract", "/resolve", "/project", "/price", "/rank",
                          "/slip/eval", "/explain"]}


@app.post("/extract", response_model=ApiResponse)
def extract(req: ExtractRequest):
    return _wrap(services.svc_extract(req))


@app.post("/resolve", response_model=ApiResponse)
def resolve(req: ResolveRequest):
    return _wrap(services.svc_resolve(req))


@app.post("/project", response_model=ApiResponse)
def project(req: ProjectRequest):
    return _wrap(services.svc_project(req))


@app.post("/price", response_model=ApiResponse)
def price(req: PriceRequest):
    return _wrap(services.svc_price(req))


@app.post("/rank", response_model=ApiResponse)
def rank(req: RankRequest):
    return _wrap(services.svc_rank(req))


@app.post("/slip/eval", response_model=ApiResponse)
def slip_eval(req: SlipRequest):
    return _wrap(services.svc_slip(req))


@app.post("/explain", response_model=ApiResponse)
def explain(req: ExplainRequest):
    return _wrap(services.svc_explain(req))
