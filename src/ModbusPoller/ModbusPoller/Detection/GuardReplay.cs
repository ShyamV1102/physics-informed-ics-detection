using System.Globalization;
using System.Linq;

namespace ModbusPoller.Detection;

public static class GuardReplay
{
    public static void Run(DetectionConfig cfg, string inPath, string outPath)
    {
        var guard = new PollGuard(cfg.PollGuard);

        using var reader = new StreamReader(inPath);
        using var writer = new StreamWriter(outPath);
        writer.WriteLine("timestamp");

        var header = reader.ReadLine().Split(',').Select(h => h.Trim()).ToList();
        int tsi = header.IndexOf("timestamp");

        string line;
        while ((line = reader.ReadLine()) != null)
        {
            var f = line.Split(',').Select(x => x.Trim()).ToArray();
            DateTime ts = DateTime.Parse(f[tsi], CultureInfo.InvariantCulture,
                DateTimeStyles.RoundtripKind);

            if (guard.ShouldUpdate(ts))
                writer.WriteLine(f[tsi]);
        }
    }
}