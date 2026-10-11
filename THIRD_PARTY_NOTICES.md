# Third-party notices

Volleyball Manager application code is licensed under the [MIT License](LICENSE).

The bundled, unmodified **Noto Sans Regular and Bold** font files are distributed under the **SIL Open Font License 1.1**, separately from the application license. Retain [OFL.txt](volleyball/fonts/OFL.txt) with redistributed fonts. [Font provenance](volleyball/fonts/provenance.json) records official upstream locations and checksums. The Python package includes these files and their license so installed PDF exports can use the same fonts as source checkouts.

Python dependencies retain their own licenses; installing or distributing them does not relicense them under MIT. Their pinned versions are listed in [requirements.txt](requirements.txt). External weather/geocoding APIs and their data have separate usage and attribution terms, documented in [the provider guide](docs/weather-adapter.md).

The whistle audio is synthesized locally from deterministic source under the application MIT license; it is not a third-party recording. [Audio provenance](volleyball/static/whistle-provenance.json) records its generator and checksum.
