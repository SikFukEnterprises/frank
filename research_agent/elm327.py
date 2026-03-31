#!/usr/bin/env python3
"""
elm327.py — ELM327 live PID validator for Frank.

Connects to an ELM327 OBD2 adapter, tests discovered PIDs from the Frank
knowledge base against the live ECU, and marks confirmed PIDs as
hardware_verified in the knowledge base.

Usage:
    python elm327.py --port /dev/ttyUSB0              # Linux serial
    python elm327.py --port COM3                       # Windows
    python elm327.py --port /dev/rfcomm0               # Bluetooth serial
    python elm327.py --port /dev/ttyUSB0 --session sfedash
    python elm327.py --scan --port /dev/ttyUSB0        # scan all Mode 01 PIDs
    python elm327.py --dry-run                         # show what would be tested, no connect
"""

import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))


# ── ELM327 class ──────────────────────────────────────────────────────────────

class ELM327:
    """
    Minimal ELM327 serial interface.
    Handles init, raw command send, and OBD query helpers.
    """

    BAUD_RATES = [38400, 9600, 115200, 57600]
    _PROMPT = b">"
    _NO_DATA = ("NO DATA", "NODATA", "?", "UNABLE TO CONNECT", "ERROR",
                "BUS INIT", "STOPPED", "CAN ERROR")

    def __init__(self, port: str, baud: int = 38400, timeout: float = 2.0):
        self._port    = port
        self._baud    = baud
        self._timeout = timeout
        self._ser     = None

    def connect(self) -> bool:
        """Open serial port and initialise ELM327. Returns True on success."""
        try:
            import serial
        except ImportError:
            print("ERROR: pyserial not installed. Run: pip install pyserial")
            return False

        try:
            import serial as _serial
            self._ser = _serial.Serial(
                self._port, self._baud,
                bytesize=8, parity="N", stopbits=1,
                timeout=self._timeout,
            )
            time.sleep(0.5)
        except Exception as e:
            print(f"ERROR: Could not open {self._port}: {e}")
            return False

        # Init sequence
        self.send_cmd("ATZ")    # reset
        time.sleep(1.5)
        self._ser.flushInput()
        r_e0 = self.send_cmd("ATE0")   # echo off
        r_l0 = self.send_cmd("ATL0")   # linefeeds off
        r_h0 = self.send_cmd("ATH0")   # headers off
        self.send_cmd("ATS0")           # spaces off
        self.send_cmd("ATSP0")          # auto protocol

        if any(x is None for x in (r_e0, r_l0)):
            print("WARN: ELM327 init responses incomplete — adapter may not be responding")

        return True

    def disconnect(self) -> None:
        if self._ser and self._ser.is_open:
            try:
                self._ser.close()
            except Exception:
                pass
        self._ser = None

    def is_connected(self) -> bool:
        return self._ser is not None and self._ser.is_open

    def send_cmd(self, cmd: str) -> str | None:
        """
        Send a command and return the response string (stripped of prompt/echo).
        Returns None on timeout or serial error.
        """
        if not self._ser or not self._ser.is_open:
            return None
        try:
            self._ser.flushInput()
            self._ser.write((cmd + "\r").encode("ascii"))
            self._ser.flush()
            # Read until '>' prompt
            buf = b""
            deadline = time.monotonic() + self._timeout
            while time.monotonic() < deadline:
                chunk = self._ser.read(64)
                if chunk:
                    buf += chunk
                    if b">" in buf:
                        break
            # Decode, strip prompt, echo, whitespace
            text = buf.decode("ascii", errors="replace")
            text = text.replace(">", "").replace("\r", "\n")
            # Remove echo (first line often mirrors the command)
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            if lines and lines[0].upper() == cmd.upper():
                lines = lines[1:]
            return "\n".join(lines) if lines else ""
        except Exception as e:
            print(f"[WARN] send_cmd({cmd!r}) error: {e}")
            return None

    def _is_no_data(self, response: str | None) -> bool:
        if response is None:
            return True
        upper = response.upper().replace(" ", "")
        return any(nd.replace(" ", "") in upper for nd in self._NO_DATA)

    def query_pid(self, mode: int, pid: int) -> str | None:
        """
        Query a standard OBD Mode+PID. Returns hex response string or None if no data.
        E.g. query_pid(0x01, 0x0C) → "410C1AF8"
        """
        cmd = f"{mode:02X}{pid:02X}"
        resp = self.send_cmd(cmd)
        if self._is_no_data(resp):
            return None
        return resp

    def query_mode22(self, pid: int) -> str | None:
        """
        Query Mode 22 (enhanced diagnostics / manufacturer-specific).
        pid is a 16-bit value. Returns hex response or None.
        """
        cmd = f"22{pid:04X}"
        resp = self.send_cmd(cmd)
        if self._is_no_data(resp):
            return None
        return resp

    def query_ssm(self, address: int) -> str | None:
        """
        Query Subaru SSM protocol at a 3-byte memory address.
        Sends a raw SSM single-address read request.
        SSM frame: 80 10 F0 05 A8 00 [addr_hi] [addr_mid] [addr_lo] [checksum]
        Returns raw response or None.
        """
        a2 = (address >> 16) & 0xFF
        a1 = (address >> 8) & 0xFF
        a0 = address & 0xFF
        data = [0x80, 0x10, 0xF0, 0x05, 0xA8, 0x00, a2, a1, a0]
        csum = sum(data) & 0xFF
        data.append(csum)
        hex_cmd = "".join(f"{b:02X}" for b in data)
        resp = self.send_cmd(hex_cmd)
        if self._is_no_data(resp):
            return None
        return resp

    def get_protocol(self) -> str:
        """Return the currently active OBD protocol description."""
        resp = self.send_cmd("ATDP")
        return resp or "unknown"

    def get_voltage(self) -> str | None:
        """Return battery voltage reading."""
        resp = self.send_cmd("ATRV")
        return resp if resp and not self._is_no_data(resp) else None


