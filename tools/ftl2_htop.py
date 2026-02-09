#!/usr/bin/env python3
# /// script
# dependencies = ["ftl2 @ git+https://github.com/benthomasson/ftl2", "rich", "psutil"]
# requires-python = ">=3.13"
# ///
"""Distributed system monitor TUI — live metrics from remote hosts via FTL2 gates.

Usage:
    uv run tools/ftl2_htop.py --inventory hosts.yml [options]
    uv run tools/ftl2_htop.py --state .ftl2-state.json [options]

Options:
    -i, --inventory   Inventory file (hosts.yml)
    -S, --state       State file (.ftl2-state.json) — loads hosts from state
    -g, --groups      Host groups to monitor (default: all groups)
    --interval        Metrics sampling interval in seconds (default: 2)
    --no-processes    Don't include process list (reduces bandwidth)
    --debug           Print raw events to stderr, no TUI

Examples:
    uv run tools/ftl2_htop.py -i hosts.yml
    uv run tools/ftl2_htop.py -S .ftl2-state.json -g terraria
    uv run tools/ftl2_htop.py -i hosts.yml -g webservers databases --interval 1
    uv run tools/ftl2_htop.py -S .ftl2-state.json --no-processes
    uv run tools/ftl2_htop.py -i hosts.yml --debug
"""

import argparse
import asyncio
import sys
from collections import deque

from rich.bar import Bar
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.columns import Columns
from rich.console import Group

from ftl2 import automation

metrics_store: dict[str, dict] = {}
# Per-host history for sparklines: {hostname: {metric: deque([values])}}
history_store: dict[str, dict[str, deque]] = {}
HISTORY_LEN = 30  # ~1 minute at 2s interval
SPARK_CHARS = "▁▂▃▄▅▆▇█"


