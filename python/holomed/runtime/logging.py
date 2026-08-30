"""Structured JSON logging with trace context correlation and atomic secret redaction."""

from datetime import datetime, timezone
import json
import logging
from typing import Any, Mapping, Optional, Sequence, Union

from holomed.configuration.models import LogLevel, SecretString
from holomed.runtime.context import TraceContext


def serialize_log_record(record_dict: dict[str, Any]) -> str:
    """Serialize canonical log record to deterministic compact JSON without trailing newline."""
    return json.dumps(record_dict, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class SecretFilter(logging.Filter):
    """Logging filter that atomically redacts exact occurrences of registered secret strings."""

    def __init__(self, secrets: Sequence[Union[SecretString, str]] = ()) -> None:
        super().__init__()
        self._raw_secrets: tuple[str, ...] = ()
        self.set_secrets(secrets)

    def set_secrets(self, secrets: Sequence[Union[SecretString, str]]) -> None:
        """Atomically replace the active secret set."""
        raw_list: list[str] = []
        for s in secrets:
            raw = s.get_secret_value() if isinstance(s, SecretString) else s
            if isinstance(raw, str) and raw:
                raw_list.append(raw)
        self._raw_secrets = tuple(raw_list)

    def redact(self, text: str) -> str:
        """Replace all registered secret occurrences in text with '<redacted>'."""
        if not isinstance(text, str):
            return text
        result = text
        for secret in self._raw_secrets:
            if secret:
                result = result.replace(secret, "<redacted>")
        return result

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self.redact(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self.redact(v) if isinstance(v, str) else v for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(self.redact(v) if isinstance(v, str) else v for v in record.args)
        return True


class StructuredLogger:
    """Correlation-aware structured logger wrapping Python's standard logging facility."""

    def __init__(
        self,
        name: str,
        secret_filter: Optional[SecretFilter] = None,
        trace_context: Optional[TraceContext] = None,
    ) -> None:
        self.name = name
        self._logger = logging.getLogger(name)
        self._secret_filter = secret_filter or SecretFilter()
        self._trace_context = trace_context

    def with_trace(self, trace: Optional[TraceContext]) -> "StructuredLogger":
        """Produce a new StructuredLogger instance with updated trace context."""
        return StructuredLogger(
            name=self.name,
            secret_filter=self._secret_filter,
            trace_context=trace,
        )

    def set_secrets(self, secrets: Sequence[Union[SecretString, str]]) -> None:
        """Update active secrets on the shared filter."""
        self._secret_filter.set_secrets(secrets)

    def _log(
        self,
        level: LogLevel,
        message: str,
        event: Optional[str] = None,
        runtime_state: Optional[str] = None,
        extra: Optional[Mapping[str, Any]] = None,
        exc_info: Optional[Any] = None,
    ) -> None:
        if not self._logger.isEnabledFor(getattr(logging, level.name)):
            return

        redacted_message = self._secret_filter.redact(message)
        record_dict: dict[str, Any] = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "level": level.name,
            "component": self.name,
            "event": event or "log_event",
            "message": redacted_message,
            "runtime_state": runtime_state,
            "correlation_id": str(self._trace_context.correlation_id) if self._trace_context and self._trace_context.correlation_id else None,
            "causation_id": str(self._trace_context.causation_id) if self._trace_context and self._trace_context.causation_id else None,
        }
        if extra:
            for k, v in extra.items():
                if isinstance(v, str):
                    record_dict[k] = self._secret_filter.redact(v)
                else:
                    record_dict[k] = v

        serialized = serialize_log_record(record_dict)
        self._logger.log(getattr(logging, level.name), serialized, exc_info=exc_info)

    def debug(self, message: str, **kwargs: Any) -> None:
        self._log(LogLevel.DEBUG, message, **kwargs)

    def info(self, message: str, **kwargs: Any) -> None:
        self._log(LogLevel.INFO, message, **kwargs)

    def warning(self, message: str, **kwargs: Any) -> None:
        self._log(LogLevel.WARNING, message, **kwargs)

    def error(self, message: str, **kwargs: Any) -> None:
        self._log(LogLevel.ERROR, message, **kwargs)

    def critical(self, message: str, **kwargs: Any) -> None:
        self._log(LogLevel.CRITICAL, message, **kwargs)


def configure_logging(level: LogLevel = LogLevel.INFO) -> SecretFilter:
    """Configure root logger with standardized formatting and return global SecretFilter."""
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.name))

    secret_filter = SecretFilter()
    for handler in root_logger.handlers:
        handler.addFilter(secret_filter)

    return secret_filter
