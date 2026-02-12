# IP-XACT Dependency Graph Tracker

## System Documentation for Engineers

---

## 1. Purpose and Problem Statement

In an ASIC design consulting company, customer designs (often delivered as FPGA RTL) must be translated into reusable IP-XACT packages. This translation produces a cascade of downstream artifacts: SDC timing constraints, UPF power intent, RTL wrappers with standardised port names, register models, verification collateral, physical design constraints, and EDA tool scripts for vendors like Synopsys, Cadence, and Siemens.

Each of these artifacts references or derives from the IP-XACT source of truth, but they are maintained as separate files consumed by different tools in different domains. When any artifact changes — a clock frequency shifts, a port gets renamed, a power domain boundary moves — the change must propagate accurately to every dependent file. In practice, this propagation is done manually, and that is where human error enters.

This system solves that problem. It models the entire IP-XACT ecosystem as a directed dependency graph, provides SHA-256-based change detection on every file, traces downstream impact via BFS traversal, and — most critically — validates that all required mappings between artifacts actually exist and are complete at the field level. If someone adds a new clock to the IP-XACT component but forgets to add the corresponding `create_clock` in the SDC, the validator catches it before the design reaches the EDA tool.

---

## 2. Architecture Overview

The system is a Python package (`ipxact_graph`) composed of six modules. It uses NetworkX as the underlying graph engine, openpyxl for Excel report generation, and pure string construction for Mermaid diagram output.

```
ipxact_dep_tracker/
├── demo.py                          # Runnable demo with realistic AES-GCM scenario
├── ipxact_graph/
│   ├── __init__.py                  # Public API exports
│   ├── models.py                    # Data models, enums, mapping schemas
│   ├── graph_manager.py             # Graph CRUD, serialisation, queries
│   ├── change_detector.py           # File hashing, change detection, BFS propagation
│   ├── mapping_validator.py         # 3-level completeness validation
│   ├── visualize_mermaid.py         # Mermaid diagram generation
│   └── visualize_excel.py           # Multi-sheet Excel report generation
├── sample_artifacts/                # Created by demo.py at runtime
└── output/                          # Generated reports, diagrams, JSON exports
```

The data flow through the system follows this sequence:

1. Engineers register artifacts as **nodes** and their dependencies as **edges** in the graph, including detailed `mapping_details` on each edge.
2. The **`MappingValidator`** audits whether all expected edges and fields exist.
3. The **`ChangeDetector`** hashes every registered file and stores a baseline.
4. On subsequent scans, the detector compares current hashes to the baseline and runs BFS to identify all downstream (and optionally upstream) affected artifacts.
5. The **`MermaidGenerator`** and **`ExcelReportGenerator`** produce visual and tabular outputs for human review.

---

## 3. Module Reference

### 3.1 `models.py` — Data Models and Mapping Schemas

This module defines the vocabulary of the entire system. Everything else builds on these types.

#### 3.1.1 NodeType Enum

Every artifact in the ASIC design flow is classified into one of 30 node types, grouped into seven categories:

