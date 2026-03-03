from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple
import random

DIRS = {
    "up": (0, -1),
    "right": (1, 0),
    "down": (0, 1),
    "left": (-1, 0),
}
DIR_ORDER = ["up", "right", "down", "left"]

def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v

def manhattan(a: Tuple[int,int], b: Tuple[int,int]) -> int:
    return abs(a[0]-b[0]) + abs(a[1]-b[1])

class DeterministicRNG:
    def __init__(self, seed: int):
        self.seed = seed
        self.rng = random.Random(seed)

    def randint(self, a: int, b: int) -> int:
        return self.rng.randint(a, b)

    def choice(self, seq):
        return self.rng.choice(seq)

    def shuffle(self, seq):
        self.rng.shuffle(seq)

    def random(self) -> float:
        return self.rng.random()
