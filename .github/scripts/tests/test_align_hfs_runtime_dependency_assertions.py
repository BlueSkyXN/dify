from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from align_hfs_runtime_dependency_assertions import (
    ContractError,
    align_dependency_assertions,
)


_DOCKERFILE = """\
RUN /app/api/.venv/bin/python -c 'import flask; from importlib.metadata import version; assert version("graphon") == "0.6.0"' \\
    && /opt/dify-agent/.venv/bin/python -c 'import dify_agent.server.app; import shellctl.client; from importlib.metadata import version; assert version("graphon") == "0.5.2"'
"""
_API_PYPROJECT = """\
[project]
dependencies = ["graphon==0.7.0"]
"""
_AGENT_PYPROJECT = """\
[project.optional-dependencies]
server = ["graphon==0.5.2"]
"""


class AlignHfsRuntimeDependencyAssertionsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.dockerfile = root / "Dockerfile"
        self.api_pyproject = root / "api.toml"
        self.agent_pyproject = root / "agent.toml"
        self.dockerfile.write_text(_DOCKERFILE, encoding="utf-8")
        self.api_pyproject.write_text(_API_PYPROJECT, encoding="utf-8")
        self.agent_pyproject.write_text(_AGENT_PYPROJECT, encoding="utf-8")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_aligns_retained_assertions_to_exact_producer_pins(self) -> None:
        api_version, agent_version, changed = align_dependency_assertions(
            self.dockerfile,
            self.api_pyproject,
            self.agent_pyproject,
        )

        self.assertEqual(api_version, "0.7.0")
        self.assertEqual(agent_version, "0.5.2")
        self.assertTrue(changed)
        text = self.dockerfile.read_text(encoding="utf-8")
        self.assertIn('version("graphon") == "0.7.0"', text)
        self.assertIn('version("graphon") == "0.5.2"', text)

        _, _, changed_again = align_dependency_assertions(
            self.dockerfile,
            self.api_pyproject,
            self.agent_pyproject,
        )
        self.assertFalse(changed_again)

    def test_rejects_missing_retained_assertion(self) -> None:
        self.dockerfile.write_text("FROM scratch\n", encoding="utf-8")

        with self.assertRaisesRegex(ContractError, "exactly one api graphon assertion"):
            align_dependency_assertions(
                self.dockerfile, self.api_pyproject, self.agent_pyproject
            )

    def test_rejects_non_exact_producer_pin(self) -> None:
        self.api_pyproject.write_text(
            '[project]\ndependencies = ["graphon>=0.7.0"]\n', encoding="utf-8"
        )

        with self.assertRaisesRegex(ContractError, "exactly one graphon== pin"):
            align_dependency_assertions(
                self.dockerfile, self.api_pyproject, self.agent_pyproject
            )


if __name__ == "__main__":
    unittest.main()
