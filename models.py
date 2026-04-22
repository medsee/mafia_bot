"""models.py — Data classes, enums, role system."""
from __future__ import annotations
import random
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional


class Role(Enum):
    CIVILIAN    = "civilian"
    MAFIA       = "mafia"
    DOCTOR      = "doctor"
    DETECTIVE   = "detective"
    SNIPER      = "sniper"
    MANIAC      = "maniac"
    MAYOR       = "mayor"
    BODYGUARD   = "bodyguard"
    JESTER      = "jester"
    SERIAL_KILLER = "serial_killer"

    @property
    def emoji(self) -> str:
        return {
            Role.CIVILIAN:     "👤",
            Role.MAFIA:        "🔫",
            Role.DOCTOR:       "💊",
            Role.DETECTIVE:    "🔍",
            Role.SNIPER:       "🎯",
            Role.MANIAC:       "🔪",
            Role.MAYOR:        "👑",
            Role.BODYGUARD:    "🛡️",
            Role.JESTER:       "🤡",
            Role.SERIAL_KILLER:"⚔️",
        }[self]

    @property
    def team(self) -> str:
        if self == Role.MAFIA:         return "mafia"
        if self in (Role.MANIAC, Role.SERIAL_KILLER): return "solo"
        if self == Role.JESTER:        return "jester"
        return "town"

    @property
    def has_night_action(self) -> bool:
        return self in (
            Role.MAFIA, Role.DOCTOR, Role.DETECTIVE,
            Role.SNIPER, Role.MANIAC, Role.BODYGUARD, Role.SERIAL_KILLER
        )

    @property
    def display_name(self) -> str:
        names = {
            Role.CIVILIAN: "Civilian", Role.MAFIA: "Mafia",
            Role.DOCTOR: "Doctor", Role.DETECTIVE: "Detective",
            Role.SNIPER: "Sniper", Role.MANIAC: "Maniac",
            Role.MAYOR: "Mayor", Role.BODYGUARD: "Bodyguard",
            Role.JESTER: "Jester", Role.SERIAL_KILLER: "Serial Killer",
        }
        return names.get(self, self.value.capitalize())

    @property
    def vote_weight(self) -> int:
        return 2 if self == Role.MAYOR else 1

    def description(self, lang: str = "en") -> str:
        from config import TEXTS
        descs = {
            "en": {
                Role.CIVILIAN:     "No special ability. Use your wits to find and vote out the Mafia!",
                Role.MAFIA:        "Kill one player each night. Win when Mafia equals Town.",
                Role.DOCTOR:       "Protect one player from death each night. Can self-heal once.",
                Role.DETECTIVE:    "Investigate one player each night to learn their true role.",
                Role.SNIPER:       "You have ONE unblockable bullet. Use it wisely!",
                Role.MANIAC:       "Kill each night. Win by being the last one standing — alone.",
                Role.MAYOR:        "Your vote counts DOUBLE. You may reveal your identity publicly.",
                Role.BODYGUARD:    "Protect someone each night — you DIE instead of them if attacked.",
                Role.JESTER:       "You win by getting VOTED OUT during the day. Trick the town!",
                Role.SERIAL_KILLER:"Kill one player each night. Win alone. Immune to Mafia.",
            },
            "uz": {
                Role.CIVILIAN:     "Maxsus qobiliyat yo'q. Mafiyani toping va chiqaring!",
                Role.MAFIA:        "Har kecha bir o'yinchini o'ldiring. Mafiya = Shahar bo'lganda g'olib.",
                Role.DOCTOR:       "Har kecha bir o'yinchini himoya qiling. Bir marta o'zini davolashi mumkin.",
                Role.DETECTIVE:    "Har kecha bir o'yinchining rolini tekshiring.",
                Role.SNIPER:       "Bitta to'xtatib bo'lmaydigan o'q. Ehtiyotlik bilan ishlating!",
                Role.MANIAC:       "Har kecha o'ldiring. Yolg'iz qolsangiz g'olib.",
                Role.MAYOR:        "Sizning ovozingiz IKKI HISOB. O'z shaxsiyatingizni oshkor qilishingiz mumkin.",
                Role.BODYGUARD:    "Kimnidir himoya qiling — hujum bo'lsa siz o'lasiz, u emas.",
                Role.JESTER:       "Kunduz ovoz bilan chiqarilsangiz g'olib! Shahanni aldang!",
                Role.SERIAL_KILLER:"Har kecha o'ldiring. Yolg'iz g'olib. Mafiyadan himoyalangan.",
            },
            "ru": {
                Role.CIVILIAN:     "Нет способности. Найдите и исключите Мафию!",
                Role.MAFIA:        "Убивайте ночью. Победа когда Мафия = Мирные.",
                Role.DOCTOR:       "Защищайте игрока каждую ночь. Раз может лечить себя.",
                Role.DETECTIVE:    "Проверяйте роль одного игрока каждую ночь.",
                Role.SNIPER:       "Одна неблокируемая пуля. Используйте мудро!",
                Role.MANIAC:       "Убивайте ночью. Победа в одиночестве.",
                Role.MAYOR:        "Ваш голос считается ДВАЖДЫ. Можно раскрыть себя.",
                Role.BODYGUARD:    "Защищайте кого-то — вы умрёте вместо них.",
                Role.JESTER:       "Победа если вас ИСКЛЮЧАТ голосованием. Обманите город!",
                Role.SERIAL_KILLER:"Убивайте ночью. Одиночная победа. Защищён от Мафии.",
            }
        }
        lang_descs = descs.get(lang, descs["en"])
        return lang_descs.get(self, descs["en"].get(self, ""))


