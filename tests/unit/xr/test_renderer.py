# -*- coding: utf-8 -*-
"""Unit tests for External Renderer Adapter Transport Boundary (D228, D235)."""

from __future__ import annotations

import pytest

from holomed.xr.exceptions import XRValidationError
from holomed.xr.renderer import DefaultExternalRendererAdapter


def test_external_renderer_adapter_export_and_security() -> None:
    """Verify DefaultExternalRendererAdapter packages envelopes and rejects script code (D235)."""
    adapter = DefaultExternalRendererAdapter()

    # Valid export
    payload = {"nodes_count": 5, "is_simulated": True}
    env = adapter.export_presentation_frame(payload, target_client="unity_client")
    assert env.message_name == "xr.presentation.frame"
    assert env.target == "unity_client"
    assert env.payload["nodes_count"] == 5

    # Script injection in payload
    with pytest.raises(XRValidationError):
        adapter.export_presentation_frame({"shader": "<script>malicious()</script>"})
