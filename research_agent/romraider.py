#!/usr/bin/env python3
"""
romraider.py — RomRaider logger XML parser and SFEDash PID config generator.

Fetches ECU parameter definitions from the RomRaider GitHub repository,
parses SSM/OBD2 parameter addresses + scaling, and can ingest them directly
into a Frank knowledge base session.

Usage:
    python romraider.py                    # fetch + print summary table
    python romraider.py --output pids.json # save SFEDash-ready config
    python romraider.py --ingest           # ingest into most recent session
    python romraider.py --session <slug>   # target specific session
    python romraider.py --list             # list all found parameters
"""

import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import urljoin

sys.path.insert(0, os.path.dirname(__file__))

import requests

_ROMRAIDER_URLS = [
    "https://raw.githubusercontent.com/RomRaider/RomRaider/master/definitions/log_defs.xml",
]

_HEADERS = {"User-Agent": "frank-research-agent/1.0"}
_FETCH_TIMEOUT = 15


# ── XML fetch + parse ──────────────────────────────────────────────────────────

def fetch_logger_xml(url: str) -> str | None:
    """Fetch RomRaider logger XML from a URL. Returns text or None."""
    try:
        resp = requests.get(url, timeout=_FETCH_TIMEOUT, headers=_HEADERS)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"[WARN] Failed to fetch {url}: {e}")
        return None


def _normalise_offset(raw: str) -> str:
    """Normalise a RomRaider offset like '#000E' to '0x000E'."""
    raw = raw.strip()
    if raw.startswith("#"):
        return "0x" + raw[1:]
    if raw.startswith("0x") or raw.startswith("0X"):
        return raw
    return raw


def _storage_len(storagetype: str) -> int:
    """Return byte length for a RomRaider storagetype."""
    st = storagetype.lower()
    if st in ("uint16", "int16"):
        return 2
    if st in ("float", "uint32", "int32"):
        return 4
    return 1  # uint8 or default


def parse_logger_xml(xml_text: str, source_url: str = "") -> list[dict]:
    """
    Parse a RomRaider logger XML definition file (definitions/log_defs.xml).
    Returns list of parameter dicts with standardized fields.

    The actual XML structure uses <logprotocol type="SSM"> wrappers and
    parameter attributes (offset, storagetype, expr, metric) rather than
    child elements.
    """
    params = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"[WARN] XML parse error: {e}")
        return []

    # Find protocol containers: <logprotocol type="SSM"> or <protocol id="...">
    protocols = root.findall(".//logprotocol")
    if not protocols:
        protocols = root.findall(".//protocol")
    if not protocols:
        protocols = [root]

    for proto_el in protocols:
        proto_id = proto_el.get("type", proto_el.get("id", "SSM"))

        for param in proto_el.findall(".//parameter"):
            param_id = param.get("id", "")
            name     = param.get("name", param_id)  # id doubles as name
            desc     = param.get("desc", param.get("description", ""))

            # Address: prefer 'offset' attribute (actual format), fall back to <address> child
            offset_attr = param.get("offset", "")
            addr_el     = param.find("address")

            address  = ""
            addr_len = 1
            storagetype = param.get("storagetype", "uint8")

            if offset_attr:
                address  = _normalise_offset(offset_attr)
                addr_len = _storage_len(storagetype)
            elif addr_el is not None:
                raw_addr = (addr_el.text or "").strip()
                if raw_addr.startswith("0x") or raw_addr.startswith("0X"):
                    address = raw_addr.upper()
                elif raw_addr.startswith("#"):
                    address = _normalise_offset(raw_addr)
                elif raw_addr.isdigit():
                    address = hex(int(raw_addr)).upper()
                else:
                    address = raw_addr
                try:
                    addr_len = int(addr_el.get("length", "1"))
                except ValueError:
                    addr_len = 1

            # Units / expression / format: prefer attributes, fall back to <conversion> child
            units = param.get("metric", "")
            expr  = param.get("expr", "")
            fmt   = "0." + "0" * int(param.get("decimals", "2") or "2") if param.get("decimals") else "0.00"

            if not units and not expr:
                for conv in param.findall(".//conversion"):
                    units = conv.get("units", "")
                    expr  = conv.get("expr", conv.get("expression", "x"))
                    fmt   = conv.get("format", "0.00")
                    break

            if not expr:
                expr = "x"

            if not name and not address:
                continue

            target = param.get("target", "1")

            params.append({
                "id":          param_id,
                "name":        name,
                "desc":        desc,
                "address":     address,
                "address_len": addr_len,
                "protocol":    proto_id,
                "units":       units,
                "expr":        expr,
                "format":      fmt,
                "target":      target,
                "source_url":  source_url,
            })

    # Also handle <switch> elements (binary flags)
    for proto_el in protocols:
        proto_id = proto_el.get("type", proto_el.get("id", "SSM"))
        for sw in proto_el.findall(".//switch"):
            sw_id   = sw.get("id", "")
            name    = sw.get("name", sw_id)
            offset  = sw.get("offset", "")
            addr_el = sw.find("address")
            address = ""

            if offset:
                address = _normalise_offset(offset)
            elif addr_el is not None:
                raw_addr = (addr_el.text or "").strip()
                address = _normalise_offset(raw_addr) if raw_addr else ""

            if name and address:
                params.append({
                    "id":          sw_id,
                    "name":        name,
                    "desc":        sw.get("desc", ""),
                    "address":     address,
                    "address_len": 1,
                    "protocol":    proto_id,
                    "units":       "bool",
                    "expr":        "x",
                    "format":      "0",
                    "target":      sw.get("target", "1"),
                    "source_url":  source_url,
                })

    return params


