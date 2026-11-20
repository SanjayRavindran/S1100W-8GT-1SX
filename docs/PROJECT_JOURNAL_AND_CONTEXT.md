# S1100W-8GT-1SX Project Journal, Engineering Lessons & Operational Context

This document captures the end-to-end engineering context, verified hardware observations, pitfalls discovered, CI/CD design decisions, and operational runbooks accumulated across the lifecycle of the **Hasivo / Realtek S1100W-8GT-1SX** firmware project so any developer or AI assistant can resume work immediately without prior chat history.

---

## 1. Confirmed Hardware & Live Device Profile

- **Model & Variant:** Hasivo `S1100W-8GT-1SX` (**non-PoE physical variant**).
  - The board omits the PoE PSE controller chips, I2C power-budget daughterboard, and transformers.
  - Although stock vendor firmware includes PoE UI stubs, open-source builds do not need PSE I2C polling (`HASIVO_S1100WP_8GT_1SX_SE`, whose board definition maps to `pcb/S1100W_8GT_1SX_SE_V1_05.c` / `PCB_SWTG118AS_V2_0_16029.c`).
- **Switching Core & MCU:** Realtek `RTL8373 / RTL8372` (`Ver8373_72: C`), enhanced 8-bit MCS-51 (`DW8051`) @ `125 MHz` with 64 KB Keil/SDCC banked memory segments and `2 MB` SPI NOR Flash (`0x200000` bytes).
- **Verified Pin & Port Map:**
  - **Ports 1–8:** 2.5GbE RJ45 via integrated RTL8221B-core multi-gig PHYs (verified live: Port 7 negotiated `1000M full duplex`; Port 1 hardware TDR `cabletest` accurately resolved open-pair distances in meters).
  - **Port 9:** 10GbE SFP+ on SerDes 1 (`sds1`), SFP presence detect on `GPIO30` (active low), LOS on `GPIO37` (active high).
  - **Port 0:** Internal CPU management port.
  - **Reset Button:** Front panel pin on `GPIO54` (upstream PR `#824` in `3762ee1` added internal pull-up initialization).
- **Serial UART Header (`3.3V TTL`, `8-N-1`):**
  - Pin 1 (Square): Switch TX $\rightarrow$ Adapter RX
  - Pin 2: GND $\rightarrow$ Adapter GND
  - Pin 3: Switch RX $\rightarrow$ Adapter TX
  - Pin 4: `3.3V VCC` — **Leave disconnected** to prevent back-powering.
  - **Critical Baud-Rate Transition:**
    - Realtek Bootloader & stock vendor firmware: **`57600` baud**.
    - RTLPlayground open-source runtime: **`115200` baud** (`TH1 = 0xef` at `125 MHz`, `-0.27%` error). Garbled output at `57600` after flashing indicates the switch successfully booted into RTLPlayground at `115200`.

---

## 2. Key Lessons from Vendor Firmware Research (`SEV1.9`)

1. **Why Linux / OpenWrt Is Impossible:**
   - The 8-bit MCS-51 core lacks an MMU, uses 64 KB banked code/data segments, and relies on closed-source Realtek 2.5G/10G SerDes calibration sequences. Replacing it requires a native 8051 banked RTOS (`logicog/RTLPlayground` via patched SDCC 4.6.0).
2. **Two-Stage Persistence Bug ("Connection Lost on Apply"):**
   - Applying IP/DHCP changes via `/ip.cgi` updates RAM `configCache` (`0x1FE000` mirror) immediately, dropping active sessions on the old IP **without** writing SPI flash.
   - Unless `/save.cgi` (`SalFlshCopyCacheToFlsh`) is triggered on the newly acquired IP, rebooting reverts to the old NVRAM setting.
3. **DHCP Option 12 Gap:**
   - Vendor LWPS omits DHCP Option 12 (`Host Name`) in `DHCPDISCOVER`, preventing home routers from registering a DNS name automatically.
4. **Surgical Localization vs. Open-Source Replacement:**
   - Byte-for-byte localization of the vendor image (clearing `langch`, hardening the `Hs2021cfgmg` serial backdoor, re-signing via `tools/calcsum.py`) proved effective as an interim step, but compiling native `RTLPlayground` eliminated proprietary bloat, fixed DHCP Option 12 (`hostname S1100W`), added live traffic telemetry, TDR diagnostics, and single-page dark/light HTML5 UI.
5. **Hardware Write-Protected Failsafe Recovery:**
   - Flash region `0x004000–0x01BFFF` houses the Realtek bootloader and HTTP failsafe server (`192.168.1.1`).
   - Holding `ESC` at `57600` baud during power-on enters loader mode (`http://192.168.1.1/httpupg.cgi?cmd=fw_upgrade`), making runtime flashing unbrickable without an external programmer.

---

## 3. Critical RTLPlayground Build & Verification Gotchas

