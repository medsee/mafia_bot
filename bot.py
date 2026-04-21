"""
bot.py — ULTRA PRO MAFIA BOT v2
python-telegram-bot v21.6 | Python 3.10-3.13 compatible

Usage:
    BOT_TOKEN=your_token python bot.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import random
import uuid
import json
from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, Callable, Awaitable

import aiosqlite
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.constants import ParseMode
from telegram.error import Forbidden, BadRequest, TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

# ══════════════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════════════

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)


# ══════════════════════════════════════════════════════════════════
# MODELS
# ══════════════════════════════════════════════════════════════════

class Role(Enum):
    CIVILIAN   = "civilian"
    MAFIA      = "mafia"
    DOCTOR     = "doctor"
    DETECTIVE  = "detective"
    SNIPER     = "sniper"
    MANIAC     = "maniac"

    @property
    def emoji(self):
        return {
            Role.CIVILIAN: "👤", Role.MAFIA: "🔫", Role.DOCTOR: "💊",
            Role.DETECTIVE: "🔍", Role.SNIPER: "🎯", Role.MANIAC: "🔪",
        }[self]

    @property
    def team(self):
        if self == Role.MAFIA:   return "mafia"
        if self == Role.MANIAC:  return "maniac"
        return "town"

    @property
    def has_night_action(self):
        return self in (Role.MAFIA, Role.DOCTOR, Role.DETECTIVE, Role.SNIPER, Role.MANIAC)

    @property
    def display_name(self):
        return self.value.capitalize()


class Phase(Enum):
    LOBBY  = auto()
    NIGHT  = auto()
    DAY    = auto()
    VOTING = auto()
    ENDED  = auto()


@dataclass
class Player:
    user_id: int
    name: str
    role: Role = Role.CIVILIAN
    is_alive: bool = True
    is_ai: bool = False
    night_target: Optional[int] = None
    has_acted: bool = False
    sniper_used: bool = False

    @property
    def mention(self):
        return f"{'🤖 ' if self.is_ai else ''}{self.name}"

    def reset_night_state(self):
        self.night_target = None
        self.has_acted = False


@dataclass
class NightResult:
    killed:    list = field(default_factory=list)
    healed:    list = field(default_factory=list)
    inspected: dict = field(default_factory=dict)
    sniped:    list = field(default_factory=list)
    messages:  list = field(default_factory=list)


# Role distribution table
_ROLE_TABLES = {
    4:  [Role.MAFIA, Role.DETECTIVE, Role.CIVILIAN, Role.CIVILIAN],
    5:  [Role.MAFIA, Role.DETECTIVE, Role.CIVILIAN, Role.CIVILIAN, Role.CIVILIAN],
    6:  [Role.MAFIA, Role.MAFIA, Role.DOCTOR, Role.DETECTIVE, Role.CIVILIAN, Role.CIVILIAN],
    7:  [Role.MAFIA, Role.MAFIA, Role.DOCTOR, Role.DETECTIVE, Role.CIVILIAN, Role.CIVILIAN, Role.CIVILIAN],
    8:  [Role.MAFIA, Role.MAFIA, Role.DOCTOR, Role.DETECTIVE, Role.SNIPER, Role.CIVILIAN, Role.CIVILIAN, Role.CIVILIAN],
    9:  [Role.MAFIA, Role.MAFIA, Role.DOCTOR, Role.DETECTIVE, Role.SNIPER, Role.MANIAC, Role.CIVILIAN, Role.CIVILIAN, Role.CIVILIAN],
    10: [Role.MAFIA, Role.MAFIA, Role.MAFIA, Role.DOCTOR, Role.DETECTIVE, Role.SNIPER, Role.MANIAC, Role.CIVILIAN, Role.CIVILIAN, Role.CIVILIAN],
}

def assign_roles(players: list) -> None:
    n = len(players)
    if n < 4:
        raise ValueError("Need at least 4 players.")
    if n <= 10:
        roles = list(_ROLE_TABLES[n])
    else:
        mafia_count = max(2, n // 4)
        roles = [Role.MAFIA] * mafia_count + [Role.DOCTOR, Role.DETECTIVE, Role.SNIPER, Role.MANIAC]
        roles += [Role.CIVILIAN] * (n - len(roles))
    random.shuffle(roles)
    for player, role in zip(players, roles):
        player.role = role


# ══════════════════════════════════════════════════════════════════
# ROLE ENGINE
# ══════════════════════════════════════════════════════════════════

class RoleEngine:

    @staticmethod
    def resolve_night(players: dict) -> NightResult:
        result = NightResult()
        alive = {uid: p for uid, p in players.items() if p.is_alive}

        mafia_votes = {}
        maniac_target = None
        doctor_heal = None
        detective_targets = []
        sniper_target = None

        for uid, player in alive.items():
            if not player.has_acted or player.night_target is None:
                continue
            t = player.night_target
            if t not in players or not players[t].is_alive:
                continue

            if player.role == Role.MAFIA:
                mafia_votes[t] = mafia_votes.get(t, 0) + 1
            elif player.role == Role.MANIAC:
                maniac_target = t
            elif player.role == Role.DOCTOR:
                doctor_heal = t
            elif player.role == Role.DETECTIVE:
                detective_targets.append(t)
            elif player.role == Role.SNIPER and not player.sniper_used:
                sniper_target = t

        # 1. Sniper (unblockable)
        if sniper_target is not None:
            sniper = next((p for p in alive.values() if p.role == Role.SNIPER), None)
            if sniper:
                sniper.sniper_used = True
                players[sniper_target].is_alive = False
                result.sniped.append(sniper_target)

        # 2. Mafia kill
        mafia_kill = None
        if mafia_votes:
            mafia_kill = max(mafia_votes, key=lambda k: mafia_votes[k])

        # 3. Doctor heal
        if doctor_heal is not None:
            result.healed.append(doctor_heal)

        # 4. Apply mafia kill
        if mafia_kill is not None:
            if mafia_kill in result.healed:
                result.messages.append("🩺 The Doctor saved someone from the Mafia tonight!")
            elif mafia_kill not in result.sniped:
                players[mafia_kill].is_alive = False
                result.killed.append(mafia_kill)

        # 5. Maniac kill
        if maniac_target is not None:
            if maniac_target in result.healed:
                result.messages.append("🩺 The Doctor unknowingly saved someone from a mysterious attacker!")
            elif maniac_target not in result.sniped and players[maniac_target].is_alive:
                players[maniac_target].is_alive = False
                result.killed.append(maniac_target)

        # 6. Detective inspect
        for t in detective_targets:
            result.inspected[t] = players[t].role

        return result

    @staticmethod
    def check_win(players: dict) -> Optional[str]:
        alive = [p for p in players.values() if p.is_alive]
        if not alive:
            return "draw"

        mafia_alive  = [p for p in alive if p.role == Role.MAFIA]
        maniac_alive = [p for p in alive if p.role == Role.MANIAC]
        town_alive   = [p for p in alive if p.role not in (Role.MAFIA, Role.MANIAC)]

        if len(alive) == 1 and maniac_alive:
            return "maniac"
        if not town_alive and maniac_alive and not mafia_alive:
            return "maniac"
        if not mafia_alive and not maniac_alive:
            return "town"
        if len(mafia_alive) >= len(town_alive) + len(maniac_alive):
            return "mafia"
        return None

    @staticmethod
    def get_night_action_verb(role: Role) -> str:
        return {
            Role.MAFIA:     "🔫 Choose your kill target",
            Role.DOCTOR:    "💊 Choose who to protect",
            Role.DETECTIVE: "🔍 Choose who to investigate",
            Role.SNIPER:    "🎯 Choose your ONE-SHOT target",
            Role.MANIAC:    "🔪 Choose your kill target",
        }.get(role, "")


# ══════════════════════════════════════════════════════════════════
# AI ENGINE
# ══════════════════════════════════════════════════════════════════

class AIEngine:

    @classmethod
    def choose_night_target(cls, actor, all_players, known_roles, vote_history):
        alive_others = [p for uid, p in all_players.items() if p.is_alive and uid != actor.user_id]
        if not alive_others:
            return None
        if actor.role == Role.MAFIA:
            non_mafia = [p for p in alive_others if p.role != Role.MAFIA]
            if not non_mafia:
                return random.choice(alive_others).user_id
            priority = {Role.DETECTIVE: 10, Role.SNIPER: 9, Role.DOCTOR: 8, Role.MANIAC: 4, Role.CIVILIAN: 2}
            weights = [float(priority.get(p.role, 1)) for p in non_mafia]
            return cls._weighted_choice(non_mafia, weights)
        elif actor.role == Role.DOCTOR:
            if random.random() < 0.25:
                return actor.user_id
            for p in alive_others:
                if known_roles.get(p.user_id) == Role.DETECTIVE:
                    return p.user_id
            return random.choice(alive_others).user_id
        elif actor.role == Role.DETECTIVE:
            unknown = [p for p in alive_others if p.user_id not in known_roles]
            return random.choice(unknown if unknown else alive_others).user_id
        elif actor.role == Role.SNIPER:
            if actor.sniper_used:
                return None
            confirmed = [p for p in alive_others if known_roles.get(p.user_id) == Role.MAFIA]
            if confirmed:
                return random.choice(confirmed).user_id
            if len(alive_others) <= 4:
                return random.choice(alive_others).user_id
            return None
        elif actor.role == Role.MANIAC:
            town = [p for p in alive_others if p.role in (Role.CIVILIAN, Role.DOCTOR, Role.DETECTIVE)]
            return random.choice(town if town else alive_others).user_id
        return None

    @classmethod
    def choose_vote_target(cls, voter, all_players, known_roles, vote_history, night_kills):
        alive_others = [p for uid, p in all_players.items() if p.is_alive and uid != voter.user_id]
        if not alive_others:
            return None
        if voter.role == Role.MAFIA:
            non_mafia = [p for p in alive_others if p.role != Role.MAFIA]
            targets = non_mafia if non_mafia else alive_others
            priority = {Role.DETECTIVE: 10, Role.SNIPER: 9, Role.DOCTOR: 8, Role.MANIAC: 4, Role.CIVILIAN: 2}
            weights = [float(priority.get(p.role, 1)) for p in targets]
            return cls._weighted_choice(targets, weights)
        # Town: vote most suspicious
        weights = []
        for p in alive_others:
            w = 1.0
            if p.user_id in known_roles:
                w = 100.0 if known_roles[p.user_id] in (Role.MAFIA, Role.MANIAC) else 0.1
            weights.append(w + random.uniform(0, 0.5))
        return cls._weighted_choice(alive_others, weights)

    @staticmethod
    def _weighted_choice(candidates, weights):
        total = sum(weights)
        if total == 0:
            return random.choice(candidates).user_id
        r = random.uniform(0, total)
        cumulative = 0.0
        for p, w in zip(candidates, weights):
            cumulative += w
            if r <= cumulative:
                return p.user_id
        return candidates[-1].user_id


# ══════════════════════════════════════════════════════════════════
# DATABASE
# ══════════════════════════════════════════════════════════════════

DB_PATH = "mafia_bot.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    game_id      TEXT PRIMARY KEY,
    chat_id      INTEGER NOT NULL,
    started_at   TEXT NOT NULL,
    ended_at     TEXT,
    winner       TEXT,
    player_count INTEGER,
    rounds       INTEGER DEFAULT 0,
    player_data  TEXT
);
CREATE TABLE IF NOT EXISTS player_stats (
    user_id        INTEGER PRIMARY KEY,
    username       TEXT,
    games_played   INTEGER DEFAULT 0,
    wins           INTEGER DEFAULT 0,
    losses         INTEGER DEFAULT 0,
    survived_games INTEGER DEFAULT 0,
    role_history   TEXT DEFAULT '{}',
    win_by_role    TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_games_chat ON games(chat_id);
"""

