"""Team roster endpoint. The dashboard reads this to populate team-override pickers."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.auth import require_auth
from api.workflow.state_machine import ROSTER

router = APIRouter(prefix="/team", tags=["team"])


class Member(BaseModel):
    slug: str
    name: str
    role: str


@router.get("", response_model=list[Member], dependencies=[Depends(require_auth)])
def list_team() -> list[Member]:
    return [Member(**m) for m in ROSTER]