# ── PID extraction from KB ────────────────────────────────────────────────────

_PID_PATTERNS = [
    # Mode 01 PID 0x0D, PID 0x0C etc.
    r"[Mm]ode\s*0?1\s+[Pp][Ii][Dd]\s*(?:0x)?([0-9A-Fa-f]{1,4})",
    r"[Pp][Ii][Dd]\s*(?:0x)?([0-9A-Fa-f]{1,4})\s*[\-—]?\s*[Mm]ode\s*0?1",
    # Mode 22 PID 0x2xxx
    r"[Mm]ode\s*22\s+[Pp][Ii][Dd]\s*(?:0x)?([0-9A-Fa-f]{2,4})",
    r"[Pp][Ii][Dd]\s*(?:0x)?([0-9A-Fa-f]{2,4})\s*[\-—]?\s*[Mm]ode\s*22",
    r"0x22([0-9A-Fa-f]{2,4})",
    # SSM address
    r"[Ss][Ss][Mm]\s+(?:address|addr|parameter)\s*(?:0x)?([0-9A-Fa-f]{4,6})",
    r"(?:address|addr)\s*(?:0x)([0-9A-Fa-f]{4,6})\s*[\-—]?\s*[Ss][Ss][Mm]",
    r"0x([0-9A-Fa-f]{5,6})\b",   # 5-6 hex digits → likely SSM address
    # Generic OBD PID mention
    r"[Pp][Ii][Dd]\s+(?:code\s+)?(?:0x)?([0-9A-Fa-f]{2,4})\b",
]

_MODE22_ADDR_RE = re.compile(r"0x22([0-9A-Fa-f]{2,4})", re.IGNORECASE)
_SSM_ADDR_RE    = re.compile(r"(?:SSM|address|addr)\s*(?:0x)?([0-9A-Fa-f]{5,6})", re.IGNORECASE)


