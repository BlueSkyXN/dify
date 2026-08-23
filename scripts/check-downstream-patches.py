#!/usr/bin/env python3
"""Validate the bounded BlueSkyXN/dify downstream patch queue."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPOSITORY_ROOT / ".fork" / "downstream-patches.json"
CALLER_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "publish-hfs-runtime.yml"
FULL_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
PATCH_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
GITHUB_URL_PATTERN = re.compile(
    r"^https://github\.com/[^/]+/[^/]+/(?:issues|pull)/[1-9][0-9]*$"
)
REUSABLE_WORKFLOW_PATTERN = re.compile(
    r"^\s*uses:\s*BlueSkyXN/Dify-all-in-one-HFS/\.github/workflows/"
    r"produce-dify-runtime\.yml@([0-9a-f]{40})\s*(?:#.*)?$",
    re.MULTILINE,
)


class Validation:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def require(self, condition: bool, message: str) -> None:
        if not condition:
            self.errors.append(message)


def run_git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=REPOSITORY_ROOT,
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def read_manifest(validation: Validation) -> dict[str, Any]:
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        validation.errors.append(
            f"cannot read {MANIFEST_PATH.relative_to(REPOSITORY_ROOT)}: {error}"
        )
        return {}

    if not isinstance(manifest, dict):
        validation.errors.append("manifest root must be a JSON object")
        return {}
    return manifest


def validate_text_field(
    validation: Validation, patch_id: str, patch: dict[str, Any], field: str
) -> None:
    value = patch.get(field)
    validation.require(
        isinstance(value, str) and bool(value.strip()),
        f"patch {patch_id!r} must define a non-empty {field}",
    )


def validate_manifest(
    validation: Validation, manifest: dict[str, Any]
) -> tuple[str | None, list[dict[str, Any]], set[str]]:
    validation.require(manifest.get("schema_version") == 1, "schema_version must be 1")

    upstream_base = manifest.get("upstream_base")
    validation.require(
        isinstance(upstream_base, str)
        and bool(FULL_SHA_PATTERN.fullmatch(upstream_base)),
        "upstream_base must be a lowercase 40-character commit SHA",
    )

    max_patches = manifest.get("max_patches")
    max_files = manifest.get("max_files")
    validation.require(
        isinstance(max_patches, int)
        and not isinstance(max_patches, bool)
        and max_patches > 0,
        "max_patches must be a positive integer",
    )
    validation.require(
        isinstance(max_files, int)
        and not isinstance(max_files, bool)
        and max_files > 0,
        "max_files must be a positive integer",
    )

    raw_patches = manifest.get("patches")
    if not isinstance(raw_patches, list):
        validation.errors.append("patches must be a JSON array")
        return upstream_base if isinstance(upstream_base, str) else None, [], set()

    if isinstance(max_patches, int) and not isinstance(max_patches, bool):
        validation.require(
            len(raw_patches) <= max_patches,
            f"patch budget exceeded: {len(raw_patches)} patches > max_patches {max_patches}",
        )

    patches: list[dict[str, Any]] = []
    patch_ids: set[str] = set()
    registered_paths: set[str] = set()

    for index, raw_patch in enumerate(raw_patches):
        if not isinstance(raw_patch, dict):
            validation.errors.append(f"patches[{index}] must be a JSON object")
            continue
        patches.append(raw_patch)

        patch_id = raw_patch.get("id")
        if not isinstance(patch_id, str) or not PATCH_ID_PATTERN.fullmatch(patch_id):
            validation.errors.append(
                f"patches[{index}].id must use lowercase kebab-case: {patch_id!r}"
            )
            display_id = f"patches[{index}]"
        else:
            display_id = patch_id
            validation.require(
                patch_id not in patch_ids, f"duplicate patch id: {patch_id}"
            )
            patch_ids.add(patch_id)

        for field in ("reason", "drop_when", "owner"):
            validate_text_field(validation, display_id, raw_patch, field)
        validation.require(
            raw_patch.get("status") == "active",
            f"patch {display_id!r} status must be 'active'; remove retired entries instead",
        )

        for field in ("upstream_issue", "upstream_pr"):
            value = raw_patch.get(field)
            validation.require(
                value is None
                or (
                    isinstance(value, str) and bool(GITHUB_URL_PATTERN.fullmatch(value))
                ),
                f"patch {display_id!r} {field} must be null or a full GitHub issue/PR URL",
            )

        tests = raw_patch.get("tests")
        validation.require(
            isinstance(tests, list)
            and bool(tests)
            and all(isinstance(test, str) and bool(test.strip()) for test in tests),
            f"patch {display_id!r} tests must be a non-empty string array",
        )

        paths = raw_patch.get("paths")
        if not isinstance(paths, list) or not paths:
            validation.errors.append(
                f"patch {display_id!r} paths must be a non-empty array"
            )
            continue

        local_paths: set[str] = set()
        for path in paths:
            if not isinstance(path, str):
                validation.errors.append(
                    f"patch {display_id!r} contains a non-string path: {path!r}"
                )
                continue
            pure_path = PurePosixPath(path)
            valid_path = (
                bool(path)
                and not pure_path.is_absolute()
                and path == pure_path.as_posix()
                and ".." not in pure_path.parts
                and "." not in pure_path.parts
            )
            validation.require(
                valid_path, f"patch {display_id!r} contains an invalid path: {path!r}"
            )
            if not valid_path:
                continue
            validation.require(
                path not in local_paths, f"patch {display_id!r} repeats path: {path}"
            )
            validation.require(
                path not in registered_paths,
                f"path is owned by more than one patch: {path}",
            )
            local_paths.add(path)
            registered_paths.add(path)

    if isinstance(max_files, int) and not isinstance(max_files, bool):
        validation.require(
            len(registered_paths) <= max_files,
            f"file budget exceeded: {len(registered_paths)} files > max_files {max_files}",
        )

    return (
        upstream_base if isinstance(upstream_base, str) else None,
        patches,
        registered_paths,
    )


def validate_git_state(
    validation: Validation, upstream_base: str | None, registered_paths: set[str]
) -> set[str]:
    if upstream_base is None or not FULL_SHA_PATTERN.fullmatch(upstream_base):
        return set()

    object_result = run_git(
        "cat-file", "-e", f"{upstream_base}^{{commit}}", check=False
    )
    if object_result.returncode != 0:
        validation.errors.append(
            f"upstream_base commit is unavailable; checkout full history: {upstream_base}"
        )
        return set()

    ancestry_result = run_git(
        "merge-base", "--is-ancestor", upstream_base, "HEAD", check=False
    )
    validation.require(
        ancestry_result.returncode == 0,
        f"upstream_base is not an ancestor of HEAD: {upstream_base}",
    )

    diff_result = run_git("diff", "--name-only", "-z", f"{upstream_base}..HEAD")
    changed_paths = {path for path in diff_result.stdout.split("\0") if path}
    unexpected_paths = sorted(changed_paths - registered_paths)
    stale_paths = sorted(registered_paths - changed_paths)
    if unexpected_paths:
        validation.errors.append(
            "unregistered downstream paths:\n  - " + "\n  - ".join(unexpected_paths)
        )
    if stale_paths:
        validation.errors.append(
            "registered paths no longer differ from upstream_base:\n  - "
            + "\n  - ".join(stale_paths)
        )
    return changed_paths


def extract_single_sha(validation: Validation, content: str, field: str) -> str | None:
    pattern = re.compile(
        rf"^\s*{re.escape(field)}:\s*['\"]?([0-9a-f]{{40}})['\"]?\s*(?:#.*)?$",
        re.MULTILINE,
    )
    matches = pattern.findall(content)
    validation.require(
        len(matches) == 1, f"caller must define exactly one literal {field} SHA"
    )
    return matches[0] if len(matches) == 1 else None


def validate_hfs_caller(validation: Validation, upstream_base: str | None) -> None:
    try:
        content = CALLER_PATH.read_text(encoding="utf-8")
    except OSError as error:
        validation.errors.append(
            f"cannot read {CALLER_PATH.relative_to(REPOSITORY_ROOT)}: {error}"
        )
        return

    reusable_refs = REUSABLE_WORKFLOW_PATTERN.findall(content)
    validation.require(
        len(reusable_refs) == 1,
        "caller must use exactly one immutable BlueSkyXN/Dify-all-in-one-HFS producer workflow",
    )
    reusable_ref = reusable_refs[0] if len(reusable_refs) == 1 else None
    contract_ref = extract_single_sha(validation, content, "contract_ref")
    caller_upstream_base = extract_single_sha(validation, content, "upstream_base_ref")

    if reusable_ref is not None and contract_ref is not None:
        validation.require(
            reusable_ref == contract_ref,
            f"reusable workflow ref {reusable_ref} does not match contract_ref {contract_ref}",
        )
    if caller_upstream_base is not None and upstream_base is not None:
        validation.require(
            caller_upstream_base == upstream_base,
            f"caller upstream_base_ref {caller_upstream_base} does not match manifest {upstream_base}",
        )


def main() -> int:
    validation = Validation()
    manifest = read_manifest(validation)
    upstream_base, patches, registered_paths = validate_manifest(validation, manifest)
    changed_paths = validate_git_state(validation, upstream_base, registered_paths)
    validate_hfs_caller(validation, upstream_base)

    if validation.errors:
        print("Downstream patch gate failed:", file=sys.stderr)
        for error in validation.errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print("Downstream patch gate passed")
    print(f"  upstream_base: {upstream_base}")
    print(f"  patches: {len(patches)}")
    print(f"  downstream files: {len(changed_paths)}")
    for patch in patches:
        print(
            f"  - {patch['id']}: {len(patch['paths'])} file(s); owner={patch['owner']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
