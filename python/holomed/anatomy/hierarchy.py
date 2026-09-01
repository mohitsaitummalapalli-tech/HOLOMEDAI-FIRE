# -*- coding: utf-8 -*-
"""Acyclic Anatomical Hierarchy Graph and Traversal Engine for M05."""

from __future__ import annotations

from typing import Optional, Sequence

from holomed.anatomy.exceptions import (
    AnatomyCapacityError,
    AnatomyHierarchyError,
)
from holomed.anatomy.models import (
    MAX_ANATOMICAL_ENTITIES,
    MAX_ANATOMY_HIERARCHY_DEPTH,
    AnatomicalEntity,
)


class AnatomyHierarchy:
    """Bounded, cycle-free directed tree hierarchy for anatomical entities."""

    def __init__(self) -> None:
        self._entities: dict[str, AnatomicalEntity] = {}
        self._children: dict[str, list[str]] = {}

    @property
    def entity_count(self) -> int:
        return len(self._entities)

    @property
    def entities(self) -> tuple[AnatomicalEntity, ...]:
        """Return all registered entities sorted deterministically by entity_id."""
        sorted_keys = sorted(self._entities.keys())
        return tuple(self._entities[k] for k in sorted_keys)

    def add_entity(self, entity: AnatomicalEntity) -> None:
        """Register an entity into the hierarchy after cycle and depth validation."""
        if len(self._entities) >= MAX_ANATOMICAL_ENTITIES:
            raise AnatomyCapacityError(
                f"Anatomy hierarchy capacity exceeded ({MAX_ANATOMICAL_ENTITIES} entities max)"
            )

        if entity.entity_id in self._entities:
            raise AnatomyHierarchyError(f"Duplicate entity_id {entity.entity_id!r}")

        # If parent_id specified, parent must already exist
        if entity.parent_id is not None:
            if entity.parent_id not in self._entities:
                raise AnatomyHierarchyError(
                    f"Parent entity {entity.parent_id!r} does not exist in hierarchy"
                )
            if entity.parent_id == entity.entity_id:
                raise AnatomyHierarchyError(
                    f"Self-referential parent cycle detected for {entity.entity_id!r}"
                )

            # Cycle and depth check: traverse up to root
            curr_parent: Optional[str] = entity.parent_id
            depth = 1
            visited: set[str] = {entity.entity_id}

            while curr_parent is not None:
                if curr_parent in visited:
                    raise AnatomyHierarchyError(
                        f"Cycle detected in ancestry path involving {curr_parent!r}"
                    )
                visited.add(curr_parent)
                depth += 1
                if depth > MAX_ANATOMY_HIERARCHY_DEPTH:
                    raise AnatomyHierarchyError(
                        f"Anatomy hierarchy depth ({depth}) exceeds maximum allowable ({MAX_ANATOMY_HIERARCHY_DEPTH})"
                    )
                curr_parent = self._entities[curr_parent].parent_id

        self._entities[entity.entity_id] = entity
        if entity.entity_id not in self._children:
            self._children[entity.entity_id] = []

        if entity.parent_id is not None:
            if entity.parent_id not in self._children:
                self._children[entity.parent_id] = []
            self._children[entity.parent_id].append(entity.entity_id)
            self._children[entity.parent_id].sort()

    def get_entity(self, entity_id: str) -> AnatomicalEntity:
        """Retrieve an entity by ID or raise AnatomyHierarchyError."""
        if entity_id not in self._entities:
            raise AnatomyHierarchyError(f"Entity {entity_id!r} not found in hierarchy")
        return self._entities[entity_id]

    def get_children(self, entity_id: str) -> tuple[str, ...]:
        """Return direct children IDs sorted lexicographically."""
        if entity_id not in self._entities:
            raise AnatomyHierarchyError(f"Entity {entity_id!r} not found in hierarchy")
        return tuple(sorted(self._children.get(entity_id, [])))

    def get_ancestry(self, entity_id: str) -> tuple[str, ...]:
        """Return ordered list of ancestor entity IDs from immediate parent up to root."""
        if entity_id not in self._entities:
            raise AnatomyHierarchyError(f"Entity {entity_id!r} not found in hierarchy")

        ancestry: list[str] = []
        curr = self._entities[entity_id].parent_id
        while curr is not None:
            ancestry.append(curr)
            curr = self._entities[curr].parent_id
        return tuple(ancestry)

    def get_descendants(self, entity_id: str) -> tuple[str, ...]:
        """Return ordered list of all descendants using deterministic breadth-first search."""
        if entity_id not in self._entities:
            raise AnatomyHierarchyError(f"Entity {entity_id!r} not found in hierarchy")

        descendants: list[str] = []
        queue: list[str] = list(sorted(self._children.get(entity_id, [])))

        while queue:
            child = queue.pop(0)
            descendants.append(child)
            grand_children = sorted(self._children.get(child, []))
            queue.extend(grand_children)

        return tuple(descendants)

    def clear(self) -> None:
        """Clear all registered entities and relationships."""
        self._entities.clear()
        self._children.clear()
