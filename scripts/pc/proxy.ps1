$src = @'
using System;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;

public class MiniProxy
{
    static void Log(string s) { Console.WriteLine(DateTime.Now.ToString("HH:mm:ss") + " " + s); }

    public static void Run(string bindIp, int port)
    {
        var listener = new TcpListener(IPAddress.Parse(bindIp), port);
        listener.Start();
        Log("LISTENING " + bindIp + ":" + port);
        while (true)
        {
            TcpClient client;
            try { client = listener.AcceptTcpClient(); } catch { break; }
            var t = new Thread(() => Handle(client));
            t.IsBackground = true;
            t.Start();
        }
    }

    static void Handle(TcpClient client)
    {
        TcpClient up = null;
        try
        {
            client.NoDelay = true;
            var cs = client.GetStream();
            cs.ReadTimeout = 20000;

            var head = ReadHead(cs);
            if (string.IsNullOrEmpty(head)) { client.Close(); return; }
            var lines = head.Split(new string[] { "\r\n" }, StringSplitOptions.None);
            var first = lines[0];
            Log(first);
            var parts = first.Split(' ');
            if (parts.Length < 2) { client.Close(); return; }
            string method = parts[0], target = parts[1];

            if (method == "CONNECT")
            {
                int colon = target.LastIndexOf(':');
                string h = target.Substring(0, colon);
                int p = int.Parse(target.Substring(colon + 1));
                up = new TcpClient(); up.NoDelay = true;
                up.Connect(h, p);
                var us = up.GetStream();
                var ok = Encoding.ASCII.GetBytes("HTTP/1.1 200 Connection Established\r\n\r\n");
                cs.Write(ok, 0, ok.Length); cs.Flush();
                Pump(cs, us);
            }
            else
            {
                var uri = new Uri(target);
                int p = uri.Port > 0 ? uri.Port : 80;
                up = new TcpClient(); up.NoDelay = true;
                up.Connect(uri.Host, p);
                var us = up.GetStream();
                var sb = new StringBuilder();
                sb.Append(method).Append(' ').Append(uri.PathAndQuery).Append(" HTTP/1.1\r\n");
                for (int i = 1; i < lines.Length; i++)
                {
                    var l = lines[i];
                    if (l.Length == 0) continue;
                    var low = l.ToLowerInvariant();
                    if (low.StartsWith("proxy-connection:")) continue;
                    if (low.StartsWith("connection:")) continue;
                    sb.Append(l).Append("\r\n");
                }
                sb.Append("Connection: close\r\n\r\n");
                var bytes = Encoding.ASCII.GetBytes(sb.ToString());
                us.Write(bytes, 0, bytes.Length); us.Flush();
                Pump(us, cs);
            }
        }
        catch (Exception ex) { Log("ERR " + ex.Message); }
        finally
        {
            try { if (up != null) up.Close(); } catch { }
            try { client.Close(); } catch { }
        }
    }

    static string ReadHead(NetworkStream s)
    {
        var sb = new StringBuilder();
        var one = new byte[1];
        while (true)
        {
            int n;
            try { n = s.Read(one, 0, 1); } catch { break; }
            if (n <= 0) break;
            sb.Append((char)one[0]);
            if (sb.Length >= 4)
            {
                int L = sb.Length;
                if (sb[L-1] == '\n' && sb[L-2] == '\r' && sb[L-3] == '\n' && sb[L-4] == '\r') break;
            }
            if (sb.Length > 65536) break;
        }
        return sb.ToString();
    }

    static void Pump(NetworkStream a, NetworkStream b)
    {
        var buf = new byte[65536];
        while (true)
        {
            int n;
            try { n = a.Read(buf, 0, buf.Length); } catch { break; }
            if (n <= 0) break;
            try { b.Write(buf, 0, n); b.Flush(); } catch { break; }
        }
    }
}
'@
Add-Type -TypeDefinition $src -Language CSharp
$log = "proxy.log"
Start-Transcript -Path $log -Append | Out-Null
[MiniProxy]::Run("10.9.8.10", 3128)
