# S1100W-8GT-1SX Switch Firmware Functional Specification & Architecture Guide

[![Target: RTL8373/RTL8372](https://img.shields.io/badge/SoC-Realtek%20RTL8373%2FRTL8372-blue.svg)](#1-hardware-architecture--soc-topology)
[![Core: DW8051](https://img.shields.io/badge/Core-MCS--51%208051%20%40%20125MHz-orange.svg)](#1-hardware-architecture--soc-topology)
[![NVRAM: configCache](https://img.shields.io/badge/NVRAM-2702%20Bytes%20at%200x1FE000-green.svg)](#3-nvram-configuration-schema-configcache)

Comprehensive functional specification, internal architecture reference, CGI API dictionary, NVRAM data schema, and open-source implementation blueprint for the **S1100W-8GT-1SX** 8-Port 2.5G PoE + 1-Port 10G SFP+ Managed Switch.

> [!NOTE]
> ### HARDWARE VARIANT CLARIFICATION
> The target switch hardware analyzed and supported in this project is the **non-PoE physical variant** of the S1100W-8GT-1SX (8x 2.5GbE RJ45 + 1x 10GbE SFP+). While the vendor unified firmware binary includes driver stubs and CGI pages for Power over Ethernet (PSE controller chips), this hardware variant omits the PSE power controller daughterboard and coil transformers. Consequently, an open-source firmware does not need to manage active PSE I2C power budget loops, significantly reducing kernel complexity and memory footprint.

1. [Hardware Architecture & SoC Topology](#1-hardware-architecture--soc-topology)
2. [SPI Flash Memory Map & Container Container](#2-spi-flash-memory-map--container-format)
3. [NVRAM Configuration Schema (`configCache`)](#3-nvram-configuration-schema-configcache)
4. [Web Management & CGI API Reference (33 Modules)](#4-web-management--cgi-api-reference)
5. [Serial CLI Diagnostics Engine](#5-serial-cli-diagnostics-engine)
6. [PoE PSE Subsystem & I2C Control Architecture](#6-poe-pse-subsystem--i2c-control-architecture)
7. [Dumb Switch Mode vs Managed Mode Architecture](#7-dumb-switch-mode-vs-managed-mode-architecture)
8. [Open-Source Firmware Implementation Blueprint](#8-open-source-firmware-implementation-blueprint)

---

## 1. Hardware Architecture & SoC Topology

The S1100W-8GT-1SX switch is built upon Realtek's multi-gigabit Ethernet switching platform:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       REALTEK RTL8373 / RTL8372 ASIC                        │
├─────────────────────────────────────────────────────────────────────────────┤
│  Enhanced DW8051 8-Bit CPU @ 125 MHz  │  Switch Fabric Engine (L2 Switching)│
│  - 64 KB Memory Banking (Keil C51)    │  - 4K VLANs (802.1Q)                │
│  - Internal SRAM Cache                │  - 4K MAC Address Table             │
│  - Timers, Watchdog & UART Controller │  - Wire-speed Packet Filtering / ACL│
├───────────────────────────────────────┴─────────────────────────────────────┤
│                           PORT SERDES INTERFACES                            │
│  Ports 1–8: 2.5Gbps Multi-Gigabit Ethernet (RTL8224 / Integrated PHYs)     │
│  Port 9:    10Gbps SFP+ SerDes Interface (sds0 / sds1)                     │
│  CPU Port:  Internal Management Port (trap2cpu ACL)                         │
├─────────────────────────────────────────────────────────────────────────────┤
│                            I2C / GPIO PERIPHERALS                           │
│  - I2C Bus: Dual Hardware PoE PSE Controllers (Chip 0 & Chip 1)             │
│  - I2C Bus: SFP+ Optical Transceiver DDM/DOM Diagnostics (0x50/0x51)        │
│  - GPIO:    Front Panel Hardware Reset Button & Status LEDs                 │
└─────────────────────────────────────────────────────────────────────────────┘
```

* **CPU Core:** Enhanced 8051 (DW8051), 125 MHz clock frequency.
* **Instruction Set:** MCS-51 with hardware multiplier/divider and Keil code banking registers.
* **Storage:** 2 MB (16 Mbit) SPI NOR Flash (Winbond 25Q16 or FM25Q16).
* **Network Stack:** Realtek LWPS (Light Weight Protocol Stack) bare-metal embedded TCP/IP suite.

---

## 2. SPI Flash Memory Map & Container Format

### 2.1 2 MB SPI Flash Layout
The 2 MB NOR flash is partitioned into protected boot, kernel runtime banks, and persistent configuration sectors:

| Flash Address Range | Size | Allocation / Function | Write Protection |
| :--- | :--- | :--- | :--- |
| `0x000000–0x001001` | 4,098 B | Boot Vector & Reset Handler | Write-Protected |
| `0x001002–0x003FFF` | 12,286 B | Runtime Stage 1 (Updated during FW upgrade) | Writable |
| `0x004000–0x01BFFF` | 98,304 B | Realtek Bootloader & Emergency Failsafe HTTP Server (`192.168.1.1`) | Write-Protected |
| `0x01C000–0x01CFFF` | 4,096 B | Runtime Stage 2 (Updated during FW upgrade) | Writable |
| `0x01D000–0x01FBFFF` | 912,384 B | Runtime Bank-Switched Kernel, LWPS Stack, Web Assets | Writable |
| `0x01FC00–0x01FDFF` | 4,096 B | OEM Factory Calibration (`ftdft.cgi`: MAC, default credentials, logo) | Writable |
| `0x01FE00–0x01FEA8E`| 2,702 B | Active NVRAM Configuration Struct (`configCache`) | Writable |
| `0x01FF00–0x01FFFF`| 256 B | Flash Unique ID & Security Sector | Read-Only |

### 2.2 Realtek Container Header Format
The firmware update container prepends a 20-byte proprietary header:

```
Offset 0x00: [ 12 34 56 78 ] -> Magic Number (0x12345678)
Offset 0x04: [ 00 0E 18 A0 ] -> Payload Length (923,808 bytes)
Offset 0x08: [ 00 00 04 AE ] -> Big-Endian 32-bit Header Inverted Checksum
Offset 0x0C: [ 04 73 63 51 ] -> Big-Endian 32-bit Payload Inverted Checksum
Offset 0x10: [ 33 22 55 FF ] -> Model / Hardware Target ID
```

The bootloader validates both checksums before flashing. If the header or payload checksum does not match, the bootloader rejects the binary.

---

## 3. NVRAM Configuration Schema (`configCache`)

When the switch boots, `sal_sys_config_restore()` loads the persistent configuration from flash address `0x001FE000` into the 2,702-byte RAM structure `configCache`:

```c
struct configCache {
    uint32_t magic;               // Offset 0x0000: 0x23792379
    uint8_t  dhcp_state;          // Offset 0x0004: 0 = Static IP, 1 = DHCP Enabled
    uint8_t  ip_addr[4];          // Offset 0x0005: e.g. 192.168.1.28
    uint8_t  netmask[4];          // Offset 0x0009: e.g. 255.255.255.0
    uint8_t  gateway[4];          // Offset 0x000D: e.g. 192.168.1.1
    uint8_t  reserved1[6];        // Offset 0x0011: Padding
    char     username[19];        // Offset 0x0017: Admin username ("admin\0")
    char     pwd_md5[33];         // Offset 0x002C: hex_md5(user+pass) ("f6fdffe48c908deb0f4c3bd36c032e72\0")
    uint8_t  reserved2[50];       // Offset 0x004D: Padding
    uint32_t bw_ingress_rate[10]; // Offset 0x0080: Ingress rate limits per port (0x00989680 = 10Mbps)
    uint32_t bw_egress_rate[10];  // Offset 0x00A8: Egress rate limits per port
    uint8_t  port_enable[10];     // Offset 0x0140: Port admin state (1 = Enabled, 0 = Disabled)
    uint8_t  port_speed[10];      // Offset 0x014A: Port speed & duplex negotiation
    uint8_t  port_flow_ctrl[10];  // Offset 0x0154: 802.3x Flow Control
    uint8_t  port_eee[10];        // Offset 0x015E: Energy Efficient Ethernet (802.3az)
    uint16_t vlan_port_mask[16];  // Offset 0x024D: Port bitmask for VLANs (0x03FF = all 10 ports)
    uint16_t vlan_pvid[10];       // Offset 0x04C0: Default Port PVID (0x0001 = VLAN 1)
    uint8_t  poe_port_state[8];   // Offset 0x0A03: PoE admin state per port
    uint8_t  poe_power_type[8];   // Offset 0x0A0B: PoE power classification
    // ... remaining bytes cover STP parameters, QoS weights, and SNTP schedules
};
```

---

## 4. Web Management & CGI API Reference

All management web pages communicate with the embedded HTTP server via HTTP POST/GET requests to CGI endpoints.

### Authentication Model
* **Mechanism:** Cookie-based MD5 authentication.
* **Header:** `Cookie: admin=<MD5_HASH>` where `MD5_HASH = hex_md5(username + password)`.
* **Default Admin Cookie:** `admin=f6fdffe48c908deb0f4c3bd36c032e72`.

---

### Complete Endpoint Directory

| Endpoint | Method | Key Parameters | Backend SAL Handler | Description |
| :--- | :--- | :--- | :--- | :--- |
| **`/login.cgi`** | `POST` | `username`, `password`, `Response` | `web_login()` | User authentication & session generation |
| **`/logout.cgi`** | `GET` | *(none)* | `web_logout()` | Invalidates session cookie |
| **`/info.cgi`** | `GET` | *(none)* | `sal_sys_info_get()` | Displays model, MAC, IP, firmware version, and uptime |
| **`/ip.cgi`** | `POST` | `cmd=ip`, `dhcp_state` (0/1), `ip`, `netmask`, `gateway` | `sal_ip_config_set()` | Configures static IP or activates DHCP client |
| **`/port.cgi`** | `POST` | `port`, `speed`, `duplex`, `flowctrl`, `state` | `sal_port_config_set()` | Per-port physical layer speed, duplex, and admin status |
| **`/stats.cgi`** | `GET` | `port` | `sal_port_stats_get()` | TX/RX packet, byte, broadcast, and error counters |
| **`/pse_system.cgi`** | `POST` | `cmd=pse_sys`, `power_budget` | `sal_poe_system_set()` | Global PoE power budget and power management mode |
| **`/pse_port.cgi`** | `POST` | `port`, `state`, `power_limit`, `priority`, `reset` | `sal_poe_port_set()` | PoE port enable, power limit, priority, and remote power cycle |
| **`/vlan.cgi`** | `POST` | `page=static`, `vid`, `vlan_name`, `ports`, `pvid` | `sal_vlan_config_set()` | 802.1Q tagged/untagged VLANs and PVID assignment |
| **`/trunk.cgi`** | `POST` | `group_id`, `ports`, `lacp_enable` | `sal_trunk_config_set()` | Static link aggregation & 802.3ad LACP trunking |
| **`/mirroring.cgi`** | `POST` | `monitor_port`, `mirrored_ports`, `rx_tx_dir` | `sal_mirror_config_set()` | Port mirroring / SPAN traffic analyzer routing |
| **`/isolation.cgi`** | `POST` | `port`, `isolated_port_mask` | `dal_rtl8373_isolation_set()` | Hardware port isolation preventing inter-port forwarding |
| **`/loop.cgi`** | `POST` | `loop_state`, `action` (alarm/shutdown) | `sal_loop_config_set()` | Layer 2 loop detection and automatic port shutdown |
| **`/stp_global.cgi`** | `POST` | `stp_enable`, `protocol` (STP/RSTP/MSTP), `bridge_prio` | `sal_stp_global_set()` | Global Spanning Tree Protocol parameters |
| **`/stp_port.cgi`** | `POST` | `port`, `prio`, `path_cost`, `p2p`, `edge_port` | `sal_stp_port_set()` | Per-port STP priority, path cost, and edge configuration |
| **`/qos.cgi`** | `POST` | `qos_type` (802.1p/DSCP), `sched` (SP/WRR), `queue_weight` | `sal_qos_config_set()` | Quality of Service priority mapping and queue weights |
| **`/bw_ctrl.cgi`** | `POST` | `port`, `ingress_rate`, `egress_rate` | `sal_rate_config_set()` | Bandwidth shaping and rate limiting (in kbps) |
| **`/storm_ctrl.cgi`** | `POST` | `port`, `broadcast_rate`, `multicast_rate`, `unicast_rate` | `sal_rate_storm_set()` | Storm control thresholds to prevent broadcast floods |
| **`/igmp.cgi`** | `POST` | `igmp_enable`, `fast_leave`, `querier` | `sal_igmp_config_set()` | IGMP Snooping v1/v2/v3 and multicast query settings |
| **`/dhcp_snooping.cgi`** | `POST` | `enable_dhcpsnooping`, `trust_ports` | `sal_dhcp_snooping_set()` | Prevents rogue DHCP servers on untrusted ports |
| **`/mac.cgi`** | `GET` | `page=fwd_tbl`, `search_mac` | `sal_l2_mac_get()` | Dumps dynamic MAC address forwarding table (FDB) |
| **`/mac_constraint.cgi`** | `POST` | `port`, `max_mac_learn`, `action` | `sal_mac_constraint_set()` | Limits maximum MAC addresses learned per port |
| **`/jumboframe.cgi`** | `POST` | `jumbo_size` (max 9216 bytes) | `sal_jumbo_config_set()` | MTU / Jumbo frame support |
| **`/eee.cgi`** | `POST` | `port`, `eee_state` | `sal_eee_config_set()` | Energy Efficient Ethernet (IEEE 802.3az) toggles |
| **`/time.cgi`** | `POST` | `sntp_server`, `backup_server`, `timezone` | `sal_sntp_config_set()` | SNTP client configuration and time zone offset |
| **`/timed_reboot.cgi`** | `POST` | `day_mask`, `reboot_time` | `sal_timed_reboot_set()` | Scheduled automated weekly/daily reboot scheduler |
| **`/user.cgi`** | `POST` | `username`, `old_pwd`, `new_pwd` | `sal_user_config_set()` | Updates management administrative credentials |
| **`/save.cgi`** | `POST` | `cmd=save` | `SalFlshCopyCacheToFlsh()` | Commits RAM `configCache` to SPI flash NVRAM (`0x1FE000`) |
| **`/reset.cgi`** | `POST` | `cmd=reset` | `sal_sys_reset()` | Restores factory default settings in NVRAM |
| **`/reboot.cgi`** | `POST` | `cmd=reboot` | `sal_sys_reboot()` | Software system reboot |
| **`/fwug.cgi`** | `POST` | `file` (multipart binary upload) | `sal_firmware_upgrade()` | Validates container checksums and flashes runtime kernel |
| **`/config_back.cgi`** | `GET` | *(none)* | `sal_config_backup_get()` | Downloads full 2,702-byte raw binary NVRAM image |
| **`/ftdft.cgi`** | `POST` | `devmodel`, `hdrVer`, `mac`, `ip`, `netmask`, `gateway` | `sal_factory_dft_set()` | OEM Factory parameters & hardware calibration |

---

## 5. Serial CLI Diagnostics Engine

The UART interface (`57600` baud, 8-N-1) provides a low-level diagnostic prompt (`RTL8373:`):

```text
showip           # Displays CPU MHz, IP, Netmask, Gateway, MAC, Build Date
cfg              # Raw hex dump of the 2,702-byte NVRAM configCache
dft              # Clears NVRAM cache and restores default parameters
reboot           # Triggers watchdog reset
reset            # Re-initializes ASIC switching registers
web              # Activates web management mode (enables trap2cpu ACLs)
dumb             # Activates dumb switch mode (disables trap2cpu ACLs)
fiber            # Dumps SFP optical registers, LOS (Loss of Signal), SerDes modes
poe              # Probes PSE controller chip registers over I2C
lacp             # Displays 802.3ad link aggregation actor/partner state machines
stp              # Dumps spanning tree port states (Forwarding / Blocking / Listening)
mac              # Searches MAC address learning table
mib <port>       # Dumps hardware packet counters for a specific port
regget <addr>    # Reads 32-bit ASIC register
regset <a> <v>   # Writes 32-bit ASIC register
phyget <p> <d> <r>  # Reads MII/MDIO PHY register
physet <p> <d> <r> <v> # Writes MII/MDIO PHY register
sdsget / sdsset  # Direct SerDes analog calibration register read/write
gpioget / gpioset # Direct GPIO pin state read/write
i2cget <slave> <reg> # Direct I2C bus register read
```

---

## 6. PoE PSE Subsystem & I2C Control Architecture

The switch motherboard includes two dedicated 4-port PSE (Power Sourcing Equipment) controller chips communicating over an internal I2C bus:

* **PSE Chip 0:** Controls Ports 1 through 4.
* **PSE Chip 1:** Controls Ports 5 through 8.
* **I2C Protocol:**
  * Byte 1: I2C Slave Address (e.g. `0x20` / `0x24` or similar PSE controller).
  * Byte 2: Register Offset (Port Status, Power Class, Voltage, Current, Port Power Enable).
  * Byte 3: Read/Write payload.
* **PoE Watchdog (`POEDOG`):** An automated background watchdog task continuously samples port current and cuts power if a port exceeds IEEE 802.3at limits (30W per port, 120W total budget).

---

## 7. Dumb Switch Mode vs Managed Mode Architecture

```
[Managed Mode]
Packets -> Switch Fabric -> ACL Checks -> Management Frames TRAPPED to 8051 CPU Port
                                       -> Data Frames Forwarded at Wire Speed

[Dumb Switch Mode]
Packets -> Switch Fabric -> Wire Speed Hardware Switching Across All Ports
                            (trap2cpu ACLs disabled, CPU network stack idle)
```

In **Dumb Switch Mode**, the switch functions as an unmanaged Layer-2 switch:
1. `dal_rtl8373_acl.c` disables CPU port trap rules.
2. All 8x 2.5GbE ports and the 10GbE SFP+ port switch frames purely in hardware at wire speed.
3. The internal 8051 CPU ceases IP and web processing, cutting latency and reducing power by ~0.5–1W.

---

## 8. Open-Source Firmware Implementation Blueprint

Building a complete open-source replacement firmware for the S1100W-8GT-1SX is viable using the following architecture:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       OPEN-SOURCE FIRMWARE STACK                            │
├─────────────────────────────────────────────────────────────────────────────┤
│ Management: Modern Vue/React SPA Web UI (JSON REST API, TLS, Option 12 DHCP)│
├─────────────────────────────────────────────────────────────────────────────┤
│ Network:    Open-Source lwIP / uIP (IPv4, IPv6, ICMP, TCP, UDP, DHCPC)       │
├─────────────────────────────────────────────────────────────────────────────┤
│ Driver HAL: RTLPlayground Core (RTL8373 L2 Fabric, SerDes, VLANs, LACP)    │
├─────────────────────────────────────────────────────────────────────────────┤
│ PoE Driver: Custom I2C PSE Driver (Reverse-engineered from Hasivo SAL)      │
├─────────────────────────────────────────────────────────────────────────────┤
│ Compiler:   SDCC (Small Device C Compiler) with 8051 bank-switching         │
├─────────────────────────────────────────────────────────────────────────────┤
│ Packager:   calcsum.py (Produces bootloader-compatible 20-byte container)  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Key Advantages of an Open-Source Implementation:
1. **True DHCP Option 12:** Automatically broadcasts the switch's hostname to your home router.
2. **Modern Web UI:** Single-page application without outdated framesets or vendor quirks.
3. **Enhanced Security:** Modern SHA-256 password hashing and TLS capability.
4. **Active PoE Management:** First open-source RTL8372/RTL8373 firmware to support full PoE power allocation and remote port power cycling.
