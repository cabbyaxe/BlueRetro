"""
Half-duplex flash reader for the iBlueControl board via the Pico bridge.

esptool's stub READ_FLASH normally keeps 64 blocks in flight, so host ACKs
overlap the ESP32's data stream; on this wiring that full-duplex traffic
causes UART framing errors. Here we use max_inflight=1 (ESP32 sends one 4 KiB
block, waits for our ACK), verify each chunk with the stub's MD5, and on any
error hard-reset + reconnect and resume from the failed chunk.

Usage: py -I safe_read_flash.py COM6 out.bin [size_hex] [baud]
"""
import hashlib
import struct
import sys
import time

from esptool.cmds import attach_flash, detect_chip, run_stub
from esptool.util import FatalError, hexify

PORT = sys.argv[1]
OUT = sys.argv[2]
SIZE = int(sys.argv[3], 16) if len(sys.argv) > 3 else 0x800000
BAUD = int(sys.argv[4]) if len(sys.argv) > 4 else 921600
CHUNK = 0x10000  # 64 KiB per READ_FLASH command, each MD5-verified
BLOCK = 0x400  # bursts >~15 ms lose bytes on this link; 1 KiB @ 921600 = 11 ms


def connect():
    esp = detect_chip(PORT, 115200, "default-reset", False, 7)
    esp = run_stub(esp)
    esp.change_baud(BAUD)
    attach_flash(esp)
    return esp


def read_chunk(esp, offset, length):
    esp.check_command(
        "read flash", esp.ESP_CMDS["READ_FLASH"],
        struct.pack("<IIII", offset, length, BLOCK, 1),
    )
    data = b""
    while len(data) < length:
        esp._port.timeout = 3
        p = esp.read()
        data += p
        if len(data) < length and len(p) < BLOCK:
            raise FatalError(f"short block {len(p):#x} at {offset + len(data):#x}")
        esp.write(struct.pack("<I", len(data)))
    if len(data) > length:
        raise FatalError("read more than expected")
    digest = esp.read()
    if len(digest) != 16:
        raise FatalError(f"bad digest frame {hexify(digest)}")
    if hashlib.md5(data).hexdigest().upper() != hexify(digest).upper():
        raise FatalError("md5 mismatch")
    return data


def main():
    buf = bytearray()
    esp = connect()
    retries = 0
    t0 = time.time()
    while len(buf) < SIZE:
        off = len(buf)
        n = min(CHUNK, SIZE - off)
        try:
            buf += read_chunk(esp, off, n)
        except Exception as e:  # noqa: BLE001
            retries += 1
            print(f"\n  chunk {off:#08x} failed ({e}); reconnecting (retry {retries})", flush=True)
            if retries > 50:
                raise
            try:
                esp._port.close()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.5)
            esp = connect()
            continue
        el = time.time() - t0
        print(f"\r  {len(buf):#09x}/{SIZE:#x}  {len(buf) / max(el, 0.01) / 1024:.1f} KiB/s  retries={retries}",
              end="", flush=True)
    with open(OUT, "wb") as f:
        f.write(buf)
    print(f"\nwrote {OUT} ({len(buf)} bytes) md5={hashlib.md5(buf).hexdigest()} retries={retries}")
    # whole-flash MD5 straight from the chip as an end-to-end check
    full = esp.flash_md5sum(0, SIZE)
    print(f"chip md5 = {full}  {'MATCH' if full == hashlib.md5(buf).hexdigest() else 'MISMATCH'}")
    esp.hard_reset()
    esp._port.close()


if __name__ == "__main__":
    main()
