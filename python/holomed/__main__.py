# -*- coding: utf-8 -*-
"""Production Entry Point for HoloMed AI (M49)."""

import argparse
import logging
import sys
import tempfile
import uuid
from pathlib import Path

from holomed.runner import ProductionLiveSliceRunner

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("holomed.main")


def main() -> int:
    """Run the HoloMed AI pipeline."""
    parser = argparse.ArgumentParser(description="HoloMed AI Production Runner")
    parser.add_argument("--cycles", type=int, default=1, help="Number of simulation cycles to run")
    parser.add_argument("--headless", action="store_true", default=True, help="Run in headless simulation mode")
    
    args = parser.parse_args()
    
    with tempfile.TemporaryDirectory() as tmp_dir:
        storage_root = Path(tmp_dir)
        logger.info(f"Starting HoloMed AI Runner in {storage_root}")
        
        runner = ProductionLiveSliceRunner(storage_root=storage_root)
        
        try:
            logger.info("Initializing services...")
            runner.start()
            
            session_id = str(uuid.uuid4())
            logger.info(f"Starting session {session_id}")
            
            # For this step, run the headless cycles
            if args.headless:
                logger.info(f"Running {args.cycles} headless cycles...")
                success = runner.run_headless_cycles(session_id=session_id, count=args.cycles)
                if not success:
                    logger.error("Headless cycles encountered errors.")
                    return 1
            else:
                logger.error("Interactive mode not yet implemented for Phase 3.2-I")
                return 1
                
            logger.info("Cycles completed successfully.")
            
        except Exception as e:
            logger.exception("Runner encountered a fatal error")
            return 1
        finally:
            logger.info("Shutting down runner...")
            runner.stop()
            
    return 0


if __name__ == "__main__":
    sys.exit(main())
