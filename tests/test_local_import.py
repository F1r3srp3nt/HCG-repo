"""local-file import requests: a .deb built outside GitHub Actions (e.g. HCG Executable Maker on the owner's Mac),
committed under imports/local/ with its exact SHA-256 and the provenance of a possibly private source. Since
Distribution v2 it shares the Release Manager's approval (submissions/<id>/review.json) and provenance."""

import hashlib
import json
import shutil
from pathlib import Path

from support import NS, RepoTestCase, tool_deb
from hcgrepo.importer import GitHub, ImportError_, load_request, run_request
from hcgrepo.config import load_lock

COMMIT = "a5b3c2322f0de4fbbe8d10df179872c590aa1d75"


class LocalFileImportTests(RepoTestCase):
    def request(self, deb_rel: str, sha: str, **extra) -> dict:
        self.approve(sha, extra.pop("approved_sha", sha))
        lines = ['[request]', f'package = "{NS}testtool"', 'version = "1.0.0"', 'architecture = "iphoneos-arm64"', 'channel = "candidate"',
                 '[source]', 'kind = "local-file"', 'repository = "F1r3srp3nt/HCG-Signal-Contractor"', f'commit = "{COMMIT}"',
                 f'path = "{deb_rel}"', f'sha256 = "{sha}"', 'built_by = "HCG Executable Maker 0.5.0 (Unleashify, local)"',
                 'toolchain = "Xcode 27.2 (27C5000)"', 'source_visibility = "private"',
                 'submission = "submissions/sc/submission.json"']
        lines += [f'{k} = "{v}"' for k, v in extra.items()]
        (self.root / "imports").mkdir(exist_ok=True)
        p = self.root / "imports" / "local-test.toml"
        p.write_text("\n".join(lines) + "\n")
        return load_request(p)

    def approve(self, sha: str, approved_sha: str) -> None:
        d = self.root / "submissions" / "sc"
        d.mkdir(parents=True, exist_ok=True)
        (d / "review.json").write_text(json.dumps({"approval": {"sha256": approved_sha, "by": "Owner",
                                                                "record": "Approved for Candidate."}}))

    def commit_deb(self) -> tuple[str, str]:
        deb = tool_deb(self.work)
        (self.root / "imports" / "local").mkdir(parents=True, exist_ok=True)
        rel = f"imports/local/{deb.name}"
        shutil.copyfile(deb, self.root / rel)
        return rel, hashlib.sha256(deb.read_bytes()).hexdigest()

    def test_local_file_import_records_private_source_provenance(self):
        rel, sha = self.commit_deb()
        r = self.request(rel, sha)
        status, rep = run_request(self.cfg, r, GitHub(token=""), self.work)
        self.assertEqual(status, "imported", rep.render())
        entry = load_lock(self.reload())["packages"][0]
        self.assertEqual(entry["sha256"], sha)
        prov = json.loads(next(self.root.glob("pool/**/*.provenance.json")).read_text())
        flat = json.dumps(prov)
        for want in ("local-file", "F1r3srp3nt/HCG-Signal-Contractor", COMMIT, "Xcode 27.2", "private", "Unleashify, local",
                     "Approved for Candidate."):
            self.assertIn(want, flat)
        self.assertEqual(entry["channel"], "candidate")
        self.assertNotIn("run_id", prov)

    def test_debian_version_characters_in_the_file_name(self):
        rel, sha = self.commit_deb()
        tilde = "imports/local/" + Path(rel).name.replace(".deb", "~beta1+local.deb")
        (self.root / rel).rename(self.root / tilde)
        status, rep = run_request(self.cfg, self.request(tilde, sha), GitHub(token=""), self.work)
        self.assertEqual(status, "imported", rep.render())
        with self.assertRaises(ImportError_):
            self.request("imports/local/x y.deb", sha)

    def test_checksum_must_match(self):
        rel, _ = self.commit_deb()
        status, rep = run_request(self.cfg, self.request(rel, "0" * 64), GitHub(token=""), self.work)
        self.assertEqual(status, "rejected")
        self.assertIn("sha256", rep.render())

    def test_request_rules(self):
        rel, sha = self.commit_deb()
        for bad in ("pool/x.deb", "imports/local/../x.deb", "imports/local/x.zip"):
            with self.assertRaises(ImportError_):
                self.request(bad, sha)
        p = self.root / "imports" / "local-test.toml"
        p.write_text(p.read_text().replace(f'sha256 = "{sha}"\n', ""))
        with self.assertRaises(ImportError_):
            load_request(p)

    def test_missing_file_is_refused(self):
        r = self.request("imports/local/absent.deb", "1" * 64)
        with self.assertRaises(ImportError_):
            run_request(self.cfg, r, GitHub(token=""), self.work)


class LocalFileApprovalTests(LocalFileImportTests):
    """The shared approval: a local-file without the owner's approval of its exact bytes is never imported or kept."""

    def test_unapproved_bytes_are_refused(self):
        rel, sha = self.commit_deb()
        r = self.request(rel, sha, approved_sha="0" * 64)
        with self.assertRaisesRegex(ImportError_, "approval"):
            run_request(self.cfg, r, GitHub(token=""), self.work)

    def test_validate_refuses_unapproved_or_unrequested_files(self):
        from hcgrepo.config import Report
        from hcgrepo.repo import check_repository, load_pool
        rel, sha = self.commit_deb()
        rep = Report("repository")
        check_repository(self.cfg, load_pool(self.cfg, rep), rep)
        self.assertIn("artifact-unapproved", rep.codes())        # no request names it
        self.request(rel, sha, approved_sha="0" * 64)
        rep = Report("repository")
        check_repository(self.cfg, load_pool(self.cfg, rep), rep)
        self.assertIn("artifact-unapproved", rep.codes())        # named, but not approved
        self.request(rel, sha)
        rep = Report("repository")
        check_repository(self.cfg, load_pool(self.cfg, rep), rep)
        self.assertNotIn("artifact-unapproved", rep.codes())     # approved

    def test_stable_and_run_ids_are_refused(self):
        rel, sha = self.commit_deb()
        self.request(rel, sha)
        p = self.root / "imports" / "local-test.toml"
        for old, new in (('channel = "candidate"', 'channel = "stable"'), ('kind = "local-file"', 'kind = "local-file"\nrun_id = 5')):
            text = p.read_text()
            p.write_text(text.replace(old, new))
            with self.assertRaises(ImportError_):
                load_request(p)
            p.write_text(text)
