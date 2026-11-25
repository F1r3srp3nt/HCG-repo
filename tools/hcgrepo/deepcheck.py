"""Deeper package validation for incoming artifacts (Distribution Infrastructure v2).

Adds to `debinspect.validate_deb` (structure, metadata, architecture, minimum OS, rootless paths,
Mach-O, signing, entitlements review, forbidden script patterns):

- an embedded-secret scan of every payload file and maintainer script;
- a static behaviour analysis of maintainer scripts (network fetches, obfuscation, package-manager
  calls, launchd changes, writes outside /var/jb, resprings);
- a list of privileged helpers (LaunchDaemons/LaunchAgents, setuid files, sensitive entitlements);
- a **simulated lifecycle**: real dpkg installs, reinstalls, removes and purges a *copy* of the
  package in a throwaway directory with its own admin database. The copy has its maintainer scripts
  removed, so **no script of the submitted package is ever executed**; the lifecycle proves dpkg can
  unpack, register and cleanly remove the payload, not what the scripts would do on a device.

Three evidence levels are kept apart everywhere: `static` (reading the package), `simulated-lifecycle`
(dpkg on this machine, scripts not executed) and `physical-device` (only ever recorded from a real
device report; never produced by this module).
"""

from __future__ import annotations

import os
import plistlib
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import macho
from .config import Report
from .debinspect import DebInfo, _iter_tar, _norm, require_dpkg_deb

LEVEL_STATIC = "static"
LEVEL_LIFECYCLE = "simulated-lifecycle"
LEVEL_DEVICE = "physical-device"