class Database:
    def __init__(self, path=DB_PATH):
        self.path = path
        self._db = None

    async def connect(self):
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.commit()
        logger.info("Database connected: %s", self.path)

    async def close(self):
        if self._db:
            await self._db.close()

    async def start_game(self, game_id, chat_id, player_count):
        await self._db.execute(
            "INSERT OR REPLACE INTO games(game_id,chat_id,started_at,player_count) VALUES(?,?,?,?)",
            (game_id, chat_id, datetime.utcnow().isoformat(), player_count)
        )
        await self._db.commit()

    async def end_game(self, game_id, winner, rounds, player_data):
        await self._db.execute(
            "UPDATE games SET ended_at=?,winner=?,rounds=?,player_data=? WHERE game_id=?",
            (datetime.utcnow().isoformat(), winner, rounds, json.dumps(player_data), game_id)
        )
        await self._db.commit()

    async def increment_round(self, game_id):
        await self._db.execute("UPDATE games SET rounds=rounds+1 WHERE game_id=?", (game_id,))
        await self._db.commit()

    async def ensure_player(self, user_id, username):
        await self._db.execute(
            "INSERT OR IGNORE INTO player_stats(user_id,username) VALUES(?,?)", (user_id, username)
        )
        await self._db.execute(
            "UPDATE player_stats SET username=? WHERE user_id=?", (username, user_id)
        )
        await self._db.commit()

    async def record_game_result(self, user_id, username, role, won, survived):
        await self.ensure_player(user_id, username)
        async with self._db.execute(
            "SELECT role_history,win_by_role FROM player_stats WHERE user_id=?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
        rh = json.loads(row["role_history"]) if row else {}
        wb = json.loads(row["win_by_role"])  if row else {}
        rh[role] = rh.get(role, 0) + 1
        if won:
            wb[role] = wb.get(role, 0) + 1
        await self._db.execute(
            """UPDATE player_stats SET
               games_played=games_played+1, wins=wins+?, losses=losses+?,
               survived_games=survived_games+?, role_history=?, win_by_role=?
               WHERE user_id=?""",
            (1 if won else 0, 0 if won else 1, 1 if survived else 0,
             json.dumps(rh), json.dumps(wb), user_id)
        )
        await self._db.commit()

    async def get_player_stats(self, user_id):
        async with self._db.execute(
            "SELECT * FROM player_stats WHERE user_id=?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        d = dict(row)
        d["role_history"] = json.loads(d["role_history"])
        d["win_by_role"]  = json.loads(d["win_by_role"])
        return d

    async def get_leaderboard(self, limit=10):
        async with self._db.execute(
            """SELECT username, games_played, wins,
               ROUND(CAST(wins AS FLOAT)/MAX(games_played,1)*100,1) as win_rate
               FROM player_stats WHERE games_played>0
               ORDER BY wins DESC, win_rate DESC LIMIT ?""", (limit,)
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_global_stats(self):
        async with self._db.execute(
            """SELECT COUNT(*) as total_games,
               SUM(CASE WHEN winner='town'   THEN 1 ELSE 0 END) as town_wins,
               SUM(CASE WHEN winner='mafia'  THEN 1 ELSE 0 END) as mafia_wins,
               SUM(CASE WHEN winner='maniac' THEN 1 ELSE 0 END) as maniac_wins,
               AVG(player_count) as avg_players, AVG(rounds) as avg_rounds
               FROM games WHERE winner IS NOT NULL"""
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else {}


# ══════════════════════════════════════════════════════════════════
# KEYBOARDS
# ══════════════════════════════════════════════════════════════════

def lobby_keyboard(player_count, host=False):
    buttons = [
        [InlineKeyboardButton("🙋 Join Game", callback_data="join"),
         InlineKeyboardButton("🚪 Leave",     callback_data="leave")],
        [InlineKeyboardButton("🤖 Add AI Player", callback_data="add_ai")],
    ]
    if host and player_count >= 4:
        buttons.append([InlineKeyboardButton(f"▶️ Start Game ({player_count} players)", callback_data="start")])
    elif host:
        buttons.append([InlineKeyboardButton(f"⚠️ Need 4+ players ({player_count}/4)", callback_data="noop")])
    return InlineKeyboardMarkup(buttons)

def vote_keyboard(targets):
    buttons = [[InlineKeyboardButton(f"🗳️ {name}", callback_data=f"vote:{uid}")] for uid, name in targets]
    buttons.append([InlineKeyboardButton("⏭️ Abstain", callback_data="vote:skip")])
    return InlineKeyboardMarkup(buttons)

def night_action_keyboard(targets, action_emoji):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{action_emoji} {name}", callback_data=f"night:{uid}")]
        for uid, name in targets
    ])

def stats_keyboard():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard"),
        InlineKeyboardButton("📊 My Stats",    callback_data="my_stats"),
    ], [
        InlineKeyboardButton("🌍 Global Stats", callback_data="global_stats"),
    ]])

