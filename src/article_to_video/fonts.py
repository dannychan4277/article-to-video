"""讀字型檔（WOFF1／TTF／OTF）的 cmap：這個字型有哪些字。只用標準庫。

用途：燒字幕前確認旁白的每個字都在外掛附帶的字幕字型裡；缺字時 libass 會偷偷換成系統字型，
換台電腦畫面就不一樣，所以要先擋下來。
"""

from __future__ import annotations

import functools
import struct
import zlib
from pathlib import Path

__all__ = ["font_codepoints"]


def _tables(blob: bytes) -> dict[bytes, bytes]:
    sig = blob[:4]
    out: dict[bytes, bytes] = {}
    if sig == b"wOFF":
        num = struct.unpack(">H", blob[12:14])[0]
        for i in range(num):
            tag, off, comp, orig, _ = struct.unpack(">4sIIII", blob[44 + 20 * i: 64 + 20 * i])
            raw = blob[off: off + comp]
            out[tag] = zlib.decompress(raw) if comp < orig else raw
        return out
    if sig in (b"\x00\x01\x00\x00", b"OTTO", b"true"):
        num = struct.unpack(">H", blob[4:6])[0]
        for i in range(num):
            tag, _, off, length = struct.unpack(">4sIII", blob[12 + 16 * i: 28 + 16 * i])
            out[tag] = blob[off: off + length]
        return out
    raise ValueError(f"不認得的字型格式（開頭 {sig!r}；只支援 WOFF1、TTF、OTF）")


def _format4(t: bytes, off: int) -> set[int]:
    seg2 = struct.unpack(">H", t[off + 6: off + 8])[0]
    n = seg2 // 2
    ends = struct.unpack(f">{n}H", t[off + 14: off + 14 + seg2])
    starts_off = off + 16 + seg2
    starts = struct.unpack(f">{n}H", t[starts_off: starts_off + seg2])
    deltas = struct.unpack(f">{n}h", t[starts_off + seg2: starts_off + 2 * seg2])
    ro_off = starts_off + 2 * seg2
    ranges = struct.unpack(f">{n}H", t[ro_off: ro_off + seg2])
    cps: set[int] = set()
    for k in range(n):
        for c in range(starts[k], ends[k] + 1):
            if c == 0xFFFF:
                continue
            if ranges[k] == 0:
                g = (c + deltas[k]) & 0xFFFF
            else:
                p = ro_off + 2 * k + ranges[k] + 2 * (c - starts[k])
                g = struct.unpack(">H", t[p: p + 2])[0]
                if g:
                    g = (g + deltas[k]) & 0xFFFF
            if g:
                cps.add(c)
    return cps


def _format12(t: bytes, off: int) -> set[int]:
    n = struct.unpack(">I", t[off + 12: off + 16])[0]
    cps: set[int] = set()
    for k in range(n):
        start, end, glyph = struct.unpack(">III", t[off + 16 + 12 * k: off + 28 + 12 * k])
        cps.update(c for c in range(start, end + 1) if glyph + (c - start))
    return cps


@functools.cache
def font_codepoints(path: str | Path) -> frozenset[int]:
    """字型裡有字形（glyph 不是 0）的所有 Unicode 碼位。"""
    cmap = _tables(Path(path).read_bytes()).get(b"cmap")
    if cmap is None:
        raise ValueError(f"{path} 沒有 cmap 表")
    num = struct.unpack(">H", cmap[2:4])[0]
    subs: dict[tuple[int, int], int] = {}
    for i in range(num):
        pid, eid, off = struct.unpack(">HHI", cmap[4 + 8 * i: 12 + 8 * i])
        subs[(pid, eid)] = off
    cps: set[int] = set()
    for key in ((3, 10), (0, 4), (0, 6), (3, 1), (0, 3), (0, 1), (0, 0)):
        off = subs.get(key)
        if off is None:
            continue
        fmt = struct.unpack(">H", cmap[off: off + 2])[0]
        if fmt == 12:
            cps |= _format12(cmap, off)
        elif fmt == 4:
            cps |= _format4(cmap, off)
    return frozenset(cps)