def _fmt_bytes(n: int) -> str:
    """Format byte count as human-readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


def _fmt_uptime(seconds: int) -> str:
    """Format uptime in days/hours/minutes."""
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days > 0:
        return f"{days}d {hours}h {minutes}m"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _cpu_bar(percent: float, width: int = 20) -> Text:
    """Create a colored CPU usage bar."""
    filled = int(percent / 100 * width)
    empty = width - filled
    if percent > 80:
        color = "red"
    elif percent > 50:
        color = "yellow"
    else:
        color = "green"
    bar = Text()
    bar.append("█" * filled, style=color)
    bar.append("░" * empty, style="dim")
    bar.append(f" {percent:5.1f}%")
    return bar


def _sparkline(values: deque, color: str = "dim", max_val: float | None = None) -> Text:
    """Render a sparkline from a deque of values."""
    if not values:
        return Text("")
    hi = max_val if max_val is not None else max(values)
    if hi == 0:
        hi = 1
    spark = Text()
    for v in values:
        idx = min(int(v / hi * (len(SPARK_CHARS) - 1)), len(SPARK_CHARS) - 1)
        spark.append(SPARK_CHARS[idx], style=color)
    return spark


def _record_history(hostname: str, m: dict) -> None:
    """Record metric values into history for sparklines."""
    if hostname not in history_store:
        history_store[hostname] = {
            "cpu": deque(maxlen=HISTORY_LEN),
            "mem": deque(maxlen=HISTORY_LEN),
            "net_send": deque(maxlen=HISTORY_LEN),
            "net_recv": deque(maxlen=HISTORY_LEN),
        }
    h = history_store[hostname]
    h["cpu"].append(m.get("cpu", {}).get("percent_total", 0))
    h["mem"].append(m.get("memory", {}).get("percent", 0))
    h["net_send"].append(m.get("net", {}).get("bytes_sent_rate", 0))
    h["net_recv"].append(m.get("net", {}).get("bytes_recv_rate", 0))


def _mem_bar(percent: float, used: int, total: int, width: int = 20) -> Text:
    """Create a colored memory usage bar with size labels."""
    filled = int(percent / 100 * width)
    empty = width - filled
    if percent > 80:
        color = "red"
    elif percent > 60:
        color = "yellow"
    else:
        color = "cyan"
    bar = Text()
    bar.append("█" * filled, style=color)
    bar.append("░" * empty, style="dim")
    bar.append(f" {percent:5.1f}% ({_fmt_bytes(used)}/{_fmt_bytes(total)})")
    return bar


def render_host(hostname: str, m: dict) -> Panel:
    """Render a single host's metrics as a Panel."""
    cpu = m.get("cpu", {})
    mem = m.get("memory", {})
    swap = m.get("swap", {})
    disk = m.get("disk", {})
    net = m.get("net", {})
    uptime = m.get("uptime", 0)

    content = []

    # CPU section
    cpu_total = cpu.get("percent_total", 0)
    load = cpu.get("load_avg", [])
    cores = cpu.get("count", "?")
    load_str = " ".join(f"{l:.2f}" for l in load) if load else "?"

    h = history_store.get(hostname, {})

    cpu_line = Text()
    cpu_line.append("CPU  ", style="bold")
    cpu_line.append_text(_cpu_bar(cpu_total))
    cpu_line.append("  ")
    cpu_line.append_text(_sparkline(h.get("cpu", deque()), color="green", max_val=100))
    cpu_line.append(f"  {cores} cores  load: {load_str}")
    content.append(cpu_line)

    # Per-core bars (compact, 2 per line)
    per_cpu = cpu.get("percent_per_cpu", [])
    if per_cpu:
        for i in range(0, len(per_cpu), 4):
            line = Text()
            line.append("     ")
            for j in range(4):
                idx = i + j
                if idx >= len(per_cpu):
                    break
                if j > 0:
                    line.append("  ")
                line.append(f"{idx:>2}: ")
                line.append_text(_cpu_bar(per_cpu[idx], width=10))
            content.append(line)

    # Memory
    mem_line = Text()
    mem_line.append("Mem  ", style="bold")
    mem_line.append_text(
        _mem_bar(mem.get("percent", 0), mem.get("used", 0), mem.get("total", 0))
    )
    mem_line.append("  ")
    mem_line.append_text(_sparkline(h.get("mem", deque()), color="cyan", max_val=100))
    content.append(mem_line)

    # Swap
    swap_line = Text()
    swap_line.append("Swap ", style="bold")
    swap_line.append_text(
        _mem_bar(swap.get("percent", 0), swap.get("used", 0), swap.get("total", 0))
    )
    content.append(swap_line)

    # Disk
    disk_line = Text()
    disk_line.append("Disk ", style="bold")
    disk_line.append_text(
        _mem_bar(disk.get("percent", 0), disk.get("used", 0), disk.get("total", 0))
    )
    content.append(disk_line)

    # Network
    net_line = Text()
    net_line.append("Net  ", style="bold")
    net_line.append(
        f"▲ {_fmt_bytes(net.get('bytes_sent_rate', 0))}/s  "
        f"▼ {_fmt_bytes(net.get('bytes_recv_rate', 0))}/s  "
    )
    net_line.append_text(_sparkline(h.get("net_recv", deque()), color="blue"))
    content.append(net_line)

    # Uptime
    up_line = Text()
    up_line.append("Up   ", style="bold")
    up_line.append(_fmt_uptime(uptime))
    content.append(up_line)

    # Process table
    procs = m.get("processes", [])
    if procs:
        content.append(Text())  # blank line
        proc_table = Table(
            show_header=True,
            header_style="bold",
            box=None,
            padding=(0, 1),
            expand=True,
        )
        proc_table.add_column("PID", justify="right", width=7)
        proc_table.add_column("User", width=10)
        proc_table.add_column("CPU%", justify="right", width=6)
        proc_table.add_column("RSS", justify="right", width=9)
        proc_table.add_column("Status", width=8)
        proc_table.add_column("Name")

        for p in procs[:15]:
            cpu_pct = p.get("cpu_percent", 0)
            cpu_style = (
                "red" if cpu_pct > 50 else "yellow" if cpu_pct > 10 else ""
            )
            proc_table.add_row(
                str(p.get("pid", "")),
                p.get("username", "")[:10],
                Text(f"{cpu_pct:.1f}", style=cpu_style),
                _fmt_bytes(p.get("memory_rss", 0)),
                p.get("status", ""),
                p.get("name", ""),
            )
        content.append(proc_table)

    return Panel(
        Group(*content),
        title=f"[bold]{hostname}[/bold]",
        border_style="blue",
        expand=True,
    )


