from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SystemInfo:
    hostname: str
    os_name: str
    os_version: str
    cpu_model: str
    cpu_cores: int
    cpu_threads: int
    total_ram_gb: float
    uptime_seconds: Optional[float] = None
    boot_time: Optional[str] = None


@dataclass
class RamReport:
    total_gb: float
    used_gb: float
    free_gb: float
    percent_used: float
    details: dict = field(default_factory=dict)


@dataclass
class CpuReport:
    load_percent: float
    per_core_percent: list[float]
    clock_speed_mhz: Optional[int] = None
    interrupts_per_sec: Optional[float] = None
    interrupt_time_percent: Optional[float] = None
    dpc_time_percent: Optional[float] = None
    context_switches_per_sec: Optional[float] = None
    system_calls_per_sec: Optional[float] = None
    details: dict = field(default_factory=dict)


@dataclass
class TemperatureReport:
    readings: list[dict] = field(default_factory=list)
    source: str = "unavailable"


@dataclass
class DiskReport:
    partitions: list[dict] = field(default_factory=list)
    io: Optional[dict] = None


@dataclass
class ProcessReport:
    by_ram: list[dict] = field(default_factory=list)
    by_cpu: list[dict] = field(default_factory=list)
    grouped_by_name: list[dict] = field(default_factory=list)
    total_process_ram_gb: float = 0.0


@dataclass
class GpuReport:
    gpus: list[dict] = field(default_factory=list)


@dataclass
class NetworkReport:
    adapters: list[dict] = field(default_factory=list)
    latency: Optional[dict] = None


@dataclass
class StorageHealthReport:
    disks: list[dict] = field(default_factory=list)
    problem_devices: list[dict] = field(default_factory=list)


@dataclass
class DisplayReport:
    displays: list[dict] = field(default_factory=list)


@dataclass
class StabilityReport:
    """Crash indicators, BSOD history, kernel errors, page faults."""
    uptime_hours: Optional[float] = None
    bsod_dumps: list[dict] = field(default_factory=list)
    kernel_errors: list[dict] = field(default_factory=list)
    page_faults_per_sec: Optional[float] = None
    pool_failures_nonpaged: Optional[int] = None
    pool_failures_paged: Optional[int] = None
    handle_count: Optional[int] = None
    thread_count: Optional[int] = None
    process_count: Optional[int] = None
    details: dict = field(default_factory=dict)


@dataclass
class WslReport:
    """WSL distro details collected from the Windows host side."""
    distros: list[dict] = field(default_factory=list)
    details: dict = field(default_factory=dict)


@dataclass
class Anomaly:
    category: str
    severity: str  # info, warning, critical
    message: str
    value: Optional[float] = None
    threshold: Optional[float] = None


