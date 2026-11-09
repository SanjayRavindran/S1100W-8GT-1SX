# S1100W-8GT-1SX Hardware & Original Firmware Analysis

**Target Device:** Hasivo / Realtek S1100W-8GT-1SX (8x 2.5GbE RJ45 + 1x 10GbE SFP+ Managed Switch)  
**Original Firmware Target:** `S1100W-8GT-1SX-SEV1.9` (Winbond / FM25Q16 2 MB SPI NOR Flash)

---

## 1. Hardware Architecture & Switching Core

* **Switching ASIC:** Realtek **RTL8373 / RTL8372** (`Ver8373_72: C`).
* **Processor Architecture:** Enhanced **8-bit 8051 microcontroller** (Intel MCS-51 architecture) running at 125 MHz.
* **Memory Architecture:** 64 KB memory banking via Keil C51 banking hardware registers (`BANK0` to `BANK3`).
* **Operating System / Stack:** Bare-metal RTOS combining:
  * Realtek **SAL** (Software Abstraction Layer)
  * Realtek **DAL** (Device Abstraction Layer / Hardware Drivers)
  * Realtek **LWPS** (Light Weight Protocol Stack) embedded TCP/IP suite and mini-HTTP web server.
* **Storage:** 16 Mbit (2 MB) SPI NOR Flash.

---

## 2. Physical Connectivity & Serial UART Header

The PCB includes an unpopulated 4-pin 2.54mm header providing direct 3.3V TTL UART access:

```text
[Pin 1 (Square)] Switch TX  -->  Serial Adapter RX
[Pin 2]          GND        -->  Serial Adapter GND
[Pin 3]          Switch RX  -->  Serial Adapter TX
[Pin 4]          3.3V VCC   -->  DO NOT CONNECT (Leaves VCC open to prevent back-powering)
```

* **Bootloader & Vendor Baud Rate:** `57600` (8-N-1)
* **Open-Source Firmware Baud Rate:** `115200` (8-N-1)

---

## 3. Realtek 20-Byte Container Header Format

All firmware images flashed through the web upgrade handler (`/fwug.cgi`) or the emergency bootloader (`/httpupg.cgi`) must be prepended with Realtek's proprietary 20-byte image container header:

```
+-----------------------------------+-----------------------------------+
| Magic (4 bytes): 0x12345678       | Payload Length (4 bytes, BE)      |
+-----------------------------------+-----------------------------------+
| Header Checksum (4 bytes, BE)     | Payload Checksum (4 bytes, BE)    |
+-----------------------------------+-----------------------------------+
| Model / Reserved (4 bytes): 0x332255FF                                |
+-----------------------------------+-----------------------------------+
| Binary Code Payload (starts at offset 0x14) ...                       |
+-----------------------------------------------------------------------+
```

### Checksum Mathematical Formulation:
Both the header checksum and payload checksum use an inverted big-endian 32-bit unsigned accumulator algorithm:

$$\text{Checksum} = 0 - \sum \text{words}$$