# Secret patterns: report the kind and place only, never the matched value.
SECRET_PATTERNS = [
    ("error", "private-key", re.compile(rb"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED |PGP )?PRIVATE KEY(?: BLOCK)?-----")),
    ("error", "github-token", re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})")),
    ("error", "aws-access-key", re.compile(rb"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("error", "slack-token", re.compile(rb"\bxox[baprs]-[0-9A-Za-z-]{10,}")),
    ("error", "stripe-secret-key", re.compile(rb"\b[rs]k_live_[0-9A-Za-z]{20,}")),
    ("error", "anthropic-api-key", re.compile(rb"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("error", "openai-api-key", re.compile(rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{40,}")),
    ("warning", "google-api-key", re.compile(rb"\bAIza[0-9A-Za-z_-]{35}\b")),
]
SECRET_FILE_SUFFIXES = {".p12": "error", ".pfx": "error", ".pem": "warning", ".key": "warning",
                        ".mobileprovision": "warning", ".env": "warning"}
MAX_SCAN_BYTES = 256 * 1024 * 1024

SENSITIVE_ENTITLEMENTS = (
    "platform-application", "com.apple.private.security.no-sandbox", "com.apple.private.security.no-container",
    "task_for_pid-allow", "com.apple.system-task-ports", "com.apple.private.skip-library-validation",
    "com.apple.private.persona-mgmt", "com.apple.private.security.container-manager",
    "com.apple.private.security.storage.AppDataContainers", "com.apple.springboard.launchapplications",
)

# (level, code, pattern, explanation) for maintainer-script behaviour beyond debinspect's forbidden list.
SCRIPT_BEHAVIOUR = [
    ("error", "script-network", re.compile(r"\b(curl|wget|nc|ncat|ftp|scp|rsync)\b|https?://"),
     "fetches from the network; packages must be self-contained"),
    ("error", "script-obfuscated", re.compile(r"base64\s+(-d|--decode)|\beval\b|xxd\s+-r|openssl\s+enc"),
     "decodes or evaluates generated code, which cannot be reviewed"),
    ("error", "script-package-manager", re.compile(r"\b(dpkg|apt|apt-get|aptitude)\b(?!-)"),
     "calls the package manager from a maintainer script (deadlocks or changes other packages)"),
    ("warning", "script-launchctl", re.compile(r"\blaunchctl\b"), "loads, unloads or starts launchd jobs"),
    ("warning", "script-respring", re.compile(r"\b(sbreload|ldrestart|killall\s+(-\d+\s+)?(SpringBoard|backboardd))\b"),
     "resprings or restarts system daemons"),
    ("warning", "script-kill", re.compile(r"\b(killall|pkill|kill\s+-9)\b"), "kills processes"),
    ("warning", "script-chown", re.compile(r"\bchown\b"), "changes file ownership"),
]
# Commands that write: for copy-like ones only the destination (last path) is written, for the rest every path.
WRITE_DEST_CMDS = {"cp", "mv", "ln", "install", "rsync", "tee"}
WRITE_ALL_CMDS = {"rm", "mkdir", "touch", "chmod", "chown", "rmdir", "unlink"}
ABS_PATH = re.compile(r"(?<![\w$}])/[A-Za-z][^\s;'\"|&)<>]*")
REDIRECT = re.compile(r">>?\s*(/[A-Za-z][^\s;'\"|&)<>]*)")


def written_paths(body: str) -> list[str]:
    """Absolute paths a shell script writes to (best effort, statically)."""
    out: list[str] = []
    joined = re.sub(r"\\\n", " ", body)
    for line in joined.splitlines():
        out += REDIRECT.findall(line)
        for seg in re.split(r"[;&|]+|\b(?:then|do|else)\b", line):
            words = seg.split()
            if not words:
                continue
            cmd = words[0].rsplit("/", 1)[-1]
            paths = [w for w in words[1:] if ABS_PATH.fullmatch(w.strip("\"'"))]
            paths = [w.strip("\"'") for w in paths]
            if cmd in WRITE_DEST_CMDS and paths:
                out += paths[-1:] if cmd != "tee" else paths
            elif cmd in WRITE_ALL_CMDS:
                out += paths
    return out


WRITE_OK_PREFIXES = ("/var/jb", "/dev/null", "/tmp/", "/var/tmp/", "/private/var/tmp/")
# The mobile user's data: rootless packages legitimately keep (and on purge delete) their own preferences here.
USER_DATA_PREFIXES = ("/var/mobile/", "/private/var/mobile/")


def scan_secrets(info: DebInfo, r: Report) -> int:
    """Scan payload files and maintainer scripts for credentials. Returns the number of files read."""
    dpkg = require_dpkg_deb()
    scanned = 0
    for name, (_, data) in sorted(info.scripts.items()):
        _secret_hits(f"DEBIAN/{name}", data, r)
    for tf, m in _iter_tar([dpkg, "--fsys-tarfile", str(info.path)]):
        if not m.isfile():
            continue
        name = _norm(m.name)
        suffix = Path(name).suffix.lower()
        if suffix in SECRET_FILE_SUFFIXES:
            lvl = SECRET_FILE_SUFFIXES[suffix]
            (r.error if lvl == "error" else r.warn)("secret-file", f"{name}: {suffix} files can hold signing identities or credentials")
        if m.size > MAX_SCAN_BYTES:
            r.warn("secret-scan-skipped", f"{name}: {m.size} bytes, above the scan limit")
            continue
        fobj = tf.extractfile(m)
        if fobj is None:
            continue
        _secret_hits(name, fobj.read(), r)
        scanned += 1
    return scanned


def _secret_hits(name: str, data: bytes, r: Report) -> None:
    for lvl, kind, rx in SECRET_PATTERNS:
        n = len(rx.findall(data))
        if n:
            msg = f"{name}: {n} match(es) of a {kind} pattern (value not shown); rotate it if real and rebuild without it"
            (r.error if lvl == "error" else r.warn)(f"secret-{kind}", msg)


def analyse_scripts(info: DebInfo, r: Report) -> dict:
    """Static behaviour of maintainer scripts. Returns {script: [finding codes]}."""
    out: dict[str, list[str]] = {}
    rootless = info.architecture == "iphoneos-arm64"
    for name, (_, data) in sorted(info.scripts.items()):
        codes: list[str] = []
        if macho.is_macho(data[:4]):
            r.warn("script-unreviewable", f"{name} is a compiled program; its behaviour cannot be read statically")
            out[name] = ["script-unreviewable"]
            continue
        text = data.decode("utf-8", "replace")
        body = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
        for lvl, code, rx, why in SCRIPT_BEHAVIOUR:
            if rx.search(body):
                (r.error if lvl == "error" else r.warn)(code, f"{name} {why}")
                codes.append(code)
        if rootless:
            outside = sorted({x for x in written_paths(body)
                              if not (x == "/var/jb" or x.startswith(WRITE_OK_PREFIXES))})
            user = [x for x in outside if x.startswith(USER_DATA_PREFIXES)]
            system = [x for x in outside if x not in user]
            if user:
                r.warn("script-touches-user-data", f"{name} changes the mobile user's data: {', '.join(user[:5])} (review: "
                                                   "should only be this package's own files)")
                codes.append("script-touches-user-data")
            if system:
                r.error("script-writes-outside-jbroot", f"{name} writes to system locations outside /var/jb: {', '.join(system[:5])}")
                codes.append("script-writes-outside-jbroot")
        out[name] = codes
    return out


def privileged_helpers(info: DebInfo, r: Report) -> list[dict]:
    """LaunchDaemons/LaunchAgents, setuid files and sensitive entitlements in the payload."""
    found: list[dict] = []
    dpkg = require_dpkg_deb()
    launchd = re.compile(r"(^|/)Library/Launch(Daemons|Agents)/[^/]+\.plist$")
    for tf, m in _iter_tar([dpkg, "--fsys-tarfile", str(info.path)]):
        name = _norm(m.name)
        if not (m.isfile() and launchd.search(name)):
            continue
        program = None
        try:
            pl = plistlib.loads(tf.extractfile(m).read())  # type: ignore[union-attr]
            program = pl.get("Program") or (pl.get("ProgramArguments") or [None])[0]
            run_as = pl.get("UserName", "root") if "LaunchDaemons" in name else "the signed-in user"
        except Exception:  # noqa: BLE001
            run_as = "unknown (unreadable plist)"
        kind = "launch-daemon" if "LaunchDaemons" in name else "launch-agent"
        found.append({"kind": kind, "path": name, "program": program, "runs_as": run_as})
        r.warn(f"privileged-{kind}", f"{name} starts {program or 'a program'} as {run_as}; needs explicit review")
    for f in info.files:
        if f.kind == "file" and f.mode & 0o6000:
            found.append({"kind": "setuid", "path": f.path, "mode": oct(f.mode)})
    for b in info.binaries:
        if not b.info:
            continue
        ents: set[str] = set()
        for s in b.info.slices:
            ents |= {k for k, v in (s.entitlements or {}).items() if v not in (False, None, [], "")}
        sens = sorted(e for e in ents if e in SENSITIVE_ENTITLEMENTS or e.startswith("com.apple.private.security."))
        if sens:
            found.append({"kind": "entitlements", "path": b.path, "entitlements": sens})
            r.warn("privileged-entitlements", f"{b.path} holds sensitive entitlements: {', '.join(sens)}")
    return found


# ---------------------------------------------------------------------------- simulated lifecycle


def _through_symlink(info: DebInfo) -> list[str]:
    links = {f.path for f in info.files if f.kind == "symlink"}
    bad = []
    for f in info.files:
        parts = f.path.split("/")
        for i in range(1, len(parts)):
            if "/".join(parts[:i]) in links:
                bad.append(f.path)
                break
    return bad


def _dpkg(r: Path, *args: str) -> subprocess.CompletedProcess:
    cmd = ["dpkg", "--root=/", f"--instdir={r}/root", f"--admindir={r}/admin", f"--log={r}/dpkg.log",
           "--force-architecture", "--force-depends", "--force-not-root", "--force-unsafe-io",
           "--force-confnew", "--no-triggers", *args]
    env = {"PATH": os.environ.get("PATH", "/usr/sbin:/usr/bin:/sbin:/bin"), "LC_ALL": "C"}
    return subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env)


