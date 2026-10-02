# AffinityOnLinux

All Future Work is to be Halted! 
2 years ago i started my own project local Called Infinity Photo and 
progress as of now is Pretty amazing and almost on par with photoshop and Affinity it's self
while more time i needed before release to the public for linux
progress can be watched on my discord 

<img width="2560" height="1365" alt="image" src="https://github.com/user-attachments/assets/5845aaae-6cf2-427c-b44c-af3079a8f39e" />



> **⚠️ IMPORTANT: Before Opening Issues**
> 
> **DO NOT open GitHub issues until you have:**
> - Read this README completely
> - Read all documentation pages (Installation Guide, Known Issues, System Requirements, etc.)
> - Searched existing issues for similar problems
> - Checked the [Known Issues](docs/Known-issues.md) page
> 
> **Issues opened without reading the documentation will be closed immediately.**
> 
> Please take the time to read through all the documentation - it contains answers to most common questions and problems.

---

A comprehensive solution for running [Affinity software](https://www.affinity.studio/) on GNU/Linux systems using Wine with hardware acceleration support.

<img width="1277" height="1361" alt="image" src="https://github.com/user-attachments/assets/8def9258-46f4-4301-be0f-9e8ebd70bd01" />


## What is This?

AffinityOnLinux provides an easy way to install and run Affinity Photo, Designer, Publisher, and the unified Affinity v3 application on Linux. The installer automatically sets up Wine (a compatibility layer for running Windows applications) with all necessary configurations, dependencies, and optimizations.

## Quick Start

**New to Linux or want the easiest option?** Use the AppImage:

1. Download the AppImage from [GitHub Releases](https://github.com/ryzendew/AffinityOnLinux/releases/tag/Affinity-wine-10.10-Appimage)
2. Make it executable: `chmod +x Affinity-3-x86_64.AppImage`
3. Run it: `./Affinity-3-x86_64.AppImage`

**Want full features and latest updates?** Use the Python GUI Installer:

```bash
curl -sSL https://raw.githubusercontent.com/ryzendew/AffinityOnLinux/refs/heads/main/AffinityScripts/AffinityLinuxInstaller.py | python3
```

> **Testing this branch?** `main` does not have any of it. Install from a
> **clone**, not from a pipe:
>
> ```bash
> git clone -b experimental/affinity-3.3 https://forgejo.facemyer.net/facemyer/AffinityOnLinux.git
> python3 AffinityOnLinux/AffinityScripts/AffinityLinuxInstaller.py
> ```
>
> The pipe used to be the instruction here and it named an older branch, whose
> `affinity-on-linux.exe` is eight commits behind this one — including *"stop
> shipping the watchdog that kills sessions"*. The installer now pins that
> binary by SHA-256 and declines to install a different one, so a piped install
> sets up everything except file-manager integration and says why. A clone has
> the file beside it and needs no download at all.
>
> The temporary mirror URLs are listed, with what each must become before
> merging, in [`AffinityHandler/README.md`](AffinityHandler/README.md#temporary-download-sources--must-be-repointed-before-merging).

> **Several prefixes to keep track of?** [`AffinityManager/`](AffinityManager/README.md)
> is optional and changes nothing about the above. The installer stays a single
> file you run with `curl … | python3`; the manager is a separate window that
> tracks a set of prefixes, hosts this installer as one of its pages, keeps two
> of them from provisioning at the same time, and backs prefixes up and
> restores them. It needs PyQt6, which the installer needs anyway.
>
> Run it straight from the repository, the same way as the installer:
>
> ```bash
> curl -sSL https://forgejo.facemyer.net/facemyer/AffinityOnLinux/raw/branch/experimental/affinity-3.3/AffinityManager/run.py | python3
> ```
>
> That pipes a single small file, `run.py`, which downloads this branch, keeps
> it in `~/.cache/AffinityOnLinux/`, and starts the manager from it — fetching
> again on every run, so it is always the latest. It installs nothing. From a
> clone instead: `python3 AffinityManager/AffinityLinuxManager.py`. Details are
> in the [manager's README](AffinityManager/README.md#running-it).

<details>
<summary><strong>Python GUI Dependencies</strong></summary>

The installer will attempt to install PyQt6 automatically if missing. If automatic installation fails, install it manually:

**Arch/Artix/CachyOS/EndeavourOS/XeroLinux:**
```bash
sudo pacman -S python-pyqt6
```

**Fedora/Nobara:**
```bash
sudo dnf install python3-pyqt6 python3-pyqt6-svg
```

**openSUSE (Tumbleweed/Leap):**
```bash
sudo zypper install python313-PyQt6
```

**PikaOS:** 
```bash
sudo apt install python3-pyqt6.qtsvg
```

**Ubuntu 25.10:** If you encounter GUI issues, also install:
```bash
sudo apt install python3-pyqt6.qtsvg
```

</details>

## Documentation

### Getting Started
- **[Installation Guide](docs/INSTALLATION.md)** - Complete installation instructions for all methods
- **[System Requirements](docs/SYSTEM-REQUIREMENTS.md)** - Supported distributions and dependencies
- **[GUI Installer Guide](Guide/GUI-Installer-Guide.md)** - Step-by-step GUI installer instructions

### Technical Details
- **[Wine Versions](docs/WINE-VERSIONS.md)** - Available Wine versions and recommendations
- **[Hardware Acceleration](docs/HARDWARE-ACCELERATION.md)** - GPU acceleration options (vkd3d-proton, DXVK, OpenCL)
- **[OpenCL Guide](docs/OpenCL-Guide.md)** - Detailed OpenCL configuration
- **[Legacy Scripts](docs/LEGACY-SCRIPTS.md)** - Command-line installation scripts

### Per-Distribution Dependencies
- **[Arch Linux](docs/Arch-Linux.md)** - Arch, Artix, CachyOS, EndeavourOS, XeroLinux
- **[Fedora](docs/Fedora.md)** - Fedora, Nobara
- **[Ubuntu](docs/Ubuntu.md)** - Ubuntu (24.04+ and older)
- **[Linux Mint](docs/Linux-Mint.md)**
- **[Zorin OS](docs/Zorin-OS.md)**
- **[Pop!_OS](docs/Pop-OS.md)**
- **[PikaOS](docs/PikaOS.md)**
- **[openSUSE](docs/openSUSE.md)** - Tumbleweed, Leap

### Additional Resources
- **[Known Issues](docs/Known-issues.md)** - Common problems and solutions
- **[Settings Guide](Guide/Settings.md)** - Configuration options

## Getting Help

- **Discord Community:** [Join our Discord server](https://discord.gg/DW2X8MHQuh) for support and discussions
- **GitHub Issues:** Report bugs and request features on GitHub
- **Documentation:** Check the guides and documentation linked above

## Important Notes

### Support Limitations

- **AMD/Intel GPU Issues:** I cannot fix OpenCL or Wine GPU bugs for AMD/Intel GPUs as I do not have access to these GPUs for testing. Use vkd3d-proton or DXVK instead (see [Hardware Acceleration](docs/HARDWARE-ACCELERATION.md)).
- **Unsupported Distributions:** No support provided for Bazzite, Manjaro. Use AppImage at your own risk (see [System Requirements](docs/SYSTEM-REQUIREMENTS.md)).

## Contributing

Contributions are welcome! See [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) for guidelines.

## License

This project provides installation scripts and configurations for running Affinity software on Linux. Affinity software is a commercial product by Serif (Europe) Ltd. Please ensure you have a valid license before installing.

---

**Disclaimer:** This project is not affiliated with, endorsed by, or associated with Serif (Europe) Ltd. All trademarks and registered trademarks are the property of their respective owners.
