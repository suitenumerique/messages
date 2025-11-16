#!/usr/bin/env python3
"""Release script for st-messages project.

This script automates the release process by:
- Validating the release version (semver format)
- Optionally calculating the next version automatically
- Updating version files (pyproject.toml, package.json)
- Updating the CHANGELOG
- Creating a release branch and pushing it
"""

from __future__ import annotations

import argparse
import datetime
import re
import subprocess
import sys
from pathlib import Path
from typing import Literal, Optional


def run_command(
    cmd: str, shell: bool = False, capture_output: bool = False
) -> subprocess.CompletedProcess:
    """Run a shell command and return the result."""
    if capture_output:
        result = subprocess.run(
            cmd, shell=shell, capture_output=True, text=True, check=False
        )
        return result
    else:
        subprocess.run(cmd, shell=shell, check=True)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

RELEASE_KINDS = {"p": "patch", "m": "minor", "mj": "major"}
ReleaseKind = Literal["p", "m", "mj"]

SEMVER_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def get_current_version() -> str | None:
    """Extract current version from pyproject.toml."""
    path = Path("src/backend/pyproject.toml")
    if not path.exists():
        return None
    content = path.read_text()
    match = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
    return match.group(1) if match else None


def calculate_next_version(current: str, kind: ReleaseKind) -> str:
    """Calculate next version based on release kind."""
    match = SEMVER_PATTERN.match(current)
    if not match:
        raise ValueError(f"Current version '{current}' is not valid semver")

    major, minor, patch = map(int, match.groups())

    if kind == "mj":
        return f"{major + 1}.0.0"
    elif kind == "m":
        return f"{major}.{minor + 1}.0"
    else:  # patch
        return f"{major}.{minor}.{patch + 1}"


def validate_version(version: str) -> bool:
    """Validate that version follows semver format."""
    return bool(SEMVER_PATTERN.match(version))


def check_git_status() -> tuple[bool, str]:
    """Check if git working directory is clean and on main branch."""
    # Check for uncommitted changes
    result = run_command("git status --porcelain", shell=True, capture_output=True)
    if result.stdout.strip():
        return False, "Working directory has uncommitted changes"

    # Check current branch
    result = run_command("git branch --show-current", shell=True, capture_output=True)
    current_branch = result.stdout.strip()
    if current_branch != "main":
        return False, f"Not on main branch (currently on '{current_branch}')"

    return True, ""


def check_changelog_has_unreleased() -> bool:
    """Check if CHANGELOG has entries in Unreleased section."""
    path = Path("CHANGELOG.md")
    if not path.exists():
        return False

    content = path.read_text()
    # Find content between [Unreleased] and the next ## section
    match = re.search(r"## \[Unreleased\]\s*\n(.*?)(?=\n## \[|$)", content, re.DOTALL)
    if not match:
        return False

    unreleased_content = match.group(1).strip()
    # Check if there's actual content (not just empty lines)
    return bool(unreleased_content)


def update_files(version: str) -> None:
    """Update all files needed with new release version."""
    # pyproject.toml
    sys.stdout.write("Updating pyproject.toml...\n")
    pyproject_path = Path("src/backend/pyproject.toml")
    content = pyproject_path.read_text()
    content = re.sub(
        r'^(version\s*=\s*)"[^"]+"', f'\\1"{version}"', content, flags=re.MULTILINE
    )
    pyproject_path.write_text(content)

    # frontend and e2e package.json files
    sys.stdout.write("Updating package.json files...\n")
    src_path = Path("src")
    for package_json in src_path.rglob("package.json"):
        if "node_modules" in package_json.parts or ".next" in package_json.parts:
            continue
        content = package_json.read_text()
        content = re.sub(r'"version":\s*"[^"]+"', f'"version": "{version}"', content)
        package_json.write_text(content)
        sys.stdout.write(f"  → {package_json}\n")

    # Update package-lock.json files to match
    sys.stdout.write("Updating package-lock.json files...\n")
    for package_json in src_path.rglob("package.json"):
        if "node_modules" in package_json.parts or ".next" in package_json.parts:
            continue
        package_dir = package_json.parent
        lock_file = package_dir / "package-lock.json"
        if lock_file.exists():
            run_command(
                f"cd {package_dir} && npm install --package-lock-only --ignore-scripts",
                shell=True
            )
            sys.stdout.write(f"  → {lock_file}\n")


def update_changelog(version: str) -> None:
    """Update changelog file with release info."""
    sys.stdout.write("Updating CHANGELOG.md...\n")
    path = Path("CHANGELOG.md")
    lines = path.read_text().splitlines(keepends=True)

    today = datetime.date.today()
    new_lines = []

    for i, line in enumerate(lines):
        new_lines.append(line)

        # Add new version header after [Unreleased]
        if "## [Unreleased]" in line:
            new_lines.append(f"\n## [{version}] - {today}\n")

        # Update comparison links at the bottom
        if line.startswith("[unreleased]"):
            last_version_match = re.search(
                r"\[(\d+\.\d+\.\d+)\]", lines[i + 1] if i + 1 < len(lines) else ""
            )
            if last_version_match:
                last_version = last_version_match.group(1)
                new_unreleased_line = line.replace(last_version, version)
                new_release_line = (
                    lines[i + 1].replace(last_version, version)
                    if i + 1 < len(lines)
                    else ""
                )
                new_lines[-1] = new_unreleased_line
                new_lines.append(new_release_line)

    path.write_text("".join(new_lines))