def _status(r: Path, package: str) -> str:
    text = (r / "admin" / "status").read_text(encoding="utf-8", errors="replace")
    for stanza in text.split("\n\n"):
        if re.search(rf"^Package: {re.escape(package)}$", stanza, re.M):
            m = re.search(r"^Status: (.+)$", stanza, re.M)
            return m.group(1) if m else "unknown"
    return "not-installed"


def _script_free_copy(src: Path, dest_dir: Path) -> Path:
    tree = dest_dir / "tree"
    subprocess.run(["dpkg-deb", "-R", str(src), str(tree)], check=True, capture_output=True, timeout=600)
    for s in ("preinst", "postinst", "prerm", "postrm", "config", "triggers"):
        (tree / "DEBIAN" / s).unlink(missing_ok=True)
    out = dest_dir / "lifecycle-copy.deb"
    subprocess.run(["dpkg-deb", "--root-owner-group", "-Zgzip", "-b", str(tree), str(out)],
                   check=True, capture_output=True, timeout=600)
    shutil.rmtree(tree)
    return out


def _payload_present(root: Path, info: DebInfo) -> list[str]:
    """Payload entries missing or different after install (empty when the tree matches)."""
    bad = []
    for f in info.files:
        if not f.path:
            continue
        p = root / f.path
        if f.kind == "symlink":
            if not p.is_symlink() or os.readlink(p) != f.linkname:
                bad.append(f.path)
        elif f.kind == "dir":
            if not p.is_dir() or p.is_symlink():
                bad.append(f.path)
        elif f.kind in ("file", "hardlink"):
            if not p.is_file() or (f.kind == "file" and p.stat().st_size != f.size):
                bad.append(f.path)
    return bad


