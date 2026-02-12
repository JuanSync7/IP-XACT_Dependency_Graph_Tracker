"""
Change Detection & Impact Propagation Engine.

1. Scans all registered artifact files, computes SHA-256 hashes.
2. Compares against a stored hash baseline to detect changes.
3. For every changed node, performs a BFS/DFS through the dependency graph
   to identify all downstream (and optionally upstream) affected artifacts.
4. Produces a structured ChangeReport.
"""

from __future__ import annotations
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .graph_manager import GraphManager
from .models import ArtifactNode, DependencyEdge, EdgeType

logger = logging.getLogger(__name__)


@dataclass
class ChangedFile:
    node_id: str
    name: str
    file_path: str
    old_hash: Optional[str]
    new_hash: Optional[str]
    status: str  # "modified", "added", "deleted", "missing"


@dataclass
class ImpactChain:
    """One propagation path from a changed node to an affected downstream node."""
    source_node_id: str
    affected_node_id: str
    path: list[str]           # ordered node IDs from source → affected
    edge_types: list[str]     # edge types along the path
    depth: int


@dataclass
class ChangeReport:
    timestamp: float
    changed_files: list[ChangedFile] = field(default_factory=list)
    impact_chains: list[ImpactChain] = field(default_factory=list)
    affected_node_ids: set[str] = field(default_factory=set)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "changed_files": [
                {
                    "node_id": cf.node_id,
                    "name": cf.name,
                    "file_path": cf.file_path,
                    "old_hash": cf.old_hash,
                    "new_hash": cf.new_hash,
                    "status": cf.status,
                }
                for cf in self.changed_files
            ],
            "impact_chains": [
                {
                    "source": ic.source_node_id,
                    "affected": ic.affected_node_id,
                    "path": ic.path,
                    "edge_types": ic.edge_types,
                    "depth": ic.depth,
                }
                for ic in self.impact_chains
            ],
            "total_changed": len(self.changed_files),
            "total_affected": len(self.affected_node_ids),
            "affected_nodes": sorted(self.affected_node_ids),
        }

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2))


class ChangeDetector:
    """Detects file-level changes and propagates impact through the graph."""

    def __init__(self, graph: GraphManager, baseline_path: Optional[str | Path] = None):
        self._graph = graph
        self._baseline: dict[str, str] = {}  # node_id → SHA-256
        if baseline_path and Path(baseline_path).is_file():
            self._baseline = json.loads(Path(baseline_path).read_text())
            logger.info("Loaded hash baseline with %d entries", len(self._baseline))

    # ------------------------------------------------------------------ #
    #  Hash baseline management                                            #
    # ------------------------------------------------------------------ #
    def build_baseline(self) -> dict[str, str]:
        """Compute hashes for all nodes that have file_path and store as baseline."""
        baseline: dict[str, str] = {}
        for nid, node in self._graph.all_nodes.items():
            if node.file_path:
                h = node.compute_hash()
                if h:
                    baseline[nid] = h
        self._baseline = baseline
        logger.info("Built baseline with %d file hashes", len(baseline))
        return baseline

    def save_baseline(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self._baseline, indent=2))
        logger.info("Baseline saved to %s", path)

    # ------------------------------------------------------------------ #
    #  Change detection                                                    #
    # ------------------------------------------------------------------ #
    def detect_changes(self) -> list[ChangedFile]:
        """Compare current file hashes against baseline."""
        changes: list[ChangedFile] = []
        current_hashes: dict[str, Optional[str]] = {}

        for nid, node in self._graph.all_nodes.items():
            if not node.file_path:
                continue
            new_hash = node.compute_hash()
            current_hashes[nid] = new_hash
            old_hash = self._baseline.get(nid)

            if old_hash is None and new_hash is not None:
                changes.append(ChangedFile(nid, node.name, node.file_path, None, new_hash, "added"))
            elif old_hash is not None and new_hash is None:
                changes.append(ChangedFile(nid, node.name, node.file_path or "", old_hash, None, "missing"))
            elif old_hash != new_hash:
                changes.append(ChangedFile(nid, node.name, node.file_path, old_hash, new_hash, "modified"))

        logger.info("Detected %d changed files", len(changes))
        return changes

    # ------------------------------------------------------------------ #
    #  Impact propagation (BFS downstream)                                 #
    # ------------------------------------------------------------------ #
    def propagate_impact(
        self,
        changed_node_ids: list[str],
        max_depth: int = 50,
        include_upstream: bool = False,
    ) -> list[ImpactChain]:
        """
        BFS from each changed node through the dependency graph.
        Returns all impact chains showing how changes propagate.
        """
        g = self._graph.nx_graph
        all_chains: list[ImpactChain] = []

        for start_id in changed_node_ids:
            if start_id not in g:
                logger.warning("Changed node '%s' not in graph – skipping.", start_id)
                continue

            # BFS downstream (successors)
            visited = {start_id}
            queue: deque[tuple[str, list[str], list[str]]] = deque()
            # seed with immediate successors
            for succ in g.successors(start_id):
                edge_data = g.edges[start_id, succ]
                queue.append((succ, [start_id, succ], [edge_data.get("edge_type", "")]))

            while queue:
                current, path, etypes = queue.popleft()
                if current in visited or len(path) > max_depth:
                    continue
                visited.add(current)
                all_chains.append(ImpactChain(
                    source_node_id=start_id,
                    affected_node_id=current,
                    path=list(path),
                    edge_types=list(etypes),
                    depth=len(path) - 1,
                ))
                for succ in g.successors(current):
                    if succ not in visited:
                        edge_data = g.edges[current, succ]
                        queue.append((
                            succ,
                            path + [succ],
                            etypes + [edge_data.get("edge_type", "")],
                        ))

            # Optionally BFS upstream (predecessors)
            if include_upstream:
                visited_up = {start_id}
                queue_up: deque[tuple[str, list[str], list[str]]] = deque()
                for pred in g.predecessors(start_id):
                    edge_data = g.edges[pred, start_id]
                    queue_up.append((pred, [start_id, pred], [edge_data.get("edge_type", "")]))

                while queue_up:
                    current, path, etypes = queue_up.popleft()
                    if current in visited_up or len(path) > max_depth:
                        continue
                    visited_up.add(current)
                    all_chains.append(ImpactChain(
                        source_node_id=start_id,
                        affected_node_id=current,
                        path=list(path),
                        edge_types=list(etypes),
                        depth=len(path) - 1,
                    ))
                    for pred in g.predecessors(current):
                        if pred not in visited_up:
                            edge_data = g.edges[pred, current]
                            queue_up.append((
                                pred,
                                path + [pred],
                                etypes + [edge_data.get("edge_type", "")],
                            ))

        logger.info("Generated %d impact chains from %d changed nodes",
                     len(all_chains), len(changed_node_ids))
        return all_chains

    # ------------------------------------------------------------------ #
    #  Full scan                                                           #
    # ------------------------------------------------------------------ #
    def full_scan(self, include_upstream: bool = False) -> ChangeReport:
        """Detect changes and propagate impact in one call."""
        changes = self.detect_changes()
        changed_ids = [c.node_id for c in changes]
        chains = self.propagate_impact(changed_ids, include_upstream=include_upstream)
        affected = {ic.affected_node_id for ic in chains}
        report = ChangeReport(
            timestamp=time.time(),
            changed_files=changes,
            impact_chains=chains,
            affected_node_ids=affected,
        )
        logger.info("Full scan complete: %d changes, %d affected nodes",
                     len(changes), len(affected))
        return report
