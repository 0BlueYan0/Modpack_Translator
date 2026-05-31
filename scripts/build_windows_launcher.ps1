param(
    [string]$OutputPath = "",
    [switch]$SkipIcon
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$VersionFile = Join-Path $ProjectRoot "src\modpack_translator\version.py"
$VersionText = Get-Content $VersionFile -Raw
if ($VersionText -notmatch '__version__\s*=\s*"([^"]+)"') {
    throw "Cannot read app version from $VersionFile"
}
$AppVersion = $Matches[1]
$LauncherBaseName = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("5qih57WE5YyF57+76K2v5Zmo"))
$UsingDefaultOutput = -not $OutputPath
if (-not $OutputPath) {
    $OutputPath = Join-Path $ProjectRoot "$($LauncherBaseName)v$AppVersion.exe"
}

$PngIconPath = Join-Path $ProjectRoot "assets\icon\app_icon.png"
$IcoIconPath = Join-Path $ProjectRoot "assets\icon\app_icon.ico"

function Convert-PngToIco {
    param(
        [Parameter(Mandatory = $true)][string]$PngPath,
        [Parameter(Mandatory = $true)][string]$IcoPath
    )

    Add-Type -AssemblyName System.Drawing

    $source = [System.Drawing.Image]::FromFile($PngPath)
    $bitmap = New-Object System.Drawing.Bitmap 256, 256
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $memory = New-Object System.IO.MemoryStream
    $file = $null
    $writer = $null

    try {
        $graphics.Clear([System.Drawing.Color]::Transparent)
        $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
        $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
        $graphics.DrawImage($source, 0, 0, 256, 256)
        $bitmap.Save($memory, [System.Drawing.Imaging.ImageFormat]::Png)
        $pngBytes = $memory.ToArray()

        $file = [System.IO.File]::Create($IcoPath)
        $writer = New-Object System.IO.BinaryWriter($file)
        $writer.Write([UInt16]0)
        $writer.Write([UInt16]1)
        $writer.Write([UInt16]1)
        $writer.Write([Byte]0)
        $writer.Write([Byte]0)
        $writer.Write([Byte]0)
        $writer.Write([Byte]0)
        $writer.Write([UInt16]1)
        $writer.Write([UInt16]32)
        $writer.Write([UInt32]$pngBytes.Length)
        $writer.Write([UInt32]22)
        $writer.Write($pngBytes)
    }
    finally {
        if ($writer) { $writer.Dispose() }
        if ($file) { $file.Dispose() }
        $memory.Dispose()
        $graphics.Dispose()
        $bitmap.Dispose()
        $source.Dispose()
    }
}

if (-not $SkipIcon -and (Test-Path -LiteralPath $PngIconPath) -and -not (Test-Path -LiteralPath $IcoIconPath)) {
    Convert-PngToIco -PngPath $PngIconPath -IcoPath $IcoIconPath
}

$source = @"
using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

internal static class Program
{
    [STAThread]
    private static int Main()
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string runtime = Path.Combine(root, ".runtime");
        Directory.CreateDirectory(runtime);

        if (!CommandExists("uv"))
        {
            MessageBox.Show(
                "uv was not found. Install uv, then run setup_windows.bat before starting the app.",
                "Minecraft Modpack Translator",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error
            );
            return 1;
        }

        if (!File.Exists(Path.Combine(root, "setup_windows.bat")))
        {
            MessageBox.Show(
                "setup_windows.bat was not found. This folder is not a complete app package.",
                "Minecraft Modpack Translator",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error
            );
            return 1;
        }

        if (!Directory.Exists(Path.Combine(root, ".venv")) || !File.Exists(Path.Combine(runtime, "backend.json")))
        {
            MessageBox.Show(
                "First-time setup is required. Please run setup_windows.bat once, then open this launcher again.",
                "Minecraft Modpack Translator",
                MessageBoxButtons.OK,
                MessageBoxIcon.Information
            );
            return 1;
        }

        string logPath = Path.Combine(runtime, "launcher.log");
        string command = "uv run python main.py > " + Quote(logPath) + " 2>&1";

        ProcessStartInfo info = new ProcessStartInfo();
        info.FileName = "cmd.exe";
        info.Arguments = "/c " + command;
        info.WorkingDirectory = root;
        info.CreateNoWindow = true;
        info.UseShellExecute = false;
        Process.Start(info);
        return 0;
    }

    private static bool CommandExists(string name)
    {
        ProcessStartInfo info = new ProcessStartInfo();
        info.FileName = "cmd.exe";
        info.Arguments = "/c where " + name + " >nul 2>nul";
        info.CreateNoWindow = true;
        info.UseShellExecute = false;
        using (Process process = Process.Start(info))
        {
            process.WaitForExit();
            return process.ExitCode == 0;
        }
    }

    private static string Quote(string value)
    {
        return "\"" + value.Replace("\"", "\\\"") + "\"";
    }
}
"@

$compilerOptions = "/target:winexe /platform:anycpu"
if (-not $SkipIcon -and (Test-Path -LiteralPath $IcoIconPath)) {
    $compilerOptions += " /win32icon:`"$IcoIconPath`""
}

Add-Type -AssemblyName Microsoft.CSharp
$provider = New-Object Microsoft.CSharp.CSharpCodeProvider
$parameters = New-Object System.CodeDom.Compiler.CompilerParameters
$parameters.GenerateExecutable = $true
$parameters.GenerateInMemory = $false
$parameters.OutputAssembly = $OutputPath
$parameters.CompilerOptions = $compilerOptions
[void]$parameters.ReferencedAssemblies.Add("System.dll")
[void]$parameters.ReferencedAssemblies.Add("System.Windows.Forms.dll")
[void]$parameters.ReferencedAssemblies.Add("System.Drawing.dll")

$result = $provider.CompileAssemblyFromSource($parameters, $source)
if ($result.Errors.HasErrors) {
    $messages = @()
    foreach ($err in $result.Errors) {
        $messages += "$($err.FileName):$($err.Line):$($err.Column): $($err.ErrorText)"
    }
    throw "Launcher compile failed:`n$($messages -join "`n")"
}

if ($UsingDefaultOutput) {
    Get-ChildItem -LiteralPath $ProjectRoot -Filter "$($LauncherBaseName)v*.exe" |
        Where-Object { $_.FullName -ne (Resolve-Path $OutputPath).Path } |
        Remove-Item -Force -ErrorAction SilentlyContinue
}

Write-Host "Windows launcher built: $OutputPath"