def extract_pids_from_kb(kb) -> list[dict]:
    """
    Scan KB facts for PID and SSM address patterns.
    Returns list of {mode, pid, name, topic, fact, source_url}.
    """
    found = []
    seen = set()

    for topic_key, entry in kb._data.get("knowledge", {}).items():
        for fact in entry.get("facts", []):
            content = fact.get("content", "")
            if not content:
                continue

            # Mode 22 / 0x22xxxx pattern
            for m in _MODE22_ADDR_RE.finditer(content):
                pid_val = int(m.group(1), 16)
                key = (22, pid_val)
                if key not in seen:
                    seen.add(key)
                    found.append({
                        "mode": 22, "pid": pid_val,
                        "pid_str": f"0x{pid_val:04X}",
                        "name": topic_key,
                        "topic": topic_key,
                        "fact": content[:120],
                        "source_url": fact.get("source_url", ""),
                        "verified": False,
                        "response": None,
                    })

            # SSM 3-byte addresses
            for m in _SSM_ADDR_RE.finditer(content):
                addr_val = int(m.group(1), 16)
                if addr_val > 0xFFFF:  # definitely SSM (3-byte)
                    key = ("ssm", addr_val)
                    if key not in seen:
                        seen.add(key)
                        found.append({
                            "mode": "ssm", "pid": addr_val,
                            "pid_str": f"0x{addr_val:06X}",
                            "name": topic_key,
                            "topic": topic_key,
                            "fact": content[:120],
                            "source_url": fact.get("source_url", ""),
                            "verified": False,
                            "response": None,
                        })

    return found


# ── Validation ────────────────────────────────────────────────────────────────

def validate_pids(elm: ELM327, pids: list[dict], delay: float = 0.3) -> list[dict]:
    """
    Test each PID against the live ECU. Updates 'verified' and 'response' in-place.
    Returns the same list with results filled in.
    """
    for item in pids:
        mode = item["mode"]
        pid  = item["pid"]
        try:
            if mode == 22:
                resp = elm.query_mode22(pid)
            elif mode == "ssm":
                resp = elm.query_ssm(pid)
            else:
                resp = elm.query_pid(int(mode), pid)
            item["response"] = resp
            item["verified"] = resp is not None
        except Exception as e:
            item["response"] = f"ERROR: {e}"
            item["verified"] = False
        time.sleep(delay)
    return pids


def scan_mode01(elm: ELM327) -> list[int]:
    """
    Scan all standard Mode 01 PIDs by querying support bitmask PIDs (0x00, 0x20, 0x40, 0x60).
    Returns list of supported PIDs.
    """
    supported = []
    ranges = [(0x00, 0x1F), (0x20, 0x3F), (0x40, 0x5F), (0x60, 0x7F)]
    support_pids = [0x00, 0x20, 0x40, 0x60]

    for support_pid, (start, end) in zip(support_pids, ranges):
        resp = elm.query_pid(0x01, support_pid)
        if not resp:
            continue
        # Parse the 4-byte bitmask from response
        # Response format: "41 00 BE 3E B8 10" — strip mode/pid bytes
        hex_chars = re.sub(r"[^0-9A-Fa-f]", "", resp)
        if len(hex_chars) < 8:
            continue
        try:
            bitmask = int(hex_chars[:8], 16)
        except ValueError:
            continue
        for bit in range(32):
            if bitmask & (1 << (31 - bit)):
                pid = start + bit + 1
                if pid <= end:
                    supported.append(pid)
                    # Actually test each supported PID
                    test_resp = elm.query_pid(0x01, pid)
                    if test_resp:
                        supported.append(pid)

    return sorted(set(supported))


# ── KB update ─────────────────────────────────────────────────────────────────

def mark_verified_in_kb(kb, validated: list[dict]) -> int:
    """
    For each verified PID, find matching facts in KB and set hardware_verified=True.
    Returns count of facts updated.
    """
    updated = 0
    verified_items = [item for item in validated if item["verified"]]

    for item in verified_items:
        pid_str = item["pid_str"].lower()
        for entry in kb._data.get("knowledge", {}).values():
            for fact in entry.get("facts", []):
                content = fact.get("content", "").lower()
                if pid_str in content or (
                    item["name"].lower() in content and
                    str(item["pid"]) in content
                ):
                    if not fact.get("hardware_verified"):
                        fact["hardware_verified"] = True
                        updated += 1

    return updated


