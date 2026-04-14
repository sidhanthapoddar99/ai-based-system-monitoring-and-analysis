# SysDiag — Cross-Platform System Diagnostics

A Python-based system health diagnostic tool that collects hardware/software metrics, detects anomalies, and generates structured reports. Works on **Windows**, **Linux**, **macOS**, and **WSL**.

## What It Collects

| Category | Metrics |
|---|---|
| **RAM** | Total/used/free, kernel memory pools (nonpaged/paged), committed bytes, standby cache, page faults, swap |
| **CPU** | Load %, per-core usage, interrupts/sec, % interrupt time, DPC time, context switches/sec, system calls/sec, processor queue length, load average (Linux/WSL) |
| **Stability** | System uptime, BSOD/crash dumps (Windows), kernel panic logs (Linux/Mac), critical event log errors, pool allocation failures, system-wide handle/thread/process counts |
| **Temperatures** | ACPI thermal zones, per-sensor readings (Linux), SMC (Mac) |
| **Disk** | Per-partition usage, I/O throughput (read/write bytes/sec), busy %, queue length |
| **Processes** | Top N by RAM, top N by CPU time, grouped by application name |
| **Display** | GPU name, resolution, refresh rate, VRAM, driver version, multi-GPU detection |
| **WSL** (Windows) | Per-distro RAM/CPU/load average, OOM kills, kernel panics, top processes inside WSL, vmmem host memory, .wslconfig |

## Requirements

- **Python** 3.10+ (tested on 3.14)
- **psutil** (the only dependency)
- **jq** (optional, for CLI JSON filtering)

### Platform-Specific

| Platform | Extra Requirements |
|---|---|
| Windows | PowerShell (built-in), `wsl.exe` for WSL metrics |
| Linux | `/proc` filesystem, `xrandr` (optional, for display info) |
| macOS | `sysctl`, `system_profiler`, `sudo powermetrics` (optional, for detailed temps) |

## Setup

```bash
# Clone or navigate to the project
cd "path/to/Claude Env"

# Create virtual environment
python -m venv venv

# Activate
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## Usage

### Full Diagnostic (Report + Analysis)

```bash
python main.py
```

Outputs:
- `logs/reports/YYYY-MM-DD_HHMMSS_report.json` — raw machine-readable data
- `logs/analysis/YYYY-MM-DD_HHMMSS_analysis.md` — human-readable findings with anomaly detection
- Terminal summary with anomaly list

### Report Only (No Analysis)

```bash
python main.py --report-only
```

### Terminal Analysis (No Files)

```bash
python main.py --analyze-only
```

### Single Section Query

Print one section as JSON to stdout — no files written:

```bash
python main.py --section system       # Machine info + uptime
python main.py --section ram           # RAM + kernel pools + page faults
python main.py --section cpu           # CPU + interrupts + context switches
python main.py --section temps         # Temperature sensors
python main.py --section disk          # Disk usage + I/O
python main.py --section processes     # Process list (top 20)
python main.py --section display       # GPU/monitor info
python main.py --section stability     # Crash dumps, kernel errors, handle counts
python main.py --section wsl           # WSL distro details (Windows only)
```

### View Past Reports

```bash
# Pretty-print the latest report
python main.py --view latest

# View a specific report
python main.py --view logs/reports/2026-04-15_035228_report.json

