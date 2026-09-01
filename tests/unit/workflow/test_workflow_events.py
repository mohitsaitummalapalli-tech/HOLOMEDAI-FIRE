# -*- coding: utf-8 -*-
"""Unit Tests for RecordingWorkflowEventSink."""

from __future__ import annotations

import pytest

from holomed.protocol.builders import create_event
from holomed.workflow.events import RecordingWorkflowEventSink
from holomed.workflow.exceptions import WorkflowCapacityError
from holomed.workflow.models import MAX_WORKFLOW_EVENT_LOG


def test_event_sink_capacity_boundary() -> None:
    """Verify sink accepts 1000 events and raises WorkflowCapacityError on 1001."""
    sink = RecordingWorkflowEventSink(capacity=MAX_WORKFLOW_EVENT_LOG)

    for i in range(MAX_WORKFLOW_EVENT_LOG):
        env = create_event("workflow.phase.entered", "workflow_service", payload={"idx": i})
        sink.record_event(env)

    assert sink.count == 1000
    assert sink.is_full is True

    overflow = create_event("workflow.phase.entered", "workflow_service", payload={"idx": 1001})
    with pytest.raises(WorkflowCapacityError):
        sink.record_event(overflow)

    assert sink.count == 1000
    assert isinstance(sink.export_events(), tuple)
