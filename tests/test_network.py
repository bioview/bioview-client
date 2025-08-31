import json

from bioview_common import SUPPORTED_RESPONSES, ValidationError

from bioview_client.utils.network import parse_and_validate_response


def test_parse_valid_response():
    msg = {
        "type": list(SUPPORTED_RESPONSES)[0],
        "payload": {"server_info": {"hostname": "host"}},
    }
    t, p = parse_and_validate_response(json.dumps(msg), response_type=msg["type"])
    assert t == msg["type"]
    assert isinstance(p, dict)


def test_parse_invalid_json():
    import pytest

    with pytest.raises(ValidationError):
        parse_and_validate_response("not-a-json")
