#!/usr/bin/env python3
"""
IP-XACT Dependency Graph Tracker – Full Demo
=============================================

Demonstrates the COMPLETE IP-XACT output landscape for a realistic ASIC
design flow with mapping validation across all domains.

The demo intentionally includes:
  - COMPLETE mappings (ports, clocks, power) to show passing validation
  - INCOMPLETE mappings (missing CDC, missing register coverage) to show
    how the validator catches gaps that would otherwise cause human error

This demonstrates the value proposition: rather than relying on engineers
to manually track all the cross-domain dependencies, the graph + validator
automatically flags what's missing.
"""

from __future__ import annotations
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ipxact_graph import (
    ArtifactNode, DependencyEdge, NodeType, EdgeType, Domain,
    MappingCategory, GraphManager, ChangeDetector,
    MappingValidator, EdgeCaseAuditor, MermaidGenerator, ExcelReportGenerator,
)

logging.basicConfig(level=logging.WARNING, format="%(levelname)s | %(message)s")

SAMPLE_DIR = Path(__file__).resolve().parent / "sample_artifacts"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"


def create_sample_files() -> None:
    """Create realistic sample files for change detection."""
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    (SAMPLE_DIR / "customer_aes_top.sv").write_text(
        "// Customer FPGA: AES-GCM top-level\n"
        "module aes_gcm_top(\n"
        "  input clk_100mhz, clk_50mhz, rst_n,\n"
        "  input [127:0] plaintext_data, aes_key,\n"
        "  input [95:0] iv_nonce,\n"
        "  input start_encrypt,\n"
        "  output [127:0] ciphertext_out, auth_tag,\n"
        "  output done_flag\n"
        ");\nendmodule\n"
    )
    (SAMPLE_DIR / "aes_gcm_component.xml").write_text(
        '<?xml version="1.0"?>\n<spirit:component>\n'
        "  <!-- IP-XACT Component: aes_gcm_accelerator v1.0 -->\n"
        "  <!-- Ports: i_clk, i_clk_aux, i_rst_n, i_plaintext[127:0], "
        "i_key[127:0], i_iv[95:0], i_start, o_ciphertext[127:0], "
        "o_auth_tag[127:0], o_done -->\n"
        "  <!-- Bus: axi_slave (AXI4) -->\n"
        "  <!-- MemoryMap: reg_block_0 (control/status registers) -->\n"
        "  <!-- Registers: CTRL(0x00), STATUS(0x04), KEY0(0x10), IV0(0x20) -->\n"
        "</spirit:component>\n"
    )
    (SAMPLE_DIR / "soc_design.xml").write_text(
        '<?xml version="1.0"?>\n<spirit:design>\n'
        "  <!-- Instances: u_aes_gcm, u_axi_interconnect, u_uart -->\n"
        "</spirit:design>\n"
    )
    (SAMPLE_DIR / "aes_gcm_constraints.sdc").write_text(
        "create_clock -name clk_main -period 10.0 [get_ports i_clk]\n"
        "create_clock -name clk_aux -period 20.0 [get_ports i_clk_aux]\n"
        "set_clock_groups -asynchronous -group clk_main -group clk_aux\n"
        "set_false_path -from [get_ports i_rst_n]\n"
        "set_input_delay -max 3.0 -clock clk_main [get_ports {i_plaintext[*]}]\n"
        "set_output_delay -max 2.5 -clock clk_main [get_ports {o_ciphertext[*]}]\n"
    )
    (SAMPLE_DIR / "aes_gcm_dft.sdc").write_text(
        "create_clock -name clk_scan -period 50.0 [get_ports scan_clk]\n"
        "set_false_path -from [get_clocks clk_scan] -to [get_clocks clk_main]\n"
    )
    (SAMPLE_DIR / "aes_gcm_power.upf").write_text(
        "create_power_domain PD_AES -include_scope\n"
        "create_supply_net VDD -domain PD_AES\n"
        "create_supply_net VSS -domain PD_AES\n"
        "set_isolation iso_aes -domain PD_AES\n"
    )
    (SAMPLE_DIR / "aes_gcm_rtl.sv").write_text(
        "module aes_gcm_accelerator(\n"
        "  input i_clk, i_clk_aux, i_rst_n,\n"
        "  input [127:0] i_plaintext, i_key,\n"
        "  input [95:0] i_iv,\n"
        "  input i_start,\n"
        "  output [127:0] o_ciphertext, o_auth_tag,\n"
        "  output o_done\n"
        ");\nendmodule\n"
    )
    (SAMPLE_DIR / "dc_compile.tcl").write_text(
        "read_file -format sverilog aes_gcm_rtl.sv\nsource aes_gcm_constraints.sdc\n"
    )
    (SAMPLE_DIR / "aes_gcm_cdc.tcl").write_text(
        "# Spyglass CDC constraints\n"
        "set_cdc_crossing -from clk_main -to clk_aux -type gray_code\n"
    )
    (SAMPLE_DIR / "reset_scheme.json").write_text(json.dumps({
        "reset_domains": [{"name": "rst_main", "port": "i_rst_n",
                           "polarity": "active_low", "sync": "async"}],
    }, indent=2))
    (SAMPLE_DIR / "register_map.csv").write_text(
        "name,offset,width,access,reset_value\n"
        "CTRL,0x00,32,RW,0x00000000\n"
        "STATUS,0x04,32,RO,0x00000001\n"
        "KEY0,0x10,32,WO,0x00000000\n"
        "IV0,0x20,32,WO,0x00000000\n"
    )
    (SAMPLE_DIR / "aes_gcm_ral.sv").write_text(
        "class aes_gcm_reg_block extends uvm_reg_block;\n"
        "  // UVM RAL model generated from register map\n"
        "endclass\n"
    )
    (SAMPLE_DIR / "aes_gcm_regs.h").write_text(
        "#define AES_CTRL_OFFSET   0x00\n"
        "#define AES_STATUS_OFFSET 0x04\n"
        "#define AES_KEY0_OFFSET   0x10\n"
        "// NOTE: IV0 register is MISSING from this header!\n"
    )
    (SAMPLE_DIR / "memory_map.json").write_text(json.dumps({
        "memory_map": "aes_gcm_map",
        "address_blocks": [{"name": "reg_block_0", "base": "0x0", "range": "0x100"}],
    }, indent=2))
    (SAMPLE_DIR / "filelist.f").write_text("aes_gcm_rtl.sv\naes_gcm_top.sv\n")
    (SAMPLE_DIR / "bus_vip_config.json").write_text(json.dumps({
        "axi_slave": {"protocol": "AXI4", "data_width": 128, "addr_width": 32}
    }, indent=2))
    (SAMPLE_DIR / "design_params.json").write_text(json.dumps({
        "target_freq_mhz": 100, "aux_freq_mhz": 50, "key_width": 128
    }, indent=2))
    (SAMPLE_DIR / "interface_spec.md").write_text("# Interface Spec\nPort mapping table...\n")
    (SAMPLE_DIR / "pin_mapping.csv").write_text(
        "ipxact_port,physical_pin,pad_type,io_standard\n"
        "i_clk,A1,clock,LVCMOS33\n"
    )
    (SAMPLE_DIR / "floorplan.tcl").write_text(
        "create_floorplan -core_utilization 0.7\n"
        "create_placement_blockage -name u_aes_gcm -bbox {100 100 500 500}\n"
    )


