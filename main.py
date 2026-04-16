#!/usr/bin/env python3
"""Cross-platform system diagnostics tool.

Usage:
    python main.py                      # Full run: collect + report + analysis
    python main.py --report-only        # Save raw JSON report only
    python main.py --analyze-only       # Print analysis to terminal, no files
    python main.py --section ram        # Print one section as JSON
    python main.py --section stability  # Crash indicators, BSOD, kernel errors
    python main.py --section wsl        # WSL distro details (Windows only)
    python main.py --view latest        # Pretty-print latest report JSON
    python main.py --view <path>        # Pretty-print a specific report JSON
    python main.py --view latest --jq '.ram.details'  # Filter with jq syntax
"""

import argparse
import json
import platform
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from modules.base import analyze


def get_collectors():
    os_name = platform.system()
    if os_name == "Windows":
        from modules.windows import collectors
    elif os_name == "Linux":
        from modules.linux import collectors
    elif os_name == "Darwin":
        from modules.mac import collectors
    else:
        print(f"Unsupported platform: {os_name}")
        sys.exit(1)
    return collectors


def collect_all(collectors):
    print("Collecting system info...", end=" ", flush=True)
    system = collectors.collect_system_info()
    print("done")

    print("Collecting RAM...", end=" ", flush=True)
    ram = collectors.collect_ram()
    print("done")

    print("Collecting CPU (sampling ~2s)...", end=" ", flush=True)
    cpu = collectors.collect_cpu()
    print("done")

    print("Collecting temperatures...", end=" ", flush=True)
    temps = collectors.collect_temperatures()
    print("done")

    print("Collecting disk...", end=" ", flush=True)
    disk = collectors.collect_disk()
    print("done")

    print("Collecting processes...", end=" ", flush=True)
    processes = collectors.collect_processes()
    print("done")

    print("Collecting display info...", end=" ", flush=True)
    display = collectors.collect_display()
    print("done")

    print("Collecting GPU metrics...", end=" ", flush=True)
    gpu = collectors.collect_gpu() if hasattr(collectors, "collect_gpu") else None
    print("done")

    print("Collecting network info...", end=" ", flush=True)
    network = collectors.collect_network() if hasattr(collectors, "collect_network") else None
    print("done")

    print("Collecting storage health...", end=" ", flush=True)
    storage_health = collectors.collect_storage_health() if hasattr(collectors, "collect_storage_health") else None
    print("done")

    print("Collecting stability metrics...", end=" ", flush=True)
    stability = collectors.collect_stability()
    print("done")

    power = None
    if hasattr(collectors, "collect_power"):
        print("Collecting power/sleep info...", end=" ", flush=True)
        power = collectors.collect_power()
        print("done")

    wsl = None
    if platform.system() == "Windows" and hasattr(collectors, "collect_wsl"):
        print("Collecting WSL details...", end=" ", flush=True)
        wsl = collectors.collect_wsl()
        print("done")

    return system, ram, cpu, temps, disk, processes, display, stability, wsl, gpu, network, storage_health, power


def format_report_json(timestamp, system, ram, cpu, temps, disk, processes,
                       display, stability, wsl, gpu, network, storage_health,
                       power=None):
    report = {
        "timestamp": timestamp,
        "system": asdict(system),
        "ram": asdict(ram),
        "cpu": asdict(cpu),
        "temperatures": asdict(temps),
        "disk": asdict(disk),
        "processes": asdict(processes),
        "display": asdict(display),
        "stability": asdict(stability),
    }
    if wsl:
        report["wsl"] = asdict(wsl)
    if gpu:
        report["gpu"] = asdict(gpu)
    if network:
        report["network"] = asdict(network)
    if storage_health:
        report["storage_health"] = asdict(storage_health)
    if power:
        report["power"] = asdict(power)
    return report


