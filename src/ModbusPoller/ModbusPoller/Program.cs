using Microsoft.Extensions.Configuration;
using ModbusPoller.Detection;
using NModbus;
using System.Net.Sockets;

namespace ModbusPoller
{
    internal class Program
    {
        static async Task Main(string[] args)
        {
            var config = new ConfigurationBuilder()
                .SetBasePath(AppContext.BaseDirectory)
                .AddJsonFile("appsettings.json")
                .Build();
            var detection = config.GetSection("Detection").Get<DetectionConfig>();
            var pollGuard = new PollGuard(detection.PollGuard);
            var productDetector = new CusumDetector(detection.Streams["Product"]);
            var purgeDetector = new CusumDetector(detection.Streams["Purge"]);


            if (args.Length > 0 && args[0] == "replay")
            {
                Replay.Run(detection, "data/clean_export.csv", "data/cs_trace.csv");
                return;
            }

            if (args.Length > 0 && args[0] == "guardreplay")
            {
                GuardReplay.Run(detection, "data/readings.csv", "data/cs_clean_ts.csv");
                return;
            }
            string runId = args.Length > 0 ? args[0] : "unnamed_run";
            string target = args.Length > 1 ? args[1] : "attack-proxy";
            var poll = new Helper(pollGuard, productDetector, purgeDetector, runId, target);
            await poll.Connect();   
        }

    }
}
