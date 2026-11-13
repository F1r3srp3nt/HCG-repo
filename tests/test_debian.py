import unittest

import support  # noqa: F401  (sets sys.path)
from hcgrepo import debian


class VersionTests(unittest.TestCase):
    def test_dpkg_ordering(self):
        # Pairs (a, b) with a < b, following dpkg --compare-versions.
        for a, b in [("1.0", "1.0.1"), ("1.0~beta1", "1.0"), ("1.0-1", "1.0-2"), ("1.9", "1.10"),
                     ("0.1.0-1", "0.1.0-2"), ("1.0a", "1.0b"), ("1.0", "1.0+git1"), ("1:0.1", "2:0.0"),
                     ("1.0~~", "1.0~"), ("2.0-1", "10.0-1")]:
            with self.subTest(a=a, b=b):
                self.assertEqual(debian.compare_versions(a, b), -1)
                self.assertEqual(debian.compare_versions(b, a), 1)
        self.assertEqual(debian.compare_versions("1.0-0", "1.0-0"), 0)
        self.assertEqual(debian.compare_versions("01.0", "1.0"), 0)

    def test_invalid_versions(self):
        for v in ("", "a1.0", "1.0 beta", "x:1.0", "1.0-"):
            with self.subTest(v=v), self.assertRaises(debian.ControlError):
                debian.parse_version(v)


class RelationTests(unittest.TestCase):
    def test_parse(self):
        g = debian.parse_relations("firmware (>= 17.0), uikittools, bash | zsh, libfoo:any (<< 2)")
        self.assertEqual([[r.name for r in grp] for grp in g], [["firmware"], ["uikittools"], ["bash", "zsh"], ["libfoo"]])
        self.assertEqual(g[0][0].op, ">=")
        self.assertTrue(g[3][0].satisfied_by("1.9"))
        self.assertFalse(g[3][0].satisfied_by("2"))

    def test_bad_syntax(self):
        for bad in ("foo (>= )", "Foo", "foo,,bar", "foo (=> 1)"):
            with self.subTest(bad=bad), self.assertRaises(debian.ControlError):
                debian.parse_relations(bad)

    def test_firmware_bounds(self):
        self.assertEqual(debian.firmware_bounds("firmware (>= 15.0), firmware (<< 17.0)"), ("15.0", "< 17.0"))
        self.assertEqual(debian.firmware_bounds("bash"), (None, None))


class ControlTests(unittest.TestCase):
    def test_roundtrip_multiline(self):
        text = "Package: a\nDescription: short\n long line\n .\n more\n"
        fields = debian.parse_control(text)
        self.assertEqual(debian.format_stanza(fields), text)

    def test_duplicate_field(self):
        with self.assertRaises(debian.ControlError):
            debian.parse_control("Package: a\nPackage: b\n")


if __name__ == "__main__":
    unittest.main()
