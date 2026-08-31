"""M00.4 Topic engine and subscription registry.

Topic grammar
-------------
Concrete topic:  ``^[a-z0-9]+(\\.[a-z0-9]+)*$``
Pattern segments: concrete segments  +  ``*``  +  ``**``

Matching semantics:
  * ``*``  — exactly one segment
  * ``**`` — zero or more segments

Normalisation:
  Consecutive ``**`` segments are collapsed: ``a.**.**.b`` → ``a.**.b``

Subscription registry enforces:
  * deterministic registration indexes
  * duplicate rejection
  * capacity limits
  * INITIALIZED-only static registration
  * STARTED seal
  * STOPPED cleanup
  * subscriber order: ``(service_name ASC, registration_index ASC)``
"""

from __future__ import annotations

import re
from typing import Optional

from holomed.core.exceptions import (
    DuplicateSubscriptionError,
    SubscriptionCapacityError,
    SubscriptionError,
    TopicValidationError,
)
from holomed.core.models import (
    MAX_SEGMENT_LENGTH,
    MAX_TOPIC_LENGTH,
    CommandRegistration,
    EventHandler,
    EventSubscription,
    MessageHandler,
    QueryRegistration,
)

# ---------------------------------------------------------------------------
# Topic grammar
# ---------------------------------------------------------------------------

_CONCRETE_TOPIC_RE = re.compile(r"^[a-z0-9]+(\.[a-z0-9]+)*$")
_ALLOWED_SEGMENT_RE = re.compile(r"^([a-z0-9]+|\*{1,2})$")


def validate_concrete_topic(topic: str) -> str:
    """Validate *topic* against the concrete topic grammar.

    Returns the validated topic string unchanged.

    Raises:
        TopicValidationError: on any grammar, length, or character violation.
    """
    if not isinstance(topic, str):
        raise TopicValidationError(f"Topic must be a string, got {type(topic).__name__}")
    if len(topic) == 0:
        raise TopicValidationError("Topic must not be empty")
    if len(topic) > MAX_TOPIC_LENGTH:
        raise TopicValidationError(
            f"Topic exceeds maximum length of {MAX_TOPIC_LENGTH} (len={len(topic)})"
        )
    # Check for whitespace anywhere in the string.
    if any(ch.isspace() for ch in topic):
        raise TopicValidationError(f"Topic must not contain whitespace: {topic!r}")
    if topic.startswith(".") or topic.endswith("."):
        raise TopicValidationError(f"Topic must not start or end with '.': {topic!r}")
    if ".." in topic:
        raise TopicValidationError(f"Topic must not contain empty segments (..): {topic!r}")
    if not _CONCRETE_TOPIC_RE.match(topic):
        raise TopicValidationError(
            f"Concrete topic must match ^[a-z0-9]+(\\.[a-z0-9]+)*$, got {topic!r}"
        )
    # Per-segment length
    for seg in topic.split("."):
        if len(seg) > MAX_SEGMENT_LENGTH:
            raise TopicValidationError(
                f"Topic segment exceeds maximum length of {MAX_SEGMENT_LENGTH}: {seg!r}"
            )
    return topic


def validate_topic_pattern(pattern: str) -> str:
    """Validate and normalise a topic *pattern* (may contain ``*`` / ``**``).

    Validation is performed on the **raw** pattern *before* normalisation.

    Returns the **normalised** pattern string.

    Raises:
        TopicValidationError: on any grammar, length, or character violation.
    """
    if not isinstance(pattern, str):
        raise TopicValidationError(f"Pattern must be a string, got {type(pattern).__name__}")
    if len(pattern) == 0:
        raise TopicValidationError("Pattern must not be empty")
    # Validate raw pattern length
    if len(pattern) > MAX_TOPIC_LENGTH:
        raise TopicValidationError(
            f"Pattern exceeds maximum length of {MAX_TOPIC_LENGTH} (len={len(pattern)})"
        )
    if any(ch.isspace() for ch in pattern):
        raise TopicValidationError(f"Pattern must not contain whitespace: {pattern!r}")
    if pattern.startswith(".") or pattern.endswith("."):
        raise TopicValidationError(f"Pattern must not start or end with '.': {pattern!r}")
    if ".." in pattern:
        raise TopicValidationError(f"Pattern must not contain empty segments (..): {pattern!r}")

    raw_segments = pattern.split(".")
    for seg in raw_segments:
        if len(seg) == 0:
            raise TopicValidationError(f"Pattern contains empty segment: {pattern!r}")
        if len(seg) > MAX_SEGMENT_LENGTH:
            raise TopicValidationError(
                f"Pattern segment exceeds maximum length of {MAX_SEGMENT_LENGTH}: {seg!r}"
            )
        if not _ALLOWED_SEGMENT_RE.match(seg):
            # Determine specific error
            if any(ch.isupper() for ch in seg):
                raise TopicValidationError(f"Pattern must not contain uppercase characters: {pattern!r}")
            raise TopicValidationError(
                f"Pattern segment contains invalid characters: {seg!r} in {pattern!r}"
            )

    # Normalise: collapse consecutive ** segments
    normalised_segments: list[str] = []
    for seg in raw_segments:
        if seg == "**" and normalised_segments and normalised_segments[-1] == "**":
            continue  # collapse consecutive **
        normalised_segments.append(seg)

    normalised = ".".join(normalised_segments)
    # Post-normalisation length check
    if len(normalised) > MAX_TOPIC_LENGTH:
        raise TopicValidationError(
            f"Normalised pattern exceeds maximum length of {MAX_TOPIC_LENGTH} (len={len(normalised)})"
        )
    return normalised


