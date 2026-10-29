# Fedora

This guide covers **Fedora and Nobara**.

The packages below are the system dependencies the Affinity installer needs. The installer can install them automatically, but these commands let you install them yourself, or recover if the automatic install fails.

## Before You Begin

- You need a user account with `sudo` privileges (the password you enter when installing software).
- Copy and paste the commands one block at a time, then press Enter.
- If a command asks for confirmation, type `y` and press Enter.

## Step 1: Install the Dependencies

```bash
sudo dnf install wine winetricks wget curl p7zip p7zip-plugins tar jq zstd \
  dotnet-sdk-8.0 dotnet-sdk-10.0 \
  python3-pyqt6
```

What these packages do:

| Package | Purpose |
| --- | --- |
| `wine`, `winetricks` | Run the Windows version of Affinity |
| `wget`, `curl` | Download files during setup |
| `p7zip`, `p7zip-plugins`, `tar`, `zstd` | Extract the Wine archive |
| `jq` | Read configuration files |
| `dotnet-sdk-8.0`, `dotnet-sdk-10.0` | Build the patcher the installer uses |
| `python3-pyqt6` | The graphical interface of the installer |

## Step 2: Verify the Installation

```bash
which wine winetricks wget curl tar jq zstd
which 7z unzip
dotnet --list-sdks
```

- The first two commands should print file paths. If a line is empty, that package is missing.
- `dotnet --list-sdks` should list versions `8.x` and `10.x`.

## Troubleshooting

- If the .NET SDK packages are not available in your repositories, you may need to enable Microsoft's repository first. See https://dotnet.microsoft.com/download
- If the installer still reports a missing dependency, close it and reopen it, then click "One-Click Full Setup" again.

## Notes

- Fedora has no `python3-pyqt6-svg` package. The SVG support the installer's icons need is part of `python3-pyqt6`.
