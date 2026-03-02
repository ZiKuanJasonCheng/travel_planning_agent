from pydantic import BaseModel, Field
from typing import Dict, Any, Optional
from datetime import datetime, timezone


class DecisionTrace(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    node: str
    action: str
    reason: str
    inputs: Optional[Dict[str, Any]] = None
    outputs: Optional[Dict[str, Any]] = None



def log_trace(
    state,
    node: str,
    action: str,
    reason: str,
    inputs=None,
    outputs=None
):
    trace = DecisionTrace(
        node=node,
        action=action,
        reason=reason,
        inputs=inputs,
        outputs=outputs
    )
    if not state.get("traces"):
        state["traces"] = []
    state["traces"].append(trace)