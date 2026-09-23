"""Animazioni del Launchpad: effetti brevi disegnati sopra la griglia di base.

Il Launchpad originale ha LED rossi e verdi con 4 livelli (0..3) ciascuno: ogni colore qui è
una coppia (rosso, verde). Un "tono" è la direzione del colore, es. (0, 1) = verde,
(1, 1) = ambra, e un livello 0..3 lo accende più o meno.

Ogni effetto sa dire, per un istante `now` (secondi, orologio monotono), quali tasti accende
e se ha finito. Board li sovrappone alla griglia con un "max" per canale: un'onda rossa che passa
su un pad verde lo fa diventare ambra per un attimo.
"""

from __future__ import annotations

import math
import random

from .launchpad import Key

RG = tuple[int, int]

GREEN: RG = (0, 1)
RED: RG = (1, 0)
AMBER: RG = (1, 1)

# Tutti i tasti "disegnabili" con coordinate (riga, colonna) per le distanze:
# griglia 8x8, colonna scene (col 8) e riga dei tondi in alto (riga -1)
GRID_KEYS: list[tuple[Key, float, float]] = (
    [(("grid", r, c), r, c) for r in range(8) for c in range(9)]
    + [(("top", 0, c), -1, c) for c in range(8)]
)


def tone(hue: RG, level: int) -> RG:
    level = max(0, min(3, level))
    return hue[0] * level, hue[1] * level


def hue_of(rg: RG) -> RG:
    """Tono di un colore: (3, 0) -> rosso, (1, 1) -> ambra, (2, 3) -> ambra."""
    return int(rg[0] > 0), int(rg[1] > 0)


def blend(a: RG | None, b: RG) -> RG:
    return b if a is None else (max(a[0], b[0]), max(a[1], b[1]))


def breathe(now: float, hue: RG, period: float = 2.4, low: int = 1, high: int = 3) -> RG:
    """Respiro lento: il livello sale e scende tra low e high con una curva morbida."""
    x = 0.5 - 0.5 * math.cos(2 * math.pi * (now % period) / period)
    return tone(hue, low + round((high - low) * x))


def slot_pos(slot: int) -> tuple[int, int]:
    return slot // 8, slot % 8


class Effect:
    def __init__(self, start: float, duration: float) -> None:
        self.start = start
        self.duration = duration
        # Etichetta facoltativa per ritirare un gruppo di effetti (es. le onde di un permesso)
        self.tag: object = None

    def finished(self, now: float) -> bool:
        return now - self.start >= self.duration

    def progress(self, now: float) -> float:
        return max(0.0, min(1.0, (now - self.start) / self.duration))

    def render(self, now: float) -> dict[Key, RG]:
        raise NotImplementedError


class Ripple(Effect):
    """Anello che si allarga dal pad `slot` e sfuma. radius grande = attraversa tutta la griglia."""

    def __init__(self, start: float, slot: int, hue: RG, radius: float, duration: float,
                 width: float = 1.0) -> None:
        super().__init__(start, duration)
        self.origin = slot_pos(slot)
        self.hue, self.radius, self.width = hue, radius, width

    def render(self, now: float) -> dict[Key, RG]:
        if now < self.start:  # onda programmata per dopo (ripetizioni)
            return {}
        p = self.progress(now)
        ring = p * self.radius
        fade = 1 - p * p  # sfuma tardi: un'onda lunga arriva ancora visibile ai bordi
        out: dict[Key, RG] = {}
        orow, ocol = self.origin
        for key, r, c in GRID_KEYS:
            d = math.hypot(r - orow, c - ocol)
            level = math.ceil(3 * fade * max(0.0, 1 - abs(d - ring) / self.width) - 0.15)
            if level > 0:
                out[key] = tone(self.hue, level)
        return out


class Spark(Effect):
    """Lampo sul pad: pieno per un istante, poi scende (il colore di base riemerge sotto)."""

    def __init__(self, start: float, slot: int, hue: RG = AMBER, duration: float = 0.45) -> None:
        super().__init__(start, duration)
        self.key: Key = ("grid", *slot_pos(slot))
        self.hue = hue

    def render(self, now: float) -> dict[Key, RG]:
        level = round(3 * (1 - self.progress(now)) ** 0.7)
        return {self.key: tone(self.hue, level)} if level > 0 else {}


class FadeOut(Effect):
    """Il pad di una sessione tolta si spegne gradualmente invece di sparire di colpo."""

    def __init__(self, start: float, slot: int, rg: RG, duration: float = 0.5) -> None:
        super().__init__(start, duration)
        self.key: Key = ("grid", *slot_pos(slot))
        self.rg = rg

    def render(self, now: float) -> dict[Key, RG]:
        k = 1 - self.progress(now)
        rg = (round(self.rg[0] * k), round(self.rg[1] * k))
        return {self.key: rg} if rg != (0, 0) else {}


class Boot(Effect):
    """Benvenuto: una fascia rosso -> ambra -> verde attraversa il Launchpad in diagonale."""

    BANDS: list[RG] = [(3, 0), (3, 3), (0, 3), (0, 1)]

    def __init__(self, start: float, duration: float = 1.5) -> None:
        super().__init__(start, duration)

    def render(self, now: float) -> dict[Key, RG]:
        # la diagonale va da -1 (angolo in alto a sinistra) a 15 (in basso a destra)
        front = self.progress(now) * (16 + len(self.BANDS) + 1) - 1
        out: dict[Key, RG] = {}
        for key, r, c in GRID_KEYS:
            band = math.floor(front - (r + c))
            if 0 <= band < len(self.BANDS):
                out[key] = self.BANDS[band]
        return out


class Rain(Effect):
    """Screensaver: gocce verdi tenui che scendono lente lungo le colonne della griglia."""

    def __init__(self, start: float, seed: int | None = None, step: float = 0.28, spawn: float = 0.3) -> None:
        super().__init__(start, math.inf)
        self.rng = random.Random(seed)
        self.step_seconds, self.spawn = step, spawn
        self.steps = 0
        self.drops: list[list[int]] = []  # [colonna, riga della testa]

    def _advance(self, now: float) -> None:
        target = int((now - self.start) / self.step_seconds)
        while self.steps < target:
            self.steps += 1
            for drop in self.drops:
                drop[1] += 1
            self.drops = [d for d in self.drops if d[1] < 9]  # la coda esce dal fondo
            if self.rng.random() < self.spawn:
                busy = {d[0] for d in self.drops if d[1] < 2}
                free = [c for c in range(8) if c not in busy]
                if free:
                    self.drops.append([self.rng.choice(free), 0])

    def render(self, now: float) -> dict[Key, RG]:
        self._advance(now)
        out: dict[Key, RG] = {}
        for col, head in self.drops:
            for row, level in ((head, 2), (head - 1, 1)):
                if 0 <= row < 8:
                    out[("grid", row, col)] = blend(out.get(("grid", row, col)), tone(GREEN, level))
        return out