class Phase(Enum):
    LOBBY   = auto()
    NIGHT   = auto()
    DAY     = auto()
    VOTING  = auto()
    DEFENSE = auto()
    ENDED   = auto()


class AIPersonality(Enum):
    AGGRESSIVE  = "aggressive"   # votes loudly, takes risks
    PARANOID    = "paranoid"     # suspects everyone, defensive
    LOGICAL     = "logical"      # uses evidence, consistent
    RANDOM      = "random"       # unpredictable


@dataclass
class Player:
    user_id:     int
    name:        str
    role:        Role = Role.CIVILIAN
    is_alive:    bool = True
    is_ai:       bool = False
    lang:        str  = "uz"

    # Night state
    night_target: Optional[int] = None
    has_acted:    bool = False
    sniper_used:  bool = False
    protected_by: Optional[int] = None  # bodyguard protecting this player

    # Special mechanics
    last_will:    str = ""           # revealed on death
    lover_id:     Optional[int] = None  # linked player
    is_revealed:  bool = False       # Mayor revealed?

    # AI
    ai_personality: AIPersonality = AIPersonality.LOGICAL

    @property
    def mention(self) -> str:
        pfx = "🤖 " if self.is_ai else ""
        rev = "👑 " if self.is_revealed and self.role == Role.MAYOR else ""
        return f"{pfx}{rev}{self.name}"

    def reset_night_state(self) -> None:
        self.night_target = None
        self.has_acted    = False
        self.protected_by = None


@dataclass
class NightResult:
    killed:    list[int] = field(default_factory=list)
    healed:    list[int] = field(default_factory=list)
    inspected: dict[int, Role] = field(default_factory=dict)
    sniped:    list[int] = field(default_factory=list)
    guarded:   list[int] = field(default_factory=list)   # bodyguard targets
    bg_died:   list[tuple[int,int]] = field(default_factory=list)  # (guard, target)
    messages:  list[str] = field(default_factory=list)


@dataclass
class Achievement:
    key:   str
    name:  str
    emoji: str
    desc:  str


