from typing import Optional
from openai import OpenAI
from states.accommodation_constraints import AccommodationConstraint
from states.trip_state import TripState
import os

OPENAI_API_KEY = "<MY OPEN API KEY>"
os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY

client = OpenAI()

SYSTEM_PROMPT = """
You are an experienced travel assistant that extracts structured constraints from user feedbacks.
You should only extract constraints that are explicitly implied.
If nothing applies, return an empty object.
"""

def parse_feedback_with_llm(feedback: str) -> Optional[AccommodationConstraint]:
    """
        Parse feedback with LLM into a constraint object
    """
    if not feedback:
        return None

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": feedback}
        ],
        tools=[{
            "type": "function",
            "function": {
                "name": "extract_constraints",
                "parameters": AccommodationConstraint.model_json_schema()
            }
        }],
        tool_choice={"type": "function", "function": {"name": "extract_constraints"}}
    )

    # print(f"response: {response}")
    # print(f"response.choices: {response.choices}")
    # print(f"response.choices[0]: {response.choices[0]}")
    # print(f"response.choices[0].message: {response.choices[0].message}")
    # print(f"response.choices[0].message.tool_calls: {response.choices[0].message.tool_calls}")
    tool_call = response.choices[0].message.tool_calls[0]
    print(f"tool_call: {tool_call}")
    #print(f"tool_call.function: {tool_call.function}")
    args = tool_call.function.arguments
    print(f"args: {args}")

    return AccommodationConstraint.model_validate_json(args)