Specifically:
1. Initialize a 32-bit unsigned accumulator `sum = 0`.
2. For each 4-byte big-endian word, add to `sum` (modulo $2^{32}$).
3. If odd bytes remain at the end, pad with zeros to form a 32-bit word.
4. Calculate final checksum: `(~sum) + 1` (two's complement inverse).
5. The header checksum is calculated over bytes `0x00–0x07` and `0x10–0x13` (skipping checksum fields).
6. The payload checksum is calculated over all bytes from offset `0x14` to EOF.

The verification implementation is provided in [`tools/calcsum.py`](../tools/calcsum.py).

---

## 4. Dumb Switch Mode Analysis (ASIC Wire-Speed Mechanics)

The switch supports hardware switching between **Managed Mode** and **Dumb Switch Mode** (unmanaged wire-speed mode).

### Underlying ASIC Routing:
* In **Managed Mode**, the RTL8372 ASIC traps Layer-2 management packets (ARP, ICMP, HTTP) and forwards them to the internal 8051 CPU port via Access Control Lists (`trap2cpu`).
* In **Dumb Switch Mode**:
  1. The ACL rules directing packets to the CPU port in `dal_rtl8373_acl.c` are disabled.
  2. Frame switching, MAC address table learning, and packet forwarding run 100% in hardware switching fabric.
  3. All 8x 2.5GbE ports and the 10GbE SFP+ port forward Layer-2 traffic at wire speed with zero CPU latency.
  4. The embedded network stack drops off the network, cutting power consumption by ~0.5W to 1.0W.

### Toggling Dumb Mode:
* **Serial CLI:** `dumb` (activates unmanaged mode), `web` (restores management mode).
* **Hardware Button:** Hold the reset button on the front panel for ~10 seconds.

---

## 5. Linux / OpenWrt Feasibility Study

### Can OpenWrt or Linux run on this device?
**No, it is technically impossible.**
* **Microcontroller Limitations:** The management core is an **8-bit MCS-51 (8051)** microcontroller. Linux and OpenWrt require a 32-bit or 64-bit CPU architecture (MIPS, ARM, RISC-V, x86) with an MMU and a minimum of 16 MB–64 MB of RAM.
* **Memory Constraints:** MCS-51 code execution is constrained to 64 KB banked memory.
* **Proprietary SerDes:** Realtek's 2.5GbE PHY and 10G SFP+ SerDes calibration registers are closed-source.
* **The Solution:** An open-source bare-metal 8051 banked runtime (such as [logicog/RTLPlayground](https://github.com/logicog/RTLPlayground)) provides full wire-speed Layer-2 management, native web UI, and standards-compliant networking while operating natively within the 8051 hardware boundaries.

---

## 6. Factory Test Suite (`/Hasivo_mp_cfg`)

The original firmware contains an internal factory engineering and calibration suite at `/Hasivo_mp_cfg` (or `/login_ft.cgi`):

1. **Factory Defaults (`ftdft.cgi`):**
   * Writes directly to SPI Flash NVRAM sector `0x001FD000`.
   * Sets baseline factory parameters: Device Model, Hardware Version, MAC address, default IP, and PoE power limits (120W).
2. **Web Logo Customization (`ftlogo.cgi`):**
   * Uploads custom PNG logos (`logo.png`, max 60 KB).
3. **Color Customization (`ftcolor.cgi`):**
   * Exposes 21 customizable hex color parameters (`color0` through `color20`) controlling the CSS layout.

---

## 7. SPI Flash Memory Map (2 MB Winbond / FM25Q16)

```text
+-----------------------+-------------------------------------------------------+
| Flash Offset Range    | Content Description                                   |
+-----------------------+-------------------------------------------------------+
| 0x000000 - 0x001000   | Hardware Boot Vector (Write-Protected)                |
| 0x001002 - 0x003FFF   | Bootloader Stage 1 / Exception Vectors                |
| 0x004000 - 0x01BFFF   | Realtek Failsafe Bootloader & HTTP Recovery Server     |
| 0x01C000 - 0x01FFFF   | Runtime Kernel Bank 0 (Common Segment)                |
| 0x020000 - 0x1FBFFF   | Runtime Kernel Banks 1-3 & Compressed Web Assets      |
| 0x1FC000 - 0x1FDFFF   | NVRAM Sector (configCache struct, 2,702 bytes)        |
| 0x1FE000 - 0x1FFFFF   | Flash Security Sector & Chip Unique ID                |
+-----------------------+-------------------------------------------------------+
```

### Emergency Failsafe Recovery:
The Realtek bootloader (`0x004000–0x01BFFF`) is write-protected in hardware and cannot be erased by bad runtime kernels. If a firmware flash fails:
1. Connect via serial (`COM4` @ 57600 baud).
2. Hold `ESC` at power-on.
3. Access `http://192.168.1.1/` to upload a valid image via HTTP.
