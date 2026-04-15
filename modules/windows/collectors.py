import platform
import subprocess
import socket
import json
import os
from datetime import datetime
from pathlib import Path

import psutil

from modules.base import (
    SystemInfo, RamReport, CpuReport, TemperatureReport,
    DiskReport, ProcessReport, DisplayReport, StabilityReport, WslReport,
    GpuReport, NetworkReport, StorageHealthReport,
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


def _run_cmd(cmd: list[str], timeout: int = 10) -> str:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def collect_system_info() -> SystemInfo:
    uname = platform.uname()
    cpu_info = _run_ps(
        "(Get-CimInstance Win32_Processor).Name"
    ) or uname.processor

    boot = psutil.boot_time()
    uptime = psutil.time.time() - boot if boot else None
    boot_str = datetime.fromtimestamp(boot).isoformat() if boot else None

    return SystemInfo(
        hostname=socket.gethostname(),
        os_name=f"Windows {uname.release}",
        os_version=uname.version,
        cpu_model=cpu_info,
        cpu_cores=psutil.cpu_count(logical=False) or 0,
        cpu_threads=psutil.cpu_count(logical=True) or 0,
        total_ram_gb=round(psutil.virtual_memory().total / (1024**3), 1),
        uptime_seconds=round(uptime, 0) if uptime else None,
        boot_time=boot_str,
    )


def collect_ram() -> RamReport:
    vm = psutil.virtual_memory()

    details = {}
    perf = _run_ps_json(
        "Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory | "
        "Select-Object PoolNonpagedBytes, PoolPagedBytes, CommittedBytes, "
        "CacheBytes, ModifiedPageListBytes, StandbyCacheNormalPriorityBytes, "
        "StandbyCacheReserveBytes, FreeAndZeroPageListBytes, "
        "PageFaultsPersec, PoolNonpagedAllocs, PoolPagedAllocs, "
        "PoolNonpagedAllocsFailures, PoolPagedAllocsFailures"
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
        details["page_faults_per_sec"] = int(perf.get("PageFaultsPersec", 0))

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
    ctx_switches = None
    sys_calls = None

    counter_script = (
        f"(Get-Counter '\\Processor(_Total)\\Interrupts/sec',"
        f"'\\Processor(_Total)\\% Interrupt Time',"
        f"'\\Processor(_Total)\\% DPC Time',"
        f"'\\System\\Context Switches/sec',"
        f"'\\System\\System Calls/sec',"
        f"'\\System\\Processor Queue Length' "
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
            details = {}
            for s in samples:
                path = s.get("Path", "").lower()
                val = s.get("Value", 0)
                if "interrupts/sec" in path:
                    interrupts = round(val, 1)
                elif "% interrupt time" in path:
                    interrupt_pct = round(val, 3)
                elif "% dpc time" in path:
                    dpc_pct = round(val, 3)
                elif "context switches" in path:
                    ctx_switches = round(val, 0)
                elif "system calls" in path:
                    sys_calls = round(val, 0)
                elif "processor queue" in path:
                    details["processor_queue_length"] = round(val, 1)
        except json.JSONDecodeError:
            details = {}

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
        context_switches_per_sec=ctx_switches,
        system_calls_per_sec=sys_calls,
        details=details if 'details' in dir() else {},
    )


def collect_temperatures() -> TemperatureReport:
    readings = []

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

    # --- GPU adapter info (VRAM, driver) keyed by PNPDeviceID substring ---
    gpu_info = {}
    gpu_data = _run_ps_json(
        "Get-CimInstance Win32_VideoController | "
        "Select-Object Name, PNPDeviceID, AdapterRAM, DriverVersion"
    )
    if gpu_data:
        if isinstance(gpu_data, dict):
            gpu_data = [gpu_data]
        for g in gpu_data:
            pnp = g.get("PNPDeviceID", "")
            gpu_info[pnp.upper()] = {
                "name": g.get("Name", "unknown"),
                "vram_gb": round(int(g.get("AdapterRAM") or 0) / (1024**3), 1),
                "driver_version": g.get("DriverVersion", "unknown"),
            }

    # --- Per-monitor enumeration via QueryDisplayConfig API ---
    _QDC_PS = r"""
Add-Type @'
using System;
using System.Runtime.InteropServices;
using System.Collections.Generic;

public class QDCHelper {
    [StructLayout(LayoutKind.Sequential)]
    public struct LUID { public uint LowPart; public int HighPart; }

    [StructLayout(LayoutKind.Sequential)]
    public struct RATIONAL { public uint Num; public uint Den; }

    [StructLayout(LayoutKind.Sequential)]
    public struct PATH_SOURCE { public LUID adapterId; public uint id; public uint modeIdx; public uint flags; }

    [StructLayout(LayoutKind.Sequential)]
    public struct PATH_TARGET {
        public LUID adapterId; public uint id; public uint modeIdx;
        public uint outTech; public uint rotation; public uint scaling;
        public RATIONAL refreshRate;
        public uint scanLine; public bool available; public uint flags;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct PATH_INFO { public PATH_SOURCE src; public PATH_TARGET tgt; public uint flags; }

    [StructLayout(LayoutKind.Sequential)]
    public struct REGION2D { public uint cx; public uint cy; }

    [StructLayout(LayoutKind.Sequential)]
    public struct VIDEO_SIGNAL {
        public ulong pixelRate; public RATIONAL hSync; public RATIONAL vSync;
        public REGION2D activeSize; public REGION2D totalSize;
        public uint videoStd; public uint scanLine;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct TARGET_MODE { public VIDEO_SIGNAL sig; }

    [StructLayout(LayoutKind.Explicit)]
    public struct MODE_INFO {
        [FieldOffset(0)] public uint infoType;
        [FieldOffset(4)] public uint id;
        [FieldOffset(8)] public LUID adapterId;
        [FieldOffset(16)] public TARGET_MODE targetMode;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct DEV_HEADER { public uint type; public uint size; public LUID adapterId; public uint id; }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct TARGET_NAME {
        public DEV_HEADER header; public uint flags; public uint outTech;
        public ushort mfgId; public ushort prodId; public uint connInst;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 64)] public string monName;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)] public string monPath;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct ADAPTER_NAME {
        public DEV_HEADER header;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)] public string path;
    }

    [DllImport("user32.dll")] static extern int GetDisplayConfigBufferSizes(uint f, out uint np, out uint nm);
    [DllImport("user32.dll")] static extern int QueryDisplayConfig(uint f, ref uint np, [Out] PATH_INFO[] p, ref uint nm, [Out] MODE_INFO[] m, IntPtr t);
    [DllImport("user32.dll")] static extern int DisplayConfigGetDeviceInfo(ref TARGET_NAME i);
    [DllImport("user32.dll")] static extern int DisplayConfigGetDeviceInfo(ref ADAPTER_NAME i);

    public static List<string> Get() {
        var r = new List<string>();
        uint np, nm;
        GetDisplayConfigBufferSizes(2, out np, out nm);
        var paths = new PATH_INFO[np]; var modes = new MODE_INFO[nm];
        if (QueryDisplayConfig(2, ref np, paths, ref nm, modes, IntPtr.Zero) != 0) return r;
        for (int i = 0; i < np; i++) {
            var p = paths[i]; if (p.flags == 0) continue;
            double hz = p.tgt.refreshRate.Den > 0 ? (double)p.tgt.refreshRate.Num / p.tgt.refreshRate.Den : 0;
            uint w = 0, h = 0;
            if (p.tgt.modeIdx < nm) { w = modes[p.tgt.modeIdx].targetMode.sig.activeSize.cx; h = modes[p.tgt.modeIdx].targetMode.sig.activeSize.cy; }
            var tn = new TARGET_NAME(); tn.header.type = 2; tn.header.size = (uint)Marshal.SizeOf(typeof(TARGET_NAME));
            tn.header.adapterId = p.tgt.adapterId; tn.header.id = p.tgt.id;
            DisplayConfigGetDeviceInfo(ref tn);
            var an = new ADAPTER_NAME(); an.header.type = 4; an.header.size = (uint)Marshal.SizeOf(typeof(ADAPTER_NAME));
            an.header.adapterId = p.tgt.adapterId; an.header.id = p.tgt.id;
            DisplayConfigGetDeviceInfo(ref an);
            r.Add(tn.monName + "|" + (an.path ?? "") + "|" + w + "|" + h + "|" + hz.ToString("F2"));
        }
        return r;
    }
}
'@
[QDCHelper]::Get() | ForEach-Object { Write-Output $_ }
"""
    raw = _run_ps(_QDC_PS, timeout=20)
    if raw:
        for line in raw.strip().splitlines():
            parts = line.split("|", 4)
            if len(parts) < 5:
                continue
            mon_name, adapter_path, w, h, hz = parts
            adapter_upper = adapter_path.upper()

            # Match adapter path to GPU info by PNPDeviceID substring
            gpu_name = "unknown"
            vram = 0.0
            driver = "unknown"
            for pnp, info in gpu_info.items():
                # adapter_path contains the PCI VEN&DEV substring
                pnp_key = pnp.replace("\\", "#")
                if pnp_key in adapter_upper or pnp.split("\\")[-1] in adapter_upper:
                    gpu_name = info["name"]
                    vram = info["vram_gb"]
                    driver = info["driver_version"]
                    break
            else:
                # Fallback: match by VEN_ substring
                for pnp, info in gpu_info.items():
                    ven_dev = [p for p in pnp.split("\\") if p.startswith("VEN_")]
                    if ven_dev and ven_dev[0] in adapter_upper:
                        gpu_name = info["name"]
                        vram = info["vram_gb"]
                        driver = info["driver_version"]
                        break

            try:
                refresh = round(float(hz), 2)
            except ValueError:
                refresh = 0
            displays.append({
                "monitor": mon_name.strip() or "unknown",
                "gpu": gpu_name,
                "refresh_rate": refresh,
                "resolution": f"{w}x{h}",
                "vram_gb": vram,
                "driver_version": driver,
            })

    # Fallback to Win32_VideoController if QueryDisplayConfig returned nothing
    if not displays:
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


def collect_stability() -> StabilityReport:
    uptime = None
    boot = psutil.boot_time()
    if boot:
        import time
        uptime = round((time.time() - boot) / 3600, 2)

    # Check for BSOD minidumps
    bsod_dumps = []
    minidump_dir = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Minidump"
    if minidump_dir.exists():
        for dump in sorted(minidump_dir.glob("*.dmp"), key=lambda f: f.stat().st_mtime, reverse=True)[:10]:
            bsod_dumps.append({
                "file": dump.name,
                "date": datetime.fromtimestamp(dump.stat().st_mtime).isoformat(),
                "size_kb": round(dump.stat().st_size / 1024),
            })

    # Also check for full memory dumps
    full_dump = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "MEMORY.DMP"
    if full_dump.exists():
        bsod_dumps.insert(0, {
            "file": "MEMORY.DMP",
            "date": datetime.fromtimestamp(full_dump.stat().st_mtime).isoformat(),
            "size_kb": round(full_dump.stat().st_size / 1024),
        })

    # Critical kernel errors from event log (last 48 hours)
    kernel_errors = []
    event_script = (
        "Get-WinEvent -FilterHashtable @{LogName='System';Level=1,2;"
        "StartTime=(Get-Date).AddHours(-48)} -MaxEvents 20 -ErrorAction SilentlyContinue | "
        "Select-Object TimeCreated, Id, ProviderName, Message | "
        "ForEach-Object { [PSCustomObject]@{"
        "Time=$_.TimeCreated.ToString('o');"
        "Id=$_.Id;"
        "Source=$_.ProviderName;"
        "Msg=$_.Message.Substring(0, [Math]::Min(200, $_.Message.Length))"
        "} }"
    )
    events = _run_ps_json(event_script, timeout=20)
    if events:
        if isinstance(events, dict):
            events = [events]
        for e in events:
            kernel_errors.append({
                "time": e.get("Time", ""),
                "event_id": e.get("Id", 0),
                "source": e.get("Source", ""),
                "message": e.get("Msg", ""),
            })

    # Page faults, pool failures, handle/thread counts
    page_faults = None
    pool_fail_np = None
    pool_fail_p = None

    perf = _run_ps_json(
        "Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory | "
        "Select-Object PageFaultsPersec"
    )
    if perf:
        page_faults = int(perf.get("PageFaultsPersec", 0))

    # System-wide handle and thread count
    handle_count = None
    thread_count = None
    process_count = None
    sys_perf = _run_ps_json(
        "Get-CimInstance Win32_PerfFormattedData_PerfOS_System | "
        "Select-Object Threads, Processes, SystemCallsPersec"
    )
    if sys_perf:
        thread_count = int(sys_perf.get("Threads", 0))
        process_count = int(sys_perf.get("Processes", 0))

    # Handle count from all processes
    try:
        handle_count = sum(p.num_handles() for p in psutil.process_iter() if hasattr(p, 'num_handles'))
    except Exception:
        pass

    details = {}
    # Check if secure boot / bitlocker / crash config
    crash_cfg = _run_ps_json(
        "Get-ItemProperty 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\CrashControl' -ErrorAction SilentlyContinue | "
        "Select-Object CrashDumpEnabled, AutoReboot"
    )
    if crash_cfg:
        dump_types = {0: "None", 1: "Complete", 2: "Kernel", 3: "Small (Minidump)", 7: "Automatic"}
        details["crash_dump_type"] = dump_types.get(crash_cfg.get("CrashDumpEnabled", -1), "Unknown")
        details["auto_reboot_on_crash"] = bool(crash_cfg.get("AutoReboot", 0))

    return StabilityReport(
        uptime_hours=uptime,
        bsod_dumps=bsod_dumps,
        kernel_errors=kernel_errors,
        page_faults_per_sec=page_faults,
        pool_failures_nonpaged=pool_fail_np,
        pool_failures_paged=pool_fail_p,
        handle_count=handle_count,
        thread_count=thread_count,
        process_count=process_count,
        details=details,
    )


def collect_wsl() -> WslReport:
    """Collect WSL distro details from the Windows host side."""
    distros = []
    details = {}

    # Get running distros
    raw = _run_cmd(["wsl", "--list", "--verbose"], timeout=10)
    if not raw:
        return WslReport(distros=[], details={"available": False})

    # Parse the wsl --list output (has weird Unicode spacing)
    lines = raw.splitlines()
    for line in lines[1:]:  # skip header
        # Normalize whitespace (WSL outputs UTF-16 with extra null bytes)
        clean = line.replace("\x00", "").strip()
        if not clean:
            continue
        is_default = clean.startswith("*")
        clean = clean.lstrip("* ").strip()
        parts = clean.split()
        if len(parts) >= 3:
            name = parts[0]
            state = parts[1]
            version = parts[2] if len(parts) > 2 else "?"
            distros.append({
                "name": name,
                "state": state,
                "wsl_version": version,
                "is_default": is_default,
            })

    # For each running distro, collect details from inside
    for distro in distros:
        if distro["state"].lower() != "running":
            continue
        dname = distro["name"]

        # Memory from inside WSL
        meminfo = _run_cmd(["wsl", "-d", dname, "--", "cat", "/proc/meminfo"], timeout=5)
        if meminfo:
            for mline in meminfo.splitlines():
                if mline.startswith("MemTotal:"):
                    distro["total_ram_mb"] = round(int(mline.split()[1]) / 1024)
                elif mline.startswith("MemAvailable:"):
                    distro["available_ram_mb"] = round(int(mline.split()[1]) / 1024)
                elif mline.startswith("MemFree:"):
                    distro["free_ram_mb"] = round(int(mline.split()[1]) / 1024)

            if "total_ram_mb" in distro and "available_ram_mb" in distro:
                distro["ram_mb"] = distro["total_ram_mb"] - distro["available_ram_mb"]
                distro["ram_percent"] = round(distro["ram_mb"] / distro["total_ram_mb"] * 100, 1)

        # Load average
        loadavg = _run_cmd(["wsl", "-d", dname, "--", "cat", "/proc/loadavg"], timeout=5)
        if loadavg:
            parts = loadavg.split()
            if len(parts) >= 3:
                distro["load_avg_1m"] = float(parts[0])
                distro["load_avg_5m"] = float(parts[1])
                distro["load_avg_15m"] = float(parts[2])

        # OOM kills from dmesg (if accessible)
        dmesg = _run_cmd(["wsl", "-d", dname, "--", "dmesg"], timeout=5)
        if dmesg:
            oom_count = dmesg.lower().count("out of memory")
            distro["oom_kills"] = oom_count
            # Kernel panics
            panic_count = dmesg.lower().count("kernel panic")
            distro["kernel_panics"] = panic_count

        # Top processes inside WSL
        ps_output = _run_cmd([
            "wsl", "-d", dname, "--",
            "ps", "aux", "--sort=-rss"
        ], timeout=5)
        if ps_output:
            top_procs = []
            lines = ps_output.splitlines()
            for pline in lines[1:11]:  # top 10
                parts = pline.split(None, 10)
                if len(parts) >= 11:
                    top_procs.append({
                        "user": parts[0],
                        "pid": int(parts[1]),
                        "cpu_pct": float(parts[2]),
                        "mem_pct": float(parts[3]),
                        "rss_kb": int(parts[5]),
                        "command": parts[10][:80],
                    })
            distro["top_processes"] = top_procs

        # Disk usage inside WSL
        df_output = _run_cmd(["wsl", "-d", dname, "--", "df", "-h", "/"], timeout=5)
        if df_output:
            lines = df_output.splitlines()
            if len(lines) >= 2:
                distro["disk_info"] = lines[1].strip()

    # Check .wslconfig
    wslconfig = Path.home() / ".wslconfig"
    if wslconfig.exists():
        details["wslconfig"] = wslconfig.read_text().strip()
        details["wslconfig_path"] = str(wslconfig)
    else:
        details["wslconfig"] = None
        details["wslconfig_note"] = "No .wslconfig found — WSL uses defaults (up to 50% of host RAM)"

    # Get vmmem process info from Windows side
    vmmem_procs = []
    for p in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            if "vmmem" in (p.info["name"] or "").lower():
                mem = p.info["memory_info"]
                vmmem_procs.append({
                    "pid": p.info["pid"],
                    "name": p.info["name"],
                    "ram_mb": round(mem.rss / (1024**2), 1) if mem else 0,
                })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    details["vmmem_processes"] = vmmem_procs

    return WslReport(distros=distros, details=details)


def collect_gpu() -> GpuReport:
    gpus = []

    # Try nvidia-smi first
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,temperature.gpu,utilization.gpu,"
             "utilization.memory,memory.used,memory.total,fan.speed,"
             "power.draw,power.limit,clocks.current.graphics,clocks.current.memory",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 11:
                    gpus.append({
                        "name": parts[0],
                        "temperature_c": _safe_int(parts[1]),
                        "utilization_percent": _safe_int(parts[2]),
                        "memory_utilization_percent": _safe_int(parts[3]),
                        "vram_used_mb": _safe_int(parts[4]),
                        "vram_total_mb": _safe_int(parts[5]),
                        "fan_speed_percent": _safe_int(parts[6]),
                        "power_draw_w": _safe_float(parts[7]),
                        "power_limit_w": _safe_float(parts[8]),
                        "clock_graphics_mhz": _safe_int(parts[9]),
                        "clock_memory_mhz": _safe_int(parts[10]),
                    })
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    # Also detect Intel/AMD iGPU via WMI (no utilization data, but presence)
    igpu_data = _run_ps_json(
        "Get-CimInstance Win32_VideoController | "
        "Where-Object { $_.Name -notmatch 'NVIDIA' -and $_.Name -notmatch 'Virtual' "
        "-and $_.Name -notmatch 'Parsec' -and $_.Name -notmatch 'Meta' "
        "-and $_.CurrentRefreshRate -gt 0 } | "
        "Select-Object Name, AdapterRAM, DriverVersion, Status"
    )
    if igpu_data:
        if isinstance(igpu_data, dict):
            igpu_data = [igpu_data]
        for g in igpu_data:
            gpus.append({
                "name": g.get("Name", "unknown"),
                "vram_total_mb": round(int(g.get("AdapterRAM") or 0) / (1024**2)),
                "driver_version": g.get("DriverVersion", "unknown"),
                "status": g.get("Status", "unknown"),
            })

    return GpuReport(gpus=gpus)


def _safe_int(val):
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _safe_float(val):
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def collect_network() -> NetworkReport:
    adapters = []

    # Active network adapters
    adapter_data = _run_ps_json(
        "Get-NetAdapter | Where-Object Status -eq 'Up' | "
        "Select-Object Name, InterfaceDescription, LinkSpeed, MacAddress, "
        "MediaType, DriverVersion"
    )
    if adapter_data:
        if isinstance(adapter_data, dict):
            adapter_data = [adapter_data]
        for a in adapter_data:
            adapters.append({
                "name": a.get("Name", "unknown"),
                "description": a.get("InterfaceDescription", ""),
                "link_speed": a.get("LinkSpeed", "unknown"),
                "mac_address": a.get("MacAddress", ""),
                "driver_version": a.get("DriverVersion", ""),
            })

    # Latency test
    latency = {}
    ping_raw = _run_ps(
        "Test-Connection 8.8.8.8 -Count 3 -ErrorAction SilentlyContinue | "
        "Select-Object Status, Latency | ConvertTo-Json -Compress",
        timeout=20,
    )
    if ping_raw:
        try:
            ping_data = json.loads(ping_raw)
            if isinstance(ping_data, dict):
                ping_data = [ping_data]
            latencies = [p.get("Latency", 0) for p in ping_data
                         if p.get("Status") == "Success" or p.get("Latency", 0) > 0]
            if latencies:
                latency["ping_avg_ms"] = round(sum(latencies) / len(latencies), 1)
                latency["ping_min_ms"] = min(latencies)
                latency["ping_max_ms"] = max(latencies)
            total = len(ping_data)
            success = len(latencies)
            latency["packet_loss_percent"] = round((total - success) / total * 100, 1) if total else 0
        except (json.JSONDecodeError, TypeError):
            pass

    # DNS latency
    dns_raw = _run_ps(
        "$sw = [System.Diagnostics.Stopwatch]::StartNew(); "
        "Resolve-DnsName google.com -ErrorAction SilentlyContinue | Out-Null; "
        "$sw.Stop(); $sw.ElapsedMilliseconds",
        timeout=10,
    )
    if dns_raw:
        try:
            latency["dns_ms"] = int(dns_raw.strip())
        except ValueError:
            pass

    return NetworkReport(adapters=adapters, latency=latency or None)


def collect_storage_health() -> StorageHealthReport:
    disks = []
    problem_devices = []

    # Physical disk health
    disk_data = _run_ps_json(
        "Get-PhysicalDisk | Select-Object FriendlyName, MediaType, "
        "HealthStatus, OperationalStatus, "
        "@{N='SizeGB';E={[math]::Round($_.Size / 1GB, 1)}}, "
        "BusType, FirmwareVersion"
    )
    if disk_data:
        if isinstance(disk_data, dict):
            disk_data = [disk_data]
        for d in disk_data:
            disks.append({
                "name": d.get("FriendlyName", "unknown"),
                "media_type": d.get("MediaType", "unknown"),
                "health_status": d.get("HealthStatus", "unknown"),
                "operational_status": d.get("OperationalStatus", "unknown"),
                "size_gb": d.get("SizeGB", 0),
                "bus_type": d.get("BusType", "unknown"),
                "firmware_version": d.get("FirmwareVersion", ""),
            })

    # Problem devices (error code != 0)
    dev_data = _run_ps_json(
        "Get-CimInstance Win32_PnPEntity | "
        "Where-Object { $_.ConfigManagerErrorCode -ne 0 } | "
        "Select-Object Name, DeviceID, ConfigManagerErrorCode"
    )
    if dev_data:
        if isinstance(dev_data, dict):
            dev_data = [dev_data]
        for d in dev_data:
            problem_devices.append({
                "name": d.get("Name", "unknown"),
                "device_id": d.get("DeviceID", ""),
                "error_code": d.get("ConfigManagerErrorCode", 0),
            })

    return StorageHealthReport(disks=disks, problem_devices=problem_devices)
