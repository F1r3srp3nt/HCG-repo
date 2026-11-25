"""Distribution Infrastructure v2: submission manifests, the Unleashify handoff adapter, deeper validation,
the simulated dpkg lifecycle, the Candidate Release Manager and the new import-request kinds."""

import copy
import json
import os
import shutil
import tempfile
from pathlib import Path

from support import NS, RepoTestCase, app_deb, control, make_deb, tool_deb
from hcgrepo.config import Report, load_lock, load_package_meta
from hcgrepo.debinspect import inspect_deb
from hcgrepo.deepcheck import analyse_scripts, deep_validate, privileged_helpers, scan_secrets, simulate_lifecycle
from hcgrepo.importer import (AwaitingLocalImport, GitHub, ImportError_, fetch_https_artifact, load_request,
                              request_is_done, run_request)
from hcgrepo import release as rm
from hcgrepo.repo import check_repository, load_pool
from hcgrepo.submission import (SubmissionError, from_unleashify_handoff, submission_id, validate_manifest,
                                verify_against_deb)

PKG = NS + "testapp"
COMMIT = "a" * 40


def manifest_for(info, **over) -> dict:
    m = {
        "format": "hcg.repo.artifact-submission", "formatVersion": 1,
        "package": {"identifier": info.package, "version": info.version, "architecture": info.architecture},
        "artifact": {"filename": info.path.name, "sha256": info.sha256, "size": info.size},
        "compatibility": {"minimumOS": "15.0", "environment": "rootless", "jailbreaks": ["Dopamine"]},
        "source": {"repository": "F1r3srp3nt/Example", "commit": COMMIT, "dirty": False},
        "build": {"provider": "executable-maker-local", "toolchain": "Xcode 16.2", "builtAt": "2026-11-25T10:00:00Z"},
        "dependencies": ["firmware", "uikittools"],
        "maintainerScripts": {"declared": ["postinst"]},
        "validation": {"tool": "test", "checks": []},
        "channel": "candidate",
    }
    for k, v in over.items():
        m[k] = v
    return m


class ManifestTests(RepoTestCase):
    def setUp(self):
        super().setUp()
        self.info = inspect_deb(app_deb(self.work))

    def test_good_manifest_matches_package(self):
        m = manifest_for(self.info)
        self.assertEqual(validate_manifest(m), [])
        r = verify_against_deb(m, self.info)
        self.assertTrue(r.ok, r.render())

    def test_local_build_cannot_carry_a_ci_run(self):
        m = manifest_for(self.info)
        m["build"]["runId"] = 123
        self.assertTrue(any("no CI run" in p for p in validate_manifest(m)))

    def test_github_build_needs_its_run(self):
        m = manifest_for(self.info)
        m["build"]["provider"] = "github-actions-private"
        probs = validate_manifest(m)
        self.assertTrue(any("runId" in p for p in probs))
        m["build"].update(runId=44, artifactName="x-deb", runRepository="F1r3srp3nt/Maker")
        self.assertEqual(validate_manifest(m), [])

    def test_rules(self):
        for path, value, needle in ((("source", "dirty"), True, "dirty"), (("channel",), "stable", "candidate"),
                                    (("compatibility", "environment"), "rootful", "needs architecture"),
                                    (("source", "commit"), "abc1234", "40-character"),
                                    (("artifact", "sha256"), "XYZ", "sha256")):
            m = manifest_for(self.info)
            tgt = m
            for k in path[:-1]:
                tgt = tgt[k]
            tgt[path[-1]] = value
            self.assertTrue(any(needle in p for p in validate_manifest(m)), (path, validate_manifest(m)))

    def test_every_claim_is_checked(self):
        cases = [(("artifact", "sha256"), "0" * 64, "submission-mismatch"),
                 (("artifact", "size"), 5, "submission-mismatch"),
                 (("package", "version"), "9.9", "submission-mismatch"),
                 (("compatibility", "minimumOS"), "14.0", "submission-min-os"),
                 (("dependencies",), ["uikittools"], "submission-dependencies"),
                 (("maintainerScripts", "declared"), [], "submission-scripts")]
        for path, value, code in cases:
            m = copy.deepcopy(manifest_for(self.info))
            tgt = m
            for k in path[:-1]:
                tgt = tgt[k]
            tgt[path[-1]] = value
            self.assertIn(code, verify_against_deb(m, self.info).codes(), path)


