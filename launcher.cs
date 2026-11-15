// Lumina Lite.exe: starts the app with the bundled Python, without a console.
// Built by install.ps1 with the C# compiler that ships with Windows (.NET Framework 4).
using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

static class Launcher
{
    [STAThread]
    static int Main(string[] args)
    {
        string dir = AppDomain.CurrentDomain.BaseDirectory;
        string pythonw = Path.Combine(dir, @"venv\Scripts\pythonw.exe");
        string app = Path.Combine(dir, "app.py");
        if (!File.Exists(pythonw) || !File.Exists(app))
        {
            MessageBox.Show("Lumina Lite isn't fully installed in\n" + dir +
                            "\n\nRun install.ps1 again.", "Lumina Lite",
                            MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
        string extra = string.Join(" ", args);
        var psi = new ProcessStartInfo(pythonw, "\"" + app + "\" " + extra)
        {
            WorkingDirectory = dir,
            UseShellExecute = false,
        };
        Process.Start(psi);
        return 0;
    }
}
