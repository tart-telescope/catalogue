# Optional GNSS "code" identifiers for satellites (issue #4).
#
# A satellite code is the short constellation identifier used by receivers,
# e.g. "E11" (Galileo), "C14" (BeiDou), "PRN 42" (GPS). Codes are optional:
# where no guaranteed match exists, no code is returned.
#
# Sources:
#   Galileo: https://www.gsc-europa.eu/system-service-status/constellation-information
#   QZSS:    https://sys.qzss.go.jp/dod/en/constellation.html
#   GPS:     the PRN is part of the CelesTrak object name, e.g.
#            "GPS BIIR-2  (PRN 13)".
#   BeiDou:  the C-code is usually part of the name (e.g. "C14").
#
# (c) 2025 Tim Molteno (tim@elec.ac.nz) GPL v3

import re
from typing import Optional

# Galileo satellite name (GSATxxxx) -> SV ID (ranging code).
# Snapshot of the GSC constellation information table, 2026-09-27.
GALILEO_CODES = {
    "GSAT0101": "E11",
    "GSAT0102": "E12",
    "GSAT0103": "E19",
    "GSAT0201": "E18",
    "GSAT0202": "E14",
    "GSAT0203": "E26",
    "GSAT0204": "E22",
    "GSAT0206": "E30",
    "GSAT0207": "E07",
    "GSAT0208": "E08",
    "GSAT0209": "E09",
    "GSAT0210": "E01",
    "GSAT0211": "E02",
    "GSAT0212": "E03",
    "GSAT0213": "E04",
    "GSAT0214": "E05",
    "GSAT0215": "E21",
    "GSAT0216": "E25",
    "GSAT0217": "E27",
    "GSAT0218": "E31",
    "GSAT0219": "E36",
    "GSAT0220": "E13",
    "GSAT0221": "E15",
    "GSAT0222": "E33",
    "GSAT0223": "E34",
    "GSAT0224": "E10",
    "GSAT0225": "E29",
    "GSAT0226": "E23",
    "GSAT0227": "E06",
    "GSAT0232": "E16",
    "GSAT0233": "E28",
    "GSAT0234": "E32",
}

# QZSS satellite name -> PRN (positioning signal PRN), keyed by the
# canonical name (QZS<n>[R]). Handles "QZS-2", "QZS02" and "QZS2" alike.
# Snapshot of the QZSS constellation information table, 2026-09-22.
QZSS_CODES = {
    "QZS2": "PRN 194",
    "QZS3": "PRN 199",
    "QZS4": "PRN 195",
    "QZS1R": "PRN 196",
    "QZS6": "PRN 200",
    "QZS7": "PRN 201",
}

# e.g. "GPS BIIR-2  (PRN 13)" -> "PRN 13"
PRN_RE = re.compile(r"\(?\bPRN\s*(\d{1,3})\b\)?", re.IGNORECASE)

# e.g. "C14" (BeiDou), "E11" (Galileo), "J01" (QZSS), "G32" (GPS/SP3 names)
CODE_RE = re.compile(r"\b([CEJG])\s*(\d{2})\b")

# e.g. "GSAT0213", "QZS-1R"
NAME_RE = re.compile(r"[^A-Z0-9]")


def _normalize(name: str) -> str:
    """Uppercase and strip separators: 'QZS-1R' -> 'QZS1R'."""
    return NAME_RE.sub("", name.upper())


def satellite_code(name: str) -> Optional[str]:
    """Return the optional GNSS code for a satellite name, or None.

    Codes are only returned where a guaranteed match exists:
    an explicit code in the name, the official Galileo SV ID table, or
    the official QZSS PRN table.
    """
    if not name:
        return None

    # Codes embedded in the name always win ("BEIDOU-3 M15 (C14)", "C14", ...)
    m = CODE_RE.search(name)
    if m:
        return f"{m.group(1).upper()}{m.group(2)}"

    # GPS-style names carry the PRN in parentheses ("GPS BIIR-2  (PRN 13)")
    m = PRN_RE.search(name)
    if m:
        return f"PRN {int(m.group(1))}"

    norm = _normalize(name)

    code = GALILEO_CODES.get(norm)
    if code is not None:
        return code

    # QZSS names appear as "QZS-2", "QZS02", "QZS-1R", ...
    m = re.match(r"QZS+0*(\d+)(R?)$", norm)
    if m:
        return QZSS_CODES.get(f"QZS{int(m.group(1))}{m.group(2)}")

    return None
