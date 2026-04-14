#!/usr/bin/env python3
"""Cross-platform system diagnostics tool.

Collects RAM, CPU, interrupts, temperatures, disk, process, and display data.
Detects anomalies and generates reports.

Usage:
    python main.py                  # Full run: collect + report + analysis
    python main.py --report-only    # Collect and save raw report (JSON), skip analysis
    python main.py --analyze-only   # Print analysis to terminal, no files written
    python main.py --section ram    # Collect and print only one section
    python main.py --section cpu
    python main.py --section disk
    python main.py --section temps
    python main.py --section processes
    python main.py --section display
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

    return system, ram, cpu, temps, disk, processes, display


def format_report_json(timestamp, system, ram, cpu, temps, disk, processes, display):
    return {
        "timestamp": timestamp,
        "system": asdict(system),
        "ram": asdict(ram),
        "cpu": asdict(cpu),
        "temperatures": asdict(temps),
        "disk": asdict(disk),
        "processes": asdict(processes),
        "display": asdict(display),
    }


def format_analysis_md(timestamp, system, anomalies, ram, cpu, temps, disk, processes, display):
    lines = [
        f"# System Analysis — {timestamp}",
        "",
        f"**Host:** {system.hostname}  ",
        f"**OS:** {system.os_name} ({system.os_version})  ",
        f"**CPU:** {system.cpu_model} ({system.cpu_cores}C/{system.cpu_threads}T)  ",
        f"**RAM:** {system.total_ram_gb} GB  ",
        "",
    ]

    # Summary
    lines.append("## Quick Summary")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|---|---|")
    lines.append(f"| RAM Used | {ram.used_gb} GB / {ram.total_gb} GB ({ram.percent_used}%) |")
    lines.append(f"| CPU Load | {cpu.load_percent}% |")
    if cpu.interrupts_per_sec is not None:
        lines.append(f"| Interrupts/sec | {cpu.interrupts_per_sec:,.0f} |")
    if cpu.interrupt_time_percent is not None:
        lines.append(f"| % Interrupt Time | {cpu.interrupt_time_percent:.2f}% |")
    if cpu.dpc_time_percent is not None:
        lines.append(f"| % DPC Time | {cpu.dpc_time_percent:.2f}% |")
    if temps.readings:
        max_temp = max(r.get("current_c", 0) for r in temps.readings if r.get("current_c"))
        lines.append(f"| Max Temperature | {max_temp:.0f}°C |")
    lines.append(f"| Process RAM Total | {processes.total_process_ram_gb} GB |")
    lines.append("")

    # Anomalies
    if anomalies:
        lines.append("## Anomalies Detected")
        lines.append("")

        critical = [a for a in anomalies if a.severity == "critical"]
        warnings = [a for a in anomalies if a.severity == "warning"]
        infos = [a for a in anomalies if a.severity == "info"]

        for label, group in [("CRITICAL", critical), ("WARNING", warnings), ("INFO", infos)]:
            if group:
                for a in group:
                    icon = {"CRITICAL": "[!!!]", "WARNING": "[!!]", "INFO": "[i]"}[label]
                    lines.append(f"- **{icon} {label}** [{a.category}] {a.message}")
        lines.append("")
    else:
        lines.append("## No Anomalies Detected")
        lines.append("")
        lines.append("All metrics within normal ranges.")
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

    return "\n".join(lines)


def print_section(collectors, section: str):
    """Print a single section to terminal without saving files."""
    if section == "ram":
        data = collectors.collect_ram()
    elif section == "cpu":
        data = collectors.collect_cpu()
    elif section == "temps":
        data = collectors.collect_temperatures()
    elif section == "disk":
        data = collectors.collect_disk()
    elif section == "processes":
        data = collectors.collect_processes()
    elif section == "display":
        data = collectors.collect_display()
    elif section == "system":
        data = collectors.collect_system_info()
    else:
        print(f"Unknown section: {section}")
        print("Available: system, ram, cpu, temps, disk, processes, display")
        sys.exit(1)

    print(json.dumps(asdict(data), indent=2, default=str))


def main():
    parser = argparse.ArgumentParser(description="Cross-platform system diagnostics")
    parser.add_argument("--report-only", action="store_true",
                        help="Save raw JSON report only, skip analysis")
    parser.add_argument("--analyze-only", action="store_true",
                        help="Print analysis to terminal, don't save files")
    parser.add_argument("--section", type=str,
                        help="Print a single section (ram, cpu, disk, temps, processes, display, system)")
    parser.add_argument("--top-n", type=int, default=20,
                        help="Number of top processes to include (default: 20)")
    args = parser.parse_args()

    collectors = get_collectors()

    # Single section mode
    if args.section:
        print_section(collectors, args.section)
        return

    # Full collection
    system, ram, cpu, temps, disk, processes, display = collect_all(collectors)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    base_dir = Path(__file__).parent

    # Run analysis
    anomalies = analyze(system, ram, cpu, temps, disk, processes, display)

    # Print summary to terminal
    print(f"\n{'='*60}")
    print(f"  {system.hostname} — {system.os_name}")
    print(f"  {system.cpu_model}")
    print(f"  RAM: {ram.used_gb}/{ram.total_gb} GB ({ram.percent_used}%)")
    print(f"  CPU: {cpu.load_percent}%")
    if anomalies:
        crit = sum(1 for a in anomalies if a.severity == "critical")
        warn = sum(1 for a in anomalies if a.severity == "warning")
        info = sum(1 for a in anomalies if a.severity == "info")
        print(f"  Anomalies: {crit} critical, {warn} warnings, {info} info")
    else:
        print(f"  No anomalies detected")
    print(f"{'='*60}\n")

    if args.analyze_only:
        # Print full analysis to terminal
        md = format_analysis_md(timestamp, system, anomalies, ram, cpu, temps, disk, processes, display)
        print(md)
        return

    # Save raw report
    report_dir = base_dir / "logs" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_data = format_report_json(timestamp, system, ram, cpu, temps, disk, processes, display)
    report_path = report_dir / f"{timestamp}_report.json"
    report_path.write_text(json.dumps(report_data, indent=2, default=str))
    print(f"Report saved: {report_path}")

    if args.report_only:
        return

    # Save analysis
    analysis_dir = base_dir / "logs" / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    analysis_md = format_analysis_md(timestamp, system, anomalies, ram, cpu, temps, disk, processes, display)
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
