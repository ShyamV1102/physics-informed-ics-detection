using System.Globalization;
using System.Linq;

namespace ModbusPoller.Detection;

public static class Replay
{
    public static void Run(DetectionConfig cfg, string inPath, string outPath)
    {
        var product = new CusumDetector(cfg.Streams["Product"]);
        var purge = new CusumDetector(cfg.Streams["Purge"]);

        using var reader = new StreamReader(inPath);
        using var writer = new StreamWriter(outPath);
        writer.WriteLine("timestamp, cs_prod_z, cs_prod_shi, cs_prod_slo, cs_purge_z, cs_purge_shi, cs_purge_slo");

        var header = reader.ReadLine().Split(',').Select(h => h.Trim()).ToList();
        int tsi = header.IndexOf("timestamp");
        int pf = header.IndexOf("ProductFlowReg");
        int pv = header.IndexOf("ProductValvePosReg");
        int qf = header.IndexOf("PurgeFlowReg");
        int qv = header.IndexOf("PurgeValvePosReg");

        string line;
        while ((line = reader.ReadLine()) != null)
        {
            var f = line.Split(',').Select(x => x.Trim()).ToArray();
            double productFlow = double.Parse(f[pf], CultureInfo.InvariantCulture) * 500.0 / 65535.0;
            double productValve = double.Parse(f[pv], CultureInfo.InvariantCulture) * 100.0 / 65535.0;
            double purgeFlow = double.Parse(f[qf], CultureInfo.InvariantCulture) * 500.0 / 65535.0;
            double purgeValve = double.Parse(f[qv], CultureInfo.InvariantCulture) * 100.0 / 65535.0;

            var pr = product.Update(productFlow, productValve);
            var qr = purge.Update(purgeFlow, purgeValve);

            writer.WriteLine(FormattableString.Invariant(
                $"{f[tsi]}, {pr.Z}, {pr.SHigh}, {pr.SLow}, {qr.Z}, {qr.SHigh}, {qr.SLow}"));
        }
    }
}