"""
Mapping Completeness Validator for IP-XACT Dependency Graphs.

This is the enforcement layer that ensures NO mapping is missed. It performs
three levels of validation:

Level 1 – STRUCTURAL COMPLETENESS:
  "Does every IP-XACT element have edges to ALL expected downstream outputs?"
  Example: If an IP-XACT component defines 2 clocks, there MUST be an edge
  to an SDC file AND (since >1 clock) to a CDC constraint file.

Level 2 – FIELD-LEVEL COMPLETENESS:
  "For each edge that exists, does it have ALL required mapping_details fields?"
  Example: The edge from IP-XACT component → SDC must contain mappings for
  clock_domain, io_timing, and false_path categories, each with specific fields.

Level 3 – ELEMENT COVERAGE:
  "Are ALL individual ports/clocks/resets/registers actually covered?"
  Example: If the IP-XACT component defines 9 ports, the edge to RTL_WRAPPER
  must have 9 port_naming mapping entries, one per port. Not 7, not 8. All 9.

The validator produces a structured ValidationReport with:
  - PASS items (everything correct)
  - WARNING items (conditional rules not met, might be OK)
  - FAIL items (mandatory mappings missing – human error detected)
  - Coverage percentage per category
"""

from __future__ import annotations
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .graph_manager import GraphManager
from .models import (
    ArtifactNode, DependencyEdge, NodeType, EdgeType, Domain,
    MappingCategory, EXPECTED_MAPPING_FIELDS, IPXACT_ELEMENT_EXPECTED_OUTPUTS,
)

logger = logging.getLogger(__name__)


class Severity(str):
    PASS = "PASS"
    WARNING = "WARNING"
    FAIL = "FAIL"
    INFO = "INFO"


@dataclass
class ValidationItem:
    severity: str           # PASS, WARNING, FAIL, INFO
    category: str           # e.g. "structural", "field_level", "element_coverage"
    node_id: str            # The node being validated
    target_id: str = ""     # The related target node (if applicable)
    mapping_category: str = ""  # e.g. "clock_domain", "port_naming"
    message: str = ""
    details: dict = field(default_factory=dict)


@dataclass
class ValidationReport:
    timestamp: float = field(default_factory=time.time)
    items: list[ValidationItem] = field(default_factory=list)

    @property
    def passes(self) -> list[ValidationItem]:
        return [i for i in self.items if i.severity == Severity.PASS]

    @property
    def warnings(self) -> list[ValidationItem]:
        return [i for i in self.items if i.severity == Severity.WARNING]

    @property
    def failures(self) -> list[ValidationItem]:
        return [i for i in self.items if i.severity == Severity.FAIL]

    @property
    def is_valid(self) -> bool:
        return len(self.failures) == 0

    @property
    def coverage_pct(self) -> float:
        total = len(self.passes) + len(self.failures)
        if total == 0:
            return 100.0
        return round(100.0 * len(self.passes) / total, 1)

    def summary(self) -> dict:
        # Per-category breakdown
        cat_stats: dict[str, dict] = {}
        for item in self.items:
            if item.severity in (Severity.PASS, Severity.FAIL):
                cat = item.mapping_category or item.category
                if cat not in cat_stats:
                    cat_stats[cat] = {"pass": 0, "fail": 0}
                if item.severity == Severity.PASS:
                    cat_stats[cat]["pass"] += 1
                else:
                    cat_stats[cat]["fail"] += 1

        category_coverage = {}
        for cat, stats in cat_stats.items():
            total = stats["pass"] + stats["fail"]
            category_coverage[cat] = {
                "pass": stats["pass"],
                "fail": stats["fail"],
                "coverage_pct": round(100.0 * stats["pass"] / total, 1) if total > 0 else 100.0,
            }

        return {
            "timestamp": self.timestamp,
            "overall_valid": self.is_valid,
            "overall_coverage_pct": self.coverage_pct,
            "total_checks": len(self.items),
            "passes": len(self.passes),
            "warnings": len(self.warnings),
            "failures": len(self.failures),
            "category_coverage": category_coverage,
            "failure_details": [
                {
                    "node": f.node_id,
                    "target": f.target_id,
                    "category": f.mapping_category or f.category,
                    "message": f.message,
                }
                for f in self.failures
            ],
        }

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.summary(), indent=2))

    def print_report(self) -> None:
        s = self.summary()
        print("\n" + "=" * 72)
        print("  MAPPING COMPLETENESS VALIDATION REPORT")
        print("=" * 72)
        valid_str = "✅ VALID" if s["overall_valid"] else "❌ INCOMPLETE"
        print(f"  Status:   {valid_str}")
        print(f"  Coverage: {s['overall_coverage_pct']}%")
        print(f"  Checks:   {s['total_checks']} total | "
              f"{s['passes']} pass | {s['warnings']} warn | {s['failures']} fail")
        print("-" * 72)

        if s["category_coverage"]:
            print("\n  COVERAGE BY CATEGORY:")
            for cat, stats in sorted(s["category_coverage"].items()):
                bar_len = 30
                filled = int(bar_len * stats["coverage_pct"] / 100)
                bar = "█" * filled + "░" * (bar_len - filled)
                status = "✅" if stats["coverage_pct"] == 100 else "❌"
                print(f"    {status} {cat:<25s} [{bar}] {stats['coverage_pct']:5.1f}% "
                      f"({stats['pass']}/{stats['pass'] + stats['fail']})")

        if s["failure_details"]:
            print(f"\n  ❌ FAILURES ({len(s['failure_details'])}):")
            for fd in s["failure_details"]:
                tgt = f" → {fd['target']}" if fd["target"] else ""
                print(f"    • [{fd['category']}] {fd['node']}{tgt}")
                print(f"      {fd['message']}")

        if self.warnings:
            print(f"\n  ⚠️  WARNINGS ({len(self.warnings)}):")
            for w in self.warnings:
                tgt = f" → {w.target_id}" if w.target_id else ""
                print(f"    • [{w.mapping_category or w.category}] {w.node_id}{tgt}")
                print(f"      {w.message}")

        print("=" * 72 + "\n")


