# -*- coding: utf-8 -*-
"""Focused smoke test for the real production entry point."""

import subprocess
import sys
import os

def test_production_entry_point_startup_and_shutdown() -> None:
    """Verify that the production entry point runs a full cycle and cleanly shuts down."""
    
    # Run the module directly as a subprocess to verify the entire __main__ stack
    cmd = [sys.executable, "-m", "holomed", "--cycles", "1"]
    
    # We set cwd to the project root
    project_root = os.path.join(os.path.dirname(__file__), "..", "..")
    
    result = subprocess.run(
        cmd,
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=10
    )
    
    # Must exit successfully
    assert result.returncode == 0, f"Entry point failed with stdout: {result.stdout}\nstderr: {result.stderr}"
    
    # Must log proper initialization and shutdown
    assert "Starting HoloMed AI Runner" in result.stderr or "Starting HoloMed AI Runner" in result.stdout
    assert "Initializing services..." in result.stderr or "Initializing services..." in result.stdout
    assert "Cycles completed successfully" in result.stderr or "Cycles completed successfully" in result.stdout
    assert "Shutting down runner" in result.stderr or "Shutting down runner" in result.stdout
