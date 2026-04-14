import platform
import socket
import subprocess
import time
from datetime import datetime
from pathlib import Path

import psutil

from modules.base import (
    SystemInfo, RamReport, CpuReport, TemperatureReport,
    DiskReport, ProcessReport, DisplayReport, StabilityReport,
)


def _read_file(path: str) -> str:
    try:
        return Path(path).read_text().strip()
    except (FileNotFoundError, PermissionError, OSError):
        return ""


def _run_cmd(cmd: list[str], timeout: int = 10) -> str:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def _is_wsl() -> bool:
    release = _read_file("/proc/version")
    return "microsoft" in release.lower() or "wsl" in release.lower()


def collect_system_info() -> SystemInfo:
    uname = platform.uname()
    cpu_model = "unknown"

    cpuinfo = _read_file("/proc/cpuinfo")
    for line in cpuinfo.splitlines():
        if line.startswith("model name"):
            cpu_model = line.split(":", 1)[1].strip()
            break

    boot = psutil.boot_time()
    uptime = time.time() - boot if boot else None
    boot_str = datetime.fromtimestamp(boot).isoformat() if boot else None

    os_version = uname.release
    os_name = "WSL" if _is_wsl() else "Linux"
    os_release = _read_file("/etc/os-release")
    for line in os_release.splitlines():
        if line.startswith("PRETTY_NAME="):
            os_version = line.split("=", 1)[1].strip('"')
            break

    return SystemInfo(
        hostname=socket.gethostname(),
        os_name=os_name,
        os_version=os_version,
        cpu_model=cpu_model,
        cpu_cores=psutil.cpu_count(logical=False) or 0,
        cpu_threads=psutil.cpu_count(logical=True) or 0,
        total_ram_gb=round(psutil.virtual_memory().total / (1024**3), 1),
        uptime_seconds=round(uptime, 0) if uptime else None,
        boot_time=boot_str,
    )


def collect_ram() -> RamReport:
    vm = psutil.virtual_memory()
    swap = psutil.swap_memory()

    details = {
        "buffers_mb": round(getattr(vm, "buffers", 0) / (1024**2)),
        "cached_mb": round(getattr(vm, "cached", 0) / (1024**2)),
        "shared_mb": round(getattr(vm, "shared", 0) / (1024**2)),
        "swap_total_gb": round(swap.total / (1024**3), 2),
        "swap_used_gb": round(swap.used / (1024**3), 2),
        "swap_percent": swap.percent,
    }

    # Detailed /proc/meminfo
    meminfo = _read_file("/proc/meminfo")
    for line in meminfo.splitlines():
        if line.startswith("Slab:"):
            val = int(line.split()[1])  # in kB
            details["slab_mb"] = round(val / 1024)
        elif line.startswith("SReclaimable:"):
            val = int(line.split()[1])
            details["slab_reclaimable_mb"] = round(val / 1024)

    return RamReport(
        total_gb=round(vm.total / (1024**3), 1),
        used_gb=round(vm.used / (1024**3), 1),
        free_gb=round(vm.available / (1024**3), 1),
        percent_used=vm.percent,
        details=details,
    )


def collect_cpu(sample_seconds: int = 2) -> CpuReport:
    per_core = psutil.cpu_percent(interval=sample_seconds, percpu=True)
    load = sum(per_core) / len(per_core) if per_core else 0.0

    # Parse /proc/stat for interrupts and context switches
    interrupts = None
    ctx_switches = None
    stat_data = _read_file("/proc/stat")
    for line in stat_data.splitlines():
        if line.startswith("intr "):
            parts = line.split()
            if len(parts) > 1:
                interrupts = float(parts[1])
        elif line.startswith("ctxt "):
            parts = line.split()
            if len(parts) > 1:
                ctx_switches = float(parts[1])

    clock = None
    cpuinfo = _read_file("/proc/cpuinfo")
    for line in cpuinfo.splitlines():
        if line.startswith("cpu MHz"):
            try:
                clock = int(float(line.split(":", 1)[1].strip()))
            except ValueError:
                pass
            break

    details = {}
    loadavg = _read_file("/proc/loadavg")
    if loadavg:
        parts = loadavg.split()
        if len(parts) >= 3:
            details["load_avg_1m"] = float(parts[0])
            details["load_avg_5m"] = float(parts[1])
            details["load_avg_15m"] = float(parts[2])

    return CpuReport(
        load_percent=round(load, 1),
        per_core_percent=[round(c, 1) for c in per_core],
        clock_speed_mhz=clock,
        interrupts_per_sec=interrupts,
        interrupt_time_percent=None,
        dpc_time_percent=None,
        context_switches_per_sec=ctx_switches,
        details=details,
    )


