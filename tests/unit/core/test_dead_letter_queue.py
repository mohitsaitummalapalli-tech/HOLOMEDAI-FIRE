"""Tests for M00.4 Dead-Letter Queue."""

import warnings
import pytest
from holomed.core.dead_letter import DeadLetterQueue
from holomed.core.exceptions import DeadLetterCapacityError
from holomed.core.models import (
    DeadLetterOverflowPolicy,
    DeadLetterReason,
    DeadLetterRecord,
)
from holomed.protocol.builders import create_command


class TestDeadLetterQueue:
    """Test bounded FIFO ring, DROP_OLDEST, REJECT_NEW, and clear semantics."""

    def test_drop_oldest_capacity_boundary_and_eviction_counts(self) -> None:
        """Test exact 1000th, 1001st, and 1500th insertion scenario under DROP_OLDEST."""
        dlq = DeadLetterQueue(capacity=1000, overflow_policy=DeadLetterOverflowPolicy.DROP_OLDEST)

        # 1. Fill exactly to capacity (1000 insertions)
        with warnings.catch_warnings(record=True) as recorded_warnings:
            warnings.simplefilter("always")
            for i in range(1000):
                dlq.record(envelope=None, reason=DeadLetterReason.NO_HANDLER, diagnostic=f"msg_{i}")

        assert dlq.count == 1000
        assert dlq.evicted_count == 0
        assert len(recorded_warnings) == 0
        # 1000th insertion verification: first is msg_0, last is msg_999
        assert dlq.records[0].diagnostic == "msg_0"
        assert dlq.records[-1].diagnostic == "msg_999"

        # 2. 1001st insertion: evicts oldest (msg_0), records overflow warning
        with warnings.catch_warnings(record=True) as recorded_warnings:
            warnings.simplefilter("always")
            dlq.record(envelope=None, reason=DeadLetterReason.NO_HANDLER, diagnostic="msg_1000")

        assert dlq.count == 1000
        assert dlq.evicted_count == 1
        assert len(recorded_warnings) == 1
        assert "Dead-letter queue overflow" in str(recorded_warnings[0].message)
        # FIFO check: msg_0 evicted, oldest is now msg_1, newest is msg_1000
        assert dlq.records[0].diagnostic == "msg_1"
        assert dlq.records[-1].diagnostic == "msg_1000"

        # 3. Continue up to 1500 total insertions (another 499 insertions)
        for i in range(1001, 1500):
            dlq.record(envelope=None, reason=DeadLetterReason.NO_HANDLER, diagnostic=f"msg_{i}")

        assert dlq.count == 1000
        assert dlq.evicted_count == 500
        assert dlq.records[0].diagnostic == "msg_500"
        assert dlq.records[-1].diagnostic == "msg_1499"

    def test_reject_new_policy_prohibits_insertion_and_preserves_state(self) -> None:
        """Authoritative contract for REJECT_NEW:

        - when capacity is full, DO NOT evict
        - DO NOT silently discard
        - DO NOT overwrite
        - DO NOT mutate queue contents
        - DO NOT increment evicted_count
        - MUST raise DeadLetterCapacityError
        """
        dlq = DeadLetterQueue(capacity=5, overflow_policy=DeadLetterOverflowPolicy.REJECT_NEW)
        for i in range(5):
            dlq.record(envelope=None, reason=DeadLetterReason.CORRUPTED_ENVELOPE, diagnostic=f"msg_{i}")

        assert dlq.count == 5
        assert dlq.evicted_count == 0
        snapshot_before = dlq.records

        # 6th insertion must raise DeadLetterCapacityError
        with pytest.raises(DeadLetterCapacityError) as exc_info:
            dlq.record(envelope=None, reason=DeadLetterReason.NO_HANDLER, diagnostic="msg_overflow")

        assert "REJECT_NEW policy prohibits insertion" in str(exc_info.value)
        # Queue contents untouched, no eviction, no mutation
        assert dlq.count == 5
        assert dlq.evicted_count == 0
        assert dlq.records == snapshot_before

    def test_clear_resets_records_and_evicted_count(self) -> None:
        dlq = DeadLetterQueue(capacity=10, overflow_policy=DeadLetterOverflowPolicy.DROP_OLDEST)
        with warnings.catch_warnings(record=True) as recorded:
            warnings.simplefilter("always")
            for i in range(15):
                dlq.record(envelope=None, reason=DeadLetterReason.NO_HANDLER, diagnostic=f"msg_{i}")
            assert len(recorded) == 1

        assert dlq.count == 10
        assert dlq.evicted_count == 5
        assert dlq.overflow_warned is True

        dlq.clear()
        assert dlq.count == 0
        assert dlq.evicted_count == 0
        assert dlq.overflow_warned is False
        assert dlq.records == ()

    def test_records_snapshot_immutability(self) -> None:
        dlq = DeadLetterQueue(capacity=10)
        dlq.record(envelope=None, reason=DeadLetterReason.HANDLER_EXCEPTION, diagnostic="test_err")
        recs = dlq.records
        assert isinstance(recs, tuple)
        assert len(recs) == 1
        assert isinstance(recs[0], DeadLetterRecord)

    def test_all_dead_letter_reasons_recordable(self) -> None:
        dlq = DeadLetterQueue(capacity=20)
        env = create_command("sys.ping", "client_svc")
        for reason in DeadLetterReason:
            rec = dlq.record(envelope=env, reason=reason, diagnostic=f"Reason test {reason.name}")
            assert rec.reason is reason
            assert rec.envelope is env
        assert dlq.count == len(DeadLetterReason)

    def test_overflow_warning_bounded_and_non_recursive(self) -> None:
        """Adversarial test proving overflow notification is bounded to 1 per episode and non-recursive."""
        dlq = DeadLetterQueue(capacity=10, overflow_policy=DeadLetterOverflowPolicy.DROP_OLDEST)
        assert dlq.overflow_warned is False

        with warnings.catch_warnings(record=True) as recorded:
            warnings.simplefilter("always")
            # Insert 5,000 items into capacity 10 (4,990 evictions)
            for i in range(5000):
                dlq.record(envelope=None, reason=DeadLetterReason.NO_HANDLER, diagnostic=f"burst_{i}")

            # Exactly 1 warning must be emitted, not 4,990
            assert len(recorded) == 1
            assert dlq.overflow_warned is True
            assert dlq.evicted_count == 4990
            assert "Dead-letter queue overflow" in str(recorded[0].message)

            # Clear resets overflow warning state
            dlq.clear()
            assert dlq.overflow_warned is False
            assert dlq.evicted_count == 0

            # Next overflow episode emits exactly 1 warning again
            for i in range(20):
                dlq.record(envelope=None, reason=DeadLetterReason.NO_HANDLER, diagnostic=f"episode2_{i}")
            assert len(recorded) == 2  # 1 from first episode + 1 from second episode
            assert dlq.overflow_warned is True
            assert dlq.evicted_count == 10