def main_menu_keyboard():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🎮 New Game",     callback_data="new_game"),
        InlineKeyboardButton("📊 Stats",        callback_data="stats_menu"),
    ], [
        InlineKeyboardButton("❓ How to Play",  callback_data="howto"),
    ]])


# ══════════════════════════════════════════════════════════════════
# VOTE TRACKER
# ══════════════════════════════════════════════════════════════════

class VoteTracker:
    def __init__(self):
        self._votes = {}

    def cast(self, voter_id, target_id):
        is_new = voter_id not in self._votes
        self._votes[voter_id] = target_id
        return is_new

    def has_voted(self, voter_id):
        return voter_id in self._votes

    def get_previous(self, voter_id):
        return self._votes.get(voter_id)

    def tally(self):
        tally = {}
        for t in self._votes.values():
            tally[t] = tally.get(t, 0) + 1
        return tally

    def leading(self):
        t = self.tally()
        return max(t, key=lambda k: t[k]) if t else None

    def voter_count(self):
        return len(self._votes)

    def reset(self):
        self._votes.clear()


# ══════════════════════════════════════════════════════════════════
# GAME MANAGER
# ══════════════════════════════════════════════════════════════════

NIGHT_ACTION_TIMEOUT = 45
DAY_SPEECH_TIMEOUT   = 60
VOTING_TIMEOUT       = 40

