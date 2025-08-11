import json
from typing import List

from bioview_common import SUPPORTED_RESPONSES, ValidationError, validate_message_format


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
    if response_type not in SUPPORTED_RESPONSES:
        raise ValidationError(f"Unsupported response: {received_type}")
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
