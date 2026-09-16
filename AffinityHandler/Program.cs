// affinity-on-linux.exe -- open documents in Affinity from a Linux file manager.
//
// Replaces the affinity-open shell script. Set it as the .desktop Exec target:
//
//     Exec=<wine> "C:\Program Files\Affinity\Affinity\affinity-on-linux.exe" %F
//
// It runs inside the prefix, so it needs no external tools -- no wmctrl, no
// xdotool, no /proc scanning, and no shell quoting to get wrong.
//
// WHAT IT DOES
//
//   * converts the Unix paths the file manager hands it into DOS paths
//     (kernel32!wine_get_dos_file_name -- Wine converts argv[0] but passes
//     arguments through verbatim, so this is genuinely required)
//   * serialises concurrent double-clicks on a named mutex, so two fast clicks
//     cannot both decide "not running" and both cold start
//   * warm (an instance is up): hands each document straight to Affinity.exe
//     and lets Affinity's own single-instance handoff forward it
//   * cold: goes through AffinityHook.exe when it is installed, so plugins load,
//     and watches for the intermittent startup deadlock
//
// WHY WARM SKIPS AffinityHook.exe
//
// AffinityHook's job is to inject AffinityPluginLoader.dll into the process it
// starts. On a warm handoff that process forwards the document to the running
// instance and exits in about a second, so injecting into it achieves nothing.
// Skipping it is a second faster and -- because the hook forwards its arguments
// with string.Join(" ", args) and no quoting -- avoids splitting any path that
// contains a space. See TWO-STAGE COLD START below for the cold equivalent.
//
// WHY IT DOES NOT RAISE THE WINDOW
//
// It used to have to. Affinity's own SetForegroundWindow was failing, so the
// script called `wmctrl -i -a`. The wineserver was denying the request -- to
// every Wine process, including Affinity raising its own window -- whenever the
// X focus sat on a window that does not belong to Wine. Fixed by
// patches/wine/0019; see docs/wine-bug-foreground-denied.md. THIS PROGRAM
// REQUIRES THAT PATCH. Without it the document still opens, but the window
// stays behind whatever the file manager is.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

namespace AffinityOnLinux
{
    internal static class Program
    {
        private const string MutexName = "Global\\AffinityOnLinux.launch";
        private const int StallSeconds = 45;   // matches affinity-launch
        private const int MaxTries = 3;

        private static StreamWriter _log;

        private static int Main(string[] args)
        {
            OpenLog();
            Log("invoked: " + string.Join(" | ", args));

            // Serialise. Two quick double-clicks used to both see "not running"
            // and both cold start.
            using (var mutex = new Mutex(false, MutexName))
            {
                bool held = false;
                try
                {
                    try { held = mutex.WaitOne(TimeSpan.FromSeconds(90)); }
                    catch (AbandonedMutexException) { held = true; }
                    return Run(args);
                }
                finally
                {
                    if (held) { try { mutex.ReleaseMutex(); } catch { } }
                    CloseLog();
                }
            }
        }

        private static int Run(string[] args)
        {
            string dir = FindAffinityDirectory();
            if (dir == null)
            {
                Fail("Could not find Affinity.exe");
                return 1;
            }

            string affinity = Path.Combine(dir, "Affinity.exe");
            string hook = Path.Combine(dir, "AffinityHook.exe");
            bool hasHook = File.Exists(hook);
            string launcher = hasHook ? hook : affinity;

            Log("affinity=" + affinity + " hook=" + (hasHook ? hook : "(not installed)"));

            var docs = new List<string>();
            int failed = 0;
            foreach (string a in args)
            {
                string dos = ToDosPath(a);
                if (dos == null) { Note("Cannot map to a Windows path: " + a); failed = 1; continue; }
                if (!File.Exists(dos)) { Note("No such file: " + a); failed = 1; continue; }
                docs.Add(dos);
            }
            if (args.Length > 0 && docs.Count == 0) return failed;

            if (IsAffinityRunning())
            {
                // Warm. Straight to Affinity.exe -- see the header. Affinity's
                // singleton forwards the document and brings itself forward.
                if (docs.Count == 0)
                {
                    Log("warm: bare launch, nudging the running instance");
                    StartAndWait(affinity, "", 20);
                }
                else
                {
                    foreach (string d in docs)
                    {
                        Log("warm handoff: " + d);
                        if (StartAndWait(affinity, Quote(d), 30) != 0)
                        {
                            Note("Affinity refused " + Path.GetFileName(d));
                            failed = 1;
                        }
                    }
                }
                return failed;
            }

            return ColdStart(launcher, affinity, hasHook, docs) ? failed : 1;
        }

        // ---------------------------------------------------------------- cold