ACHIEVEMENTS: dict[str, Achievement] = {
    "first_blood":   Achievement("first_blood",   "First Blood",     "🩸", "Win your first game"),
    "veteran":       Achievement("veteran",        "Veteran",         "🎖️", "Play 10 games"),
    "detective_pro": Achievement("detective_pro",  "Perfect Detective","🔍", "Correctly identify 3 mafia in one game"),
    "survivor":      Achievement("survivor",       "Survivor",        "🛡️", "Survive 5 games"),
    "mafia_boss":    Achievement("mafia_boss",     "Mafia Boss",      "👔", "Win 5 games as Mafia"),
    "streak_3":      Achievement("streak_3",       "On Fire",         "🔥", "Win 3 games in a row"),
    "streak_5":      Achievement("streak_5",       "Unstoppable",     "⚡", "Win 5 games in a row"),
    "jester_win":    Achievement("jester_win",     "Fooled Em All",   "🤡", "Win as Jester"),
    "lone_wolf":     Achievement("lone_wolf",      "Lone Wolf",       "🐺", "Win as Maniac or Serial Killer"),
    "century":       Achievement("century",        "Century",         "💯", "Play 100 games"),
}


# ── Role distribution ──────────────────────────────────────────────

_ROLE_TABLES: dict[int, list[Role]] = {
    4:  [Role.MAFIA, Role.DETECTIVE, Role.CIVILIAN, Role.CIVILIAN],
    5:  [Role.MAFIA, Role.DETECTIVE, Role.CIVILIAN, Role.CIVILIAN, Role.CIVILIAN],
    6:  [Role.MAFIA, Role.MAFIA, Role.DOCTOR, Role.DETECTIVE, Role.CIVILIAN, Role.CIVILIAN],
    7:  [Role.MAFIA, Role.MAFIA, Role.DOCTOR, Role.DETECTIVE, Role.MAYOR, Role.CIVILIAN, Role.CIVILIAN],
    8:  [Role.MAFIA, Role.MAFIA, Role.DOCTOR, Role.DETECTIVE, Role.SNIPER, Role.MAYOR, Role.CIVILIAN, Role.CIVILIAN],
    9:  [Role.MAFIA, Role.MAFIA, Role.DOCTOR, Role.DETECTIVE, Role.SNIPER, Role.BODYGUARD, Role.MAYOR, Role.CIVILIAN, Role.CIVILIAN],
    10: [Role.MAFIA, Role.MAFIA, Role.DOCTOR, Role.DETECTIVE, Role.SNIPER, Role.BODYGUARD, Role.MAYOR, Role.MANIAC, Role.CIVILIAN, Role.CIVILIAN],
    11: [Role.MAFIA, Role.MAFIA, Role.MAFIA, Role.DOCTOR, Role.DETECTIVE, Role.SNIPER, Role.BODYGUARD, Role.MAYOR, Role.MANIAC, Role.CIVILIAN, Role.CIVILIAN],
    12: [Role.MAFIA, Role.MAFIA, Role.MAFIA, Role.DOCTOR, Role.DETECTIVE, Role.SNIPER, Role.BODYGUARD, Role.MAYOR, Role.MANIAC, Role.JESTER, Role.CIVILIAN, Role.CIVILIAN],
}

def assign_roles(players: list[Player]) -> None:
    n = len(players)
    if n < 4:
        raise ValueError("Need at least 4 players.")
    if n in _ROLE_TABLES:
        roles = list(_ROLE_TABLES[n])
    else:
        mafia_count = max(2, n // 4)
        roles = ([Role.MAFIA] * mafia_count +
                 [Role.DOCTOR, Role.DETECTIVE, Role.SNIPER, Role.BODYGUARD, Role.MAYOR, Role.MANIAC, Role.JESTER])
        roles += [Role.CIVILIAN] * (n - len(roles))
    random.shuffle(roles)
    personalities = list(AIPersonality)
    for i, (player, role) in enumerate(zip(players, roles)):
        player.role = role
        if player.is_ai:
            player.ai_personality = personalities[i % len(personalities)]