| Category | Node Types | Description |
|----------|-----------|-------------|
| **IP-XACT Core** | `IPXACT_COMPONENT`, `IPXACT_DESIGN`, `IPXACT_DESIGN_CONFIG`, `IPXACT_ABSTRACTION_DEF`, `IPXACT_CATALOG`, `IPXACT_GENERATOR_CHAIN` | The IEEE 1685 XML files that serve as the source of truth. Components define ports, bus interfaces, parameters, memory maps, and registers. Designs define instances and interconnections. Abstraction definitions describe bus protocols (AXI, AHB, APB). |
| **Constraints** | `SDC_CONSTRAINT`, `UPF_POWER`, `CDC_CONSTRAINT`, `RESET_SCHEME` | Tool-consumed constraint files. SDC holds clock definitions, I/O delays, false paths, multicycle paths, and clock groups. UPF holds power domains, supply nets, isolation, retention, and level shifters. CDC holds clock domain crossing rules. Reset schemes document reset trees and deassertion timing. |
| **RTL & Source** | `RTL_SOURCE`, `FPGA_SOURCE`, `RTL_WRAPPER`, `RTL_FILELIST` | The customer's original FPGA code (`FPGA_SOURCE`), the generated RTL wrapper with standardised port names (`RTL_WRAPPER`), and filelist manifests for synthesis and simulation tools. |
| **Register / Memory** | `REGISTER_MAP`, `UVM_RAL_MODEL`, `C_HEADER`, `REGISTER_DOC`, `MEMORY_MAP`, `LINKER_SCRIPT`, `ADDRESS_DECODE` | The complete register generation chain. A single IP-XACT memory map definition must produce a register map, a UVM RAL model for verification, C headers for firmware, human-readable register documentation, a memory map for SoC integration, a linker script, and address decode logic. |
| **Verification** | `BUS_VIP_CONFIG`, `PROTOCOL_CHECKER`, `TESTBENCH_TOP` | Verification IP configuration files derived from bus interface and abstraction definitions. |
| **Physical Design** | `PIN_MAPPING`, `FLOORPLAN_CONSTRAINT`, `IO_PAD_CONFIG`, `DEF_LEF_CONSTRAINT` | Physical design inputs derived from port lists, hierarchy, and component parameters. |
| **EDA / Config / Docs** | `EDA_SCRIPT`, `VENDOR_EXTENSION`, `CONFIG_PARAM`, `DOCUMENTATION` | Tool-specific scripts, design parameter files, and interface specification documents. |

#### 3.1.2 EdgeType Enum

Directed edges between nodes carry one of nine relationship types:

| Edge Type | Arrow in Mermaid | Meaning |
|-----------|-----------------|---------|
| `GENERATES` | `-->` | Source produces target (e.g., IP-XACT component → SDC constraints) |
| `CONSTRAINS` | `-.->` | Source imposes rules on target (e.g., main SDC → DFT SDC must be consistent) |
| `REFERENCES` | `-->` | Source references target by name (e.g., component referenced by design) |
| `MAPS_TO` | `<-->` | Bidirectional field-level mapping between artifacts |
| `DERIVES_FROM` | `==>` | Target was derived/translated from source (e.g., customer FPGA → IP-XACT) |
| `CONFIGURES` | `-.->` | Source configures target's behaviour (e.g., SDC sourced by DC compile script) |
| `INSTANTIATES` | `-->` | Source instantiates target (e.g., design instantiates component) |
| `ABSTRACTS` | `-.->` | Source provides abstraction for target |
| `VALIDATES` | `-.->` | Source validates or checks target |

#### 3.1.3 Domain Enum

Each node and edge belongs to a domain, which corresponds to the engineering team or design stage responsible for it:

`FRONTEND`, `VERIFICATION`, `DFT`, `PHYSICAL_DESIGN`, `SIGNOFF`, `FPGA_TRANSLATION`, `FIRMWARE`, `GLOBAL`.

Domains are used for filtering in Mermaid diagrams and for the domain summary sheet in Excel reports.

#### 3.1.4 ArtifactNode Dataclass

Each node in the graph is an instance of `ArtifactNode`:

```python
ArtifactNode(
    node_id="sdc_main",                     # Unique identifier
    name="AES-GCM Main SDC",                # Human-readable name
    node_type=NodeType.SDC_CONSTRAINT,       # Classification
    domain=Domain.FRONTEND,                  # Owning domain
    file_path="/path/to/constraints.sdc",    # Filesystem path for hashing
    description="Primary timing constraints",
    eda_tool="synopsys_dc",                  # Which tool consumes this
    version="1.0",
    tags=["sdc", "timing"],
    metadata={},                             # Arbitrary key-value store
    defined_elements={                       # CRITICAL for validation
        "clocks": ["i_clk", "i_clk_aux"],
        "resets": ["i_rst_n"],
        "bus_interfaces": ["axi_slave"],
        "memory_maps": ["reg_block_0"],
        "ports": ["i_clk", "i_clk_aux", "i_rst_n", ...],
        "power_domains": ["PD_AES"],
    }
)
```