def collect_temperatures() -> TemperatureReport:
    readings = []

    try:
        temps = psutil.sensors_temperatures()
        if temps:
            for chip, entries in temps.items():
                for e in entries:
                    readings.append({
                        "label": e.label or chip,
                        "current_c": e.current,
                        "high_c": e.high,
                        "critical_c": e.critical,
                    })
            return TemperatureReport(readings=readings, source="psutil")
    except AttributeError:
        pass

    # Fallback: /sys/class/thermal
    thermal_base = Path("/sys/class/thermal")
    if thermal_base.exists():
        for zone in sorted(thermal_base.glob("thermal_zone*")):
            try:
                temp_raw = (zone / "temp").read_text().strip()
                type_raw = (zone / "type").read_text().strip()
                temp_c = int(temp_raw) / 1000
                readings.append({
                    "label": type_raw,
                    "current_c": temp_c,
                    "high_c": None,
                    "critical_c": None,
                })
            except (FileNotFoundError, PermissionError, ValueError):
                continue
        if readings:
            return TemperatureReport(readings=readings, source="/sys/class/thermal")

    return TemperatureReport(readings=[], source="unavailable")


def collect_disk() -> DiskReport:
    partitions = []
    for p in psutil.disk_partitions(all=False):
        if "snap" in p.mountpoint or "loop" in p.device:
            continue
        try:
            usage = psutil.disk_usage(p.mountpoint)
            partitions.append({
                "device": p.device,
                "mountpoint": p.mountpoint,
                "fstype": p.fstype,
                "total_gb": round(usage.total / (1024**3), 1),
                "used_gb": round(usage.used / (1024**3), 1),
                "free_gb": round(usage.free / (1024**3), 1),
                "percent": usage.percent,
            })
        except (PermissionError, OSError):
            continue

    io = None
    counters = psutil.disk_io_counters()
    if counters:
        io = {
            "read_bytes_total": counters.read_bytes,
            "write_bytes_total": counters.write_bytes,
            "read_count": counters.read_count,
            "write_count": counters.write_count,
        }

    return DiskReport(partitions=partitions, io=io)


