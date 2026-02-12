"""
Expanded Data Models for Complete IP-XACT Dependency Coverage.

IEEE 1685 (IP-XACT) defines these primary artifact categories, each of which
produces downstream outputs consumed by various EDA tools across domains:

IP-XACT Core Artifacts:
  - Component        : Ports, bus interfaces, parameters, memory maps, registers
  - Design           : Instances, interconnections, hierConnections
  - DesignConfig     : View selections, generator chain configs
  - AbstractionDef   : Bus abstraction definitions (AXI, AHB, APB, etc.)
  - Catalog          : Master index referencing all IP-XACT files
  - GeneratorChain   : Tool-specific generator configurations

Downstream Outputs (what IP-XACT generates for EDA tools):
  - SDC constraints    : Clock defs, I/O delays, false paths, multicycle, clock groups
  - UPF/CPF power      : Power domains, supply nets, isolation, retention, level shifters
  - RTL wrappers       : Port-mapped wrappers with standardised naming
  - Filelists          : RTL file lists for synthesis/sim tools
  - Register models    : UVM RAL, C headers, HTML register docs
  - Memory maps        : Address decode logic, linker scripts
  - Bus VIP configs    : Protocol checker configs (AXI VIP, etc.)
  - CDC constraints    : Clock domain crossing rules (Spyglass CDC, Meridian)
  - Reset schemes      : Reset domain definitions, reset tree configs
  - Pin/Pad mapping    : DEF/LEF constraints, IO pad ring configs
  - Hierarchy/Floorplan: Block placement constraints, partition definitions
  - Vendor extensions  : Tool-specific TCL (Synopsys, Cadence, Siemens)

This module defines all node types, edge types, and critically the EXPECTED
MAPPING SCHEMAS that the validator uses to ensure nothing is missed.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import hashlib
import os
import time


# ====================================================================== #
#  Enumerations                                                            #
# ====================================================================== #

class NodeType(Enum):
    # --- IP-XACT Core ---
    IPXACT_COMPONENT = "ipxact_component"
    IPXACT_DESIGN = "ipxact_design"
    IPXACT_DESIGN_CONFIG = "ipxact_design_config"
    IPXACT_ABSTRACTION_DEF = "ipxact_abstraction_def"
    IPXACT_CATALOG = "ipxact_catalog"
    IPXACT_GENERATOR_CHAIN = "ipxact_generator_chain"

    # --- Constraint Files ---
    SDC_CONSTRAINT = "sdc_constraint"
    UPF_POWER = "upf_power"
    CDC_CONSTRAINT = "cdc_constraint"
    RESET_SCHEME = "reset_scheme"

    # --- RTL & Source ---
    RTL_SOURCE = "rtl_source"
    FPGA_SOURCE = "fpga_source"
    RTL_WRAPPER = "rtl_wrapper"
    RTL_FILELIST = "rtl_filelist"

    # --- Register / Memory ---
    REGISTER_MAP = "register_map"
    UVM_RAL_MODEL = "uvm_ral_model"
    C_HEADER = "c_header"
    REGISTER_DOC = "register_doc"
    MEMORY_MAP = "memory_map"
    LINKER_SCRIPT = "linker_script"
    ADDRESS_DECODE = "address_decode"

    # --- Verification ---
    BUS_VIP_CONFIG = "bus_vip_config"
    PROTOCOL_CHECKER = "protocol_checker"
    TESTBENCH_TOP = "testbench_top"

    # --- Physical Design ---
    PIN_MAPPING = "pin_mapping"
    FLOORPLAN_CONSTRAINT = "floorplan_constraint"
    IO_PAD_CONFIG = "io_pad_config"
    DEF_LEF_CONSTRAINT = "def_lef_constraint"

    # --- EDA Tool Scripts ---
    EDA_SCRIPT = "eda_script"
    VENDOR_EXTENSION = "vendor_extension"

    # --- Configuration & Documentation ---
    CONFIG_PARAM = "config_param"
    DOCUMENTATION = "documentation"


class EdgeType(Enum):
    GENERATES = "generates"
    CONSTRAINS = "constrains"
    REFERENCES = "references"
    MAPS_TO = "maps_to"
    DERIVES_FROM = "derives_from"
    CONFIGURES = "configures"
    INSTANTIATES = "instantiates"
    ABSTRACTS = "abstracts"
    VALIDATES = "validates"


class Domain(Enum):
    FRONTEND = "frontend_design"
    VERIFICATION = "verification"
    DFT = "dft"
    PHYSICAL_DESIGN = "physical_design"
    SIGNOFF = "signoff"
    FPGA_TRANSLATION = "fpga_translation"
    FIRMWARE = "firmware"
    GLOBAL = "global"


class MappingCategory(Enum):
    """Categories of mappings that must exist for completeness."""
    PORT_NAMING = "port_naming"
    CLOCK_DOMAIN = "clock_domain"
    CLOCK_CONSTRAINT = "clock_constraint"
    RESET_DOMAIN = "reset_domain"
    RESET_CONSTRAINT = "reset_constraint"
    IO_TIMING = "io_timing"
    FALSE_PATH = "false_path"
    MULTICYCLE_PATH = "multicycle_path"
    CLOCK_GROUP = "clock_group"
    POWER_DOMAIN = "power_domain"
    ISOLATION_STRATEGY = "isolation_strategy"
    RETENTION_STRATEGY = "retention_strategy"
    LEVEL_SHIFTER = "level_shifter"
    BUS_INTERFACE = "bus_interface"
    MEMORY_MAP = "memory_map_mapping"
    REGISTER_BLOCK = "register_block"
    ADDRESS_SPACE = "address_space"
    CDC_CROSSING = "cdc_crossing"
    PIN_ASSIGNMENT = "pin_assignment"
    HIERARCHY_MAPPING = "hierarchy_mapping"
    FILELIST_ENTRY = "filelist_entry"


# ====================================================================== #
#  Expected Mapping Schemas                                                #
# ====================================================================== #
# These define what fields MUST be present in mapping_details for each
# combination of source -> target node types. The validator uses these
# to flag incomplete mappings.

EXPECTED_MAPPING_FIELDS: dict[tuple[NodeType, NodeType], list[dict]] = {

    # ── IP-XACT Component → SDC ─────────────────────────────────────── #
    (NodeType.IPXACT_COMPONENT, NodeType.SDC_CONSTRAINT): [
        {
            "category": MappingCategory.CLOCK_DOMAIN.value,
            "required_fields": ["ipxact_clock_port", "sdc_clock_name", "period_ns",
                                "uncertainty_setup", "uncertainty_hold"],
            "description": "Each IP-XACT clock port must map to a create_clock command",
        },
        {
            "category": MappingCategory.IO_TIMING.value,
            "required_fields": ["ipxact_port", "sdc_command", "clock_domain",
                                "max_delay", "min_delay"],
            "description": "Each I/O port must have input/output delay constraints",
        },
        {
            "category": MappingCategory.FALSE_PATH.value,
            "required_fields": ["ipxact_port_or_domain", "sdc_false_path_spec"],
            "description": "Async signals (resets, async inputs) must have false paths",
        },
        {
            "category": MappingCategory.CLOCK_GROUP.value,
            "required_fields": ["group_name", "clock_list", "relationship"],
            "description": "Multiple clocks must define clock groups (async/exclusive)",
            "conditional": True,  # Only required if >1 clock domain
        },
        {
            "category": MappingCategory.MULTICYCLE_PATH.value,
            "required_fields": ["from_signal", "to_signal", "multiplier", "clock_domain"],
            "description": "Multicycle paths between slow/fast domains",
            "conditional": True,
        },
    ],

    # ── IP-XACT Component → UPF ─────────────────────────────────────── #
    (NodeType.IPXACT_COMPONENT, NodeType.UPF_POWER): [
        {
            "category": MappingCategory.POWER_DOMAIN.value,
            "required_fields": ["ipxact_component", "upf_power_domain",
                                "supply_net_vdd", "supply_net_vss"],
            "description": "Each component must map to a power domain",
        },
        {
            "category": MappingCategory.ISOLATION_STRATEGY.value,
            "required_fields": ["power_domain", "isolation_signal",
                                "isolation_sense", "isolation_location"],
            "description": "Isolation cells for domain boundaries",
            "conditional": True,
        },
        {
            "category": MappingCategory.RETENTION_STRATEGY.value,
            "required_fields": ["power_domain", "retention_signal",
                                "retention_registers"],
            "description": "Retention strategy for power-gated domains",
            "conditional": True,
        },
        {
            "category": MappingCategory.LEVEL_SHIFTER.value,
            "required_fields": ["from_domain", "to_domain", "shifter_type"],
            "description": "Level shifters between voltage domains",
            "conditional": True,
        },
    ],

    # ── IP-XACT Component → RTL Wrapper ──────────────────────────────── #
    (NodeType.IPXACT_COMPONENT, NodeType.RTL_WRAPPER): [
        {
            "category": MappingCategory.PORT_NAMING.value,
            "required_fields": ["ipxact_port", "rtl_port", "direction", "width"],
            "description": "Every IP-XACT port must map to an RTL port",
        },
    ],

    # ── IP-XACT Component → CDC ──────────────────────────────────────── #
    (NodeType.IPXACT_COMPONENT, NodeType.CDC_CONSTRAINT): [
        {
            "category": MappingCategory.CDC_CROSSING.value,
            "required_fields": ["from_clock_domain", "to_clock_domain",
                                "crossing_type", "sync_scheme"],
            "description": "Each clock domain crossing must be documented",
        },
    ],

    # ── IP-XACT Component → Reset Scheme ─────────────────────────────── #
    (NodeType.IPXACT_COMPONENT, NodeType.RESET_SCHEME): [
        {
            "category": MappingCategory.RESET_DOMAIN.value,
            "required_fields": ["ipxact_reset_port", "reset_domain",
                                "polarity", "sync_async"],
            "description": "Each reset port maps to a reset domain",
        },
        {
            "category": MappingCategory.RESET_CONSTRAINT.value,
            "required_fields": ["reset_domain", "associated_clock",
                                "deassert_timing"],
            "description": "Reset deassertion timing relative to clock",
        },
    ],

    # ── IP-XACT Component → Register Map ─────────────────────────────── #
    (NodeType.IPXACT_COMPONENT, NodeType.REGISTER_MAP): [
        {
            "category": MappingCategory.REGISTER_BLOCK.value,
            "required_fields": ["register_name", "offset", "width",
                                "access_type", "reset_value"],
            "description": "Each register in IP-XACT memory map must be mapped",
        },
        {
            "category": MappingCategory.ADDRESS_SPACE.value,
            "required_fields": ["address_space_name", "base_address",
                                "range", "bus_interface"],
            "description": "Address space mapping for each bus interface",
        },
    ],

    # ── Register Map → UVM RAL ───────────────────────────────────────── #
    (NodeType.REGISTER_MAP, NodeType.UVM_RAL_MODEL): [
        {
            "category": MappingCategory.REGISTER_BLOCK.value,
            "required_fields": ["register_name", "ral_class_name",
                                "access_type", "field_list"],
            "description": "Each register must produce a RAL register class",
        },
    ],

    # ── Register Map → C Header ──────────────────────────────────────── #
    (NodeType.REGISTER_MAP, NodeType.C_HEADER): [
        {
            "category": MappingCategory.REGISTER_BLOCK.value,
            "required_fields": ["register_name", "c_define_name",
                                "offset_hex", "field_masks"],
            "description": "Each register must produce C #defines",
        },
    ],

    # ── IP-XACT Component → Pin Mapping ──────────────────────────────── #
    (NodeType.IPXACT_COMPONENT, NodeType.PIN_MAPPING): [
        {
            "category": MappingCategory.PIN_ASSIGNMENT.value,
            "required_fields": ["ipxact_port", "physical_pin",
                                "pad_type", "io_standard"],
            "description": "Each top-level port must map to a physical pin",
        },
    ],

    # ── IP-XACT Design → Floorplan ───────────────────────────────────── #
    (NodeType.IPXACT_DESIGN, NodeType.FLOORPLAN_CONSTRAINT): [
        {
            "category": MappingCategory.HIERARCHY_MAPPING.value,
            "required_fields": ["instance_name", "component_ref",
                                "placement_region", "area_estimate"],
            "description": "Each instance must have placement constraints",
        },
    ],

    # ── IP-XACT Component → Filelist ─────────────────────────────────── #
    (NodeType.IPXACT_COMPONENT, NodeType.RTL_FILELIST): [
        {
            "category": MappingCategory.FILELIST_ENTRY.value,
            "required_fields": ["file_path", "file_type", "compile_order"],
            "description": "All source files listed with correct compile order",
        },
    ],

    # ── IP-XACT Component → Bus VIP Config ───────────────────────────── #
    (NodeType.IPXACT_COMPONENT, NodeType.BUS_VIP_CONFIG): [
        {
            "category": MappingCategory.BUS_INTERFACE.value,
            "required_fields": ["bus_interface_name", "protocol",
                                "vip_type", "config_params"],
            "description": "Each bus interface must have VIP configuration",
        },
    ],

    # ── FPGA Source → IP-XACT Component (customer translation) ────────── #
    (NodeType.FPGA_SOURCE, NodeType.IPXACT_COMPONENT): [
        {
            "category": MappingCategory.PORT_NAMING.value,
            "required_fields": ["customer_port", "ipxact_port",
                                "direction", "width", "rename_reason"],
            "description": "Each customer FPGA port maps to standardised IP-XACT port",
        },
        {
            "category": MappingCategory.CLOCK_DOMAIN.value,
            "required_fields": ["customer_clock", "ipxact_clock_domain",
                                "frequency_mhz"],
            "description": "Customer clock signals map to IP-XACT clock domains",
        },
        {
            "category": MappingCategory.RESET_DOMAIN.value,
            "required_fields": ["customer_reset", "ipxact_reset_domain",
                                "polarity"],
            "description": "Customer reset signals map to IP-XACT reset domains",
        },
    ],

    # ── SDC → SDC (cross-domain constraint consistency) ───────────────── #
    (NodeType.SDC_CONSTRAINT, NodeType.SDC_CONSTRAINT): [
        {
            "category": MappingCategory.CLOCK_DOMAIN.value,
            "required_fields": ["source_clock", "target_clock_reference",
                                "false_path_defined"],
            "description": "DFT/signoff SDC must reference same clocks as main SDC",
        },
    ],

    # ── IP-XACT Component → Memory Map ───────────────────────────────── #
    (NodeType.IPXACT_COMPONENT, NodeType.MEMORY_MAP): [
        {
            "category": MappingCategory.MEMORY_MAP.value,
            "required_fields": ["memory_map_name", "address_block",
                                "base_address", "range"],
            "description": "Each IP-XACT memory map must produce decode logic",
        },
    ],

    # ── Memory Map → Address Decode ──────────────────────────────────── #
    (NodeType.MEMORY_MAP, NodeType.ADDRESS_DECODE): [
        {
            "category": MappingCategory.ADDRESS_SPACE.value,
            "required_fields": ["address_block", "decode_select_signal",
                                "base_address", "range"],
            "description": "Each address block needs decode logic",
        },
    ],

    # ── Memory Map → Linker Script ───────────────────────────────────── #
    (NodeType.MEMORY_MAP, NodeType.LINKER_SCRIPT): [
        {
            "category": MappingCategory.ADDRESS_SPACE.value,
            "required_fields": ["memory_region", "origin_address",
                                "length", "access_permissions"],
            "description": "Each memory region maps to linker MEMORY section",
        },
    ],

    # ── IP-XACT Abstraction Def → Bus VIP Config ────────────────────── #
    (NodeType.IPXACT_ABSTRACTION_DEF, NodeType.BUS_VIP_CONFIG): [
        {
            "category": MappingCategory.BUS_INTERFACE.value,
            "required_fields": ["abstraction_name", "protocol_version",
                                "port_map_list", "vip_parameter_overrides"],
            "description": "Abstraction definition drives VIP parameterisation",
        },
    ],

    # ── IP-XACT Abstraction Def → Protocol Checker ───────────────────── #
    (NodeType.IPXACT_ABSTRACTION_DEF, NodeType.PROTOCOL_CHECKER): [
        {
            "category": MappingCategory.BUS_INTERFACE.value,
            "required_fields": ["abstraction_name", "checker_rules",
                                "port_connections"],
            "description": "Abstraction drives protocol checker configuration",
        },
    ],
}


# ====================================================================== #
#  Which downstream outputs are expected from each IP-XACT element type    #
# ====================================================================== #
# The validator uses this to check: "You have clocks defined in your
# IP-XACT component, but no edge to an SDC file – that's a gap."

IPXACT_ELEMENT_EXPECTED_OUTPUTS: dict[str, list[NodeType]] = {
    "clocks": [
        NodeType.SDC_CONSTRAINT,     # Must generate clock constraints
        NodeType.CDC_CONSTRAINT,     # If >1 clock: must have CDC rules
    ],
    "resets": [
        NodeType.RESET_SCHEME,       # Must document reset domains
        NodeType.SDC_CONSTRAINT,     # Must have false paths for async resets
    ],
    "bus_interfaces": [
        NodeType.BUS_VIP_CONFIG,     # Must have VIP config for verification
        NodeType.RTL_WRAPPER,        # Must have port mapping in wrapper
    ],
    "memory_maps": [
        NodeType.REGISTER_MAP,       # Must produce register map
        NodeType.UVM_RAL_MODEL,      # Must produce UVM RAL
        NodeType.C_HEADER,           # Must produce firmware headers
        NodeType.REGISTER_DOC,       # Must produce human-readable docs
        NodeType.ADDRESS_DECODE,     # Must produce address decode logic
    ],
    "ports": [
        NodeType.RTL_WRAPPER,        # Must have RTL wrapper
        NodeType.RTL_FILELIST,       # Must be in filelist
        NodeType.DOCUMENTATION,      # Must be documented
    ],
    "power_domains": [
        NodeType.UPF_POWER,          # Must generate UPF
    ],
    "top_level_ports": [
        NodeType.PIN_MAPPING,        # Must map to physical pins (for top-level)
    ],
}


# ====================================================================== #
#  Node colour palette (for visualisation)                                 #
# ====================================================================== #

NODE_COLOURS = {
    NodeType.IPXACT_COMPONENT: "#4A90D9",
    NodeType.IPXACT_DESIGN: "#357ABD",
    NodeType.IPXACT_DESIGN_CONFIG: "#2E86C1",
    NodeType.IPXACT_ABSTRACTION_DEF: "#5DADE2",
    NodeType.IPXACT_CATALOG: "#85C1E9",
    NodeType.IPXACT_GENERATOR_CHAIN: "#AED6F1",
    NodeType.SDC_CONSTRAINT: "#E67E22",
    NodeType.UPF_POWER: "#8E44AD",
    NodeType.CDC_CONSTRAINT: "#AF7AC5",
    NodeType.RESET_SCHEME: "#BB8FCE",
    NodeType.RTL_SOURCE: "#27AE60",
    NodeType.FPGA_SOURCE: "#16A085",
    NodeType.RTL_WRAPPER: "#2ECC71",
    NodeType.RTL_FILELIST: "#82E0AA",
    NodeType.REGISTER_MAP: "#D4AC0D",
    NodeType.UVM_RAL_MODEL: "#F4D03F",
    NodeType.C_HEADER: "#F9E79F",
    NodeType.REGISTER_DOC: "#FCF3CF",
    NodeType.MEMORY_MAP: "#D5B60A",
    NodeType.LINKER_SCRIPT: "#B7950B",
    NodeType.ADDRESS_DECODE: "#9A7D0A",
    NodeType.BUS_VIP_CONFIG: "#1ABC9C",
    NodeType.PROTOCOL_CHECKER: "#48C9B0",
    NodeType.TESTBENCH_TOP: "#76D7C4",
    NodeType.PIN_MAPPING: "#EC7063",
    NodeType.FLOORPLAN_CONSTRAINT: "#E74C3C",
    NodeType.IO_PAD_CONFIG: "#F1948A",
    NodeType.DEF_LEF_CONSTRAINT: "#D98880",
    NodeType.EDA_SCRIPT: "#C0392B",
    NodeType.VENDOR_EXTENSION: "#922B21",
    NodeType.CONFIG_PARAM: "#F39C12",
    NodeType.DOCUMENTATION: "#7F8C8D",
}

EDGE_COLOURS = {
    EdgeType.GENERATES: "#E74C3C",
    EdgeType.CONSTRAINS: "#E67E22",
    EdgeType.REFERENCES: "#3498DB",
    EdgeType.MAPS_TO: "#2ECC71",
    EdgeType.DERIVES_FROM: "#9B59B6",
    EdgeType.CONFIGURES: "#F1C40F",
    EdgeType.INSTANTIATES: "#1ABC9C",
    EdgeType.ABSTRACTS: "#85C1E9",
    EdgeType.VALIDATES: "#48C9B0",
}


# ====================================================================== #
#  Data classes                                                            #
# ====================================================================== #

@dataclass
class ArtifactNode:
    """Represents a single file/artifact in the ASIC design flow."""
    node_id: str
    name: str
    node_type: NodeType
    domain: Domain
    file_path: Optional[str] = None
    description: str = ""
    eda_tool: str = ""
    version: str = "1.0"
    tags: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    # For IP-XACT components: track which sub-elements are defined
    # so the validator can check if all expected outputs exist.
    # e.g. {"clocks": ["i_clk", "i_clk_scan"],
    #        "resets": ["i_rst_n"],
    #        "bus_interfaces": ["axi_slave"],
    #        "memory_maps": ["reg_block_0"],
    #        "ports": [...],
    #        "power_domains": ["PD_AES"],
    #        "top_level_ports": ["i_clk", ...]}
    defined_elements: dict = field(default_factory=dict)

    _file_hash: Optional[str] = field(default=None, repr=False)
    _last_checked: Optional[float] = field(default=None, repr=False)

    def compute_hash(self) -> Optional[str]:
        if not self.file_path or not os.path.isfile(self.file_path):
            return None
        try:
            h = hashlib.sha256()
            with open(self.file_path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
            self._file_hash = h.hexdigest()
            self._last_checked = time.time()
            return self._file_hash
        except OSError as e:
            raise RuntimeError(f"Cannot hash file {self.file_path}: {e}") from e

    @property
    def short_label(self) -> str:
        return f"{self.name}\n({self.node_type.value})"


@dataclass
class DependencyEdge:
    """Represents a directed dependency between two artifacts."""
    source_id: str
    target_id: str
    edge_type: EdgeType
    label: str = ""
    domain: Domain = Domain.GLOBAL
    metadata: dict = field(default_factory=dict)
    mapping_details: list[dict] = field(default_factory=list)

    @property
    def edge_id(self) -> str:
        return f"{self.source_id}--{self.edge_type.value}-->{self.target_id}"