AI_NAMES = ["Alice", "Bob", "Carlos", "Diana", "Erik", "Fatima", "George", "Hana", "Ivan", "Julia"]

SendFn = Callable[..., Awaitable[None]]


class GameManager:
    def __init__(self, chat_id, send_fn, db):
        self.chat_id      = chat_id
        self.game_id      = str(uuid.uuid4())[:8]
        self.send         = send_fn
        self.db           = db
        self.phase        = Phase.LOBBY
        self.players      = {}
        self.round        = 0
        self.host_id      = None
        self.vote_tracker = VoteTracker()
        self._known_roles  = {}
        self._vote_history = {}
        self._all_kills    = []
        self._phase_task   = None

    # ── Lobby ──────────────────────────────────

    def join(self, user_id, name):
        if user_id in self.players or len(self.players) >= 10 or self.phase != Phase.LOBBY:
            return False
        self.players[user_id] = Player(user_id=user_id, name=name)
        if self.host_id is None:
            self.host_id = user_id
        return True

    def leave(self, user_id):
        if user_id not in self.players or self.phase != Phase.LOBBY:
            return False
        del self.players[user_id]
        if self.host_id == user_id and self.players:
            self.host_id = next(iter(self.players))
        return True

    def add_ai_player(self):
        ai_count = sum(1 for p in self.players.values() if p.is_ai)
        if ai_count >= 6 or len(self.players) >= 10:
            return False
        ai_name = next(
            (n for n in AI_NAMES if n not in {p.name for p in self.players.values()}),
            f"Bot{random.randint(100,999)}"
        )
        ai_id = -(1000 + ai_count)
        self.players[ai_id] = Player(user_id=ai_id, name=ai_name, is_ai=True)
        return True

    def player_count(self):
        return len(self.players)

    def alive_players(self):
        return [p for p in self.players.values() if p.is_alive]

    def get_player(self, user_id):
        return self.players.get(user_id)

    def is_ended(self):
        return self.phase == Phase.ENDED

    # ── Start ──────────────────────────────────

    async def start_game(self):
        if self.phase != Phase.LOBBY:
            return
        if len(self.players) < 4:
            await self.send(self.chat_id, "❌ Need at least 4 players to start!")
            return
        assign_roles(list(self.players.values()))
        self.phase = Phase.NIGHT
        await self.db.start_game(self.game_id, self.chat_id, len(self.players))
        await self.send(
            self.chat_id,
            f"🎭 <b>Game #{self.game_id} begins!</b>\n"
            f"👥 {len(self.players)} players — roles assigned.\n"
            f"Check your private messages!\n\n<i>🌙 Night falls...</i>"
        )
        await self._begin_night()

    # ── Night ──────────────────────────────────

    async def _begin_night(self):
        self.phase = Phase.NIGHT
        self.round += 1
        await self.db.increment_round(self.game_id)
        for p in self.players.values():
            p.reset_night_state()
        await self.send(self.chat_id, f"🌙 <b>Night {self.round}</b>\nThe city falls silent. Special roles, check your DMs.")
        await self._process_ai_night_actions()
        self._cancel_phase_task()
        self._phase_task = asyncio.create_task(self._night_timeout())

    async def _night_timeout(self):
        await asyncio.sleep(NIGHT_ACTION_TIMEOUT)
        await self._resolve_night()

    async def _process_ai_night_actions(self):
        for uid, player in self.players.items():
            if not player.is_alive or not player.is_ai or not player.role.has_night_action:
                continue
            await asyncio.sleep(random.uniform(1.5, 4.0))
            target = AIEngine.choose_night_target(player, self.players, self._known_roles, self._vote_history)
            if target is not None:
                player.night_target = target
                player.has_acted = True

    async def record_night_action(self, actor_id, target_id):
        if self.phase != Phase.NIGHT:
            return "❌ It's not night phase."
        actor = self.players.get(actor_id)
        if not actor or not actor.is_alive:
            return "❌ You are not in this game or already dead."
        if not actor.role.has_night_action:
            return "❌ Your role has no night action."
        if actor.role == Role.SNIPER and actor.sniper_used:
            return "❌ You've already used your sniper shot."
        target = self.players.get(target_id)
        if not target or not target.is_alive:
            return "❌ Invalid or dead target."
        if target_id == actor_id and actor.role != Role.DOCTOR:
            return "❌ You can't target yourself."
        actor.night_target = target_id
        actor.has_acted = True
        if self._all_active_roles_acted():
            self._cancel_phase_task()
            asyncio.create_task(self._resolve_night())
        return f"✅ Action recorded on <b>{target.name}</b>."

    def _all_active_roles_acted(self):
        for p in self.players.values():
            if p.is_alive and p.role.has_night_action and not p.has_acted:
                if p.role == Role.SNIPER and p.sniper_used:
                    continue
                return False
        return True

    async def _resolve_night(self):
        if self.phase != Phase.NIGHT:
            return
        result = RoleEngine.resolve_night(self.players)
        self._all_kills.extend(result.killed + result.sniped)
        self._known_roles.update(result.inspected)

        lines = ["☀️ <b>Dawn breaks...</b>\n"]
        if not result.killed and not result.sniped:
            lines.append("🕊️ The night was peaceful — nobody died!")
        else:
            for uid in result.killed:
                p = self.players[uid]
                lines.append(f"💀 <b>{p.name}</b> was found dead. They were the <b>{p.role.display_name} {p.role.emoji}</b>")
            for uid in result.sniped:
                p = self.players[uid]
                lines.append(f"🎯 <b>{p.name}</b> was eliminated by the Sniper. They were the <b>{p.role.display_name} {p.role.emoji}</b>")
        lines.extend(result.messages)
        await self.send(self.chat_id, "\n".join(lines))

        winner = RoleEngine.check_win(self.players)
        if winner:
            await self._end_game(winner)
        else:
            await self._begin_day()

    # ── Day ────────────────────────────────────

    async def _begin_day(self):
        self.phase = Phase.DAY
        alive_list = "\n".join(
            f"  {'✅' if p.is_alive else '💀'} {p.mention}" for p in self.players.values()
        )
        await self.send(
            self.chat_id,
            f"☀️ <b>Day {self.round}</b> — Discussion time!\n\n"
            f"<b>Alive ({len(self.alive_players())}):</b>\n{alive_list}\n\n"
            f"🗣️ Discuss for {DAY_SPEECH_TIMEOUT}s, then voting begins!",
            show_skip=(True)
        )
        self._cancel_phase_task()
        self._phase_task = asyncio.create_task(self._day_timeout())

    async def _day_timeout(self):
        await asyncio.sleep(DAY_SPEECH_TIMEOUT)
        await self._begin_voting()

    async def skip_to_vote(self, user_id):
        if user_id != self.host_id:
            return "❌ Only the host can skip."
        if self.phase != Phase.DAY:
            return "❌ Not in day phase."
        self._cancel_phase_task()
        asyncio.create_task(self._begin_voting())
        return "⏩ Skipping to vote!"

    # ── Voting ─────────────────────────────────

    async def _begin_voting(self):
        self.phase = Phase.VOTING
        self.vote_tracker.reset()
        alive = self.alive_players()
        await self.send(
            self.chat_id,
            f"🗳️ <b>Voting phase!</b>\nVote to eliminate a player. ⏱️ {VOTING_TIMEOUT}s",
            vote_targets=[(p.user_id, p.name) for p in alive]
        )
        await self._process_ai_votes()
        self._cancel_phase_task()
        self._phase_task = asyncio.create_task(self._voting_timeout())

    async def _process_ai_votes(self):
        for uid, player in self.players.items():
            if not player.is_alive or not player.is_ai:
                continue
            await asyncio.sleep(random.uniform(1.0, 3.0))
            target = AIEngine.choose_vote_target(player, self.players, self._known_roles, self._vote_history, self._all_kills)
            if target and target in self.players:
                self.vote_tracker.cast(uid, target)
                self._vote_history.setdefault(uid, []).append(target)

    def cast_vote(self, voter_id, target_id):
        if self.phase != Phase.VOTING:
            return "❌ Voting is not active."
        voter = self.players.get(voter_id)
        if not voter or not voter.is_alive:
            return "❌ You can't vote."
        target = self.players.get(target_id)
        if not target or not target.is_alive:
            return "❌ Invalid target."
        if target_id == voter_id:
            return "❌ Can't vote for yourself."
        prev = self.vote_tracker.get_previous(voter_id)
        self.vote_tracker.cast(voter_id, target_id)
        self._vote_history.setdefault(voter_id, []).append(target_id)
        if prev and prev != target_id:
            return f"🔄 Changed vote to <b>{target.name}</b>"
        return f"✅ Voted for <b>{target.name}</b>"

    async def _voting_timeout(self):
        await asyncio.sleep(VOTING_TIMEOUT)
        await self._resolve_vote()

    async def _resolve_vote(self):
        if self.phase != Phase.VOTING:
            return
        tally = self.vote_tracker.tally()
        if not tally:
            await self.send(self.chat_id, "🤷 No votes cast. Nobody eliminated.")
            await self._check_and_continue()
            return

        tally_lines = [f"  {self.players[uid].name}: {v} vote(s)" for uid, v in sorted(tally.items(), key=lambda x: -x[1])]
        max_votes = max(tally.values())
        leaders = [uid for uid, v in tally.items() if v == max_votes]

        if len(leaders) > 1:
            tied = ", ".join(self.players[uid].name for uid in leaders)
            await self.send(self.chat_id,
                f"📊 <b>Votes:</b>\n" + "\n".join(tally_lines) +
                f"\n\n⚖️ <b>Tie between {tied}!</b> Nobody eliminated.")
        else:
            elim = self.players[leaders[0]]
            elim.is_alive = False
            await self.send(self.chat_id,
                f"📊 <b>Votes:</b>\n" + "\n".join(tally_lines) +
                f"\n\n🪓 <b>{elim.name}</b> was eliminated! They were the <b>{elim.role.display_name} {elim.role.emoji}</b>!")

        await self._check_and_continue()

    async def _check_and_continue(self):
        winner = RoleEngine.check_win(self.players)
        if winner:
            await self._end_game(winner)
        else:
            await self._begin_night()

    # ── End ────────────────────────────────────

    async def _end_game(self, winner):
        self.phase = Phase.ENDED
        self._cancel_phase_task()

        emoji = {"town": "🏙️", "mafia": "🔫", "maniac": "🔪", "draw": "⚖️"}.get(winner, "🎭")
        text  = {"town": "The Town wins!", "mafia": "The Mafia wins!", "maniac": "The Maniac wins!", "draw": "Draw!"}.get(winner, winner)

        reveal = "\n".join(
            f"  {p.role.emoji} <b>{p.name}</b> — {p.role.display_name} [{'✅ Survived' if p.is_alive else '💀 Eliminated'}]"
            for p in self.players.values()
        )
        await self.send(self.chat_id,
            f"{emoji} <b>GAME OVER!</b>\n<b>{text}</b>\n\n<b>Final roles:</b>\n{reveal}\n\n"
            f"📊 Game lasted {self.round} round(s). Use /stats!")

        player_data = [{"user_id": p.user_id, "name": p.name, "role": p.role.value, "survived": p.is_alive, "is_ai": p.is_ai} for p in self.players.values()]
        await self.db.end_game(self.game_id, winner, self.round, player_data)

        for p in self.players.values():
            if p.is_ai:
                continue
            won = (winner == p.role.team)
            await self.db.record_game_result(p.user_id, p.name, p.role.value, won, p.is_alive)

    def _cancel_phase_task(self):
        if self._phase_task and not self._phase_task.done():
            self._phase_task.cancel()
        self._phase_task = None

    def role_info(self, user_id):
        p = self.players.get(user_id)
        if not p:
            return "You are not in this game."
        desc = {
            Role.CIVILIAN:  "You have no special ability. Vote wisely!",
            Role.MAFIA:     "Each night, coordinate to eliminate a town member.",
            Role.DOCTOR:    "Each night, protect one player from death.",
            Role.DETECTIVE: "Each night, investigate one player to learn their role.",
            Role.SNIPER:    "You have ONE bullet. Use it to eliminate a confirmed threat.",
            Role.MANIAC:    "You win ALONE. Kill at night, survive until last standing.",
        }.get(p.role, "")
        lines = [f"{p.role.emoji} <b>You are the {p.role.display_name}!</b>\n", desc]
        if p.role == Role.MAFIA:
            teammates = [q.name for qid, q in self.players.items() if q.role == Role.MAFIA and qid != user_id]
            if teammates:
                lines.append(f"\n🤝 Mafia partners: <b>{', '.join(teammates)}</b>")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# GAME REGISTRY