        // TWO-STAGE COLD START
        //
        // A cold start goes through AffinityHook.exe so plugins load, and the
        // hook forwards its arguments with string.Join(" ", args). A path
        // containing a space therefore arrives at Affinity split into fragments,
        // one "failed to open" dialog each.
        //
        // Rather than depend on a patched hook -- or pre-quote to match the
        // unpatched one, which then breaks against a patched one -- side-step
        // the hook's argument handling entirely for those paths: start the hook
        // with no document at all, wait until the window is up, and then hand
        // the document over exactly as a warm handoff would. That behaves the
        // same against a vanilla AffinityPluginLoader and a patched one.
        //
        // 8.3 short paths were tried first and rejected: they open the right
        // file, but Affinity keeps the short name as the document's identity
        // (the lock sidecar comes out as BRAN~ECI.AF~lock~), so the tab and
        // Recent Documents would show it too.
        private static bool ColdStart(string launcher, string affinity, bool viaHook, List<string> docs)
        {
            bool needsTwoStage = viaHook && docs.Exists(NeedsQuoting);
            string arguments = needsTwoStage ? "" : JoinQuoted(docs);

            // Husks from a previous close still own the single-instance
            // registration. Left alone, the two-stage handoff below hands the
            // document to one of them and it is silently dropped.
            List<Process> husks = FindHusks();
            if (husks.Count > 0)
            {
                Log("clearing " + husks.Count + " husk process(es) left by a previous close");
                foreach (Process h in husks)
                    try { h.Kill(); } catch { }
                Thread.Sleep(1500);
            }

            Log("cold start: launcher=" + Path.GetFileName(launcher)
                + " twoStage=" + needsTwoStage + " args=[" + arguments + "]");

            for (int attempt = 1; attempt <= MaxTries; attempt++)
            {
                Process child = Start(launcher, arguments);
                if (child == null) { Note("could not start " + launcher); return false; }

                int outcome = WaitForStartup(child);
                if (outcome == 1)
                {
                    if (attempt > 1) Note("started on attempt " + attempt);
                    if (needsTwoStage)
                        foreach (string d in docs)
                        {
                            Log("cold two-stage handoff: " + d);
                            StartAndWait(affinity, Quote(d), 30);
                        }
                    return true;
                }

                Note(outcome == 2
                    ? "exited during startup (attempt " + attempt + "/" + MaxTries + ") -- retrying"
                    : "startup stalled (attempt " + attempt + "/" + MaxTries + ") -- retrying");
                KillAffinity(child);
            }

            Note("failed to start after " + MaxTries + " attempts; launching without the watchdog");
            Start(launcher, arguments);
            return true;
        }

        // 1 = a window came up, 2 = the child exited first, 0 = stalled.
        private static int WaitForStartup(Process child)
        {
            for (int i = 0; i < StallSeconds; i++)
            {
                Thread.Sleep(1000);
                if (FindAffinityWindow() != IntPtr.Zero) return 1;
                try { if (child.HasExited) return 2; } catch { return 2; }
            }
            return 0;
        }

        // Only ever this prefix's processes: inside Wine, Process.GetProcesses()
        // cannot see anything else. That is also why this program can never fall
        // into the `pgrep -f Affinity.exe matches my own shell` trap.
        private static void KillAffinity(Process child)
        {
            try { if (!child.HasExited) child.Kill(); } catch { }
            foreach (string name in new[] { "Affinity", "AffinityHook" })
                foreach (Process p in SafeGetProcesses(name))
                    try { p.Kill(); } catch { }
            Thread.Sleep(2000);
        }

        // -------------------------------------------------------------- helpers

        // A window means a running instance, no argument. Without one, processes
        // alone are ambiguous: Affinity is either on its way up or on its way
        // down, and the two need opposite answers.
        //
        // Closing Affinity leaves husks. Measured while watching a close:
        // 152 threads and a window, then moments later processes at three
        // threads, no window, no document -- and they linger. Treating those as
        // "running" sends the document to a dying process, which discards it,
        // and the forward then starts an instance of its own WITHOUT the
        // document. That is the blank window with no tab.
        //
        // Thread count alone will not separate them. A live instance runs at
        // 150+ and one still starting passes fifty within seconds, but a
        // closing one descends through every value on its way down -- caught at
        // 17 once. So a count is only ever used as proof of life, never as
        // proof of death.
        //
        // The asymmetry decides the rest. Mistaking a husk for a live instance
        // costs an unopened document; mistaking a live instance for a husk
        // kills the user's session and whatever was unsaved in it. So a
        // process is only ever declared dead after it has been given the whole
        // startup window to produce one, which anything genuinely starting
        // will.
        private const int LiveThreadFloor = 40;

        private static bool AnyLiveByThreads()
        {
            foreach (Process p in SafeGetProcesses("Affinity"))
                if (ThreadCount(p) >= LiveThreadFloor) return true;
            return false;
        }