def make_handoff(work: Path, deb: Path, run_id=None, access="connect") -> Path:
    info = inspect_deb(deb)
    h = work / f"{info.package}_{info.version}-candidate-handoff"
    h.mkdir()
    shutil.copyfile(deb, h / deb.name)
    (h / "SHA256SUMS").write_text(f"{info.sha256}  {deb.name}\n")
    (h / "control.txt").write_text(info.control_text)
    prov = {"package": info.package, "version": info.version, "architecture": info.architecture, "file": deb.name,
            "sha256": info.sha256, "size": info.size,
            "source": {"repository": "F1r3srp3nt/HCG-Example", "commit": COMMIT, "dirty": False,
                       "ci_repository": "F1r3srp3nt/HCG-Executable-Maker" if run_id else None,
                       "ci_commit": "b" * 40 if run_id else None, "ci_run_id": run_id,
                       "ci_run_url": f"https://github.com/x/y/actions/runs/{run_id}" if run_id else None,
                       "ci_artifact": "example-deb" if run_id else None},
            "app": {"bundle_id": info.package, "name": "Test App", "minimum_os": "15.0", "device_families": ["iphone"]},
            "packaging": {"tool": "HCG Executable Maker 1.4", "built_with": "Xcode 16.2", "packaged_at": "2026-11-25T09:00:00Z",
                          "install_path": "/var/jb/Applications/TestApp.app", "compression": "gzip"},
            "validation": [{"check": "rootless paths", "status": "Passed", "detail": ""}], "device_tested": False,
            "unleashify": {"mode": "standard", "source_revision": {"branch": "main", "commit": COMMIT}}}
    (h / "provenance.json").write_text(json.dumps(prov))
    (h / "package.toml").write_text(f'[display]\nsummary = "Test app."\nknown_issues = "- Not verified on a device."\n'
                                    f'[release]\nchannel = "beta"\n[access]\nlevel = "{access}"\n')
    (h / "CHANGELOG.md").write_text(f"# Changelog\n\n## {info.version}\n\n- First candidate.\n")
    return h


class HandoffTests(RepoTestCase):
    def test_local_handoff_is_a_local_provider_without_run(self):
        deb = app_deb(self.work, version="0.1.0~beta1")
        m, path = from_unleashify_handoff(make_handoff(self.work, deb))
        self.assertEqual(m["build"]["provider"], "executable-maker-local")
        self.assertNotIn("runId", m["build"])
        self.assertIsNone(m["maintainerScripts"]["declared"])  # not recorded by the handoff, so not guessed
        self.assertEqual(m["source"]["branch"], "main")
        self.assertEqual(path.name, deb.name)

    def test_ci_handoff_keeps_its_run(self):
        deb = app_deb(self.work, version="0.1.0~beta1")
        m, _ = from_unleashify_handoff(make_handoff(self.work, deb, run_id=4486))
        self.assertEqual((m["build"]["provider"], m["build"]["runId"]), ("github-actions", 4486))

    def test_disagreeing_checksums_are_refused(self):
        deb = app_deb(self.work, version="0.1.0~beta1")
        h = make_handoff(self.work, deb)
        (h / "SHA256SUMS").write_text(f"{'0' * 64}  {deb.name}\n")
        with self.assertRaises(SubmissionError):
            from_unleashify_handoff(h)