The `defined_elements` dictionary is the critical input for the mapping validator. It tells the system what the IP-XACT component actually contains, so the validator can check whether all those elements have corresponding mappings to downstream artifacts. If this dictionary is empty, the validator raises a warning that it cannot perform structural or element coverage checks.

The `compute_hash()` method calculates a SHA-256 hash of the file at `file_path`, used by the change detector for baseline comparison.

#### 3.1.5 DependencyEdge Dataclass

Each edge carries a `mapping_details` list — this is where the actual field-level relationships are recorded:

```python
DependencyEdge(
    source_id="ipxact_comp",
    target_id="sdc_main",
    edge_type=EdgeType.GENERATES,
    label="Generates timing constraints",
    domain=Domain.FRONTEND,
    mapping_details=[
        {
            "category": "clock_domain",          # Which mapping schema this satisfies
            "ipxact_clock_port": "i_clk",        # Source field in IP-XACT
            "sdc_clock_name": "clk_main",        # Target field in SDC
            "period_ns": 10.0,
            "uncertainty_setup": 0.3,
            "uncertainty_hold": 0.1,
        },
        {
            "category": "io_timing",
            "ipxact_port": "i_plaintext",
            "sdc_command": "set_input_delay",
            "clock_domain": "clk_main",
            "max_delay": 3.0,
            "min_delay": 0.5,
        },
        # ... more entries
    ]
)
```

Each entry in `mapping_details` must include a `category` field that matches one of the `MappingCategory` enum values. The validator uses this category to look up the required fields from the `EXPECTED_MAPPING_FIELDS` schema dictionary.

#### 3.1.6 EXPECTED_MAPPING_FIELDS — The Validation Schema

This is a dictionary keyed by `(source_NodeType, target_NodeType)` tuples. For each pair, it defines a list of mapping categories that must be present in the edge's `mapping_details`, along with the required fields for each category.

For example, the schema for `(IPXACT_COMPONENT, SDC_CONSTRAINT)` requires:

| Category | Required Fields | Conditional? |
|----------|----------------|-------------|
| `clock_domain` | `ipxact_clock_port`, `sdc_clock_name`, `period_ns`, `uncertainty_setup`, `uncertainty_hold` | No — always required |
| `io_timing` | `ipxact_port`, `sdc_command`, `clock_domain`, `max_delay`, `min_delay` | No |
| `false_path` | `ipxact_port_or_domain`, `sdc_false_path_spec` | No |
| `clock_group` | `group_name`, `clock_list`, `relationship` | Yes — only if >1 clock domain |
| `multicycle_path` | `from_signal`, `to_signal`, `multiplier`, `clock_domain` | Yes |

The full schema covers 16 source→target type pairs across the entire design flow. When you add new artifact types to your flow, you extend this dictionary to define what completeness means for those pairs.

#### 3.1.7 IPXACT_ELEMENT_EXPECTED_OUTPUTS — Structural Expectations

This dictionary maps each IP-XACT sub-element category (clocks, resets, bus interfaces, memory maps, ports, power domains, top-level ports) to the downstream node types that must exist:

| Element Key | Must Have Edges To |
|-------------|-------------------|
| `clocks` | `SDC_CONSTRAINT`, `CDC_CONSTRAINT` (if >1 clock) |
| `resets` | `RESET_SCHEME`, `SDC_CONSTRAINT` |
| `bus_interfaces` | `BUS_VIP_CONFIG`, `RTL_WRAPPER` |
| `memory_maps` | `REGISTER_MAP`, `UVM_RAL_MODEL`, `C_HEADER`, `REGISTER_DOC`, `ADDRESS_DECODE` |
| `ports` | `RTL_WRAPPER`, `RTL_FILELIST`, `DOCUMENTATION` |
| `power_domains` | `UPF_POWER` |
| `top_level_ports` | `PIN_MAPPING` |

This is the Level 1 structural check. If your IP-XACT component defines clocks but has no edge to any SDC constraint node, that is a FAIL.

---

### 3.2 `graph_manager.py` — Graph CRUD and Serialisation

The `GraphManager` class wraps a NetworkX `DiGraph` and provides a typed Python API over it.

