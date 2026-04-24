"""
ULTRA MAFIA BOT
===============
Rollar: Fuqaro, Mafia, Don, Komissar, Shifokor, Guvoh, Kamikaze, Suicid
python-telegram-bot==21.6 | Python 3.10+
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Optional, Callable, Awaitable

import aiosqlite
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
)
from telegram.constants import ParseMode
from telegram.error import Forbidden, BadRequest, TelegramError
from telegram.ext import (
    Application, ApplicationBuilder, CallbackQueryHandler,
    CommandHandler, ContextTypes, MessageHandler, filters,
)

# ══════════════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════════════
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

# ══════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════
DB_PATH          = os.environ.get("DB_PATH", "mafia.db")
LOBBY_TIMEOUT    = 60     # seconds to join
NIGHT_TIMEOUT    = 50
DAY_TIMEOUT      = 60
VOTE_TIMEOUT     = 45
DEFENSE_TIMEOUT  = 25
MIN_PLAYERS      = 4
MAX_PLAYERS      = 15
MAX_EXTEND       = 2      # max lobby extensions
EXTEND_SECS      = 30

AI_NAMES = [
    "🤖 Alice", "🤖 Bob", "🤖 Carlos", "🤖 Diana",
    "🤖 Erik", "🤖 Fatima", "🤖 George", "🤖 Hana",
]

# ══════════════════════════════════════════════════════════════════
# ENUMS & MODELS
# ══════════════════════════════════════════════════════════════════
class Role(Enum):
    FUQARO   = "fuqaro"
    MAFIA    = "mafia"
    DON      = "don"
    KOMISSAR = "komissar"
    SHIFOKOR = "shifokor"
    GUVOH    = "guvoh"
    KAMIKAZE = "kamikaze"
    SUICID   = "suicid"

    @property
    def emoji(self) -> str:
        return {
            Role.FUQARO:   "👨‍🌾",
            Role.MAFIA:    "🔫",
            Role.DON:      "🧠",
            Role.KOMISSAR: "👮",
            Role.SHIFOKOR: "💉",
            Role.GUVOH:    "👁",
            Role.KAMIKAZE: "💣",
            Role.SUICID:   "☠️",
        }[self]

    @property
    def uz_name(self) -> str:
        return {
            Role.FUQARO:   "Fuqaro",
            Role.MAFIA:    "Mafia",
            Role.DON:      "Don",
            Role.KOMISSAR: "Komissar",
            Role.SHIFOKOR: "Shifokor",
            Role.GUVOH:    "Guvoh",
            Role.KAMIKAZE: "Kamikaze",
            Role.SUICID:   "Suicid",
        }[self]

    @property
    def is_mafia_team(self) -> bool:
        return self in (Role.MAFIA, Role.DON)

    @property
    def has_night_action(self) -> bool:
        return self in (
            Role.MAFIA, Role.DON, Role.KOMISSAR,
            Role.SHIFOKOR, Role.GUVOH
        )

    @property
    def description(self) -> str:
        return {
            Role.FUQARO:   "Maxsus qobiliyat yo'q. Mafiyani toping va ovoz bering!",
            Role.MAFIA:    "Har kecha Don bilan birga bitta o'yinchini o'ldiring.",
            Role.DON:      "Mafiya rahbari. Har kecha o'ldirish nishonini tanlaysiz. Komissar tekshiruviga immunsiz.",
            Role.KOMISSAR: "Har kecha bitta o'yinchini tekshirasiz. Mafiya yoki Don ekanini bilib olasiz.",
            Role.SHIFOKOR: "Har kecha bitta o'yinchini davolaysiz. O'ldirishdan himoya qilasiz.",
            Role.GUVOH:    "Har kecha bitta o'yinchini kuzatasiz. Uning kechagi harakatini bilasiz.",
            Role.KAMIKAZE: "Ovoz bilan osilganda — o'zi bilan birga tasodifiy bitta o'yinchini o'ldiradi!",
            Role.SUICID:   "Ovoz bilan osilganda — o'sha zahoti O'ZI yutadi!",
        }[self]


class Phase(Enum):
    LOBBY   = auto()
    NIGHT   = auto()
    DAY     = auto()
    VOTING  = auto()
    DEFENSE = auto()
    ENDED   = auto()


@dataclass
class Player:
    user_id:      int
    name:         str
    role:         Role = Role.FUQARO
    is_alive:     bool = True
    is_ai:        bool = False
    night_target: Optional[int] = None
    has_acted:    bool = False

    def reset_night(self):
        self.night_target = None
        self.has_acted = False

    @property
    def tag(self) -> str:
        dead = "💀 " if not self.is_alive else ""
        return f"{dead}{self.name}"


@dataclass
class NightResult:
    killed:   list[int] = field(default_factory=list)
    healed:   list[int] = field(default_factory=list)
    checked:  dict[int, bool] = field(default_factory=dict)   # uid -> is_mafia
    witnessed: dict[int, str] = field(default_factory=dict)   # guvoh_uid -> info_text
    messages: list[str] = field(default_factory=list)


# ══════════════════════════════════════════════════════════════════
# ROLE DISTRIBUTION
# ══════════════════════════════════════════════════════════════════
def build_role_list(n: int) -> list[Role]:
    """Build balanced role list for n players. Don never goes to AI."""
    if n < 4:
        raise ValueError("Kamida 4 o'yinchi kerak")

    if n == 4:
        return [Role.DON, Role.KOMISSAR, Role.FUQARO, Role.FUQARO]
    elif n == 5:
        return [Role.DON, Role.MAFIA, Role.KOMISSAR, Role.FUQARO, Role.FUQARO]
    elif n == 6:
        return [Role.DON, Role.MAFIA, Role.KOMISSAR, Role.SHIFOKOR, Role.FUQARO, Role.FUQARO]
    elif n == 7:
        return [Role.DON, Role.MAFIA, Role.KOMISSAR, Role.SHIFOKOR, Role.GUVOH, Role.FUQARO, Role.FUQARO]
    elif n == 8:
        return [Role.DON, Role.MAFIA, Role.KOMISSAR, Role.SHIFOKOR, Role.GUVOH, Role.KAMIKAZE, Role.FUQARO, Role.FUQARO]
    elif n == 9:
        return [Role.DON, Role.MAFIA, Role.MAFIA, Role.KOMISSAR, Role.SHIFOKOR, Role.GUVOH, Role.KAMIKAZE, Role.FUQARO, Role.FUQARO]
    elif n == 10:
        return [Role.DON, Role.MAFIA, Role.MAFIA, Role.KOMISSAR, Role.SHIFOKOR, Role.GUVOH, Role.KAMIKAZE, Role.SUICID, Role.FUQARO, Role.FUQARO]
    else:
        mafia_n = max(2, n // 4)
        roles = [Role.DON] + [Role.MAFIA] * mafia_n
        roles += [Role.KOMISSAR, Role.SHIFOKOR, Role.GUVOH, Role.KAMIKAZE, Role.SUICID]
        roles += [Role.FUQARO] * (n - len(roles))
        return roles


def assign_roles(players: list[Player]) -> None:
    """Assign roles. Don NEVER goes to AI player."""
    n = len(players)
    roles = build_role_list(n)
    random.shuffle(roles)

    humans = [p for p in players if not p.is_ai]
    ais    = [p for p in players if p.is_ai]

    # Ensure Don goes to human
    if Role.DON in roles:
        don_idx = roles.index(Role.DON)
        # If don_idx would land on AI, swap
        if don_idx >= len(humans):
            # Find a non-don role at human position
            for i in range(len(humans)):
                if roles[i] != Role.DON:
                    roles[i], roles[don_idx] = roles[don_idx], roles[i]
                    break

    # Assign: first humans then AIs
    all_ordered = humans + ais
    for p, r in zip(all_ordered, roles):
        p.role = r


# ══════════════════════════════════════════════════════════════════
# ROLE ENGINE
# ══════════════════════════════════════════════════════════════════
class RoleEngine:

    @staticmethod
    def resolve_night(players: dict[int, Player]) -> NightResult:
        res = NightResult()
        alive = {uid: p for uid, p in players.items() if p.is_alive}

        mafia_votes: dict[int, int] = {}
        heal_target: Optional[int] = None
        komissar_checks: list[tuple[int, int]] = []  # (komissar_uid, target_uid)
        guvoh_watches: list[tuple[int, int]] = []    # (guvoh_uid, target_uid)

        for uid, p in alive.items():
            if not p.has_acted or p.night_target is None:
                continue
            t = p.night_target
            if t not in players or not players[t].is_alive:
                continue

            if p.role in (Role.MAFIA, Role.DON):
                mafia_votes[t] = mafia_votes.get(t, 0) + 1
            elif p.role == Role.SHIFOKOR:
                heal_target = t
            elif p.role == Role.KOMISSAR:
                komissar_checks.append((uid, t))
            elif p.role == Role.GUVOH:
                guvoh_watches.append((uid, t))

        # Heal
        if heal_target is not None:
            res.healed.append(heal_target)

        # Mafia kill (majority)
        if mafia_votes:
            kill_target = max(mafia_votes, key=lambda k: mafia_votes[k])
            if kill_target in res.healed:
                res.messages.append("💉 Shifokor kimnidir o'limdan saqlab qoldi!")
            else:
                players[kill_target].is_alive = False
                res.killed.append(kill_target)

        # Komissar checks
        for k_uid, t_uid in komissar_checks:
            is_mafia = players[t_uid].role.is_mafia_team
            # Don is immune: komissar sees "tinch" for Don
            if players[t_uid].role == Role.DON:
                is_mafia = False
            res.checked[t_uid] = is_mafia

        # Guvoh (witness) - learns what target did last night
        for g_uid, t_uid in guvoh_watches:
            target_p = players[t_uid]
            if target_p.role.is_mafia_team:
                if target_p.night_target and target_p.night_target in players:
                    victim_name = players[target_p.night_target].name
                    info = (f"🔴 {target_p.name} ({target_p.role.emoji}) "
                            f"→ <b>{victim_name}</b>ni o'ldirmoqchi edi!")
                else:
                    info = f"🔴 {target_p.name} Mafiya/Don — lekin nishon tanlamadi."
            elif target_p.role == Role.KOMISSAR and target_p.night_target:
                tname = players.get(target_p.night_target, Player(-1,"?")).name
                info = f"👮 {target_p.name} → <b>{tname}</b>ni tekshirdi."
            elif target_p.role == Role.SHIFOKOR and target_p.night_target:
                tname = players.get(target_p.night_target, Player(-1,"?")).name
                info = f"💉 {target_p.name} → <b>{tname}</b>ni davoladi."
            else:
                info = f"👨‍🌾 {target_p.name} hech narsa qilmadi (oddiy fuqaro)."
            res.witnessed[g_uid] = info

        return res

    @staticmethod
    def check_win(players: dict[int, Player]) -> Optional[str]:
        alive = [p for p in players.values() if p.is_alive]
        if not alive:
            return "draw"

        mafia_alive = [p for p in alive if p.role.is_mafia_team]
        town_alive  = [p for p in alive if not p.role.is_mafia_team]

        # Town wins: no mafia left
        if not mafia_alive:
            return "town"

        # Mafia wins: mafia >= town
        if len(mafia_alive) >= len(town_alive):
            return "mafia"

        return None


# ══════════════════════════════════════════════════════════════════
# DATABASE
# ══════════════════════════════════════════════════════════════════
SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    game_id      TEXT PRIMARY KEY,
    chat_id      INTEGER,
    started_at   TEXT,
    ended_at     TEXT,
    winner       TEXT,
    player_count INTEGER DEFAULT 0,
    rounds       INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS player_stats (
    user_id      INTEGER PRIMARY KEY,
    username     TEXT DEFAULT '',
    games        INTEGER DEFAULT 0,
    wins         INTEGER DEFAULT 0,
    losses       INTEGER DEFAULT 0,
    survived     INTEGER DEFAULT 0,
    elo          INTEGER DEFAULT 1000,
    streak       INTEGER DEFAULT 0,
    best_streak  INTEGER DEFAULT 0,
    role_wins    TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_elo ON player_stats(elo DESC);
"""