def analyze(system: SystemInfo, ram: RamReport, cpu: CpuReport,
            temps: TemperatureReport, disk: DiskReport,
            processes: ProcessReport,
            display: Optional[DisplayReport] = None,
            stability: Optional[StabilityReport] = None,
            wsl: Optional[WslReport] = None,
            gpu: Optional[GpuReport] = None,
            network: Optional[NetworkReport] = None,
            storage_health: Optional[StorageHealthReport] = None) -> list[Anomaly]:
    anomalies = []

    # --- RAM ---
    if ram.percent_used > 95:
        anomalies.append(Anomaly("ram", "critical",
                                 f"RAM usage critically high: {ram.percent_used:.1f}%",
                                 ram.percent_used, 95))
    elif ram.percent_used > 85:
        anomalies.append(Anomaly("ram", "warning",
                                 f"RAM usage high: {ram.percent_used:.1f}%",
                                 ram.percent_used, 85))

    nonpaged = ram.details.get("nonpaged_pool_mb")
    if nonpaged and nonpaged > 1024:
        anomalies.append(Anomaly("ram", "warning",
                                 f"Nonpaged pool is {nonpaged:.0f} MB (normal: 300-800 MB). "
                                 "Likely a kernel driver leak (VPN, Docker vSwitch, GPU).",
                                 nonpaged, 1024))

    committed = ram.details.get("committed_gb")
    if committed and committed > system.total_ram_gb:
        anomalies.append(Anomaly("ram", "warning",
                                 f"Committed memory ({committed:.1f} GB) exceeds physical RAM "
                                 f"({system.total_ram_gb:.1f} GB). System is overcommitted.",
                                 committed, system.total_ram_gb))

    # --- CPU ---
    if cpu.load_percent > 95:
        anomalies.append(Anomaly("cpu", "critical",
                                 f"CPU load critically high: {cpu.load_percent:.1f}%",
                                 cpu.load_percent, 95))
    elif cpu.load_percent > 80:
        anomalies.append(Anomaly("cpu", "warning",
                                 f"CPU load high: {cpu.load_percent:.1f}%",
                                 cpu.load_percent, 80))

    if cpu.interrupts_per_sec and cpu.interrupts_per_sec > 100000:
        anomalies.append(Anomaly("cpu", "warning",
                                 f"Interrupt rate is {cpu.interrupts_per_sec:.0f}/sec "
                                 "(normal: 10,000-50,000). Possible driver issue.",
                                 cpu.interrupts_per_sec, 100000))

    if cpu.interrupt_time_percent and cpu.interrupt_time_percent > 5:
        anomalies.append(Anomaly("cpu", "warning",
                                 f"Interrupt time is {cpu.interrupt_time_percent:.2f}% "
                                 "(normal: < 2%). Hardware/driver issue likely.",
                                 cpu.interrupt_time_percent, 5))

    if cpu.dpc_time_percent and cpu.dpc_time_percent > 5:
        anomalies.append(Anomaly("cpu", "warning",
                                 f"DPC time is {cpu.dpc_time_percent:.2f}% "
                                 "(normal: < 2%). Driver latency issue.",
                                 cpu.dpc_time_percent, 5))

    if cpu.context_switches_per_sec and cpu.context_switches_per_sec > 100000:
        anomalies.append(Anomaly("cpu", "warning",
                                 f"Context switches: {cpu.context_switches_per_sec:,.0f}/sec "
                                 "(normal: < 100,000). Heavy thread contention.",
                                 cpu.context_switches_per_sec, 100000))

    # --- Temperatures ---
    for reading in temps.readings:
        temp_c = reading.get("current_c", 0)
        label = reading.get("label", "unknown")
        if temp_c and temp_c > 95:
            anomalies.append(Anomaly("temperature", "critical",
                                     f"{label} at {temp_c:.1f}°C — thermal throttling likely",
                                     temp_c, 95))
        elif temp_c and temp_c > 80:
            anomalies.append(Anomaly("temperature", "warning",
                                     f"{label} at {temp_c:.1f}°C — running hot",
                                     temp_c, 80))

    # --- Disk ---
    for part in disk.partitions:
        pct = part.get("percent", 0)
        mount = part.get("mountpoint") or part.get("device", "?")
        if pct > 95:
            anomalies.append(Anomaly("disk", "critical",
                                     f"Disk {mount} is {pct:.1f}% full",
                                     pct, 95))
        elif pct > 90:
            anomalies.append(Anomaly("disk", "warning",
                                     f"Disk {mount} is {pct:.1f}% full",
                                     pct, 90))

    if disk.io:
        queue = disk.io.get("queue_length", 0)
        if queue and queue > 2.0:
            anomalies.append(Anomaly("disk", "warning",
                                     f"Disk queue length is {queue:.2f} (normal: < 2). I/O bottleneck.",
                                     queue, 2.0))

    # --- Processes ---
    for proc in processes.by_ram[:10]:
        ram_mb = proc.get("ram_mb", 0)
        name = proc.get("name", "?")
        if ram_mb > 4096:
            anomalies.append(Anomaly("process", "info",
                                     f"{name} using {ram_mb:.0f} MB RAM (> 4 GB)",
                                     ram_mb, 4096))

    SYSTEM_PROCESSES = {
        "system idle process", "system", "idle", "kernel_task",
        "svchost.exe", "wmiprvse.exe",
        "csrss.exe", "lsass.exe", "smss.exe", "services.exe",
        "registry", "memory compression",
    }

    for proc in processes.by_cpu[:10]:
        cpu_sec = proc.get("cpu_seconds", 0)
        name = proc.get("name", "?")
        if name.lower() in SYSTEM_PROCESSES:
            continue
        if cpu_sec > 10000:
            anomalies.append(Anomaly("process", "warning",
                                     f"{name} has burned {cpu_sec:.0f} CPU seconds "
                                     f"({cpu_sec/3600:.1f} hours). Possible runaway process.",
                                     cpu_sec, 10000))

    ram_gap = ram.used_gb - processes.total_process_ram_gb
    if ram_gap > 4:
        anomalies.append(Anomaly("ram", "warning",
                                 f"Kernel/driver memory gap: {ram_gap:.1f} GB unaccounted for "
                                 "(OS used - process total). Possible kernel memory leak.",
                                 ram_gap, 4))

    # --- Display ---
    if display:
        refresh_rates = set()
        gpu_names = set()
        for d in display.displays:
            if d.get("refresh_rate"):
                refresh_rates.add(d["refresh_rate"])
            if d.get("gpu"):
                gpu_names.add(d["gpu"])
        if len(gpu_names) > 1:
            anomalies.append(Anomaly("display", "info",
                                     f"Multi-GPU rendering: {', '.join(gpu_names)}. "
                                     "Cross-GPU compositing can add input latency."))
        if len(refresh_rates) > 1:
            anomalies.append(Anomaly("display", "info",
                                     f"Mixed refresh rates: {', '.join(str(r)+'Hz' for r in sorted(refresh_rates))}. "
                                     "DWM cannot use a single vsync cadence."))

    # --- Stability ---
    if stability:
        if stability.bsod_dumps:
            count = len(stability.bsod_dumps)
            recent = stability.bsod_dumps[0].get("date", "unknown")
            anomalies.append(Anomaly("stability", "critical",
                                     f"{count} BSOD minidump(s) found. Most recent: {recent}. "
                                     "System has crashed.",
                                     count, 0))

        if stability.kernel_errors:
            count = len(stability.kernel_errors)
            anomalies.append(Anomaly("stability", "warning",
                                     f"{count} critical kernel/system error(s) in event log.",
                                     count, 0))

        if stability.page_faults_per_sec and stability.page_faults_per_sec > 5000:
            anomalies.append(Anomaly("stability", "warning",
                                     f"Page faults: {stability.page_faults_per_sec:,.0f}/sec "
                                     "(normal: < 5,000). Heavy paging — possible memory pressure.",
                                     stability.page_faults_per_sec, 5000))

        if stability.pool_failures_nonpaged and stability.pool_failures_nonpaged > 0:
            anomalies.append(Anomaly("stability", "critical",
                                     f"Nonpaged pool allocation failures: {stability.pool_failures_nonpaged}. "
                                     "Kernel ran out of locked memory — crash risk.",
                                     stability.pool_failures_nonpaged, 0))

        if stability.pool_failures_paged and stability.pool_failures_paged > 0:
            anomalies.append(Anomaly("stability", "warning",
                                     f"Paged pool allocation failures: {stability.pool_failures_paged}.",
                                     stability.pool_failures_paged, 0))

        if stability.uptime_hours and stability.uptime_hours < 1:
            anomalies.append(Anomaly("stability", "info",
                                     f"System uptime: {stability.uptime_hours:.1f} hours. "
                                     "Recently rebooted — check if it was a crash."))

        if stability.handle_count and stability.handle_count > 500000:
            anomalies.append(Anomaly("stability", "warning",
                                     f"System-wide handle count: {stability.handle_count:,}. "
                                     "Possible handle leak.",
                                     stability.handle_count, 500000))

    # --- WSL ---
    if wsl:
        for distro in wsl.distros:
            ram_mb = distro.get("ram_mb", 0)
            if ram_mb > 8192:
                anomalies.append(Anomaly("wsl", "info",
                                         f"WSL distro '{distro.get('name', '?')}' using "
                                         f"{ram_mb/1024:.1f} GB RAM.",
                                         ram_mb, 8192))
            oom_kills = distro.get("oom_kills", 0)
            if oom_kills > 0:
                anomalies.append(Anomaly("wsl", "warning",
                                         f"WSL distro '{distro.get('name', '?')}' has "
                                         f"{oom_kills} OOM kill(s).",
                                         oom_kills, 0))

    # --- GPU ---
    if gpu:
        for g in gpu.gpus:
            temp = g.get("temperature_c")
            name = g.get("name", "GPU")
            if temp and temp > 95:
                anomalies.append(Anomaly("gpu", "critical",
                                         f"{name} at {temp}°C — thermal throttling likely.",
                                         temp, 95))
            elif temp and temp > 85:
                anomalies.append(Anomaly("gpu", "warning",
                                         f"{name} at {temp}°C — running hot.",
                                         temp, 85))
            util = g.get("utilization_percent")
            if util and util > 95:
                anomalies.append(Anomaly("gpu", "warning",
                                         f"{name} GPU utilization at {util}%. Fully saturated.",
                                         util, 95))
            vram_used = g.get("vram_used_mb", 0)
            vram_total = g.get("vram_total_mb", 0)
            if vram_total and vram_used / vram_total > 0.90:
                pct = vram_used / vram_total * 100
                anomalies.append(Anomaly("gpu", "warning",
                                         f"{name} VRAM usage: {vram_used} MB / {vram_total} MB ({pct:.0f}%).",
                                         pct, 90))

    # --- Network ---
    if network:
        if network.latency:
            ping_ms = network.latency.get("ping_avg_ms")
            if ping_ms and ping_ms > 100:
                anomalies.append(Anomaly("network", "warning",
                                         f"Network latency: {ping_ms:.0f}ms avg (normal: < 50ms).",
                                         ping_ms, 100))
            packet_loss = network.latency.get("packet_loss_percent")
            if packet_loss and packet_loss > 0:
                anomalies.append(Anomaly("network", "warning",
                                         f"Packet loss: {packet_loss:.1f}%.",
                                         packet_loss, 0))
            dns_ms = network.latency.get("dns_ms")
            if dns_ms and dns_ms > 200:
                anomalies.append(Anomaly("network", "warning",
                                         f"DNS resolution: {dns_ms:.0f}ms (normal: < 100ms). "
                                         "Consider switching DNS to 1.1.1.1 or 8.8.8.8.",
                                         dns_ms, 200))

    # --- Storage Health ---
    if storage_health:
        for d in storage_health.disks:
            health = d.get("health_status", "").lower()
            name = d.get("name", "Disk")
            if health and health != "healthy":
                anomalies.append(Anomaly("storage", "critical",
                                         f"{name} health status: {d['health_status']}. "
                                         "Backup data immediately.",
                                         0, 0))
        for dev in storage_health.problem_devices:
            anomalies.append(Anomaly("storage", "warning",
                                     f"Problem device: {dev.get('name', 'unknown')} "
                                     f"(error code {dev.get('error_code', '?')}).",
                                     0, 0))

    return anomalies