Lessons learned by debugging failed local and GitHub Actions builds against `logicog/RTLPlayground`:

1. **Makefile Targets & Variables:**
   - **No `make toolchain` target exists:** Running `make toolchain` fails immediately. Top-level `make` automatically builds `$(SDCC)` via `sh sdcc/build.sh $(abspath $(SDCC_PREFIX))` if absent.
   - **Use `MACHINE=`, not `BOARD=`:** Build with `make CI=1 MACHINE=HASIVO_S1100WP_8GT_1SX_SE CONFIG=config_s1100w.txt`.
   - **Startup Config Embedding:** `CONFIG=config_s1100w.txt` is injected by `fileadder` at both `0x6f000` (default config) and `0x70000` (active startup config). Keeping [`config/s1100w.txt`](../config/s1100w.txt) in git keeps CI and local builds aligned.
2. **`installer/Makefile` Parallel Race & `updatebuilder` CLI:**
   - `installer/updatebuilder` expects `argp` flags (`-i <rtlinstaller.ihx> -o <output> <input>`), **not** positional board arguments.
   - **Always invoke `make -C installer` single-threaded (never `-j`):** In upstream `installer/Makefile`, `$(BUILDDIR)/rtlplayground_oem_upgrade.bin` does not declare `$(BUILDDIR)/updatebuilder` as a prerequisite, so parallel `make -j` can invoke `./output/updatebuilder` before `gcc` finishes compiling it.
   - Top-level `make` must run first with default `OUTPUT` so it creates the symlink `output/rtlplayground.bin -> HASIVO_S1100WP_8GT_1SX_SE/rtlplayground-...bin`, which `installer/Makefile` reads from `../output/rtlplayground.bin`.
3. **Mandatory Host Dependencies (`debian:trixie`):**
   - Upstream `Dockerfile` and CI build on `debian:trixie` (`Debian 13`). Using `container: image: debian:trixie` avoids host GCC/glibc drift on `ubuntu-latest`.
   - Required tools verified in CI: `build-essential`, `binutils`, `bison`, `flex`, `libboost-dev`, `libboost-graph-dev`, `libjson-c-dev`, `zlib1g-dev`, **`pkg-config`** (required by upstream `tools/Makefile` to resolve zlib flags for `fileadder`), `patch`, `bzip2`, `xz-utils`, `zstd`, `xxd`, `python3`, `python-is-python3`, `curl`, `ca-certificates`, `git`, `jq`.
4. **Distinguishing the Two Output Binaries & Checksum Validators:**
   - **`...-FULL-FLASH.bin` (`524,288` B):** Raw 512 KB SPI image from `output/rtlplayground.bin` for external SOIC-8 flashers. Validated via upstream `tools/output/HASIVO_S1100WP_8GT_1SX_SE/crc_calculator -v` (expects `CRC16 is: 0xb001` / `Checksum OK`). *Note: `tools/calcsum.py` intentionally rejects this file (`Invalid magic value`) because it lacks the 20-byte OEM update header.*
   - **`...-OEM-UPGRADE.bin` (`~540,710` B):** Bootloader/Web-UI flashable container from `installer/output/rtlplayground_oem_upgrade.bin` wrapping `rtlinstaller.ihx`, dual 20-byte headers (`0x12345678` / `0x332255ff` at `0x00` and `0x4012`), and payload. Validated via [`tools/calcsum.py`](../tools/calcsum.py) (checks magic, size parity, primary/secondary header equality, header sum, and payload sum).

---

## 4. Open-Source Firmware Runtime Operation (`RTLPlayground`)

Verified live on the switch (`v0.1.0-948c828`, later superseded in GitHub Releases by `v0.1.0-3762ee1`):

- **Web Console (`http://<ip>/`):**
  - Serves gzip-compressed single-page HTML5/CSS3/JS UI with auto dark/light themes (`prefers-color-scheme`) and browser language detection.
  - Authentication is password-only (`<input name="pwd">` $\rightarrow$ `POST /login` $\rightarrow$ `session` cookie with `SameSite=Strict`). Default compiled password from `config/s1100w.txt` is `admin`.
- **Serial CLI (`115200` baud, prompt `> `):**
  - Persistence command is **`flash save`** (writes compressed config to SPI flash security sector 1) — *not* `save`, `write`, or `commit`.
  - Key verified commands: `version`, `ip`, `mac`, `hostname [name]`, `passwd <pw>`, `port <1-8> show`, `port <1-8> cabletest` / `cabletest show`, `sfp`, `vlan show`, `stp status`, `lldp`, `flash save`, `reboot`.
  - `help` and `?` return `Error: Unknown command`; commands are dispatched in upstream `cmd_parser.c`.

---

## 5. CI/CD Pipeline Architecture & Release Policy