class MappingValidator:
    """Validates that all required mappings are established in the graph."""

    def __init__(self, graph: GraphManager):
        self._graph = graph

    def validate(self) -> ValidationReport:
        """Run all three validation levels and return a complete report."""
        report = ValidationReport()
        self._level1_structural(report)
        self._level2_field_completeness(report)
        self._level3_element_coverage(report)
        logger.info("Validation complete: %d checks, %d failures, %.1f%% coverage",
                     len(report.items), len(report.failures), report.coverage_pct)
        return report

    # ------------------------------------------------------------------ #
    #  Level 1: Structural Completeness                                    #
    # ------------------------------------------------------------------ #
    def _level1_structural(self, report: ValidationReport) -> None:
        """Check that every IP-XACT component with defined elements has
        edges to all expected downstream output types."""
        nodes = self._graph.all_nodes
        edges = self._graph.all_edges

        for nid, node in nodes.items():
            if node.node_type not in (NodeType.IPXACT_COMPONENT, NodeType.IPXACT_DESIGN):
                continue
            if not node.defined_elements:
                report.items.append(ValidationItem(
                    severity=Severity.WARNING,
                    category="structural",
                    node_id=nid,
                    message=(f"IP-XACT node '{node.name}' has no defined_elements. "
                             "Cannot validate structural completeness. "
                             "Populate defined_elements dict with clocks, resets, "
                             "bus_interfaces, memory_maps, ports, power_domains."),
                ))
                continue

            # Get all outgoing edges from this node
            outgoing = self._graph.get_edges_from(nid)
            target_types = {nodes[e.target_id].node_type for e in outgoing
                           if e.target_id in nodes}

            for element_key, element_values in node.defined_elements.items():
                if not element_values:
                    continue  # Element category is empty, skip

                expected_outputs = IPXACT_ELEMENT_EXPECTED_OUTPUTS.get(element_key, [])
                for expected_type in expected_outputs:
                    # Special case: CDC only required if >1 clock
                    if (expected_type == NodeType.CDC_CONSTRAINT
                            and element_key == "clocks"
                            and len(element_values) <= 1):
                        continue

                    if expected_type in target_types:
                        report.items.append(ValidationItem(
                            severity=Severity.PASS,
                            category="structural",
                            node_id=nid,
                            mapping_category=element_key,
                            message=f"Edge exists: {node.name} → {expected_type.value}",
                        ))
                    else:
                        report.items.append(ValidationItem(
                            severity=Severity.FAIL,
                            category="structural",
                            node_id=nid,
                            mapping_category=element_key,
                            message=(
                                f"MISSING EDGE: '{node.name}' defines {element_key} "
                                f"({element_values}) but has NO edge to any "
                                f"{expected_type.value} node. This mapping must be "
                                f"established to prevent downstream errors."
                            ),
                            details={
                                "element_key": element_key,
                                "element_values": element_values,
                                "expected_target_type": expected_type.value,
                            },
                        ))

    # ------------------------------------------------------------------ #
    #  Level 2: Field-Level Completeness                                   #
    # ------------------------------------------------------------------ #
    def _level2_field_completeness(self, report: ValidationReport) -> None:
        """For each edge that exists, check that mapping_details contains
        all required fields per the EXPECTED_MAPPING_FIELDS schema."""
        nodes = self._graph.all_nodes
        edges = self._graph.all_edges

        for eid, edge in edges.items():
            src = nodes.get(edge.source_id)
            tgt = nodes.get(edge.target_id)
            if not src or not tgt:
                continue

            schema_key = (src.node_type, tgt.node_type)
            schemas = EXPECTED_MAPPING_FIELDS.get(schema_key)
            if not schemas:
                # No schema defined for this edge type pair – info only
                report.items.append(ValidationItem(
                    severity=Severity.INFO,
                    category="field_level",
                    node_id=edge.source_id,
                    target_id=edge.target_id,
                    message=(f"No mapping schema defined for "
                             f"{src.node_type.value} → {tgt.node_type.value}. "
                             f"Consider adding one to EXPECTED_MAPPING_FIELDS."),
                ))
                continue

            # Get all categories present in the mapping_details
            present_categories = set()
            for md in edge.mapping_details:
                cat = md.get("category", "")
                if cat:
                    present_categories.add(cat)

            for schema in schemas:
                cat = schema["category"]
                is_conditional = schema.get("conditional", False)
                required_fields = schema["required_fields"]

                # Find all mapping_details entries for this category
                matching_entries = [
                    md for md in edge.mapping_details
                    if md.get("category") == cat
                ]

                if not matching_entries:
                    if is_conditional:
                        report.items.append(ValidationItem(
                            severity=Severity.WARNING,
                            category="field_level",
                            node_id=edge.source_id,
                            target_id=edge.target_id,
                            mapping_category=cat,
                            message=(f"Conditional mapping '{cat}' not present in "
                                     f"{src.name} → {tgt.name}. "
                                     f"Verify this is intentionally omitted. "
                                     f"({schema['description']})"),
                        ))
                    else:
                        report.items.append(ValidationItem(
                            severity=Severity.FAIL,
                            category="field_level",
                            node_id=edge.source_id,
                            target_id=edge.target_id,
                            mapping_category=cat,
                            message=(f"MISSING MAPPING CATEGORY: '{cat}' not found in "
                                     f"mapping_details for {src.name} → {tgt.name}. "
                                     f"Required fields: {required_fields}. "
                                     f"({schema['description']})"),
                            details={"required_fields": required_fields},
                        ))
                    continue

                # Check each entry has all required fields
                for idx, entry in enumerate(matching_entries):
                    missing_fields = [
                        f for f in required_fields
                        if f not in entry or entry[f] is None or entry[f] == ""
                    ]
                    if missing_fields:
                        report.items.append(ValidationItem(
                            severity=Severity.FAIL,
                            category="field_level",
                            node_id=edge.source_id,
                            target_id=edge.target_id,
                            mapping_category=cat,
                            message=(f"INCOMPLETE MAPPING: '{cat}' entry #{idx} in "
                                     f"{src.name} → {tgt.name} is missing fields: "
                                     f"{missing_fields}"),
                            details={
                                "entry_index": idx,
                                "missing_fields": missing_fields,
                                "present_fields": list(entry.keys()),
                            },
                        ))
                    else:
                        report.items.append(ValidationItem(
                            severity=Severity.PASS,
                            category="field_level",
                            node_id=edge.source_id,
                            target_id=edge.target_id,
                            mapping_category=cat,
                            message=(f"Mapping complete: '{cat}' entry #{idx} in "
                                     f"{src.name} → {tgt.name}"),
                        ))

    # ------------------------------------------------------------------ #
    #  Level 3: Element Coverage                                           #
    # ------------------------------------------------------------------ #
    def _level3_element_coverage(self, report: ValidationReport) -> None:
        """Check that ALL individual elements (ports, clocks, resets, registers)
        defined in the IP-XACT component are covered by mapping entries.

        For example, if component defines 9 ports, the port_naming mappings
        to RTL_WRAPPER must have 9 entries – one per port."""
        nodes = self._graph.all_nodes
        edges = self._graph.all_edges

        # Map: which MappingCategory tracks which defined_elements key,
        # and which field in the mapping_detail identifies the element
        COVERAGE_CHECKS = [
            {
                "element_key": "ports",
                "mapping_category": MappingCategory.PORT_NAMING.value,
                "element_field_in_mapping": "ipxact_port",
                "target_types": [NodeType.RTL_WRAPPER],
            },
            {
                "element_key": "clocks",
                "mapping_category": MappingCategory.CLOCK_DOMAIN.value,
                "element_field_in_mapping": "ipxact_clock_port",
                "target_types": [NodeType.SDC_CONSTRAINT],
            },
            {
                "element_key": "resets",
                "mapping_category": MappingCategory.RESET_DOMAIN.value,
                "element_field_in_mapping": "ipxact_reset_port",
                "target_types": [NodeType.RESET_SCHEME],
            },
            {
                "element_key": "bus_interfaces",
                "mapping_category": MappingCategory.BUS_INTERFACE.value,
                "element_field_in_mapping": "bus_interface_name",
                "target_types": [NodeType.BUS_VIP_CONFIG],
            },
            {
                "element_key": "memory_maps",
                "mapping_category": MappingCategory.MEMORY_MAP.value,
                "element_field_in_mapping": "memory_map_name",
                "target_types": [NodeType.MEMORY_MAP, NodeType.REGISTER_MAP],
            },
            {
                "element_key": "power_domains",
                "mapping_category": MappingCategory.POWER_DOMAIN.value,
                "element_field_in_mapping": "upf_power_domain",
                "target_types": [NodeType.UPF_POWER],
            },
        ]

        for nid, node in nodes.items():
            if not node.defined_elements:
                continue

            outgoing = self._graph.get_edges_from(nid)

            for check in COVERAGE_CHECKS:
                elem_key = check["element_key"]
                elements = node.defined_elements.get(elem_key, [])
                if not elements:
                    continue

                cat = check["mapping_category"]
                id_field = check["element_field_in_mapping"]
                target_types = check["target_types"]

                # Find all edges to the expected target types
                relevant_edges = [
                    e for e in outgoing
                    if e.target_id in nodes
                    and nodes[e.target_id].node_type in target_types
                ]

                if not relevant_edges:
                    # Already caught by Level 1, skip here
                    continue

                # Collect all mapped element names across all relevant edges
                mapped_elements = set()
                for edge in relevant_edges:
                    for md in edge.mapping_details:
                        if md.get("category") == cat:
                            val = md.get(id_field)
                            if val:
                                mapped_elements.add(val)

                # Compare against defined elements
                defined_set = set(elements)
                covered = defined_set & mapped_elements
                missing = defined_set - mapped_elements
                extra = mapped_elements - defined_set

                if not missing:
                    report.items.append(ValidationItem(
                        severity=Severity.PASS,
                        category="element_coverage",
                        node_id=nid,
                        mapping_category=cat,
                        message=(f"Full coverage: all {len(defined_set)} {elem_key} "
                                 f"in '{node.name}' have '{cat}' mappings"),
                        details={
                            "defined": sorted(defined_set),
                            "covered": sorted(covered),
                        },
                    ))
                else:
                    report.items.append(ValidationItem(
                        severity=Severity.FAIL,
                        category="element_coverage",
                        node_id=nid,
                        mapping_category=cat,
                        message=(f"INCOMPLETE COVERAGE: {len(missing)}/{len(defined_set)} "
                                 f"{elem_key} in '{node.name}' have NO '{cat}' mapping. "
                                 f"Missing: {sorted(missing)}"),
                        details={
                            "defined": sorted(defined_set),
                            "covered": sorted(covered),
                            "missing": sorted(missing),
                            "coverage_pct": round(100.0 * len(covered) / len(defined_set), 1),
                        },
                    ))

                if extra:
                    report.items.append(ValidationItem(
                        severity=Severity.WARNING,
                        category="element_coverage",
                        node_id=nid,
                        mapping_category=cat,
                        message=(f"Extra mappings found for {elem_key} not in "
                                 f"defined_elements: {sorted(extra)}. "
                                 f"Check if defined_elements needs updating."),
                        details={"extra": sorted(extra)},
                    ))