ACHIEVEMENTS_DEF = {
    "first_win":   ("🏅", "Birinchi g'alaba",   "Birinchi o'yinni yut"),
    "veteran":     ("🎖️", "Veteran",             "10 ta o'yin o'yna"),
    "streak3":     ("🔥", "O'tda yonmoq",         "3 ta ketma-ket g'alaba"),
    "streak5":     ("⚡", "To'xtatib bo'lmas",    "5 ta ketma-ket g'alaba"),
    "mafia_boss":  ("👔", "Mafia Bosi",           "Mafia sifatida 5 marta yut"),
    "survivor":    ("🛡️", "Omon qoluvchi",        "5 ta o'yinda tirik qol"),
    "don_master":  ("🧠", "Don Ustasi",           "Don sifatida 3 marta yut"),
    "komissar_pro":("🔍", "Komissar Pro",         "3 ta Mafiayani fosh qil"),
}


class DB:
    def __init__(self):
        self._db: Optional[aiosqlite.Connection] = None

    async def connect(self):
        self._db = await aiosqlite.connect(DB_PATH)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.commit()
        log.info("DB ulandi: %s", DB_PATH)

    async def close(self):
        if self._db:
            await self._db.close()

    async def save_game(self, game_id: str, chat_id: int, player_count: int):
        await self._db.execute(
            "INSERT OR REPLACE INTO games(game_id,chat_id,started_at,player_count) VALUES(?,?,?,?)",
            (game_id, chat_id, datetime.utcnow().isoformat(), player_count)
        )
        await self._db.commit()

    async def end_game(self, game_id: str, winner: str, rounds: int):
        await self._db.execute(
            "UPDATE games SET ended_at=?,winner=?,rounds=? WHERE game_id=?",
            (datetime.utcnow().isoformat(), winner, rounds, game_id)
        )
        await self._db.commit()

    async def update_player(self, user_id: int, username: str, won: bool,
                             survived: bool, role: str, opponent_elo: int = 1000):
        # ensure exists
        await self._db.execute(
            "INSERT OR IGNORE INTO player_stats(user_id,username) VALUES(?,?)",
            (user_id, username)
        )
        async with self._db.execute(
            "SELECT * FROM player_stats WHERE user_id=?", (user_id,)
        ) as c:
            row = dict(await c.fetchone())

        import math
        old_elo = row["elo"]
        expected = 1 / (1 + math.pow(10, (opponent_elo - old_elo) / 400))
        elo_change = round(32 * ((1.0 if won else 0.0) - expected))
        new_elo = max(100, old_elo + elo_change)

        new_streak = (row["streak"] + 1) if won else 0
        best_streak = max(row["best_streak"], new_streak)

        rw = json.loads(row["role_wins"])
        if won:
            rw[role] = rw.get(role, 0) + 1

        await self._db.execute(
            """UPDATE player_stats SET
               username=?, games=games+1, wins=wins+?, losses=losses+?,
               survived=survived+?, elo=?, streak=?, best_streak=?, role_wins=?
               WHERE user_id=?""",
            (username, 1 if won else 0, 0 if won else 1,
             1 if survived else 0, new_elo, new_streak, best_streak,
             json.dumps(rw), user_id)
        )
        await self._db.commit()
        return elo_change

    async def get_stats(self, user_id: int) -> Optional[dict]:
        async with self._db.execute(
            "SELECT * FROM player_stats WHERE user_id=?", (user_id,)
        ) as c:
            row = await c.fetchone()
        if not row:
            return None
        d = dict(row)
        d["role_wins"] = json.loads(d["role_wins"])
        return d

    async def get_leaderboard(self, limit=10) -> list:
        async with self._db.execute(
            """SELECT username,games,wins,elo,best_streak,
               ROUND(CAST(wins AS FLOAT)/MAX(games,1)*100,1) as wr
               FROM player_stats WHERE games>0
               ORDER BY elo DESC LIMIT ?""", (limit,)
        ) as c:
            return [dict(r) for r in await c.fetchall()]

    async def get_global(self) -> dict:
        async with self._db.execute(
            """SELECT COUNT(*) as total,
               SUM(CASE WHEN winner='town'  THEN 1 ELSE 0 END) as town,
               SUM(CASE WHEN winner='mafia' THEN 1 ELSE 0 END) as mafia,
               SUM(CASE WHEN winner='suicid'THEN 1 ELSE 0 END) as suicid,
               AVG(player_count) as avg_p, AVG(rounds) as avg_r
               FROM games WHERE winner IS NOT NULL"""
        ) as c:
            row = await c.fetchone()
        return dict(row) if row else {}

    def _get_achievements(self, stats: dict) -> list[str]:
        earned = []
        if stats["wins"] >= 1:           earned.append("first_win")
        if stats["games"] >= 10:         earned.append("veteran")
        if stats["streak"] >= 3:         earned.append("streak3")
        if stats["streak"] >= 5:         earned.append("streak5")
        if stats["survived"] >= 5:       earned.append("survivor")
        rw = stats["role_wins"]
        if rw.get("mafia",0)+rw.get("don",0) >= 5:   earned.append("mafia_boss")
        if rw.get("don",0) >= 3:         earned.append("don_master")
        return earned


