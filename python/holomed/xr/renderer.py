# -*- coding: utf-8 -*-
"""External Renderer Adapter and Presentation Protocol Transport (D228, D235)."""

from __future__ import annotations

from typing import Any, Mapping

from holomed.protocol.builders import create_event
from holomed.protocol.models import MessageEnvelope
from holomed.xr.exceptions import XRValidationError
from holomed.xr.models import MAX_XR_PAYLOAD_BYTES


class IExternalRendererAdapter:
    """Immutable protocol boundary contract for external display runtimes."""

    def export_presentation_frame(
        self,
        scene_description: Mapping[str, Any],
        target_client: str = "unity_client",
    ) -> MessageEnvelope:
        """Export standardized presentation frame envelope to external client."""
        raise NotImplementedError


class DefaultExternalRendererAdapter(IExternalRendererAdapter):
    """Standard-library presentation transport bridge to external clients (e.g. Unity/WebXR)."""

    def export_presentation_frame(
        self,
        scene_description: Mapping[str, Any],
        target_client: str = "unity_client",
    ) -> MessageEnvelope:
        if not isinstance(scene_description, (dict, Mapping)):
            raise XRValidationError("scene_description must be a valid mapping")

        # Security check: ensure no executable shader code or script blocks are injected
        for key, val in scene_description.items():
            if isinstance(val, str) and ("<script" in val.lower() or "exec(" in val.lower()):
                raise XRValidationError(f"Executable script/code injection detected in key {key!r}")

        return create_event(
            message_name="xr.presentation.frame",
            source="xr_service",
            target=target_client,
            payload=dict(scene_description),
        )
