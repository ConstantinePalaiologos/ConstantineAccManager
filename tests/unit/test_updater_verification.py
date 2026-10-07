import hashlib
import os
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from features import updater

PAYLOAD = b"MZ" + b"\x00" * 1022


def sha256_digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


class VerifyDownloadTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def write(self, data):
        path = os.path.join(self.folder.name, "update.exe")
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def test_matching_size_and_digest_pass(self):
        updater.verify_download(self.write(PAYLOAD), len(PAYLOAD), sha256_digest(PAYLOAD))

    def test_digest_is_case_insensitive(self):
        updater.verify_download(self.write(PAYLOAD), len(PAYLOAD), sha256_digest(PAYLOAD).upper().replace("SHA256", "sha256"))

    def test_wrong_digest_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "checksum"):
            updater.verify_download(self.write(PAYLOAD), len(PAYLOAD), sha256_digest(b"other"))

    def test_wrong_size_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "bytes"):
            updater.verify_download(self.write(PAYLOAD), len(PAYLOAD) + 1, None)

    def test_empty_file_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "empty"):
            updater.verify_download(self.write(b""), None, None)

    def test_file_that_is_not_an_executable_is_rejected(self):
        data = b"<html>rate limited</html>"
        with self.assertRaisesRegex(RuntimeError, "executable"):
            updater.verify_download(self.write(data), len(data), sha256_digest(data))

    def test_missing_digest_still_checks_size(self):
        updater.verify_download(self.write(PAYLOAD), len(PAYLOAD), None)
        with self.assertRaises(RuntimeError):
            updater.verify_download(self.write(PAYLOAD), 5, None)


class TrustedUrlTests(unittest.TestCase):
    def test_only_https_github_downloads_are_trusted(self):
        good = "https://github.com/evanovar/RobloxAccountManager/releases/download/v1/x.exe"
        self.assertTrue(updater.is_trusted_download_url(good))
        for bad in (
            "http://github.com/x.exe",
            "https://github.com.evil.example/x.exe",
            "https://example.com/github.com/x.exe",
            "ftp://github.com/x.exe",
            "",
        ):
            with self.subTest(url=bad):
                self.assertFalse(updater.is_trusted_download_url(bad))


class DownloadUpdateTests(unittest.TestCase):
    def run_download(self, asset, body, headers=None):
        response = MagicMock()
        response.headers = headers if headers is not None else {"content-length": str(len(body))}
        response.iter_content.return_value = [body[i:i + 512] for i in range(0, len(body), 512)]
        finished = threading.Event()
        outcome = {}

        def on_done(success, message):
            outcome["result"] = (success, message)
            finished.set()

        with patch.object(updater, "get_update_target", return_value="C:/app/RAM.exe"), \
                patch.object(updater, "get_exe_asset", return_value=asset), \
                patch.object(updater.requests, "get", return_value=response), \
                patch.object(updater, "_launch_installer") as launch:
            updater.download_update(lambda _: None, on_done)
            self.assertTrue(finished.wait(10))
        return outcome["result"], launch

    def asset(self, **overrides):
        base = {
            "url": "https://github.com/evanovar/RobloxAccountManager/releases/download/v1/EvanovarRAM-v1.0.0.exe",
            "name": "EvanovarRAM-v1.0.0.exe",
            "size": len(PAYLOAD),
            "digest": sha256_digest(PAYLOAD),
        }
        base.update(overrides)
        return base

    def test_verified_download_starts_the_installer(self):
        (success, _), launch = self.run_download(self.asset(), PAYLOAD)
        self.assertTrue(success)
        launch.assert_called_once()

    def test_tampered_download_never_reaches_the_installer(self):
        tampered = PAYLOAD[:-1] + b"\x01"
        (success, message), launch = self.run_download(self.asset(), tampered)
        self.assertFalse(success)
        self.assertIn("checksum", message)
        launch.assert_not_called()

    def test_truncated_download_never_reaches_the_installer(self):
        (success, message), launch = self.run_download(
            self.asset(), PAYLOAD[:512], headers={"content-length": str(len(PAYLOAD))}
        )
        self.assertFalse(success)
        self.assertIn("interrupted", message)
        launch.assert_not_called()

    def test_untrusted_address_is_refused_before_downloading(self):
        (success, message), launch = self.run_download(self.asset(url="https://example.com/x.exe"), PAYLOAD)
        self.assertFalse(success)
        self.assertIn("github.com", message)
        launch.assert_not_called()


class UpdateSourceTests(unittest.TestCase):
    def test_updates_come_from_the_constantine_repository(self):
        for address in (updater.GITHUB_API, updater.RELEASES_PAGE):
            self.assertIn("ConstantinePalaiologos/ConstantineAccManager", address)
            self.assertNotIn("evanovar", address.lower())

    def test_only_constantine_release_files_are_recognised(self):
        pattern = updater.RELEASE_ASSET_PATTERN
        self.assertTrue(pattern.fullmatch("ConstantineAccManager-v2.7.2.1.exe"))
        self.assertTrue(pattern.fullmatch("ConstantineAccManager-v3.0.0.exe"))
        self.assertFalse(pattern.fullmatch("EvanovarRAM-v2.7.2.exe"))
        self.assertFalse(pattern.fullmatch("ConstantineAccManager-v2.7.2.1.zip"))

    def test_the_right_file_is_picked_from_a_release(self):
        release = {
            "tag_name": "v2.7.3.1",
            "assets": [
                {"name": "EvanovarRAM-v2.7.3.exe", "browser_download_url": "https://github.com/x/stock.exe"},
                {
                    "name": "ConstantineAccManager-v2.7.3.1.exe",
                    "browser_download_url": "https://github.com/x/ours.exe",
                    "size": 1234,
                    "digest": "sha256:abc",
                },
            ],
        }
        response = MagicMock()
        response.json.return_value = release
        with patch.object(updater.requests, "get", return_value=response):
            asset = updater.get_exe_asset()
        self.assertEqual(asset["name"], "ConstantineAccManager-v2.7.3.1.exe")
        self.assertEqual(asset["size"], 1234)
        self.assertEqual(asset["digest"], "sha256:abc")

    def test_a_release_without_our_file_offers_nothing(self):
        release = {
            "tag_name": "v9.9.9",
            "assets": [{"name": "EvanovarRAM-v9.9.9.exe", "browser_download_url": "https://github.com/x/stock.exe"}],
        }
        response = MagicMock()
        response.json.return_value = release
        with patch.object(updater.requests, "get", return_value=response):
            self.assertIsNone(updater.get_exe_asset())


if __name__ == "__main__":
    unittest.main()
