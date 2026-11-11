# S1100W-8GT-1SX Open-Source Firmware (RTLPlayground) User & Operational Guide

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](../LICENSE)
[![Firmware: RTLPlayground](https://img.shields.io/badge/Firmware-RTLPlayground--v0.1.0-blueviolet.svg)](#architecture-overview)
[![Architecture: MCS-51](https://img.shields.io/badge/Architecture-MCS--51%208051-orange.svg)](#architecture-overview)

Comprehensive operational guide, serial CLI command reference, and Web UI documentation for the open-source firmware distribution on the **Hasivo / Realtek S1100W-8GT-1SX** (and non-PoE variants) 8-port 2.5GbE + 1-port 10GbE SFP+ managed network switch.

---

## Table of Contents
- [Architecture Overview](#architecture-overview)
- [Hardware Port & Pin Mapping](#hardware-port--pin-mapping)
- [Web Management Interface](#web-management-interface)
- [Serial CLI Reference](#serial-cli-reference)
- [Network Services & DHCP Client](#network-services--dhcp-client)
- [Hardware TDR Cable Diagnostics](#hardware-tdr-cable-diagnostics)
- [Configuration Persistence & Flash Storage](#configuration-persistence--flash-storage)
- [Building from Source](#building-from-source)
- [Emergency Recovery & Failsafe Bootloader](#emergency-recovery--failsafe-bootloader)

---

## Architecture Overview

The open-source firmware for the S1100W-8GT-1SX is based on the **[logicog/RTLPlayground](https://github.com/logicog/RTLPlayground)** framework, engineered specifically for Realtek RTL837x family switching ASICs running enhanced 8-bit MCS-51 (8051) microcontrollers.

* **Target Microcontroller:** Enhanced MCS-51 core running at 125 MHz.
* **Compiler:** SDCC 4.6.0 with banked code memory model (`--model-large --banked`).
* **Code Memory:** 4 physical memory banks mapped dynamically into the 64 KB address space:
  * **Bank 0 / Common:** Reset vectors, interrupt service routines, scheduler loop, timer ticks.
  * **Bank 1:** PHY driver, RTL8373 register access, SerDes initialization, SerDes calibration.
  * **Bank 2:** Interactive ANSI CLI parser, string formatting, hardware TDR cable diagnostics.
  * **Bank 3:** Embedded HTTP web server, gzip decompressor, session token manager, 802.1w RSTP state machine.
* **Switching Core:** Realtek RTL8373 / RTL8372 wire-speed Layer-2 fabric.
* **Network Stack:** Optimized lightweight embedded TCP/IP with zero-copy packet dispatching.

---

## Hardware Port & Pin Mapping

| Switch Port | Physical Role | Hardware Interface | Link Speeds Supported |
| :--- | :--- | :--- | :--- |
| **Port 1 – Port 8** | 2.5GbE Access Ports | Integrated Multi-Gigabit PHYs (RTL8221B core) | 10M / 100M / 1000M / 2.5Gbps Full Duplex |
| **Port 9** | 10GbE SFP+ Optical / DAC | SerDes 1 (`sds1`) | 1Gbps / 10Gbps |
| **Port 0 (CPU)** | Management CPU Port | Internal ASIC DMA channel | 1000M internal |
| **SFP Detect** | Optical Presence | GPIO 30 | Active Low |
| **SFP LOS** | Loss of Signal | GPIO 37 | Active High |
| **Reset Button**| Front Panel Button | GPIO 54 | Active Low |

---

## Web Management Interface

The open-source firmware features a modern, single-page responsive Web Console built with standards-compliant HTML5, CSS3, and JavaScript, completely eliminating proprietary vendor plugins or external frameworks.

* **Access URL:** `http://<switch-ip>/` (Default: acquired via DHCP, or static fallback `192.168.1.28`)
* **Default Password:** `admin`
* **Session Management:** Secure cookie tokens with `SameSite=Strict` and Content Security Policy (`CSP`).
* **Theme Support:** Automatic dark/light theme switching based on operating system preferences, with manual overrides.

### Core Web Modules
1. **Live Traffic Telemetry:** Real-time throughput graph displaying packets/sec and byte counters for all 9 ports.
2. **Port Configuration:**
   * Enable/Disable port administrative state.
   * Speed negotiation: Auto-Negotiation, 10M, 100M, 1000M, 2500M.
   * Energy Efficient Ethernet (EEE 802.3az) toggle.
   * Custom port labeling.
3. **802.1Q VLAN Editor:**
   * Create, edit, and delete VLANs (IDs 1 through 4094).
   * Visual matrix for Tagged (`T`), Untagged (`U`), and Excluded members.
   * Port PVID (Port VLAN ID) assignment.
   * Ingress frame filtering (Tagged only, Untagged only, or Any).
4. **Rapid Spanning Tree (RSTP 802.1w):**
   * Per-port STP enable/disable, edge port detection, path cost overrides, and priority tuning.
   * Live root bridge identification and port role status (Root, Designated, Alternate, Blocking).
5. **Link Aggregation (LACP 802.3ad):**
   * Multi-port static trunks and dynamic LACP aggregates.
6. **Storm Control & Bandwidth Limits:**
   * Ingress and egress rate limiting (0.016 Mbps to 10,000 Mbps).
   * Broadcast, unknown multicast, and unknown unicast storm thresholds.
7. **LLDP (Link Layer Discovery Protocol):**
   * Advertises switch identity and discovers connected network devices and capabilities.

---

## Serial CLI Reference

Direct physical access via the 4-pin 2.54mm TTL UART header:

```
[Pin 1] Switch TX  -->  Adapter RX
[Pin 2] GND        -->  Adapter GND
[Pin 3] Switch RX  -->  Adapter TX
[Pin 4] 3.3V VCC   -->  DO NOT CONNECT
```

* **Baud Rate:** `115200` (8-N-1, No Flow Control)
* **Prompt:** `> `

### System Commands

| Command | Arguments | Description | Example |
| :--- | :--- | :--- | :--- |
| `version` | None | Displays firmware version, build timestamp, SDCC compiler info, and detected hardware | `version` |
| `hostname` | `[name]` | Displays or sets the system hostname broadcasted in DHCP discovery | `hostname S1100W` |
| `ip` | None | Shows active IP address, assignment mode (DHCP / static), and renewal timer | `ip` |
| `mac` | None | Displays the factory hardware MAC address read from ASIC registers | `mac` |
| `passwd` | `<password>` | Updates the administrator password for web and console authentication | `passwd admin` |
| `flash save` | None | Commits running configuration to SPI flash security registers | `flash save` |
| `reboot` | None | Restarts the switch microcontroller and switching fabric | `reboot` |

### Port & Physical Layer Commands

| Command | Arguments | Description | Example |
| :--- | :--- | :--- | :--- |
| `port <1-8> show` | None | Shows link speed, duplex, auto-negotiation, and link partner advertised capabilities | `port 1 show` |
| `port <1-8> on` | None | Administratively enables the specified port | `port 1 on` |
| `port <1-8> off` | None | Administratively disables the specified port | `port 1 off` |
| `port <1-8> 2g5 full`| None | Forces 2.5 Gbps Full Duplex mode | `port 2 2g5 full` |
| `port <1-8> 1g full` | None | Forces 1.0 Gbps Full Duplex mode | `port 2 1g full` |
| `port <1-8> name <txt>`| `<txt>` | Assigns a custom description name to the port | `port 1 name Uplink-Router` |
| `sfp` | None | Displays SFP+ optical transceiver presence, vendor, part number, and DDM diagnostics | `sfp` |

### VLAN & L2 Commands

| Command | Arguments | Description | Example |
| :--- | :--- | :--- | :--- |
| `vlan show` | None | Displays ingress VLAN table, PVIDs, frame filtering, and port acceptance types | `vlan show` |
| `vlan <id> <ports>` | `[port][t]...` | Creates or updates a VLAN membership (`t` for tagged, omission for untagged) | `vlan 10 1 2 9t` |
| `stp status` | None | Shows global STP state, root bridge ID, and root port | `stp status` |
| `stp on` | None | Enables 802.1w Rapid Spanning Tree Protocol | `stp on` |
| `stp off` | None | Disables Spanning Tree Protocol | `stp off` |
| `lldp` | `[on\|off]` | Enables or disables Link Layer Discovery Protocol | `lldp on` |

---

## Network Services & DHCP Client

### Automatic Hostname & DHCP Option 12
The open-source firmware includes a fully compliant DHCP client with **DHCP Option 12 (Host Name)** support.

When the switch powers on:
1. It sends a `DHCPDISCOVER` packet containing Option 12 formatted as `S1100W`.
2. The local router allocates an IP lease and registers `S1100W` in the local DNS resolver.
3. Network clients can access the switch directly using:
   ```text
   http://S1100W/   or   http://s1100w.local/
   ```

### Fallback Static IP
If no DHCP server responds within 30 seconds, the switch automatically falls back to:
* **IP:** `192.168.1.28`
* **Netmask:** `255.255.255.0`
* **Gateway:** `192.168.1.1`

---

## Hardware TDR Cable Diagnostics

The RTL8221B multi-gigabit PHYs incorporate hardware Time Domain Reflectometry (TDR) to accurately pinpoint cable faults, open pairs, and short circuits down to the centimeter:

1. **Initiate Cable Test:**
   ```text
   > port 1 cabletest
   Logical Port: 00
   Cable test started
   ```
2. **Display Diagnostic Results:**
   ```text
   > port 1 cabletest show
   Logical Port: 00
   Cable test port 1
   Pair A: open at 5.2 m
   Pair B: open at 5.2 m
   Pair C: open at 5.4 m
   Pair D: open at 5.8 m
   ```
* Displays status (`OK`, `Open`, `Short`) and distance to fault for all 4 twisted pairs (A, B, C, D).

---

## Configuration Persistence & Flash Storage

Changes made via the CLI or Web UI take effect immediately in runtime memory. To ensure settings survive power cycles:

* **Via Serial CLI:**
  ```text
  > flash save
  ```
* **Via Web UI:** Click **Save** on the bottom navigation bar.

The firmware writes the compressed binary configuration record into SPI NOR flash security sector 1.

---

## Building from Source

To compile the open-source firmware from source on Debian Trixie / Ubuntu:

```bash
# 1. Install prerequisites
sudo apt-get update
sudo apt-get install -y build-essential libboost-dev libboost-graph-dev libjson-c-dev \
  zlib1g-dev bison flex patch bzip2 xxd python3 python-is-python3 curl ca-certificates git

# 2. Clone repository
git clone https://github.com/logicog/RTLPlayground.git
cd RTLPlayground

# 3. Configure tailored board profile
cat << 'EOF' > config_s1100w.txt
hostname S1100W
passwd admin
ip 192.168.1.28
netmask 255.255.255.0
gw 192.168.1.1
ip dhcp
EOF

# 4. Compile patched SDCC toolchain (automatically invoked on first build) & firmware binary
make CI=1 MACHINE=HASIVO_S1100WP_8GT_1SX_SE CONFIG=config_s1100w.txt

# 5. Build installer & package OEM-upgradeable container image
#    Outputs: installer/output/rtlplayground_oem_upgrade.bin & output/rtlplayground.bin
make -C installer
```

---

## Emergency Recovery & Failsafe Bootloader

The switch's hardware write-protected bootloader provides emergency unbrick recovery:

1. Connect the serial console (`115200` or `57600` baud).
2. Power on the switch while holding `ESC`.
3. The bootloader starts an emergency HTTP recovery server on `192.168.1.1`.
4. Connect an Ethernet cable from your PC to Port 1, configure your PC to `192.168.1.2/24`, and upload an OEM upgrade image downloaded from [Releases](../../releases):
   ```bash
   curl -F "file=@S1100W-OPENSOURCE-<tag>-OEM-UPGRADE.bin" "http://192.168.1.1/httpupg.cgi?cmd=fw_upgrade"
   ```
5. The switch will verify the container, flash the kernel, and reboot automatically.

