#!/usr/bin/env python3
"""Align retained HFS runtime assertions with exact producer dependency pins."""

from __future__ import annotations

import argparse
import re
import tomllib
from pathlib import Path


class ContractError(ValueError):
    """Raised when the retained runtime dependency contract cannot be aligned."""


_PIN_RE = re.compile(r"^graphon==([A-Za-z0-9][A-Za-z0-9._+-]*)$")
_ASSERTION_PATTERNS = {
    "api": re.compile(
        r"(?P<prefix>/app/api/\.venv/bin/python -c \'import flask; from importlib\.metadata import version; "
        r'assert version\("graphon"\) == ")[^"]+(?P<suffix>"\')'
    ),
    "agent": re.compile(
        r"(?P<prefix>/opt/dify-agent/\.venv/bin/python -c \'import dify_agent\.server\.app; import shellctl\.client; "
        r'from importlib\.metadata import version; assert version\("graphon"\) == ")[^"]+(?P<suffix>"\')'
    ),
}


def _table(payload: object, path: tuple[str, ...]) -> object:
    current = payload
    for key in path:
        if not isinstance(current, dict) or key not in current:
            raise ContractError(f"pyproject is missing {'.'.join(path)}")
        current = current[key]
    return current


def _graphon_pin(pyproject: Path, dependency_path: tuple[str, ...]) -> str:
    payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    dependencies = _table(payload, dependency_path)
    if not isinstance(dependencies, list) or not all(
        isinstance(item, str) for item in dependencies
    ):
        raise ContractError(
            f"{pyproject} {'.'.join(dependency_path)} must be a string list"
        )

    matches = [
        match.group(1) for item in dependencies if (match := _PIN_RE.fullmatch(item))
    ]
    if len(matches) != 1:
        raise ContractError(
            f"{pyproject} must contain exactly one graphon== pin in {'.'.join(dependency_path)}"
        )
    return matches[0]


def _replace_assertion(text: str, component: str, version: str) -> str:
    updated, count = _ASSERTION_PATTERNS[component].subn(
        rf"\g<prefix>{version}\g<suffix>", text
    )
    if count != 1:
        raise ContractError(
            f"retained Dockerfile must contain exactly one {component} graphon assertion"
        )
    return updated


def align_dependency_assertions(
    dockerfile: Path,
    api_pyproject: Path,
    agent_pyproject: Path,
) -> tuple[str, str, bool]:
    api_version = _graphon_pin(api_pyproject, ("project", "dependencies"))
    agent_version = _graphon_pin(
        agent_pyproject, ("project", "optional-dependencies", "server")
    )

    original = dockerfile.read_text(encoding="utf-8")
    updated = _replace_assertion(original, "api", api_version)
    updated = _replace_assertion(updated, "agent", agent_version)
    changed = updated != original
    if changed:
        dockerfile.write_text(updated, encoding="utf-8")
    return api_version, agent_version, changed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dockerfile", type=Path, required=True)
    parser.add_argument("--api-pyproject", type=Path, required=True)
    parser.add_argument("--agent-pyproject", type=Path, required=True)
    args = parser.parse_args()

    try:
        api_version, agent_version, changed = align_dependency_assertions(
            args.dockerfile,
            args.api_pyproject,
            args.agent_pyproject,
        )
    except (ContractError, OSError, tomllib.TOMLDecodeError) as exc:
        parser.error(str(exc))

    state = "updated" if changed else "already aligned"
    print(
        f"retained graphon assertions {state}: api={api_version}, agent={agent_version}"
    )


if __name__ == "__main__":
    main()
