"""Connect HCG Store releases ("Ship to Store") to HCG Repo.

HCG Store holds the .deb files of connect/paid packages and delivers them to Sileo through its payment
provider API, after re-checking sign-in and entitlement on every download. HCG Repo never stores those
files. It indexes their control data, sizes and checksums, and Sileo refuses any downloaded file whose
checksums differ from the index (DownloadManager). So HCG Repo may index a Store-held package only
when it is the exact file the Store approved, pinned by SHA-256.

The input is the Store's own release record, unchanged: the JSON that
`GET /api/v1/publishing/releases/<id>` returns to a publisher or approver (web-hcg-store
server/src/publishing.ts, releaseView/artifactView). HCG Repo reuses it; it keeps no product registry
of its own beyond config/products.toml, which the Store's check:repo-mapping already cross-checks.

Nothing here publishes. An accepted artifact is recorded as a protected lock entry with provenance;
publication stays the manual Publish repository workflow, and the index shows protected entries only
once [access] payment_endpoint is set (docs/ACCESS.md).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .config import Report, RepoConfig, load_package_meta
from .debinspect import DebInfo

# HCG Repo access level -> Store release distributions that enforce the same rule. "connect" means any
# signed-in HCG account; "paid" means ownership via HCG's central entitlement. A mismatch is refused
# rather than resolved, so importing never changes who may download a package.
ACCESS_TO_DISTRIBUTION = {"connect": {"free"}, "paid": {"paid", "subscription", "entitlement"}}
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def load_release_record(path: Path) -> tuple[dict, str]:
    raw = Path(path).read_bytes()
    try:
        rec = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"{path}: not JSON: {exc}") from exc
    if not isinstance(rec, dict) or "artifacts" not in rec or "status" not in rec:
        raise ValueError(f"{path}: not an HCG Store release record (GET /api/v1/publishing/releases/<id>)")
    return rec, hashlib.sha256(raw).hexdigest()


def find_artifact(rec: dict, artifact_id: str) -> dict | None:
    return next((a for a in rec.get("artifacts") or [] if a.get("id") == artifact_id), None)


def check_release(cfg: RepoConfig, rec: dict, artifact_id: str, info: DebInfo | None = None) -> Report:
    """Every reason HCG Repo must not index this Store artifact. Empty report: it may be recorded."""
    r = Report(f"store release {rec.get('id')} artifact {artifact_id}")
    status, elig = rec.get("status"), rec.get("approvedEligibility")
    if status == "withdrawn":
        r.error("store-release-withdrawn", f"the Store withdrew release {rec.get('id')} ({rec.get('withdrawnReason') or 'no reason given'}); "
                "withdraw it here too (hcgrepo withdraw) if it was recorded")
    elif status != "approved":
        r.error("store-release-not-approved", f"release status is {status!r}; only releases a person approved in HCG Store can be indexed")
    elif elig != "public":
        r.error("store-release-not-public", f"release is approved for {elig!r}; only public releases go into the public index")
    if "hcg-repo" not in (rec.get("destinations") or []):
        r.error("store-destination", "the release was not shipped to HCG Repo (destinations lacks hcg-repo)")
    if rec.get("productSource") != "catalogue":
        r.error("store-draft-product", "the release belongs to a Store draft product, not a catalogue product")

    a = find_artifact(rec, artifact_id)
    if a is None:
        r.error("store-artifact-missing", f"artifact {artifact_id} is not part of this release")
        return r
    v = a.get("validation") or {}
    if a.get("status") != "verified":
        r.error("store-artifact-not-verified", f"artifact status is {a.get('status')!r}; the Store did not verify it")
    if v.get("problems"):
        r.error("store-artifact-problems", "the Store recorded problems: " + "; ".join(map(str, v["problems"])))
    if v.get("sha256Matches") is not True:
        r.error("store-artifact-sha256", "the Store did not confirm the uploaded bytes match the declared SHA-256")
    if a.get("format") != "deb" or a.get("platform") != "ios" or a.get("kind") != "jailbreak-package":
        r.error("store-artifact-kind", f"artifact is {a.get('platform')}/{a.get('format')}/{a.get('kind')}, not an iOS jailbreak package")
    sha = str(a.get("sha256") or "")
    if not SHA256.match(sha):
        r.error("store-artifact-sha256", "the Store record has no verified SHA-256")

    pkg = a.get("package") or {}
    deb = ((v.get("content") or {}).get("debian")) or {}
    ident = (pkg.get("id"), pkg.get("version"), pkg.get("architecture"))
    if deb and (deb.get("package"), deb.get("version"), deb.get("architecture")) != ident:
        r.error("store-control-mismatch", f"declared {ident} but the Store read {(deb.get('package'), deb.get('version'), deb.get('architecture'))}")
    if ident[2] and ident[2] not in (a.get("architectures") or []):
        r.error("store-architecture", f"{ident[2]} is not among the artifact's declared architectures {a.get('architectures')}")
    if ident[2] and ident[2] not in cfg.architectures:
        r.error("store-architecture", f"{ident[2]} is not an architecture this repository serves {cfg.architectures}")

    # Store authorize_download matches a Sileo request when the Debian version equals the release's
    # SemVer or starts with "<semver>-" (a Debian revision). Anything else could never be delivered.
    ver, dver = str(rec.get("version") or ""), str(ident[1] or "")
    if not (dver == ver or dver.startswith(ver + "-")):
        r.error("store-version", f"Debian version {dver!r} does not match the Store release version {ver!r}; "
                "Sileo's download request would find no file")

    product = cfg.product_for_package(ident[0] or "")
    if product is None:
        r.error("store-unmapped", f"{ident[0]} is not mapped to any product in config/products.toml")
    else:
        if product.store_slug != rec.get("productId"):
            r.error("store-product-mismatch", f"{ident[0]} belongs to {product.store_slug!r} here, the Store release is for {rec.get('productId')!r}")
        if (product.store_edition or None) != rec.get("editionId"):
            r.error("store-edition-mismatch", f"{ident[0]} is edition {product.store_edition or None!r} here, the Store release is {rec.get('editionId')!r}")
        meta = load_package_meta(cfg, ident[0])
        access = meta.access_for(ident[1])
        allowed = ACCESS_TO_DISTRIBUTION.get(access)
        if allowed is None:
            r.error("store-access-public", f"{ident[0]} is a public package here; Store-held files are only for connect/paid packages")
        elif rec.get("distribution") not in allowed:
            r.error("store-access-mismatch", f"{ident[0]} requires {access!r} here but the Store release is distributed as "
                    f"{rec.get('distribution')!r}; change neither side as a side effect, ask for a decision")

    if info is not None:
        if (info.package, info.version, info.architecture) != ident:
            r.error("store-file-identity", f"the .deb is {info.key}, the Store artifact is {ident}")
        if info.sha256 != sha:
            r.error("store-file-sha256", f"the .deb's SHA-256 {info.sha256} is not the Store artifact's {sha}")
        if info.size != a.get("sizeBytes"):
            r.error("store-file-size", f"the .deb is {info.size} bytes, the Store artifact {a.get('sizeBytes')}")
    return r


def provenance(rec: dict, artifact_id: str, record_sha256: str, store_origin: str) -> dict:
    a = find_artifact(rec, artifact_id) or {}
    return {
        "source": "hcg-store",
        "store_origin": store_origin,
        "store_release": rec.get("id"),
        "store_artifact": artifact_id,
        "store_product": rec.get("productId"),
        "store_edition": rec.get("editionId"),
        "store_version": rec.get("version"),
        "store_build": rec.get("build"),
        "store_channel": rec.get("channel"),
        "store_distribution": rec.get("distribution"),
        "store_approved_at": rec.get("approvedAt"),
        "store_artifact_sha256": a.get("sha256"),
        "store_record_sha256": record_sha256,
    }
