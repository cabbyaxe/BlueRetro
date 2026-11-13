# BlueRetro for the iBlueControlMod (GameCube internal)

This branch builds [BlueRetro](https://github.com/darthcloud/BlueRetro) **v25.04** for the
Chinese **"iBlueControlMod" NGC** internal Bluetooth board. That board runs an old BlueRetro fork
(about v1.9, from early 2024), and its seller warns that flashing stock BlueRetro bricks it.

v25.04 brings everything upstream added since then, including the fix for
[darthcloud/BlueRetro#146](https://github.com/darthcloud/BlueRetro/issues/146)
(input lag with several Switch controllers connected).

> Not affiliated with darthcloud/BlueRetro or the board's seller. Upstream BlueRetro is archived,
> so please report problems with this build **here**, not upstream.

## Why stock BlueRetro breaks this board

The board uses the "HW2" design, but with some pins moved and a different kind of power control.
This was found by disassembling the vendor firmware and confirmed on real hardware:

| Function | Upstream HW2 | iBlueControlMod |
|---|---|---|
| Pairing / boot button | GPIO0 | **GPIO16** |
| Console power-off | GPIO16 | **GPIO17** |
| Error LED | GPIO17 | **GPIO22** |
| Port LEDs P1–P4 | 2, 4, 12, 15 | **15, 4, 2, 12** |
| Console power (GPIO13) | 20 ms pulse, imitating the power button | **held high = console powered** (it switches the console's power) |

Unchanged from HW2: GC data 19/5/26/27, port sense 35/36/32/33, power sense 39, reset 14.

Two things go wrong with stock BlueRetro:
- It drives the board's power-off line as if it were an LED.
- It never holds the console's power on, so the GameCube won't start at all, not even
  Picoboot or an HDMI mod.

This build changes only those pins and the power behaviour. You can read the code changes with
`git diff v25.04 ibluecontrol -- main`.

## Is my board supported?

- PCB marked **V1.1**, with an ESP32-WROOM-32 module and an unpopulated 6-pin header next to it.
- `ibc_flash.py check` reads the board's partition table and **refuses to write** unless it
  matches the known layout (factory, ota_0, ota_1, 1 MB each). If yours is different, open an
  issue with the `check` output.

## What you need

- A **Raspberry Pi Pico or Pico 2** used as a USB-to-serial adapter (recommended). Its bridge
  firmware also puts the ESP32 into flashing mode by itself, so no shorting pins by hand.
  Any 3.3 V USB-to-UART adapter also works, but then you put the board into flashing mode yourself.
- 6 wires and a soldering iron for the header.
- Python 3.9+ with `pip install esptool` (v4.7 or newer; tested with 5.2).

**Remove the board from the console, or at least unplug the console, before wiring it up.** The
Pico powers the board over 3V3.

## Wiring (Pico)

Header pins on the board, by function. Check yours with a multimeter's continuity mode against
the ESP32-WROOM-32 module pins:

| Header | Signal | ESP32 module pin | Pico pin |
|---|---|---|---|
| H1 | GND | 1 (GND) | any GND (e.g. pin 38) |
| H2 | TXD0 | 35 | **GP1** (pin 2), UART RX |
| H3 | RXD0 | 34 | **GP0** (pin 1), UART TX |
| H4 | EN | 3 | **GP2** (pin 4) |
| H5 | IO0 | 25 | **GP3** (pin 5) |
| H6 | 3V3 | 2 | **3V3(OUT)** (pin 36) |

The Pico only ever pulls EN and IO0 low or lets them go; it never drives them high.

## Steps

1. **Load the bridge firmware onto the Pico.** Hold BOOTSEL while plugging it in, then copy the
   file onto the `RPI-RP2` drive:
   - Pico (RP2040): `esp_bridge_PICO_rp2040.uf2`
   - Pico 2 (RP2350): `esp_bridge_PICO2_rp2350.uf2`

   Both are on the release page, and the source is in `ibluecontrol/pico-bridge`. A serial port
   (COMx or `/dev/ttyACMx`) appears.
2. **Back up the whole flash** (strongly recommended). It takes a few minutes:
   ```
   python safe_read_flash.py COM6 my_ibc_backup.bin 0x400000
   ```
   Keep this file private: it contains your controller pairing keys.
3. **Check the board:**
   ```
   python ibc_flash.py check COM6
   ```
   You should see `Partition table: OK` and the factory slot holding `BlueRetro`.
4. **Flash:**
   ```
   python ibc_flash.py flash COM6 BlueRetro_ibluecontrol_gamecube.bin
   ```
   This writes BlueRetro into the spare **ota_0** slot and selects it for boot. The vendor
   firmware stays in the **factory** slot, and the bootloader and partition table are untouched.
5. Disconnect the Pico (at least the 3V3 wire), put the board back in, and power on. Console power
   comes on about one second after you flip the switch, just as with the vendor firmware.
6. Pair a controller using the board's button. Configuration works through the normal BlueRetro
   web config (Web Bluetooth).

### Going back to the vendor firmware

```
python ibc_flash.py rollback COM6   # boot the vendor firmware
python ibc_flash.py restore  COM6   # boot BlueRetro again
```

### Without a Pico (plain USB-UART adapter)

Wire GND, TX→RXD0, RX←TXD0 and 3V3. Your adapter must be able to supply about 500 mA on 3V3;
many can't, so use a separate 3.3 V supply if needed. Hold **IO0 to GND**, briefly short
**EN to GND**, release IO0, then run any command with `--no-reset`. Repeat this when the script
asks.

## Serial console / troubleshooting

The firmware prints its log on TXD0 at **921600** baud. A healthy boot shows:

```
App version:      v25.04... gamecube
# internal_flag_init: Internal adapter
# sys_mgr_init: iBlueControl console power on (GPIO13 held high)
```

On some setups, long continuous reads from the ESP32 lose bytes. `safe_read_flash.py` reads in
1 KiB blocks with an MD5 check on each, for that reason. Writing with esptool is fine; it checks
a hash after writing.

## Building from source

The build is the same as upstream BlueRetro (ESP-IDF image `ghcr.io/darthcloud/idf-blueretro:v5.5.0_2024-12-02`):

```
cp configs/ibluecontrol/gamecube sdkconfig
BR_HW=_ibluecontrol BR_SYS=_gamecube idf.py reconfigure build
# -> build/BlueRetro_ibluecontrol_gamecube.bin
```

GitHub Actions (`.github/workflows/ibluecontrol.yml`) builds every push. Pushing an `ibc-*` tag
creates a draft release.

## License

BlueRetro is Apache-2.0 © Jacques Gagnon (darthcloud). The iBlueControl changes are marked in the
source and released under the same license.
