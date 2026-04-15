# System Diagnostics Tool

## 1. Purpose

Cross-platform system health diagnostic tool that collects hardware and software metrics, detects anomalies, and generates reports. Works on **Windows**, **Linux**, **macOS**, and **WSL**.

Collects:
- RAM usage, kernel memory pools (nonpaged/paged), page faults, swap
- CPU load, per-core usage, interrupts/sec, interrupt time, DPC time, context switches/sec, system calls/sec, processor queue length
- Stability: uptime, BSOD/crash dumps, kernel errors from event logs, pool allocation failures, system-wide handle/thread/process counts
- Temperatures (ACPI zones, sensors, SMC)
- Disk usage and I/O throughput
- Process breakdown by RAM and CPU (individual + grouped by app)
- Display/GPU info (refresh rates, resolution, multi-GPU detection)
- GPU metrics (temperature, utilization, VRAM, power draw, clocks via nvidia-smi)
- Network (active adapters, link speed, ping latency, packet loss, DNS resolution time)
- Storage health (SMART/physical disk status, problem devices)
- WSL details (per-distro RAM, load average, OOM kills, processes, .wslconfig)

## 2. How to Run the Code

### Setup

```bash
python -m venv venv

# Activate
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

pip install -r requirements.txt
```

### Requirements

- Python 3.10+ (tested on 3.14)
- `psutil` (installed via requirements.txt)
- `jq` (optional, for CLI JSON filtering)
- Windows: PowerShell (for interrupts, kernel pools, stability, display info)
- Linux: `/proc` filesystem, optionally `xrandr`
- macOS: `sysctl`, `system_profiler`, optionally `sudo powermetrics` for temps

## 3. How to Do Analysis

### Full Run (Report + Analysis)

```bash
./venv/Scripts/python.exe main.py
```

This will:
1. Collect all system metrics including stability and WSL
2. Save raw data as JSON to `logs/reports/YYYY-MM-DD_HHMMSS_report.json`
3. Run anomaly detection against thresholds
4. Save human-readable analysis to `logs/analysis/YYYY-MM-DD_HHMMSS_analysis.md`
5. Print a summary + anomalies to the terminal

### Terminal-Only Analysis (No Files)

```bash
./venv/Scripts/python.exe main.py --analyze-only
```

### Anomaly Thresholds

| Metric | Warning | Critical |
|---|---|---|
| RAM usage | > 85% | > 95% |
| Nonpaged pool (Windows) | > 1024 MB | - |
| CPU load | > 80% | > 95% |
| Interrupts/sec | > 100,000 | - |
| % Interrupt time | > 5% | - |
| % DPC time | > 5% | - |
| Context switches/sec | > 100,000 | - |
| Temperature | > 80°C | > 95°C |
| Disk usage | > 90% | > 95% |
| Disk queue length | > 2.0 | - |
| Process RAM > 4 GB | info | - |
| Process CPU > 10,000 sec | warning | - |
| Kernel memory gap > 4 GB | warning | - |
| BSOD dumps found | - | critical |
| Kernel/system errors | warning | - |
| Page faults > 5,000/sec | warning | - |
| Nonpaged pool failures | - | critical |
| Handle count > 500,000 | warning | - |
| Uptime < 1 hour | info | - |
| WSL OOM kills | warning | - |
| GPU temp > 85°C | warning | > 95°C |
| GPU utilization > 95% | warning | - |
| GPU VRAM > 90% | warning | - |
| Network ping > 100ms | warning | - |
| Packet loss > 0% | warning | - |
| DNS resolution > 200ms | warning | - |
| Disk health != Healthy | - | critical |
| Problem devices (error code) | warning | - |

## 4. How to Generate Reports

### Raw JSON Report Only

```bash
./venv/Scripts/python.exe main.py --report-only
```

### Full Report + Analysis

```bash
./venv/Scripts/python.exe main.py
```

### View Past Reports

```bash
./venv/Scripts/python.exe main.py --view latest
./venv/Scripts/python.exe main.py --view latest --jq '.ram.details'
./venv/Scripts/python.exe main.py --view latest --jq '.stability.kernel_errors'
./venv/Scripts/python.exe main.py --view latest --jq '.wsl.distros'
./venv/Scripts/python.exe main.py --view latest --jq '.processes.by_cpu[0]'
./venv/Scripts/python.exe main.py --view logs/reports/YYYY-MM-DD_HHMMSS_report.json
```

Or with `jq` directly:
```bash
cat logs/reports/*_report.json | jq '.stability'
```

## 5. Individual Section Queries (No Reports)

```bash
./venv/Scripts/python.exe main.py --section system       # Machine info + uptime
./venv/Scripts/python.exe main.py --section ram           # RAM + kernel pools + page faults
./venv/Scripts/python.exe main.py --section cpu           # CPU + interrupts + context switches
./venv/Scripts/python.exe main.py --section temps         # Temperature sensors
./venv/Scripts/python.exe main.py --section disk          # Disk usage + I/O
./venv/Scripts/python.exe main.py --section processes     # Process list (top 20)
./venv/Scripts/python.exe main.py --section display       # Per-monitor refresh rates + GPU mapping
./venv/Scripts/python.exe main.py --section gpu            # GPU temp, utilization, VRAM, power
./venv/Scripts/python.exe main.py --section network        # Adapters, ping, DNS latency
./venv/Scripts/python.exe main.py --section storage        # Disk health, problem devices
./venv/Scripts/python.exe main.py --section stability     # Crash dumps, kernel errors, handles
./venv/Scripts/python.exe main.py --section wsl           # WSL distro details (Windows only)
```

No files are written. Output is JSON to stdout.

## Project Structure

```
├── main.py                 # Entry point — CLI, orchestration, output formatting
├── CLAUDE.md               # This file
├── README.md               # Full documentation with examples
├── requirements.txt        # Python dependencies (psutil)
├── .gitignore
├── modules/
│   ├── base.py             # Shared dataclasses + anomaly detection logic
│   ├── windows/
│   │   └── collectors.py   # Windows: psutil + PowerShell/WMI + WSL bridge
│   ├── linux/
│   │   └── collectors.py   # Linux/WSL: psutil + /proc + dmesg + journalctl
│   └── mac/
│       └── collectors.py   # macOS: psutil + sysctl + system_profiler
└── logs/
    ├── reports/            # Raw JSON snapshots (.gitignored)
    └── analysis/           # Markdown analysis reports (.gitignored)
```

## Notes

- Some collectors need **admin/sudo** for full data (Windows temps, Linux dmesg). The tool degrades gracefully.
- Windows collection takes ~15-20 seconds due to `Get-Counter` sampling.
- System processes (System Idle, svchost, csrss, etc.) are auto-excluded from CPU anomaly detection.
- WSL metrics are collected from the Windows host by running `wsl -d <distro>` commands.
- `logs/reports/` and `logs/analysis/` contents are gitignored; `.gitkeep` files are tracked.
