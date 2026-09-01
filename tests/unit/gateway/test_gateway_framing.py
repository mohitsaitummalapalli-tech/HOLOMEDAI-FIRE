# -*- coding: utf-8 -*-
"""Unit Tests for Length-Prefixed Wire Framing and Allocation Protection."""

from __future__ import annotations

import struct
import pytest

from holomed.gateway.exceptions import GatewayFramingError
from holomed.gateway.framing import FrameParser, encode_frame
from holomed.gateway.models import MAX_FRAME_BYTES


def test_framing_length_prefix_encode_decode() -> None:
    """Verify clean encoding and decoding of valid payloads."""
    payload = b'{"protocol_version":"1.0","message_name":"test"}'
    framed = encode_frame(payload)

    assert len(framed) == 4 + len(payload)
    declared_len = struct.unpack(">I", framed[:4])[0]
    assert declared_len == len(payload)

    parser = FrameParser()
    frames = parser.feed(framed)
    assert len(frames) == 1
    assert frames[0] == payload


def test_framing_rejects_payload_exceeding_1mib_before_allocation() -> None:
    """Verify parser rejects frame header declaring > 1 MiB before buffer allocation."""
    # Construct malicious 4-byte header declaring 10 MiB
    fake_len = 10 * 1024 * 1024
    malicious_header = struct.pack(">I", fake_len)

    parser = FrameParser()
    with pytest.raises(GatewayFramingError) as exc_info:
        parser.feed(malicious_header)

    assert "exceeds MAX_FRAME_BYTES" in str(exc_info.value)
    # Ensure parser did not allocate 10 MiB buffer
    assert parser.buffered_bytes == 0


def test_framing_rejects_zero_length_frame() -> None:
    """Verify 0-length frames are rejected."""
    zero_frame = struct.pack(">I", 0)
    parser = FrameParser()
    with pytest.raises(GatewayFramingError):
        parser.feed(zero_frame)


def test_framing_handles_partial_chunks_and_reassembly() -> None:
    """Verify parser correctly buffers and reassembles fragmented chunks."""
    payload = b"Hello, HoloMed Clinical Gateway Network Framing!"
    framed = encode_frame(payload)

    parser = FrameParser()
    # Feed chunk 1: only first 10 bytes
    frames1 = parser.feed(framed[:10])
    assert len(frames1) == 0
    assert parser.buffered_bytes == 10

    # Feed chunk 2: remaining bytes
    frames2 = parser.feed(framed[10:])
    assert len(frames2) == 1
    assert frames2[0] == payload
    assert parser.buffered_bytes == 0


def test_framing_multiple_frames_in_single_chunk() -> None:
    """Verify multiple frames arriving in a single stream read are all parsed."""
    f1 = encode_frame(b"Frame One")
    f2 = encode_frame(b"Frame Two")
    f3 = encode_frame(b"Frame Three")

    combined = f1 + f2 + f3
    parser = FrameParser()
    frames = parser.feed(combined)
    assert len(frames) == 3
    assert frames == [b"Frame One", b"Frame Two", b"Frame Three"]