class DeepCheckTests(RepoTestCase):
    def deep(self, deb, lifecycle=False):
        return deep_validate(inspect_deb(deb), lifecycle=lifecycle)

    def test_existing_style_package_passes_with_lifecycle(self):
        r, ev = self.deep(app_deb(self.work), lifecycle=True)
        self.assertTrue(r.ok, r.render())
        lc = ev["lifecycle"]
        self.assertTrue(lc["passed"])
        self.assertFalse(lc["scripts_executed"])
        self.assertEqual([s["step"] for s in lc["steps"]], ["install", "reinstall", "remove", "purge"])
        self.assertFalse(ev["physical_device"]["recorded"])

    def test_secrets_are_found_but_never_echoed(self):
        token = "ghp_" + "Q" * 36
        key = "-----BEGIN " + "RSA PRIVATE KEY-----\nMIIE\n"
        deb = make_deb(self.work / "s.deb", control(NS + "testtool"),
                       files={"var/jb/usr/share/x/config.txt": (f"token={token}\n{key}".encode(), 0o644),
                              "var/jb/usr/share/x/dev.p12": (b"\x30\x82", 0o644)})
        r, _ = self.deep(deb)
        self.assertTrue({"secret-github-token", "secret-private-key", "secret-file"} <= r.codes(), r.render())
        self.assertNotIn(token, r.render())

    def test_script_behaviour(self):
        script = ("#!/bin/sh\ncurl -o /tmp/x https://example.invalid/x\ncp /tmp/x /usr/bin/x\n"
                  "rm -f /var/mobile/Library/Preferences/com.x.plist\nlaunchctl load /var/jb/Library/LaunchDaemons/x.plist\n")
        deb = make_deb(self.work / "sc.deb", control(NS + "testtool"),
                       files={"var/jb/usr/bin/t": (b"#!/bin/sh\n", 0o755)}, scripts={"postinst": (script, 0o755)})
        r, _ = self.deep(deb)
        self.assertTrue({"script-network", "script-writes-outside-jbroot", "script-touches-user-data",
                         "script-launchctl"} <= r.codes(), r.render())
        self.assertIn("/usr/bin/x", r.render())
        self.assertNotIn("/var/jb/Library", " ".join(m for c, m in r.errors))

    def test_comments_do_not_count(self):
        deb = make_deb(self.work / "c.deb", control(NS + "testtool"), files={"var/jb/usr/bin/t": (b"x", 0o755)},
                       scripts={"postrm": ("#!/bin/sh\n# never curl anything\nexit 0\n", 0o755)})
        self.assertTrue(self.deep(deb)[0].ok)

    def test_launch_daemon_is_listed(self):
        import plistlib
        pl = plistlib.dumps({"Label": "x", "Program": "/var/jb/usr/libexec/xd"})
        deb = make_deb(self.work / "d.deb", control(NS + "testtool"),
                       files={"var/jb/Library/LaunchDaemons/com.x.plist": (pl, 0o644),
                              "var/jb/usr/libexec/xd": (b"#!/bin/sh\n", 0o755)})
        r, ev = self.deep(deb)
        self.assertIn("privileged-launch-daemon", r.codes())
        self.assertEqual(ev["static"]["privileged_helpers"][0]["runs_as"], "root")

    def test_lifecycle_never_runs_scripts(self):
        marker = self.tmp / "ran"
        script = f"#!/bin/sh\ntouch {marker}\nexit 1\n"
        deb = make_deb(self.work / "m.deb", control(NS + "testtool"), files={"var/jb/usr/bin/t": (b"x", 0o755)},
                       scripts={n: (script, 0o755) for n in ("preinst", "postinst", "prerm", "postrm")})
        r = Report("x")
        rec = simulate_lifecycle(inspect_deb(deb), r)
        self.assertTrue(rec["passed"], r.render())
        self.assertFalse(marker.exists())

    def test_conffiles_survive_remove_and_go_on_purge(self):
        def setup(stage):
            (stage / "DEBIAN" / "conffiles").write_text("/var/jb/etc/x.conf\n")
        deb = make_deb(self.work / "cf.deb", control(NS + "testtool"),
                       files={"var/jb/etc/x.conf": (b"a=1\n", 0o644), "var/jb/usr/bin/t": (b"x", 0o755)}, setup=setup)
        r = Report("x")
        rec = simulate_lifecycle(inspect_deb(deb), r)
        self.assertTrue(rec["passed"], r.render())
        self.assertEqual(rec["steps"][2]["status"], "deinstall ok config-files")

    def test_paths_through_own_symlink_are_refused_before_extraction(self):
        def setup(stage):
            os.symlink("/etc", stage / "var/jb/esc")
        deb = make_deb(self.work / "l.deb", control(NS + "testtool"), files={"var/jb/usr/bin/t": (b"x", 0o755)},
                       setup=setup)
        info = inspect_deb(deb)
        from hcgrepo.debinspect import FileEntry
        info.files.append(FileEntry("var/jb/esc/passwd", "file", 0o644, 0, 0, 1))
        r = Report("x")
        simulate_lifecycle(info, r)
        self.assertIn("lifecycle-through-symlink", r.codes())

    def test_upgrade_from_previous_version(self):
        old = tool_deb(self.work, version="1.0.0")
        (self.work / "new").mkdir()
        new = tool_deb(self.work / "new", version="1.1.0")
        r, ev = deep_validate(inspect_deb(new), upgrade_from=old)
        self.assertTrue(ev["lifecycle"]["passed"], r.render())
        self.assertEqual(ev["lifecycle"]["steps"][0]["step"], "install-previous")