def build_full_graph() -> GraphManager:
    """Build the comprehensive dependency graph with ALL IP-XACT output types."""
    gm = GraphManager()
    S = str(SAMPLE_DIR)

    # ================================================================== #
    #  NODES: Complete IP-XACT ecosystem                                   #
    # ================================================================== #

    # -- Customer source --
    gm.add_node(ArtifactNode(
        node_id="fpga_src", name="Customer AES-GCM FPGA",
        node_type=NodeType.FPGA_SOURCE, domain=Domain.FPGA_TRANSLATION,
        file_path=f"{S}/customer_aes_top.sv",
        tags=["customer", "fpga"],
    ))

    # -- IP-XACT Core --
    gm.add_node(ArtifactNode(
        node_id="ipxact_comp", name="AES-GCM IP-XACT Component",
        node_type=NodeType.IPXACT_COMPONENT, domain=Domain.GLOBAL,
        file_path=f"{S}/aes_gcm_component.xml",
        eda_tool="ip-xact",
        # THIS IS KEY: defined_elements tells the validator what to expect
        defined_elements={
            "clocks": ["i_clk", "i_clk_aux"],
            "resets": ["i_rst_n"],
            "bus_interfaces": ["axi_slave"],
            "memory_maps": ["reg_block_0"],
            "ports": ["i_clk", "i_clk_aux", "i_rst_n", "i_plaintext",
                      "i_key", "i_iv", "i_start", "o_ciphertext",
                      "o_auth_tag", "o_done"],
            "power_domains": ["PD_AES"],
            "top_level_ports": ["i_clk", "i_clk_aux", "i_rst_n",
                                "i_plaintext", "i_key", "i_iv", "i_start",
                                "o_ciphertext", "o_auth_tag", "o_done"],
        },
        tags=["ipxact", "component"],
    ))

    gm.add_node(ArtifactNode(
        node_id="ipxact_design", name="SoC Design (IP-XACT)",
        node_type=NodeType.IPXACT_DESIGN, domain=Domain.GLOBAL,
        file_path=f"{S}/soc_design.xml",
        eda_tool="ip-xact",
        defined_elements={
            "instances": ["u_aes_gcm", "u_axi_interconnect", "u_uart"],
        },
    ))

    # -- Constraint files --
    gm.add_node(ArtifactNode(
        node_id="sdc_main", name="Main SDC",
        node_type=NodeType.SDC_CONSTRAINT, domain=Domain.FRONTEND,
        file_path=f"{S}/aes_gcm_constraints.sdc", eda_tool="synopsys_dc",
    ))
    gm.add_node(ArtifactNode(
        node_id="sdc_dft", name="DFT SDC",
        node_type=NodeType.SDC_CONSTRAINT, domain=Domain.DFT,
        file_path=f"{S}/aes_gcm_dft.sdc", eda_tool="synopsys_dft",
    ))
    gm.add_node(ArtifactNode(
        node_id="upf", name="UPF Power Intent",
        node_type=NodeType.UPF_POWER, domain=Domain.FRONTEND,
        file_path=f"{S}/aes_gcm_power.upf", eda_tool="synopsys_dc",
    ))
    gm.add_node(ArtifactNode(
        node_id="cdc", name="CDC Constraints",
        node_type=NodeType.CDC_CONSTRAINT, domain=Domain.VERIFICATION,
        file_path=f"{S}/aes_gcm_cdc.tcl", eda_tool="spyglass_cdc",
    ))
    gm.add_node(ArtifactNode(
        node_id="reset_sch", name="Reset Scheme",
        node_type=NodeType.RESET_SCHEME, domain=Domain.FRONTEND,
        file_path=f"{S}/reset_scheme.json",
    ))

    # -- RTL --
    gm.add_node(ArtifactNode(
        node_id="rtl_wrap", name="RTL Wrapper",
        node_type=NodeType.RTL_WRAPPER, domain=Domain.FRONTEND,
        file_path=f"{S}/aes_gcm_rtl.sv",
    ))
    gm.add_node(ArtifactNode(
        node_id="filelist", name="RTL Filelist",
        node_type=NodeType.RTL_FILELIST, domain=Domain.FRONTEND,
        file_path=f"{S}/filelist.f",
    ))

    # -- Register / Memory --
    gm.add_node(ArtifactNode(
        node_id="reg_map", name="Register Map",
        node_type=NodeType.REGISTER_MAP, domain=Domain.GLOBAL,
        file_path=f"{S}/register_map.csv",
    ))
    gm.add_node(ArtifactNode(
        node_id="uvm_ral", name="UVM RAL Model",
        node_type=NodeType.UVM_RAL_MODEL, domain=Domain.VERIFICATION,
        file_path=f"{S}/aes_gcm_ral.sv",
    ))
    gm.add_node(ArtifactNode(
        node_id="c_header", name="C Register Header",
        node_type=NodeType.C_HEADER, domain=Domain.FIRMWARE,
        file_path=f"{S}/aes_gcm_regs.h",
    ))
    gm.add_node(ArtifactNode(
        node_id="mem_map", name="Memory Map",
        node_type=NodeType.MEMORY_MAP, domain=Domain.GLOBAL,
        file_path=f"{S}/memory_map.json",
    ))

    # -- Verification --
    gm.add_node(ArtifactNode(
        node_id="bus_vip", name="AXI VIP Config",
        node_type=NodeType.BUS_VIP_CONFIG, domain=Domain.VERIFICATION,
        file_path=f"{S}/bus_vip_config.json", eda_tool="synopsys_vip",
    ))

    # -- Physical design --
    gm.add_node(ArtifactNode(
        node_id="pin_map", name="Pin Mapping",
        node_type=NodeType.PIN_MAPPING, domain=Domain.PHYSICAL_DESIGN,
        file_path=f"{S}/pin_mapping.csv",
    ))
    gm.add_node(ArtifactNode(
        node_id="floorplan", name="Floorplan Constraints",
        node_type=NodeType.FLOORPLAN_CONSTRAINT, domain=Domain.PHYSICAL_DESIGN,
        file_path=f"{S}/floorplan.tcl", eda_tool="synopsys_icc2",
    ))

    # -- EDA scripts --
    gm.add_node(ArtifactNode(
        node_id="dc_script", name="DC Compile Script",
        node_type=NodeType.EDA_SCRIPT, domain=Domain.FRONTEND,
        file_path=f"{S}/dc_compile.tcl", eda_tool="synopsys_dc",
    ))

    # -- Config / Docs --
    gm.add_node(ArtifactNode(
        node_id="config", name="Design Parameters",
        node_type=NodeType.CONFIG_PARAM, domain=Domain.GLOBAL,
        file_path=f"{S}/design_params.json",
    ))
    gm.add_node(ArtifactNode(
        node_id="doc_spec", name="Interface Spec",
        node_type=NodeType.DOCUMENTATION, domain=Domain.GLOBAL,
        file_path=f"{S}/interface_spec.md",
    ))

    # ================================================================== #
    #  EDGES: Dependencies with mapping details                            #
    # ================================================================== #

    # ── FPGA → IP-XACT (customer translation) ───────────────────────── #
    gm.add_edge(DependencyEdge(
        source_id="fpga_src", target_id="ipxact_comp",
        edge_type=EdgeType.DERIVES_FROM,
        label="Customer FPGA → IP-XACT translation",
        domain=Domain.FPGA_TRANSLATION,
        mapping_details=[
            # PORT NAMING — complete for all 10 ports
            {"category": "port_naming", "customer_port": "clk_100mhz", "ipxact_port": "i_clk",
             "direction": "in", "width": 1, "rename_reason": "Standard clock prefix"},
            {"category": "port_naming", "customer_port": "clk_50mhz", "ipxact_port": "i_clk_aux",
             "direction": "in", "width": 1, "rename_reason": "Auxiliary clock"},
            {"category": "port_naming", "customer_port": "rst_n", "ipxact_port": "i_rst_n",
             "direction": "in", "width": 1, "rename_reason": "Standard reset prefix"},
            {"category": "port_naming", "customer_port": "plaintext_data", "ipxact_port": "i_plaintext",
             "direction": "in", "width": 128, "rename_reason": "Shortened data name"},
            {"category": "port_naming", "customer_port": "aes_key", "ipxact_port": "i_key",
             "direction": "in", "width": 128, "rename_reason": "Standard input prefix"},
            {"category": "port_naming", "customer_port": "iv_nonce", "ipxact_port": "i_iv",
             "direction": "in", "width": 96, "rename_reason": "Shortened"},
            {"category": "port_naming", "customer_port": "start_encrypt", "ipxact_port": "i_start",
             "direction": "in", "width": 1, "rename_reason": "Standard control prefix"},
            {"category": "port_naming", "customer_port": "ciphertext_out", "ipxact_port": "o_ciphertext",
             "direction": "out", "width": 128, "rename_reason": "Standard output prefix"},
            {"category": "port_naming", "customer_port": "auth_tag", "ipxact_port": "o_auth_tag",
             "direction": "out", "width": 128, "rename_reason": "Standard output prefix"},
            {"category": "port_naming", "customer_port": "done_flag", "ipxact_port": "o_done",
             "direction": "out", "width": 1, "rename_reason": "Standard output prefix"},
            # CLOCK DOMAINS
            {"category": "clock_domain", "customer_clock": "clk_100mhz",
             "ipxact_clock_domain": "clk_main", "frequency_mhz": 100},
            {"category": "clock_domain", "customer_clock": "clk_50mhz",
             "ipxact_clock_domain": "clk_aux", "frequency_mhz": 50},
            # RESET DOMAINS
            {"category": "reset_domain", "customer_reset": "rst_n",
             "ipxact_reset_domain": "rst_main", "polarity": "active_low"},
        ],
    ))

    # ── IP-XACT Component → SDC (clock & timing constraints) ─────────── #
    gm.add_edge(DependencyEdge(
        source_id="ipxact_comp", target_id="sdc_main",
        edge_type=EdgeType.GENERATES,
        label="Generates timing constraints",
        domain=Domain.FRONTEND,
        mapping_details=[
            # CLOCK DOMAINS — one per clock
            {"category": "clock_domain", "ipxact_clock_port": "i_clk",
             "sdc_clock_name": "clk_main", "period_ns": 10.0,
             "uncertainty_setup": 0.3, "uncertainty_hold": 0.1},
            {"category": "clock_domain", "ipxact_clock_port": "i_clk_aux",
             "sdc_clock_name": "clk_aux", "period_ns": 20.0,
             "uncertainty_setup": 0.3, "uncertainty_hold": 0.1},
            # IO TIMING — representative entries
            {"category": "io_timing", "ipxact_port": "i_plaintext",
             "sdc_command": "set_input_delay", "clock_domain": "clk_main",
             "max_delay": 3.0, "min_delay": 0.5},
            {"category": "io_timing", "ipxact_port": "o_ciphertext",
             "sdc_command": "set_output_delay", "clock_domain": "clk_main",
             "max_delay": 2.5, "min_delay": 0.3},
            # FALSE PATHS
            {"category": "false_path", "ipxact_port_or_domain": "i_rst_n",
             "sdc_false_path_spec": "set_false_path -from [get_ports i_rst_n]"},
            # CLOCK GROUPS (conditional – present because >1 clock)
            {"category": "clock_group", "group_name": "async_clocks",
             "clock_list": ["clk_main", "clk_aux"],
             "relationship": "asynchronous"},
        ],
    ))

    # ── IP-XACT Component → UPF ──────────────────────────────────────── #
    gm.add_edge(DependencyEdge(
        source_id="ipxact_comp", target_id="upf",
        edge_type=EdgeType.GENERATES, label="Generates power intent",
        domain=Domain.FRONTEND,
        mapping_details=[
            {"category": "power_domain", "ipxact_component": "aes_gcm_accelerator",
             "upf_power_domain": "PD_AES",
             "supply_net_vdd": "VDD", "supply_net_vss": "VSS"},
            {"category": "isolation_strategy", "power_domain": "PD_AES",
             "isolation_signal": "iso_en", "isolation_sense": "high",
             "isolation_location": "parent"},
        ],
    ))

    # ── IP-XACT Component → RTL Wrapper ──────────────────────────────── #
    gm.add_edge(DependencyEdge(
        source_id="ipxact_comp", target_id="rtl_wrap",
        edge_type=EdgeType.GENERATES, label="Generates RTL wrapper",
        domain=Domain.FRONTEND,
        mapping_details=[
            # COMPLETE port naming — all 10 ports
            {"category": "port_naming", "ipxact_port": "i_clk", "rtl_port": "i_clk", "direction": "in", "width": 1},
            {"category": "port_naming", "ipxact_port": "i_clk_aux", "rtl_port": "i_clk_aux", "direction": "in", "width": 1},
            {"category": "port_naming", "ipxact_port": "i_rst_n", "rtl_port": "i_rst_n", "direction": "in", "width": 1},
            {"category": "port_naming", "ipxact_port": "i_plaintext", "rtl_port": "i_plaintext", "direction": "in", "width": 128},
            {"category": "port_naming", "ipxact_port": "i_key", "rtl_port": "i_key", "direction": "in", "width": 128},
            {"category": "port_naming", "ipxact_port": "i_iv", "rtl_port": "i_iv", "direction": "in", "width": 96},
            {"category": "port_naming", "ipxact_port": "i_start", "rtl_port": "i_start", "direction": "in", "width": 1},
            {"category": "port_naming", "ipxact_port": "o_ciphertext", "rtl_port": "o_ciphertext", "direction": "out", "width": 128},
            {"category": "port_naming", "ipxact_port": "o_auth_tag", "rtl_port": "o_auth_tag", "direction": "out", "width": 128},
            {"category": "port_naming", "ipxact_port": "o_done", "rtl_port": "o_done", "direction": "out", "width": 1},
        ],
    ))

    # ── IP-XACT Component → CDC (clock domain crossings) ─────────────── #
    gm.add_edge(DependencyEdge(
        source_id="ipxact_comp", target_id="cdc",
        edge_type=EdgeType.GENERATES, label="Generates CDC constraints",
        domain=Domain.VERIFICATION,
        mapping_details=[
            {"category": "cdc_crossing", "from_clock_domain": "clk_main",
             "to_clock_domain": "clk_aux", "crossing_type": "gray_code",
             "sync_scheme": "2-flop synchroniser"},
            # INTENTIONAL GAP: Missing clk_aux → clk_main crossing!
            # The validator should catch this if we define both directions.
        ],
    ))

    # ── IP-XACT Component → Reset Scheme ─────────────────────────────── #
    gm.add_edge(DependencyEdge(
        source_id="ipxact_comp", target_id="reset_sch",
        edge_type=EdgeType.GENERATES, label="Generates reset scheme",
        domain=Domain.FRONTEND,
        mapping_details=[
            {"category": "reset_domain", "ipxact_reset_port": "i_rst_n",
             "reset_domain": "rst_main", "polarity": "active_low",
             "sync_async": "async"},
            {"category": "reset_constraint", "reset_domain": "rst_main",
             "associated_clock": "clk_main",
             "deassert_timing": "synchronous deassertion after 2 clk_main cycles"},
        ],
    ))

    # ── IP-XACT Component → Register Map ─────────────────────────────── #
    gm.add_edge(DependencyEdge(
        source_id="ipxact_comp", target_id="reg_map",
        edge_type=EdgeType.GENERATES, label="Generates register map",
        domain=Domain.GLOBAL,
        mapping_details=[
            {"category": "register_block", "register_name": "CTRL",
             "offset": "0x00", "width": 32, "access_type": "RW", "reset_value": "0x0"},
            {"category": "register_block", "register_name": "STATUS",
             "offset": "0x04", "width": 32, "access_type": "RO", "reset_value": "0x1"},
            {"category": "register_block", "register_name": "KEY0",
             "offset": "0x10", "width": 32, "access_type": "WO", "reset_value": "0x0"},
            {"category": "register_block", "register_name": "IV0",
             "offset": "0x20", "width": 32, "access_type": "WO", "reset_value": "0x0"},
            {"category": "address_space", "address_space_name": "aes_gcm_regs",
             "base_address": "0x0000_0000", "range": "0x100",
             "bus_interface": "axi_slave"},
        ],
    ))

    # ── IP-XACT Component → Memory Map ───────────────────────────────── #
    gm.add_edge(DependencyEdge(
        source_id="ipxact_comp", target_id="mem_map",
        edge_type=EdgeType.GENERATES, label="Generates memory map",
        domain=Domain.GLOBAL,
        mapping_details=[
            {"category": "memory_map_mapping", "memory_map_name": "reg_block_0",
             "address_block": "aes_regs", "base_address": "0x0", "range": "0x100"},
        ],
    ))

    # ── Register Map → UVM RAL ───────────────────────────────────────── #
    gm.add_edge(DependencyEdge(
        source_id="reg_map", target_id="uvm_ral",
        edge_type=EdgeType.GENERATES, label="Generates UVM RAL model",
        domain=Domain.VERIFICATION,
        mapping_details=[
            {"category": "register_block", "register_name": "CTRL",
             "ral_class_name": "aes_gcm_ctrl_reg", "access_type": "RW",
             "field_list": ["enable", "mode", "key_size"]},
            {"category": "register_block", "register_name": "STATUS",
             "ral_class_name": "aes_gcm_status_reg", "access_type": "RO",
             "field_list": ["busy", "done", "error"]},
            {"category": "register_block", "register_name": "KEY0",
             "ral_class_name": "aes_gcm_key0_reg", "access_type": "WO",
             "field_list": ["key_data"]},
            {"category": "register_block", "register_name": "IV0",
             "ral_class_name": "aes_gcm_iv0_reg", "access_type": "WO",
             "field_list": ["iv_data"]},
        ],
    ))

    # ── Register Map → C Header ──────────────────────────────────────── #
    # INTENTIONAL GAP: IV0 register is missing from C header!
    gm.add_edge(DependencyEdge(
        source_id="reg_map", target_id="c_header",
        edge_type=EdgeType.GENERATES, label="Generates C header",
        domain=Domain.FIRMWARE,
        mapping_details=[
            {"category": "register_block", "register_name": "CTRL",
             "c_define_name": "AES_CTRL_OFFSET", "offset_hex": "0x00",
             "field_masks": ["AES_CTRL_EN_MASK=0x1"]},
            {"category": "register_block", "register_name": "STATUS",
             "c_define_name": "AES_STATUS_OFFSET", "offset_hex": "0x04",
             "field_masks": ["AES_STATUS_BUSY_MASK=0x1"]},
            {"category": "register_block", "register_name": "KEY0",
             "c_define_name": "AES_KEY0_OFFSET", "offset_hex": "0x10",
             "field_masks": ["AES_KEY0_DATA_MASK=0xFFFFFFFF"]},
            # IV0 is INTENTIONALLY MISSING — validator should catch this
        ],
    ))

    # ── IP-XACT Component → Bus VIP Config ───────────────────────────── #
    gm.add_edge(DependencyEdge(
        source_id="ipxact_comp", target_id="bus_vip",
        edge_type=EdgeType.GENERATES, label="Generates VIP config",
        domain=Domain.VERIFICATION,
        mapping_details=[
            {"category": "bus_interface", "bus_interface_name": "axi_slave",
             "protocol": "AXI4", "vip_type": "synopsys_axi_vip",
             "config_params": {"data_width": 128, "addr_width": 32}},
        ],
    ))

    # ── IP-XACT Component → Pin Mapping ──────────────────────────────── #
    # INTENTIONAL GAP: Only 1 of 10 ports mapped — validator will flag this
    gm.add_edge(DependencyEdge(
        source_id="ipxact_comp", target_id="pin_map",
        edge_type=EdgeType.GENERATES, label="Generates pin mapping",
        domain=Domain.PHYSICAL_DESIGN,
        mapping_details=[
            {"category": "pin_assignment", "ipxact_port": "i_clk",
             "physical_pin": "A1", "pad_type": "clock", "io_standard": "LVCMOS33"},
            # All other ports MISSING — validator will flag 9/10 missing
        ],
    ))

    # ── IP-XACT Component → Filelist ─────────────────────────────────── #
    gm.add_edge(DependencyEdge(
        source_id="ipxact_comp", target_id="filelist",
        edge_type=EdgeType.GENERATES, label="Generates RTL filelist",
        domain=Domain.FRONTEND,
        mapping_details=[
            {"category": "filelist_entry", "file_path": "aes_gcm_rtl.sv",
             "file_type": "systemverilog", "compile_order": 1},
            {"category": "filelist_entry", "file_path": "aes_gcm_top.sv",
             "file_type": "systemverilog", "compile_order": 2},
        ],
    ))

    # ── IP-XACT Component → Documentation ────────────────────────────── #
    gm.add_edge(DependencyEdge(
        source_id="ipxact_comp", target_id="doc_spec",
        edge_type=EdgeType.GENERATES, label="Generates interface spec",
        domain=Domain.GLOBAL,
    ))

    # ── IP-XACT Component → Design (reference) ──────────────────────── #
    gm.add_edge(DependencyEdge(
        source_id="ipxact_comp", target_id="ipxact_design",
        edge_type=EdgeType.REFERENCES, label="Component used in design",
    ))

    # ── IP-XACT Design → Floorplan ───────────────────────────────────── #
    gm.add_edge(DependencyEdge(
        source_id="ipxact_design", target_id="floorplan",
        edge_type=EdgeType.GENERATES, label="Generates floorplan constraints",
        domain=Domain.PHYSICAL_DESIGN,
        mapping_details=[
            {"category": "hierarchy_mapping", "instance_name": "u_aes_gcm",
             "component_ref": "aes_gcm_accelerator",
             "placement_region": "crypto_region", "area_estimate": "50000 um²"},
        ],
    ))

    # ── Cross-domain SDC consistency ─────────────────────────────────── #
    gm.add_edge(DependencyEdge(
        source_id="sdc_main", target_id="sdc_dft",
        edge_type=EdgeType.CONSTRAINS, label="DFT must match main clocks",
        domain=Domain.DFT,
        mapping_details=[
            {"category": "clock_domain", "source_clock": "clk_main",
             "target_clock_reference": "clk_main", "false_path_defined": True},
        ],
    ))

    # ── Downstream tool dependencies ─────────────────────────────────── #
    gm.add_edge(DependencyEdge(
        source_id="sdc_main", target_id="dc_script",
        edge_type=EdgeType.CONFIGURES, label="SDC sourced by DC",
    ))
    gm.add_edge(DependencyEdge(
        source_id="rtl_wrap", target_id="dc_script",
        edge_type=EdgeType.CONFIGURES, label="RTL read by DC",
    ))
    gm.add_edge(DependencyEdge(
        source_id="upf", target_id="dc_script",
        edge_type=EdgeType.CONFIGURES, label="UPF applied in synthesis",
    ))
    gm.add_edge(DependencyEdge(
        source_id="config", target_id="ipxact_comp",
        edge_type=EdgeType.CONFIGURES, label="Params configure IP-XACT",
    ))

    return gm


