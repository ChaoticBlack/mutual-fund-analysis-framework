"""Guards against publishing personal data: no data files inside the repo folder, and no personal-looking strings in
source, tests or docs (the placeholder PAN ABCDE1234F is allowed)."""
import os, re, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules", "data"}
DATA_EXT = (".xlsx", ".xls", ".pdf", ".csv")
TEXT_EXT = (".py", ".md", ".txt", ".yml", ".yaml", ".toml", ".cfg", ".gitignore", ".gitattributes")


def files(exts):
    for dp, dn, fn in os.walk(ROOT):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        for f in fn:
            if f.lower().endswith(exts):
                yield os.path.join(dp, f)


class HygieneTests(unittest.TestCase):
    def test_no_data_files_in_the_repository_folder(self):
        found = [os.path.relpath(p, ROOT) for p in files(DATA_EXT)]
        self.assertEqual(found, [], "keep statements, workbooks and index files in a data folder OUTSIDE the repo")

    def test_no_personal_looking_strings_in_text_files(self):
        email = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}")
        phone = re.compile(r"(?<!\d)[6-9]\d{9}(?!\d)")
        pan = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
        bad = []
        for p in files(TEXT_EXT):
            if os.path.basename(p) == "test_repo_hygiene.py":
                continue
            text = open(p, encoding="utf-8", errors="ignore").read()
            for rx, label in ((email, "e-mail"), (phone, "phone-like number"), (pan, "PAN-like id")):
                for m in rx.finditer(text):
                    if label == "PAN-like id" and m.group(0) == "ABCDE1234F":
                        continue
                    if label == "e-mail" and m.group(0).endswith("users.noreply.github.com"):
                        continue
                    bad.append(f"{os.path.relpath(p, ROOT)}: {label} {m.group(0)[:12]}...")
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()