# ── Rich display ───────────────────────────────────────────────────────────────

def print_results(results: list[dict]) -> None:
    """Print PID validation results as a Rich table."""
    try:
        from rich.console import Console
        from rich.table import Table
        from rich.text import Text
        from rich import box
        console = Console(highlight=False)
    except ImportError:
        for r in results:
            status = "✓" if r["verified"] else "✗"
            print(f"{status} [{r['mode']}] {r['pid_str']:12} {r['name'][:40]:40} {r['response'] or 'NO DATA'}")
        return

    table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold cyan", pad_edge=False)
    table.add_column("",        width=3)
    table.add_column("Mode",    width=6)
    table.add_column("PID",     width=12)
    table.add_column("Name",    width=35)
    table.add_column("Response")

    verified = 0
    for r in results:
        if r["verified"]:
            verified += 1
            icon    = Text("✓", style="bold green")
            resp    = Text(r["response"] or "", style="dim green")
        else:
            icon    = Text("✗", style="dim red")
            resp    = Text("NO DATA", style="dim")
        table.add_row(icon, str(r["mode"]), r["pid_str"], r["name"][:34], resp)

    from rich.panel import Panel
    console.print(Panel(
        table,
        title=f"[bold cyan]ELM327 Validation — {verified}/{len(results)} PIDs responded[/bold cyan]",
        border_style="cyan",
        padding=(0, 1),
    ))


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ELM327 PID validator for Frank")
    parser.add_argument("--port",    metavar="PORT", help="Serial port (e.g. /dev/ttyUSB0)")
    parser.add_argument("--baud",    type=int, default=38400)
    parser.add_argument("--session", metavar="SLUG", help="Frank session to use")
    parser.add_argument("--scan",    action="store_true", help="Scan all Mode 01 PIDs")
    parser.add_argument("--dry-run", action="store_true", help="Show PIDs without connecting")
    args = parser.parse_args()

    import config
    import sessions as session_manager
    from knowledge_base import KnowledgeBase

    # Load session
    existing = session_manager.list_sessions()
    if args.session:
        match = next((s for s in existing if s["slug"] == args.session or s["name"] == args.session), None)
        if match:
            session_manager.activate_session(match)
    elif existing:
        session_manager.activate_session(existing[0])

    kb = KnowledgeBase()
    if not kb.load():
        print("No knowledge base found. Run Frank first to create a session.")
        sys.exit(1)

    pids = extract_pids_from_kb(kb)
    print(f"Found {len(pids)} PID/address candidates in knowledge base.")

    if not pids:
        print("No PIDs discovered yet — run Frank to research more topics first.")
        sys.exit(0)

    if args.dry_run:
        print("\n[DRY RUN] Would test these PIDs:\n")
        for item in pids:
            print(f"  [{item['mode']:4}] {item['pid_str']:12} — {item['name'][:50]}")
            print(f"         fact: {item['fact'][:80]}")
        sys.exit(0)

    if not args.port:
        print("ERROR: --port required unless using --dry-run")
        sys.exit(1)

    elm = ELM327(args.port, baud=args.baud)
    print(f"Connecting to ELM327 on {args.port} @ {args.baud} baud...")
    if not elm.connect():
        print("Connection failed. Check port and adapter.")
        sys.exit(1)

    print(f"Connected. Protocol: {elm.get_protocol()}")
    v = elm.get_voltage()
    if v:
        print(f"Battery: {v}")

    if args.scan:
        print("\nScanning Mode 01 PID support bitmap...")
        supported = scan_mode01(elm)
        print(f"Supported Mode 01 PIDs: {[hex(p) for p in supported]}")

    print(f"\nTesting {len(pids)} PIDs...\n")
    results = validate_pids(elm, pids)
    elm.disconnect()

    print_results(results)

    verified_count = sum(1 for r in results if r["verified"])
    if verified_count:
        updated = mark_verified_in_kb(kb, results)
        kb.save()
        print(f"\nMarked {updated} facts as hardware_verified in KB.")
    else:
        print("\nNo PIDs verified — nothing written to KB.")


if __name__ == "__main__":
    main()
