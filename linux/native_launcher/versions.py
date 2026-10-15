# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared release-version validation and SemVer precedence (without build metadata)."""
import re


def version_key(text):
    """Return a comparable key, or None for an invalid release version.

    Stable releases sort after prereleases of the same version; numeric
    prerelease identifiers sort numerically and before text identifiers.
    """
    if not isinstance(text, str):
        return None
    number = r"(0|[1-9][0-9]*)"
    match = re.fullmatch(number + r"\." + number + r"\." + number +
                         r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?", text)
    if not match:
        return None
    major, minor, patch, suffix = match.groups()
    identifiers = []
    if suffix is not None:
        for part in suffix.split("."):
            if part.isdecimal():
                if len(part) > 1 and part.startswith("0"):
                    return None
                identifiers.append((0, int(part)))
            else:
                identifiers.append((1, part))
    return (int(major), int(minor), int(patch), suffix is None, tuple(identifiers))
