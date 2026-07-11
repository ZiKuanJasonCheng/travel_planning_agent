from typing import Dict, TypedDict, List, Optional, Literal


def merge_constraints(old_constraints: Dict | None, new_constraints: Dict | None) -> Dict | None:
    """
    Merge two constraints dictionaries.
    Rules:
    - If old_constraints is empty or None, we use new_constraints directly
    - If new_constraints is empty or None, we remain old_constraints unchanged
    - If new_constraints is not empty nor None, we set the new values
    """
    # If old_constraints is empty or None, we use new_constraints directly
    if not old_constraints:
        return new_constraints or {}
    
    # If new_constraints is empty or None, we remain old_constraints unchanged
    if not new_constraints:
        return old_constraints
    
    
    # Copy old_constraints so it won't affect the downstream iterations
    merged_constraints = old_constraints.copy()

    for key, new_value in new_constraints.items():
        if not key:
            continue
    
        old_value = merged_constraints.get(key)

        # If new_value and old_value are dictionaries, we merge constraints recursively
        if isinstance(old_value, dict) and isinstance(new_value, dict):
            merged_constraints[key] = merge_constraints(old_value, new_value)
        else:
            merged_constraints[key] = new_value

    return merged_constraints


def resolve_category_constraints(state: dict, category: str) -> tuple[dict, bool]:
    """Merge this round's new_constraints[category] into constraints[category].

    Returns (merged, unchanged). `unchanged` is only True when this is NOT a
    brand-new trip (state["feedback"] is not None) and the merge produced no
    difference from the existing value — i.e. it's safe for the caller to
    consider skipping replanning, pending its own rerun_planning/error checks.
    """
    existing = state.get("constraints", {}).get(category) or {}
    new = state.get("new_constraints", {}).get(category) or {}
    merged = merge_constraints(existing, new)
    unchanged = state.get("feedback") is not None and merged == existing
    return merged, unchanged