class ReleaseManagerTests(RepoTestCase):
    def setUp(self):
        super().setUp()
        self._store = tempfile.TemporaryDirectory(prefix="hcg-incoming-")
        self.store = Path(self._store.name)
        self.deb = app_deb(self.work, version="0.1.0~beta1")
        self.handoff = make_handoff(self.work, self.deb)

    def tearDown(self):
        self._store.cleanup()
        super().tearDown()

    def submit(self, **kw):
        return rm.submit(self.cfg, self.store, handoff=kw.pop("handoff", self.handoff), lifecycle=kw.pop("lifecycle", True))

    def test_full_flow_to_candidate(self):
        review = self.submit()
        self.assertEqual(review["state"], "validated", json.dumps(review["findings"], indent=1))
        sid, sha = review["submission"], review["sha256"]
        self.assertTrue(review["levels"]["simulated_lifecycle"]["passed"])
        self.assertFalse(review["levels"]["physical_device"]["recorded"])
        # Nothing reaches the repository before approval.
        self.assertFalse((self.root / "submissions").exists())
        self.assertEqual(load_lock(self.cfg)["packages"], [])
        self.assertEqual(oct((self.store / sid).stat().st_mode & 0o777), "0o700")
        with self.assertRaises(rm.ReleaseError):
            rm.prepare(self.cfg, self.store, sid)
        with self.assertRaises(rm.ReleaseError):
            rm.approve(self.store, sid, "0" * 64, "Owner", "approved")
        rm.approve(self.store, sid, sha, "Owner", "Approved: Candidate import of this exact build.")
        with self.assertRaisesRegex(rm.ReleaseError, "does not exist"):
            rm.prepare(self.cfg, self.store, sid)
        with self.assertRaisesRegex(rm.ReleaseError, "free-demo approval"):
            rm.prepare(self.cfg, self.store, sid, create_package_metadata=True)
        self.reload()
        status, rep, review = rm.prepare(self.cfg, self.store, sid, create_package_metadata=True,
                                         free_demo_approval=f"Owner approved 2026-11-25: 0.1.0~beta1 (SHA-256 {sha}) "
                                                            "as a free public Candidate-only demo.")
        self.assertEqual(status, "imported", rep.render())
        self.assertEqual(review["state"], "prepared")
        e = load_lock(self.cfg)["packages"][0]
        self.assertEqual((e["sha256"], e["channel"]), (sha, "candidate"))
        prov = json.loads((self.root / (e["filename"] + ".provenance.json")).read_text())
        self.assertEqual(prov["provider"], "executable-maker-local")
        self.assertNotIn("run_id", prov)
        self.assertEqual(prov["approval"]["sha256"], sha)
        self.assertFalse(list((self.root / "submissions").rglob("*.deb")))
        req = load_request(self.root / "imports" / f"{sid}.toml")
        self.assertEqual(req["source"]["kind"], "local-submission")
        self.assertTrue(request_is_done(self.cfg, req))
        pool_report = Report("repository")
        check_repository(self.cfg, load_pool(self.cfg, pool_report), pool_report)
        self.assertTrue(pool_report.ok, pool_report.render())
        self.assertEqual(load_package_meta(self.reload(), PKG).access_for("0.1.0~beta1"), "public")
        self.assertEqual(load_package_meta(self.cfg, PKG).access_for("0.1.0~beta2"), "connect")

    def native_manifest(self, **over) -> Path:
        m, _ = from_unleashify_handoff(self.handoff)
        m["maintainerScripts"] = {"declared": sorted(inspect_deb(self.deb).scripts)}
        for k, v in over.items():
            m[k] = v
        (self.handoff / "submission.json").write_text(json.dumps(m))
        return self.handoff / "submission.json"

    def test_a_native_manifest_in_the_handoff_is_used_and_declares_the_scripts(self):
        self.native_manifest()
        review = self.submit(lifecycle=False)
        self.assertEqual(review["state"], "validated", json.dumps(review["findings"], indent=1))
        self.assertNotIn("submission-scripts-undeclared", [f["code"] for f in review["findings"]])
        self.assertEqual(review["history"][-1]["via"], "unleashify-handoff (native submission.json)")

    def test_a_native_manifest_that_disagrees_with_the_handoff_is_refused(self):
        m, _ = from_unleashify_handoff(self.handoff)
        self.native_manifest(artifact={**m["artifact"], "sha256": "0" * 64})
        with self.assertRaisesRegex(SubmissionError, "disagrees with the handoff"):
            self.submit(lifecycle=False)
        self.native_manifest(source={**m["source"], "commit": "c" * 40})
        with self.assertRaisesRegex(SubmissionError, "source.commit"):
            self.submit(lifecycle=False)

    def test_without_a_native_manifest_the_adapter_is_used(self):
        review = self.submit(lifecycle=False)
        self.assertIn("submission-scripts-undeclared", [f["code"] for f in review["findings"]])
        self.assertEqual(review["history"][-1]["via"], "unleashify-handoff")

    def test_local_file_route_reaches_prepare_packages(self):
        review = self.submit(lifecycle=False)
        sid, sha = review["submission"], review["sha256"]
        rm.approve(self.store, sid, sha, "Owner", "Approved: Candidate import of this exact build.")
        status, rep, review = rm.prepare(self.cfg, self.store, sid, create_package_metadata=True, via="local-file",
                                         free_demo_approval=f"Owner approved 2026-11-25: 0.1.0~beta1 (SHA-256 {sha}).")
        self.assertEqual(status, "staged", rep.render())
        self.assertEqual(load_lock(self.cfg)["packages"], [])               # nothing imported yet
        staged = self.root / "imports" / "local" / self.deb.name
        self.assertTrue(staged.is_file())
        pool_report = Report("repository")
        check_repository(self.reload(), load_pool(self.cfg, pool_report), pool_report)
        self.assertTrue(pool_report.ok, pool_report.render())              # approved staged file is allowed
        # What Prepare packages does in CI: the same request, the same validation engine.
        r = load_request(self.root / "imports" / f"{sid}.toml")
        self.assertEqual(r["source"]["kind"], "local-file")
        status, rep = run_request(self.cfg, r, GitHub(token=""), self.work)
        self.assertEqual(status, "imported", rep.render())
        e = load_lock(self.cfg)["packages"][0]
        self.assertEqual((e["sha256"], e["channel"]), (sha, "candidate"))
        prov = json.loads((self.root / (e["filename"] + ".provenance.json")).read_text())
        self.assertEqual((prov["provider"], prov["route"], prov["approval"]["sha256"]), ("executable-maker-local", "local-file", sha))
        self.assertTrue(prov["validation_levels"]["simulated_lifecycle"]["passed"])

    def test_validation_is_independent_of_the_providers_claims(self):
        prov = json.loads((self.handoff / "provenance.json").read_text())
        prov["app"]["minimum_os"] = "14.0"  # the package's Info.plist says 15.0
        (self.handoff / "provenance.json").write_text(json.dumps(prov))
        review = self.submit(lifecycle=False)
        self.assertEqual(review["state"], "refused")
        with self.assertRaises(rm.ReleaseError):
            rm.approve(self.store, review["submission"], review["sha256"], "Owner", "ok")

    def test_released_version_is_never_overwritten(self):
        self.write_meta(PKG, '[display]\nsummary = "x"\nknown_issues = "- n"\n[release]\nchannel = "beta"\n'
                             '[access]\nlevel = "public"\nexception = "Test."\nexception_versions = ["0.1.0~beta1"]\n')
        self.reload()
        from hcgrepo.importer import import_deb
        (self.work / "o").mkdir()
        other = app_deb(self.work / "o", version="0.1.0~beta1", depends="firmware (>= 15.0), uikittools, bash")
        self.assertEqual(import_deb(self.cfg, other, {"source": "t"})[0], "imported")
        review = self.submit(lifecycle=False)
        self.assertEqual(review["state"], "refused")
        self.assertIn("immutable-version", {f["code"] for f in review["findings"]})

    def test_store_must_be_outside_the_checkout(self):
        with self.assertRaisesRegex(rm.ReleaseError, "private"):
            rm.submit(self.cfg, self.root / "incoming", handoff=self.handoff)

    def test_deb_under_submissions_fails_validation(self):
        (self.root / "submissions" / "x").mkdir(parents=True)
        shutil.copyfile(self.deb, self.root / "submissions" / "x" / "a.deb")
        r = Report("repository")
        check_repository(self.cfg, load_pool(self.cfg, r), r)
        self.assertIn("artifact-outside-pool", r.codes())

    def test_ci_reports_local_submission_as_awaiting(self):
        review = self.submit(lifecycle=False)
        sid, sha = review["submission"], review["sha256"]
        rm.approve(self.store, sid, sha, "Owner", "Approved.")
        m, review, _ = rm.load_review(self.store, sid)
        (self.root / "submissions" / sid).mkdir(parents=True)
        rm._write_json(self.root / "submissions" / sid / "review.json", review, private=False)
        req_path = rm.write_import_record(self.cfg, m, review)
        r = load_request(req_path)
        with self.assertRaises(AwaitingLocalImport):
            run_request(self.cfg, r, GitHub(token=""), self.work, incoming=None)

    def test_superseded_candidates_are_listed(self):
        self.write_meta(PKG, '[display]\nsummary = "x"\nknown_issues = "- n"\n[release]\nchannel = "beta"\n'
                             '[access]\nlevel = "public"\nexception = "Test."\nexception_versions = ["1.0.0~b1", "1.0.0~b2"]\n')
        self.reload()
        from hcgrepo.importer import import_deb
        for v in ("1.0.0~b1", "1.0.0~b2"):
            d = self.work / v
            d.mkdir()
            self.assertEqual(import_deb(self.cfg, app_deb(d, version=v), {"source": "t"}, channel="candidate")[0], "imported")
        self.assertEqual(rm.superseded_candidates(self.cfg),
                         [{"package": PKG, "architecture": "iphoneos-arm64", "version": "1.0.0~b1", "superseded_by": "1.0.0~b2"}])


