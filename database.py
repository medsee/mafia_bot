"""database.py — Async SQLite with ELO, achievements, streaks, game replay."""
from __future__ import annotations
import json
import logging
import math
from datetime import datetime
from typing import Optional
import aiosqlite
from config import cfg

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    game_id      TEXT PRIMARY KEY,
    chat_id      INTEGER NOT NULL,
    started_at   TEXT,
    ended_at     TEXT,
    winner       TEXT,
    player_count INTEGER DEFAULT 0,
    rounds       INTEGER DEFAULT 0,
    player_data  TEXT DEFAULT '[]',
    game_log     TEXT DEFAULT '[]',
    lang         TEXT DEFAULT 'uz'
);

CREATE TABLE IF NOT EXISTS player_stats (
    user_id        INTEGER PRIMARY KEY,
    username       TEXT DEFAULT '',
    lang           TEXT DEFAULT 'uz',
    games_played   INTEGER DEFAULT 0,
    wins           INTEGER DEFAULT 0,
    losses         INTEGER DEFAULT 0,
    survived_games INTEGER DEFAULT 0,
    elo            INTEGER DEFAULT 1000,
    win_streak     INTEGER DEFAULT 0,
    best_streak    INTEGER DEFAULT 0,
    role_history   TEXT DEFAULT '{}',
    win_by_role    TEXT DEFAULT '{}',
    achievements   TEXT DEFAULT '[]',
    last_will      TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id INTEGER PRIMARY KEY,
    lang    TEXT DEFAULT 'uz'
);

