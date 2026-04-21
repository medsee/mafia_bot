"""
database.py — Async SQLite layer for persistent game history and player statistics.
Uses aiosqlite. Schema is auto-created on first run.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)

DB_PATH = "mafia_bot.db"


# ──────────────────────────────────────────────────────────────────────────────
# Schema
# ──────────────────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    game_id         TEXT PRIMARY KEY,
    chat_id         INTEGER NOT NULL,
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    winner          TEXT,           -- 'town' | 'mafia' | 'maniac' | 'draw'
    player_count    INTEGER,
    rounds          INTEGER DEFAULT 0,
    player_data     TEXT            -- JSON snapshot of final players
);

CREATE TABLE IF NOT EXISTS player_stats (
    user_id         INTEGER PRIMARY KEY,
    username        TEXT,
    games_played    INTEGER DEFAULT 0,
    wins            INTEGER DEFAULT 0,
    losses          INTEGER DEFAULT 0,
    kills           INTEGER DEFAULT 0,    -- times eliminated another player
    survived_games  INTEGER DEFAULT 0,
    role_history    TEXT DEFAULT '{}',    -- JSON: {role: count}
    win_by_role     TEXT DEFAULT '{}'     -- JSON: {role: wins}
);

CREATE INDEX IF NOT EXISTS idx_games_chat ON games(chat_id);
CREATE INDEX IF NOT EXISTS idx_games_winner ON games(winner);
"""


# ──────────────────────────────────────────────────────────────────────────────
# Database manager
# ──────────────────────────────────────────────────────────────────────────────

class Database:
    def __init__(self, path: str = DB_PATH):
        self.path = path
        self._db: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.commit()
        logger.info("Database connected: %s", self.path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    # ── Game records ──────────────────────────────────────────────────────────

    async def start_game(self, game_id: str, chat_id: int, player_count: int) -> None:
        await self._db.execute(
            "INSERT OR REPLACE INTO games(game_id, chat_id, started_at, player_count) VALUES (?,?,?,?)",
            (game_id, chat_id, datetime.utcnow().isoformat(), player_count)
        )
        await self._db.commit()

    async def end_game(
        self, game_id: str, winner: str,
        rounds: int, player_data: list[dict]
    ) -> None:
        await self._db.execute(
            """UPDATE games SET ended_at=?, winner=?, rounds=?, player_data=?
               WHERE game_id=?""",
            (datetime.utcnow().isoformat(), winner, rounds,
             json.dumps(player_data), game_id)
        )
        await self._db.commit()

    async def increment_round(self, game_id: str) -> None:
        await self._db.execute(
            "UPDATE games SET rounds = rounds + 1 WHERE game_id = ?", (game_id,)
        )
        await self._db.commit()

    # ── Player stats ──────────────────────────────────────────────────────────

    async def ensure_player(self, user_id: int, username: str) -> None:
        await self._db.execute(
            """INSERT OR IGNORE INTO player_stats(user_id, username)
               VALUES (?, ?)""",
            (user_id, username)
        )
        await self._db.execute(
            "UPDATE player_stats SET username=? WHERE user_id=?",
            (username, user_id)
        )
        await self._db.commit()

    async def record_game_result(
        self, user_id: int, username: str,
        role: str, won: bool, survived: bool, kills: int = 0
    ) -> None:
        await self.ensure_player(user_id, username)

        # Fetch current JSON counters
        async with self._db.execute(
            "SELECT role_history, win_by_role FROM player_stats WHERE user_id=?",
            (user_id,)
        ) as cur:
            row = await cur.fetchone()

        role_history: dict = json.loads(row["role_history"]) if row else {}
        win_by_role: dict = json.loads(row["win_by_role"]) if row else {}

        role_history[role] = role_history.get(role, 0) + 1
        if won:
            win_by_role[role] = win_by_role.get(role, 0) + 1

        await self._db.execute(
            """UPDATE player_stats SET
               games_played = games_played + 1,
               wins         = wins + ?,
               losses       = losses + ?,
               kills        = kills + ?,
               survived_games = survived_games + ?,
               role_history = ?,
               win_by_role  = ?
               WHERE user_id=?""",
            (1 if won else 0, 0 if won else 1, kills,
             1 if survived else 0,
             json.dumps(role_history), json.dumps(win_by_role),
             user_id)
        )
        await self._db.commit()

    # ── Queries ───────────────────────────────────────────────────────────────

    async def get_player_stats(self, user_id: int) -> Optional[dict]:
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

    async def get_leaderboard(self, limit: int = 10) -> list[dict]:
        async with self._db.execute(
            """SELECT username, games_played, wins,
               ROUND(CAST(wins AS FLOAT) / MAX(games_played,1) * 100, 1) as win_rate
               FROM player_stats
               WHERE games_played > 0
               ORDER BY wins DESC, win_rate DESC
               LIMIT ?""",
            (limit,)
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_chat_history(self, chat_id: int, limit: int = 5) -> list[dict]:
        async with self._db.execute(
            """SELECT game_id, started_at, ended_at, winner, player_count, rounds
               FROM games WHERE chat_id=? AND winner IS NOT NULL
               ORDER BY ended_at DESC LIMIT ?""",
            (chat_id, limit)
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_global_stats(self) -> dict:
        async with self._db.execute(
            """SELECT COUNT(*) as total_games,
               SUM(CASE WHEN winner='town'   THEN 1 ELSE 0 END) as town_wins,
               SUM(CASE WHEN winner='mafia'  THEN 1 ELSE 0 END) as mafia_wins,
               SUM(CASE WHEN winner='maniac' THEN 1 ELSE 0 END) as maniac_wins,
               AVG(player_count) as avg_players,
               AVG(rounds) as avg_rounds
               FROM games WHERE winner IS NOT NULL"""
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else {}
