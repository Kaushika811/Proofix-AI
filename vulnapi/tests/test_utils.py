import pickle

import pytest

from vulnapi.utils import deserialize_data, safe_load_json


def test_unsafe_deser_rejected():
    """Bug 4 demo: pickle on untrusted input should be rejected."""
    malicious = pickle.dumps({"cmd": "whoami"})
    with pytest.raises((ValueError, TypeError, Exception)):
        # Should not use pickle.loads on untrusted data
        result = deserialize_data(malicious)
        assert isinstance(result, dict)


def test_safe_json_load():
    data = b'{"key": "value"}'
    result = safe_load_json(data)
    assert result == {"key": "value"}