# ══════════════════════════════════════════════════════════════════

class GameRegistry:
    def __init__(self):
        self._games = {}

    def get(self, chat_id):
        return self._games.get(chat_id)

    def create(self, chat_id, send_fn, db):
        gm = GameManager(chat_id, send_fn, db)
        self._games[chat_id] = gm
        return gm

    def remove(self, chat_id):
        self._games.pop(chat_id, None)

    def cleanup_ended(self):
        ended = [cid for cid, gm in self._games.items() if gm.is_ended()]
        for cid in ended:
            del self._games[cid]
        return len(ended)


# ══════════════════════════════════════════════════════════════════
# GLOBALS
# ══════════════════════════════════════════════════════════════════

db_instance = Database()
registry    = GameRegistry()


# ══════════════════════════════════════════════════════════════════
# SEND HELPER
# ══════════════════════════════════════════════════════════════════

async def _send_message(app, chat_id, text, vote_targets=None, show_skip=False, **kwargs):
    try:
        markup = vote_keyboard(vote_targets) if vote_targets else None
        await app.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML, reply_markup=markup)
    except Forbidden:
        logger.warning("Forbidden: chat_id=%s", chat_id)
    except (BadRequest, TelegramError) as e:
        logger.warning("Send error to %s: %s", chat_id, e)