def create_release(version: str, kind: ReleaseKind, dry_run: bool = False) -> None:
    """Create release branch and push."""
    branch_name = f"release/{version}"

    if dry_run:
        sys.stdout.write(f"\n[DRY-RUN] Would create branch: {branch_name}\n")
        sys.stdout.write("[DRY-RUN] Would update version files\n")
        sys.stdout.write("[DRY-RUN] Would update CHANGELOG\n")
        sys.stdout.write(f"[DRY-RUN] Would push to origin/{branch_name}\n")
        return

    sys.stdout.write(f"\nCreating release branch: {branch_name}\n")
    run_command(f"git checkout -b {branch_name}", shell=True)
    run_command("git pull --rebase origin main", shell=True)

    update_changelog(version)
    update_files(version)

    run_command("git add CHANGELOG.md", shell=True)
    run_command("git add src/", shell=True)

    message = f"""🔖({RELEASE_KINDS[kind]}) release version {version}

Update all version files and changelog for {RELEASE_KINDS[kind]} release."""
    run_command(f"git commit -m '{message}'", shell=True)

    confirm = input(
        f"""
\033[0;32m### RELEASE ###
Ready to push branch '{branch_name}' to origin.
Continue? (y/n): \x1b[0m"""
    ).strip().lower()

    if confirm == "y":
        run_command(f"git push origin {branch_name}", shell=True)
        print("\033[1;34m✅ Release branch pushed successfully!\x1b[0m")
        print("\n\033[1;34mPLEASE DO MANUALLY THE FOLLOWING STEPS:\x1b[0m")
    else:
        sys.stdout.write("\n⚠️  Push cancelled. Branch created locally.\n")
        print("\n\033[1;34mPLEASE DO MANUALLY THE FOLLOWING STEPS:\x1b[0m")
        print("\033[1;34m- Push the release branch:\x1b[0m")
        print(f"\033[1;34m>> git push origin {branch_name}\x1b[0m")
    print_next_steps_after_release_branch_pushed(version, branch_name)


def print_next_steps_after_release_branch_pushed(version: str, branch_name: str) -> None:
    """Print next steps after release branch is pushed."""
    sys.stdout.write(
        f"""\033[1;34m- Create PR: https://github.com/suitenumerique/messages/compare/{branch_name}
- After merge, tag the release:
   >> git checkout main
   >> git pull
   >> git tag v{version}
   >> git push origin v{version}
\x1b[0m"""
    )


def main() -> None:
    """Main entry point."""

    parser = argparse.ArgumentParser(description="Create a new release for st-messages")
    parser.add_argument("--version", "-v", help="Release version (semver format)")
    parser.add_argument("--kind", "-k", choices=RELEASE_KINDS.keys(), help="Release kind")
    parser.add_argument(
        "--dry-run", "-n", action="store_true", help="Show what would be done"
    )
    parser.add_argument(
        "--skip-checks", action="store_true", help="Skip git status checks"
    )
    args = parser.parse_args()

    # Check git status
    if not args.skip_checks:
        is_clean, error = check_git_status()
        if not is_clean:
            sys.stderr.write(f"\033[0;31m❌ {error}\033[0m\n")
            sys.stderr.write("Use --skip-checks to bypass this check.\n")
            sys.exit(1)

    # Get current version
    current_version = get_current_version()
    if current_version:
        sys.stdout.write(f"Current version: {current_version}\n")

    # Get release kind
    kind = args.kind
    while kind not in RELEASE_KINDS:
        kind = input("Release kind (p=patch, m=minor, mj=major): ").strip()

    # Get or calculate version
    version = args.version
    if not version and current_version:
        suggested = calculate_next_version(current_version, kind)
        version = input(f"Version [{suggested}]: ").strip() or suggested

    while not version or not validate_version(version):
        if version:
            sys.stdout.write(f"❌ Invalid version format: '{version}' (expected: X.Y.Z)\n")
        version = input("Enter release version (X.Y.Z): ").strip()

    # Check changelog
    if not check_changelog_has_unreleased():
        sys.stdout.write(
            "\033[0;33m⚠️  Warning: No entries found in CHANGELOG [Unreleased] section\033[0m\n"
        )
        if input("Continue anyway? (y/n): ").strip().lower() != "y":
            sys.exit(0)

    # Confirm
    sys.stdout.write(f"\n📦 Preparing {RELEASE_KINDS[kind]} release: v{version}\n")

    create_release(version, kind, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
