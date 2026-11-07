using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

class Program {
  [DllImport("setupapi.dll", CharSet=CharSet.Unicode)]
  static extern int CM_Get_Device_Interface_List_SizeW(out int len, ref Guid g, string inst, int flags);
  [DllImport("setupapi.dll", CharSet=CharSet.Unicode)]
  static extern int CM_Get_Device_Interface_ListW(ref Guid g, string inst, [Out] char[] buf, int len, int flags);
  [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)]
  static extern IntPtr CreateFileW(string p, uint acc, uint sh, IntPtr sa, uint cd, uint fl, IntPtr t);
  [DllImport("winusb.dll", SetLastError=true)]
  static extern bool WinUsb_Initialize(IntPtr dev, out IntPtr iface);
  [DllImport("winusb.dll", SetLastError=true)]
  static extern bool WinUsb_Free(IntPtr iface);
  [DllImport("winusb.dll", SetLastError=true)]
  static extern bool WinUsb_QueryInterfaceSettings(IntPtr iface, byte alt, out IFD d);
  [DllImport("winusb.dll", SetLastError=true)]
  static extern bool WinUsb_QueryPipe(IntPtr iface, byte alt, byte idx, out PIPEINFO p);
  [DllImport("winusb.dll", SetLastError=true)]
  static extern bool WinUsb_WritePipe(IntPtr iface, byte pipeId, byte[] buf, uint len, out uint sent, IntPtr ov);
  [DllImport("winusb.dll", SetLastError=true)]
  static extern bool WinUsb_ReadPipe(IntPtr iface, byte pipeId, byte[] buf, uint len, out uint read, IntPtr ov);
  [DllImport("winusb.dll", SetLastError=true)]
  static extern bool WinUsb_SetPipePolicy(IntPtr iface, byte pipeId, uint policy, uint valLen, ref uint val);

  [StructLayout(LayoutKind.Sequential, Pack=1)]
  struct IFD { public byte bLength, bDescriptorType, bInterfaceNumber, bAlternateSetting, bNumEndpoints, bInterfaceClass, bInterfaceSubClass, bInterfaceProtocol, iInterface; }
  [StructLayout(LayoutKind.Sequential, Pack=1)]
  struct PIPEINFO { public uint PipeType; public byte PipeId; public ushort MaximumPacketSize; public byte Interval; }

  static IntPtr iface;
  static byte epIn = 0x81, epOut = 0x02;
  static int maxPkt = 512;

  static List<string> L(string gs) {
    var r = new List<string>(); var g = new Guid(gs); int len;
    if (CM_Get_Device_Interface_List_SizeW(out len, ref g, null, 1) != 0) return r;
    var b = new char[len]; g = new Guid(gs);
    if (CM_Get_Device_Interface_ListW(ref g, null, b, len, 1) != 0) return r;
    foreach (var p in new string(b).TrimEnd((char)0).Split((char)0)) if (p.Length > 3) r.Add(p);
    return r;
  }

  static bool Open() {
    string path = null;
    foreach (var p in L("a5dcbf10-6530-11d2-901f-00c04fb951ed")) if (p.ToUpper().Contains("18D1")) path = p;
    if (path == null) { Console.WriteLine("NO DEVICE"); return false; }
    IntPtr h = CreateFileW(path, 0xC0000000, 3, IntPtr.Zero, 3, 0x40000000, IntPtr.Zero);
    if (h == IntPtr.Zero || h == new IntPtr(-1)) { Console.WriteLine("OPEN FAIL " + Marshal.GetLastWin32Error()); return false; }
    if (!WinUsb_Initialize(h, out iface)) { Console.WriteLine("WINUSB FAIL " + Marshal.GetLastWin32Error()); return false; }
    IFD d;
    if (!WinUsb_QueryInterfaceSettings(iface, 0, out d)) { Console.WriteLine("QIS FAIL"); return false; }
    for (byte i = 0; i < d.bNumEndpoints; i++) {
      PIPEINFO pi;
      if (WinUsb_QueryPipe(iface, 0, i, out pi)) {
        if ((pi.PipeId & 0x80) != 0) { epIn = pi.PipeId; if (pi.MaximumPacketSize > 0) maxPkt = pi.MaximumPacketSize; }
        else epOut = pi.PipeId;
      }
    }
    uint to = 20000;
    WinUsb_SetPipePolicy(iface, epIn, 3, 4, ref to);
    WinUsb_SetPipePolicy(iface, epOut, 3, 4, ref to);
    return true;
  }

  static string ReadResp(int maxMsgs) {
    var sb = new StringBuilder();
    byte[] buf = new byte[65536];
    for (int i = 0; i < maxMsgs; i++) {
      uint read;
      if (!WinUsb_ReadPipe(iface, epIn, buf, (uint)buf.Length, out read, IntPtr.Zero)) break;
      if (read == 0) break;
      string s = Encoding.ASCII.GetString(buf, 0, (int)read);
      sb.Append(s);
      if (s.StartsWith("OKAY") || s.StartsWith("FAIL") || s.StartsWith("DATA")) break;
    }
    return sb.ToString();
  }

  static bool Send(string s) {
    byte[] b = Encoding.ASCII.GetBytes(s);
    uint sent;
    if (!WinUsb_WritePipe(iface, epOut, b, (uint)b.Length, out sent, IntPtr.Zero)) {
      Console.WriteLine("WRITE FAIL err=" + Marshal.GetLastWin32Error());
      return false;
    }
    return true;
  }

  static string Show(string s) { return s.Replace("\r", "\\r").Replace("\n", "\\n"); }

  static int Main(string[] args) {
    bool isFlash = args.Length > 0 && args[0] == "flash";
    if (!Open()) return 1;

    if (isFlash) {
      if (args.Length < 3) { Console.WriteLine("usage: flash <partition> <file>"); WinUsb_Free(iface); return 1; }
      string part = args[1], file = args[2];
      long size = new FileInfo(file).Length;
      if (!Send("download:" + size.ToString("x8"))) { WinUsb_Free(iface); return 1; }
      string r = ReadResp(4);
      Console.WriteLine("RESP1: " + Show(r));
      if (!r.StartsWith("DATA")) { Console.WriteLine("NOT DATA PHASE"); WinUsb_Free(iface); return 1; }
      int want = int.Parse(r.Substring(4, 8), System.Globalization.NumberStyles.HexNumber);
      Console.WriteLine("device wants " + want + " bytes, file has " + size);
      if (want != size) { Console.WriteLine("SIZE MISMATCH"); WinUsb_Free(iface); return 1; }

      byte[] data = File.ReadAllBytes(file);
      int chunkSize = maxPkt * 128;
      int total = 0;
      var sw = System.Diagnostics.Stopwatch.StartNew();
      while (total < data.Length) {
        int chunk = Math.Min(chunkSize, data.Length - total);
        byte[] piece = new byte[chunk];
        Array.Copy(data, total, piece, 0, chunk);
        uint sent;
        if (!WinUsb_WritePipe(iface, epOut, piece, (uint)chunk, out sent, IntPtr.Zero)) {
          Console.WriteLine("BULK WRITE FAIL at " + total + " err=" + Marshal.GetLastWin32Error());
          WinUsb_Free(iface);
          return 1;
        }
        total += (int)sent;
        if (total % (chunkSize * 40) < chunkSize && total > 0) {
          double mb = total / 1048576.0;
          Console.WriteLine("  " + mb.ToString("F1") + " MB  " + (mb / sw.Elapsed.TotalSeconds).ToString("F2") + " MB/s");
        }
      }
      Console.WriteLine("SENT " + total + " bytes in " + sw.Elapsed.TotalSeconds.ToString("F1") + "s (" + (total/1048576.0/sw.Elapsed.TotalSeconds).ToString("F2") + " MB/s)");
      r = ReadResp(4);
      Console.WriteLine("RESP2: " + Show(r));

      if (!Send("flash:" + part)) { WinUsb_Free(iface); return 1; }
      r = ReadResp(60);
      Console.WriteLine("RESP3: " + Show(r));
      WinUsb_Free(iface);
      return 0;
    }

    string cmd = string.Join(" ", args);
    if (!Send(cmd)) { WinUsb_Free(iface); return 1; }
    string resp = ReadResp(200);
    Console.WriteLine(Show(resp));
    WinUsb_Free(iface);
    return 0;
  }
}
