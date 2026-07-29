"""Serves the most recent RAGAS evaluation results (written by
eval/run_eval.py) to the frontend's Eval Dashboard page."""

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.deps import get_current_user
from app.models import User

router = APIRouter(prefix="/admin", tags=["eval"])

# This file always lives two directories below the backend package root
# (backend/app/routers/eval_router.py locally, /app/app/routers/... in the
# Docker image where backend/'s contents become /app) -- parents[2] lands
# on that root either way, so eval/run_eval.py (see BACKEND_DIR there) and
# this endpoint agree on the same eval_results/ location in both setups.
RESULTS_PATH = Path(__file__).resolve().parents[2] / "eval_results" / "latest.json"


@router.get("/eval")
def get_latest_eval_results(current_user: User = Depends(get_current_user)):
    if not RESULTS_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail="No eval results yet -- run `python eval/run_eval.py` first.",
        )
    with open(RESULTS_PATH, encoding="utf-8") as f:
        return json.load(f)