class CompatibilityTests(RepoTestCase):
    def test_status_needs_evidence(self):
        from hcgrepo import compat
        e = {"version": "1"}
        insp = {"inspection": {"depends": ""}}
        lc = dict(insp, validation_levels={"simulated_lifecycle": {"passed": True}})
        t = lambda stage, result: {"stage": stage, "result": result}  # noqa: E731
        self.assertEqual(compat.derive_status(e, {}, []), compat.UNTESTED)
        self.assertEqual(compat.derive_status(e, insp, []), compat.STATIC)
        self.assertEqual(compat.derive_status(e, lc, []), compat.LIFECYCLE)
        self.assertEqual(compat.derive_status(e, lc, [t("install", "pass")]), compat.PHYS_INSTALL)
        self.assertEqual(compat.derive_status(e, lc, [t("runtime", "partial")]), compat.PHYS_INSTALL)
        self.assertEqual(compat.derive_status(e, lc, [t("install", "pass"), t("runtime", "pass")]), compat.PHYS_LAUNCH)
        self.assertEqual(compat.derive_status(e, lc, [t("install", "fail")]), compat.INCOMPATIBLE)
        self.assertEqual(compat.derive_status({"status": "withdrawn"}, lc, [t("runtime", "pass")]), compat.WITHDRAWN)

    def test_not_published_unless_enabled(self):
        from hcgrepo.site import build
        from hcgrepo.importer import import_deb
        self.assertEqual(import_deb(self.cfg, tool_deb(self.work), {"source": "t"})[0], "imported")
        out = self.tmp / "site"
        build(self.cfg, "https://example.test/", out)
        self.assertFalse((out / "repo" / "compatibility.json").exists())
        cfgp = self.root / "config" / "repo.toml"
        cfgp.write_text(cfgp.read_text() + "\n[compatibility]\npublish = true\n")
        build(self.reload(), "https://example.test/", self.tmp / "site2")
        reg = json.loads((self.tmp / "site2" / "repo" / "compatibility.json").read_text())
        self.assertEqual(reg["packages"][0]["versions"][0]["status"], "Static validation passed")


