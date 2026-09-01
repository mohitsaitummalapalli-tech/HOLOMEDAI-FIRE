# -*- coding: utf-8 -*-
"""Holographic Presentation Description Assembly for M06."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from holomed.xr.camera import ViewportManager
from holomed.xr.exceptions import (
    XRCapacityError,
    XRValidationError,
)
from holomed.xr.models import (
    MAX_ANNOTATIONS,
    AnnotationItem,
    InteractionRay,
    PresentationDescription,
)
from holomed.xr.scene import SceneGraph


class PresentationEngine:
    """Assembles unified spatial presentation descriptions without raw mesh buffer dumps."""

    def __init__(self) -> None:
        self._annotations: dict[str, AnnotationItem] = {}
        self._active_ray: Optional[InteractionRay] = None

    @property
    def annotation_count(self) -> int:
        return len(self._annotations)

    def add_annotation(self, item: AnnotationItem) -> None:
        """Register spatial annotation, enforcing MAX_ANNOTATIONS (64)."""
        if len(self._annotations) >= MAX_ANNOTATIONS:
            raise XRCapacityError(f"Annotation capacity exceeded ({MAX_ANNOTATIONS} max)")
        if item.annotation_id in self._annotations:
            raise XRValidationError(f"Duplicate annotation_id {item.annotation_id!r}")
        self._annotations[item.annotation_id] = item

    def remove_annotation(self, annotation_id: str) -> None:
        """Remove annotation by ID."""
        self._annotations.pop(annotation_id, None)

    def set_interaction_ray(self, ray: Optional[InteractionRay]) -> None:
        """Set or clear active gesture interaction ray."""
        self._active_ray = ray

    def build_description(
        self,
        epoch_id: int,
        frame_sequence: int,
        scene_graph: SceneGraph,
        viewport_manager: ViewportManager,
    ) -> PresentationDescription:
        """Assemble immutable presentation frame snapshot."""
        now_utc = datetime.now(timezone.utc).isoformat()
        nodes_dict = {n.node_id: n for n in scene_graph.nodes}
        viewports_dict = {v.viewport_id: v for v in viewport_manager.viewports}
        annotations_dict = dict(self._annotations)

        return PresentationDescription(
            epoch_id=epoch_id,
            frame_sequence=frame_sequence,
            timestamp_utc=now_utc,
            nodes=nodes_dict,
            viewports=viewports_dict,
            annotations=annotations_dict,
            active_interaction_ray=self._active_ray,
            is_simulated=True,
        )

    def clear(self) -> None:
        """Clear all annotations and active ray."""
        self._annotations.clear()
        self._active_ray = None
