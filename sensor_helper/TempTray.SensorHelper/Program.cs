using System.Diagnostics;
using System.Globalization;
using System.Reflection;
using System.Runtime.Loader;
using System.Text.Json;
using Microsoft.Win32;
using System.Security.Principal;
using LibreHardwareMonitor.Hardware;

namespace TempTray.SensorHelper;

internal sealed class UpdateVisitor : IVisitor
{
    public void VisitComputer(IComputer computer) => computer.Traverse(this);

    public void VisitHardware(IHardware hardware)
    {
        try { hardware.Update(); } catch { }
        foreach (IHardware subHardware in hardware.SubHardware)
        {
            subHardware.Accept(this);
        }
    }

    public void VisitSensor(ISensor sensor) { }
    public void VisitParameter(IParameter parameter) { }
}

internal sealed class Program
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        WriteIndented = false
    };

    private sealed record Result(
        string CpuName,
        float? CpuTempC,
        string GpuName,
        float? GpuTempC,
        float? CpuPowerW = null,
        float? GpuPowerW = null,
        string? CpuSensorName = null,
        string? CpuSensorHardware = null,
        string? GpuSensorName = null,
        string? GpuSensorHardware = null,
        string? Status = null,
        string? Error = null
    );

    private sealed record SensorChoice(
        string HardwareName,
        string HardwareType,
        string SensorName,
        float Value,
        int Score
    );

    private static Result? LastGood;

    private static int Main()
    {
        ConfigureRuntimeEnvironment();

        Computer computer = new()
        {
            IsCpuEnabled = true,
            IsGpuEnabled = true,
            IsMotherboardEnabled = true,
            IsControllerEnabled = true,
            IsMemoryEnabled = false,
            IsNetworkEnabled = false,
            IsStorageEnabled = false,
            IsPsuEnabled = false,
            IsBatteryEnabled = false
        };

        // LHM changes its Computer feature flags across releases. Enable any extra
        // controller/EC/SuperIO flags when they exist without tying TrayTemps to one
        // exact LibreHardwareMonitorLib build. Older boards often expose CPU temp
        // through these paths rather than the CPU hardware node.
        EnableComputerOption(computer, "IsEmbeddedControllerEnabled", true);
        EnableComputerOption(computer, "IsSuperIOEnabled", true);
        EnableComputerOption(computer, "IsEcEnabled", true);
        EnableComputerOption(computer, "IsControllerEnabled", true);
        EnableComputerOption(computer, "IsMotherboardEnabled", true);

        try
        {
            computer.Open();
            WarmUp(computer, passes: 8, delayMs: 150);

            string? line;
            while ((line = Console.ReadLine()) != null)
            {
                line = line.Trim().ToLowerInvariant();
                if (line is "quit" or "exit") break;

                try
                {
                    if (line == "dump")
                    {
                        DumpSensors(computer);
                        continue;
                    }

                    // Empty input and "read" both return a normal reading. This makes manual
                    // PowerShell tests less fragile while preserving the app protocol.
                    if (line.Length == 0 || line == "read")
                    {
                        WriteJson(ReadTempsWithRetry(computer));
                        continue;
                    }

                    WriteJson(new Result("Unknown", null, "Unknown", null, Status: "unknown-command", Error: $"Unknown command: {line}"));
                }
                catch (Exception ex)
                {
                    WriteJson(new Result("Unknown", null, "Unknown", null, Status: "read-error", Error: ex.GetType().Name + ": " + ex.Message));
                }
            }
        }
        catch (Exception ex)
        {
            WriteJson(new Result("Unknown", null, "Unknown", null, Status: "startup-error", Error: ex.GetType().Name + ": " + ex.Message));
            return 1;
        }
        finally
        {
            try { computer.Close(); } catch { }
        }

        return 0;
    }


    private static void ConfigureRuntimeEnvironment()
    {
        try
        {
            Directory.SetCurrentDirectory(AppContext.BaseDirectory);
            string existingPath = Environment.GetEnvironmentVariable("PATH") ?? string.Empty;
            if (!existingPath.Split(Path.PathSeparator).Any(p => string.Equals(p, AppContext.BaseDirectory, StringComparison.OrdinalIgnoreCase)))
            {
                Environment.SetEnvironmentVariable("PATH", AppContext.BaseDirectory + Path.PathSeparator + existingPath);
            }

            AssemblyLoadContext.Default.Resolving += (_, assemblyName) =>
            {
                string candidate = Path.Combine(AppContext.BaseDirectory, assemblyName.Name + ".dll");
                if (File.Exists(candidate))
                {
                    try { return AssemblyLoadContext.Default.LoadFromAssemblyPath(candidate); } catch { }
                }
                return null;
            };
        }
        catch { }
    }

    private static void EnableComputerOption(Computer computer, string propertyName, bool value)
    {
        try
        {
            var property = typeof(Computer).GetProperty(propertyName);
            if (property is not null && property.CanWrite && property.PropertyType == typeof(bool))
            {
                property.SetValue(computer, value);
            }
        }
        catch { }
    }

    private static void WarmUp(Computer computer, int passes, int delayMs)
    {
        UpdateVisitor visitor = new();
        for (int i = 0; i < passes; i++)
        {
            computer.Accept(visitor);
            Thread.Sleep(delayMs);
        }
    }

    private static Result ReadTempsWithRetry(Computer computer)
    {
        Result last = LastGood ?? new Result("Unknown", null, "Unknown", null, Status: "warming-up");
        UpdateVisitor visitor = new();

        // Cold CPU sensors on older Ryzen and newer Intel systems often start as null/0.
        // The helper is intentionally long-lived; this retry loop prevents one bad early
        // read from becoming a permanent "--°C" state in the app.
        for (int i = 0; i < 8; i++)
        {
            computer.Accept(visitor);
            Result current = ReadTemps(computer, last);

            bool gotGpu = current.GpuTempC.HasValue || last.GpuTempC.HasValue;
            bool gotCpu = current.CpuTempC.HasValue;

            if (gotCpu)
            {
                LastGood = current with { Status = "ok" };
                return LastGood;
            }

            // Keep returning a valid previous CPU reading instead of flashing back to --°C.
            if (LastGood?.CpuTempC is not null && gotGpu && i >= 2)
            {
                LastGood = MergeWithLastGood(current, LastGood) with { Status = "last-known-good-cpu" };
                return LastGood;
            }

            last = current;
            Thread.Sleep(200);
        }

        Result finalResult = MergeWithLastGood(last, LastGood) with { Status = last.CpuTempC.HasValue ? "ok" : "cpu-unavailable-after-retry" };
        if (finalResult.CpuTempC.HasValue || finalResult.GpuTempC.HasValue)
        {
            LastGood = finalResult;
        }
        return finalResult;
    }

    private static Result MergeWithLastGood(Result current, Result? previous)
    {
        if (previous is null) return current;
        return current with
        {
            CpuName = current.CpuName != "Unknown" ? current.CpuName : previous.CpuName,
            GpuName = current.GpuName != "Unknown" ? current.GpuName : previous.GpuName,
            CpuTempC = current.CpuTempC ?? previous.CpuTempC,
            GpuTempC = current.GpuTempC ?? previous.GpuTempC,
            CpuPowerW = current.CpuPowerW ?? previous.CpuPowerW,
            GpuPowerW = current.GpuPowerW ?? previous.GpuPowerW,
            CpuSensorName = current.CpuSensorName ?? previous.CpuSensorName,
            CpuSensorHardware = current.CpuSensorHardware ?? previous.CpuSensorHardware,
            GpuSensorName = current.GpuSensorName ?? previous.GpuSensorName,
            GpuSensorHardware = current.GpuSensorHardware ?? previous.GpuSensorHardware,
        };
    }

    private static Result ReadTemps(Computer computer, Result? previous)
    {
        List<IHardware> hardware = FlattenHardware(computer.Hardware).ToList();

        IHardware? cpu = hardware.FirstOrDefault(h => h.HardwareType == HardwareType.Cpu);
        List<IHardware> gpus = hardware.Where(IsGpuHardware).ToList();
        IHardware? gpu = SelectBestGpu(gpus);

        string cpuName = cpu?.Name ?? previous?.CpuName ?? "Unknown";
        string gpuName = gpu?.Name ?? previous?.GpuName ?? "Unknown";

        SensorChoice? cpuChoice = cpu is null ? null : SelectCpuTemperature(cpu, primaryCpu: true);
        cpuChoice ??= SelectCpuFallbackTemperature(hardware, cpu);

        SensorChoice? gpuChoice = gpu is null ? null : SelectGpuTemperature(gpu);
        SensorChoice? cpuPowerChoice = cpu is null ? null : SelectCpuPower(cpu);
        SensorChoice? gpuPowerChoice = gpu is null ? null : SelectGpuPower(gpu);

        return new Result(
            CpuName: cpuName,
            CpuTempC: cpuChoice?.Value,
            GpuName: gpuName,
            GpuTempC: gpuChoice?.Value,
            CpuPowerW: cpuPowerChoice?.Value,
            GpuPowerW: gpuPowerChoice?.Value,
            CpuSensorName: cpuChoice?.SensorName,
            CpuSensorHardware: cpuChoice?.HardwareName,
            GpuSensorName: gpuChoice?.SensorName,
            GpuSensorHardware: gpuChoice?.HardwareName,
            Status: cpuChoice is null ? "cpu-not-ready" : "ok"
        );
    }

    private static IHardware? SelectBestGpu(List<IHardware> gpus)
    {
        if (gpus.Count == 0) return null;

        IHardware? nvidia = gpus.FirstOrDefault(h => h.HardwareType == HardwareType.GpuNvidia || TextFor(h).Contains("nvidia") || TextFor(h).Contains("geforce") || TextFor(h).Contains("rtx") || TextFor(h).Contains("gtx"));
        if (nvidia is not null) return nvidia;

        IHardware? intelArc = gpus.FirstOrDefault(h => TextFor(h).Contains("arc"));
        if (intelArc is not null) return intelArc;

        IHardware? amdDiscrete = gpus.FirstOrDefault(h =>
        {
            string text = TextFor(h);
            return (h.HardwareType == HardwareType.GpuAmd || text.Contains("amd") || text.Contains("radeon"))
                && !text.Contains("radeon(tm) graphics")
                && !text.Contains("radeon graphics");
        });
        if (amdDiscrete is not null) return amdDiscrete;

        IHardware? withTemp = gpus
            .Select(g => new { Gpu = g, Temp = SelectGpuTemperature(g) })
            .Where(x => x.Temp is not null)
            .OrderByDescending(x => x.Temp!.Score)
            .ThenByDescending(x => x.Temp!.Value)
            .Select(x => x.Gpu)
            .FirstOrDefault();
        return withTemp ?? gpus[0];
    }

    private static IEnumerable<IHardware> FlattenHardware(IEnumerable<IHardware> hardware)
    {
        foreach (IHardware h in hardware)
        {
            yield return h;
            foreach (IHardware child in FlattenHardware(h.SubHardware))
            {
                yield return child;
            }
        }
    }

    private static bool IsGpuHardware(IHardware h)
    {
        if (h.HardwareType is HardwareType.GpuNvidia or HardwareType.GpuAmd or HardwareType.GpuIntel) return true;
        string text = TextFor(h);
        return text.Contains("nvidia") || text.Contains("geforce") || text.Contains("rtx") || text.Contains("gtx") || text.Contains("radeon") || text.Contains("intel gpu") || text.Contains("arc");
    }

    private static string TextFor(IHardware h) => (h.Name + " " + h.Identifier).ToLowerInvariant();

    private static SensorChoice? SelectCpuTemperature(IHardware cpu, bool primaryCpu)
    {
        return TemperatureSensors(cpu)
            .Select(s => ScoreCpuSensor(cpu, s, primaryCpu))
            .Where(c => c is not null)
            .OrderByDescending(c => c!.Score)
            .ThenByDescending(c => c!.Value)
            .FirstOrDefault();
    }

    private static SensorChoice? SelectCpuFallbackTemperature(IEnumerable<IHardware> allHardware, IHardware? cpu)
    {
        return allHardware
            .Where(hw => hw != cpu)
            .Where(hw => !IsGpuHardware(hw))
            .Where(hw => hw.HardwareType is not HardwareType.Storage and not HardwareType.Memory and not HardwareType.Network)
            .SelectMany(hw => TemperatureSensors(hw).Select(sensor => ScoreCpuSensor(hw, sensor, primaryCpu: false)))
            .Where(c => c is not null)
            .OrderByDescending(c => c!.Score)
            .ThenByDescending(c => c!.Value)
            .FirstOrDefault();
    }

    private static SensorChoice? SelectGpuTemperature(IHardware gpu)
    {
        return TemperatureSensors(gpu)
            .Select(s => ScoreGpuSensor(gpu, s))
            .Where(c => c is not null)
            .OrderByDescending(c => c!.Score)
            .ThenByDescending(c => c!.Value)
            .FirstOrDefault();
    }

    private static SensorChoice? SelectCpuPower(IHardware cpu)
    {
        return PowerSensors(cpu)
            .Select(s => ScoreCpuPowerSensor(cpu, s))
            .Where(c => c is not null)
            .OrderByDescending(c => c!.Score)
            .ThenByDescending(c => c!.Value)
            .FirstOrDefault();
    }

    private static SensorChoice? SelectGpuPower(IHardware gpu)
    {
        return PowerSensors(gpu)
            .Select(s => ScoreGpuPowerSensor(gpu, s))
            .Where(c => c is not null)
            .OrderByDescending(c => c!.Score)
            .ThenByDescending(c => c!.Value)
            .FirstOrDefault();
    }

    private static SensorChoice? ScoreCpuSensor(IHardware hw, ISensor sensor, bool primaryCpu)
    {
        if (!sensor.Value.HasValue || !IsPlausibleCpuTemperature(sensor.Value.Value)) return null;

        string name = sensor.Name.ToLowerInvariant();
        if (name.Contains("distance to tjmax") || name.Contains("distance to t-jmax")) return null;

        int score = 0;
        if (primaryCpu) score += 60;

        if (name.Contains("cpu package")) score += 70;
        else if (name.Contains("package")) score += 65;
        else if (name.Contains("tctl/tdie") || name.Contains("tctl") || name.Contains("tdie")) score += 64;
        else if (name.Contains("core max")) score += 58;
        else if (name.Contains("core average")) score += 54;
        else if (name.Contains("ccd")) score += 50;
        else if (name.Contains("die")) score += 45;
        else if (name.Contains("p-core") || name.Contains("e-core") || name.Contains("core #") || name.Contains("core ")) score += 35;
        else if (name.Contains("cpu")) score += 30;

        if (!primaryCpu)
        {
            string hwText = TextFor(hw);
            if (name.Contains("cpu") || name.Contains("package") || name.Contains("tctl") || name.Contains("tdie")) score += 35;
            else if (hw.HardwareType is HardwareType.Motherboard or HardwareType.SuperIO or HardwareType.EmbeddedController || hwText.Contains("nuvoton") || hwText.Contains("ite") || hwText.Contains("asus") || hwText.Contains("msi")) score += 10;
            else return null;
        }

        if (score <= 0) return null;
        return new SensorChoice(hw.Name, hw.HardwareType.ToString(), sensor.Name, sensor.Value.Value, score);
    }

    private static SensorChoice? ScoreGpuSensor(IHardware hw, ISensor sensor)
    {
        if (!sensor.Value.HasValue || !IsPlausibleGpuTemperature(sensor.Value.Value)) return null;
        string name = sensor.Name.ToLowerInvariant();
        int score = 20;
        if (name.Contains("gpu core")) score += 80;
        else if (name == "core" || name.Contains(" core")) score += 70;
        else if (name.Contains("hot spot") || name.Contains("hotspot")) score += 50;
        else if (name.Contains("memory junction") || name.Contains("junction")) score += 35;
        else if (name.Contains("memory")) score += 20;
        return new SensorChoice(hw.Name, hw.HardwareType.ToString(), sensor.Name, sensor.Value.Value, score);
    }

    private static SensorChoice? ScoreCpuPowerSensor(IHardware hw, ISensor sensor)
    {
        if (!sensor.Value.HasValue || !IsPlausiblePower(sensor.Value.Value)) return null;
        string name = sensor.Name.ToLowerInvariant();
        int score = 0;
        if (name.Contains("cpu package") || name == "package") score += 90;
        else if (name.Contains("package")) score += 75;
        else if (name.Contains("ppt")) score += 70;
        else if (name.Contains("core") && !name.Contains("gpu")) score += 40;
        else if (name.Contains("cpu")) score += 35;
        if (score <= 0) return null;
        return new SensorChoice(hw.Name, hw.HardwareType.ToString(), sensor.Name, sensor.Value.Value, score);
    }

    private static SensorChoice? ScoreGpuPowerSensor(IHardware hw, ISensor sensor)
    {
        if (!sensor.Value.HasValue || !IsPlausiblePower(sensor.Value.Value)) return null;
        string name = sensor.Name.ToLowerInvariant();
        int score = 20;
        if (name.Contains("gpu package")) score += 90;
        else if (name.Contains("board power")) score += 85;
        else if (name.Contains("total board")) score += 82;
        else if (name.Contains("gpu power")) score += 80;
        else if (name == "package" || name.Contains("package")) score += 65;
        else if (name.Contains("power")) score += 30;
        return new SensorChoice(hw.Name, hw.HardwareType.ToString(), sensor.Name, sensor.Value.Value, score);
    }

    private static bool IsPlausibleCpuTemperature(float value) => value > 5.0f && value < 125.0f;
    private static bool IsPlausibleGpuTemperature(float value) => value > 5.0f && value < 130.0f;
    private static bool IsPlausiblePower(float value) => value > 0.05f && value < 2000.0f;

    private static IEnumerable<ISensor> TemperatureSensors(IHardware hardware)
    {
        foreach (ISensor sensor in hardware.Sensors)
        {
            if (sensor.SensorType == SensorType.Temperature)
            {
                yield return sensor;
            }
        }
        foreach (IHardware subHardware in hardware.SubHardware)
        {
            foreach (ISensor sensor in TemperatureSensors(subHardware))
            {
                yield return sensor;
            }
        }
    }

    private static IEnumerable<ISensor> PowerSensors(IHardware hardware)
    {
        foreach (ISensor sensor in hardware.Sensors)
        {
            if (sensor.SensorType == SensorType.Power)
            {
                yield return sensor;
            }
        }
        foreach (IHardware subHardware in hardware.SubHardware)
        {
            foreach (ISensor sensor in PowerSensors(subHardware))
            {
                yield return sensor;
            }
        }
    }

    private static void DumpSensors(Computer computer)
    {
        Result selected = ReadTempsWithRetry(computer);
        Console.WriteLine("TrayTemps helper selected readings");
        Console.WriteLine($"SELECTED|CPU|{selected.CpuName}|{selected.CpuSensorHardware ?? "none"}|{selected.CpuSensorName ?? "none"}|{ValueText(selected.CpuTempC)}|{selected.Status ?? "unknown"}");
        Console.WriteLine($"SELECTED|GPU|{selected.GpuName}|{selected.GpuSensorHardware ?? "none"}|{selected.GpuSensorName ?? "none"}|{ValueText(selected.GpuTempC)}|{selected.Status ?? "unknown"}");
        Console.WriteLine($"SELECTED_POWER|CPU|{selected.CpuName}|{ValueText(selected.CpuPowerW)}");
        Console.WriteLine($"SELECTED_POWER|GPU|{selected.GpuName}|{ValueText(selected.GpuPowerW)}");
        Console.WriteLine();
        Console.WriteLine("LibreHardwareMonitor diagnostics");
        Console.WriteLine($"DIAG|BaseDirectory|{AppContext.BaseDirectory}");
        Console.WriteLine($"DIAG|CurrentDirectory|{Directory.GetCurrentDirectory()}");
        Console.WriteLine($"DIAG|LhmAssembly|{typeof(Computer).Assembly.Location}");
        Console.WriteLine($"DIAG|LhmVersion|{typeof(Computer).Assembly.GetName().Version}");
        Console.WriteLine($"DIAG|ProcessElevated|{IsProcessElevated()}");
        Console.WriteLine($"DIAG|PawnIOService|{PawnIoServiceStatus()}");
        Console.WriteLine($"DIAG|PawnIODriverFiles|{PawnIoDriverFilesStatus()}");
        Console.WriteLine($"DIAG|KnownLowLevelDriverServices|{KnownLowLevelDriverServicesStatus()}");
        Console.WriteLine($"DIAG|LowLevelAccessAssessment|{LowLevelAccessAssessment()}");
        Console.WriteLine($"DIAG|DriverCandidateInventory|{DriverCandidateInventory()}");
        Console.WriteLine($"DIAG|LhmAssemblyFile|{FileDiagnostics(typeof(Computer).Assembly.Location)}");
        string officialLhmExe = Path.Combine(AppContext.BaseDirectory, "LibreHardwareMonitor.exe");
        string nestedOfficialLhm = Path.Combine(AppContext.BaseDirectory, "lhm_official_release");
        Console.WriteLine($"DIAG|OfficialLhmExeInHelper|{FileDiagnostics(officialLhmExe)}");
        Console.WriteLine($"DIAG|NestedOfficialLhmFolder|{(Directory.Exists(nestedOfficialLhm) ? "present" : "missing")}");
        Console.WriteLine("DIAG|DriverInstallAction|not-attempted;diagnostics-only");
        Console.WriteLine($"DIAG|WarmUpPasses|8");
        Console.WriteLine($"DIAG|ReadRetryPasses|8");
        Console.WriteLine("DIAG|EnabledFlags|Cpu,Gpu,Motherboard,Controller plus EmbeddedController/SuperIO/Ec if present");
        Console.WriteLine();
        Console.WriteLine("CPU temperature candidates");
        DumpCpuCandidates(computer);
        Console.WriteLine();
        Console.WriteLine("Raw LibreHardwareMonitor sensors");

        foreach (IHardware hw in FlattenHardware(computer.Hardware))
        {
            Console.WriteLine($"HARDWARE|{hw.HardwareType}|{hw.Name}|{hw.Identifier}");
            foreach (ISensor s in hw.Sensors)
            {
                Console.WriteLine($"SENSOR|{hw.Name}|{s.SensorType}|{s.Name}|{ValueText(s.Value)}");
            }
        }
        Console.WriteLine($"HELPER|BaseDirectory|{AppContext.BaseDirectory}");
        foreach (string file in new[] { "LibreHardwareMonitor.exe", "LibreHardwareMonitorLib.dll", "LibreHardwareMonitor.dll", "System.Management.dll", "HidSharp.dll", "NvAPIWrapper.Net.dll", "RAMSPDToolkit-NDD.dll", "Iot.Device.Bindings.dll", "System.Device.Gpio.dll", "PawnIO.sys", "PawnIo.sys", "WinRing0x64.sys", "WinRing0.sys" })
        {
            string path = Path.Combine(AppContext.BaseDirectory, file);
            Console.WriteLine($"HELPER_FILE|{file}|{File.Exists(path)}|{FileDiagnostics(path)}");
        }
        try
        {
            foreach (string dll in Directory.GetFiles(AppContext.BaseDirectory, "*.dll").Select(Path.GetFileName).Where(n => n is not null).OrderBy(n => n))
            {
                Console.WriteLine($"HELPER_DLL|{dll}");
            }
        }
        catch { }
        Console.Out.Flush();
    }

    private static void DumpCpuCandidates(Computer computer)
    {
        List<IHardware> hardware = FlattenHardware(computer.Hardware).ToList();
        IHardware? cpu = hardware.FirstOrDefault(h => h.HardwareType == HardwareType.Cpu);
        foreach (IHardware hw in hardware.Where(h => !IsGpuHardware(h)))
        {
            bool primaryCpu = cpu is not null && ReferenceEquals(hw, cpu);
            foreach (ISensor sensor in TemperatureSensors(hw))
            {
                string reason = "accepted";
                int score = 0;
                if (!sensor.Value.HasValue) reason = "null";
                else if (!IsPlausibleCpuTemperature(sensor.Value.Value)) reason = "implausible";
                else
                {
                    SensorChoice? choice = ScoreCpuSensor(hw, sensor, primaryCpu);
                    if (choice is null) reason = "filtered";
                    else score = choice.Score;
                }
                Console.WriteLine($"CPU_CANDIDATE|{hw.HardwareType}|{hw.Name}|{sensor.Name}|{ValueText(sensor.Value)}|{reason}|{score}");
            }
        }
    }

    private static bool IsProcessElevated()
    {
        try
        {
            using WindowsIdentity identity = WindowsIdentity.GetCurrent();
            WindowsPrincipal principal = new(identity);
            return principal.IsInRole(WindowsBuiltInRole.Administrator);
        }
        catch
        {
            return false;
        }
    }


    private static string LowLevelAccessAssessment()
    {
        try
        {
            bool elevated = IsProcessElevated();
            bool anyDriverFile = CandidateDriverPaths().Any(File.Exists);
            bool anyKnownService = ServiceExists("PawnIO") || ServiceExists("PawnIo") || ServiceExists("PawnIODriver") || ServiceExists("WinRing0_1_2_0") || ServiceExists("WinRing0") || ServiceExists("OpenLibSys");

            if (!elevated) return "degraded:not-elevated";
            if (anyKnownService && anyDriverFile) return "possibly-available:service-and-driver-file-present";
            if (anyKnownService) return "partial:service-present-driver-file-not-found-by-traytemps";
            if (anyDriverFile) return "partial:driver-file-present-service-missing";
            return "missing:no-known-low-level-driver-service-or-driver-file";
        }
        catch (Exception ex)
        {
            return "assessment-error:" + ex.GetType().Name;
        }
    }

    private static bool ServiceExists(string name)
    {
        try
        {
            using RegistryKey? key = Registry.LocalMachine.OpenSubKey(@"SYSTEM\CurrentControlSet\Services\" + name);
            return key is not null;
        }
        catch { return false; }
    }

    private static IEnumerable<string> CandidateDriverPaths()
    {
        string systemDrivers = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "System32", "drivers");
        string[] names = { "PawnIO.sys", "PawnIo.sys", "WinRing0x64.sys", "WinRing0.sys", "WinRing0_1_2_0.sys", "OpenLibSys.sys" };
        foreach (string name in names)
        {
            yield return Path.Combine(AppContext.BaseDirectory, name);
            yield return Path.Combine(systemDrivers, name);
        }
    }

    private static string DriverCandidateInventory()
    {
        try
        {
            List<string> parts = new();
            foreach (string path in CandidateDriverPaths().Distinct(StringComparer.OrdinalIgnoreCase))
            {
                if (File.Exists(path))
                {
                    parts.Add(Path.GetFileName(path) + "=" + FileDiagnostics(path));
                }
            }

            try
            {
                foreach (string sys in Directory.GetFiles(AppContext.BaseDirectory, "*.sys", SearchOption.AllDirectories))
                {
                    string name = Path.GetFileName(sys);
                    if (!parts.Any(p => p.StartsWith(name + "=", StringComparison.OrdinalIgnoreCase)))
                    {
                        parts.Add(name + "=" + FileDiagnostics(sys));
                    }
                }
            }
            catch { }

            return parts.Count == 0 ? "none" : string.Join(",", parts);
        }
        catch (Exception ex)
        {
            return "inventory-error:" + ex.GetType().Name;
        }
    }

    private static string FileDiagnostics(string path)
    {
        try
        {
            if (string.IsNullOrWhiteSpace(path) || !File.Exists(path)) return "missing";
            FileInfo info = new(path);
            string version = "unknown";
            try
            {
                FileVersionInfo vi = FileVersionInfo.GetVersionInfo(path);
                version = string.IsNullOrWhiteSpace(vi.FileVersion) ? "unknown" : vi.FileVersion!;
            }
            catch { }
            return $"present;bytes={info.Length};version={version}";
        }
        catch (Exception ex)
        {
            return "filediag-error:" + ex.GetType().Name;
        }
    }

    private static string PawnIoServiceStatus()
    {
        return ServiceStatusForNames(new[] { "PawnIO", "PawnIo", "PawnIODriver", "LibreHardwareMonitor" });
    }

    private static string KnownLowLevelDriverServicesStatus()
    {
        return ServiceStatusForNames(new[] { "PawnIO", "WinRing0_1_2_0", "WinRing0", "OpenLibSys" });
    }

    private static string ServiceStatusForNames(IEnumerable<string> names)
    {
        try
        {
            List<string> parts = new();
            foreach (string name in names.Distinct(StringComparer.OrdinalIgnoreCase))
            {
                using RegistryKey? key = Registry.LocalMachine.OpenSubKey(@"SYSTEM\CurrentControlSet\Services\" + name);
                if (key is null)
                {
                    parts.Add(name + "=missing");
                    continue;
                }
                object? start = key.GetValue("Start");
                object? type = key.GetValue("Type");
                object? imagePath = key.GetValue("ImagePath");
                parts.Add($"{name}=present;Start={start ?? "unknown"};Type={type ?? "unknown"};ImagePath={imagePath ?? "unknown"}");
            }
            return string.Join(",", parts);
        }
        catch (Exception ex)
        {
            return "check-error:" + ex.GetType().Name;
        }
    }

    private static string PawnIoDriverFilesStatus()
    {
        try
        {
            string systemDrivers = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "System32", "drivers");
            string[] names = { "PawnIO.sys", "PawnIo.sys", "WinRing0x64.sys", "WinRing0.sys" };
            List<string> parts = new();
            foreach (string name in names)
            {
                bool besideHelper = File.Exists(Path.Combine(AppContext.BaseDirectory, name));
                bool inDrivers = File.Exists(Path.Combine(systemDrivers, name));
                parts.Add($"{name}:helper={besideHelper};system32drivers={inDrivers}");
            }
            return string.Join(",", parts);
        }
        catch (Exception ex)
        {
            return "check-error:" + ex.GetType().Name;
        }
    }

    private static string ValueText(float? value) => value.HasValue ? value.Value.ToString(CultureInfo.InvariantCulture) : "null";

    private static void WriteJson(Result result)
    {
        Console.WriteLine(JsonSerializer.Serialize(result, JsonOptions));
        Console.Out.Flush();
    }
}
