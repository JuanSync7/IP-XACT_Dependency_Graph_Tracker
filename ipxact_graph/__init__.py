"""IP-XACT Dependency Graph Tracker – Public API."""

from .models import (
    ArtifactNode, DependencyEdge, NodeType, EdgeType, Domain,
    MappingCategory, EXPECTED_MAPPING_FIELDS, IPXACT_ELEMENT_EXPECTED_OUTPUTS,
)
from .graph_manager import GraphManager
from .change_detector import ChangeDetector, ChangeReport
from .mapping_validator import MappingValidator, ValidationReport
from .edge_case_auditor import EdgeCaseAuditor
from .visualize_mermaid import MermaidGenerator, generate_impact_mermaid
from .visualize_excel import ExcelReportGenerator

__all__ = [
    "ArtifactNode", "DependencyEdge", "NodeType", "EdgeType", "Domain",
    "MappingCategory", "EXPECTED_MAPPING_FIELDS", "IPXACT_ELEMENT_EXPECTED_OUTPUTS",
    "GraphManager",
    "ChangeDetector", "ChangeReport",
    "MappingValidator", "ValidationReport",
    "EdgeCaseAuditor",
    "MermaidGenerator", "generate_impact_mermaid",
    "ExcelReportGenerator",
]