- **Git Tree Policy:** Zero `.bin` or firmware build blobs are tracked in git; compiled images live exclusively in [GitHub Releases](../../releases) and workflow artifacts.
- **Weekly `main`-Branch Tracking ([`tools/check_upstream_release.py`](../tools/check_upstream_release.py)):**
  - Because `logicog/RTLPlayground` rarely publishes GitHub releases (its legacy `v0_1_alpha` tag predates `HASIVO_S1100WP_8GT_1SX_SE` and `installer/`), the pipeline tracks upstream `main` on a weekly schedule (Sundays at `02:00 UTC`, `cron: '0 2 * * 0'`).
  - `check_upstream_release.py` authenticates with `GITHUB_TOKEN`, resolves the exact `main` (or manual `target_ref`) commit SHA via the GitHub REST API, verifies `machine/HASIVO_S1100WP_8GT_1SX_SE.h` and `installer/Makefile` exist at that commit, extracts `VERSION` from upstream `Makefile`, and forms `v<VERSION>-<short_sha>`.
  - Scheduled runs skip cleanly (`should_build=false`, exit `0`) if `v<VERSION>-<short_sha>` is already published (no new commits on `main`), avoiding redundant weekly builds.
- **Pinned Builds, Release Notes & Toolchain Caching:**
  - `jobs.build` clones `logicog/RTLPlayground` (`--filter=blob:none`, preserving full commit history for changelog generation) and checks out the exact pinned `commit_sha`.
  - `workflow_dispatch` defaults `target_ref` to `main` (supporting any branch, tag, or commit SHA) alongside `publish_release` and `force_build` inputs.
  - `actions/cache@v4` caches `RTLPlayground/toolchain` keyed by `sdcc-trixie-${{ hashFiles('RTLPlayground/sdcc/*') }}`, cutting build runtime from `~3m29s` (cold) to `~1m37s`–`2m13s` (cached), with a 3-attempt retry loop around `sh sdcc/build.sh`.
  - [`tools/generate_release_notes.py`](../tools/generate_release_notes.py) queries the latest prior release, computes the first-parent commit diff (`prev_sha..curr_sha`), categorizes merged PRs/commits (`S1100W-Specific Changes`, `Firmware, Networking & Web UI`, `Other Board & Hardware Support`, `Documentation, Tests & Build Tooling`), and writes `release_notes.md` at the workspace root (outside `dist/`, so `softprops/action-gh-release@v2` passes it via `body_path:` without attaching it as an asset).
- **Actions History Hygiene:** Old failed, skipped, and non-releasing `push`/`workflow_dispatch` workflow runs are periodically pruned so the Actions history reflects release-producing builds cleanly.

---

## 6. Local Workspace & Host Handoff

- **Repository Contents (Tracked in Git):**
  - [`config/s1100w.txt`](../config/s1100w.txt) — Startup board config (`hostname S1100W`, `ip dhcp`, fallback `192.168.1.28/24`, gw `192.168.1.1`, password `admin`).
  - [`.github/workflows/build-and-release.yml`](../.github/workflows/build-and-release.yml) — Weekly upstream `main` builder, `debian:trixie` compiler, dual verifier, release notes generator, and GitHub Release publisher.
  - [`tools/calcsum.py`](../tools/calcsum.py), [`tools/check_upstream_release.py`](../tools/check_upstream_release.py), & [`tools/generate_release_notes.py`](../tools/generate_release_notes.py) — Container checksum verifier, upstream commit/compatibility checker, and automated release-notes generator.
  - `docs/` — [`ORIGINAL_FIRMWARE_ANALYSIS.md`](ORIGINAL_FIRMWARE_ANALYSIS.md), [`FIRMWARE_SPECIFICATION.md`](FIRMWARE_SPECIFICATION.md), [`OPENSOURCE_FIRMWARE_GUIDE.md`](OPENSOURCE_FIRMWARE_GUIDE.md), and this journal.
- **Local-Only Gitignored Files (Preserved on Windows Workstation):**
  - Stock & patched reference images (`S1100W-8GT-1SX-SEV1.9*.bin`), NVRAM dump (`config_backup.bin`), UART captures (`*.log`, `*.bin`), and the initially flashed `948c828` build pair under `backups/`.
- **Git / GitHub Operations via Linux Host (`192.168.1.15`):**
  - The Windows workstation does not have `git` or `gh` installed locally. Git commits, pushes, and `gh` CLI operations are performed over SSH on `192.168.1.15` (`zero@192.168.1.15`, clone path `~/S1100W-8GT-1SX`, repo-local `user.name`/`user.email` set to `Sanjay Ravindran <mail2snjyrvndrn@gmail.com>`).
  - Never copy local `.bin`, `.log`, or `backups/` files into `~/S1100W-8GT-1SX`.
- **CI Independence:** All firmware builds and weekly releases run autonomously on GitHub Actions without requiring a dedicated build host; local Windows only needs Python 3 for `tools/*.py` and `pyserial` (`COM4`) for UART console access.
