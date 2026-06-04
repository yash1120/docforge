from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from .walk import walk_repo
from .detect_stack import (
    detect_dependencies,
    detect_entry_points,
    detect_languages,
    detect_license,
    detect_public_api,
    detect_top_level_modules,
)


class Manifest(BaseModel):
    """Everything docforge needs to know about a repo before reading any code."""

    repo_path: str
    repo_name: str
    primary_language: Optional[str] = None
    languages: dict[str, int] = Field(default_factory=dict)
    frameworks: list[str] = Field(default_factory=list)
    entry_points: list[str] = Field(default_factory=list)
    top_level_modules: list[str] = Field(default_factory=list)
    dependency_files: list[str] = Field(default_factory=list)
    dependencies: dict[str, list[str]] = Field(default_factory=dict)
    license: Optional[str] = None
    license_file: Optional[str] = None
    public_api: list[str] = Field(default_factory=list)
    readme_path: Optional[str] = None
    has_tests: bool = False
    has_ci: bool = False
    has_docker: bool = False
    total_files: int = 0
    total_code_files: int = 0
    total_loc: int = 0
    skipped_files: int = 0


def build_manifest(repo_path: str | Path) -> Manifest:
    root = Path(repo_path).resolve()
    if not root.is_dir():
        raise ValueError(f"Not a directory: {root}")

    files, skipped = walk_repo(root)

    languages, primary, total_loc, total_code_files = detect_languages(files, root)
    frameworks, deps, dep_files = detect_dependencies(root)
    entry_points = detect_entry_points(root, files, primary)
    top_level = detect_top_level_modules(root)
    license_name, license_file = detect_license(root)
    public_api = detect_public_api(root, files, primary)

    readme = next(
        (p for p in [root / "README.md", root / "README.rst", root / "README.txt"] if p.exists()),
        None,
    )

    has_tests = any(
        (root / d).exists() for d in ("tests", "test", "spec", "__tests__")
    ) or any(
        f.name.endswith(("_test.py", "_test.go", ".test.ts", ".test.js", ".spec.ts", ".spec.js"))
        for f in files
    )
    has_ci = (root / ".github" / "workflows").is_dir() or (root / ".gitlab-ci.yml").exists()
    has_docker = (root / "Dockerfile").exists() or (root / "docker-compose.yml").exists()

    return Manifest(
        repo_path=str(root),
        repo_name=root.name,
        primary_language=primary,
        languages=languages,
        frameworks=frameworks,
        entry_points=[str(p.relative_to(root)).replace("\\", "/") for p in entry_points],
        top_level_modules=top_level,
        dependency_files=[str(p.relative_to(root)).replace("\\", "/") for p in dep_files],
        dependencies=deps,
        license=license_name,
        license_file=str(license_file.relative_to(root)).replace("\\", "/") if license_file else None,
        public_api=public_api,
        readme_path=str(readme.relative_to(root)).replace("\\", "/") if readme else None,
        has_tests=has_tests,
        has_ci=has_ci,
        has_docker=has_docker,
        total_files=len(files) + skipped,
        total_code_files=total_code_files,
        total_loc=total_loc,
        skipped_files=skipped,
    )