def format_analysis_md(timestamp, system, anomalies, ram, cpu, temps, disk,
                       processes, display, stability, wsl, gpu, network,
                       storage_health, power=None):
    lines = [
        f"# System Analysis — {timestamp}",
        "",
        f"**Host:** {system.hostname}  ",
        f"**OS:** {system.os_name} ({system.os_version})  ",
        f"**CPU:** {system.cpu_model} ({system.cpu_cores}C/{system.cpu_threads}T)  ",
        f"**RAM:** {system.total_ram_gb} GB  ",
    ]
    if system.uptime_seconds:
        hours = system.uptime_seconds / 3600
        days = hours / 24
        if days >= 1:
            lines.append(f"**Uptime:** {days:.1f} days ({hours:.0f} hours)  ")
        else:
            lines.append(f"**Uptime:** {hours:.1f} hours  ")
    if system.boot_time:
        lines.append(f"**Boot Time:** {system.boot_time}  ")
    lines.append("")

    # Summary table
    lines.append("## Quick Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| RAM Used | {ram.used_gb} GB / {ram.total_gb} GB ({ram.percent_used}%) |")
    lines.append(f"| CPU Load | {cpu.load_percent}% |")
    if cpu.interrupts_per_sec is not None:
        lines.append(f"| Interrupts/sec | {cpu.interrupts_per_sec:,.0f} |")
    if cpu.interrupt_time_percent is not None:
        lines.append(f"| % Interrupt Time | {cpu.interrupt_time_percent:.2f}% |")
    if cpu.dpc_time_percent is not None:
        lines.append(f"| % DPC Time | {cpu.dpc_time_percent:.2f}% |")
    if cpu.context_switches_per_sec is not None:
        lines.append(f"| Context Switches/sec | {cpu.context_switches_per_sec:,.0f} |")
    if cpu.system_calls_per_sec is not None:
        lines.append(f"| System Calls/sec | {cpu.system_calls_per_sec:,.0f} |")
    if cpu.details.get("processor_queue_length") is not None:
        lines.append(f"| Processor Queue Length | {cpu.details['processor_queue_length']} |")
    if cpu.details.get("load_avg_1m") is not None:
        lines.append(f"| Load Average (1/5/15m) | {cpu.details['load_avg_1m']}/{cpu.details['load_avg_5m']}/{cpu.details['load_avg_15m']} |")
    if temps.readings:
        valid = [r.get("current_c", 0) for r in temps.readings if r.get("current_c")]
        if valid:
            lines.append(f"| Max Temperature | {max(valid):.0f}°C |")
    lines.append(f"| Process RAM Total | {processes.total_process_ram_gb} GB |")
    if stability.process_count:
        lines.append(f"| Process Count | {stability.process_count} |")
    if stability.handle_count:
        lines.append(f"| System Handle Count | {stability.handle_count:,} |")
    if stability.thread_count:
        lines.append(f"| System Thread Count | {stability.thread_count:,} |")
    if stability.page_faults_per_sec is not None:
        lines.append(f"| Page Faults/sec | {stability.page_faults_per_sec:,} |")
    lines.append("")

    # Anomalies
    if anomalies:
        lines.append("## Anomalies Detected")
        lines.append("")
        for label, group in [
            ("CRITICAL", [a for a in anomalies if a.severity == "critical"]),
            ("WARNING", [a for a in anomalies if a.severity == "warning"]),
            ("INFO", [a for a in anomalies if a.severity == "info"]),
        ]:
            for a in group:
                icon = {"CRITICAL": "[!!!]", "WARNING": "[!!]", "INFO": "[i]"}[label]
                lines.append(f"- **{icon} {label}** [{a.category}] {a.message}")
        lines.append("")
    else:
        lines.append("## No Anomalies Detected")
        lines.append("")
        lines.append("All metrics within normal ranges.")
        lines.append("")

    # Stability
    lines.append("## Stability")
    lines.append("")
    if stability.uptime_hours:
        lines.append(f"**Uptime:** {stability.uptime_hours:.1f} hours")
    if stability.bsod_dumps:
        lines.append("")
        lines.append(f"### BSOD / Crash Dumps ({len(stability.bsod_dumps)} found)")
        lines.append("")
        lines.append("| File | Date | Size (KB) |")
        lines.append("|---|---|---|")
        for d in stability.bsod_dumps:
            lines.append(f"| {d['file']} | {d['date']} | {d['size_kb']} |")
    else:
        lines.append("")
        lines.append("No crash dumps found.")
    if stability.kernel_errors:
        lines.append("")
        lines.append(f"### Kernel/System Errors (last 48h) — {len(stability.kernel_errors)} events")
        lines.append("")
        for e in stability.kernel_errors[:10]:
            src = e.get("source", "")
            eid = e.get("event_id", "")
            msg = e.get("message", e.get("msg", ""))
            time_str = e.get("time", "")
            if src:
                lines.append(f"- `[{time_str}]` **{src}** (ID:{eid}): {msg}")
            else:
                lines.append(f"- {msg}")
        if len(stability.kernel_errors) > 10:
            lines.append(f"- ... and {len(stability.kernel_errors) - 10} more")
    if stability.details:
        if stability.details.get("crash_dump_type"):
            lines.append(f"\n**Crash dump config:** {stability.details['crash_dump_type']}")
        if stability.details.get("auto_reboot_on_crash") is not None:
            lines.append(f"**Auto-reboot on crash:** {stability.details['auto_reboot_on_crash']}")
    lines.append("")

    # Power / Sleep
    if power:
        lines.append("## Power & Sleep")
        lines.append("")

        if power.sleep_blockers:
            lines.append("### Active Sleep Blockers")
            lines.append("")
            lines.append("| PID | Process | Type | Duration | Name |")
            lines.append("|---|---|---|---|---|")
            for b in power.sleep_blockers:
                lines.append(
                    f"| {b.get('pid', '?')} | {b.get('process', '?')} | "
                    f"{b.get('assertion_type', '?')} | {b.get('duration', '?')} | "
                    f"{b.get('name', '?')} |"
                )
            lines.append("")

        if power.kernel_assertions:
            lines.append("### Kernel Wake Assertions")
            lines.append("")
            lines.append("| Type | Description | Owner |")
            lines.append("|---|---|---|")
            for k in power.kernel_assertions:
                lines.append(
                    f"| {k.get('type', '?')} | {k.get('description', '?')} | "
                    f"{k.get('owner', '?')} |"
                )
            lines.append("")

        if power.sleep_wake_stats:
            lines.append("### Sleep/Wake Stats")
            lines.append("")
            lines.append("| Metric | Value |")
            lines.append("|---|---|")
            for key, val in power.sleep_wake_stats.items():
                label = key.replace("_", " ").title()
                lines.append(f"| {label} | {val} |")
            lines.append("")

        if power.power_settings:
            lines.append("### Power Settings")
            lines.append("")
            lines.append("| Setting | Value |")
            lines.append("|---|---|")
            for key, val in power.power_settings.items():
                lines.append(f"| {key} | {val} |")
            lines.append("")

        if power.recent_wake_events:
            lines.append("### Recent Sleep/Wake Events")
            lines.append("")
            lines.append("| Time | Event | Details |")
            lines.append("|---|---|---|")
            for e in power.recent_wake_events:
                lines.append(
                    f"| {e.get('time', '?')} | {e.get('event', '?')} | "
                    f"{e.get('details', '')} |"
                )
            lines.append("")

    # RAM details
    if ram.details:
        lines.append("## RAM Details")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|---|---|")
        for key, val in ram.details.items():
            label = key.replace("_", " ").title()
            if "gb" in key:
                lines.append(f"| {label} | {val} GB |")
            elif "mb" in key:
                lines.append(f"| {label} | {val} MB |")
            else:
                lines.append(f"| {label} | {val} |")
        lines.append("")

    # Disk
    if disk.partitions:
        lines.append("## Disk Usage")
        lines.append("")
        lines.append("| Device | Mount | Size (GB) | Used (GB) | Free (GB) | Used % |")
        lines.append("|---|---|---|---|---|---|")
        for p in disk.partitions:
            lines.append(
                f"| {p['device']} | {p['mountpoint']} | {p['total_gb']} | "
                f"{p['used_gb']} | {p['free_gb']} | {p['percent']}% |"
            )
        lines.append("")
    if disk.io:
        lines.append("### Disk I/O")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|---|---|")
        for key, val in disk.io.items():
            label = key.replace("_", " ").title()
            if "bytes_sec" in key:
                lines.append(f"| {label} | {val/1024:.1f} KB/s |")
            else:
                lines.append(f"| {label} | {val} |")
        lines.append("")

    # Top processes
    lines.append("## Top Processes by RAM")
    lines.append("")
    lines.append("| PID | Name | RAM (MB) | CPU (sec) | Threads |")
    lines.append("|---|---|---|---|---|")
    for p in processes.by_ram[:15]:
        lines.append(
            f"| {p['pid']} | {p['name']} | {p['ram_mb']} | "
            f"{p['cpu_seconds']} | {p['threads']} |"
        )
    lines.append("")

    lines.append("## Top Processes by CPU Time")
    lines.append("")
    lines.append("| PID | Name | CPU (sec) | RAM (MB) | Threads |")
    lines.append("|---|---|---|---|---|")
    for p in processes.by_cpu[:15]:
        lines.append(
            f"| {p['pid']} | {p['name']} | {p['cpu_seconds']} | "
            f"{p['ram_mb']} | {p['threads']} |"
        )
    lines.append("")

    lines.append("## Grouped by Application")
    lines.append("")
    lines.append("| Name | Instances | Total RAM (MB) | Total CPU (sec) |")
    lines.append("|---|---|---|---|")
    for g in processes.grouped_by_name[:15]:
        lines.append(
            f"| {g['name']} | {g['count']} | {g['total_ram_mb']} | "
            f"{g['total_cpu_seconds']} |"
        )
    lines.append("")

    # Display
    if display.displays:
        lines.append("## Display / GPU")
        lines.append("")
        lines.append("| GPU | Resolution | Refresh Rate | Details |")
        lines.append("|---|---|---|---|")
        for d in display.displays:
            rate = f"{d['refresh_rate']} Hz" if d.get("refresh_rate") else "N/A"
            res = d.get("resolution", "?")
            extra = []
            if d.get("vram_gb"):
                extra.append(f"VRAM: {d['vram_gb']} GB")
            if d.get("driver_version"):
                extra.append(f"Driver: {d['driver_version']}")
            if d.get("vram"):
                extra.append(f"VRAM: {d['vram']}")
            lines.append(f"| {d.get('gpu', '?')} | {res} | {rate} | {', '.join(extra) or '-'} |")
        lines.append("")

    # GPU
    if gpu and gpu.gpus:
        lines.append("## GPU")
        lines.append("")
        lines.append("| GPU | Temp | Util | VRAM Used | VRAM Total | Fan | Power | Clocks |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for g in gpu.gpus:
            temp = f"{g['temperature_c']}°C" if g.get("temperature_c") is not None else "-"
            util = f"{g['utilization_percent']}%" if g.get("utilization_percent") is not None else "-"
            vused = f"{g['vram_used_mb']} MB" if g.get("vram_used_mb") is not None else "-"
            vtotal = f"{g['vram_total_mb']} MB" if g.get("vram_total_mb") is not None else "-"
            fan = f"{g['fan_speed_percent']}%" if g.get("fan_speed_percent") is not None else "-"
            power = f"{g['power_draw_w']:.0f}W / {g['power_limit_w']:.0f}W" if g.get("power_draw_w") is not None else "-"
            clocks = f"{g['clock_graphics_mhz']}MHz" if g.get("clock_graphics_mhz") is not None else "-"
            lines.append(f"| {g.get('name', '?')} | {temp} | {util} | {vused} | {vtotal} | {fan} | {power} | {clocks} |")
        lines.append("")

    # Network
    if network:
        if network.adapters:
            lines.append("## Network")
            lines.append("")
            lines.append("| Adapter | Description | Link Speed | Driver |")
            lines.append("|---|---|---|---|")
            for a in network.adapters:
                lines.append(f"| {a.get('name', '?')} | {a.get('description', '')} | "
                             f"{a.get('link_speed', '?')} | {a.get('driver_version', '-')} |")
            lines.append("")
        if network.latency:
            lines.append("### Latency")
            lines.append("")
            lines.append("| Metric | Value |")
            lines.append("|---|---|")
            lat = network.latency
            if lat.get("ping_avg_ms") is not None:
                lines.append(f"| Ping (8.8.8.8) | {lat['ping_min_ms']}-{lat['ping_max_ms']}ms (avg {lat['ping_avg_ms']}ms) |")
            if lat.get("packet_loss_percent") is not None:
                lines.append(f"| Packet Loss | {lat['packet_loss_percent']}% |")
            if lat.get("dns_ms") is not None:
                lines.append(f"| DNS Resolution | {lat['dns_ms']}ms |")
            lines.append("")

    # Storage Health
    if storage_health:
        if storage_health.disks:
            lines.append("## Storage Health")
            lines.append("")
            lines.append("| Drive | Type | Health | Status | Size | Bus |")
            lines.append("|---|---|---|---|---|---|")
            for d in storage_health.disks:
                lines.append(f"| {d.get('name', '?')} | {d.get('media_type', '?')} | "
                             f"{d.get('health_status', '?')} | {d.get('operational_status', '?')} | "
                             f"{d.get('size_gb', '?')} GB | {d.get('bus_type', '?')} |")
            lines.append("")
        if storage_health.problem_devices:
            lines.append("### Problem Devices")
            lines.append("")
            lines.append("| Device | Error Code |")
            lines.append("|---|---|")
            for d in storage_health.problem_devices:
                lines.append(f"| {d.get('name', '?')} | {d.get('error_code', '?')} |")
            lines.append("")

    # Temperatures
    if temps.readings:
        lines.append("## Temperatures")
        lines.append("")
        lines.append(f"Source: {temps.source}")
        lines.append("")
        lines.append("| Sensor | Current (°C) | High (°C) | Critical (°C) |")
        lines.append("|---|---|---|---|")
        for r in temps.readings:
            curr = f"{r['current_c']:.1f}" if r.get("current_c") is not None else "N/A"
            high = f"{r['high_c']:.1f}" if r.get("high_c") is not None else "-"
            crit = f"{r['critical_c']:.1f}" if r.get("critical_c") is not None else "-"
            lines.append(f"| {r.get('label', '?')} | {curr} | {high} | {crit} |")
        lines.append("")

    # WSL
    if wsl and wsl.distros:
        lines.append("## WSL Distros")
        lines.append("")
        for distro in wsl.distros:
            default_mark = " (default)" if distro.get("is_default") else ""
            lines.append(f"### {distro['name']}{default_mark}")
            lines.append("")
            lines.append(f"- **State:** {distro.get('state', '?')}")
            lines.append(f"- **WSL Version:** {distro.get('wsl_version', '?')}")
            if "ram_mb" in distro:
                lines.append(f"- **RAM Used:** {distro['ram_mb']} MB / {distro.get('total_ram_mb', '?')} MB ({distro.get('ram_percent', '?')}%)")
            if "load_avg_1m" in distro:
                lines.append(f"- **Load Average:** {distro['load_avg_1m']} / {distro['load_avg_5m']} / {distro['load_avg_15m']}")
            if distro.get("oom_kills", 0) > 0:
                lines.append(f"- **OOM Kills:** {distro['oom_kills']}")
            if distro.get("kernel_panics", 0) > 0:
                lines.append(f"- **Kernel Panics:** {distro['kernel_panics']}")
            if distro.get("disk_info"):
                lines.append(f"- **Disk:** {distro['disk_info']}")
            if distro.get("top_processes"):
                lines.append("")
                lines.append("| User | PID | CPU% | MEM% | RSS (KB) | Command |")
                lines.append("|---|---|---|---|---|---|")
                for p in distro["top_processes"][:10]:
                    lines.append(
                        f"| {p['user']} | {p['pid']} | {p['cpu_pct']} | "
                        f"{p['mem_pct']} | {p['rss_kb']} | {p['command']} |"
                    )
            lines.append("")

        # vmmem info
        vmmem = wsl.details.get("vmmem_processes", [])
        if vmmem:
            lines.append("### Host-side VM Memory (vmmem)")
            lines.append("")
            for v in vmmem:
                lines.append(f"- **{v['name']}** (PID {v['pid']}): {v['ram_mb']} MB")
            lines.append("")

        # .wslconfig
        if wsl.details.get("wslconfig"):
            lines.append("### .wslconfig")
            lines.append("")
            lines.append("```ini")
            lines.append(wsl.details["wslconfig"])
            lines.append("```")
            lines.append("")
        elif wsl.details.get("wslconfig_note"):
            lines.append(f"> {wsl.details['wslconfig_note']}")
            lines.append("")

    return "\n".join(lines)


def print_section(collectors, section: str):
    """Print a single section to terminal as JSON."""
    collect_fn = {
        "system": collectors.collect_system_info,
        "ram": collectors.collect_ram,
        "cpu": collectors.collect_cpu,
        "temps": collectors.collect_temperatures,
        "disk": collectors.collect_disk,
        "processes": collectors.collect_processes,
        "display": collectors.collect_display,
        "stability": collectors.collect_stability,
    }
    if hasattr(collectors, "collect_gpu"):
        collect_fn["gpu"] = collectors.collect_gpu
    if hasattr(collectors, "collect_network"):
        collect_fn["network"] = collectors.collect_network
    if hasattr(collectors, "collect_storage_health"):
        collect_fn["storage"] = collectors.collect_storage_health
    if hasattr(collectors, "collect_power"):
        collect_fn["power"] = collectors.collect_power

    if section == "wsl":
        if not hasattr(collectors, "collect_wsl"):
            print("WSL collection is only available on Windows.")
            sys.exit(1)
        data = collectors.collect_wsl()
    elif section in collect_fn:
        data = collect_fn[section]()
    else:
        print(f"Unknown section: {section}")
        print(f"Available: {', '.join(list(collect_fn.keys()) + ['wsl'])}")
        sys.exit(1)

    print(json.dumps(asdict(data), indent=2, default=str))


def view_report(path_or_latest: str, jq_filter: str | None = None):
    """Pretty-print a report JSON file."""
    base_dir = Path(__file__).parent / "logs" / "reports"

    if path_or_latest == "latest":
        reports = sorted(base_dir.glob("*_report.json"))
        if not reports:
            print("No reports found in logs/reports/")
            sys.exit(1)
        filepath = reports[-1]
    else:
        filepath = Path(path_or_latest)

    if not filepath.exists():
        print(f"File not found: {filepath}")
        sys.exit(1)

    if jq_filter:
        # Use Python-based jq-like filtering
        data = json.loads(filepath.read_text())
        result = _jq_filter(data, jq_filter)
        print(json.dumps(result, indent=2, default=str))
    else:
        data = json.loads(filepath.read_text())
        print(json.dumps(data, indent=2, default=str))


def _jq_filter(data, filter_str: str):
    """Simple jq-like dot notation filter (e.g., '.ram.details', '.cpu')."""
    parts = filter_str.strip().lstrip(".").split(".")
    result = data
    for part in parts:
        if not part:
            continue
        # Handle array index like [0]
        if "[" in part:
            key, idx = part.split("[", 1)
            idx = int(idx.rstrip("]"))
            if key:
                result = result[key]
            result = result[idx]
        else:
            result = result[part]
    return result


def main():
    parser = argparse.ArgumentParser(description="Cross-platform system diagnostics")
    parser.add_argument("--report-only", action="store_true",
                        help="Save raw JSON report only, skip analysis")
    parser.add_argument("--analyze-only", action="store_true",
                        help="Print analysis to terminal, don't save files")
    parser.add_argument("--section", type=str,
                        help="Print a single section (ram, cpu, disk, temps, processes, display, stability, power, wsl)")
    parser.add_argument("--view", type=str,
                        help="View a report JSON ('latest' or path)")
    parser.add_argument("--jq", type=str,
                        help="jq-style filter for --view (e.g., '.ram.details')")
    parser.add_argument("--top-n", type=int, default=20,
                        help="Number of top processes to include (default: 20)")
    args = parser.parse_args()

    # View mode
    if args.view:
        view_report(args.view, args.jq)
        return

    collectors = get_collectors()

    # Single section mode
    if args.section:
        print_section(collectors, args.section)
        return

    # Full collection
    system, ram, cpu, temps, disk, processes, display, stability, wsl, gpu, network, storage_health, power = collect_all(collectors)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    base_dir = Path(__file__).parent

    # Run analysis
    anomalies = analyze(system, ram, cpu, temps, disk, processes, display, stability, wsl,
                        gpu, network, storage_health, power)

    # Print summary
    print(f"\n{'='*60}")
    print(f"  {system.hostname} — {system.os_name}")
    print(f"  {system.cpu_model}")
    print(f"  RAM: {ram.used_gb}/{ram.total_gb} GB ({ram.percent_used}%)")
    print(f"  CPU: {cpu.load_percent}%")
    if cpu.interrupts_per_sec is not None:
        print(f"  Interrupts: {cpu.interrupts_per_sec:,.0f}/sec")
    if cpu.context_switches_per_sec is not None:
        print(f"  Context Switches: {cpu.context_switches_per_sec:,.0f}/sec")
    if stability.uptime_hours:
        print(f"  Uptime: {stability.uptime_hours:.1f} hours")
    if stability.bsod_dumps:
        print(f"  BSOD Dumps: {len(stability.bsod_dumps)} found!")
    if power and power.details.get("sleep_blocked_by"):
        blockers = ", ".join(power.details["sleep_blocked_by"])
        print(f"  Sleep blocked by: {blockers}")
    if anomalies:
        crit = sum(1 for a in anomalies if a.severity == "critical")
        warn = sum(1 for a in anomalies if a.severity == "warning")
        info = sum(1 for a in anomalies if a.severity == "info")
        print(f"  Anomalies: {crit} critical, {warn} warnings, {info} info")
    else:
        print("  No anomalies detected")
    print(f"{'='*60}\n")

    if args.analyze_only:
        md = format_analysis_md(timestamp, system, anomalies, ram, cpu, temps, disk,
                                processes, display, stability, wsl, gpu, network,
                                storage_health, power)
        print(md)
        return

    # Save raw report
    report_dir = base_dir / "logs" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_data = format_report_json(timestamp, system, ram, cpu, temps, disk,
                                     processes, display, stability, wsl, gpu,
                                     network, storage_health, power)
    report_path = report_dir / f"{timestamp}_report.json"
    report_path.write_text(json.dumps(report_data, indent=2, default=str))
    print(f"Report saved: {report_path}")

    if args.report_only:
        return

    # Save analysis
    analysis_dir = base_dir / "logs" / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    analysis_md = format_analysis_md(timestamp, system, anomalies, ram, cpu, temps, disk,
                                     processes, display, stability, wsl, gpu, network,
                                     storage_health, power)
    analysis_path = analysis_dir / f"{timestamp}_analysis.md"
    analysis_path.write_text(analysis_md, encoding="utf-8")
    print(f"Analysis saved: {analysis_path}")

    # Print anomalies
    if anomalies:
        print("\nAnomalies:")
        for a in anomalies:
            icon = {"critical": "[!!!]", "warning": "[!!]", "info": "[i]"}[a.severity]
            print(f"  {icon} [{a.category}] {a.message}")


if __name__ == "__main__":
    main()
