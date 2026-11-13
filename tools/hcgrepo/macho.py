"""Minimal, read-only Mach-O inspection: architectures, linked libraries, code signature and
embedded entitlements. Enough to review a jailbreak package, not a general Mach-O library."""

from __future__ import annotations

import plistlib
import struct
from dataclasses import dataclass, field

MH_MAGIC = 0xFEEDFACE
MH_MAGIC_64 = 0xFEEDFACF
FAT_MAGIC = 0xCAFEBABE
FAT_MAGIC_64 = 0xCAFEBABF

CPU_TYPE_ARM = 12
CPU_TYPE_ARM64 = 0x0100000C
CPU_TYPE_X86_64 = 0x01000007
CPU_SUBTYPE_MASK = 0x00FFFFFF
CPU_SUBTYPE_ARM64E = 2

LC_LOAD_DYLIB = 0xC
LC_LOAD_WEAK_DYLIB = 0x80000018
LC_REEXPORT_DYLIB = 0x8000001F
LC_LAZY_LOAD_DYLIB = 0x20
LC_LOAD_UPWARD_DYLIB = 0x80000023
LC_RPATH = 0x8000001C
LC_CODE_SIGNATURE = 0x1D
LC_ENCRYPTION_INFO = 0x21
LC_ENCRYPTION_INFO_64 = 0x2C

CSMAGIC_EMBEDDED_SIGNATURE = 0xFADE0CC0
CSMAGIC_CODEDIRECTORY = 0xFADE0C02
CSMAGIC_EMBEDDED_ENTITLEMENTS = 0xFADE7171
CSSLOT_CODEDIRECTORY = 0
CSSLOT_ENTITLEMENTS = 5

FILETYPES = {1: "object", 2: "executable", 6: "dylib", 8: "bundle", 0xB: "dsym"}


class MachOError(ValueError):
    pass


@dataclass
class Slice:
    arch: str
    filetype: str
    dylibs: list[str] = field(default_factory=list)
    rpaths: list[str] = field(default_factory=list)
    signed: bool = False
    entitlements: dict | None = None
    encrypted: bool = False


@dataclass
class MachO:
    slices: list[Slice]

    @property
    def arches(self) -> list[str]:
        return [s.arch for s in self.slices]


def is_macho(head: bytes) -> bool:
    if len(head) < 4:
        return False
    be = struct.unpack(">I", head[:4])[0]
    le = struct.unpack("<I", head[:4])[0]
    # FAT_MAGIC collides with Java class files; those are not expected in iOS packages.
    return be in (FAT_MAGIC, FAT_MAGIC_64) or le in (MH_MAGIC, MH_MAGIC_64)


def arch_name(cputype: int, cpusubtype: int) -> str:
    sub = cpusubtype & CPU_SUBTYPE_MASK
    if cputype == CPU_TYPE_ARM64:
        return "arm64e" if sub == CPU_SUBTYPE_ARM64E else "arm64"
    if cputype == CPU_TYPE_ARM:
        return {9: "armv7", 11: "armv7s", 12: "armv7k", 6: "armv6"}.get(sub, f"arm(sub {sub})")
    if cputype == CPU_TYPE_X86_64:
        return "x86_64"
    if cputype == 7:
        return "i386"
    return f"cpu({cputype:#x})"


def parse(data: bytes) -> MachO:
    if len(data) < 8:
        raise MachOError("file too small for a Mach-O header")
    be = struct.unpack(">I", data[:4])[0]
    if be in (FAT_MAGIC, FAT_MAGIC_64):
        n = struct.unpack(">I", data[4:8])[0]
        if n == 0 or n > 32:
            raise MachOError(f"implausible fat arch count {n}")
        slices = []
        entry = 20 if be == FAT_MAGIC else 32
        for i in range(n):
            off = 8 + i * entry
            if be == FAT_MAGIC:
                _ct, _cs, offset, size, _al = struct.unpack(">iiIII", data[off:off + 20])
            else:
                _ct, _cs, offset, size, _al, _r = struct.unpack(">iiQQII", data[off:off + 32])
            if offset + size > len(data):
                raise MachOError("fat slice extends past end of file (truncated?)")
            slices.append(_parse_thin(data[offset:offset + size]))
        return MachO(slices)
    return MachO([_parse_thin(data)])