db = DB()

# ══════════════════════════════════════════════════════════════════
# VOTE TRACKER
# ══════════════════════════════════════════════════════════════════
class VoteTracker:
    def __init__(self):
        self._v: dict[int, int] = {}

    def cast(self, voter: int, target: int) -> bool:
        new = voter not in self._v
        self._v[voter] = target
        return new

    def prev(self, voter: int) -> Optional[int]:
        return self._v.get(voter)

    def tally(self) -> dict[int, int]:
        t: dict[int, int] = {}
        for v in self._v.values():
            t[v] = t.get(v, 0) + 1
        return t

    def count(self) -> int:
        return len(self._v)

    def reset(self):
        self._v.clear()


# ══════════════════════════════════════════════════════════════════
# GAME MANAGER
# ══════════════════════════════════════════════════════════════════
SendFn = Callable[..., Awaitable[None]]


class Game:
    def __init__(self, chat_id: int, send_fn: SendFn):
        self.chat_id   = chat_id
        self.game_id   = str(uuid.uuid4())[:6].upper()
        self.send      = send_fn
        self.phase     = Phase.LOBBY
        self.players:  dict[int, Player] = {}
        self.round     = 0
        self.host_id:  Optional[int] = None
        self.votes     = VoteTracker()
        self._task:    Optional[asyncio.Task] = None
        self._extends  = 0           # lobby extensions used
        self._lobby_remaining = LOBBY_TIMEOUT
        self._defense_id: Optional[int] = None

    # ── Lobby ──────────────────────────────────────────────────────
    def join(self, uid: int, name: str) -> bool:
        if uid in self.players or len(self.players) >= MAX_PLAYERS or self.phase != Phase.LOBBY:
            return False
        self.players[uid] = Player(uid, name)
        if not self.host_id:
            self.host_id = uid
        return True

    def leave(self, uid: int) -> bool:
        if uid not in self.players or self.phase != Phase.LOBBY:
            return False
        del self.players[uid]
        if self.host_id == uid and self.players:
            self.host_id = next(iter(self.players))
        return True

    def add_ai(self) -> bool:
        ai_n = sum(1 for p in self.players.values() if p.is_ai)
        if ai_n >= 6 or len(self.players) >= MAX_PLAYERS:
            return False
        name = next(
            (n for n in AI_NAMES if n not in {p.name for p in self.players.values()}),
            f"🤖 Bot{random.randint(10,99)}"
        )
        ai_id = -(1000 + ai_n)
        self.players[ai_id] = Player(ai_id, name, is_ai=True)
        return True

    def n(self) -> int:
        return len(self.players)

    def alive(self) -> list[Player]:
        return [p for p in self.players.values() if p.is_alive]

    def is_ended(self) -> bool:
        return self.phase == Phase.ENDED

    # ── Start lobby countdown ──────────────────────────────────────
    async def start_lobby_timer(self) -> None:
        """60s lobby timer with warnings at 30s and 10s."""
        remaining = LOBBY_TIMEOUT
        while remaining > 0:
            await asyncio.sleep(1)
            remaining -= 1
            if remaining == 30:
                await self.send(
                    self.chat_id,
                    f"⏰ <b>Lobbyga qo'shilishga 30 soniya qoldi!</b>\n"
                    f"Hozir {self.n()} o'yinchi bor.\n/join yozing!"
                )
            elif remaining == 10:
                await self.send(
                    self.chat_id,
                    f"⚠️ <b>10 soniya qoldi!</b> Shoshiling! ({self.n()} o'yinchi)"
                )
            self._lobby_remaining = remaining

        # Time's up — auto start or cancel
        if self.phase != Phase.LOBBY:
            return
        if self.n() >= MIN_PLAYERS:
            await self.send(self.chat_id, "⏱ Vaqt tugadi! O'yin avtomatik boshlanmoqda...")
            await self._do_start()
        else:
            self.phase = Phase.ENDED
            await self.send(
                self.chat_id,
                f"❌ Vaqt tugadi. Yetarli o'yinchi yo'q ({self.n()}/{MIN_PLAYERS}).\n"
                f"Yangi o'yin uchun /newgame"
            )

    async def extend_lobby(self, uid: int) -> str:
        if uid != self.host_id:
            try:
                # check admin
                pass
            except Exception:
                pass
        if self._extends >= MAX_EXTEND:
            return f"❌ Maksimal uzaytirish ({MAX_EXTEND} marta) ishlatildi."
        if self.phase != Phase.LOBBY:
            return "❌ Lobby fazasi emas."
        self._extends += 1
        self._lobby_remaining += EXTEND_SECS
        return f"✅ +{EXTEND_SECS} soniya qo'shildi! ({self._extends}/{MAX_EXTEND})"

    # ── Start game ────────────────────────────────────────────────
    async def start_game(self) -> str:
        if self.phase != Phase.LOBBY:
            return "❌ Lobby fazasi emas."
        if self.n() < MIN_PLAYERS:
            return f"❌ Kamida {MIN_PLAYERS} o'yinchi kerak! (hozir {self.n()})"
        await self._do_start()
        return "ok"

    async def _do_start(self) -> None:
        if self.phase != Phase.LOBBY:
            return
        self._cancel()
        assign_roles(list(self.players.values()))
        self.phase = Phase.NIGHT

        await db.save_game(self.game_id, self.chat_id, self.n())

        # Announce
        await self.send(
            self.chat_id,
            f"🎭 <b>O'yin #{self.game_id} boshlandi!</b>\n"
            f"👥 {self.n()} o'yinchi — rollar taqsimlandi!\n\n"
            f"Shaxsiy xabaringizni tekshiring 📩"
        )
        await self._begin_night()

    # ── Night ─────────────────────────────────────────────────────
    async def _begin_night(self) -> None:
        self.phase = Phase.NIGHT
        self.round += 1
        for p in self.players.values():
            p.reset_night()

        await self.send(
            self.chat_id,
            f"🌙 <b>{self.round}-kecha boshlanmoqda...</b>\n"
            f"Maxsus rollar, DM ni tekshiring!\n"
            f"⏱ {NIGHT_TIMEOUT} soniya"
        )

        # AI actions
        await self._ai_night()

        self._cancel()
        self._task = asyncio.create_task(self._night_timeout())

    async def _ai_night(self) -> None:
        alive_others_ids = [uid for uid, p in self.players.items() if p.is_alive]
        for uid, p in self.players.items():
            if not p.is_alive or not p.is_ai or not p.role.has_night_action:
                continue
            await asyncio.sleep(random.uniform(1, 3))
            candidates = [i for i in alive_others_ids if i != uid]
            if candidates:
                p.night_target = random.choice(candidates)
                p.has_acted = True

    async def _night_timeout(self) -> None:
        await asyncio.sleep(NIGHT_TIMEOUT)
        await self._resolve_night()

    async def record_action(self, uid: int, target_id: int) -> str:
        if self.phase != Phase.NIGHT:
            return "❌ Kecha fazasi emas."
        p = self.players.get(uid)
        if not p or not p.is_alive:
            return "❌ Siz o'yinda yoki tirik emassiz."
        if not p.role.has_night_action:
            return "❌ Sizning rolingizda kecha harakati yo'q."
        t = self.players.get(target_id)
        if not t or not t.is_alive:
            return "❌ Noto'g'ri nishon."
        if target_id == uid and p.role != Role.SHIFOKOR:
            return "❌ O'zingizni nishon qila olmaysiz."
        p.night_target = target_id
        p.has_acted = True
        if self._all_acted():
            self._cancel()
            asyncio.create_task(self._resolve_night())
        return f"✅ <b>{t.name}</b> ga harakat qayd etildi."

    def _all_acted(self) -> bool:
        for p in self.players.values():
            if p.is_alive and p.role.has_night_action and not p.has_acted:
                return False
        return True

    async def _resolve_night(self) -> None:
        if self.phase != Phase.NIGHT:
            return

        res = RoleEngine.resolve_night(self.players)
        lines = ["☀️ <b>Tong otdi...</b>\n"]

        if not res.killed:
            lines.append("🕊️ Bu kecha hech kim vafot etmadi!")
        else:
            for uid in res.killed:
                p = self.players[uid]
                lines.append(
                    f"💀 <b>{p.name}</b> o'lgan topildi!\n"
                    f"   U {p.role.emoji} <b>{p.role.uz_name}</b> edi."
                )

        lines.extend(res.messages)
        await self.send(self.chat_id, "\n".join(lines))

        # Private: komissar results
        for k_uid, p in self.players.items():
            if p.role == Role.KOMISSAR and p.is_alive and p.night_target in res.checked:
                is_m = res.checked[p.night_target]
                t_name = self.players[p.night_target].name
                result_text = "🔴 MAFIYA!" if is_m else "🟢 Tinch fuqaro."
                await self.send(
                    k_uid,
                    f"👮 Tekshiruv natijasi:\n"
                    f"<b>{t_name}</b> → {result_text}"
                )

        # Private: guvoh results
        for g_uid, info in res.witnessed.items():
            await self.send(g_uid, f"👁 <b>Kuzatuv natijasi:</b>\n{info}")

        winner = RoleEngine.check_win(self.players)
        if winner:
            await self._end_game(winner)
        else:
            await self._begin_day()

    # ── Day ───────────────────────────────────────────────────────
    async def _begin_day(self) -> None:
        self.phase = Phase.DAY

        alive_list = "\n".join(
            f"  ✅ {p.tag}" for p in self.players.values() if p.is_alive
        )
        await self.send(
            self.chat_id,
            f"☀️ <b>{self.round}-kun — Muhokama!</b>\n\n"
            f"<b>Tirik o'yinchilar ({len(self.alive())}):</b>\n{alive_list}\n\n"
            f"🗣 {DAY_TIMEOUT} soniya muhokama qiling!"
        )

        self._cancel()
        self._task = asyncio.create_task(self._day_timeout())

    async def _day_timeout(self) -> None:
        await asyncio.sleep(DAY_TIMEOUT)
        await self._begin_voting()

    async def skip_day(self, uid: int) -> str:
        if uid != self.host_id:
            return "❌ Faqat host o'tkazib yuborishi mumkin."
        if self.phase != Phase.DAY:
            return "❌ Kun fazasi emas."
        self._cancel()
        asyncio.create_task(self._begin_voting())
        return "⏩ Ovoz berishga o'tilmoqda!"

    # ── Voting ────────────────────────────────────────────────────
    async def _begin_voting(self) -> None:
        self.phase = Phase.VOTING
        self.votes.reset()

        alive = self.alive()
        targets = [(p.user_id, f"{p.role.emoji if False else ''} {p.name}") for p in alive]

        # Simple display without role emoji (roles hidden)
        targets_clean = [(p.user_id, p.name) for p in alive]

        buttons = [
            [InlineKeyboardButton(f"🗳 {name}", callback_data=f"vote:{uid}")]
            for uid, name in targets_clean
        ]
        buttons.append([InlineKeyboardButton("⏭ Betaraf", callback_data="vote:skip")])

        await self.send(
            self.chat_id,
            f"🗳 <b>Ovoz berish!</b>\n"
            f"Kim chiqarilsin? ⏱ {VOTE_TIMEOUT} soniya",
            markup=InlineKeyboardMarkup(buttons)
        )

        # AI votes
        for uid, p in self.players.items():
            if p.is_alive and p.is_ai:
                await asyncio.sleep(random.uniform(1, 3))
                candidates = [i for i in [q.user_id for q in self.alive()] if i != uid]
                if candidates:
                    self.votes.cast(uid, random.choice(candidates))

        self._cancel()
        self._task = asyncio.create_task(self._vote_timeout())

    def cast_vote(self, voter: int, target: int) -> str:
        if self.phase != Phase.VOTING:
            return "❌ Ovoz berish vaqti emas."
        vp = self.players.get(voter)
        tp = self.players.get(target)
        if not vp or not vp.is_alive:
            return "❌ Siz ovoz bera olmaysiz."
        if not tp or not tp.is_alive:
            return "❌ Noto'g'ri nishon."
        if voter == target:
            return "❌ O'zingizga ovoz bera olmaysiz."
        prev = self.votes.prev(voter)
        self.votes.cast(voter, target)
        if prev and prev != target and prev in self.players:
            return f"🔄 Ovoz o'zgartirildi → <b>{tp.name}</b>"
        return f"✅ <b>{tp.name}</b> ga ovoz berildi"

    async def _vote_timeout(self) -> None:
        await asyncio.sleep(VOTE_TIMEOUT)
        await self._resolve_vote()

    async def _resolve_vote(self) -> None:
        if self.phase != Phase.VOTING:
            return

        tally = self.votes.tally()
        if not tally:
            await self.send(self.chat_id, "🤷 Hech kim ovoz bermadi. Hech kim chiqarilmadi.")
            await self._maybe_continue()
            return

        # Show results
        tally_lines = [
            f"  {self.players[uid].name}: {v} ovoz"
            for uid, v in sorted(tally.items(), key=lambda x: -x[1])
            if uid in self.players
        ]
        max_v = max(tally.values())
        leaders = [uid for uid, v in tally.items() if v == max_v and uid in self.players]

        if len(leaders) > 1:
            names = ", ".join(self.players[uid].name for uid in leaders)
            await self.send(
                self.chat_id,
                f"📊 <b>Ovozlar:</b>\n" + "\n".join(tally_lines) +
                f"\n\n⚖️ Tenglik: <b>{names}</b>! Hech kim chiqarilmadi."
            )
            await self._maybe_continue()
            return

        condemned = leaders[0]
        p = self.players[condemned]

        # Suicid: wins immediately
        if p.role == Role.SUICID:
            p.is_alive = False
            await self.send(
                self.chat_id,
                f"📊 <b>Ovozlar:</b>\n" + "\n".join(tally_lines) +
                f"\n\n☠️ <b>{p.name}</b> osib o'ldirildi...\n"
                f"U {p.role.emoji} <b>SUICID</b> edi — va U G'OLIB BO'LDI! 🎉"
            )
            await self._end_game("suicid", winner_id=condemned)
            return

        # Kamikaze: kills a random extra player
        kamikaze_extra: Optional[int] = None
        if p.role == Role.KAMIKAZE:
            others = [uid for uid, q in self.players.items()
                      if q.is_alive and uid != condemned]
            if others:
                kamikaze_extra = random.choice(others)

        # Defense phase
        self._defense_id = condemned
        self.phase = Phase.DEFENSE

        msg = (
            f"📊 <b>Ovozlar:</b>\n" + "\n".join(tally_lines) +
            f"\n\n⚖️ <b>{p.name}</b> — osishdan oldin {DEFENSE_TIMEOUT} soniya gapirish huquqi!"
        )
        await self.send(self.chat_id, msg)

        self._cancel()
        self._task = asyncio.create_task(
            self._defense_timeout(condemned, kamikaze_extra)
        )

    async def _defense_timeout(self, condemned_id: int, kamikaze_extra: Optional[int]) -> None:
        await asyncio.sleep(DEFENSE_TIMEOUT)
        await self._execute(condemned_id, kamikaze_extra)

    async def _execute(self, condemned_id: int, kamikaze_extra: Optional[int]) -> None:
        if self.phase not in (Phase.DEFENSE, Phase.VOTING):
            return
        p = self.players.get(condemned_id)
        if not p:
            return

        p.is_alive = False
        msg = (
            f"🪓 <b>{p.name}</b> osib o'ldirildi!\n"
            f"U {p.role.emoji} <b>{p.role.uz_name}</b> edi."
        )

        if kamikaze_extra and self.players.get(kamikaze_extra):
            victim = self.players[kamikaze_extra]
            victim.is_alive = False
            msg += (
                f"\n\n💣 KAMIKAZE! <b>{p.name}</b> o'lishdan oldin "
                f"<b>{victim.name}</b> ni ham o'ldirdi!"
            )

        await self.send(self.chat_id, msg)
        await self._maybe_continue()

    async def _maybe_continue(self) -> None:
        winner = RoleEngine.check_win(self.players)
        if winner:
            await self._end_game(winner)
        else:
            await self._begin_night()

    # ── End ───────────────────────────────────────────────────────
    async def _end_game(self, winner: str, winner_id: Optional[int] = None) -> None:
        self.phase = Phase.ENDED
        self._cancel()

        emoji = {"town": "🏙", "mafia": "🔫", "suicid": "☠️", "draw": "⚖️"}.get(winner, "🎭")
        text  = {
            "town":   "Fuqarolar g'alaba qozondi! 🏙",
            "mafia":  "Mafiya g'alaba qozondi! 🔫",
            "suicid": "Suicid g'alaba qozondi! ☠️",
            "draw":   "Durrang! ⚖️",
        }.get(winner, winner)

        reveal = "\n".join(
            f"  {p.role.emoji} <b>{p.name}</b> — {p.role.uz_name} "
            f"[{'✅ Tirik' if p.is_alive else '💀 O\'ldi'}]"
            for p in self.players.values()
        )

        await self.send(
            self.chat_id,
            f"{emoji} <b>O'YIN TUGADI!</b>\n<b>{text}</b>\n\n"
            f"<b>Yakuniy rollar:</b>\n{reveal}\n\n"
            f"📊 {self.round} tur o'ynaldi. /stats buyrug'i bilan statistika ko'ring!"
        )

        # Persist
        await db.end_game(self.game_id, winner, self.round)

        all_elos = []
        for p in self.players.values():
            if p.user_id > 0:
                s = await db.get_stats(p.user_id)
                if s:
                    all_elos.append(s["elo"])
        avg_elo = sum(all_elos) // len(all_elos) if all_elos else 1000

        for p in self.players.values():
            if p.is_ai or p.user_id < 0:
                continue
            won = (
                (winner == "town"   and not p.role.is_mafia_team) or
                (winner == "mafia"  and p.role.is_mafia_team) or
                (winner == "suicid" and p.role == Role.SUICID and p.user_id == winner_id)
            )
            elo_ch = await db.update_player(
                p.user_id, p.name, won, p.is_alive, p.role.value, avg_elo
            )
            sign = "+" if elo_ch >= 0 else ""
            try:
                await self.send(
                    p.user_id,
                    f"📊 O'yin tugadi!\n"
                    f"{'🏆 G\'ALABA' if won else '💀 Mag\'lubiyat'}\n"
                    f"ELO: {sign}{elo_ch}"
                )
            except Exception:
                pass

    def _cancel(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    def role_info(self, uid: int) -> str:
        p = self.players.get(uid)
        if not p:
            return "Siz bu o'yinda emassiz."
        lines = [f"{p.role.emoji} <b>Siz — {p.role.uz_name}!</b>\n", p.role.description]
        if p.role.is_mafia_team:
            mates = [
                q.name for qid, q in self.players.items()
                if q.role.is_mafia_team and qid != uid
            ]
            if mates:
                lines.append(f"\n🤝 Jamoadoshlar: <b>{', '.join(mates)}</b>")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# REGISTRY
# ══════════════════════════════════════════════════════════════════
class Registry:
    def __init__(self):
        self._g: dict[int, Game] = {}

    def get(self, chat_id: int) -> Optional[Game]:
        return self._g.get(chat_id)

    def new(self, chat_id: int, send_fn: SendFn) -> Game:
        g = Game(chat_id, send_fn)
        self._g[chat_id] = g
        return g

    def remove(self, chat_id: int):
        self._g.pop(chat_id, None)

    def cleanup(self) -> int:
        ended = [c for c, g in self._g.items() if g.is_ended()]
        for c in ended:
            del self._g[c]
        return len(ended)

    def find_user(self, uid: int) -> Optional[Game]:
        for g in self._g.values():
            if uid in g.players:
                return g
        return None


reg = Registry()

# ══════════════════════════════════════════════════════════════════
# SEND HELPER
# ══════════════════════════════════════════════════════════════════
async def safe_send(app: Application, chat_id: int, text: str,
                    markup: Optional[InlineKeyboardMarkup] = None) -> None:
    try:
        await app.bot.send_message(
            chat_id=chat_id, text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
        )
    except Forbidden:
        log.warning("Forbidden: %s", chat_id)
    except BadRequest as e:
        log.warning("BadRequest %s: %s", chat_id, e)
    except TelegramError as e:
        log.error("TelegramError %s: %s", chat_id, e)


def make_send(app: Application) -> SendFn:
    async def _s(chat_id: int, text: str, **kw) -> None:
        await safe_send(app, chat_id, text, kw.get("markup"))
    return _s


# ══════════════════════════════════════════════════════════════════
# NIGHT DM
# ══════════════════════════════════════════════════════════════════
async def send_night_dm(app: Application, game: Game, uid: int) -> None:
    p = game.players.get(uid)
    if not p or not p.is_alive or not p.role.has_night_action:
        return

    alive_targets = [
        (q.user_id, q.name)
        for q in game.alive()
        if q.user_id != uid
    ]

    verbs = {
        Role.MAFIA:    "🔫 Kim o'ldirilsin?",
        Role.DON:      "🧠 Kim o'ldirilsin? (Don buyrug'i)",
        Role.KOMISSAR: "👮 Kimni tekshirasiz?",
        Role.SHIFOKOR: "💉 Kimni davolaysiz?",
        Role.GUVOH:    "👁 Kimni kuzatasiz?",
    }
    verb = verbs.get(p.role, "🎯 Nishon tanlang")

    buttons = [
        [InlineKeyboardButton(f"{p.role.emoji} {name}", callback_data=f"night:{uid2}")]
        for uid2, name in alive_targets
    ]
    buttons.append([InlineKeyboardButton("⏭ O'tkazib yuborish", callback_data="night:skip")])

    try:
        await app.bot.send_message(
            chat_id=uid,
            text=f"🌙 <b>Kecha harakati!</b>\n{verb}",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    except (Forbidden, BadRequest) as e:
        log.warning("Night DM fail %s: %s", uid, e)


# ══════════════════════════════════════════════════════════════════
# KEYBOARDS
# ══════════════════════════════════════════════════════════════════
def lobby_kb(n: int, is_host: bool) -> InlineKeyboardMarkup:
    btns = [
        [InlineKeyboardButton("🙋 Qo'shilish", callback_data="join"),
         InlineKeyboardButton("🚪 Chiqish",    callback_data="leave")],
        [InlineKeyboardButton("🤖 AI qo'shish", callback_data="add_ai")],
    ]
    if is_host:
        if n >= MIN_PLAYERS:
            btns.append([InlineKeyboardButton(
                f"▶️ Boshlash ({n} o'yinchi)", callback_data="start_now"
            )])
        else:
            btns.append([InlineKeyboardButton(
                f"⚠️ Kamida {MIN_PLAYERS} kerak ({n}/{MIN_PLAYERS})", callback_data="noop"
            )])
        btns.append([InlineKeyboardButton(
            f"⏰ +30s uzaytirish", callback_data="extend_lobby"
        )])
    return InlineKeyboardMarkup(btns)


def stats_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📈 Reyting",         callback_data="leaderboard"),
         InlineKeyboardButton("👤 Mening stats",    callback_data="my_stats")],
        [InlineKeyboardButton("🌍 Global stats",    callback_data="global_stats"),
         InlineKeyboardButton("🏆 Yutuqlar",        callback_data="my_achievements")],
    ])


