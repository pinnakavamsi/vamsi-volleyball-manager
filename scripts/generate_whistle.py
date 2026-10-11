"""Reproduce the original synthetic whistle asset using Python's standard library.

This creates an audio cue from mathematical oscillators, not a recording or sample.
The generator and its output are covered by the project's MIT license.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import random
import struct
import wave

ROOT = Path(__file__).resolve().parents[1]
RATE = 44100
DURATION = 0.65


def generate() -> None:
    rng = random.Random(20261011)
    samples = bytearray()
    for index in range(round(RATE * DURATION)):
        t = index / RATE
        envelope = min(1.0, t / 0.025, (DURATION - t) / 0.09)
        vibrato = 3.0 * math.sin(2 * math.pi * 9 * t)
        primary = math.sin(2 * math.pi * 2900 * t + vibrato)
        secondary = math.sin(2 * math.pi * 3150 * t + 0.8 * vibrato)
        signal = 0.50 * primary + 0.23 * secondary + 0.025 * rng.uniform(-1, 1)
        samples.extend(struct.pack('<h', round(32767 * envelope * signal)))
    destination = ROOT / 'volleyball' / 'static' / 'whistle.wav'
    with wave.open(str(destination), 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(RATE)
        output.writeframes(samples)
    provenance = {
        'asset': 'whistle.wav',
        'origin': 'Original mathematical synthesis; no third-party recordings or samples.',
        'generator': 'scripts/generate_whistle.py',
        'license': 'MIT',
        'sample_rate': RATE,
        'duration_seconds': DURATION,
        'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
    }
    (destination.parent / 'whistle-provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
    print('Generated synthetic whistle and provenance.')


if __name__ == '__main__':
    generate()
