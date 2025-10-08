import json
from typing import List

from bioview_common import (
    MAX_BUFFER_SIZE,
    SUPPORTED_COMMANDS,
    SUPPORTED_RESPONSES,
    Command,
    ValidationError,
    log_print,
)


def send_command(
    sock, command, params=None, logger=None, buffer_size: int = MAX_BUFFER_SIZE
):
    """
    Sends a command and returns a response.
    """
    if not isinstance(command, Command) or command.name not in SUPPORTED_COMMANDS:
        log_print(logger, "error", f"Invalid command: {command}")
        return None

    try:
        command_dict = {"type": command.name, "payload": params or {}}
        command_json = json.dumps(command_dict).encode("utf-8")
        sock.send(command_json)
    except Exception as e:
        msg = f"Error occurred while sending command: {e}"
        log_print(logger, "error", msg)
        return None

    # If we are here, a valid command has been sent. Wait for response
    response = sock.recv(buffer_size)

    return response


def parse_and_validate_response(data: str) -> List:
    if not data:
        raise ValidationError("Server returned no response")

    try:
        message = json.loads(data.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise ValidationError("Invalid JSON format") from e

    # Validate response type
    resp_type = message.get("type", None)
    if not resp_type or resp_type not in SUPPORTED_RESPONSES:
        raise ValidationError(f"Invalid response: {resp_type}")

    # Validate payload is a dictionary
    resp_payload = message.get("payload")
    if not isinstance(resp_payload, dict):
        raise ValidationError(
            f"payload must be a dict but got {type(resp_payload)} instead"
        )

    return resp_type, resp_payload
