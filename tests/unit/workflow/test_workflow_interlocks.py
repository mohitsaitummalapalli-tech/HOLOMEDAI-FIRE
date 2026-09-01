# -*- coding: utf-8 -*-
"""Unit Tests for SafetyInterlockEngine and Severity Precedence."""

from __future__ import annotations

from holomed.workflow.interlocks import SafetyInterlockEngine
from holomed.workflow.models import InterlockSeverity, SafetyInterlock


def test_interlock_severity_precedence() -> None:
    """Verify tripped interlocks are ordered by CRITICAL > BLOCKING > WARNING > INFO."""
    engine = SafetyInterlockEngine()
    it_info = SafetyInterlock("i1", InterlockSeverity.INFO, "info", False, "r1", "s", 1, "sess")
    it_warn = SafetyInterlock("i2", InterlockSeverity.WARNING, "warn", False, "r2", "s", 1, "sess")
    it_crit = SafetyInterlock("i3", InterlockSeverity.CRITICAL, "crit", False, "r3", "s", 1, "sess")
    it_block = SafetyInterlock("i4", InterlockSeverity.BLOCKING, "block", False, "r4", "s", 1, "sess")

    engine.register_interlock(it_info)
    engine.register_interlock(it_warn)
    engine.register_interlock(it_crit)
    engine.register_interlock(it_block)

    tripped = engine.get_tripped_interlocks()
    assert len(tripped) == 4
    assert [t.severity for t in tripped] == [
        InterlockSeverity.CRITICAL,
        InterlockSeverity.BLOCKING,
        InterlockSeverity.WARNING,
        InterlockSeverity.INFO,
    ]


def test_blocking_and_critical_detection() -> None:
    """Verify engine accurately flags presence of blocking and critical interlocks."""
    engine = SafetyInterlockEngine()
    assert engine.has_blocking_interlock() is False
    assert engine.has_critical_interlock() is False

    # Register passing interlock
    engine.register_interlock(
        SafetyInterlock("i_ok", InterlockSeverity.BLOCKING, "ok", True, "Clear", "s", 1, "sess")
    )
    assert engine.has_blocking_interlock() is False

    # Register tripped BLOCKING interlock
    engine.register_interlock(
        SafetyInterlock("i_blk", InterlockSeverity.BLOCKING, "blk", False, "Blocked", "s", 1, "sess")
    )
    assert engine.has_blocking_interlock() is True
    assert engine.has_critical_interlock() is False

    # Register tripped CRITICAL interlock
    engine.register_interlock(
        SafetyInterlock("i_crt", InterlockSeverity.CRITICAL, "crt", False, "Critical", "s", 1, "sess")
    )
    assert engine.has_blocking_interlock() is True
    assert engine.has_critical_interlock() is True
