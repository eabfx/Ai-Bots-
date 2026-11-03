# Gives Kelly Terminal its own identity in the Windows taskbar, so the button, a pinned icon and the Alt+Tab list show the
# Kelly logo instead of Chrome's. Chrome does not do this for a plain "--app=" window, so this does it from outside:
#   Set-KellyWindowId  tags the open Kelly Terminal window with the identity and the icon to pin.
#   Set-ShortcutId     writes the same identity into a .lnk file, so the pinned icon and the window group together.
# It only touches the window's and the shortcut's own taskbar properties. Nothing in Chrome or Windows settings changes.
$script:Id = "KellyTerminal.App"
if (-not ("KellyId" -as [type])) {
Add-Type -TypeDefinition @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using System.Text;
public static class KellyId {
  [StructLayout(LayoutKind.Sequential, Pack=4)] public struct PROPERTYKEY { public Guid fmtid; public uint pid; }
  [StructLayout(LayoutKind.Explicit)] public struct PROPVARIANT { [FieldOffset(0)] public ushort vt; [FieldOffset(8)] public IntPtr p; [FieldOffset(16)] public IntPtr q; }
  [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  public interface IPropertyStore { int GetCount(out uint c); int GetAt(uint i, out PROPERTYKEY k); int GetValue(ref PROPERTYKEY k, out PROPVARIANT v); int SetValue(ref PROPERTYKEY k, ref PROPVARIANT v); int Commit(); }
  [ComImport, Guid("00021401-0000-0000-C000-000000000046")] public class ShellLink { }
  [DllImport("shell32.dll")] static extern int SHGetPropertyStoreForWindow(IntPtr hwnd, ref Guid riid, [MarshalAs(UnmanagedType.Interface)] out IPropertyStore ps);
  [DllImport("ole32.dll")] static extern int PropVariantClear(ref PROPVARIANT v);
  delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc cb, IntPtr l);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
  static readonly Guid APP = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3");
  static void Put(IPropertyStore ps, uint pid, string value) {
    PROPERTYKEY k = new PROPERTYKEY(); k.fmtid = APP; k.pid = pid;
    PROPVARIANT v = new PROPVARIANT(); v.vt = 31; v.p = Marshal.StringToCoTaskMemUni(value); ps.SetValue(ref k, ref v); PropVariantClear(ref v);
  }
  public static int TagWindows(string titleStart, string id, string command, string name, string icon) {
    var found = new List<IntPtr>();
    EnumWindows((h, l) => { var t = new StringBuilder(256); GetWindowText(h, t, 256); if (IsWindowVisible(h) && t.ToString().StartsWith(titleStart)) found.Add(h); return true; }, IntPtr.Zero);
    int n = 0;
    foreach (var h in found) {
      Guid iid = new Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"); IPropertyStore ps;
      if (SHGetPropertyStoreForWindow(h, ref iid, out ps) != 0) continue;
      Put(ps, 5, id); Put(ps, 2, command); Put(ps, 4, name); Put(ps, 3, icon); ps.Commit(); n++;
    }
    return n;
  }
  public static void TagShortcut(string lnk, string id) {
    var link = new ShellLink(); var file = (IPersistFile)link; file.Load(lnk, 2);
    var ps = (IPropertyStore)link; Put(ps, 5, id); ps.Commit(); file.Save(lnk, true);
  }
}
"@
}
function Set-KellyWindowId([string]$Command, [string]$Icon, [int]$Seconds = 12) {
  for ($i = 0; $i -lt ($Seconds * 2); $i++) {
    if ([KellyId]::TagWindows("Kelly", $script:Id, $Command, "Kelly Terminal", $Icon) -gt 0) { return $true }
    Start-Sleep -Milliseconds 500
  }
  return $false
}
function Set-ShortcutId([string]$Path) { [KellyId]::TagShortcut($Path, $script:Id) }
