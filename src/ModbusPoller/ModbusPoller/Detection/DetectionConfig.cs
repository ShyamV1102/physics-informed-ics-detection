using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Threading.Tasks;

namespace ModbusPoller.Detection
{
    public class DetectionConfig
    {
        public Dictionary<string, StreamConfig> Streams { get; set; } = new();
        public PollGuardConfig PollGuard { get; set; } = new();
    }
    public class PollGuardConfig
    {
        public double NominalDtSeconds { get; set; }
        public double DtMultiplier { get; set; }
        public int RecoverySamples { get; set; }
    }
    public class StreamConfig
    {
        public double M { get; set; }
        public double C { get; set; }
        public double Mu { get; set; }
        public double Sigma { get; set; }
        public double Slack { get; set; }
        public double H { get; set; }
    }
}
