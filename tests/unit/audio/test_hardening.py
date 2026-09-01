# -*- coding: utf-8 -*-
"""Hostile architectural hardening tests for M02 Audio subsystem."""

import ast
from pathlib import Path

from holomed.audio.exceptions import (
    AudioCapacityError,
    AudioDeviceIdentityError,
    AudioEpochMismatchError,
    AudioError,
    AudioFrameValidationError,
    AudioLifecycleError,
    AudioPipelineError,
    AudioResourceIntegrityError,
    AudioSequenceError,
    AudioShutdownError,
    AudioValidationError,
)
from holomed.audio.service import AudioService
from holomed.common.exceptions import HoloMedError
from holomed.devices.exceptions import (
    DeviceCapacityError,
    DeviceError,
    DeviceLifecycleError,
    DeviceResourceIntegrityError,
    DeviceShutdownError,
    DeviceValidationError,
)
from holomed.devices.manager import DeviceManager
from holomed.devices.models import DeviceShutdownFailureRecord
from holomed.runtime.service import ServiceRegistration, compile_topology
from tests.unit.audio.conftest import make_test_audio_chunk

PROHIBITED_MODULES: frozenset[str] = frozenset(
    {
        "socket",
        "urllib.request",
        "http.client",
        "requests",
        "aiohttp",
        "asyncio",
        "threading",
        "multiprocessing",
        "subprocess",
        "cv2",
        "mediapipe",
        "pyaudio",
        "sounddevice",
        "serial",
    }
)


def test_ast_prohibited_imports() -> None:
    """Scan all M02 source files for prohibited async, network, threading, or native audio libraries."""
    audio_pkg = Path("python/holomed/audio")
    violations: list[tuple[str, str]] = []

    for file_path in audio_pkg.glob("*.py"):
        tree = ast.parse(file_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if any(alias.name == p or alias.name.startswith(p + ".") for p in PROHIBITED_MODULES):
                        violations.append((file_path.name, alias.name))
            elif isinstance(node, ast.ImportFrom) and node.module:
                if any(node.module == p or node.module.startswith(p + ".") for p in PROHIBITED_MODULES):
                    violations.append((file_path.name, node.module))

    assert len(violations) == 0, f"Prohibited imports detected in M02: {violations}"


def test_topological_compilation_minimal_footprint(test_runtime_context) -> None:
    """Verify AudioService compiles in runtime topology depending only on device_manager."""
    dm = DeviceManager()
    audio_svc = AudioService(device_manager=dm)

    assert audio_svc.dependencies == ("device_manager",)

    regs = {
        "device.mgr": ServiceRegistration("device.mgr", lambda: None, ()),
        "audio.service": ServiceRegistration(
            "audio.service",
            lambda: None,
            ("device.mgr",),  # Minimal footprint
        ),
    }

    order = compile_topology(regs)
    assert order.index("device.mgr") < order.index("audio.service")


def test_exception_hierarchy_multiple_inheritance_d193() -> None:
    """Verify multiple-inheritance exception structure conforms to M00 base handlers (D193)."""
    # AudioShutdownError
    rec = DeviceShutdownFailureRecord(
        device_id="res_1",
        error_type="RuntimeError",
        error_message="msg",
        execution_index=0,
        unreleased_resources=("res_1",),
    )
    shut_err = AudioShutdownError("Shutdown failed", [rec])
    assert isinstance(shut_err, AudioError)
    assert isinstance(shut_err, DeviceShutdownError)
    assert isinstance(shut_err, DeviceError)
    assert isinstance(shut_err, HoloMedError)

    # AudioLifecycleError
    life_err = AudioLifecycleError("Bad state")
    assert isinstance(life_err, AudioError)
    assert isinstance(life_err, DeviceLifecycleError)

    # AudioCapacityError
    cap_err = AudioCapacityError("Full")
    assert isinstance(cap_err, AudioError)
    assert isinstance(cap_err, DeviceCapacityError)

    # AudioValidationError
    val_err = AudioValidationError("Bad field")
    assert isinstance(val_err, AudioError)
    assert isinstance(val_err, DeviceValidationError)

    # AudioFrameValidationError
    frame_val_err = AudioFrameValidationError("Corrupt frame")
    assert isinstance(frame_val_err, AudioValidationError)
    assert isinstance(frame_val_err, AudioError)

    # AudioDeviceIdentityError
    dev_id_err = AudioDeviceIdentityError("Unknown device")
    assert isinstance(dev_id_err, AudioError)

    # AudioEpochMismatchError
    epoch_err = AudioEpochMismatchError("Epoch mismatch")
    assert isinstance(epoch_err, AudioError)

    # AudioPipelineError
    pipe_err = AudioPipelineError("Pipeline fault")
    assert isinstance(pipe_err, AudioError)

    # AudioResourceIntegrityError
    res_err = AudioResourceIntegrityError("Corrupted handle")
    assert isinstance(res_err, AudioError)
    assert isinstance(res_err, DeviceResourceIntegrityError)

    # AudioSequenceError
    seq_err = AudioSequenceError("Sequence regression")
    assert isinstance(seq_err, AudioError)


def test_end_to_end_deterministic_reproducibility(
    test_runtime_context, device_registry_with_microphone
) -> None:
    """Verify identical synthetic audio input produces bit-identical processing results."""
    _, dm = device_registry_with_microphone

    svc1 = AudioService(device_manager=dm)
    svc1.initialize(test_runtime_context)
    svc1.start()

    svc2 = AudioService(device_manager=dm)
    svc2.initialize(test_runtime_context)
    svc2.start()

    chunk1, pcm1 = make_test_audio_chunk(sequence_number=1, freq_hz=440.0)
    chunk2, pcm2 = make_test_audio_chunk(sequence_number=1, freq_hz=440.0)

    res1 = svc1.ingest_chunk(chunk1, pcm1)
    res2 = svc2.ingest_chunk(chunk2, pcm2)

    assert res1.quality == res2.quality
    assert res1.features.rms_energy == res2.features.rms_energy
    assert res1.features.spectral_centroid_hz == res2.features.spectral_centroid_hz
    assert res1.features.peak_frequency_hz == res2.features.peak_frequency_hz
