# -*- coding: utf-8 -*-
"""Scene Graph Transform Tree and Bounded Traversal Engine for M06."""

from __future__ import annotations

from typing import Optional

from holomed.anatomy.geometry import compose_rigid_transforms
from holomed.anatomy.models import RigidTransform3D
from holomed.xr.exceptions import (
    XRCapacityError,
    XRHierarchyError,
)
from holomed.xr.models import (
    MAX_SCENE_DEPTH,
    MAX_SCENE_NODES,
    VisualNode,
)


class SceneGraph:
    """Bounded, directed acyclic scene graph tree managing visual spatial nodes."""

    def __init__(self) -> None:
        self._nodes: dict[str, VisualNode] = {}
        self._children: dict[str, list[str]] = {}

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    @property
    def nodes(self) -> tuple[VisualNode, ...]:
        """Return all registered visual nodes sorted deterministically by node_id ASC."""
        sorted_keys = sorted(self._nodes.keys())
        return tuple(self._nodes[k] for k in sorted_keys)

    def add_node(self, node: VisualNode) -> None:
        """Register a visual node into the scene graph after cycle and depth validation."""
        if len(self._nodes) >= MAX_SCENE_NODES:
            raise XRCapacityError(f"Scene graph capacity exceeded ({MAX_SCENE_NODES} nodes max)")

        if node.node_id in self._nodes:
            raise XRHierarchyError(f"Duplicate node_id {node.node_id!r}")

        if node.parent_id is not None:
            if node.parent_id not in self._nodes:
                raise XRHierarchyError(f"Parent node {node.parent_id!r} does not exist in scene graph")
            if node.parent_id == node.node_id:
                raise XRHierarchyError(f"Self-referential parent cycle detected for {node.node_id!r}")

            # Cycle and depth check: traverse up to root
            curr_parent: Optional[str] = node.parent_id
            depth = 1
            visited: set[str] = {node.node_id}

            while curr_parent is not None:
                if curr_parent in visited:
                    raise XRHierarchyError(f"Cycle detected in visual hierarchy involving {curr_parent!r}")
                visited.add(curr_parent)
                depth += 1
                if depth > MAX_SCENE_DEPTH:
                    raise XRHierarchyError(
                        f"Scene graph depth ({depth}) exceeds maximum allowable ({MAX_SCENE_DEPTH})"
                    )
                curr_parent = self._nodes[curr_parent].parent_id

        self._nodes[node.node_id] = node
        if node.node_id not in self._children:
            self._children[node.node_id] = []

        if node.parent_id is not None:
            if node.parent_id not in self._children:
                self._children[node.parent_id] = []
            self._children[node.parent_id].append(node.node_id)
            self._children[node.parent_id].sort()

    def get_node(self, node_id: str) -> VisualNode:
        """Retrieve a visual node by ID or raise XRHierarchyError."""
        if node_id not in self._nodes:
            raise XRHierarchyError(f"Visual node {node_id!r} not found in scene graph")
        return self._nodes[node_id]

    def get_children(self, node_id: str) -> tuple[str, ...]:
        """Return direct children node IDs sorted lexicographically."""
        if node_id not in self._nodes:
            raise XRHierarchyError(f"Visual node {node_id!r} not found in scene graph")
        return tuple(sorted(self._children.get(node_id, [])))

    def get_ancestry(self, node_id: str) -> tuple[str, ...]:
        """Return ordered list of ancestor node IDs from immediate parent up to root."""
        if node_id not in self._nodes:
            raise XRHierarchyError(f"Visual node {node_id!r} not found in scene graph")

        ancestry: list[str] = []
        curr = self._nodes[node_id].parent_id
        while curr is not None:
            ancestry.append(curr)
            curr = self._nodes[curr].parent_id
        return tuple(ancestry)

    def get_descendants(self, node_id: str) -> tuple[str, ...]:
        """Return ordered list of all descendants using deterministic breadth-first search."""
        if node_id not in self._nodes:
            raise XRHierarchyError(f"Visual node {node_id!r} not found in scene graph")

        descendants: list[str] = []
        queue: list[str] = list(sorted(self._children.get(node_id, [])))

        while queue:
            child = queue.pop(0)
            descendants.append(child)
            grand_children = sorted(self._children.get(child, []))
            queue.extend(grand_children)

        return tuple(descendants)

    def get_global_transform(self, node_id: str) -> RigidTransform3D:
        """Compute accumulated world-space transform from root to this node."""
        node = self.get_node(node_id)
        if node.parent_id is None:
            return node.local_transform

        parent_global = self.get_global_transform(node.parent_id)
        # Adapt frame names to satisfy composition check if needed
        aligned_local = RigidTransform3D(
            translation=node.local_transform.translation,
            rotation_quaternion=node.local_transform.rotation_quaternion,
            source_frame=node.local_transform.source_frame,
            target_frame=parent_global.source_frame,
        )
        return compose_rigid_transforms(parent_global, aligned_local)

    def remove_node(self, node_id: str) -> None:
        """Remove a node and recursively remove all its descendants."""
        if node_id not in self._nodes:
            return

        descendants = self.get_descendants(node_id)
        for desc_id in reversed(descendants):
            self._nodes.pop(desc_id, None)
            self._children.pop(desc_id, None)

        node = self._nodes.pop(node_id)
        self._children.pop(node_id, None)
        if node.parent_id and node.parent_id in self._children:
            if node_id in self._children[node.parent_id]:
                self._children[node.parent_id].remove(node_id)

    def clear(self) -> None:
        """Clear all registered nodes and relationships."""
        self._nodes.clear()
        self._children.clear()
