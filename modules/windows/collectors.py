import platform
import subprocess
import socket
import json

import psutil

from modules.base import (
    SystemInfo, RamReport, CpuReport, TemperatureReport,
    DiskReport, ProcessReport, DisplayReport,
)


def _run_ps(script: str, timeout: int = 15) -> str:
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NoLogo", "-Command", script],
            capture_output=True, text=True, timeout=timeout,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def _run_ps_json(script: str, timeout: int = 15):
    raw = _run_ps(f"{script} | ConvertTo-Json -Compress", timeout)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def collect_system_info() -> SystemInfo:
    uname = platform.uname()
    cpu_info = _run_ps(
        "(Get-CimInstance Win32_Processor).Name"
    ) or uname.processor

    return SystemInfo(
        hostname=socket.gethostname(),
        os_name=f"Windows {uname.release}",
        os_version=uname.version,
        cpu_model=cpu_info,
        cpu_cores=psutil.cpu_count(logical=False) or 0,
        cpu_threads=psutil.cpu_count(logical=True) or 0,
        total_ram_gb=round(psutil.virtual_memory().total / (1024**3), 1),
    )


def collect_ram() -> RamReport:
    vm = psutil.virtual_memory()

    details = {}
    perf = _run_ps_json(
        "Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory | "
        "Select-Object PoolNonpagedBytes, PoolPagedBytes, CommittedBytes, "
        "CacheBytes, ModifiedPageListBytes, StandbyCacheNormalPriorityBytes, "
        "StandbyCacheReserveBytes, FreeAndZeroPageListBytes"
    )
    if perf:
        details["nonpaged_pool_mb"] = round(int(perf.get("PoolNonpagedBytes", 0)) / (1024**2))
        details["paged_pool_mb"] = round(int(perf.get("PoolPagedBytes", 0)) / (1024**2))
        details["committed_gb"] = round(int(perf.get("CommittedBytes", 0)) / (1024**3), 2)
        details["cache_gb"] = round(int(perf.get("CacheBytes", 0)) / (1024**3), 2)
        details["modified_mb"] = round(int(perf.get("ModifiedPageListBytes", 0)) / (1024**2))
        standby_normal = int(perf.get("StandbyCacheNormalPriorityBytes", 0))
        standby_reserve = int(perf.get("StandbyCacheReserveBytes", 0))
        details["standby_cache_gb"] = round((standby_normal + standby_reserve) / (1024**3), 2)
        details["free_zero_gb"] = round(int(perf.get("FreeAndZeroPageListBytes", 0)) / (1024**3), 2)

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

    interrupts = None
    interrupt_pct = None
    dpc_pct = None

    counter_script = (
        f"(Get-Counter '\\Processor(_Total)\\Interrupts/sec',"
        f"'\\Processor(_Total)\\% Interrupt Time',"
        f"'\\Processor(_Total)\\% DPC Time' "
        f"-SampleInterval {sample_seconds} -MaxSamples 1)"
        f".CounterSamples | ForEach-Object {{ "
        f"[PSCustomObject]@{{Path=$_.Path;Value=$_.CookedValue}} "
        f"}} | ConvertTo-Json -Compress"
    )
    raw = _run_ps(counter_script, timeout=sample_seconds + 10)
    if raw:
        try:
            samples = json.loads(raw)
            if isinstance(samples, dict):
                samples = [samples]
            for s in samples:
                path = s.get("Path", "").lower()
                val = s.get("Value", 0)
                if "interrupts/sec" in path:
                    interrupts = round(val, 1)
                elif "% interrupt time" in path:
                    interrupt_pct = round(val, 3)
                elif "% dpc time" in path:
                    dpc_pct = round(val, 3)
        except json.JSONDecodeError:
            pass

    clock = None
    clock_raw = _run_ps("(Get-CimInstance Win32_Processor).CurrentClockSpeed")
    if clock_raw and clock_raw.isdigit():
        clock = int(clock_raw)

    return CpuReport(
        load_percent=round(load, 1),
        per_core_percent=[round(c, 1) for c in per_core],
        clock_speed_mhz=clock,
        interrupts_per_sec=interrupts,
        interrupt_time_percent=interrupt_pct,
        dpc_time_percent=dpc_pct,
    )


def collect_temperatures() -> TemperatureReport:
    readings = []

    # Try WMI thermal zones (needs admin for MSAcpi)
    data = _run_ps_json(
        "Get-CimInstance Win32_PerfFormattedData_Counters_ThermalZoneInformation -ErrorAction SilentlyContinue | "
        "Select-Object Name, Temperature"
    )
    if data:
        if isinstance(data, dict):
            data = [data]
        for entry in data:
            temp_k = int(entry.get("Temperature", 0))
            if temp_k > 0:
                temp_c = temp_k - 273
                readings.append({
                    "label": entry.get("Name", "unknown"),
                    "current_c": temp_c,
                    "high_c": None,
                    "critical_c": None,
                })
        return TemperatureReport(readings=readings, source="Win32_ThermalZoneInformation")

    # Fallback: psutil (limited on Windows)
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

    return TemperatureReport(readings=[], source="unavailable (may need admin)")


def collect_disk() -> DiskReport:
    partitions = []
    for p in psutil.disk_partitions(all=False):
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
    io_script = (
        "(Get-Counter '\\PhysicalDisk(_Total)\\Disk Read Bytes/sec',"
        "'\\PhysicalDisk(_Total)\\Disk Write Bytes/sec',"
        "'\\PhysicalDisk(_Total)\\% Disk Time',"
        "'\\PhysicalDisk(_Total)\\Avg. Disk Queue Length' "
        "-SampleInterval 2 -MaxSamples 1).CounterSamples | ForEach-Object { "
        "[PSCustomObject]@{Path=$_.Path;Value=$_.CookedValue} "
        "} | ConvertTo-Json -Compress"
    )
    raw = _run_ps(io_script, timeout=15)
    if raw:
        try:
            samples = json.loads(raw)
            if isinstance(samples, dict):
                samples = [samples]
            io = {}
            for s in samples:
                path = s.get("Path", "").lower()
                val = s.get("Value", 0)
                if "read bytes" in path:
                    io["read_bytes_sec"] = round(val, 1)
                elif "write bytes" in path:
                    io["write_bytes_sec"] = round(val, 1)
                elif "% disk time" in path:
                    io["busy_percent"] = round(val, 2)
                elif "queue length" in path:
                    io["queue_length"] = round(val, 4)
        except json.JSONDecodeError:
            pass

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

    # Group by name
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
    data = _run_ps_json(
        "Get-CimInstance Win32_VideoController | "
        "Where-Object { $_.CurrentRefreshRate -gt 0 } | "
        "Select-Object Name, CurrentRefreshRate, "
        "CurrentHorizontalResolution, CurrentVerticalResolution, "
        "AdapterRAM, DriverVersion"
    )
    if data:
        if isinstance(data, dict):
            data = [data]
        for entry in data:
            displays.append({
                "gpu": entry.get("Name", "unknown"),
                "refresh_rate": entry.get("CurrentRefreshRate"),
                "resolution": f"{entry.get('CurrentHorizontalResolution', '?')}x{entry.get('CurrentVerticalResolution', '?')}",
                "vram_gb": round(int(entry.get("AdapterRAM", 0)) / (1024**3), 1),
                "driver_version": entry.get("DriverVersion", "unknown"),
            })
    return DisplayReport(displays=displays)
