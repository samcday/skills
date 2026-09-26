---
name: lab-relay
description: Hard power-cycle the DragonBoard 410c (fastboot bc72e60) through the lab USB relay on sam-desktop, or switch any relay channel. Use when the DB410c is wedged and its UART cannot recover it, or when asked to cut, restore or cycle lab board power.
---

# Lab relay

The Rust tool beside this file drives a dcttech `USBRelay4` HID module
(16c0:05df, bought as Phipps Electronics' "4 Channel 5V Low Level USB Relay
Module"): a V-USB ATmega driving four Songle SRD-05VDC-SL-C relays, whose
contacts are rated 10 A at 30 V DC. It runs entirely from USB. Its barrel jack
and blue 2-pin terminal are an optional external supply through an unmarked
regulator; leave them unused.

Run it as `cargo run -q --release --manifest-path
~/.claude/skills/lab-relay/Cargo.toml -- COMMAND`, where `COMMAND` is one of:

```text
status
on N
off N
pulse N [SECONDS]
```

The first run builds it. Every command prints the coil states, such as
`1:off 2:off 3:off 4:off`, and checks each switch against the module's own
report. `on` energises a coil: NO closes and NC opens. All coils drop when the
module loses USB power. A pulse blocks every signal it can before the coil
closes. The first signal to arrive ends the pulse early, releases the coil, and
exits 128 plus the signal number. Only SIGKILL can leave a coil on; SIGSTOP
holds it on until SIGCONT.

| Channel | Load |
|---|---|
| 1 | DB410c 12 V brick, positive lead through COM/NC: `on 1` cuts the board's power, `off 1` restores it |
| 2–4 | unassigned |

Channel 1 is the relay nearest the USB-B socket, and its terminals read NO1,
COM1, NC1 from that end. Sam metered it on 2026-09-23: COM1–NC1 conducts with
the coil off, COM1–NO1 with it on.

## Power-cycle the DB410c

```sh
cargo run -q --release --manifest-path ~/.claude/skills/lab-relay/Cargo.toml -- pulse 1 5
timeout 60 sh -c 'until fastboot devices | grep -q bc72e60; do sleep 1; done'
```

The board returns to U-Boot's resident fastboot. Its console is
`/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_A5069RR4-if00-port0` (115200 8N1),
which shows SBL1 and then the U-Boot banner. A cycle destroys whatever runs on
the board, including RAM-only liveboot state.

Status (2026-09-23): all four coils switch and click, and channel 1's contacts
are metered. Channel 1 is not wired yet, so this procedure has not been
validated end to end.

## Device access

`/etc/udev/rules.d/70-lab-relay.rules` pins `/dev/lab-relay` to USB port
`pci-0000:67:00.0-usb-0:6:1.0` and grants the desktop session access. If the
module moves port, Sam must reinstall the rule (sudo) with the new `ID_PATH`
from `udevadm info -q property -n /dev/hidrawN`:

```
SUBSYSTEM=="hidraw", ENV{ID_PATH}=="pci-0000:67:00.0-usb-0:6:1.0", ATTRS{idVendor}=="16c0", ATTRS{idProduct}=="05df", SYMLINK+="lab-relay", TAG+="uaccess"
```