class RequestKindTests(RepoTestCase):
    def write(self, body: str) -> Path:
        p = self.root / "imports"
        p.mkdir(exist_ok=True)
        f = p / "r.toml"
        f.write_text('[request]\npackage = "com.hakmicapitalgroup.x"\nversion = "1"\narchitecture = "iphoneos-arm64"\n'
                     'channel = "candidate"\n[source]\nrepository = "A/B"\ncommit = "' + COMMIT + '"\n' + body)
        return f

    def test_https_artifact_rules(self):
        ok = ('kind = "https-artifact"\nprovider = "hcg-server-build-host"\nurl = "https://build.example/x.deb"\n'
              'size = 10\nsha256 = "' + "c" * 64 + '"\nsubmission = "submissions/x/submission.json"\nauth_env = "HCG_BUILD_HOST_TOKEN"\n')
        load_request(self.write(ok))
        for bad in (ok.replace("https://", "http://"), ok.replace("HCG_BUILD_HOST_TOKEN", "ghp_secretvalue"),
                    ok.replace('size = 10\n', ''), ok.replace("hcg-server-build-host", "github-actions"),
                    ok + "run_id = 5\n"):
            with self.assertRaises(ImportError_):
                load_request(self.write(bad))

    def test_https_fetch_refuses_plain_http(self):
        with self.assertRaises(ImportError_):
            fetch_https_artifact("http://example.invalid/x.deb", self.work, "c" * 64, 10)

    def test_local_submission_refuses_invented_run(self):
        body = ('kind = "local-submission"\nprovider = "executable-maker-local"\nsha256 = "' + "c" * 64 +
                '"\nsubmission = "submissions/x/submission.json"\nrun_id = 7\n')
        with self.assertRaisesRegex(ImportError_, "no CI run"):
            load_request(self.write(body))


if __name__ == "__main__":
    import unittest
    unittest.main()
