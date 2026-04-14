# System Diagnostics Tool

## 1. Purpose

Cross-platform system health diagnostic tool that collects hardware and software metrics, detects anomalies, and generates reports. Works on **Windows**, **Linux**, **macOS**, and **WSL**.

Collects:
- RAM usage, kernel memory pools (nonpaged/paged), swap
- CPU load, per-core usage, interrupts/sec, interrupt time, DPC time
- Temperatures (ACPI zones, sensors, SMC)
- Disk usage and I/O throughput
- Process breakdown by RAM and CPU (individual + grouped by app)
- Display/GPU info (refresh rates, resolution, multi-GPU detection)

Analyzes data against thresholds to flag anomalies like memory leaks, CPU spin, thermal throttling, disk pressure, and runaway processes.

## 2. How to Run the Code

### Setup

```bash
# From the project root
python -m venv venv

# Activate
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

pip install -r requirements.txt
```

### Requirements

- Python 3.14+ (3.10+ should also work)
- `psutil` (installed via requirements.txt)
- Windows: PowerShell (for interrupts, kernel pools, display info)
- Linux: `/proc` filesystem, optionally `xrandr`
- macOS: `sysctl`, `system_profiler`, optionally `sudo powermetrics` for temps

## 3. How to Do Analysis

### Full Run (Report + Analysis)

```bash
python main.py
```

This will:
1. Collect all system metrics
2. Save raw data as JSON to `logs/reports/YYYY-MM-DD_HHMMSS_report.json`
3. Run anomaly detection against thresholds
4. Save human-readable analysis to `logs/analysis/YYYY-MM-DD_HHMMSS_analysis.md`
5. Print a summary + anomalies to the terminal

### Terminal-Only Analysis (No Files)

```bash
python main.py --analyze-only
```

Prints the full analysis markdown to the terminal without writing any files. Good for quick checks.

### Anomaly Thresholds

The analyzer checks for:

| Metric | Warning | Critical |
|---|---|---|
| RAM usage | > 85% | > 95% |
| Nonpaged pool (Windows) | > 1024 MB | - |
| CPU load | > 80% | > 95% |
| Interrupts/sec | > 100,000 | - |
| % Interrupt time | > 5% | - |
| % DPC time | > 5% | - |
| Temperature | > 80°C | > 95°C |
| Disk usage | > 90% | > 95% |
| Disk queue length | > 2.0 | - |
| Single process RAM | > 4 GB (info) | - |
| Process CPU time | > 10,000 sec (warning) | - |
| Kernel memory gap | > 4 GB unaccounted | - |

## 4. How to Generate Reports

### Raw JSON Report Only

```bash
python main.py --report-only
```

Saves machine-readable JSON to `logs/reports/` without running analysis. Useful for feeding into other tools or comparing snapshots over time.

### Full Report + Analysis

```bash
python main.py
```

Generates both:
- `logs/reports/YYYY-MM-DD_HHMMSS_report.json` — raw data
- `logs/analysis/YYYY-MM-DD_HHMMSS_analysis.md` — formatted findings

### Report Structure

The JSON report contains these sections:
```json
{
  "timestamp": "...",
  "system": { "hostname", "os_name", "cpu_model", ... },
  "ram": { "total_gb", "used_gb", "details": { "nonpaged_pool_mb", ... } },
  "cpu": { "load_percent", "interrupts_per_sec", "per_core_percent", ... },
  "temperatures": { "readings": [...], "source": "..." },
  "disk": { "partitions": [...], "io": { ... } },
  "processes": { "by_ram": [...], "by_cpu": [...], "grouped_by_name": [...] },
  "display": { "displays": [...] }
}
```

## 5. Individual Section Queries (No Reports)

Run a single collector and print its output directly to the terminal as JSON:

```bash
python main.py --section system      # Machine info
python main.py --section ram         # RAM + kernel pools
python main.py --section cpu         # CPU load + interrupts
python main.py --section temps       # Temperature sensors
python main.py --section disk        # Disk usage + I/O
python main.py --section processes   # Process list (top 20)
python main.py --section display     # GPU/monitor refresh rates
```

No files are written. Output is JSON to stdout — pipe it to `jq` or other tools:

```bash
python main.py --section ram | jq '.details'
python main.py --section processes | jq '.by_cpu[:5]'
```

## Project Structure

```
├── main.py                 # Entry point — CLI, orchestration, output formatting
├── CLAUDE.md               # This file
├── requirements.txt        # Python dependencies (psutil)
├── process.md              # Manual audit methodology documentation
├── monitor.ps1             # Original PowerShell live monitor script
├── modules/
│   ├── base.py             # Shared dataclasses + anomaly detection logic
│   ├── windows/
│   │   └── collectors.py   # Windows: psutil + PowerShell/WMI
│   ├── linux/
│   │   └── collectors.py   # Linux/WSL: psutil + /proc + xrandr
│   └── mac/
│       └── collectors.py   # macOS: psutil + sysctl + system_profiler
└── logs/
    ├── reports/            # Raw JSON snapshots
    └── analysis/           # Markdown analysis reports
```

## Notes

- Some collectors need **admin/sudo** for full data (Windows temps via WMI, Linux sensors). The tool degrades gracefully — it reports what it can access.
- Windows interrupt/DPC counters use `Get-Counter` which blocks for ~2 seconds per sample. Total collection time is ~10-15 seconds on Windows.
- The `logs/reports/` and `logs/analysis/` directories are gitignored (contents only — `.gitkeep` files are tracked).