def main() -> None:
    print("\n" + "=" * 72)
    print("  IP-XACT DEPENDENCY GRAPH TRACKER – FULL DEMO")
    print("  Complete IP-XACT Output Coverage + Mapping Validation")
    print("=" * 72)

    # Step 1: Create sample files
    print("\n[1/7] Creating sample artifacts...")
    create_sample_files()

    # Step 2: Build graph
    print("[2/7] Building comprehensive dependency graph...")
    gm = build_full_graph()
    s = gm.summary()
    print(f"       {s['total_nodes']} nodes, {s['total_edges']} edges")
    print(f"       Node types: {len(s['node_types'])} distinct types")
    print(f"       Domains: {list(s['domains'].keys())}")

    # Step 3: Mapping Validation (THE KEY NEW FEATURE)
    print("\n[3/7] Running mapping completeness validation...")
    validator = MappingValidator(gm)
    val_report = validator.validate()
    val_report.print_report()
    val_report.save(OUTPUT_DIR / "validation_report.json")

    # Step 4: Edge Case Audit (THE EXTENDED CHECKS)
    print("\n[4/8] Running edge case audit...")
    auditor = EdgeCaseAuditor(gm)
    edge_report = auditor.full_audit(
        scan_directories=[SAMPLE_DIR],
        check_file_content=True,
    )
    edge_report.print_report()
    edge_report.save(OUTPUT_DIR / "edge_case_report.json")

    # Step 5: Change detection
    print("[5/8] Building hash baseline...")
    detector = ChangeDetector(gm)
    detector.build_baseline()
    detector.save_baseline(OUTPUT_DIR / "hash_baseline.json")

    # Simulate a change
    print("[6/8] Simulating SDC clock change (10ns → 8ns)...")
    sdc_file = SAMPLE_DIR / "aes_gcm_constraints.sdc"
    original = sdc_file.read_text()
    sdc_file.write_text(original.replace("period 10.0", "period 8.0"))
    report = detector.full_scan(include_upstream=True)
    report.save(OUTPUT_DIR / "change_report.json")
    print(f"       Changed: {len(report.changed_files)} files")
    print(f"       Affected: {len(report.affected_node_ids)} downstream/upstream nodes")
    for ic in report.impact_chains:
        print(f"         {' → '.join(ic.path)}")
    sdc_file.write_text(original)  # restore

    # Step 6: Mermaid diagrams
    print("\n[7/8] Generating Mermaid diagrams...")
    mermaid = MermaidGenerator(gm)
    mermaid.save(OUTPUT_DIR / "full_graph.mermaid",
                 title="IP-XACT Complete Dependency Graph")
    mermaid.save(OUTPUT_DIR / "impact_graph.mermaid",
                 title="Change Impact: SDC Clock Update",
                 change_report=report)
    print("       Saved: full_graph.mermaid, impact_graph.mermaid")

    # Step 7: Excel report
    print("[8/8] Generating Excel report...")
    excel = ExcelReportGenerator(gm)
    excel.generate(OUTPUT_DIR / "dependency_report.xlsx", change_report=report)
    print("       Saved: dependency_report.xlsx")

    # Save graph
    gm.save(OUTPUT_DIR / "dependency_graph.json")

    print("\n" + "=" * 72)
    print("  DEMO COMPLETE")
    print("=" * 72)
    print(f"""
OUTPUTS in {OUTPUT_DIR}/:
  • validation_report.json  ← Mapping completeness audit
  • edge_case_report.json   ← Extended edge case audit (drift, orphans, semantics)
  • dependency_graph.json   ← Serialised graph
  • full_graph.mermaid      ← Full dependency diagram
  • impact_graph.mermaid    ← Change impact visualisation
  • dependency_report.xlsx  ← Multi-sheet Excel workbook
  • change_report.json      ← Change detection results

INTENTIONAL GAPS DEMONSTRATED (validator catches these):
  ❌ Pin mapping: Only 1/10 ports mapped to physical pins
  ❌ C header:    IV0 register missing from firmware header
  ⚠️  CDC:        Only one crossing direction documented (clk_main→clk_aux)
  ⚠️  Some conditional mappings (retention, level shifters) not present
""")


if __name__ == "__main__":
    main()
