import json
from typing import List

from bioview_common import SUPPORTED_RESPONSES, ValidationError, validate_message_format


def _build_supported_values():
    """Return a set of possible string forms for SUPPORTED_RESPONSES for tolerant matching."""
    vals = set()
    try:
        for r in SUPPORTED_RESPONSES:
            if hasattr(r, "name"):
                vals.add(r.name)
                vals.add(r.name.lower())
            if hasattr(r, "value") and isinstance(r.value, str):
                vals.add(r.value)
                vals.add(r.value.lower())
            vals.add(str(r))
            vals.add(str(r).lower())
    except Exception:
        v = SUPPORTED_RESPONSES
        vals.add(v)
        if isinstance(v, str):
            vals.add(v.lower())
    return vals


def parse_and_validate_response(data: str, response_type=None) -> List:
    if not data:
        raise ValidationError("Server returned no response")

    try:
        message = json.loads(data)
    except json.JSONDecodeError as e:
        raise ValidationError("Invalid JSON format") from e

    # Validate message structure
    required_fields = ["type", "payload"]
    if not validate_message_format(message, required_fields):
        raise ValidationError("Message missing required fields")

    # Validate response type
    received_type = message.get("type")
    supported_values = _build_supported_values()
    recv_norm = (
        received_type.lower()
        if isinstance(received_type, str)
        else str(received_type).lower()
    )
    if recv_norm not in {s.lower() for s in supported_values}:
        raise ValidationError(f"Unsupported response: {received_type}")

    # If a specific expected response_type was provided, verify it matches
    if response_type and response_type != received_type:
        raise ValidationError(
            f"Incorrect response type: {received_type}. Expected: {response_type}"
        )

    # Validate payload is a dictionary
    received_payload = message.get("payload")
    if not isinstance(received_payload, dict):
        raise ValidationError(
            f"payload must be a dict but got {type(received_payload)} instead"
        )

    return received_type, received_payload
