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