"""Debian control-file parsing, version comparison and relationship parsing.

Implements the parts of deb822 / dpkg semantics this repository needs, without third-party
libraries. Version comparison follows dpkg's verrevcmp algorithm exactly.
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass

# ---------------------------------------------------------------------------- control files


class ControlError(ValueError):
    pass


def parse_stanzas(text: str) -> list[list[tuple[str, str]]]:
    """Parse deb822 text into stanzas of (field, raw value) in original order.

    The raw value keeps continuation lines verbatim (including their leading space) so that a
    field can be written back byte-for-byte.
    """
    stanzas: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    for lineno, line in enumerate(text.split("\n"), 1):
        if line.endswith("\r"):
            raise ControlError(f"line {lineno}: CR line ending")
        if line.strip() == "":
            if current:
                stanzas.append(current)
                current = []
            continue
        if line[0] in " \t":
            if not current:
                raise ControlError(f"line {lineno}: continuation line without a field")
            name, value = current[-1]
            current[-1] = (name, value + "\n" + line)
            continue
        if line.startswith("#"):
            continue
        if ":" not in line:
            raise ControlError(f"line {lineno}: expected 'Field: value', got {line[:60]!r}")
        name, _, value = line.partition(":")
        if not re.fullmatch(r"[!-9;-~][!-9;-~]*", name):
            raise ControlError(f"line {lineno}: invalid field name {name!r}")
        if any(n.lower() == name.lower() for n, _ in current):
            raise ControlError(f"line {lineno}: duplicate field {name!r}")
        current.append((name, value.strip(" \t")))
    if current:
        stanzas.append(current)
    return stanzas


def parse_control(text: str) -> list[tuple[str, str]]:
    stanzas = parse_stanzas(text)
    if len(stanzas) != 1:
        raise ControlError(f"expected exactly one stanza in control file, found {len(stanzas)}")
    return stanzas[0]


def field(fields: list[tuple[str, str]], name: str) -> str | None:
    for n, v in fields:
        if n.lower() == name.lower():
            return v
    return None


def format_stanza(fields: list[tuple[str, str]]) -> str:
    out = []
    for name, value in fields:
        if "\n" in value:
            first, rest = value.split("\n", 1)
            out.append(f"{name}: {first}".rstrip(" ") + "\n" + rest)
        else:
            out.append(f"{name}: {value}")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------- names, versions

PACKAGE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9.+-]+$")
_UPSTREAM_RE = re.compile(r"^[0-9][A-Za-z0-9.+~-]*$")
_REVISION_RE = re.compile(r"^[A-Za-z0-9.+~]+$")


@dataclass(frozen=True)
class Version:
    epoch: int
    upstream: str
    revision: str

    def __str__(self) -> str:
        s = f"{self.epoch}:" if self.epoch else ""
        s += self.upstream
        if self.revision:
            s += f"-{self.revision}"
        return s


def parse_version(text: str) -> Version:
    v = text.strip()
    if not v or any(c.isspace() for c in v):
        raise ControlError(f"invalid version {text!r}: empty or contains whitespace")
    epoch = 0
    if ":" in v:
        e, v = v.split(":", 1)
        if not e.isdigit():
            raise ControlError(f"invalid version {text!r}: epoch must be numeric")
        epoch = int(e)
    revision = ""
    if "-" in v:
        v, revision = v.rsplit("-", 1)
        if not _REVISION_RE.match(revision):
            raise ControlError(f"invalid version {text!r}: bad revision {revision!r}")
    if not _UPSTREAM_RE.match(v):
        raise ControlError(f"invalid version {text!r}: upstream must start with a digit and use only [A-Za-z0-9.+~-]")
    return Version(epoch, v, revision)


def _order(c: str) -> int:
    if c in string.digits:
        return 0
    if c in string.ascii_letters:
        return ord(c)
    if c == "~":
        return -1
    if c:
        return ord(c) + 256
    return 0


def _verrevcmp(a: str, b: str) -> int:
    i = j = 0
    while i < len(a) or j < len(b):
        first_diff = 0
        while (i < len(a) and a[i] not in string.digits) or (j < len(b) and b[j] not in string.digits):
            ac = _order(a[i]) if i < len(a) else 0
            bc = _order(b[j]) if j < len(b) else 0
            if ac != bc:
                return ac - bc
            i += 1
            j += 1
        while i < len(a) and a[i] == "0":
            i += 1
        while j < len(b) and b[j] == "0":
            j += 1
        while i < len(a) and a[i] in string.digits and j < len(b) and b[j] in string.digits:
            if not first_diff:
                first_diff = ord(a[i]) - ord(b[j])
            i += 1
            j += 1
        if i < len(a) and a[i] in string.digits:
            return 1
        if j < len(b) and b[j] in string.digits:
            return -1
        if first_diff:
            return first_diff
    return 0


def compare_versions(a: str | Version, b: str | Version) -> int:
    va = a if isinstance(a, Version) else parse_version(a)
    vb = b if isinstance(b, Version) else parse_version(b)
    if va.epoch != vb.epoch:
        return -1 if va.epoch < vb.epoch else 1
    r = _verrevcmp(va.upstream, vb.upstream)
    if r:
        return -1 if r < 0 else 1
    r = _verrevcmp(va.revision, vb.revision)
    return 0 if r == 0 else (-1 if r < 0 else 1)


# ---------------------------------------------------------------------------- relationships

_REL_RE = re.compile(
    r"^(?P<name>[a-z0-9][a-z0-9.+-]*)(?::(?P<archq>[a-z0-9-]+))?"
    r"(?:\s*\(\s*(?P<op><<|<=|=|>=|>>|<|>)\s*(?P<ver>[^)\s]+)\s*\))?"
    r"(?:\s*\[(?P<arches>[^\]]*)\])?"
    r"(?:\s*<(?P<profiles>[^>]*)>)*$"
)


@dataclass(frozen=True)
class Relation:
    name: str
    op: str | None = None
    version: str | None = None
    arch_qualifier: str | None = None

    def __str__(self) -> str:
        s = self.name + (f":{self.arch_qualifier}" if self.arch_qualifier else "")
        if self.op:
            s += f" ({self.op} {self.version})"
        return s

    def satisfied_by(self, version: str) -> bool:
        if not self.op:
            return True
        c = compare_versions(version, self.version or "0")
        return {
            "<<": c < 0, "<": c <= 0, "<=": c <= 0,
            "=": c == 0,
            ">>": c > 0, ">": c >= 0, ">=": c >= 0,
        }[self.op]


def parse_relations(text: str | None) -> list[list[Relation]]:
    """Parse e.g. 'a (>= 1), b | c' into [[a>=1], [b, c]]. Raises ControlError on bad syntax."""
    if not text or not text.strip():
        return []
    groups: list[list[Relation]] = []
    for group in " ".join(text.split()).split(","):
        group = group.strip()
        if not group:
            raise ControlError(f"empty relation in {text!r}")
        alts = []
        for alt in group.split("|"):
            m = _REL_RE.match(alt.strip())
            if not m:
                raise ControlError(f"cannot parse relation {alt.strip()!r}")
            ver = m.group("ver")
            if ver is not None:
                parse_version(ver)
            alts.append(Relation(m.group("name"), m.group("op"), ver, m.group("archq")))
        groups.append(alts)
    return groups


def firmware_bounds(depends: str | None) -> tuple[str | None, str | None]:
    """Return (minimum, maximum) firmware versions declared via `firmware` relations, if any.

    Only considers relations that are mandatory (a group with a single alternative).
    """
    lo = hi = None
    for group in parse_relations(depends):
        if len(group) != 1 or group[0].name != "firmware" or not group[0].op:
            continue
        r = group[0]
        if r.op in (">=", ">>", ">"):
            lo = r.version if lo is None or compare_versions(r.version, lo) > 0 else lo
        elif r.op in ("<<", "<=", "<"):
            hi_str = f"{'<' if r.op in ('<<', '<') else '≤'} {r.version}"
            hi = hi_str
        elif r.op == "=":
            lo, hi = r.version, f"= {r.version}"
    return lo, hi
