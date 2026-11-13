"""
iBlueControlMod (NGC) BlueRetro flasher.

Writes BlueRetro to the board's *ota_0* slot and points the bootloader at it.
The vendor firmware in the *factory* slot, the bootloader and the partition
table are never touched, so `rollback` always brings the vendor firmware back.

Usage (needs Python 3.9+ and `pip install esptool`):
    python ibc_flash.py check    COMx
    python ibc_flash.py flash    COMx BlueRetro_ibluecontrol_gamecube.bin
    python ibc_flash.py rollback COMx          # boot vendor firmware again
    python ibc_flash.py restore  COMx          # boot ota_0 (BlueRetro) again

Options:
    --baud N        flashing baud rate (default 460800)
    --no-reset      adapter has no EN/IO0 control: hold IO0 low, pulse EN,
                    then run the command (download mode entered by hand)
"""
import argparse
import binascii
import os
import struct
import subprocess
import sys
import tempfile

from esptool.cmds import attach_flash, detect_chip, run_stub

OTADATA_ADDR = 0xD000
OTADATA_SIZE = 0x2000
OTA0_ADDR = 0x110000
OTA0_SIZE = 0x100000
FACTORY_ADDR = 0x10000
PART_TABLE_ADDR = 0x8000
PART_TABLE_SIZE = 0xC00
# MD5 of the partition table every known iBlueControlMod board ships with
# (identical to upstream BlueRetro partitions.csv: factory/ota_0/ota_1 1M each).
PART_TABLE_MD5 = "5cb25ebdbf7d7812626c9714213dd4b3"
APP_DESC_MAGIC = 0xABCD5432


def otadata(seq):
    """otadata image selecting ota_(seq-1); seq=None -> blank (boot factory)."""
    img = bytearray(b"\xff" * OTADATA_SIZE)
    if seq is not None:
        crc = binascii.crc32(struct.pack("<I", seq), 0xFFFFFFFF) & 0xFFFFFFFF
        entry = struct.pack("<I", seq) + b"\xff" * 20 + struct.pack("<II", 0xFFFFFFFF, crc)
        img[0:32] = entry
    return bytes(img)


def connect(args):
    before = "no-reset" if args.no_reset else "default-reset"
    esp = detect_chip(args.port, 115200, before, False, 7)
    if esp.CHIP_NAME != "ESP32":
        sys.exit(f"Not an ESP32 ({esp.CHIP_NAME}) - wrong board?")
    esp = run_stub(esp)
    attach_flash(esp)
    return esp


def app_info(esp, addr):
    hdr = esp.read_flash(addr, 0x100)
    if hdr[0] != 0xE9:
        return None
    magic, _sec, _r0, _r1, version, project = struct.unpack_from("<IIII32s32s", hdr, 0x20)
    if magic != APP_DESC_MAGIC:
        return "(unknown app)"
    v = version.split(b"\0")[0].decode(errors="replace")
    p = project.split(b"\0")[0].decode(errors="replace")
    return f"{p} version '{v}'"


def check(args, quiet=False):
    esp = connect(args)
    ok = True
    pt = esp.flash_md5sum(PART_TABLE_ADDR, PART_TABLE_SIZE)
    print(f"Chip:            {esp.get_chip_description()}")
    print(f"Partition table: {'OK (matches iBlueControlMod/BlueRetro layout)' if pt == PART_TABLE_MD5 else 'UNEXPECTED ' + pt}")
    if pt != PART_TABLE_MD5:
        ok = False
    print(f"factory slot:    {app_info(esp, FACTORY_ADDR)}")
    print(f"ota_0 slot:      {app_info(esp, OTA0_ADDR) or '(empty)'}")
    od = esp.read_flash(OTADATA_ADDR, 32)
    seq = struct.unpack_from("<I", od)[0]
    print(f"Boots:           {'factory (vendor)' if seq == 0xFFFFFFFF else f'ota slot (seq {seq})'}")
    esp.hard_reset()
    esp._port.close()
    if not ok:
        print("\nThis board does not match the expected layout. Nothing was written.\n"
              "Please open an issue with the output above instead of forcing it.")
    return ok


def esptool_write(args, pairs):
    cmd = [sys.executable, "-m", "esptool", "--chip", "esp32", "-p", args.port, "-b", str(args.baud),
           "--before", "no-reset" if args.no_reset else "default-reset", "--after", "hard-reset",
           "write-flash", "--flash-mode", "keep", "--flash-size", "keep", "--flash-freq", "keep"]
    for addr, path in pairs:
        cmd += [hex(addr), path]
    print("> " + " ".join(cmd))
    subprocess.run(cmd, check=True)


def write_otadata(args, seq):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "otadata.bin")
        with open(p, "wb") as f:
            f.write(otadata(seq))
        esptool_write(args, [(OTADATA_ADDR, p)])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["check", "flash", "rollback", "restore"])
    ap.add_argument("port")
    ap.add_argument("app", nargs="?")
    ap.add_argument("--baud", type=int, default=460800)
    ap.add_argument("--no-reset", action="store_true")
    args = ap.parse_args()

    if args.cmd == "check":
        sys.exit(0 if check(args) else 1)

    if args.cmd == "flash":
        if not args.app or not os.path.isfile(args.app):
            sys.exit("flash needs the BlueRetro_ibluecontrol_gamecube.bin path")
        data = open(args.app, "rb").read()
        if data[0] != 0xE9 or len(data) > OTA0_SIZE:
            sys.exit("That file is not an ESP32 app image that fits the 1 MB slot")
        if b"BlueRetro_ibluecontrol" not in data[:0x200]:
            sys.exit("Refusing: not an iBlueControl BlueRetro build (stock BlueRetro bricks this board)")
        if not check(args):
            sys.exit(1)
        if args.no_reset:
            input("Re-enter download mode (hold IO0, pulse EN), then press Enter...")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "otadata.bin")
            with open(p, "wb") as f:
                f.write(otadata(1))
            esptool_write(args, [(OTA0_ADDR, args.app), (OTADATA_ADDR, p)])
        print("\nDone. BlueRetro is in ota_0 and selected for boot; vendor firmware kept in factory.")
    elif args.cmd == "rollback":
        write_otadata(args, None)
        print("\nDone. The board boots the vendor (factory) firmware again.")
    elif args.cmd == "restore":
        write_otadata(args, 1)
        print("\nDone. The board boots BlueRetro (ota_0) again.")


if __name__ == "__main__":
    main()
