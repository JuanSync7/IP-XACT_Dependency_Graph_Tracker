"""
Core dependency graph manager built on NetworkX.

Provides CRUD operations on nodes/edges, serialisation to/from JSON,
and the foundation for change-detection and impact-propagation queries.
"""

from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Optional

import networkx as nx

from .models import (
    ArtifactNode, DependencyEdge, NodeType, EdgeType, Domain,
)

logger = logging.getLogger(__name__)


class GraphManager:
    """Central manager for the IP-XACT dependency graph."""

    def __init__(self) -> None:
        self._graph = nx.DiGraph()
        self._nodes: dict[str, ArtifactNode] = {}
        self._edges: dict[str, DependencyEdge] = {}

    # ------------------------------------------------------------------ #
    #  Node operations                                                     #
    # ------------------------------------------------------------------ #
    def add_node(self, node: ArtifactNode) -> None:
        if node.node_id in self._nodes:
            raise ValueError(f"Node '{node.node_id}' already exists. Use update_node().")
        self._nodes[node.node_id] = node
        self._graph.add_node(
            node.node_id,
            label=node.name,
            node_type=node.node_type.value,
            domain=node.domain.value,
            eda_tool=node.eda_tool,
        )
        logger.info("Added node: %s (%s)", node.node_id, node.node_type.value)

    def update_node(self, node: ArtifactNode) -> None:
        if node.node_id not in self._nodes:
            raise KeyError(f"Node '{node.node_id}' not found.")
        self._nodes[node.node_id] = node
        self._graph.nodes[node.node_id].update(
            label=node.name,
            node_type=node.node_type.value,
            domain=node.domain.value,
            eda_tool=node.eda_tool,
        )

    def remove_node(self, node_id: str) -> None:
        if node_id not in self._nodes:
            raise KeyError(f"Node '{node_id}' not found.")
        # Remove associated edges from our edge dict
        edges_to_remove = [
            eid for eid, e in self._edges.items()
            if e.source_id == node_id or e.target_id == node_id
        ]
        for eid in edges_to_remove:
            del self._edges[eid]
        self._graph.remove_node(node_id)
        del self._nodes[node_id]
        logger.info("Removed node: %s (and %d edges)", node_id, len(edges_to_remove))

    def get_node(self, node_id: str) -> ArtifactNode:
        if node_id not in self._nodes:
            raise KeyError(f"Node '{node_id}' not found.")
        return self._nodes[node_id]

    def get_nodes_by_type(self, ntype: NodeType) -> list[ArtifactNode]:
        return [n for n in self._nodes.values() if n.node_type == ntype]

    def get_nodes_by_domain(self, domain: Domain) -> list[ArtifactNode]:
        return [n for n in self._nodes.values() if n.domain == domain]

    # ------------------------------------------------------------------ #
    #  Edge operations                                                     #
    # ------------------------------------------------------------------ #
    def add_edge(self, edge: DependencyEdge) -> None:
        if edge.source_id not in self._nodes:
            raise KeyError(f"Source node '{edge.source_id}' not found.")
        if edge.target_id not in self._nodes:
            raise KeyError(f"Target node '{edge.target_id}' not found.")
        eid = edge.edge_id
        if eid in self._edges:
            raise ValueError(f"Edge '{eid}' already exists.")
        self._edges[eid] = edge
        self._graph.add_edge(
            edge.source_id,
            edge.target_id,
            edge_type=edge.edge_type.value,
            label=edge.label,
            domain=edge.domain.value,
        )
        logger.info("Added edge: %s", eid)

    def remove_edge(self, source_id: str, target_id: str, edge_type: EdgeType) -> None:
        eid = f"{source_id}--{edge_type.value}-->{target_id}"
        if eid not in self._edges:
            raise KeyError(f"Edge '{eid}' not found.")
        del self._edges[eid]
        self._graph.remove_edge(source_id, target_id)

    def get_edges_from(self, node_id: str) -> list[DependencyEdge]:
        return [e for e in self._edges.values() if e.source_id == node_id]

    def get_edges_to(self, node_id: str) -> list[DependencyEdge]:
        return [e for e in self._edges.values() if e.target_id == node_id]

    # ------------------------------------------------------------------ #
    #  Graph queries                                                       #
    # ------------------------------------------------------------------ #
    @property
    def nx_graph(self) -> nx.DiGraph:
        return self._graph

    @property
    def all_nodes(self) -> dict[str, ArtifactNode]:
        return dict(self._nodes)

    @property
    def all_edges(self) -> dict[str, DependencyEdge]:
        return dict(self._edges)

    def predecessors(self, node_id: str) -> list[str]:
        return list(self._graph.predecessors(node_id))

    def successors(self, node_id: str) -> list[str]:
        return list(self._graph.successors(node_id))

    def has_cycle(self) -> bool:
        return not nx.is_directed_acyclic_graph(self._graph)

    def topological_order(self) -> list[str]:
        if self.has_cycle():
            raise RuntimeError("Graph has cycles – topological sort not possible.")
        return list(nx.topological_sort(self._graph))

    def shortest_path(self, source: str, target: str) -> list[str]:
        try:
            return nx.shortest_path(self._graph, source, target)
        except nx.NetworkXNoPath:
            return []

    # ------------------------------------------------------------------ #
    #  Serialisation                                                       #
    # ------------------------------------------------------------------ #
    def save(self, path: str | Path) -> None:
        path = Path(path)
        data = {
            "nodes": [
                {
                    "node_id": n.node_id,
                    "name": n.name,
                    "node_type": n.node_type.value,
                    "domain": n.domain.value,
                    "file_path": n.file_path,
                    "description": n.description,
                    "eda_tool": n.eda_tool,
                    "version": n.version,
                    "tags": n.tags,
                    "metadata": n.metadata,
                }
                for n in self._nodes.values()
            ],
            "edges": [
                {
                    "source_id": e.source_id,
                    "target_id": e.target_id,
                    "edge_type": e.edge_type.value,
                    "label": e.label,
                    "domain": e.domain.value,
                    "metadata": e.metadata,
                    "mapping_details": e.mapping_details,
                }
                for e in self._edges.values()
            ],
        }
        path.write_text(json.dumps(data, indent=2))
        logger.info("Graph saved to %s (%d nodes, %d edges)",
                     path, len(self._nodes), len(self._edges))

    @classmethod
    def load(cls, path: str | Path) -> "GraphManager":
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"Graph file not found: {path}")
        data = json.loads(path.read_text())
        gm = cls()
        for nd in data["nodes"]:
            node = ArtifactNode(
                node_id=nd["node_id"],
                name=nd["name"],
                node_type=NodeType(nd["node_type"]),
                domain=Domain(nd["domain"]),
                file_path=nd.get("file_path"),
                description=nd.get("description", ""),
                eda_tool=nd.get("eda_tool", ""),
                version=nd.get("version", "1.0"),
                tags=nd.get("tags", []),
                metadata=nd.get("metadata", {}),
            )
            gm.add_node(node)
        for ed in data["edges"]:
            edge = DependencyEdge(
                source_id=ed["source_id"],
                target_id=ed["target_id"],
                edge_type=EdgeType(ed["edge_type"]),
                label=ed.get("label", ""),
                domain=Domain(ed.get("domain", "global")),
                metadata=ed.get("metadata", {}),
                mapping_details=ed.get("mapping_details", []),
            )
            gm.add_edge(edge)
        logger.info("Graph loaded from %s (%d nodes, %d edges)",
                     path, len(gm._nodes), len(gm._edges))
        return gm

    def summary(self) -> dict:
        type_counts = {}
        for n in self._nodes.values():
            type_counts[n.node_type.value] = type_counts.get(n.node_type.value, 0) + 1
        domain_counts = {}
        for n in self._nodes.values():
            domain_counts[n.domain.value] = domain_counts.get(n.domain.value, 0) + 1
        return {
            "total_nodes": len(self._nodes),
            "total_edges": len(self._edges),
            "has_cycles": self.has_cycle(),
            "node_types": type_counts,
            "domains": domain_counts,
        }
