import json 
from typing import List, Dict, An

from bioview_common import ValidationError, SUPPORTED_RESPONSES

# Validation for received responses to ensure integrity
def validate_message_format(data: Dict[str, Any], expected_fields: list) -> bool:
    """Validate that message contains required fields and proper format"""
    if not isinstance(data, dict):
        return False
    
    for field in expected_fields:
        if field not in data:
            return False
    
    return True

def parse_and_validate_response(data: str, response_type = None) -> List:  
    if not data: 
        raise ValidationError("Server returned no response")

    try:
        message = json.loads(data)
    except json.JSONDecodeError:
        raise ValidationError("Invalid JSON format")
    
    # Validate message structure
    required_fields = ['type', 'payload']
    if not validate_message_format(message, required_fields):
        raise ValidationError("Message missing required fields")
    
    # Validate response type
    received_type = message.get('type')
    if response_type not in SUPPORTED_RESPONSES:
        raise ValidationError(f"Unsupported response: {received_type}")
    if response_type: 
        if response_type != received_type:
            raise ValidationError(f"Incorrect response type received: {received_type}. Expected: {response_type}")

    # Validate payload is a dictionary
    received_payload = message.get('payload') 
    if not isinstance(received_payload, dict):
        raise ValidationError(f"payload must be a dict but got {type(received_payload)} instead")
    
    return received_type, received_payload