"""
Mermaid Diagram Generator for IP-XACT Dependency Graphs.

Produces Mermaid-syntax diagrams that can be:
  - Rendered in GitHub/GitLab markdown
  - Pasted into mermaid.live
  - Embedded in Confluence / internal wikis
  - Saved as .mermaid files and rendered by Claude

Supports:
  - Full graph view
  - Filtered views by domain / node type / EDA tool
  - Impact-highlighted views (changed nodes in red, affected in orange)
  - Subgraph clustering by domain
"""

from __future__ import annotations
import logging
from pathlib import Path
from typing import Optional

from .graph_manager import GraphManager
from .models import (
    NodeType, EdgeType, Domain, NODE_COLOURS, EDGE_COLOURS,
)
from .change_detector import ChangeReport

logger = logging.getLogger(__name__)

# Mermaid shape mapping by node type
MERMAID_SHAPES = {
    # IP-XACT core → subroutine shape
    NodeType.IPXACT_COMPONENT: ("[[", "]]"),
    NodeType.IPXACT_DESIGN: ("[[", "]]"),
    NodeType.IPXACT_DESIGN_CONFIG: ("[[", "]]"),
    NodeType.IPXACT_ABSTRACTION_DEF: ("[[", "]]"),
    NodeType.IPXACT_CATALOG: ("[[", "]]"),
    NodeType.IPXACT_GENERATOR_CHAIN: ("[[", "]]"),
    # Constraints → hexagon
    NodeType.SDC_CONSTRAINT: ("{{", "}}"),
    NodeType.UPF_POWER: ("{{", "}}"),
    NodeType.CDC_CONSTRAINT: ("{{", "}}"),
    NodeType.RESET_SCHEME: ("{{", "}}"),
    # RTL → rectangle
    NodeType.RTL_SOURCE: ("[", "]"),
    NodeType.FPGA_SOURCE: ("([", "])"),
    NodeType.RTL_WRAPPER: ("[", "]"),
    NodeType.RTL_FILELIST: ("[", "]"),
    # Register/Memory → cylindrical
    NodeType.REGISTER_MAP: ("[(", ")]"),
    NodeType.UVM_RAL_MODEL: ("[(", ")]"),
    NodeType.C_HEADER: ("[(", ")]"),
    NodeType.REGISTER_DOC: ("[(", ")]"),
    NodeType.MEMORY_MAP: ("[(", ")]"),
    NodeType.LINKER_SCRIPT: ("[(", ")]"),
    NodeType.ADDRESS_DECODE: ("[(", ")]"),
    # Verification → rounded
    NodeType.BUS_VIP_CONFIG: ("(", ")"),
    NodeType.PROTOCOL_CHECKER: ("(", ")"),
    NodeType.TESTBENCH_TOP: ("(", ")"),
    # Physical → asymmetric
    NodeType.PIN_MAPPING: (">", "]"),
    NodeType.FLOORPLAN_CONSTRAINT: (">", "]"),
    NodeType.IO_PAD_CONFIG: (">", "]"),
    NodeType.DEF_LEF_CONSTRAINT: (">", "]"),
    # EDA scripts → asymmetric
    NodeType.EDA_SCRIPT: (">", "]"),
    NodeType.VENDOR_EXTENSION: (">", "]"),
    # Config/Docs → stadium/rounded
    NodeType.CONFIG_PARAM: ("([", "])"),
    NodeType.DOCUMENTATION: ("(", ")"),
}

EDGE_STYLE_MAP = {
    EdgeType.GENERATES: "-->",
    EdgeType.CONSTRAINS: "-.->",
    EdgeType.REFERENCES: "-->",
    EdgeType.MAPS_TO: "<-->",
    EdgeType.DERIVES_FROM: "==>",
    EdgeType.CONFIGURES: "-.->",
    EdgeType.INSTANTIATES: "-->",
    EdgeType.ABSTRACTS: "-.->",
    EdgeType.VALIDATES: "-.->",
}


def _sanitise(text: str) -> str:
    """Sanitise text for Mermaid labels."""
    return text.replace('"', "'").replace("\n", " ")


