#!/usr/bin/env python3
"""
Release script for cmping.

This script:
1. Parses the CHANGELOG.md to get the latest version
2. Validates it's a proper version jump from the current git tag
3. Creates a git tag for the version
4. Builds and uploads to PyPI
5. On success, adds a "dev" changelog entry and commits it
"""

import re
import subprocess
import sys
from pathlib import Path


def run(cmd, check=True, capture=True):
    """Run a shell command and return output."""
    print(f"$ {cmd}")
    result = subprocess.run(
        cmd, shell=True, check=check, capture_output=capture, text=True
    )
    if capture and result.stdout:
        print(result.stdout.strip())
    return result


def get_changelog_version():
    """Parse CHANGELOG.md to get the latest version."""
    changelog = Path("CHANGELOG.md").read_text()
    # Match version patterns like "## 0.16.0" or "## 1.0.0"
    match = re.search(r"^## (\d+\.\d+\.\d+)", changelog, re.MULTILINE)
    if not match:
        print("ERROR: Could not find version in CHANGELOG.md")
        print("Expected format: '## X.Y.Z' at the start of a line")
        sys.exit(1)
    return match.group(1)


def get_latest_git_tag():
    """Get the latest git tag version."""
    result = run("git tag --sort=-v:refname", check=False)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    # Get the first tag (latest by version)
    tags = result.stdout.strip().split("\n")
    for tag in tags:
        # Match version tags like "0.15.0" or "v0.15.0"
        if re.match(r"^v?\d+\.\d+\.\d+$", tag):
            return tag.lstrip("v")
    return None


def parse_version(version_str):
    """Parse version string into tuple of ints."""
    return tuple(int(x) for x in version_str.split("."))


def validate_version_jump(new_version, old_version):
    """Validate that new_version is a proper increment from old_version."""
    if old_version is None:
        print(f"No previous version found, {new_version} will be the first release")
        return True

    new = parse_version(new_version)
    old = parse_version(old_version)

    # Check for proper version jump (major, minor, or patch increment)
    if new <= old:
        print(f"ERROR: New version {new_version} is not greater than {old_version}")
        return False

    # Check that it's a single increment (not skipping versions)
    major_diff = new[0] - old[0]
    minor_diff = new[1] - old[1]
    patch_diff = new[2] - old[2]

    valid = False
    if major_diff == 1 and new[1] == 0 and new[2] == 0:
        # Major version bump: X.0.0
        valid = True
    elif major_diff == 0 and minor_diff == 1 and new[2] == 0:
        # Minor version bump: X.Y.0
        valid = True
    elif major_diff == 0 and minor_diff == 0 and patch_diff == 1:
        # Patch version bump: X.Y.Z
        valid = True

    if not valid:
        print(f"ERROR: Invalid version jump from {old_version} to {new_version}")
        print("Expected one of:")
        print(f"  - Major: {old[0]+1}.0.0")
        print(f"  - Minor: {old[0]}.{old[1]+1}.0")
        print(f"  - Patch: {old[0]}.{old[1]}.{old[2]+1}")
        return False

    return True


def create_git_tag(version):
    """Create and push a git tag for the version."""
    tag = version
    print(f"\nCreating git tag: {tag}")

    # Check if tag already exists
    result = run(f"git tag -l {tag}", check=False)
    if result.stdout.strip() == tag:
        print(f"ERROR: Tag {tag} already exists")
        sys.exit(1)

    # Create the tag
    run(f"git tag {tag}")

    # Push the tag
    print(f"Pushing tag {tag} to origin...")
    run(f"git push origin {tag}")

    return tag


def build_and_upload():
    """Build the package and upload to PyPI."""
    print("\nCleaning previous builds...")
    run("rm -rf dist build *.egg-info")

    print("\nBuilding package...")
    run("python -m build")

    print("\nUploading to PyPI...")
    # Upload all distribution files (wheel and sdist)
    run("twine upload dist/*")


def add_dev_changelog_entry(released_version):
    """Add a dev changelog entry after successful release."""
    changelog_path = Path("CHANGELOG.md")
    changelog = changelog_path.read_text()

    # Parse the version to create the dev version
    parts = parse_version(released_version)
    # Bump patch version for dev
    dev_version = f"{parts[0]}.{parts[1]}.{parts[2] + 1}.dev0"

    # Insert new dev entry after the header
    new_entry = f"""
# cmping changelog 

## {dev_version}

(in development)

## {released_version}"""

    # Replace the header and current version
    changelog = re.sub(
        r"^\s*# cmping changelog\s*\n\s*## " + re.escape(released_version),
        new_entry,
        changelog,
        flags=re.MULTILINE,
    )

    changelog_path.write_text(changelog)
    print(f"\nAdded dev changelog entry: {dev_version}")

    # Commit the change
    run("git add CHANGELOG.md")
    run(f'git commit -m "post release: start {dev_version}"')
    run("git push")


def main():
    """Main release workflow."""
    print("=" * 60)
    print("cmping Release Script")
    print("=" * 60)

    # Step 1: Get version from changelog
    print("\n[1/5] Reading version from CHANGELOG.md...")
    new_version = get_changelog_version()
    print(f"Found version: {new_version}")

    # Step 2: Get current git tag
    print("\n[2/5] Checking current git tags...")
    old_version = get_latest_git_tag()
    if old_version:
        print(f"Latest git tag: {old_version}")
    else:
        print("No previous version tags found")

    # Step 3: Validate version jump
    print("\n[3/5] Validating version jump...")
    if not validate_version_jump(new_version, old_version):
        sys.exit(1)
    print(f"Version jump {old_version or 'none'} -> {new_version} is valid")

    # Step 4: Create git tag and build/upload
    print("\n[4/5] Creating tag and uploading to PyPI...")
    tag = create_git_tag(new_version)
    build_and_upload()

    # Step 5: Add dev changelog entry
    print("\n[5/5] Adding dev changelog entry...")
    add_dev_changelog_entry(new_version)

    # Final summary
    print("\n" + "=" * 60)
    print("Release completed successfully!")
    print("=" * 60)
    print(f"\nUploaded to PyPI: cmping {tag}")
    print(f"Tag: {tag}")
    print("\nNext steps:")
    print("  - Verify the release on PyPI: https://pypi.org/project/cmping/")
    print(f"  - Check the tag on GitHub: https://github.com/chatmail/cmping/releases/tag/{tag}")


if __name__ == "__main__":
    main()
