"""
models.py — Pure data classes and enums. No I/O, no Telegram, no side effects.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional


# ──────────────────────────────────────────────
# Enumerations
# ──────────────────────────────────────────────

class Role(Enum):
    CIVILIAN   = "civilian"
    MAFIA      = "mafia"
    DOCTOR     = "doctor"
    DETECTIVE  = "detective"
    SNIPER     = "sniper"
    MANIAC     = "maniac"

    @property
    def emoji(self) -> str:
        return {
            Role.CIVILIAN:  "👤",
            Role.MAFIA:     "🔫",
            Role.DOCTOR:    "💊",
            Role.DETECTIVE: "🔍",
            Role.SNIPER:    "🎯",
            Role.MANIAC:    "🔪",
        }[self]

    @property
    def team(self) -> str:
        """Returns logical team for win-condition checks."""
        if self == Role.MAFIA:
            return "mafia"
        if self == Role.MANIAC:
            return "maniac"
        return "town"

    @property
    def has_night_action(self) -> bool:
        return self in (Role.MAFIA, Role.DOCTOR, Role.DETECTIVE, Role.SNIPER, Role.MANIAC)

    @property
    def display_name(self) -> str:
        return self.value.capitalize()


class Phase(Enum):
    LOBBY    = auto()
    NIGHT    = auto()
    DAY      = auto()
    VOTING   = auto()
    ENDED    = auto()


class NightAction(Enum):
    KILL      = "kill"       # mafia / maniac
    HEAL      = "heal"       # doctor
    INSPECT   = "inspect"    # detective
    SNIPE     = "snipe"      # sniper (one-shot eliminate)


# ──────────────────────────────────────────────
# Player
# ──────────────────────────────────────────────

@dataclass
class Player:
    user_id: int
    name: str
    role: Role = Role.CIVILIAN
    is_alive: bool = True
    is_ai: bool = False

    # Night-action tracking (reset each night)
    night_target: Optional[int] = None        # user_id of chosen target
    has_acted: bool = False

    # Persistent ability flags
    sniper_used: bool = False                 # sniper gets exactly one shot

    # For doctor: track last-healed to prevent consecutive same-target
    last_healed: Optional[int] = None

    @property
    def mention(self) -> str:
        return f"{'🤖 ' if self.is_ai else ''}{self.name}"

    def reset_night_state(self) -> None:
        self.night_target = None
        self.has_acted = False


# ──────────────────────────────────────────────
# Role distribution helper
# ──────────────────────────────────────────────

_ROLE_TABLES: dict[int, list[Role]] = {
    4:  [Role.MAFIA, Role.DETECTIVE, Role.CIVILIAN, Role.CIVILIAN],
    5:  [Role.MAFIA, Role.DETECTIVE, Role.CIVILIAN, Role.CIVILIAN, Role.CIVILIAN],
    6:  [Role.MAFIA, Role.MAFIA, Role.DOCTOR, Role.DETECTIVE, Role.CIVILIAN, Role.CIVILIAN],
    7:  [Role.MAFIA, Role.MAFIA, Role.DOCTOR, Role.DETECTIVE, Role.CIVILIAN, Role.CIVILIAN, Role.CIVILIAN],
    8:  [Role.MAFIA, Role.MAFIA, Role.DOCTOR, Role.DETECTIVE, Role.SNIPER, Role.CIVILIAN, Role.CIVILIAN, Role.CIVILIAN],
    9:  [Role.MAFIA, Role.MAFIA, Role.DOCTOR, Role.DETECTIVE, Role.SNIPER, Role.MANIAC, Role.CIVILIAN, Role.CIVILIAN, Role.CIVILIAN],
    10: [Role.MAFIA, Role.MAFIA, Role.MAFIA, Role.DOCTOR, Role.DETECTIVE, Role.SNIPER, Role.MANIAC, Role.CIVILIAN, Role.CIVILIAN, Role.CIVILIAN],
}

def assign_roles(players: list[Player]) -> None:
    """Assign roles in-place based on player count. Scales for > 10 players."""
    n = len(players)
    if n < 4:
        raise ValueError("Need at least 4 players.")

    if n <= 10:
        roles = list(_ROLE_TABLES[n])
    else:
        # Scale: 1 mafia per 3.5 players, plus doctor & detective always present
        mafia_count = max(2, n // 4)
        roles = [Role.MAFIA] * mafia_count + [Role.DOCTOR, Role.DETECTIVE, Role.SNIPER, Role.MANIAC]
        roles += [Role.CIVILIAN] * (n - len(roles))

    random.shuffle(roles)
    for player, role in zip(players, roles):
        player.role = role


# ──────────────────────────────────────────────
# Night result container
# ──────────────────────────────────────────────

@dataclass
class NightResult:
    killed: list[int] = field(default_factory=list)      # user_ids eliminated
    healed: list[int] = field(default_factory=list)       # user_ids saved
    inspected: dict[int, Role] = field(default_factory=dict)  # detective results
    sniped: list[int] = field(default_factory=list)       # sniper kills
    messages: list[str] = field(default_factory=list)     # public announcement lines