def main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎮 Yangi o'yin", callback_data="new_game"),
         InlineKeyboardButton("📊 Statistika",  callback_data="stats_menu")],
        [InlineKeyboardButton("❓ Qoidalar",    callback_data="howto")],
    ])


# ══════════════════════════════════════════════════════════════════
# COMMANDS
# ══════════════════════════════════════════════════════════════════
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🎭 <b>ULTRA MAFIA BOT</b> ga xush kelibsiz!\n\n"
        "Rollar: 👨‍🌾 Fuqaro • 🔫 Mafia • 🧠 Don\n"
        "👮 Komissar • 💉 Shifokor • 👁 Guvoh\n"
        "💣 Kamikaze • ☠️ Suicid\n\n"
        "Guruh chatda /newgame bilan boshlang!",
        parse_mode=ParseMode.HTML,
        reply_markup=main_kb(),
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🎭 <b>Buyruqlar:</b>\n\n"
        "/newgame — Yangi o'yin\n"
        "/join — Qo'shilish\n"
        "/leave — Chiqish\n"
        "/startgame — Boshlash (host)\n"
        "/endgame — Tugatish (host/admin)\n"
        "/players — O'yinchilar\n"
        "/myrole — Mening rolim\n"
        "/stats — Statistika\n"
        "/help — Yordam\n\n"
        "<b>Rollar:</b>\n"
        "👨‍🌾 Fuqaro — Mafiyani toping!\n"
        "🔫 Mafia — Har kecha o'ldiring\n"
        "🧠 Don — Mafiya boshlig'i (tekshiruvga immun)\n"
        "👮 Komissar — Rol tekshiradi\n"
        "💉 Shifokor — Bir kishini davolaydi\n"
        "👁 Guvoh — Kimdir harakatini kuzatadi\n"
        "💣 Kamikaze — Osilsa birini o'ldiradi\n"
        "☠️ Suicid — Osilsa O'ZI g'olib!\n\n"
        "<b>G'alaba:</b>\n"
        "🏙 Fuqaro: Barcha mafiya o'lsa\n"
        "🔫 Mafia: Mafiya ≥ fuqaro\n"
        "☠️ Suicid: Ovozda osilsa",
        parse_mode=ParseMode.HTML,
    )