def render_dashboard() -> Group:
    """Render the full dashboard with all hosts."""
    if not metrics_store:
        return Group(Text("Waiting for metrics...", style="dim italic"))

    panels = []
    for hostname in sorted(metrics_store.keys()):
        panels.append(render_host(hostname, metrics_store[hostname]))

    return Group(*panels)


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="FTL2 distributed system monitor"
    )
    parser.add_argument(
        "-i", "--inventory", help="Inventory file"
    )
    parser.add_argument(
        "-S", "--state", help="State file (loads hosts from state)"
    )
    parser.add_argument(
        "-g", "--groups", nargs="+", help="Host groups to monitor (default: all)"
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Metrics interval in seconds (default: 2)",
    )
    parser.add_argument(
        "--no-processes",
        action="store_true",
        help="Don't include process list",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print raw events to stderr (disables TUI)",
    )
    args = parser.parse_args()

    if not args.inventory and not args.state:
        parser.error("either --inventory or --state is required")

    automation_kwargs = {"gate_subsystem": True}
    if args.inventory:
        automation_kwargs["inventory"] = args.inventory
    if args.state:
        automation_kwargs["state_file"] = args.state

    async with automation(**automation_kwargs) as ftl:
        # Determine groups to monitor
        if args.groups:
            groups = args.groups
        else:
            groups = list(ftl.hosts.groups)

        if not groups:
            print("No groups found in inventory. Add hosts with groups or use --groups.")
            return

        available = set(ftl.hosts.groups) | set(ftl.hosts.keys())
        for group in groups:
            if group not in available:
                print(f"Error: '{group}' not found in inventory. Available groups: {ftl.hosts.groups}")
                return

        # Start monitoring on each group
        for group in groups:
            proxy = ftl[group]
            print(f"Starting monitor on {group} (interval={args.interval}s)")
            await proxy.monitor(
                interval=args.interval,
                include_processes=not args.no_processes,
            )

            def _on_metrics(m, g=group):
                host = m.get("hostname", g)
                metrics_store[host] = m
                _record_history(host, m)

            if args.debug:
                event_count = [0]

                def _debug_handler(m, g=group):
                    event_count[0] += 1
                    host = m.get("hostname", g)
                    cpu = m.get("cpu", {}).get("percent_total", "?")
                    mem = m.get("memory", {}).get("percent", "?")
                    procs = len(m.get("processes", []))
                    keys = list(m.keys())
                    print(
                        f"[{event_count[0]}] SystemMetrics from {host}: "
                        f"CPU={cpu}% Mem={mem}% procs={procs} keys={keys}",
                        file=sys.stderr,
                    )
                    _on_metrics(m, g)

                proxy.on("SystemMetrics", _debug_handler)
            else:
                proxy.on("SystemMetrics", _on_metrics)

        if args.debug:
            # Debug mode: just print events, no TUI
            print("Debug mode: listening for events (Ctrl+C to stop)...",
                  file=sys.stderr)
            await ftl.listen()
            return

        # Run live display and event listener concurrently
        with Live(
            render_dashboard(), refresh_per_second=2, screen=True
        ) as live:

            async def update_display():
                while True:
                    live.update(render_dashboard())
                    await asyncio.sleep(0.5)

            await asyncio.gather(
                ftl.listen(),
                update_display(),
            )


def cli():
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    cli()