#### Key Operations

| Method | Purpose |
|--------|---------|
| `add_node(node)` | Register an artifact. Raises `ValueError` if the `node_id` already exists. |
| `update_node(node)` | Replace an existing node's metadata. |
| `remove_node(node_id)` | Delete a node and all its connected edges. |
| `get_node(node_id)` | Retrieve an `ArtifactNode` by ID. |
| `get_nodes_by_type(ntype)` | Filter nodes by `NodeType`. |
| `get_nodes_by_domain(domain)` | Filter nodes by `Domain`. |
| `add_edge(edge)` | Register a dependency. Validates that both source and target nodes exist. |
| `remove_edge(source, target, type)` | Delete a specific edge. |
| `get_edges_from(node_id)` | All outgoing edges from a node. |
| `get_edges_to(node_id)` | All incoming edges to a node. |
| `predecessors(node_id)` | Nodes that feed into this node. |
| `successors(node_id)` | Nodes that depend on this node. |
| `has_cycle()` | Returns `True` if the graph contains cycles (should be `False` for valid dependency graphs). |
| `topological_order()` | Returns nodes in dependency order. Raises `RuntimeError` if cycles exist. |
| `shortest_path(source, target)` | Find the shortest dependency path between two nodes. Returns `[]` if no path exists. |
| `save(path)` | Serialise to JSON. |
| `GraphManager.load(path)` | Class method to reconstruct a graph from a saved JSON file. |
| `summary()` | Returns a dict with total counts, cycle status, and breakdowns by type and domain. |

#### Serialisation Format

The graph persists as a JSON file with two top-level arrays: `nodes` and `edges`. This file can and should be version-controlled alongside the design repository. When an engineer opens a new terminal or CI job, they call `GraphManager.load("dependency_graph.json")` to restore the full graph state.

---

### 3.3 `change_detector.py` — File Hashing and Impact Propagation

This module answers two questions: "What files changed?" and "What else is affected by those changes?"

#### Workflow

1. **Build a baseline.** On first run (or after a known-good state), call `detector.build_baseline()`. This computes SHA-256 hashes of every node that has a `file_path` and stores the result as a JSON dictionary mapping `node_id → hash`.

2. **Detect changes.** On subsequent runs, call `detector.detect_changes()`. This recomputes hashes and compares against the stored baseline. Each changed file gets a status:
   - `modified` — File exists and hash differs from baseline.
   - `added` — File exists but has no baseline hash (new artifact).
   - `missing` — Baseline hash exists but file is gone.

3. **Propagate impact.** Call `detector.propagate_impact(changed_ids)` or use the convenience method `detector.full_scan()` which does both steps. The propagation uses breadth-first search (BFS) starting from each changed node, traversing outgoing edges (downstream dependencies). For each affected node, it records the full path and edge types traversed, producing an `ImpactChain`:

```
ImpactChain(
    source_node_id = "sdc_main",        # The file that changed
    affected_node_id = "sdc_dft",       # A downstream artifact
    path = ["sdc_main", "sdc_dft"],     # Traversal path
    edge_types = ["constrains"],        # Edge types along the path
    depth = 1                           # Hops from source
)
```

4. **Optional upstream propagation.** Pass `include_upstream=True` to also trace backwards through predecessors. This answers "what upstream sources might have caused this change?"

#### ChangeReport

The `full_scan()` method returns a `ChangeReport` containing:
- `changed_files` — List of `ChangedFile` objects with old/new hashes and status.
- `impact_chains` — List of `ImpactChain` objects showing propagation paths.
- `affected_node_ids` — Set of all node IDs affected by the changes.

The report can be saved to JSON and is also consumed by the Mermaid and Excel generators for impact-highlighted visualisations.

---

### 3.4 `mapping_validator.py` — Completeness Enforcement

This is the module that prevents human error. It performs three levels of validation against the schemas defined in `models.py`.

#### Level 1 — Structural Completeness

For every IP-XACT node that has a `defined_elements` dictionary, the validator checks whether outgoing edges exist to all expected downstream node types (per `IPXACT_ELEMENT_EXPECTED_OUTPUTS`).

