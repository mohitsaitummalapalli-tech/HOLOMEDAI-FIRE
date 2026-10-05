import pytest
import pickle
from holomed.gateway._capability import _GatewayCapability, _INTERNAL_GATEWAY_KEY
from holomed.gateway.exceptions import GatewayAuthorizationError, GatewayValidationError

def test_capability_creation():
    cap = _GatewayCapability(_INTERNAL_GATEWAY_KEY, 123, "session1", "query")
    assert cap.service_instance_id == 123
    assert cap.session_id == "session1"
    assert cap.action == "query"
    assert cap.transaction_id is not None
    assert cap.is_active is True
    assert repr(cap).startswith("<_GatewayCapability active=True session='session1' action='query'")

def test_capability_prohibits_external_creation():
    with pytest.raises(GatewayAuthorizationError, match="Direct external construction of _GatewayCapability is strictly prohibited"):
        _GatewayCapability(object(), 123, "session1", "query")

def test_capability_validation():
    with pytest.raises(GatewayValidationError, match="service_instance_id must be an integer"):
        _GatewayCapability(_INTERNAL_GATEWAY_KEY, "123", "session1", "query")
        
    with pytest.raises(GatewayValidationError, match="session_id must be a non-empty string"):
        _GatewayCapability(_INTERNAL_GATEWAY_KEY, 123, "", "query")
        
    with pytest.raises(GatewayValidationError, match="action must be a non-empty string"):
        _GatewayCapability(_INTERNAL_GATEWAY_KEY, 123, "session1", "")

def test_capability_invalidation():
    cap = _GatewayCapability(_INTERNAL_GATEWAY_KEY, 123, "session1", "query")
    assert cap.is_active is True
    cap.invalidate()
    assert cap.is_active is False
    assert "active=False" in repr(cap)

def test_capability_not_serializable():
    cap = _GatewayCapability(_INTERNAL_GATEWAY_KEY, 123, "session1", "query")
    with pytest.raises(TypeError, match="_GatewayCapability cannot be serialized"):
        pickle.dumps(cap)
        
    with pytest.raises(TypeError, match="_GatewayCapability cannot be serialized"):
        cap.__getstate__()

    with pytest.raises(TypeError, match="_GatewayCapability cannot be serialized"):
        cap.__setstate__({})