        private static bool AnyAffinityProcess()
        {
            foreach (string name in new[] { "Affinity", "AffinityHook" })
                if (SafeGetProcesses(name).Length > 0) return true;
            return false;
        }

        private static bool IsAffinityRunning()
        {
            if (FindAffinityWindow() != IntPtr.Zero) return true;
            if (AnyLiveByThreads()) return true;

            // Processes but no window and no thread count to vouch for them.
            // Either a start in progress -- the hook exists for ~30s before
            // Affinity.exe does, and missing it there is what cold starts a
            // rival on a second double-click -- or husks from a close.
            if (!AnyAffinityProcess()) return false;

            // Age settles it without waiting. A start that has not produced a
            // window yet is seconds old; a husk has been alive since Affinity
            // was started, which is however long the session lasted. Waiting out
            // the startup window instead is correct but costs 46 seconds on
            // every close-then-click, which reads as nothing happening at all.
            if (!AnyStartedRecently())
            {
                Log("processes are older than a startup and have no window; husks");
                return false;
            }

            Log("recently started processes with no window; waiting to see whether one is starting");
            for (int i = 0; i < StallSeconds; i++)
            {
                Thread.Sleep(1000);
                if (FindAffinityWindow() != IntPtr.Zero) return true;
                if (AnyLiveByThreads()) return true;
                if (!AnyAffinityProcess()) return false;   // husks reaped themselves
                if (!AnyStartedRecently())
                {
                    Log("no window within a startup; husks");
                    return false;
                }
            }
            Log("no window appeared; treating the remaining processes as husks");
            return false;
        }

        // Long enough for a genuine start to have put a window up. The hook
        // exists for about thirty seconds before Affinity.exe does, so this is
        // measured from the oldest of them and kept generous -- being wrong the
        // other way kills a live session.
        private const int StartupGraceSeconds = 75;

        private static bool AnyStartedRecently()
        {
            foreach (string name in new[] { "Affinity", "AffinityHook" })
                foreach (Process p in SafeGetProcesses(name))
                {
                    try
                    {
                        if ((DateTime.Now - p.StartTime).TotalSeconds < StartupGraceSeconds)
                            return true;
                    }
                    catch
                    {
                        // No start time: say it might be starting. An unopened
                        // document beats killing something that is alive.
                        return true;
                    }
                }
            return false;
        }

        // Husks left behind by a close. They hold Affinity's single-instance
        // registration, so a handoff reaches them instead of a live instance.
        // Only ever called after IsAffinityRunning() has already waited out the
        // startup window and concluded there is nothing alive here.
        private static List<Process> FindHusks()
        {
            var husks = new List<Process>();
            if (FindAffinityWindow() != IntPtr.Zero) return husks;
            if (AnyLiveByThreads()) return husks;
            foreach (string name in new[] { "Affinity", "AffinityHook" })
                husks.AddRange(SafeGetProcesses(name));
            return husks;
        }

        private static int ThreadCount(Process p)
        {
            try { return p.Threads.Count; } catch { return 0; }
        }

        private static Process[] SafeGetProcesses(string name)
        {
            try { return Process.GetProcessesByName(name); }
            catch { return new Process[0]; }
        }

        private static string FindAffinityDirectory()
        {
            string here = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
            if (here != null && File.Exists(Path.Combine(here, "Affinity.exe"))) return here;

            string standard = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
                "Affinity", "Affinity");
            return File.Exists(Path.Combine(standard, "Affinity.exe")) ? standard : null;
        }