def fetch_and_parse_all() -> list[dict]:
    """Fetch all configured RomRaider XML URLs and return merged parameter list."""
    all_params = []
    seen_ids = set()
    for url in _ROMRAIDER_URLS:
        xml_text = fetch_logger_xml(url)
        if not xml_text:
            continue
        params = parse_logger_xml(xml_text, source_url=url)
        for p in params:
            key = (p["address"], p["protocol"])
            if key not in seen_ids and p["address"]:
                seen_ids.add(key)
                all_params.append(p)
        print(f"[INFO] {url.split('/')[-1]}: {len(params)} parameters")
    return all_params


# ── SFEDash config generator ───────────────────────────────────────────────────

def generate_sfe_dash_config(parameters: list[dict]) -> dict:
    """Generate a SFEDash-ready config dict from parsed parameters."""
    return {
        "version":      "1.0",
        "source":       "RomRaider",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "parameters": [
            {
                "name":          p["name"],
                "description":   p["desc"],
                "pid_address":   p["address"],
                "address_len":   p["address_len"],
                "protocol":      p["protocol"],
                "units":         p["units"],
                "scaling":       p["expr"],
                "display_format": p["format"],
                "romraider_id":  p["id"],
                "target":        p["target"],
            }
            for p in parameters
            if p.get("address")
        ],
    }


# ── KB ingest ──────────────────────────────────────────────────────────────────

def ingest_into_kb(parameters: list[dict], kb) -> int:
    """
    Inject parsed RomRaider parameters as high-confidence facts into the KB.
    Groups parameters by protocol for sensible topic organisation.
    Returns count of facts added.
    """
    by_protocol: dict[str, list[dict]] = {}
    for p in parameters:
        proto = p.get("protocol", "SSM")
        by_protocol.setdefault(proto, []).append(p)

    total_added = 0
    for proto, params in by_protocol.items():
        topic_key = f"romraider {proto} parameters"
        facts = []
        for p in params:
            content = (
                f"{p['name']}: address {p['address']} "
                f"(len={p['address_len']}) "
                f"units={p['units']} scaling={p['expr']}"
            )
            if p.get("desc"):
                content += f" — {p['desc']}"
            facts.append({
                "content":        content,
                "confidence":     "high",
                "source_url":     p.get("source_url", ""),
                "source_count":   1,
                "model_rank":     1,  # treat as authoritative
                "added_at":       datetime.now(timezone.utc).isoformat(),
            })

        synthetic = {
            "_model_rank":     1,
            "summary":         f"RomRaider {proto} parameter definitions ({len(params)} parameters)",
            "facts":           facts,
            "conflicts":       [],
            "follow_up_topics": [],
            "related_topics":  [],
        }
        new_added, _ = kb.update_topic(topic_key, synthetic)
        total_added += new_added

    return total_added


# ── Rich display ───────────────────────────────────────────────────────────────

def print_summary(parameters: list[dict]) -> None:
    """Print a Rich-formatted table of all found parameters."""
    try:
        from rich.console import Console
        from rich.table import Table
        from rich import box
        console = Console(highlight=False)
    except ImportError:
        for p in parameters:
            print(f"{p['protocol']:8} {p['address']:10} {p['name']:40} {p['units']}")
        return

    table = Table(
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style="bold cyan",
        pad_edge=False,
        row_styles=["", "dim"],
    )
    table.add_column("Protocol", width=8)
    table.add_column("Address",  width=10)
    table.add_column("Name",     width=36)
    table.add_column("Units",    width=10)
    table.add_column("Scaling",  width=18)
    table.add_column("Len", width=4)

    for p in parameters:
        table.add_row(
            p.get("protocol", ""),
            p.get("address", ""),
            p.get("name", ""),
            p.get("units", ""),
            p.get("expr", ""),
            str(p.get("address_len", 1)),
        )

    from rich.panel import Panel
    console.print(Panel(
        table,
        title=f"[bold cyan]RomRaider Parameters ({len(parameters)} total)[/bold cyan]",
        border_style="cyan",
        padding=(0, 1),
    ))


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="RomRaider XML parser + SFEDash config generator")
    parser.add_argument("--output",  metavar="PATH", help="Save SFEDash config JSON to file")
    parser.add_argument("--ingest",  action="store_true", help="Ingest parameters into Frank KB")
    parser.add_argument("--session", metavar="SLUG", help="Session to ingest into")
    parser.add_argument("--list",    action="store_true", help="List all parameters (default)")
    args = parser.parse_args()

    print("Fetching RomRaider logger XML definitions...")
    params = fetch_and_parse_all()

    if not params:
        print("No parameters found. Check network connectivity.")
        sys.exit(1)

    print(f"\nTotal: {len(params)} unique parameters across all files.\n")

    if args.output:
        cfg = generate_sfe_dash_config(params)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        print(f"SFEDash config saved: {args.output}")

    if args.ingest:
        import config as frank_config
        import sessions as session_manager
        from knowledge_base import KnowledgeBase

        existing = session_manager.list_sessions()
        if args.session:
            match = next((s for s in existing if s["slug"] == args.session or s["name"] == args.session), None)
            if not match:
                print(f"Session '{args.session}' not found.")
                sys.exit(1)
            session_manager.activate_session(match)
        elif existing:
            session_manager.activate_session(existing[0])

        kb = KnowledgeBase()
        if not kb.load():
            print("No knowledge base found. Run Frank first to create a session.")
            sys.exit(1)

        count = ingest_into_kb(params, kb)
        kb.save()
        print(f"Ingested {count} parameters as facts into: {frank_config.KNOWLEDGE_FILE}")
        return

    # Default: print summary table
    print_summary(params)


if __name__ == "__main__":
    main()
