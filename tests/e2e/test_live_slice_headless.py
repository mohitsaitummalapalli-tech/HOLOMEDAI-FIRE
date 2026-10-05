# -*- coding: utf-8 -*-
"""Headless End-to-End Test for the Production Live-Slice Pipeline."""

import pytest
import uuid
import tempfile
from pathlib import Path

from holomed.runner import ProductionLiveSliceRunner


def test_production_live_slice_headless() -> None:
    """Verify the production runner starts, runs cycles, and shuts down safely."""
    
    with tempfile.TemporaryDirectory() as tmp_dir:
        storage_root = Path(tmp_dir)
        
        runner = ProductionLiveSliceRunner(storage_root=storage_root)
        
        # 1. Start the runner (spins up services and unity device)
        runner.start()
        
        session_id = str(uuid.uuid4())
        
        try:
            # 2. Run Headless Cycles (runs execute_cycle and records it)
            # Should succeed if pipeline flows and Unity Virtual Device executes/responds successfully
            success = runner.run_headless_cycles(session_id=session_id, count=5)
            
            # Assertions
            assert success is True, "Expected all execution cycles and persistence verifications to pass"
            
        finally:
            # 3. Shutdown gracefully
            runner.stop()
