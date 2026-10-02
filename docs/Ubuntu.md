# Ubuntu

The packages below are the system dependencies the Affinity installer needs. The installer can install them automatically, but these commands let you install them yourself, or recover if the automatic install fails.

The steps depend on your Ubuntu version. Follow the section that matches yours.

## Which Section Applies to You?

Run this command to find your version:

```bash
grep VERSION_ID /etc/os-release
```

- Version `24.04` or newer (25.10, 25.04, 24.10, 24.04): use **Section A**.
- Version `23.10` or older (23.04, 22.04, 20.04): use **Section B**.

## Section A: Ubuntu 24.04 and Newer

### Step 1: Install the Dependencies

```bash
sudo dpkg --add-architecture i386
sudo apt update
sudo apt install wine wine64 wine32 libwine libwine:i386 fonts-wine \
  winetricks wget curl p7zip-full tar jq zstd \
  dotnet-sdk-8.0 dotnet-sdk-10.0 \
  python3-pyqt6
```

### Ubuntu 26.04

Ubuntu 26.04 no longer ships `p7zip-full` (the `7zip` package provides the `7z` command) or `dotnet-sdk-8.0`, and apt refuses the whole command if a package is missing. Use:

```bash
sudo dpkg --add-architecture i386
sudo apt update
sudo apt install wine wine64 wine32 libwine libwine:i386 fonts-wine \
  winetricks wget curl 7zip tar jq zstd \
  dotnet-sdk-10.0 \
  python3-pyqt6 python3-pyqt6.qtsvg
```

## Section B: Ubuntu 23.10 and Older

These versions use WineHQ staging because the official repositories no longer carry a compatible Wine for them.

### Step 1: Add the WineHQ Repository

```bash
sudo mkdir -pm755 /etc/apt/keyrings
wget -O- https://dl.winehq.org/wine-builds/winehq.key | sudo gpg --dearmor -o /etc/apt/keyrings/winehq-archive.key
sudo wget -NP /etc/apt/sources.list.d/ https://dl.winehq.org/wine-builds/ubuntu/dists/jammy/winehq-jammy.sources
```

### Step 2: Install the Dependencies

```bash
sudo apt update
sudo apt install --install-recommends -y winehq-staging \
  winetricks wget curl p7zip-full tar jq zstd \
  dotnet-sdk-8.0 dotnet-sdk-10.0 \
  python3-pyqt6
```

## Verify the Installation (Both Sections)

```bash
which wine winetricks wget curl tar jq zstd
which 7z unzip
dotnet --list-sdks
```

- The first two commands should print file paths. If a line is empty, that package is missing.
- `dotnet --list-sdks` should list versions `8.x` and `10.x`.

## Troubleshooting

- If the .NET SDK packages are not available, you may need to enable Microsoft's repository first. See https://dotnet.microsoft.com/download
- On Ubuntu 25.10, if the installer opens but the icons are missing, install one extra package:
  ```bash
  sudo apt install python3-pyqt6.qtsvg
  ```
- If the installer still reports a missing dependency, close it and reopen it, then click "One-Click Full Setup" again.