async def cmd_newgame(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user    = update.effective_user

    reg.cleanup()
    existing = reg.get(chat_id)
    if existing and not existing.is_ended():
        await update.message.reply_text(
            "⚠️ O'yin allaqachon ketmoqda!\n/endgame bilan tugatib, qaytadan /newgame yozing."
        )
        return

    send_fn = make_send(ctx.application)
    g = reg.new(chat_id, send_fn)
    g.join(user.id, user.full_name)

    msg = await update.message.reply_text(
        f"🎮 <b>Yangi o'yin lobbisi ochildi!</b>\n"
        f"🏠 Host: <b>{user.full_name}</b>\n\n"
        f"O'yinchilar (1/{MAX_PLAYERS}):\n  ✅ {user.full_name}\n\n"
        f"⏱ <b>{LOBBY_TIMEOUT} soniya</b> ichida qo'shiling!",
        parse_mode=ParseMode.HTML,
        reply_markup=lobby_kb(1, is_host=True),
    )

    # Start lobby countdown
    asyncio.create_task(g.start_lobby_timer())


async def cmd_join(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user    = update.effective_user
    g = reg.get(chat_id)

    if not g or g.is_ended():
        await update.message.reply_text("❌ Faol lobby yo'q. /newgame yozing.")
        return
    if g.phase != Phase.LOBBY:
        await update.message.reply_text("❌ O'yin boshlangan! Keyingi o'yinga kuting.")
        return
    if g.join(user.id, user.full_name):
        n = g.n()
        plist = "\n".join(f"  ✅ {p.name}" for p in g.players.values())
        await update.message.reply_text(
            f"✅ <b>{user.full_name}</b> qo'shildi! ({n}/{MAX_PLAYERS})\n\n"
            f"<b>O'yinchilar:</b>\n{plist}",
            parse_mode=ParseMode.HTML,
            reply_markup=lobby_kb(n, is_host=(user.id == g.host_id)),
        )
    else:
        await update.message.reply_text("⚠️ Allaqachon qo'shilgansiz yoki joy yo'q.")


async def cmd_leave(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user    = update.effective_user
    g = reg.get(chat_id)
    if not g or g.phase != Phase.LOBBY:
        await update.message.reply_text("❌ Faol lobby yo'q.")
        return
    if g.leave(user.id):
        n = g.n()
        await update.message.reply_text(
            f"👋 <b>{user.full_name}</b> chiqdi. ({n}/{MAX_PLAYERS})",
            parse_mode=ParseMode.HTML,
            reply_markup=lobby_kb(n, is_host=(user.id == g.host_id)),
        )
    else:
        await update.message.reply_text("❌ Lobbida emassiz.")


async def cmd_startgame(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user    = update.effective_user
    g = reg.get(chat_id)
    if not g:
        await update.message.reply_text("❌ Faol lobby yo'q. /newgame yozing.")
        return
    if user.id != g.host_id:
        await update.message.reply_text("❌ Faqat host boshlay oladi.")
        return
    result = await g.start_game()
    if result != "ok":
        await update.message.reply_text(result)
        return
    # Send role DMs
    for p in g.players.values():
        if p.is_ai:
            continue
        try:
            await ctx.application.bot.send_message(
                chat_id=p.user_id,
                text=g.role_info(p.user_id),
                parse_mode=ParseMode.HTML,
            )
            if p.role.has_night_action:
                await send_night_dm(ctx.application, g, p.user_id)
        except Forbidden:
            await update.message.reply_text(
                f"⚠️ <b>{p.name}</b> ga xabar jo'natib bo'lmadi!\n"
                f"Iltimos, avval botga /start yuboring.",
                parse_mode=ParseMode.HTML,
            )


async def cmd_endgame(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user    = update.effective_user
    g = reg.get(chat_id)
    if not g:
        await update.message.reply_text("❌ Faol o'yin yo'q.")
        return
    try:
        member = await ctx.application.bot.get_chat_member(chat_id, user.id)
        is_adm = member.status in ("administrator", "creator")
    except Exception:
        is_adm = False
    if user.id != g.host_id and not is_adm:
        await update.message.reply_text("❌ Faqat host yoki admin tugatishi mumkin.")
        return
    reg.remove(chat_id)
    await update.message.reply_text("🛑 O'yin majburiy tugatildi.")


async def cmd_players(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    g = reg.get(update.effective_chat.id)
    if not g:
        await update.message.reply_text("❌ Faol o'yin yo'q.")
        return
    lines = [f"👥 <b>O'yin #{g.game_id} | Faza: {g.phase.name}</b>\n"]
    for p in g.players.values():
        icon = "✅" if p.is_alive else "💀"
        ai   = " 🤖" if p.is_ai else ""
        lines.append(f"  {icon} {p.name}{ai}")
    lines.append(f"\n⏱ Tur: {g.round}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_myrole(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    g = reg.find_user(uid)
    if not g:
        await update.message.reply_text("❌ Siz faol o'yinda emassiz.")
        return
    await update.message.reply_text(g.role_info(uid), parse_mode=ParseMode.HTML)


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📊 <b>Statistika</b> — nimani ko'rmoqchisiz?",
        parse_mode=ParseMode.HTML,
        reply_markup=stats_kb(),
    )


# ══════════════════════════════════════════════════════════════════
# CALLBACK HANDLER
# ══════════════════════════════════════════════════════════════════
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q    = update.callback_query
    await q.answer()
    data = q.data or ""
    uid  = q.from_user.id
    name = q.from_user.full_name
    cid  = q.message.chat_id

    try:
        # ── static ─────────────────────────────────────────────
        if data == "noop":
            return

        elif data == "new_game":
            await q.answer("Guruh chatda /newgame yozing!", show_alert=True)
            return

        elif data == "howto":
            await q.answer(
                "🌙 Kecha: rollar harakat qiladi\n"
                "☀️ Kun: muhokama (60s)\n"
                "🗳 Ovoz: kim chiqarilsin (45s)\n"
                "⚖️ Himoya: 25s gapirishga ruxsat\n\n"
                "🏙 Fuqaro yutadi: Mafiya yo'q\n"
                "🔫 Mafiya yutadi: Mafiya ≥ Fuqaro\n"
                "☠️ Suicid yutadi: Ovozda osilsa",
                show_alert=True
            )
            return

        elif data == "stats_menu":
            await q.edit_message_text(
                "📊 <b>Statistika</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=stats_kb(),
            )
            return

        # ── stats ──────────────────────────────────────────────
        elif data == "leaderboard":
            rows = await db.get_leaderboard()
            if not rows:
                await q.answer("Hali statistika yo'q!", show_alert=True)
                return
            medals = ["🥇","🥈","🥉"] + ["🏅"]*20
            lines = ["📈 <b>ELO Reytingi:</b>\n"]
            for i, r in enumerate(rows):
                lines.append(
                    f"{medals[i]} <b>{r['username']}</b> — "
                    f"ELO: <b>{r['elo']}</b> | {r['wins']}G/{r['games']}O ({r['wr']}%)"
                )
            try:
                await ctx.application.bot.send_message(
                    uid, "\n".join(lines), parse_mode=ParseMode.HTML
                )
                await q.answer("DM ga yuborildi! ✅")
            except Forbidden:
                await q.answer("\n".join(lines)[:200], show_alert=True)

        elif data == "my_stats":
            s = await db.get_stats(uid)
            if not s:
                await q.answer("Hali o'yin o'ynamagansiz!", show_alert=True)
                return
            wr = round(s["wins"] / max(s["games"], 1) * 100, 1)
            rw_lines = "\n".join(
                f"  {Role(r).emoji} {Role(r).uz_name}: {c} marta"
                for r, c in sorted(s["role_wins"].items(), key=lambda x: -x[1])
            ) or "  —"
            msg = (
                f"👤 <b>{s['username']}</b>\n\n"
                f"🎮 O'yinlar: {s['games']}\n"
                f"🏆 G'alabalar: {s['wins']} ({wr}%)\n"
                f"💀 Mag'lubiyat: {s['losses']}\n"
                f"🛡 Tirik qoldi: {s['survived']}\n"
                f"📈 ELO: <b>{s['elo']}</b>\n"
                f"🔥 Eng uzun streak: {s['best_streak']}\n\n"
                f"<b>Rol g'alabalari:</b>\n{rw_lines}"
            )
            try:
                await ctx.application.bot.send_message(uid, msg, parse_mode=ParseMode.HTML)
                await q.answer("Stats DM ga yuborildi! ✅")
            except Forbidden:
                await q.answer(msg[:200], show_alert=True)

        elif data == "global_stats":
            g_data = await db.get_global()
            if not g_data or not g_data.get("total"):
                await q.answer("Hali global statistika yo'q!", show_alert=True)
                return
            await q.answer(
                f"🌍 Jami: {int(g_data['total']or 0)} o'yin\n"
                f"🏙 Fuqaro: {int(g_data['town']or 0)}\n"
                f"🔫 Mafia: {int(g_data['mafia']or 0)}\n"
                f"☠️ Suicid: {int(g_data['suicid']or 0)}\n"
                f"O'rtacha o'yinchi: {round(g_data['avg_p']or 0,1)}",
                show_alert=True
            )

        elif data == "my_achievements":
            s = await db.get_stats(uid)
            if not s:
                await q.answer("Hali o'yin o'ynamagansiz!", show_alert=True)
                return
            earned = db._get_achievements(s)
            lines = ["🏅 <b>Yutuqlar:</b>\n"]
            for key, (emoji, title, desc) in ACHIEVEMENTS_DEF.items():
                if key in earned:
                    lines.append(f"{emoji} <b>{title}</b> — {desc}")
                else:
                    lines.append(f"🔒 <i>{title}</i> — {desc}")
            try:
                await ctx.application.bot.send_message(
                    uid, "\n".join(lines), parse_mode=ParseMode.HTML
                )
                await q.answer("Yutuqlar DM ga yuborildi! ✅")
            except Forbidden:
                await q.answer("\n".join(lines)[:200], show_alert=True)

        # ── lobby ──────────────────────────────────────────────
        elif data == "join":
            g = reg.get(cid)
            if not g or g.phase != Phase.LOBBY:
                await q.answer("Faol lobby yo'q!", show_alert=True)
                return
            if g.join(uid, name):
                n = g.n()
                plist = "\n".join(f"  ✅ {p.name}" for p in g.players.values())
                await q.edit_message_text(
                    f"🎮 <b>Lobby</b> — {n}/{MAX_PLAYERS}\n\n"
                    f"<b>O'yinchilar:</b>\n{plist}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=lobby_kb(n, is_host=(uid == g.host_id)),
                )
            else:
                await q.answer("Allaqachon qo'shilgansiz yoki joy yo'q!", show_alert=True)

        elif data == "leave":
            g = reg.get(cid)
            if g and g.leave(uid):
                n = g.n()
                plist = "\n".join(f"  ✅ {p.name}" for p in g.players.values()) or "(bo'sh)"
                await q.edit_message_text(
                    f"🎮 <b>Lobby</b> — {n}/{MAX_PLAYERS}\n\n"
                    f"<b>O'yinchilar:</b>\n{plist}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=lobby_kb(n, is_host=(uid == g.host_id)),
                )
            else:
                await q.answer("Lobbida emassiz.", show_alert=True)

        elif data == "add_ai":
            g = reg.get(cid)
            if not g or g.phase != Phase.LOBBY:
                await q.answer("Faol lobby yo'q!", show_alert=True)
                return
            if uid != g.host_id:
                await q.answer("Faqat host AI qo'sha oladi!", show_alert=True)
                return
            if g.add_ai():
                n = g.n()
                plist = "\n".join(f"  ✅ {p.name}" for p in g.players.values())
                await q.edit_message_text(
                    f"🎮 <b>Lobby</b> — {n}/{MAX_PLAYERS}\n\n"
                    f"<b>O'yinchilar:</b>\n{plist}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=lobby_kb(n, is_host=True),
                )
            else:
                await q.answer("Maksimal AI yoki lobby to'liq.", show_alert=True)

        elif data == "extend_lobby":
            g = reg.get(cid)
            if not g:
                await q.answer("Lobby topilmadi!", show_alert=True)
                return
            result = await g.extend_lobby(uid)
            await q.answer(result, show_alert=True)

        elif data == "start_now":
            g = reg.get(cid)
            if not g:
                await q.answer("Lobby topilmadi!", show_alert=True)
                return
            if uid != g.host_id:
                await q.answer("Faqat host boshlay oladi!", show_alert=True)
                return
            if g.n() < MIN_PLAYERS:
                await q.answer(f"Kamida {MIN_PLAYERS} o'yinchi kerak!", show_alert=True)
                return
            await q.edit_message_text("⏳ O'yin boshlanmoqda...", parse_mode=ParseMode.HTML)
            result = await g.start_game()
            if result == "ok":
                for p in g.players.values():
                    if p.is_ai:
                        continue
                    try:
                        await ctx.application.bot.send_message(
                            p.user_id, g.role_info(p.user_id), parse_mode=ParseMode.HTML
                        )
                        if p.role.has_night_action:
                            await send_night_dm(ctx.application, g, p.user_id)
                    except Forbidden:
                        await safe_send(
                            ctx.application, cid,
                            f"⚠️ <b>{p.name}</b> ga DM yuborib bo'lmadi! "
                            f"Iltimos, botga /start yuboring."
                        )
            else:
                await safe_send(ctx.application, cid, result)

        # ── night action ───────────────────────────────────────
        elif data.startswith("night:"):
            ts = data.split(":", 1)[1]
            if ts == "skip":
                await q.edit_message_text("✅ Kecha harakati o'tkazib yuborildi.")
                return
            g = reg.find_user(uid)
            if not g:
                await q.answer("O'yinda emassiz.", show_alert=True)
                return
            fb = await g.record_action(uid, int(ts))
            await q.edit_message_text(fb, parse_mode=ParseMode.HTML)

        # ── voting ─────────────────────────────────────────────
        elif data.startswith("vote:"):
            ts = data.split(":", 1)[1]
            g = reg.get(cid)
            if not g:
                await q.answer("Faol o'yin yo'q.", show_alert=True)
                return
            if ts == "skip":
                await q.answer("Betaraf qoldingiz.")
                return
            fb = g.cast_vote(uid, int(ts))
            await q.answer(fb)

        elif data == "skip_to_vote":
            g = reg.get(cid)
            if g:
                res = await g.skip_day(uid)
                await q.answer(res)

    except Exception as e:
        log.exception("Callback xato (data=%s): %s", data, e)
        try:
            await q.answer("⚠️ Xatolik yuz berdi.", show_alert=True)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════
# ERROR HANDLER
# ══════════════════════════════════════════════════════════════════
async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Xato: %s", ctx.error, exc_info=ctx.error)
    if isinstance(ctx.error, (Forbidden, BadRequest)):
        return
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("⚠️ Kutilmagan xatolik yuz berdi.")
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════
# POST INIT / SHUTDOWN
# ══════════════════════════════════════════════════════════════════
async def post_init(app: Application) -> None:
    await db.connect()
    await app.bot.set_my_commands([
        BotCommand("newgame",   "Yangi o'yin lobbisi"),
        BotCommand("join",      "Lobbyga qo'shilish"),
        BotCommand("leave",     "Lobbydan chiqish"),
        BotCommand("startgame", "O'yinni boshlash (host)"),
        BotCommand("endgame",   "O'yinni tugatish"),
        BotCommand("players",   "O'yinchilar ro'yxati"),
        BotCommand("myrole",    "Mening rolim"),
        BotCommand("stats",     "Statistika"),
        BotCommand("help",      "Yordam"),
    ])

    async def _cleanup():
        while True:
            await asyncio.sleep(300)
            n = reg.cleanup()
            if n:
                log.info("Tozalandi: %d o'yin", n)

    asyncio.create_task(_cleanup())
    log.info("🎭 ULTRA MAFIA BOT tayyor!")


async def post_shutdown(app: Application) -> None:
    await db.close()
    log.info("Bot to'xtatildi.")


# ══════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════
def main() -> None:
    token = os.environ.get("BOT_TOKEN", "")
    if not token:
        log.error("BOT_TOKEN topilmadi! Environment variable o'rnating.")
        sys.exit(1)

    app = (
        ApplicationBuilder()
        .token(token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("help",      cmd_help))
    app.add_handler(CommandHandler("newgame",   cmd_newgame))
    app.add_handler(CommandHandler("join",      cmd_join))
    app.add_handler(CommandHandler("leave",     cmd_leave))
    app.add_handler(CommandHandler("startgame", cmd_startgame))
    app.add_handler(CommandHandler("endgame",   cmd_endgame))
    app.add_handler(CommandHandler("players",   cmd_players))
    app.add_handler(CommandHandler("myrole",    cmd_myrole))
    app.add_handler(CommandHandler("stats",     cmd_stats))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_error_handler(on_error)

    log.info("🎭 Bot ishga tushmoqda...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