CREATE INDEX IF NOT EXISTS idx_games_chat   ON games(chat_id);
CREATE INDEX IF NOT EXISTS idx_games_winner ON games(winner);
CREATE INDEX IF NOT EXISTS idx_stats_elo    ON player_stats(elo DESC);
"""


class Database:
    def __init__(self, path: str = cfg.DB_PATH):
        self.path = path
        self._db: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.commit()
        logger.info("DB connected: %s", self.path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    # ── Games ─────────────────────────────────────────────────────

    async def start_game(self, game_id: str, chat_id: int, player_count: int, lang: str = "uz") -> None:
        await self._db.execute(
            "INSERT OR REPLACE INTO games(game_id,chat_id,started_at,player_count,lang) VALUES(?,?,?,?,?)",
            (game_id, chat_id, datetime.utcnow().isoformat(), player_count, lang)
        )
        await self._db.commit()

    async def end_game(self, game_id: str, winner: str, rounds: int, player_data: list, game_log: list) -> None:
        await self._db.execute(
            "UPDATE games SET ended_at=?,winner=?,rounds=?,player_data=?,game_log=? WHERE game_id=?",
            (datetime.utcnow().isoformat(), winner, rounds,
             json.dumps(player_data), json.dumps(game_log), game_id)
        )
        await self._db.commit()

    async def increment_round(self, game_id: str) -> None:
        await self._db.execute("UPDATE games SET rounds=rounds+1 WHERE game_id=?", (game_id,))
        await self._db.commit()

    async def get_game_log(self, game_id: str) -> Optional[dict]:
        async with self._db.execute("SELECT * FROM games WHERE game_id=?", (game_id,)) as c:
            row = await c.fetchone()
        if not row:
            return None
        d = dict(row)
        d["player_data"] = json.loads(d["player_data"])
        d["game_log"]    = json.loads(d["game_log"])
        return d

    async def get_recent_games(self, chat_id: int, limit: int = 5) -> list:
        async with self._db.execute(
            "SELECT game_id,started_at,ended_at,winner,player_count,rounds FROM games "
            "WHERE chat_id=? AND winner IS NOT NULL ORDER BY ended_at DESC LIMIT ?",
            (chat_id, limit)
        ) as c:
            rows = await c.fetchall()
        return [dict(r) for r in rows]

    # ── Players ───────────────────────────────────────────────────

    async def ensure_player(self, user_id: int, username: str, lang: str = "uz") -> None:
        await self._db.execute(
            "INSERT OR IGNORE INTO player_stats(user_id,username,lang) VALUES(?,?,?)",
            (user_id, username, lang)
        )
        await self._db.execute(
            "UPDATE player_stats SET username=? WHERE user_id=?", (username, user_id)
        )
        await self._db.commit()

    async def record_game_result(
        self, user_id: int, username: str, role: str,
        won: bool, survived: bool, opponent_elo: int = 1000
    ) -> dict:
        """Record result and update ELO. Returns dict with new_elo, gained_elo, new_achievements."""
        await self.ensure_player(user_id, username)

        async with self._db.execute(
            "SELECT * FROM player_stats WHERE user_id=?", (user_id,)
        ) as c:
            row = await c.fetchone()

        stats = dict(row)
        rh = json.loads(stats["role_history"])
        wb = json.loads(stats["win_by_role"])
        achievements = json.loads(stats["achievements"])

        rh[role] = rh.get(role, 0) + 1
        if won:
            wb[role] = wb.get(role, 0) + 1

        # ELO calculation
        old_elo = stats["elo"]
        expected = 1 / (1 + math.pow(10, (opponent_elo - old_elo) / 400))
        actual = 1.0 if won else 0.0
        elo_change = round(cfg.ELO_K * (actual - expected))
        new_elo = max(100, old_elo + elo_change)

        # Streak
        new_streak = (stats["win_streak"] + 1) if won else 0
        best_streak = max(stats["best_streak"], new_streak)

        new_games  = stats["games_played"] + 1
        new_wins   = stats["wins"] + (1 if won else 0)
        new_losses = stats["losses"] + (0 if won else 1)
        new_surv   = stats["survived_games"] + (1 if survived else 0)

        # Check achievements
        new_achs = []
        def _check(key, condition):
            if condition and key not in achievements:
                achievements.append(key)
                new_achs.append(key)

        _check("first_blood",   new_wins == 1)
        _check("veteran",       new_games >= 10)
        _check("century",       new_games >= 100)
        _check("survivor",      new_surv >= 5)
        _check("mafia_boss",    wb.get("mafia", 0) >= 5)
        _check("streak_3",      new_streak >= 3)
        _check("streak_5",      new_streak >= 5)
        _check("jester_win",    won and role == "jester")
        _check("lone_wolf",     won and role in ("maniac", "serial_killer"))

        await self._db.execute(
            """UPDATE player_stats SET
               games_played=?, wins=?, losses=?, survived_games=?,
               elo=?, win_streak=?, best_streak=?,
               role_history=?, win_by_role=?, achievements=?
               WHERE user_id=?""",
            (new_games, new_wins, new_losses, new_surv,
             new_elo, new_streak, best_streak,
             json.dumps(rh), json.dumps(wb), json.dumps(achievements),
             user_id)
        )
        await self._db.commit()
        return {"new_elo": new_elo, "elo_change": elo_change, "new_achievements": new_achs}

    async def get_player_stats(self, user_id: int) -> Optional[dict]:
        async with self._db.execute(
            "SELECT * FROM player_stats WHERE user_id=?", (user_id,)
        ) as c:
            row = await c.fetchone()
        if not row:
            return None
        d = dict(row)
        d["role_history"] = json.loads(d["role_history"])
        d["win_by_role"]  = json.loads(d["win_by_role"])
        d["achievements"] = json.loads(d["achievements"])
        return d

    async def get_leaderboard(self, limit: int = 10) -> list:
        async with self._db.execute(
            """SELECT username, games_played, wins, elo, best_streak,
               ROUND(CAST(wins AS FLOAT)/MAX(games_played,1)*100,1) as win_rate
               FROM player_stats WHERE games_played>0
               ORDER BY elo DESC LIMIT ?""", (limit,)
        ) as c:
            rows = await c.fetchall()
        return [dict(r) for r in rows]

    async def get_global_stats(self) -> dict:
        async with self._db.execute(
            """SELECT COUNT(*) as total_games,
               SUM(CASE WHEN winner='town'   THEN 1 ELSE 0 END) as town_wins,
               SUM(CASE WHEN winner='mafia'  THEN 1 ELSE 0 END) as mafia_wins,
               SUM(CASE WHEN winner='solo'   THEN 1 ELSE 0 END) as solo_wins,
               SUM(CASE WHEN winner='jester' THEN 1 ELSE 0 END) as jester_wins,
               AVG(player_count) as avg_players, AVG(rounds) as avg_rounds
               FROM games WHERE winner IS NOT NULL"""
        ) as c:
            row = await c.fetchone()
        return dict(row) if row else {}

    # ── User settings ─────────────────────────────────────────────

    async def get_user_lang(self, user_id: int) -> str:
        async with self._db.execute(
            "SELECT lang FROM user_settings WHERE user_id=?", (user_id,)
        ) as c:
            row = await c.fetchone()
        if row:
            return row["lang"]
        async with self._db.execute(
            "SELECT lang FROM player_stats WHERE user_id=?", (user_id,)
        ) as c:
            row = await c.fetchone()
        return row["lang"] if row else "uz"

    async def set_user_lang(self, user_id: int, lang: str) -> None:
        await self._db.execute(
            "INSERT OR REPLACE INTO user_settings(user_id,lang) VALUES(?,?)", (user_id, lang)
        )
        await self._db.execute(
            "UPDATE player_stats SET lang=? WHERE user_id=?", (lang, user_id)
        )
        await self._db.commit()

    async def save_last_will(self, user_id: int, will: str) -> None:
        await self._db.execute(
            "UPDATE player_stats SET last_will=? WHERE user_id=?", (will[:200], user_id)
        )
        await self._db.commit()

    async def get_last_will(self, user_id: int) -> str:
        async with self._db.execute(
            "SELECT last_will FROM player_stats WHERE user_id=?", (user_id,)
        ) as c:
            row = await c.fetchone()
        return row["last_will"] if row else ""
