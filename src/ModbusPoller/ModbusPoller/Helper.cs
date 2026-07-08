using NModbus;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Net.Sockets;
using System.Text;
using System.Threading.Tasks;

namespace ModbusPoller
{
    public class Helper
    {
        private readonly string _ip = "192.168.95.2";
        private readonly int _port = 502;
        public async Task Connect()
        {
            ModbusFactory factory = new ModbusFactory();
            try
            {
                int count = 0;
                using (TcpClient tcpClient = new TcpClient(_ip, _port))
                {
                    Console.WriteLine("Before creating master");
                    using (IModbusMaster master = factory.CreateMaster(tcpClient))
                    {
                        Console.WriteLine("master created");
                        master.Transport.ReadTimeout = 1000000;
                        master.Transport.WriteTimeout = 1000000;
                        if (tcpClient.Connected)
                        {
                            
                            var value = await master.ReadInputRegistersAsync(Convert.ToByte(0), ModbusRegisterMap.F1ValvePosReg, 13);
                            foreach(var item in value)
                            {

                                Console.WriteLine($" value: {item}");
                                if(item != 0)
                                {
                                    count++;
                                }
                            }
                        }
                    }
                    Console.WriteLine($"After. Count: {count}");
                }
            }
            catch (Exception ex)
            {
                Console.WriteLine(ex.StackTrace);
                throw;
            }

        }
    }
}