def make_send_fn(app):
    async def _send(chat_id, text, **kwargs):
        await _send_message(app, chat_id, text, **kwargs)
    return _send


# ══════════════════════════════════════════════════════════════════
# COMMAND HANDLERS
# ══════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎭 <b>Welcome to ULTRA PRO MAFIA BOT!</b>\n\n"
        "Roles: 👤 Civilian • 🔫 Mafia • 💊 Doctor\n"
        "       🔍 Detective • 🎯 Sniper • 🔪 Maniac\n\n"
        "Use /newgame in a group chat to start!\n"
        "Use /help to see all commands.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard(),
    )

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎭 <b>MAFIA BOT — Commands</b>\n\n"
        "<b>Group chat:</b>\n"
        "/newgame — Create lobby\n/join — Join lobby\n/leave — Leave lobby\n"
        "/startgame — Start (host)\n/endgame — Force end (host/admin)\n"
        "/players — Player list\n/stats — Statistics\n/myrole — Your role\n\n"
        "<b>Roles:</b>\n"
        "👤 Civilian — Vote wisely\n🔫 Mafia — Kill each night\n"
        "💊 Doctor — Protect each night\n🔍 Detective — Investigate each night\n"
        "🎯 Sniper — One unblockable kill\n🔪 Maniac — Solo winner\n\n"
        "<b>Win conditions:</b>\n"
        "🏙️ Town: eliminate all Mafia+Maniac\n"
        "🔫 Mafia: equal or outnumber Town\n"
        "🔪 Maniac: last survivor",
        parse_mode=ParseMode.HTML,
    )

