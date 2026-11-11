# Hasivo S1100W-8GT-1SX Knowledge Base & Firmware Pipeline

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Architecture: 8051](https://img.shields.io/badge/Architecture-MCS--51%208051-orange.svg)](#hardware-architecture)
[![Upstream: RTLPlayground](https://img.shields.io/badge/Upstream-logicog%2FRTLPlayground-blueviolet.svg)](https://github.com/logicog/RTLPlayground)
[![Build Pipeline](https://github.com/SanjayRavindran/S1100W-8GT-1SX/actions/workflows/build-and-release.yml/badge.svg)](https://github.com/SanjayRavindran/S1100W-8GT-1SX/actions/workflows/build-and-release.yml)

Exhaustive hardware documentation, reverse-engineering specifications, original firmware architecture, and an automated CI/CD build pipeline for the **Hasivo / Realtek S1100W-8GT-1SX** (8x 2.5GbE RJ45 + 1x 10GbE SFP+) managed network switch.

> [!NOTE]
> **No Binary Policy:** This repository does not store or distribute pre-compiled binary firmware images in the git tree. All firmware binaries are generated automatically from upstream [logicog/RTLPlayground](https://github.com/logicog/RTLPlayground) releases via the [GitHub Actions CI/CD Pipeline](#automated-cicd-build-pipeline) and published as downloadable assets under [GitHub Releases](../../releases).

---

> [!CAUTION]
> ### DISCLAIMER
> This documentation, tools, and build pipelines are provided strictly for educational, research, and hardware interoperability purposes. Modifying or flashing network switch firmware carries inherent risks of bricking the device or damaging hardware. The author and contributors accept **NO responsibility or liability** for damaged, bricked, or non-functional hardware resulting from the use of these tools or firmwares. **Proceed entirely at your own risk.**

---

## Table of Contents
- [Hardware Architecture & Port Mapping](#hardware-architecture--port-mapping)
- [Serial UART Header & Physical Access](#serial-uart-header--physical-access)
- [Original Firmware Documentation & Inner Workings](#original-firmware-documentation--inner-workings)
- [Linux & OpenWrt Feasibility](#linux--openwrt-feasibility)
- [Automated CI/CD Build Pipeline](#automated-cicd-build-pipeline)
- [Open-Source Firmware (RTLPlayground)](#open-source-firmware-rtlplayground)
- [Firmware Flashing & Recovery Guide](#firmware-flashing--recovery-guide)
- [Realtek Container Format & Verification Tool](#realtek-container-format--verification-tool)
- [Repository Structure](#repository-structure)
- [License & Acknowledgments](#license--acknowledgments)

---

## Hardware Architecture & Port Mapping

The **Hasivo S1100W-8GT-1SX** (and non-PoE variant) is an 8-port 2.5 Gigabit Ethernet + 1-port 10 Gigabit SFP+ managed Layer-2 switch powered by Realtek silicon:

* **Switching Fabric:** Realtek **RTL8373 / RTL8372** (`Ver8373_72: C`) wire-speed Layer-2 core.
* **Microcontroller:** Enhanced **8-bit MCS-51 (8051)** running at 125 MHz.
* **Memory Architecture:** 64 KB memory banking with 4 dynamically switched code memory banks.
* **Storage:** 16 Mbit (2 MB) SPI NOR Flash (Winbond / FM25Q16).

### Physical Port & Pin Map

| Port | Hardware Interface | Driver Core | Supported Speeds |
| :--- | :--- | :--- | :--- |
| **Port 1 – Port 8** | 2.5GbE RJ45 | Integrated RTL8221B multi-gig PHYs | 10M / 100M / 1000M / 2.5Gbps Full Duplex |
| **Port 9** | 10GbE SFP+ Optical / DAC | SerDes 1 (`sds1`) | 1Gbps / 10Gbps |
| **Port 0 (CPU)** | Internal Management Port | ASIC Internal DMA | 1000M wire-speed |
| **SFP Detect** | Optical Presence | GPIO 30 | Active Low |
| **SFP LOS** | Loss of Signal | GPIO 37 | Active High |
| **Reset Button**| Front Panel Pin | GPIO 54 | Active Low |

---

## Serial UART Header & Physical Access

The switch PCB includes an unpopulated 4-pin 2.54mm TTL UART header providing direct console access to the bootloader and operating system:

```text
[Pin 1 (Square Pad)] Switch TX  -->  Adapter RX
[Pin 2]              GND        -->  Adapter GND
[Pin 3]              Switch RX  -->  Adapter TX
[Pin 4]              3.3V VCC   -->  DO NOT CONNECT (Leaves VCC open to prevent back-powering)
```

* **Bootloader & Factory Firmware:** `57600` baud, 8-N-1
* **Open-Source Firmware:** `115200` baud, 8-N-1

---

## Original Firmware Documentation & Inner Workings

The original vendor firmware combines the Realtek Software Abstraction Layer (SAL), Hardware Driver Layer (DAL), and the **LWPS** (Light Weight Protocol Stack) embedded TCP/IP suite.

Comprehensive technical references detailing the inner workings of the original firmware:

1. **[`docs/FIRMWARE_SPECIFICATION.md`](docs/FIRMWARE_SPECIFICATION.md):**
   * **All 33 CGI Modules:** Exhaustive breakdown of endpoints, form inputs, validation rules, and SAL handlers (`info.cgi`, `port.cgi`, `vlan.cgi`, `trunk.cgi`, `mirror.cgi`, `qos.cgi`, `igmp.cgi`, `stp.cgi`, `sntp.cgi`, `fwug.cgi`, etc.).
   * **NVRAM Struct Map:** Field-by-field layout of the 2,702-byte `configCache` NVRAM binary structure (`0x1FC000`).
   * **Flash Memory Map:** 2 MB SPI flash partition table and boundary offsets.
2. **[`docs/ORIGINAL_FIRMWARE_ANALYSIS.md`](docs/ORIGINAL_FIRMWARE_ANALYSIS.md):**
   * **Dumb Switch Mode Mechanics:** How the RTL8372 ASIC forwards packets in hardware without CPU intervention when management ACL traps (`trap2cpu`) are disabled.
   * **Factory Engineering Suite:** OEM branding and calibration submenus at `/Hasivo_mp_cfg` (`ftdft.cgi`, `ftlogo.cgi`, `ftcolor.cgi`).
   * **20-Byte Container Format:** Mathematical formulation of the 32-bit inverted accumulator checksum.

---

## Linux & OpenWrt Feasibility

### Can Linux or OpenWrt run on this switch?
**No, it is technically impossible.**
* **CPU Limitations:** The RTL8372/RTL8373 management core is an **8-bit MCS-51 (8051)** microcontroller. Linux and OpenWrt require a 32-bit or 64-bit CPU architecture (MIPS, ARM, RISC-V, x86) with an MMU and a minimum of 16 MB–64 MB of RAM.
* **Memory Constraints:** MCS-51 code execution is constrained to 64 KB banks.
* **Proprietary SerDes:** Realtek's 2.5GbE PHY and 10G SFP+ SerDes calibration registers are closed-source.
* **The Solution:** An open-source bare-metal 8051 banked runtime (such as [logicog/RTLPlayground](https://github.com/logicog/RTLPlayground)) provides full wire-speed Layer-2 management, native web UI, and standards-compliant networking while operating natively within the 8051 hardware boundaries.

---

## Automated CI/CD Build Pipeline

This repository includes a continuous integration build pipeline located at [`.github/workflows/build-and-release.yml`](.github/workflows/build-and-release.yml).

### How It Works:
1. **Upstream Release Tracking:** Runs every 6 hours via [`tools/check_upstream_release.py`](tools/check_upstream_release.py), checking the [logicog/RTLPlayground](https://github.com/logicog/RTLPlayground) `releases.atom` feed and verifying board compatibility (`machine/HASIVO_S1100WP_8GT_1SX_SE.h` and `installer/Makefile`). Legacy releases predating S1100W support (such as `v0_1_alpha`) are skipped cleanly.
2. **Hermetic Cross-Compilation (`debian:trixie`):** Provisions an isolated Debian Trixie build container matching upstream CI, compiles and caches the patched SDCC 4.6.0 toolchain (`sdcc/build.sh` with retry support), and compiles `MACHINE=HASIVO_S1100WP_8GT_1SX_SE` with `--Werror` (`CI=1`).
3. **Packaging & Dual Checksum Verification:** Builds `make -C installer` cleanly to package the Realtek OEM upgrade container, validates it with [`tools/calcsum.py`](tools/calcsum.py), and validates the raw 512 KB SPI image with `crc_calculator -v`.
4. **Automated GitHub Releases:** When a new compatible upstream release is detected (or triggered manually via `workflow_dispatch`), it publishes a GitHub Release under [Releases](../../releases) with both `OEM-UPGRADE.bin` and `FULL-FLASH.bin` plus `SHA256SUMS.txt` attached—keeping the git tree free of binaries.

---

## Open-Source Firmware (RTLPlayground)

The open-source firmware distribution for the S1100W provides a modern, bloat-free operating environment:

* **Modern Single-Page Web Console:** Lightweight HTML5/CSS/JavaScript interface with automatic dark/light theme switching.
* **Native DHCP Option 12:** Automatically announces `hostname S1100W` during DHCP discovery. Your home router will instantly recognize and resolve the switch as `S1100W` on your LAN.
* **Real-time Live Traffic:** Packets/sec throughput telemetry across all 9 ports.
* **Rapid Spanning Tree (RSTP 802.1w):** Standards-compliant loop prevention.
* **802.1Q VLANs & PVIDs:** Visual VLAN matrix and ingress filtering.
* **LACP (802.3ad):** Multi-port hardware link aggregation.
* **Hardware TDR Cable Diagnostics:** Centimeter-accurate cable fault detection.
* **Interactive Serial CLI:** 115200 baud command shell.

Detailed usage and command documentation is available in [`docs/OPENSOURCE_FIRMWARE_GUIDE.md`](docs/OPENSOURCE_FIRMWARE_GUIDE.md).

---

## Firmware Flashing & Recovery Guide

Firmware binaries built by the CI/CD pipeline are wrapped with Realtek container headers and can be flashed directly through the web interface or the emergency recovery bootloader:

### Method 1: Web UI Upgrade
1. Download the latest `S1100W-OPENSOURCE-<tag>-OEM-UPGRADE.bin` from [Releases](../../releases).
2. Open your switch's current Web UI and log in.
3. Go to **Tools $\rightarrow$ Firmware Upgrade** (`/fwug.cgi`).
4. Select the downloaded `.bin` file and click **Upgrade**.
5. The switch will verify the container checksum, write to SPI flash, and reboot within ~60 seconds.

### Method 2: Bootloader Failsafe HTTP Upgrade (Zero-Risk Recovery)
The Realtek bootloader (`0x004000–0x01BFFF`) is write-protected in hardware and provides an emergency recovery web server:
1. Connect a 3.3V USB-UART serial adapter to the 4-pin header (`57600` baud).
2. Power cycle the switch while pressing `ESC`.
3. The bootloader starts an emergency recovery server at `http://192.168.1.1/`.
4. Connect an Ethernet cable to Port 1, configure your PC to `192.168.1.2/24`, and upload:
   ```bash
   curl -F "file=@S1100W-OPENSOURCE-<tag>-OEM-UPGRADE.bin" "http://192.168.1.1/httpupg.cgi?cmd=fw_upgrade"
   ```

---

## Realtek Container Format & Verification Tool

Verify any firmware image container at any time using [`tools/calcsum.py`](tools/calcsum.py):

```bash
# Verify checksum integrity:
python tools/calcsum.py <firmware_image.bin>

# Recalculate and update checksums in-place:
python tools/calcsum.py -u <firmware_image.bin>
```

Expected output for a valid image (exits `0`; exits non-zero on invalid magic, length, payload sum, header sum, or header mismatch):
```text
magic: 12345678
length: 00084012
header sum: 000003d6
payload sum: 020c04ad
reserved: 332255ff
```

---

## Repository Structure

```text
S1100W-8GT-1SX/
├── README.md                                  # Primary project documentation & knowledge base
├── LICENSE                                    # MIT License
├── .gitignore                                 # Ignores all binaries and temporary artifacts
├── .github/
│   └── workflows/
│       └── build-and-release.yml              # Automated upstream CI/CD release workflow
├── docs/
│   ├── ORIGINAL_FIRMWARE_ANALYSIS.md          # Hardware architecture, dumb mode & container format
│   ├── FIRMWARE_SPECIFICATION.md              # Complete 33-CGI module & NVRAM struct specification
│   └── OPENSOURCE_FIRMWARE_GUIDE.md           # Exhaustive open-source user & CLI reference guide
└── tools/
    ├── calcsum.py                             # Realtek container checksum verification tool
    └── check_upstream_release.py              # Upstream RTLPlayground release compatibility checker
```

---

## License & Acknowledgments

* Licensed under the [MIT License](LICENSE).
* Open-source firmware runtime developed by the [logicog/RTLPlayground](https://github.com/logicog/RTLPlayground) project.
* Container format research inspired by [`up-n-atom/SWTG118AS`](https://github.com/up-n-atom/SWTG118AS).
