"""Native package contracts for the optional monitorize-vkms integration."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class VkmsPackagingTest(unittest.TestCase):
    def test_native_packages_suggest_external_helper(self):
        sources = (
            "packaging/arch/PKGBUILD",
            "packaging/rpm/monitorize.spec",
            "packaging/tumbleweed/monitorize.spec",
            "packaging/deb/debian-trixie/debian/control",
            "packaging/deb/ubuntu-24.04/debian/control",
            "packaging/deb/ubuntu-26.04/debian/control",
        )
        for source in sources:
            with self.subTest(source=source):
                self.assertIn("monitorize-vkms", (ROOT / source).read_text())

    def test_native_docs_explain_helper_is_optional_and_separate(self):
        for source in (
            "packaging/arch/README.md", "packaging/deb/README.md",
            "packaging/rpm/README.md", "packaging/tumbleweed/README.md",
        ):
            with self.subTest(source=source):
                content = (ROOT / source).read_text()
                self.assertIn("monitorize-vkms", content)
                self.assertIn("stock `vkms` alone", content.lower())


if __name__ == "__main__":
    unittest.main()
