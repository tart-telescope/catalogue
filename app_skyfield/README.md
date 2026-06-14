# app-skyfield

GNSS satellite catalogue tools using [Skyfield](https://rhodesmill.org/skyfield/) and [SP3](https://pypi.org/project/sp3/) precise orbit data.

## Scripts

- **`skyfield_catalog.py`** — Downloads TLE data from [CelesTrak](https://celestrak.org) for BeiDou, Galileo, GPS, and QZSS constellations, computes satellite positions, and plots them on a polar sky map.
- **`sp3_catalog.py`** — Downloads SP3 precise orbit files and returns satellite positions in ICRS coordinates using astropy.

## Dependencies

Managed with [uv](https://docs.astral.sh/uv/):

```
uv sync
```

## Usage

```sh
uv run python skyfield_catalog.py
uv run python sp3_catalog.py
```
