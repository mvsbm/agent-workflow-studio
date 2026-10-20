"""The studio's own traceability matrix: every requirement in REQUIREMENTS.md is verified, either by a
test CI runs (the test file says "Verifies: R8, R27") or by a stated other means ("*Verified by:* …",
e.g. the real-agent evals), and no test cites a requirement that does not exist.

Verifies: R36
"""

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REQUIREMENT = re.compile(r"^\*\*(R\d+) · ([^*]+)\*\*", re.M)
MARKER = re.compile(r"Verifies:\s*(R\d+(?:\s*,\s*R\d+)*)")


def requirements() -> dict[str, str]:
    """Requirement id → its paragraph (title included)."""
    text = (REPO / "REQUIREMENTS.md").read_text(encoding="utf-8")
    found = list(REQUIREMENT.finditer(text))
    return {
        m[1]: text[m.start() : found[i + 1].start() if i + 1 < len(found) else len(text)]
        for i, m in enumerate(found)
    }


def ci_tests() -> list[Path]:
    """The test files CI runs: unit, web, the browser tests run.py lists, and the capability tests."""
    listed = re.findall(
        r'"([a-z_]+\.cjs)"', (REPO / "tests/e2e/run.py").read_text(encoding="utf-8")
    )
    return [
        *sorted((REPO / "tests/unit").glob("test_*.py")),
        *sorted((REPO / "tests/web").glob("test_*.mjs")),
        *(REPO / "tests/e2e" / name for name in listed),
        REPO / "tests/e2e/capabilities.cjs",
        *sorted((REPO / "tests/e2e/capabilities").glob("*.cjs")),
    ]


def matrix() -> dict[str, list[str]]:
    """Requirement id → the CI test files that verify it."""
    found: dict[str, list[str]] = {}
    for path in ci_tests():
        for marker in MARKER.findall(path.read_text(encoding="utf-8")):
            for rid in re.findall(r"R\d+", marker):
                found.setdefault(rid, []).append(str(path.relative_to(REPO)))
    return found


class TraceabilityTests(unittest.TestCase):
    def test_every_requirement_is_verified(self) -> None:
        verified = matrix()
        unverified = [
            rid
            for rid, text in requirements().items()
            if rid not in verified and "*Verified by:*" not in text
        ]
        self.assertEqual(
            unverified,
            [],
            "Name a CI test that verifies each (Verifies: Rn in the test file), or say how it is "
            "verified instead (*Verified by:* …) in REQUIREMENTS.md.",
        )

    def test_every_ci_test_file_names_what_it_verifies(self) -> None:
        unnamed = [
            str(p.relative_to(REPO))
            for p in ci_tests()
            if not MARKER.search(p.read_text(encoding="utf-8"))
        ]
        self.assertEqual(
            unnamed, [], "Add `Verifies: Rn` naming the requirement each file protects."
        )

    def test_tests_cite_only_real_requirements(self) -> None:
        unknown = sorted(set(matrix()) - set(requirements()))
        self.assertEqual(
            unknown, [], "These tests cite requirements REQUIREMENTS.md does not have."
        )


if __name__ == "__main__":
    unittest.main()
