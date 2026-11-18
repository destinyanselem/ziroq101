from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def test_google_genai_major_version_is_bounded():
    lines = (ROOT / "worker" / "requirements.txt").read_text(encoding="utf-8").splitlines()
    requirement = next(line for line in lines if line.lower().startswith("google-genai"))
    assert re.search(r"<\s*3(?:\.0(?:\.0)?)?", requirement), requirement


def test_worker_requirements_have_no_duplicate_packages():
    requirements = (ROOT / "worker" / "requirements.txt").read_text(encoding="utf-8").splitlines()
    names = [
        re.split(r"[<>=!~;\[]", line.strip(), maxsplit=1)[0].lower().replace("_", "-")
        for line in requirements
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert len(names) == len(set(names)), names