**What it catches:** "You defined two clocks in your IP-XACT component, but there is no edge to any CDC constraint node. You need to create CDC constraints for the clock domain crossings."

**Conditional rules:** CDC constraints are only required when >1 clock is defined. The validator handles this automatically.

**If `defined_elements` is empty:** The validator emits a WARNING (not a FAIL) because it cannot perform structural checks without knowing what the component contains. Engineers should always populate this field.

#### Level 2 — Field-Level Completeness

For every edge that exists in the graph, the validator looks up the `(source_type, target_type)` pair in `EXPECTED_MAPPING_FIELDS`. For each required mapping category in the schema, it checks:

1. Does at least one `mapping_details` entry with that category exist?
2. Does every such entry contain all required fields with non-empty values?

**What it catches:** "Your IP-XACT → SDC edge has clock_domain mappings for both clocks, but the io_timing category is completely missing. You haven't defined any input/output delay constraints."

**Conditional categories:** Some categories (like `multicycle_path`, `retention_strategy`, `level_shifter`) are marked as `conditional: True` in the schema. If absent, they produce a WARNING rather than a FAIL.

**No schema defined:** If no schema exists for a given `(source_type, target_type)` pair, the validator emits an INFO message suggesting you add one. This is how you know the system needs extending when you introduce new artifact relationships.

#### Level 3 — Element Coverage

For IP-XACT nodes with `defined_elements`, the validator cross-references each individual element against the mapping entries. Specifically:

- If `defined_elements["ports"]` lists 10 ports, the `port_naming` category in the edge to `RTL_WRAPPER` must have 10 mapping entries, each with an `ipxact_port` field matching one of the 10 defined ports.
- If `defined_elements["clocks"]` lists 2 clocks, the `clock_domain` category in the edge to `SDC_CONSTRAINT` must have 2 entries with matching `ipxact_clock_port` fields.

**What it catches:** "Your IP-XACT component defines 10 ports, but only 1 has a pin_assignment mapping to the pin mapping file. 9 ports have no physical pin assignment. Missing: i_clk_aux, i_rst_n, i_plaintext, ..."

The coverage checks are defined in the `COVERAGE_CHECKS` list within the `_level3_element_coverage` method:

| Element Key | Mapping Category | Identifying Field | Checked Against Target Types |
|-------------|-----------------|-------------------|----------------------------|
| `ports` | `port_naming` | `ipxact_port` | `RTL_WRAPPER` |
| `clocks` | `clock_domain` | `ipxact_clock_port` | `SDC_CONSTRAINT` |
| `resets` | `reset_domain` | `ipxact_reset_port` | `RESET_SCHEME` |
| `bus_interfaces` | `bus_interface` | `bus_interface_name` | `BUS_VIP_CONFIG` |
| `memory_maps` | `memory_map_mapping` | `memory_map_name` | `MEMORY_MAP`, `REGISTER_MAP` |
| `power_domains` | `power_domain` | `upf_power_domain` | `UPF_POWER` |

#### ValidationReport

The validator returns a `ValidationReport` with:

- `is_valid` — `True` only if there are zero FAIL items.
- `coverage_pct` — Percentage of PASS items out of (PASS + FAIL).
- `summary()` — Structured dict with per-category coverage breakdown.
- `print_report()` — Human-readable console output with progress bars and categorised failure messages.

Severity levels:

| Severity | Meaning |
|----------|---------|
| **PASS** | Mapping exists and is complete. |
| **WARNING** | A conditional mapping is absent (may be intentional) or `defined_elements` is empty. |
| **FAIL** | A mandatory mapping is missing or incomplete. This is a potential human error. |
| **INFO** | No validation schema exists for this edge type pair. Consider adding one. |

---

### 3.5 `visualize_mermaid.py` — Diagram Generation

Generates Mermaid-syntax diagrams that can be rendered in GitHub/GitLab markdown, mermaid.live, Confluence, or any tool that supports Mermaid.

#### Features