def _payload_left(root: Path, info: DebInfo, keep: set[str]) -> list[str]:
    left = []
    for f in info.files:
        if not f.path or f.kind == "dir" or f.path in keep:
            continue
        p = root / f.path
        if p.exists() or p.is_symlink():
            left.append(f.path)
    return left


def simulate_lifecycle(info: DebInfo, r: Report, upgrade_from: Path | None = None) -> dict:
    """Install, reinstall, remove and purge a script-free copy with dpkg in a throwaway root.

    Returns a record {level, scripts_executed: False, steps: [...], passed}. Failures are errors.
    """
    rec: dict = {"level": LEVEL_LIFECYCLE, "scripts_executed": False, "steps": [], "passed": False,
                 "note": "dpkg on an isolated root with its own database; the package's maintainer scripts were "
                         "removed from the test copy and never run. This is not a device test."}
    if not shutil.which("dpkg"):
        r.warn("lifecycle-unavailable", "dpkg is not installed here; lifecycle not simulated")
        rec["note"] = "dpkg unavailable: not simulated"
        return rec
    through = _through_symlink(info)
    if through:
        r.error("lifecycle-through-symlink", f"payload installs files through its own symlinks ({', '.join(through[:3])}); "
                                             "refused before extraction")
        return rec
    with tempfile.TemporaryDirectory(prefix="hcg-lifecycle-") as tmp:
        t = Path(tmp)
        for d in ("root", "admin/info", "admin/updates", "admin/triggers"):
            (t / d).mkdir(parents=True)
        (t / "admin" / "status").write_text("")
        (t / "admin" / "available").write_text("")
        work = t / "work"
        work.mkdir()

        def step(name: str, args: list[str], want_status: str | None, check) -> bool:
            cp = _dpkg(t, *args)
            status = _status(t, info.package)
            problems = []
            if cp.returncode != 0:
                problems.append(f"dpkg exit {cp.returncode}: {(cp.stderr or cp.stdout).strip()[-400:]}")
            if want_status and status != want_status:
                problems.append(f"status is {status!r}, expected {want_status!r}")
            problems += check()
            rec["steps"].append({"step": name, "ok": not problems, "status": status, "problems": problems})
            for pr in problems:
                r.error(f"lifecycle-{name}", pr)
            return not problems

        try:
            copy = _script_free_copy(info.path, work)
            prev = None
            if upgrade_from is not None:
                (work / "prev").mkdir()
                prev = _script_free_copy(Path(upgrade_from), work / "prev")
                if not step("install-previous", ["-i", str(prev)], "install ok installed", lambda: []):
                    return rec
            ok = step("upgrade" if prev else "install", ["-i", str(copy)], "install ok installed",
                      lambda: [f"missing or different after install: {p}" for p in _payload_present(t / "root", info)[:10]])
            ok = ok and step("reinstall", ["-i", str(copy)], "install ok installed",
                             lambda: [f"missing after reinstall: {p}" for p in _payload_present(t / "root", info)[:10]])
            conf = {c.lstrip("/") for c in info.conffiles}
            after_remove = "deinstall ok config-files" if conf else "not-installed"
            ok = ok and step("remove", ["-r", info.package], after_remove,
                             lambda: [f"left behind after remove: {p}" for p in _payload_left(t / "root", info, conf)[:10]])
            ok = ok and step("purge", ["-P", info.package], "not-installed",
                             lambda: [f"left behind after purge: {p}" for p in _payload_left(t / "root", info, set())[:10]])
            rec["passed"] = bool(ok)
        except subprocess.SubprocessError as exc:
            r.error("lifecycle-copy", f"could not prepare the script-free test copy: {exc}")
    return rec


# ---------------------------------------------------------------------------- one call


def deep_validate(info: DebInfo, lifecycle: bool = True, upgrade_from: Path | None = None) -> tuple[Report, dict]:
    """Run every v2 check on an inspected package. Returns (report, evidence record)."""
    r = Report(f"{info.package} {info.version} {info.architecture}")
    scanned = scan_secrets(info, r)
    scripts = analyse_scripts(info, r)
    helpers = privileged_helpers(info, r)
    static_ok = r.ok
    evidence = {
        "static": {"level": LEVEL_STATIC, "passed": static_ok, "files_scanned_for_secrets": scanned,
                   "maintainer_scripts": scripts, "privileged_helpers": helpers},
        "lifecycle": None,
        "physical_device": {"level": LEVEL_DEVICE, "recorded": False,
                            "note": "only a report from a real device can set this; HCG Repo never infers it"},
    }
    if lifecycle:
        evidence["lifecycle"] = simulate_lifecycle(info, r, upgrade_from)
    return r, evidence