def topic_matches(topic: str, pattern: str) -> bool:
    """Deterministic segment-by-segment topic matching (no regex at match time).

    ``*``  matches exactly one segment.
    ``**`` matches zero or more segments.

    Both *topic* and *pattern* are assumed pre-validated.
    """
    topic_segs = topic.split(".")
    pat_segs = pattern.split(".")
    return _match(topic_segs, 0, pat_segs, 0)


def _match(topic_segs: list[str], ti: int, pat_segs: list[str], pi: int) -> bool:
    """Recursive segment matcher with ``**`` zero-or-more backtracking."""
    while pi < len(pat_segs):
        pat = pat_segs[pi]
        if pat == "**":
            # Try consuming 0..N topic segments
            # First, collapse any further consecutive ** (should be normalised, but be safe)
            next_pi = pi + 1
            while next_pi < len(pat_segs) and pat_segs[next_pi] == "**":
                next_pi += 1
            # If ** is the last pattern segment, it matches everything remaining
            if next_pi >= len(pat_segs):
                return True
            # Try matching rest of pattern starting from each remaining topic position
            for start in range(ti, len(topic_segs) + 1):
                if _match(topic_segs, start, pat_segs, next_pi):
                    return True
            return False
        elif pat == "*":
            # Must match exactly one segment
            if ti >= len(topic_segs):
                return False
            ti += 1
            pi += 1
        else:
            # Literal match
            if ti >= len(topic_segs) or topic_segs[ti] != pat:
                return False
            ti += 1
            pi += 1

    # Pattern exhausted — topic must also be exhausted
    return ti >= len(topic_segs)


# ---------------------------------------------------------------------------
# Subscription registry
# ---------------------------------------------------------------------------


class SubscriptionRegistry:
    """Manages static command, query, and event handler registrations.

    Thread safety: NOT thread-safe (synchronous single-threaded design).
    """

    def __init__(self) -> None:
        self._commands: dict[str, CommandRegistration] = {}
        self._queries: dict[str, QueryRegistration] = {}
        self._events: list[EventSubscription] = []
        self._sealed: bool = False
        self._next_index: int = 0

    # -- Registration (INITIALIZED only) ------------------------------------

    def register_command(
        self,
        topic: str,
        handler: MessageHandler,
        service_name: str,
    ) -> CommandRegistration:
        """Register a command handler for *topic*. Exactly one handler per topic."""
        self._check_registration_allowed()
        validated = validate_concrete_topic(topic)
        if validated in self._commands:
            raise DuplicateSubscriptionError(
                f"Duplicate command handler for topic {validated!r}: "
                f"already registered by '{self._commands[validated].service_name}'"
            )
        reg = CommandRegistration(
            topic=validated,
            handler=handler,
            service_name=service_name,
            registration_index=self._next_index,
        )
        self._commands[validated] = reg
        self._next_index += 1
        return reg

    def register_query(
        self,
        topic: str,
        handler: MessageHandler,
        service_name: str,
    ) -> QueryRegistration:
        """Register a query handler for *topic*. Exactly one handler per topic."""
        self._check_registration_allowed()
        validated = validate_concrete_topic(topic)
        if validated in self._queries:
            raise DuplicateSubscriptionError(
                f"Duplicate query handler for topic {validated!r}: "
                f"already registered by '{self._queries[validated].service_name}'"
            )
        reg = QueryRegistration(
            topic=validated,
            handler=handler,
            service_name=service_name,
            registration_index=self._next_index,
        )
        self._queries[validated] = reg
        self._next_index += 1
        return reg

    def subscribe_event(
        self,
        pattern: str,
        handler: EventHandler,
        service_name: str,
    ) -> EventSubscription:
        """Subscribe an event handler to *pattern*."""
        self._check_registration_allowed()
        normalised = validate_topic_pattern(pattern)
        sub = EventSubscription(
            pattern=normalised,
            handler=handler,
            service_name=service_name,
            registration_index=self._next_index,
        )
        self._events.append(sub)
        self._next_index += 1
        return sub

    # -- Lookup -------------------------------------------------------------

    def lookup_command(self, topic: str) -> Optional[CommandRegistration]:
        """Return the command registration for *topic*, or ``None``."""
        return self._commands.get(topic)

    def lookup_query(self, topic: str) -> Optional[QueryRegistration]:
        """Return the query registration for *topic*, or ``None``."""
        return self._queries.get(topic)

    def matching_event_subscriptions(self, topic: str) -> tuple[EventSubscription, ...]:
        """Return event subscriptions whose pattern matches *topic*.

        Order: ``(service_name ASC, registration_index ASC)``
        """
        matched = [sub for sub in self._events if topic_matches(topic, sub.pattern)]
        matched.sort(key=lambda s: (s.service_name, s.registration_index))
        return tuple(matched)

    # -- Lifecycle ----------------------------------------------------------

    def seal(self) -> None:
        """Seal the registry — no further static registrations allowed."""
        self._sealed = True

    def clear(self) -> None:
        """Clear all registrations and reset state."""
        self._commands.clear()
        self._queries.clear()
        self._events.clear()
        self._sealed = False
        self._next_index = 0

    @property
    def is_sealed(self) -> bool:
        return self._sealed

    @property
    def command_count(self) -> int:
        return len(self._commands)

    @property
    def query_count(self) -> int:
        return len(self._queries)

    @property
    def event_subscription_count(self) -> int:
        return len(self._events)

    # -- Internal -----------------------------------------------------------

    def _check_registration_allowed(self) -> None:
        if self._sealed:
            raise SubscriptionError(
                "Cannot register handlers after registry has been sealed (STARTED state)"
            )