def _parse_thin(data: bytes) -> Slice:
    if len(data) < 28:
        raise MachOError("slice too small")
    magic = struct.unpack("<I", data[:4])[0]
    if magic == MH_MAGIC_64:
        hdr = 32
    elif magic == MH_MAGIC:
        hdr = 28
    else:
        raise MachOError(f"bad Mach-O magic {magic:#x} (big-endian Mach-O is not supported)")
    _m, cputype, cpusubtype, filetype, ncmds, sizeofcmds, _flags = struct.unpack("<IiiIIII", data[:28])
    if hdr + sizeofcmds > len(data):
        raise MachOError("load commands extend past end of file (truncated?)")
    sl = Slice(arch=arch_name(cputype, cpusubtype), filetype=FILETYPES.get(filetype, f"type {filetype}"))
    off = hdr
    for _ in range(ncmds):
        if off + 8 > hdr + sizeofcmds:
            raise MachOError("malformed load command table")
        cmd, cmdsize = struct.unpack("<II", data[off:off + 8])
        if cmdsize < 8 or off + cmdsize > hdr + sizeofcmds:
            raise MachOError("malformed load command size")
        body = data[off:off + cmdsize]
        if cmd in (LC_LOAD_DYLIB, LC_LOAD_WEAK_DYLIB, LC_REEXPORT_DYLIB, LC_LAZY_LOAD_DYLIB, LC_LOAD_UPWARD_DYLIB):
            sl.dylibs.append(_lc_str(body, struct.unpack("<I", body[8:12])[0]))
        elif cmd == LC_RPATH:
            sl.rpaths.append(_lc_str(body, struct.unpack("<I", body[8:12])[0]))
        elif cmd == LC_CODE_SIGNATURE:
            dataoff, datasize = struct.unpack("<II", body[8:16])
            if dataoff + datasize > len(data):
                raise MachOError("code signature extends past end of file (truncated?)")
            sl.signed, sl.entitlements = _parse_signature(data[dataoff:dataoff + datasize])
        elif cmd in (LC_ENCRYPTION_INFO, LC_ENCRYPTION_INFO_64):
            cryptid = struct.unpack("<I", body[16:20])[0]
            sl.encrypted = cryptid != 0
        off += cmdsize
    return sl


def _lc_str(body: bytes, offset: int) -> str:
    raw = body[offset:]
    return raw.split(b"\0", 1)[0].decode("utf-8", "replace")


def _parse_signature(blob: bytes) -> tuple[bool, dict | None]:
    if len(blob) < 12:
        return False, None
    magic, _length, count = struct.unpack(">III", blob[:12])
    if magic != CSMAGIC_EMBEDDED_SIGNATURE:
        return False, None
    has_cd = False
    ents = None
    for i in range(min(count, 64)):
        typ, off = struct.unpack(">II", blob[12 + i * 8:20 + i * 8])
        if off + 8 > len(blob):
            continue
        bmagic, blen = struct.unpack(">II", blob[off:off + 8])
        if typ == CSSLOT_CODEDIRECTORY and bmagic == CSMAGIC_CODEDIRECTORY:
            has_cd = True
        elif typ == CSSLOT_ENTITLEMENTS and bmagic == CSMAGIC_EMBEDDED_ENTITLEMENTS:
            try:
                ents = plistlib.loads(blob[off + 8:off + blen])
            except Exception as exc:  # noqa: BLE001 - surface as a review problem
                raise MachOError(f"embedded entitlements are not a valid plist: {exc}") from exc
    return has_cd, ents


# ---------------------------------------------------------------------------- test helper


def build_test_macho(arch: str = "arm64", entitlements: dict | None = None, dylibs: list[str] | None = None,
                     signed: bool = True, filetype: int = 2) -> bytes:
    """Build a tiny, structurally valid (not runnable) thin Mach-O for tests."""
    cputype, sub = {"arm64": (CPU_TYPE_ARM64, 0), "arm64e": (CPU_TYPE_ARM64, 2),
                    "x86_64": (CPU_TYPE_X86_64, 3)}[arch]
    cmds = b""
    ncmds = 0
    for d in dylibs or ["/usr/lib/libSystem.B.dylib"]:
        name = d.encode() + b"\0"
        name += b"\0" * (-(24 + len(name)) % 8)
        cmds += struct.pack("<IIIIII", LC_LOAD_DYLIB, 24 + len(name), 24, 0, 0, 0) + name
        ncmds += 1
    sig = b""
    if signed:
        cd = struct.pack(">II", CSMAGIC_CODEDIRECTORY, 8)
        blobs = [(CSSLOT_CODEDIRECTORY, cd)]
        if entitlements is not None:
            x = plistlib.dumps(entitlements)
            blobs.append((CSSLOT_ENTITLEMENTS, struct.pack(">II", CSMAGIC_EMBEDDED_ENTITLEMENTS, 8 + len(x)) + x))
        index = b""
        payload = b""
        base = 12 + 8 * len(blobs)
        for typ, b in blobs:
            index += struct.pack(">II", typ, base + len(payload))
            payload += b
        sig = struct.pack(">III", CSMAGIC_EMBEDDED_SIGNATURE, base + len(payload), len(blobs)) + index + payload
        ncmds += 1
        cmds_len = len(cmds) + 16
        dataoff = 32 + cmds_len + 16  # some padding before the signature
        cmds += struct.pack("<IIII", LC_CODE_SIGNATURE, 16, dataoff, len(sig))
    header = struct.pack("<IiiIIIII", MH_MAGIC_64, cputype, sub, filetype, ncmds, len(cmds), 0, 0)
    body = header + cmds
    if signed:
        body += b"\0" * 16 + sig
    return body