- **Domain subgraphs.** Nodes are grouped into Mermaid `subgraph` blocks by domain (Frontend Design, Verification, DFT, Physical Design, etc.), making the organisational structure visible.
- **Node shapes by type.** IP-XACT core artifacts use subroutine shapes (`[[ ]]`), constraints use hexagons (`{{ }}`), RTL uses rectangles, register/memory uses cylinders, physical design uses asymmetric shapes.
- **Edge styles by type.** `GENERATES` uses solid arrows, `CONSTRAINS` and `CONFIGURES` use dotted arrows, `DERIVES_FROM` uses thick arrows, `MAPS_TO` uses bidirectional arrows.
- **Colour coding.** Each node type has a distinct colour from the `NODE_COLOURS` palette.
- **Change-impact highlighting.** When a `ChangeReport` is passed in, changed nodes are styled with red fill and thick borders (`changed` class), and affected downstream nodes are styled with orange fill and dashed borders (`affected` class).
- **Filtering.** Diagrams can be filtered by domain, node type, or EDA tool to produce focused views.

#### Usage

```python
mermaid = MermaidGenerator(graph_manager)

# Full graph
mermaid.save("full_graph.mermaid", title="My Design")

# Impact-highlighted
mermaid.save("impact.mermaid", title="Change Impact", change_report=report)

# Filtered to one domain
mermaid.save("dft_only.mermaid", filter_domain=Domain.DFT)

# Get as string (for embedding in other docs)
diagram_text = mermaid.generate(title="My Design")
```

---

### 3.6 `visualize_excel.py` — Workbook Report Generation

Generates a multi-sheet Excel workbook with professional formatting. This is the output format most engineers are accustomed to working with.

#### Sheets

| Sheet | Content | Purpose |
|-------|---------|---------|
| **Node Registry** | All artifacts with type, domain, EDA tool, file path, version, tags, description. Rows colour-coded by domain. | Master inventory of every file in the design flow. |
| **Edge Mappings** | Source → Target with relationship type, domain, label, and the concatenated mapping details for each edge. | Shows all dependencies at a glance. |
| **Adjacency Matrix** | N×N matrix of all nodes, with cells filled where edges exist (showing abbreviated edge type). | Quick visual check for missing connections between any two artifacts. |
| **Domain Summary** | Per-domain counts of IP-XACT files, constraint files, RTL/FPGA files, EDA scripts, configs, and docs. | High-level view of flow completeness per engineering team. |
| **Constraint Mappings** | Detailed table of every IP-XACT ↔ constraint/mapping relationship with source field, target field, clock domain, constraint type, and notes. Covers SDC, UPF, CDC, reset, pin, register, memory, bus VIP, and floorplan mappings. | The definitive reference for "which IP-XACT field maps to which constraint command." |
| **Impact Report** | (Only when a `ChangeReport` is provided.) Changed files highlighted in red with old/new hashes. Impact chains highlighted in orange showing propagation paths with depth. | Post-change review document for sign-off. |

---

## 4. How to Use the System

### 4.1 Initial Setup

```bash
pip install networkx openpyxl
```

No other dependencies are required. The system uses only Python standard library modules beyond these two packages.

### 4.2 Defining Your Design's Graph

Create a Python script (or extend `demo.py`) that registers your project's actual artifacts:

```python
from ipxact_graph import (
    ArtifactNode, DependencyEdge, NodeType, EdgeType, Domain,
    GraphManager, MappingValidator, ChangeDetector,
    MermaidGenerator, ExcelReportGenerator,
)

gm = GraphManager()

# 1. Register nodes
gm.add_node(ArtifactNode(
    node_id="ipxact_my_block",
    name="My Block IP-XACT Component",
    node_type=NodeType.IPXACT_COMPONENT,
    domain=Domain.GLOBAL,
    file_path="/project/ipxact/my_block_component.xml",
    eda_tool="ip-xact",
    defined_elements={
        "clocks": ["i_clk"],
        "resets": ["i_rst_n"],
        "ports": ["i_clk", "i_rst_n", "i_data", "o_result"],
        "bus_interfaces": [],
        "memory_maps": [],
        "power_domains": ["PD_MAIN"],
    },
))

gm.add_node(ArtifactNode(
    node_id="sdc_my_block",
    name="My Block SDC",
    node_type=NodeType.SDC_CONSTRAINT,
    domain=Domain.FRONTEND,
    file_path="/project/constraints/my_block.sdc",
    eda_tool="synopsys_dc",
))

# 2. Register edges WITH mapping details
gm.add_edge(DependencyEdge(
    source_id="ipxact_my_block",
    target_id="sdc_my_block",
    edge_type=EdgeType.GENERATES,
    label="IP-XACT generates SDC",
    mapping_details=[
        {"category": "clock_domain", "ipxact_clock_port": "i_clk",
         "sdc_clock_name": "clk_main", "period_ns": 5.0,
         "uncertainty_setup": 0.2, "uncertainty_hold": 0.05},
        {"category": "io_timing", "ipxact_port": "i_data",
         "sdc_command": "set_input_delay", "clock_domain": "clk_main",
         "max_delay": 1.5, "min_delay": 0.2},
        {"category": "false_path", "ipxact_port_or_domain": "i_rst_n",
         "sdc_false_path_spec": "set_false_path -from [get_ports i_rst_n]"},
    ],
))

# ... register all other nodes and edges

# 3. Save the graph
gm.save("dependency_graph.json")
```

### 4.3 Running Validation

```python
validator = MappingValidator(gm)
report = validator.validate()
report.print_report()     # Console output with coverage bars
report.save("validation_report.json")

if not report.is_valid:
    print("ACTION REQUIRED: Fix the failures listed above before tapeout.")
```

### 4.4 Running Change Detection

```python
detector = ChangeDetector(gm)

# First time: build baseline
detector.build_baseline()
detector.save_baseline("hash_baseline.json")

# After files have been modified:
detector = ChangeDetector(gm, baseline_path="hash_baseline.json")
change_report = detector.full_scan(include_upstream=True)
change_report.save("change_report.json")

for cf in change_report.changed_files:
    print(f"Changed: {cf.name} ({cf.status})")
for ic in change_report.impact_chains:
    print(f"Impact: {' → '.join(ic.path)}")
```

### 4.5 Generating Reports

```python
# Mermaid diagram
mermaid = MermaidGenerator(gm)
mermaid.save("graph.mermaid", title="My Design", change_report=change_report)

# Excel workbook
excel = ExcelReportGenerator(gm)
excel.generate("report.xlsx", change_report=change_report)
```

### 4.6 CI/CD Integration

The system is designed to run in automated pipelines. A typical CI stage would:

```bash
python -c "
from ipxact_graph import GraphManager, MappingValidator, ChangeDetector

gm = GraphManager.load('dependency_graph.json')

# Validate mappings
v = MappingValidator(gm)
report = v.validate()
report.print_report()
if not report.is_valid:
    exit(1)  # Fail the pipeline

# Check for undocumented changes
d = ChangeDetector(gm, baseline_path='hash_baseline.json')
cr = d.full_scan()
if cr.changed_files:
    cr.save('change_report.json')
    print(f'WARNING: {len(cr.changed_files)} files changed, '
          f'{len(cr.affected_node_ids)} affected')
"
```

---

## 5. The Demo Scenario

The included `demo.py` models a realistic scenario: a customer delivers an FPGA AES-GCM crypto accelerator, and the consulting company translates it into a full reusable IP-XACT package.

### Artifacts created (20 nodes)

Customer FPGA source → IP-XACT component → SoC design, main SDC, DFT SDC, UPF, CDC constraints, reset scheme, RTL wrapper, RTL filelist, register map → UVM RAL, C header, memory map, bus VIP config, pin mapping, floorplan constraints, DC compile script, design parameters, interface specification.

### Intentional gaps for demonstrating the validator

