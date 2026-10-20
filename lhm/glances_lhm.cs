using System;
using System.Globalization;
using LibreHardwareMonitor.Hardware;

// 基于 beszel/agent/lhm/beszel_lhm.cs 改造（大屏项目自有版，协议向后兼容）：
//   getTemps   → 每行 "name|value"（仅温度，旧协议，兼容原解析）
//   getSensors → 每行 "T|name|value" | "F|name|RPM" | "P|name|Watt"（温度+风扇+功耗），空行结束
class Program
{
  static void Main()
  {
    var computer = new Computer
    {
      IsCpuEnabled = true,
      IsGpuEnabled = true,
      IsMemoryEnabled = true,
      IsMotherboardEnabled = true,
      IsStorageEnabled = true,
    };
    computer.Open();

    var reader = Console.In;
    var writer = Console.Out;

    string line;
    while ((line = reader.ReadLine()) != null)
    {
      var cmd = line.Trim();
      if (cmd.Equals("getTemps", StringComparison.OrdinalIgnoreCase))
      {
        foreach (var hw in computer.Hardware)
        {
          ProcessSensors(hw, writer, false);
          foreach (var sub in hw.SubHardware)
            ProcessSensors(sub, writer, false);
        }
        writer.WriteLine();
        writer.Flush();
      }
      else if (cmd.Equals("getSensors", StringComparison.OrdinalIgnoreCase))
      {
        foreach (var hw in computer.Hardware)
        {
          ProcessSensors(hw, writer, true);
          foreach (var sub in hw.SubHardware)
            ProcessSensors(sub, writer, true);
        }
        writer.WriteLine();
        writer.Flush();
      }
    }
    computer.Close();
  }

  static void ProcessSensors(IHardware hardware, System.IO.TextWriter writer, bool all)
  {
    var updated = false;
    foreach (var sensor in hardware.Sensors)
    {
      var isTemp = sensor.SensorType == SensorType.Temperature;
      var isFan = all && sensor.SensorType == SensorType.Fan;
      var isPower = all && sensor.SensorType == SensorType.Power;
      if ((!isTemp && !isFan && !isPower) || !sensor.Value.HasValue)
        continue;
      if (isTemp &&
          (sensor.Name.IndexOf("Distance", StringComparison.OrdinalIgnoreCase) >= 0 ||
           sensor.Name.IndexOf("Limit", StringComparison.OrdinalIgnoreCase) >= 0 ||
           sensor.Name.IndexOf("Critical", StringComparison.OrdinalIgnoreCase) >= 0 ||
           sensor.Name.IndexOf("Warning", StringComparison.OrdinalIgnoreCase) >= 0 ||
           sensor.Name.IndexOf("Resolution", StringComparison.OrdinalIgnoreCase) >= 0))
        continue;
      if (isPower &&
          (sensor.Name.IndexOf("Differential", StringComparison.OrdinalIgnoreCase) >= 0))
        continue;

      if (!updated)
      {
        hardware.Update();
        updated = true;
      }

      var name = sensor.Name;
      if (sensor.Name.StartsWith("Temperature"))
        name = hardware.Identifier.ToString().Replace("/", "_").TrimStart('_') + sensor.Name.Substring(11);

      var prefix = all ? (isFan ? "F" : isPower ? "P" : "T") + "|" : "";
      var value = sensor.Value.Value.ToString("0.##", CultureInfo.InvariantCulture);
      writer.WriteLine(prefix + name + "|" + value);
    }
  }
}
