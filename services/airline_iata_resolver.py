"""
Airline IATA Code Resolver
Converts airline names (e.g. "Cathay Pacific") to IATA codes (e.g. "CX") using an LLM.
"""
import json
from typing import List, Optional
from openai import OpenAI
import os

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

SYSTEM_PROMPT = """
You are an aviation expert. Convert airline names to their official 2-letter IATA carrier codes.
Return only the codes that you are confident about. If an airline name is ambiguous or unknown, omit it.
"""

_SCHEMA = {
    "type": "object",
    "properties": {
        "iata_codes": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of 2-letter IATA carrier codes corresponding to the input airline names, in the same order."
        }
    },
    "required": ["iata_codes"]
}


def resolve_airline_iata_codes(airline_names: List[str]) -> List[str]:
    """
    Convert a list of airline names to IATA codes.
    Names that are already valid 2-letter codes are passed through unchanged.
    """
    if not airline_names:
        return []

    # Pass through anything that already looks like a 2-letter IATA code
    already_codes = [name for name in airline_names if len(name) == 2 and name.isalpha() and name.isupper()]
    needs_resolution = [name for name in airline_names if name not in already_codes]

    if not needs_resolution:
        return already_codes

    user_message = "Convert these airline names to IATA codes: " + ", ".join(needs_resolution)

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message}
            ],
            tools=[{
                "type": "function",
                "function": {
                    "name": "return_iata_codes",
                    "parameters": _SCHEMA
                }
            }],
            tool_choice={"type": "function", "function": {"name": "return_iata_codes"}},
            timeout=60
        )

        tool_call = response.choices[0].message.tool_calls[0]
        result = json.loads(tool_call.function.arguments)
        resolved = result.get("iata_codes", [])
        return already_codes + resolved

    except Exception as e:
        print(f"airline_iata_resolver: error resolving {needs_resolution}: {e}")
        # Fall back to the original names so filtering degrades gracefully
        return already_codes + needs_resolution