class MermaidGenerator:
    def __init__(self, graph: GraphManager):
        self._graph = graph

    def generate(
        self,
        title: str = "IP-XACT Dependency Graph",
        direction: str = "TB",
        filter_domain: Optional[Domain] = None,
        filter_node_type: Optional[NodeType] = None,
        filter_eda_tool: Optional[str] = None,
        group_by_domain: bool = True,
        change_report: Optional[ChangeReport] = None,
    ) -> str:
        """Generate complete Mermaid diagram string."""
        lines: list[str] = []
        lines.append(f"---")
        lines.append(f"title: {title}")
        lines.append(f"---")
        lines.append(f"graph {direction}")
        lines.append("")

        nodes = self._graph.all_nodes
        edges = self._graph.all_edges

        # Apply filters
        if filter_domain:
            nodes = {k: v for k, v in nodes.items() if v.domain == filter_domain}
        if filter_node_type:
            nodes = {k: v for k, v in nodes.items() if v.node_type == filter_node_type}
        if filter_eda_tool:
            nodes = {k: v for k, v in nodes.items() if v.eda_tool == filter_eda_tool}

        visible_ids = set(nodes.keys())

        # Filter edges to only those between visible nodes
        visible_edges = {
            k: v for k, v in edges.items()
            if v.source_id in visible_ids and v.target_id in visible_ids
        }

        # Determine changed / affected node IDs for highlighting
        changed_ids: set[str] = set()
        affected_ids: set[str] = set()
        if change_report:
            changed_ids = {cf.node_id for cf in change_report.changed_files}
            affected_ids = change_report.affected_node_ids - changed_ids

        # Group nodes by domain into subgraphs
        if group_by_domain:
            domains_map: dict[str, list[str]] = {}
            for nid, node in nodes.items():
                dom = node.domain.value
                domains_map.setdefault(dom, []).append(nid)

            for dom, nids in sorted(domains_map.items()):
                lines.append(f"    subgraph {dom}[\"{dom.replace('_', ' ').title()}\"]")
                lines.append(f"        direction {direction}")
                for nid in sorted(nids):
                    lines.append(f"        {self._node_def(nodes[nid])}")
                lines.append("    end")
                lines.append("")
        else:
            for nid in sorted(nodes.keys()):
                lines.append(f"    {self._node_def(nodes[nid])}")
            lines.append("")

        # Edges
        for eid, edge in sorted(visible_edges.items()):
            arrow = EDGE_STYLE_MAP.get(edge.edge_type, "-->")
            label = _sanitise(edge.label) if edge.label else edge.edge_type.value
            lines.append(f"    {edge.source_id} {arrow}|{label}| {edge.target_id}")
        lines.append("")

        # Style classes for change highlighting
        lines.append("    %% Styling")
        for nt, colour in NODE_COLOURS.items():
            class_name = f"cls_{nt.value}"
            matching = [nid for nid, n in nodes.items() if n.node_type == nt]
            if matching:
                lines.append(f"    classDef {class_name} fill:{colour},stroke:#333,color:#fff")
                lines.append(f"    class {','.join(matching)} {class_name}")

        if changed_ids & visible_ids:
            lines.append("    classDef changed fill:#E74C3C,stroke:#C0392B,color:#fff,stroke-width:3px")
            lines.append(f"    class {','.join(changed_ids & visible_ids)} changed")
        if affected_ids & visible_ids:
            lines.append("    classDef affected fill:#F39C12,stroke:#E67E22,color:#fff,stroke-width:2px,stroke-dasharray:5")
            lines.append(f"    class {','.join(affected_ids & visible_ids)} affected")

        return "\n".join(lines)

    def _node_def(self, node: ArtifactNode) -> str:
        """Generate a single Mermaid node definition."""
        left, right = MERMAID_SHAPES.get(node.node_type, ("[", "]"))
        label = _sanitise(f"{node.name}")
        if node.eda_tool:
            label += f" ({node.eda_tool})"
        return f'{node.node_id}{left}"{label}"{right}'

    def save(self, path: str | Path, **kwargs) -> None:
        content = self.generate(**kwargs)
        Path(path).write_text(content)
        logger.info("Mermaid diagram saved to %s", path)


def generate_impact_mermaid(
    graph: GraphManager,
    change_report: ChangeReport,
    title: str = "Change Impact Analysis",
) -> str:
    """Convenience function to produce an impact-highlighted Mermaid diagram."""
    gen = MermaidGenerator(graph)
    return gen.generate(title=title, change_report=change_report)