        private static Process Start(string exe, string arguments)
        {
            var psi = new ProcessStartInfo
            {
                FileName = exe,
                Arguments = arguments,
                WorkingDirectory = Path.GetDirectoryName(exe),
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            // Belt and braces: the prefix registry already carries opencl="".
            // With OpenCL live on a real GPU the startup deadlock is ~100%
            // instead of ~33%.
            psi.EnvironmentVariables["WINEDLLOVERRIDES"] = "opencl=d";
            try { return Process.Start(psi); }
            catch (Exception ex) { Log("start failed: " + ex.Message); return null; }
        }

        private static int StartAndWait(string exe, string arguments, int seconds)
        {
            Process p = Start(exe, arguments);
            if (p == null) return -1;
            try { return p.WaitForExit(seconds * 1000) ? p.ExitCode : 0; }
            catch { return 0; }
        }

        // ------------------------------------------------------------ path work

        [DllImport("kernel32.dll", EntryPoint = "wine_get_dos_file_name",
                   CallingConvention = CallingConvention.Cdecl)]
        private static extern IntPtr WineGetDosFileName(IntPtr utf8Path);

        // Wine converts argv[0] to a DOS path but passes arguments through
        // verbatim, so a file manager's /mnt/work/x.afphoto arrives unchanged.
        // wine_get_dos_file_name takes CP_UNIXCP (UTF-8 here), so marshal the
        // bytes rather than letting the runtime use the ANSI code page.
        private static string ToDosPath(string path)
        {
            if (string.IsNullOrEmpty(path)) return null;
            if (!path.StartsWith("/")) return path;      // already a Windows path

            byte[] utf8 = Encoding.UTF8.GetBytes(path);
            IntPtr buf = Marshal.AllocHGlobal(utf8.Length + 1);
            try
            {
                Marshal.Copy(utf8, 0, buf, utf8.Length);
                Marshal.WriteByte(buf, utf8.Length, 0);
                IntPtr result = WineGetDosFileName(buf);
                return result == IntPtr.Zero ? null : Marshal.PtrToStringUni(result);
            }
            catch (EntryPointNotFoundException) { return path; }   // not Wine
            catch (DllNotFoundException) { return path; }
            finally { Marshal.FreeHGlobal(buf); }
        }

        private static readonly char[] ArgumentDelimiters = { ' ', '\t', '\n', '\v', '"' };

        private static bool NeedsQuoting(string s)
        {
            return s.Length == 0 || s.IndexOfAny(ArgumentDelimiters) >= 0;
        }

        private static string JoinQuoted(List<string> items)
        {
            var sb = new StringBuilder();
            foreach (string s in items)
            {
                if (sb.Length > 0) sb.Append(' ');
                sb.Append(Quote(s));
            }
            return sb.ToString();
        }

        // ProcessStartInfo.Arguments is handed to CreateProcess and re-parsed
        // with the CommandLineToArgvW rules, so quote to survive that.
        private static string Quote(string arg)
        {
            if (!NeedsQuoting(arg)) return arg;

            var sb = new StringBuilder();
            sb.Append('"');
            for (int i = 0; i < arg.Length; i++)
            {
                int slashes = 0;
                while (i < arg.Length && arg[i] == '\\') { slashes++; i++; }

                if (i == arg.Length) { sb.Append('\\', slashes * 2); break; }
                if (arg[i] == '"') { sb.Append('\\', slashes * 2 + 1); }
                else { sb.Append('\\', slashes); }
                sb.Append(arg[i]);
            }
            sb.Append('"');
            return sb.ToString();
        }

        // ------------------------------------------------------------- windows

        private delegate bool EnumWindowsProc(IntPtr hwnd, IntPtr lParam);

        [DllImport("user32.dll")] private static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lParam);
        [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr hwnd);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern int GetClassNameW(IntPtr hwnd, StringBuilder buf, int max);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern int GetWindowTextW(IntPtr hwnd, StringBuilder buf, int max);
        [DllImport("user32.dll")] private static extern bool GetWindowRect(IntPtr hwnd, out RECT r);

        [StructLayout(LayoutKind.Sequential)]
        private struct RECT { public int Left, Top, Right, Bottom; }

        // Affinity is WPF: its main window class is HwndWrapper[Affinity.exe;;<guid>].
        // Several of those exist -- the real one is visible, titled and wide.
        private static IntPtr FindAffinityWindow()
        {
            IntPtr found = IntPtr.Zero;
            var cls = new StringBuilder(256);
            var txt = new StringBuilder(256);

            EnumWindows((hwnd, lp) =>
            {
                if (!IsWindowVisible(hwnd)) return true;
                cls.Length = 0; txt.Length = 0;
                GetClassNameW(hwnd, cls, cls.Capacity);
                if (cls.ToString().IndexOf("HwndWrapper[Affinity.exe", StringComparison.Ordinal) < 0) return true;
                GetWindowTextW(hwnd, txt, txt.Capacity);
                if (txt.Length == 0) return true;
                RECT r;
                if (!GetWindowRect(hwnd, out r) || r.Right - r.Left < 600) return true;
                found = hwnd;
                return false;
            }, IntPtr.Zero);

            return found;
        }

        // ------------------------------------------------------------- logging

        private static void OpenLog()
        {
            try
            {
                string dir = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "AffinityOnLinux");
                Directory.CreateDirectory(dir);
                _log = new StreamWriter(Path.Combine(dir, "affinity-on-linux.log"), true) { AutoFlush = true };
            }
            catch { _log = null; }
        }

        private static void CloseLog()
        {
            try { if (_log != null) _log.Dispose(); } catch { }
            _log = null;
        }

        private static void Log(string message)
        {
            try { if (_log != null) _log.WriteLine(DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss ") + message); } catch { }
            Console.WriteLine(message);
        }

        private static void Note(string message)
        {
            Log("NOTE: " + message);
            Console.Error.WriteLine("affinity-on-linux: " + message);
        }

        private static void Fail(string message)
        {
            Note(message);
        }
    }
}