def collect_processes(top_n: int = 20) -> ProcessReport:
    procs = []
    for p in psutil.process_iter(["pid", "name", "memory_info", "cpu_times", "num_threads", "status"]):
        try:
            info = p.info
            mem = info.get("memory_info")
            cpu_t = info.get("cpu_times")
            ram_mb = mem.rss / (1024**2) if mem else 0
            cpu_sec = (cpu_t.user + cpu_t.system) if cpu_t else 0
            procs.append({
                "pid": info["pid"],
                "name": info["name"] or "unknown",
                "ram_mb": round(ram_mb, 1),
                "cpu_seconds": round(cpu_sec, 1),
                "threads": info.get("num_threads") or 0,
                "status": info.get("status") or "",
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    by_ram = sorted(procs, key=lambda x: x["ram_mb"], reverse=True)[:top_n]
    by_cpu = sorted(procs, key=lambda x: x["cpu_seconds"], reverse=True)[:top_n]

    groups = {}
    for p in procs:
        name = p["name"]
        if name not in groups:
            groups[name] = {"name": name, "count": 0, "total_ram_mb": 0, "total_cpu_seconds": 0}
        groups[name]["count"] += 1
        groups[name]["total_ram_mb"] += p["ram_mb"]
        groups[name]["total_cpu_seconds"] += p["cpu_seconds"]

    for g in groups.values():
        g["total_ram_mb"] = round(g["total_ram_mb"], 1)
        g["total_cpu_seconds"] = round(g["total_cpu_seconds"], 1)

    grouped = sorted(groups.values(), key=lambda x: x["total_ram_mb"], reverse=True)[:top_n]

    total_ram = sum(p["ram_mb"] for p in procs) / 1024
    return ProcessReport(
        by_ram=by_ram,
        by_cpu=by_cpu,
        grouped_by_name=grouped,
        total_process_ram_gb=round(total_ram, 2),
    )


def collect_display() -> DisplayReport:
    displays = []
    # Try xrandr
    output = _run_cmd(["xrandr", "--current"])
    if output:
        for line in output.splitlines():
            if " connected " in line:
                parts = line.split()
                name = parts[0]
                # Find the active mode line (has * after refresh rate)
                displays.append({"gpu": name, "refresh_rate": None, "resolution": None})
            elif displays and "*" in line:
                parts = line.split()
                if parts:
                    res = parts[0]
                    # Find refresh rate (number followed by *)
                    for part in parts[1:]:
                        if "*" in part:
                            try:
                                rate = float(part.replace("*", "").replace("+", ""))
                                displays[-1]["resolution"] = res
                                displays[-1]["refresh_rate"] = round(rate)
                            except ValueError:
                                pass
                            break
    return DisplayReport(displays=displays)


def collect_stability() -> StabilityReport:
    uptime = None
    boot = psutil.boot_time()
    if boot:
        uptime = round((time.time() - boot) / 3600, 2)

    # OOM kills and kernel panics from dmesg
    kernel_errors = []
    oom_kills = 0
    dmesg = _run_cmd(["dmesg", "--level=emerg,alert,crit,err"], timeout=5)
    if dmesg:
        for line in dmesg.splitlines()[-20:]:
            if "out of memory" in line.lower():
                oom_kills += 1
            kernel_errors.append({"message": line.strip()[:200]})

    # Check /var/log/kern.log or journalctl
    if not kernel_errors:
        journal = _run_cmd(["journalctl", "-p", "err", "-n", "20", "--no-pager"], timeout=5)
        if journal:
            for line in journal.splitlines():
                if line.startswith("--"):
                    continue
                kernel_errors.append({"message": line.strip()[:200]})

    # Page faults from /proc/vmstat
    page_faults = None
    vmstat = _read_file("/proc/vmstat")
    for line in vmstat.splitlines():
        if line.startswith("pgfault "):
            page_faults = int(line.split()[1])
            break

    # Handle/thread/process count
    handle_count = None
    thread_count = None
    process_count = None
    try:
        process_count = len(list(psutil.process_iter()))
    except Exception:
        pass

    file_nr = _read_file("/proc/sys/fs/file-nr")
    if file_nr:
        parts = file_nr.split()
        if parts:
            handle_count = int(parts[0])

    details = {}
    # Load average
    loadavg = _read_file("/proc/loadavg")
    if loadavg:
        parts = loadavg.split()
        if len(parts) >= 4:
            details["load_avg"] = f"{parts[0]} {parts[1]} {parts[2]}"
            details["running_threads"] = parts[3]

    if oom_kills > 0:
        details["oom_kills"] = oom_kills

    return StabilityReport(
        uptime_hours=uptime,
        bsod_dumps=[],
        kernel_errors=kernel_errors,
        page_faults_per_sec=page_faults,
        pool_failures_nonpaged=None,
        pool_failures_paged=None,
        handle_count=handle_count,
        thread_count=thread_count,
        process_count=process_count,
        details=details,
    )
