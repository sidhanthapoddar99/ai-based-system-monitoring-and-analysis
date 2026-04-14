import platform
import subprocess
import socket
import json
import re

import psutil

from modules.base import (
    SystemInfo, RamReport, CpuReport, TemperatureReport,
    DiskReport, ProcessReport, DisplayReport,
)


def _run_cmd(cmd: list[str], timeout: int = 10) -> str:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def collect_system_info() -> SystemInfo:
    uname = platform.uname()
    cpu_model = _run_cmd(["sysctl", "-n", "machdep.cpu.brand_string"]) or uname.processor

    # macOS version
    os_version = _run_cmd(["sw_vers", "-productVersion"]) or uname.release
    os_name = f"macOS {os_version}"

    return SystemInfo(
        hostname=socket.gethostname(),
        os_name=os_name,
        os_version=os_version,
        cpu_model=cpu_model,
        cpu_cores=psutil.cpu_count(logical=False) or 0,
        cpu_threads=psutil.cpu_count(logical=True) or 0,
        total_ram_gb=round(psutil.virtual_memory().total / (1024**3), 1),
    )


def collect_ram() -> RamReport:
    vm = psutil.virtual_memory()
    swap = psutil.swap_memory()

    details = {
        "active_mb": round(getattr(vm, "active", 0) / (1024**2)),
        "inactive_mb": round(getattr(vm, "inactive", 0) / (1024**2)),
        "wired_mb": round(getattr(vm, "wired", 0) / (1024**2)),
        "swap_total_gb": round(swap.total / (1024**3), 2),
        "swap_used_gb": round(swap.used / (1024**3), 2),
    }

    # Get memory pressure from vm_stat
    vm_stat = _run_cmd(["vm_stat"])
    if vm_stat:
        for line in vm_stat.splitlines():
            if "Pages compressed" in line:
                match = re.search(r"(\d+)", line.split(":")[1])
                if match:
                    pages = int(match.group(1))
                    details["compressed_mb"] = round(pages * 16384 / (1024**2))

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

    clock = None
    freq = psutil.cpu_freq()
    if freq:
        clock = int(freq.current)

    return CpuReport(
        load_percent=round(load, 1),
        per_core_percent=[round(c, 1) for c in per_core],
        clock_speed_mhz=clock,
        interrupts_per_sec=None,
        interrupt_time_percent=None,
        dpc_time_percent=None,
    )


def collect_temperatures() -> TemperatureReport:
    readings = []

    # psutil doesn't support temps on macOS, try powermetrics (needs sudo)
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

    # Try ioreg for SMC temps (doesn't need sudo)
    ioreg = _run_cmd(["ioreg", "-rn", "AppleSMC"])
    if "temperature" in ioreg.lower():
        return TemperatureReport(
            readings=[{"label": "SMC", "current_c": None, "high_c": None, "critical_c": None}],
            source="ioreg (raw — install iStats for parsed temps)",
        )

    return TemperatureReport(
        readings=[],
        source="unavailable (run 'sudo powermetrics --samplers smc' for temps)",
    )


def collect_disk() -> DiskReport:
    partitions = []
    for p in psutil.disk_partitions(all=False):
        if "/private/var/vm" in p.mountpoint:
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
    # Use system_profiler for display info
    output = _run_cmd(["system_profiler", "SPDisplaysDataType", "-json"], timeout=15)
    if output:
        try:
            data = json.loads(output)
            for gpu in data.get("SPDisplaysDataType", []):
                gpu_name = gpu.get("_name", "unknown")
                for display in gpu.get("spdisplays_ndrvs", []):
                    res = display.get("_spdisplays_resolution", "?")
                    # macOS doesn't always expose refresh rate in system_profiler
                    displays.append({
                        "gpu": gpu_name,
                        "resolution": res,
                        "refresh_rate": None,
                        "vram": gpu.get("sppci_vram", "unknown"),
                    })
        except (json.JSONDecodeError, KeyError):
            pass
    return DisplayReport(displays=displays)
