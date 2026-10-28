import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("filter_published", Path(__file__).with_name("filter-published-python.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PublishedArtifactsTest(unittest.TestCase):
    def test_resumes_after_one_verified_wheel(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            for stem in ("watasu", "watasu_code_interpreter"):
                for suffix in ("-py3-none-any.whl", ".tar.gz"):
                    (directory / f"{stem}-0.1.76{suffix}").write_bytes(b"built artifact")
            wheel = "watasu-0.1.76-py3-none-any.whl"
            def fetch(url):
                if "/watasu/" in url:
                    return {"urls": [{"filename": wheel, "digests": {"sha256": hashlib.sha256(b"built artifact").hexdigest()}}]}
                return {}
            self.assertTrue(module.filter_existing(directory, "0.1.76", fetch))
            self.assertEqual(len(list(directory.iterdir())), 3)
            self.assertFalse((directory / wheel).exists())

    def test_mismatch_preserves_all_built_files(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            for stem in ("watasu", "watasu_code_interpreter"):
                for suffix in ("-py3-none-any.whl", ".tar.gz"):
                    (directory / f"{stem}-0.1.76{suffix}").write_bytes(b"built artifact")
            def fetch(_):
                return {"urls": [{"filename": "watasu-0.1.76-py3-none-any.whl", "digests": {"sha256": "0" * 64}}]}
            with self.assertRaisesRegex(ValueError, "differs"):
                module.filter_existing(directory, "0.1.76", fetch)
            self.assertEqual(len(list(directory.iterdir())), 4)


if __name__ == "__main__":
    unittest.main()
