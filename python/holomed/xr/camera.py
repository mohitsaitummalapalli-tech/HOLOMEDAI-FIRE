# -*- coding: utf-8 -*-
"""Camera Viewport Management and Multi-View Systems for M06."""

from __future__ import annotations

from typing import Optional

from holomed.xr.exceptions import (
    XRCapacityError,
    XRValidationError,
)
from holomed.xr.models import (
    MAX_VIEWPORTS,
    CameraViewport,
)


class ViewportManager:
    """Manages active surgical viewports (3D, Axial, Sagittal, Coronal)."""

    def __init__(self) -> None:
        self._viewports: dict[str, CameraViewport] = {}

    @property
    def viewport_count(self) -> int:
        return len(self._viewports)

    @property
    def viewports(self) -> tuple[CameraViewport, ...]:
        """Return all viewports sorted deterministically by viewport_id ASC."""
        sorted_keys = sorted(self._viewports.keys())
        return tuple(self._viewports[k] for k in sorted_keys)

    def add_viewport(self, viewport: CameraViewport) -> None:
        """Register a camera viewport, enforcing MAX_VIEWPORTS bound."""
        if len(self._viewports) >= MAX_VIEWPORTS:
            raise XRCapacityError(f"Viewport capacity exceeded ({MAX_VIEWPORTS} viewports max)")

        if viewport.viewport_id in self._viewports:
            raise XRValidationError(f"Duplicate viewport_id {viewport.viewport_id!r}")

        self._viewports[viewport.viewport_id] = viewport

    def get_viewport(self, viewport_id: str) -> CameraViewport:
        """Retrieve viewport by ID or raise XRValidationError."""
        if viewport_id not in self._viewports:
            raise XRValidationError(f"Viewport {viewport_id!r} not found")
        return self._viewports[viewport_id]

    def remove_viewport(self, viewport_id: str) -> None:
        """Remove a viewport by ID."""
        self._viewports.pop(viewport_id, None)

    def clear(self) -> None:
        """Clear all registered viewports."""
        self._viewports.clear()
