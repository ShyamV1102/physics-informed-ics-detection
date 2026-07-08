using NModbus;
using System.Net.Sockets;

namespace ModbusPoller
{
    internal class Program
    {
        static async Task Main(string[] args)
        {
            var poll = new Helper();
            await poll.Connect();
        }

    }
}
