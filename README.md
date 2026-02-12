# IP-XACT Dependency Graph Tracker

A Python-based dependency graph and mapping validation system for ASIC design flows that use IP-XACT (IEEE 1685) as the source of truth for component packaging, port standardisation, and EDA tool integration.

The system tracks every artifact generated from IP-XACT — SDC constraints, UPF power intent, RTL wrappers, register models, CDC rules, pin mappings, EDA scripts, and more — as nodes in a directed graph, then validates that all required cross-artifact mappings are complete and detects when file changes need to propagate downstream.

---

## Quick Start

### Prerequisites

```bash
pip install networkx openpyxl
```

Python 3.10+ required.

### Run the Demo

```bash
python demo.py
```

This creates a realistic AES-GCM crypto accelerator scenario with 20 artifacts across 7 domains, runs the 3-level mapping validation (84 checks), simulates an SDC clock frequency change with impact propagation, and generates all output files.

### Output Files

After running, check the `output/` directory:

| File | What It Contains |
|------|-----------------|
| `validation_report.json` | Mapping completeness audit — the key deliverable |
| `dependency_graph.json` | Serialised graph (version-control this) |
| `full_graph.mermaid` | Full dependency diagram — paste into [mermaid.live](https://mermaid.live) |
| `impact_graph.mermaid` | Change-impact highlighted diagram |
| `dependency_report.xlsx` | 6-sheet Excel workbook with all mappings and matrices |
| `change_report.json` | Which files changed and what's affected |
| `hash_baseline.json` | SHA-256 baseline for change detection |

---

## What Problem Does This Solve?

When a consulting company translates a customer's FPGA design into a reusable IP-XACT package, the translation produces dozens of downstream files consumed by different EDA tools across different engineering domains. The IP-XACT component's clock definition must match the SDC's `create_clock`; the port list must match the RTL wrapper, the pin mapping, and the filelist; the memory map must produce a UVM RAL model, C headers, register docs, and address decode logic.

These cross-file dependencies are typically tracked manually, and changes are propagated by engineers remembering which other files need updating. This system replaces that manual tracking with an automated graph that validates completeness and traces change impact.

---

## Project Structure

```
ipxact_dep_tracker/
├── demo.py                            # Runnable demo (AES-GCM scenario)
├── SYSTEM_DOCUMENTATION.md            # Full technical reference
├── README.md                          # This file
├── ipxact_graph/
│   ├── models.py                      # Node/edge types, mapping schemas
│   ├── graph_manager.py               # Graph CRUD, serialisation, queries
│   ├── change_detector.py             # SHA-256 hashing, BFS impact propagation
│   ├── mapping_validator.py           # 3-level completeness enforcement
│   ├── visualize_mermaid.py           # Mermaid diagram generation
│   └── visualize_excel.py             # Multi-sheet Excel reports
├── sample_artifacts/                   # Created by demo.py at runtime
└── output/                             # Generated reports and diagrams
```

---

## Core Concepts

### Nodes: Design Artifacts

Every file in the flow is registered as a node with a type, domain, file path, and — for IP-XACT components — a `defined_elements` dict listing what the component contains:

```python
gm.add_node(ArtifactNode(
    node_id="ipxact_my_block",
    name="My Block IP-XACT Component",
    node_type=NodeType.IPXACT_COMPONENT,
    domain=Domain.GLOBAL,
    file_path="/project/ipxact/my_block.xml",
    eda_tool="ip-xact",
    defined_elements={
        "clocks": ["i_clk", "i_clk_aux"],
        "resets": ["i_rst_n"],
        "ports": ["i_clk", "i_clk_aux", "i_rst_n", "i_data", "o_result"],
        "bus_interfaces": ["axi_slave"],
        "memory_maps": ["reg_block_0"],
        "power_domains": ["PD_MAIN"],
    },
))
```

The system supports 30 node types covering IP-XACT core files, constraint files (SDC/UPF/CDC/reset), RTL, the full register generation chain (register map → UVM RAL → C header → register doc → address decode → linker script), verification collateral, physical design files, and EDA scripts.

### Edges: Dependencies with Field-Level Mappings

Dependencies carry `mapping_details` — structured dictionaries that record exactly which field in the source maps to which field in the target:

```python
gm.add_edge(DependencyEdge(
    source_id="ipxact_my_block",
    target_id="sdc_my_block",
    edge_type=EdgeType.GENERATES,
    mapping_details=[
        {"category": "clock_domain",
         "ipxact_clock_port": "i_clk",
         "sdc_clock_name": "clk_main",
         "period_ns": 5.0,
         "uncertainty_setup": 0.2,
         "uncertainty_hold": 0.05},
        {"category": "io_timing",
         "ipxact_port": "i_data",
         "sdc_command": "set_input_delay",
         "clock_domain": "clk_main",
         "max_delay": 1.5,
         "min_delay": 0.2},
    ],
))
```

These mapping details are what the validator checks for completeness.

### Validation: 3-Level Completeness Enforcement

```python
validator = MappingValidator(gm)
report = validator.validate()
report.print_report()
```

| Level | Question Answered | Example Failure |
|-------|------------------|-----------------|
| **1 — Structural** | Does every IP-XACT element have edges to all expected downstream outputs? | "Component defines `memory_maps` but has no edge to any `uvm_ral_model` node." |
| **2 — Field-Level** | Does each edge contain all required mapping fields? | "Edge to SDC is missing the `io_timing` category entirely." |
| **3 — Element Coverage** | Is every individual port/clock/reset/register actually mapped? | "10 ports defined but only 1 has a `pin_assignment` mapping. Missing: i_clk_aux, i_rst_n, ..." |

The expected mappings are defined as schemas in `models.py` (`EXPECTED_MAPPING_FIELDS` and `IPXACT_ELEMENT_EXPECTED_OUTPUTS`). Extend these dicts when you add new artifact types to your flow.

### Change Detection and Impact Propagation

```python
detector = ChangeDetector(gm)
detector.build_baseline()
detector.save_baseline("baseline.json")

# Later, after files have been modified:
detector = ChangeDetector(gm, baseline_path="baseline.json")
report = detector.full_scan(include_upstream=True)
```

The detector computes SHA-256 hashes of every registered file, compares against the baseline, and runs BFS through the graph to trace all downstream (and optionally upstream) artifacts affected by each change. The demo simulates changing an SDC clock period and shows the impact chain propagating to the DFT SDC, DC compile script, and upstream to the IP-XACT component and customer FPGA source.

---

## Visualisation

### Mermaid Diagrams

Generated `.mermaid` files can be rendered in GitHub/GitLab markdown, [mermaid.live](https://mermaid.live), Confluence, or any Mermaid-compatible viewer. Nodes are grouped by domain into subgraphs, shaped by type, and colour-coded. When a change report is provided, changed nodes appear in red and affected nodes in orange.

```python
mermaid = MermaidGenerator(gm)
mermaid.save("graph.mermaid", title="My Design", change_report=report)
```

### Excel Reports

The generated `.xlsx` workbook contains six sheets: Node Registry, Edge Mappings, Adjacency Matrix, Domain Summary, Constraint Mappings (the field-level IP-XACT ↔ SDC/UPF/CDC detail table), and Impact Report.

```python
excel = ExcelReportGenerator(gm)
excel.generate("report.xlsx", change_report=change_report)
```

---

## CI/CD Integration

```python
from ipxact_graph import GraphManager, MappingValidator, ChangeDetector

gm = GraphManager.load("dependency_graph.json")

# Gate: fail the pipeline if mappings are incomplete
validator = MappingValidator(gm)
report = validator.validate()
if not report.is_valid:
    report.print_report()
    exit(1)

# Warn: flag undocumented file changes
detector = ChangeDetector(gm, baseline_path="hash_baseline.json")
cr = detector.full_scan()
if cr.changed_files:
    cr.save("change_report.json")
    print(f"{len(cr.changed_files)} files changed, "
          f"{len(cr.affected_node_ids)} artifacts affected")
```

---

## Extending the System

To add a new artifact type:

1. Add a `NodeType` enum value in `models.py`.
2. Add its colour to `NODE_COLOURS` and its Mermaid shape to `MERMAID_SHAPES`.
3. Add entries to `IPXACT_ELEMENT_EXPECTED_OUTPUTS` if IP-XACT elements should produce this artifact.
4. Add entries to `EXPECTED_MAPPING_FIELDS` to define what fields the edges to/from this artifact must contain.
5. Optionally add a coverage check entry in `MappingValidator._level3_element_coverage`.

See [SYSTEM_DOCUMENTATION.md](SYSTEM_DOCUMENTATION.md) for the full module reference, API details, and design rationale.

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `networkx` ≥2.6 | Graph engine (BFS, cycle detection, topological sort) |
| `openpyxl` ≥3.0 | Excel workbook generation |
| Python ≥3.10 | Dataclasses, type hint syntax |

---

## License

Internal / Proprietary. For use within the organisation's ASIC design consulting workflows.