async def cmd_newgame(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user    = update.effective_user
    registry.cleanup_ended()

    existing = registry.get(chat_id)
    if existing and not existing.is_ended():
        await update.message.reply_text("⚠️ A game is already running! Use /endgame to end it.")
        return

    send_fn = make_send_fn(ctx.application)
    gm = registry.create(chat_id, send_fn, db_instance)
    gm.join(user.id, user.full_name)

    await update.message.reply_text(
        f"🎮 <b>New Mafia game lobby!</b>\n🏠 Host: <b>{user.full_name}</b>\n\n"
        f"Players (1/10):\n  ✅ {user.full_name}\n\nMinimum 4 players needed.",
        parse_mode=ParseMode.HTML,
        reply_markup=lobby_keyboard(1, host=True),
    )

async def cmd_join(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user    = update.effective_user
    gm = registry.get(chat_id)

    if not gm or gm.is_ended():
        await update.message.reply_text("❌ No active lobby. Use /newgame.")
        return
    if gm.phase != Phase.LOBBY:
        await update.message.reply_text("❌ Game already in progress!")
        return
    if gm.join(user.id, user.full_name):
        count = gm.player_count()
        player_list = "\n".join(f"  ✅ {p.mention}" for p in gm.players.values())
        await update.message.reply_text(
            f"✅ <b>{user.full_name}</b> joined! ({count}/10)\n\n<b>Players:</b>\n{player_list}",
            parse_mode=ParseMode.HTML,
            reply_markup=lobby_keyboard(count, host=(user.id == gm.host_id)),
        )
    else:
        await update.message.reply_text("⚠️ Already in lobby or lobby full.")

async def cmd_leave(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user    = update.effective_user
    gm = registry.get(chat_id)
    if not gm or gm.phase != Phase.LOBBY:
        await update.message.reply_text("❌ No active lobby to leave.")
        return
    if gm.leave(user.id):
        await update.message.reply_text(
            f"👋 <b>{user.full_name}</b> left. ({gm.player_count()}/10)",
            parse_mode=ParseMode.HTML,
            reply_markup=lobby_keyboard(gm.player_count(), host=(user.id == gm.host_id)),
        )
    else:
        await update.message.reply_text("❌ You're not in the lobby.")

async def cmd_startgame(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user    = update.effective_user
    gm = registry.get(chat_id)

    if not gm:
        await update.message.reply_text("❌ No active lobby.")
        return
    if user.id != gm.host_id:
        await update.message.reply_text("❌ Only the host can start.")
        return

    await gm.start_game()

    for player in gm.players.values():
        if player.is_ai:
            continue
        try:
            await ctx.application.bot.send_message(
                chat_id=player.user_id, text=gm.role_info(player.user_id), parse_mode=ParseMode.HTML
            )
            if player.role.has_night_action and gm.phase == Phase.NIGHT:
                await _send_night_dm(ctx.application, gm, player.user_id)
        except Forbidden:
            await update.message.reply_text(
                f"⚠️ Can't DM <b>{player.name}</b> — they need to /start the bot privately first!",
                parse_mode=ParseMode.HTML,
            )

async def _send_night_dm(app, gm, user_id):
    player = gm.get_player(user_id)
    if not player or not player.is_alive or not player.role.has_night_action:
        return
    if player.role == Role.SNIPER and player.sniper_used:
        try:
            await app.bot.send_message(chat_id=user_id, text="🎯 You've already used your sniper shot.", parse_mode=ParseMode.HTML)
        except Forbidden:
            pass
        return
    alive_targets = [(p.user_id, p.name) for p in gm.alive_players() if p.user_id != user_id]
    verb = RoleEngine.get_night_action_verb(player.role)
    try:
        await app.bot.send_message(
            chat_id=user_id,
            text=f"🌙 <b>Night action!</b>\n{verb}:",
            parse_mode=ParseMode.HTML,
            reply_markup=night_action_keyboard(alive_targets, player.role.emoji),
        )
    except (Forbidden, BadRequest) as e:
        logger.warning("Night DM failed for %s: %s", user_id, e)

async def cmd_endgame(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user    = update.effective_user
    gm = registry.get(chat_id)
    if not gm:
        await update.message.reply_text("❌ No active game.")
        return
    try:
        member = await ctx.application.bot.get_chat_member(chat_id, user.id)
        is_admin = member.status in ("administrator", "creator")
    except Exception:
        is_admin = False
    if user.id != gm.host_id and not is_admin:
        await update.message.reply_text("❌ Only host or admin can end the game.")
        return
    registry.remove(chat_id)
    await update.message.reply_text("🛑 Game ended.")

async def cmd_players(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    gm = registry.get(update.effective_chat.id)
    if not gm:
        await update.message.reply_text("❌ No active game.")
        return
    lines = [f"👥 <b>Game #{gm.game_id} — Phase: {gm.phase.name}</b>\n"]
    for p in gm.players.values():
        lines.append(f"  {'✅' if p.is_alive else '💀'} {p.mention}{'🤖' if p.is_ai else ''}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

async def cmd_myrole(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    for gm in registry._games.values():
        if user.id in gm.players:
            await update.message.reply_text(gm.role_info(user.id), parse_mode=ParseMode.HTML)
            return
    await update.message.reply_text("❌ You're not in an active game.")

async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📊 <b>Statistics</b>\nChoose what to view:",
        parse_mode=ParseMode.HTML,
        reply_markup=stats_keyboard(),
    )


# ══════════════════════════════════════════════════════════════════
# CALLBACK HANDLER
# ══════════════════════════════════════════════════════════════════

async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data    = query.data or ""
    user    = query.from_user
    chat_id = query.message.chat_id

    try:
        if data == "noop":
            return

        elif data == "join":
            gm = registry.get(chat_id)
            if not gm or gm.phase != Phase.LOBBY:
                await query.answer("No active lobby!", show_alert=True); return
            if gm.join(user.id, user.full_name):
                count = gm.player_count()
                plist = "\n".join(f"  ✅ {p.mention}" for p in gm.players.values())
                await query.edit_message_text(
                    f"🎮 <b>Lobby</b> — {count}/10\n\n<b>Players:</b>\n{plist}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=lobby_keyboard(count, host=(user.id == gm.host_id)),
                )
            else:
                await query.answer("Already in lobby or full!", show_alert=True)

        elif data == "leave":
            gm = registry.get(chat_id)
            if gm and gm.leave(user.id):
                count = gm.player_count()
                plist = "\n".join(f"  ✅ {p.mention}" for p in gm.players.values()) or "(empty)"
                await query.edit_message_text(
                    f"🎮 <b>Lobby</b> — {count}/10\n\n<b>Players:</b>\n{plist}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=lobby_keyboard(count, host=(user.id == gm.host_id)),
                )
            else:
                await query.answer("You're not in the lobby.", show_alert=True)

        elif data == "add_ai":
            gm = registry.get(chat_id)
            if not gm or gm.phase != Phase.LOBBY:
                await query.answer("No active lobby!", show_alert=True); return
            if user.id != gm.host_id:
                await query.answer("Only host can add AI!", show_alert=True); return
            if gm.add_ai_player():
                count = gm.player_count()
                plist = "\n".join(f"  ✅ {p.mention}" for p in gm.players.values())
                await query.edit_message_text(
                    f"🎮 <b>Lobby</b> — {count}/10\n\n<b>Players:</b>\n{plist}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=lobby_keyboard(count, host=True),
                )
            else:
                await query.answer("Max AI players reached or lobby full.", show_alert=True)

        elif data == "start":
            gm = registry.get(chat_id)
            if not gm:
                await query.answer("No lobby!", show_alert=True); return
            if user.id != gm.host_id:
                await query.answer("Only host can start!", show_alert=True); return
            if gm.player_count() < 4:
                await query.answer("Need 4+ players!", show_alert=True); return
            await query.edit_message_text("⏳ Starting game...", parse_mode=ParseMode.HTML)
            await gm.start_game()
            for player in gm.players.values():
                if player.is_ai:
                    continue
                try:
                    await ctx.application.bot.send_message(
                        chat_id=player.user_id, text=gm.role_info(player.user_id), parse_mode=ParseMode.HTML
                    )
                    if player.role.has_night_action and gm.phase == Phase.NIGHT:
                        await _send_night_dm(ctx.application, gm, player.user_id)
                except Forbidden:
                    await ctx.application.bot.send_message(
                        chat_id=chat_id,
                        text=f"⚠️ Can't DM <b>{player.name}</b>. Ask them to /start the bot privately.",
                        parse_mode=ParseMode.HTML,
                    )

        elif data.startswith("night:"):
            tid_str = data.split(":", 1)[1]
            if tid_str == "skip":
                await query.edit_message_text("✅ Skipped night action."); return
            gm = next((g for g in registry._games.values() if user.id in g.players), None)
            if not gm:
                await query.answer("Not in a game.", show_alert=True); return
            fb = await gm.record_night_action(user.id, int(tid_str))
            await query.edit_message_text(fb, parse_mode=ParseMode.HTML)

        elif data.startswith("vote:"):
            ts = data.split(":", 1)[1]
            gm = registry.get(chat_id)
            if not gm:
                await query.answer("No active game.", show_alert=True); return
            if ts == "skip":
                await query.answer("You abstained."); return
            fb = gm.cast_vote(user.id, int(ts))
            await query.answer(fb)

        elif data == "skip_to_vote":
            gm = registry.get(chat_id)
            if gm:
                result = await gm.skip_to_vote(user.id)
                await query.answer(result)

        elif data in ("leaderboard", "stats_menu"):
            rows = await db_instance.get_leaderboard()
            if not rows:
                await query.answer("No stats yet!", show_alert=True); return
            medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 10
            lines = ["🏆 <b>Leaderboard</b>\n"] + [
                f"{medals[i]} <b>{r['username']}</b> — {r['wins']}W/{r['games_played']}G ({r['win_rate']}%)"
                for i, r in enumerate(rows)
            ]
            try:
                await ctx.application.bot.send_message(chat_id=user.id, text="\n".join(lines), parse_mode=ParseMode.HTML)
                await query.answer("Sent to DM!")
            except Forbidden:
                await query.answer("\n".join(lines)[:200], show_alert=True)

        elif data == "my_stats":
            stats = await db_instance.get_player_stats(user.id)
            if not stats:
                await query.answer("No stats yet!", show_alert=True); return
            wr = round(stats["wins"] / max(stats["games_played"], 1) * 100, 1)
            rl = "\n".join(f"  {Role(r).emoji} {r}: {c}" for r, c in sorted(stats["role_history"].items(), key=lambda x: -x[1]))
            msg = (f"📊 <b>{stats['username']}</b>\n🎮 Games: {stats['games_played']}\n"
                   f"🏆 Wins: {stats['wins']} ({wr}%)\n💀 Losses: {stats['losses']}\n"
                   f"🛡️ Survived: {stats['survived_games']}\n\n<b>Roles:</b>\n{rl or '  None yet'}")
            try:
                await ctx.application.bot.send_message(chat_id=user.id, text=msg, parse_mode=ParseMode.HTML)
                await query.answer("Stats sent to DM!")
            except Forbidden:
                await query.answer(msg[:200], show_alert=True)

        elif data == "global_stats":
            g = await db_instance.get_global_stats()
            if not g or not g.get("total_games"):
                await query.answer("No global stats yet!", show_alert=True); return
            msg = (f"🌍 Global Stats\nTotal: {int(g['total_games'] or 0)} games\n"
                   f"🏙️ Town: {int(g['town_wins'] or 0)}\n🔫 Mafia: {int(g['mafia_wins'] or 0)}\n"
                   f"🔪 Maniac: {int(g['maniac_wins'] or 0)}\nAvg players: {round(g['avg_players'] or 0, 1)}")
            await query.answer(msg[:200], show_alert=True)

        elif data == "howto":
            await query.answer(
                "Night=kills, Day=discuss+vote.\nTown wins: eliminate all Mafia+Maniac.\n"
                "Mafia wins: equal Town count.\nManiac wins: last survivor.", show_alert=True
            )

        elif data == "new_game":
            await query.answer("Use /newgame in a group chat!")

    except Exception as e:
        logger.exception("Callback error (data=%s): %s", data, e)
        try:
            await query.answer("⚠️ Error occurred. Try again.", show_alert=True)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════
# ERROR HANDLER
# ══════════════════════════════════════════════════════════════════

async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    logger.error("Unhandled error: %s", ctx.error, exc_info=ctx.error)
    if isinstance(ctx.error, (Forbidden, BadRequest)):
        return
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("⚠️ An unexpected error occurred.")
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════
# STARTUP / SHUTDOWN
# ══════════════════════════════════════════════════════════════════

async def post_init(app: Application):
    await db_instance.connect()
    await app.bot.set_my_commands([
        BotCommand("newgame",   "Create a new game lobby"),
        BotCommand("join",      "Join the current lobby"),
        BotCommand("leave",     "Leave the lobby"),
        BotCommand("startgame", "Start the game (host only)"),
        BotCommand("endgame",   "Force-end current game"),
        BotCommand("players",   "Show player list"),
        BotCommand("myrole",    "Show your role"),
        BotCommand("stats",     "View statistics"),
        BotCommand("help",      "Show help"),
    ])
    logger.info("🎭 Bot ready!")

    async def _cleanup():
        while True:
            await asyncio.sleep(300)
            n = registry.cleanup_ended()
            if n:
                logger.info("Cleaned up %d ended game(s)", n)

    asyncio.create_task(_cleanup())


async def post_shutdown(app: Application):
    await db_instance.close()
    logger.info("Database closed.")


# ══════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════

def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        logger.error("BOT_TOKEN not set!")
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
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_error_handler(error_handler)

    logger.info("🎭 ULTRA PRO MAFIA BOT starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