1. **Pin mapping incomplete** — Only 1 of 10 ports has a physical pin assignment. The validator flags 9 missing at Level 3 element coverage.
2. **C header missing IV0 register** — The register map has 4 registers but the C header edge only maps 3. The validator would catch this if the reg_map node had `defined_elements` with its register list (currently the check runs on the IP-XACT component's memory_maps, demonstrating the structural check instead).
3. **Missing structural edges** — The IP-XACT component defines `memory_maps: ["reg_block_0"]` but has no direct edge to `UVM_RAL_MODEL`, `C_HEADER`, `REGISTER_DOC`, or `ADDRESS_DECODE` (those go through the intermediate `reg_map` node). The Level 1 structural check flags these 4 missing edges.
4. **Conditional mappings absent** — No multicycle path, retention strategy, or level shifter mappings. These produce warnings, not failures, since they are marked conditional.

### Change simulation

The demo modifies the SDC clock period from 10ns to 8ns (100 MHz → 125 MHz) and shows how BFS propagation identifies 5 affected artifacts: DFT SDC, DC compile script, and traces upstream to the IP-XACT component, customer FPGA source, and config parameters.

---

## 6. Extending the System

### Adding a new artifact type

1. Add the new `NodeType` enum value in `models.py`.
2. Add a colour entry in `NODE_COLOURS`.
3. Add a Mermaid shape in `MERMAID_SHAPES` in `visualize_mermaid.py`.
4. If it should be an expected output from IP-XACT elements, add it to `IPXACT_ELEMENT_EXPECTED_OUTPUTS`.
5. If edges to/from it should carry validated mappings, add the schema to `EXPECTED_MAPPING_FIELDS`.

### Adding a new mapping schema

Add an entry to `EXPECTED_MAPPING_FIELDS` keyed by the `(source_NodeType, target_NodeType)` tuple:

```python
(NodeType.IPXACT_COMPONENT, NodeType.MY_NEW_ARTIFACT): [
    {
        "category": MappingCategory.MY_CATEGORY.value,
        "required_fields": ["field_a", "field_b", "field_c"],
        "description": "What this mapping represents",
        "conditional": False,  # True if not always required
    },
]
```

### Adding a new element coverage check

Add an entry to the `COVERAGE_CHECKS` list in `MappingValidator._level3_element_coverage`:

```python
{
    "element_key": "my_elements",                  # Key in defined_elements
    "mapping_category": MappingCategory.X.value,   # Category to search for
    "element_field_in_mapping": "my_id_field",     # Field that identifies the element
    "target_types": [NodeType.MY_TARGET],           # Target node types to check
}
```

---

## 7. Design Decisions and Rationale

**Why NetworkX instead of a database?** The graph is small enough (tens to hundreds of nodes for even a large SoC) that an in-memory graph is sufficient. NetworkX provides built-in BFS, cycle detection, topological sort, and shortest path algorithms. The JSON serialisation makes it trivially version-controllable alongside the design repo.

**Why SHA-256 for change detection?** It is deterministic, collision-resistant, and fast enough for files in the tens-of-megabytes range typical of design artifacts. The baseline file is a simple JSON dict that can be committed or stored as a CI artifact.

**Why field-level mapping validation instead of just structural checks?** Structural checks only tell you "an SDC file exists." They do not tell you whether that SDC file actually contains a `create_clock` for every clock defined in the IP-XACT component, or whether the I/O delay values reference the correct clock domain. The field-level schema catches the class of errors where someone creates the file but misses entries inside it.

**Why `defined_elements` on the node instead of parsing the IP-XACT XML?** The system is designed to work regardless of whether the actual IP-XACT XML files exist yet. During early architecture phases, engineers define what the component will contain before the XML is written. The `defined_elements` dict can be populated from a spec, from a parsed XML, or from an AI-generated analysis of customer FPGA code.

**Why both Mermaid and Excel?** Mermaid diagrams are ideal for visual communication in code reviews, wiki pages, and markdown documents. Excel is what most hardware engineers actually use day-to-day for tracking and sign-off. The adjacency matrix sheet in particular is a format that physical design and verification leads are accustomed to reviewing.

---

## 8. Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `networkx` | ≥2.6 | Graph data structure, BFS, topological sort |
| `openpyxl` | ≥3.0 | Excel workbook generation |
| Python | ≥3.10 | Type hints (`X | Y` syntax), dataclasses |

No other external dependencies. The Mermaid output is plain text with no rendering library required.
