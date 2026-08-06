#!/usr/bin/env python3
"""Align the retained HFS runtime build with the exact producer contract."""

from __future__ import annotations

import argparse
import re
import tomllib
from pathlib import Path


class ContractError(ValueError):
    """Raised when the retained runtime build contract cannot be aligned."""


_PIN_RE = re.compile(r"^graphon==([A-Za-z0-9][A-Za-z0-9._+-]*)$")
_PYTHONUNBUFFERED_RE = re.compile(r"^ENV PYTHONUNBUFFERED=1$", re.MULTILINE)
_PYTHONDONTWRITEBYTECODE_RE = re.compile(
    r"^ENV PYTHONDONTWRITEBYTECODE=(?P<value>\S+)$", re.MULTILINE
)
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


def _disable_bytecode_cache_writes(text: str) -> str:
    directives = list(_PYTHONDONTWRITEBYTECODE_RE.finditer(text))
    if directives:
        if len(directives) != 1 or directives[0].group("value") != "1":
            raise ContractError(
                "retained Dockerfile must contain at most one "
                "ENV PYTHONDONTWRITEBYTECODE=1 directive"
            )
        updated = text
        directive = directives[0]
    else:
        updated, count = _PYTHONUNBUFFERED_RE.subn(
            "ENV PYTHONUNBUFFERED=1\nENV PYTHONDONTWRITEBYTECODE=1", text
        )
        if count != 1:
            raise ContractError(
                "retained Dockerfile must contain exactly one "
                "ENV PYTHONUNBUFFERED=1 insertion anchor"
            )
        directive = _PYTHONDONTWRITEBYTECODE_RE.search(updated)
        if (
            directive is None
        ):  # pragma: no cover - guarded by the exact replacement above
            raise ContractError(
                "failed to disable retained Python bytecode cache writes"
            )

    assertion_positions = [
        match.start()
        for pattern in _ASSERTION_PATTERNS.values()
        if (match := pattern.search(updated)) is not None
    ]
    if len(assertion_positions) != len(_ASSERTION_PATTERNS) or directive.start() > min(
        assertion_positions
    ):
        raise ContractError(
            "ENV PYTHONDONTWRITEBYTECODE=1 must precede retained Python checks"
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
    updated = _disable_bytecode_cache_writes(updated)
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
        "retained runtime contract "
        f"{state}: api_graphon={api_version}, agent_graphon={agent_version}, "
        "python_bytecode=disabled"
    )


if __name__ == "__main__":
    main()
