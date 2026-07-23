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

            var productDetector = new CusumDetector(detection.Streams["Product"]);
            var purgeDetector = new CusumDetector(detection.Streams["Purge"]);
            //var poll = new Helper();
            //await poll.Connect();

            Console.WriteLine($"sigma={detection.Streams["Product"].Sigma}, recovery={detection.PollGuard.RecoverySamples}");
        }

    }
}