# Filter with jq-style dot notation
python main.py --view latest --jq '.ram.details'
python main.py --view latest --jq '.stability.kernel_errors'
python main.py --view latest --jq '.wsl.distros'
python main.py --view latest --jq '.processes.by_cpu[0]'
python main.py --view latest --jq '.cpu'
```

You can also use `jq` directly:

```bash
cat logs/reports/*_report.json | jq '.stability.bsod_dumps'
```

## Anomaly Detection

The analyzer automatically flags issues against these thresholds:

| Category | Condition | Severity |
|---|---|---|
| RAM usage > 85% | memory pressure | warning |
| RAM usage > 95% | critical memory | critical |
| Nonpaged pool > 1 GB | kernel driver leak | warning |
| Committed > physical RAM | overcommitted | warning |
| CPU load > 80% | high utilization | warning |
| CPU load > 95% | saturated | critical |
| Interrupts > 100,000/sec | driver issue | warning |
| Interrupt time > 5% | hardware problem | warning |
| DPC time > 5% | driver latency | warning |
| Context switches > 100,000/sec | thread contention | warning |
| Temperature > 80°C | running hot | warning |
| Temperature > 95°C | thermal throttle | critical |
| Disk > 90% full | low space | warning |
| Disk > 95% full | critical space | critical |
| Disk queue > 2.0 | I/O bottleneck | warning |
| Process RAM > 4 GB | large process | info |
| Process CPU > 10,000 sec | possible runaway | warning |
| Kernel/process RAM gap > 4 GB | kernel leak | warning |
| BSOD dumps found | system crashed | critical |
| Critical event log errors | kernel errors | warning |
| Page faults > 5,000/sec | heavy paging | warning |
| Nonpaged pool failures > 0 | crash risk | critical |
| Handle count > 500,000 | handle leak | warning |
| Uptime < 1 hour | recent reboot | info |
| Multi-GPU rendering | input latency risk | info |
| Mixed refresh rates | vsync mismatch | info |
| WSL OOM kills > 0 | memory pressure | warning |

## Output Structure

### JSON Report

```json
{
  "timestamp": "2026-04-15_035228",
  "system": { "hostname", "os_name", "cpu_model", "uptime_seconds", "boot_time", ... },
  "ram": { "total_gb", "used_gb", "percent_used", "details": { "nonpaged_pool_mb", ... } },
  "cpu": { "load_percent", "interrupts_per_sec", "context_switches_per_sec", ... },
  "temperatures": { "readings": [...], "source": "..." },
  "disk": { "partitions": [...], "io": { "read_bytes_sec", "queue_length", ... } },
  "processes": { "by_ram": [...], "by_cpu": [...], "grouped_by_name": [...] },
  "display": { "displays": [{ "gpu", "refresh_rate", "resolution", ... }] },
  "stability": { "uptime_hours", "bsod_dumps": [...], "kernel_errors": [...], ... },
  "wsl": { "distros": [{ "name", "ram_mb", "load_avg_1m", "oom_kills", ... }], ... }
}
```

### Analysis Markdown

The analysis MD includes:
- System info header with uptime
- Quick summary table of all key metrics
- Anomaly list with severity levels
- Stability section (crash dumps, kernel errors, crash config)
- RAM breakdown including kernel pools
- Disk usage and I/O
- Top processes by RAM and CPU time
- Grouped application memory
- Display/GPU with refresh rates
- WSL distro details with per-distro processes

## Project Structure

```
├── main.py                     # CLI entry point + report formatting
├── requirements.txt            # psutil>=6.0.0
├── CLAUDE.md                   # AI assistant context file
├── README.md                   # This file
├── .gitignore
├── modules/
│   ├── base.py                 # Dataclasses + anomaly detection engine
│   ├── windows/
│   │   └── collectors.py       # psutil + PowerShell + WMI + WSL bridge
│   ├── linux/
│   │   └── collectors.py       # psutil + /proc + dmesg + journalctl
│   └── mac/
│       └── collectors.py       # psutil + sysctl + system_profiler + ioreg
└── logs/
    ├── reports/                # Raw JSON snapshots (.gitignored)
    └── analysis/               # Markdown analysis (.gitignored)
```

## Notes

- Some metrics require **admin/sudo** for full data (Windows temps, Linux dmesg, macOS powermetrics). The tool degrades gracefully.
- Windows collection takes ~15-20 seconds due to PowerShell `Get-Counter` sampling intervals.
- System processes (System Idle Process, svchost, csrss, etc.) are automatically excluded from CPU time anomaly detection.
- WSL collection runs `wsl -d <distro>` commands from the Windows host to get Linux-side metrics without needing a separate install inside WSL.
