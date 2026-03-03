from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

Terrain = str  # normal / water / metal / fragile

@dataclass
class Cell:
    height: int = 0  # -1/0/1
    temperature: float = 0.0  # -100..100
    charge: int = 0  # -5..5
    terrain: Terrain = "normal"
    unit_id: Optional[str] = None
    # cell-based statuses
    capacitor_value: int = 0
    capacitor_ttl: int = 0
    trap_kind: Optional[str] = None
    trap_ttl: int = 0
    trap_charge_add: int = 0
    gravity_well_ttl: int = 0

@dataclass
class Unit:
    uid: str
    team: str  # "player" or "enemy" or "boss"
    x: int
    y: int
    hp: int
    move: int
    conductivity: float = 1.0
    weight: str = "medium"
    tags: List[str] = field(default_factory=list)
    # statuses
    burning: int = 0  # ttl in turns
    frozen: int = 0   # ttl in turns
    # extras (enemy aquatic)
    water_regen: int = 0
    shock_taken_mult: float = 1.0

@dataclass
class TurnModifiers:
    # base (can be modified permanently by relics)
    base_temp_diffusion_factor: float = 0.5
    base_temp_decay_rate: float = 0.5
    base_discharge_damage_mult: float = 1.0
    base_discharge_range_bonus: int = 0
    base_wall_damage_bonus: int = 0
    # turn (reset each turn)
    temp_diffusion_factor: float = 0.5
    temp_decay_rate: float = 0.5
    discharge_damage_mult: float = 1.0
    discharge_range_bonus: int = 0
    burning_bonus_damage: int = 0
    temp_threshold_multiplier: int = 1
    displacement_steps_bonus: int = 0
    wall_damage_bonus: int = 0
    displacement_extra_damage: int = 0
    gravity_map: str = "identity"  # identity / flip_y
    global_charge_pulse: int = 0

    def reset_for_turn(self):
        self.temp_diffusion_factor = self.base_temp_diffusion_factor
        self.temp_decay_rate = self.base_temp_decay_rate
        self.discharge_damage_mult = self.base_discharge_damage_mult
        self.discharge_range_bonus = self.base_discharge_range_bonus
        self.burning_bonus_damage = 0
        self.temp_threshold_multiplier = 1
        self.displacement_steps_bonus = 0
        self.wall_damage_bonus = self.base_wall_damage_bonus
        self.displacement_extra_damage = 0
        self.gravity_map = "identity"
        self.global_charge_pulse = 0

@dataclass
class GameEvent:
    kind: str
    data: Dict[str, Any]

@dataclass
class GameEventLog:
    seed: int
    events: List[GameEvent] = field(default_factory=list)
    cursor: int = -1  # replay cursor (index into events)

    def push(self, kind: str, **data):
        self.events.append(GameEvent(kind=kind, data=data))

    def reset_cursor_end(self):
        self.cursor = len(self.events) - 1
