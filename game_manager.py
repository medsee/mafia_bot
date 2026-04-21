"""
game_manager.py — State-machine core for a single Mafia game instance.

Manages: lobby → night → day → voting → [night...] → ended

One GameManager per chat. GameRegistry holds them all.
"""
from __future__ import annotations

import asyncio
import logging
import random
import uuid
from datetime import datetime
from typing import Optional, Callable, Awaitable

from ai_engine import AIEngine
from models import (
    NightResult, Phase, Player, Role, NightAction,
    assign_roles
)
from role_engine import RoleEngine

logger = logging.getLogger(__name__)

# Timing constants (seconds)
LOBBY_TIMEOUT        = 120   # auto-start if enough players after this
NIGHT_ACTION_TIMEOUT = 45    # how long night phase lasts
DAY_SPEECH_TIMEOUT   = 60    # how long day discussion lasts
VOTING_TIMEOUT       = 40    # how long voting lasts

AI_NAMES = [
    "Alice", "Bob", "Carlos", "Diana", "Erik",
    "Fatima", "George", "Hana", "Ivan", "Julia"
]


# ──────────────────────────────────────────────────────────────────────────────
# Vote tracker (anti-spam)
# ──────────────────────────────────────────────────────────────────────────────

class VoteTracker:
    """Allows each player to cast exactly one vote per voting phase.
    Supports vote changing (last vote wins)."""

    def __init__(self) -> None:
        self._votes: dict[int, int] = {}  # voter_id -> target_id

    def cast(self, voter_id: int, target_id: int) -> bool:
        """Returns True if this is a new vote, False if changed."""
        is_new = voter_id not in self._votes
        self._votes[voter_id] = target_id
        return is_new

    def has_voted(self, voter_id: int) -> bool:
        return voter_id in self._votes

    def get_previous(self, voter_id: int) -> Optional[int]:
        return self._votes.get(voter_id)

    def tally(self) -> dict[int, int]:
        """Returns {target_id: vote_count}."""
        tally: dict[int, int] = {}
        for target in self._votes.values():
            tally[target] = tally.get(target, 0) + 1
        return tally

    def leading(self) -> Optional[int]:
        t = self.tally()
        if not t:
            return None
        return max(t, key=lambda k: t[k])

    def voter_count(self) -> int:
        return len(self._votes)

    def all_votes(self) -> dict[int, int]:
        return dict(self._votes)

    def reset(self) -> None:
        self._votes.clear()


# ──────────────────────────────────────────────────────────────────────────────
# GameManager
# ──────────────────────────────────────────────────────────────────────────────

# Callback type: async fn(chat_id, text, **kwargs)
SendFn = Callable[..., Awaitable[None]]


class GameManager:
    def __init__(self, chat_id: int, send_fn: SendFn, db) -> None:
        self.chat_id      = chat_id
        self.game_id      = str(uuid.uuid4())[:8]
        self.send         = send_fn   # injected messaging function
        self.db           = db

        self.phase        = Phase.LOBBY
        self.players:     dict[int, Player] = {}
        self.round        = 0
        self.started_at   = datetime.utcnow()

        self.vote_tracker = VoteTracker()
        self.night_result: Optional[NightResult] = None

        # Accumulated knowledge for AI
        self._known_roles:   dict[int, Role] = {}   # detective-revealed
        self._vote_history:  dict[int, list[int]] = {}  # user_id -> list of vote targets
        self._all_kills:     list[int] = []          # accumulates all night kills

        # Phase timers
        self._phase_task:    Optional[asyncio.Task] = None

        # Lobby host (first joiner)
        self.host_id: Optional[int] = None

    # ──────────────────────────────────────────
    # Lobby management
    # ──────────────────────────────────────────

    def join(self, user_id: int, name: str) -> bool:
        """Returns True if joined, False if already in game."""
        if user_id in self.players:
            return False
        if len(self.players) >= 10:
            return False
        if self.phase != Phase.LOBBY:
            return False
        self.players[user_id] = Player(user_id=user_id, name=name)
        if self.host_id is None:
            self.host_id = user_id
        return True

    def leave(self, user_id: int) -> bool:
        if user_id not in self.players or self.phase != Phase.LOBBY:
            return False
        del self.players[user_id]
        if self.host_id == user_id and self.players:
            self.host_id = next(iter(self.players))
        return True

    def add_ai_player(self) -> bool:
        existing_ai = sum(1 for p in self.players.values() if p.is_ai)
        if existing_ai >= 6 or len(self.players) >= 10:
            return False
        ai_name = next(
            (n for n in AI_NAMES
             if n not in {p.name for p in self.players.values()}),
            f"Bot{random.randint(100,999)}"
        )
        ai_id = -(1000 + existing_ai)  # negative IDs for AI
        p = Player(user_id=ai_id, name=ai_name, is_ai=True)
        self.players[ai_id] = p
        return True

    def player_count(self) -> int:
        return len(self.players)

    def alive_players(self) -> list[Player]:
        return [p for p in self.players.values() if p.is_alive]

    def alive_human_players(self) -> list[Player]:
        return [p for p in self.players.values() if p.is_alive and not p.is_ai]

    # ──────────────────────────────────────────
    # Game start
    # ──────────────────────────────────────────

    async def start_game(self) -> None:
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
            f"👥 {len(self.players)} players — roles have been assigned.\n"
            f"Check your private messages for your role!\n\n"
            f"<i>🌙 Night falls... The city sleeps.</i>"
        )

        await self._begin_night()

    # ──────────────────────────────────────────
    # Night phase
    # ──────────────────────────────────────────

    async def _begin_night(self) -> None:
        self.phase = Phase.NIGHT
        self.round += 1
        await self.db.increment_round(self.game_id)

        # Reset night state for all alive players
        for p in self.players.values():
            p.reset_night_state()

        await self.send(
            self.chat_id,
            f"🌙 <b>Night {self.round}</b>\n"
            "The city falls silent. Special roles, check your DMs."
        )

        # Process AI night actions
        await self._process_ai_night_actions()

        # Start timeout for human players
        self._cancel_phase_task()
        self._phase_task = asyncio.create_task(self._night_timeout())

    async def _night_timeout(self) -> None:
        await asyncio.sleep(NIGHT_ACTION_TIMEOUT)
        await self._resolve_night()

    async def _process_ai_night_actions(self) -> None:
        """AI players act immediately (with slight artificial delay)."""
        for uid, player in self.players.items():
            if not player.is_alive or not player.is_ai:
                continue
            if not player.role.has_night_action:
                continue

            await asyncio.sleep(random.uniform(1.5, 4.0))  # realistic delay

            target = AIEngine.choose_night_target(
                actor=player,
                all_players=self.players,
                known_roles=self._known_roles,
                vote_history=self._vote_history,
            )
            if target is not None:
                player.night_target = target
                player.has_acted = True
                logger.debug("AI %s (%s) targets %s", player.name, player.role, target)

    async def record_night_action(self, actor_id: int, target_id: int) -> str:
        """Called when a human player DMs their night action. Returns feedback."""
        if self.phase != Phase.NIGHT:
            return "❌ It's not night phase."
        if actor_id not in self.players:
            return "❌ You are not in this game."

        actor = self.players[actor_id]
        if not actor.is_alive:
            return "❌ You are dead."
        if not actor.role.has_night_action:
            return "❌ Your role has no night action."
        if actor.role == Role.SNIPER and actor.sniper_used:
            return "❌ You've already used your sniper shot."
        if target_id not in self.players:
            return "❌ Invalid target."
        if target_id == actor_id:
            if actor.role != Role.DOCTOR:
                return "❌ You can't target yourself."
        target = self.players[target_id]
        if not target.is_alive:
            return "❌ That player is already dead."

        actor.night_target = target_id
        actor.has_acted = True

        # Auto-resolve if all role-players have acted
        if self._all_active_roles_acted():
            self._cancel_phase_task()
            asyncio.create_task(self._resolve_night())

        return f"✅ Action recorded on <b>{target.name}</b>."

    def _all_active_roles_acted(self) -> bool:
        for p in self.players.values():
            if p.is_alive and p.role.has_night_action and not p.has_acted:
                if p.role == Role.SNIPER and p.sniper_used:
                    continue  # sniper already spent shot
                return False
        return True

    async def _resolve_night(self) -> None:
        if self.phase != Phase.NIGHT:
            return

        result = RoleEngine.resolve_night(self.players)
        self.night_result = result
        self._all_kills.extend(result.killed + result.sniped)

        # Update known roles from detective work
        self._known_roles.update(result.inspected)

        # Announce night results
        lines = ["☀️ <b>Dawn breaks...</b>\n"]

        if not result.killed and not result.sniped:
            lines.append("🕊️ The night was peaceful — nobody died!")
        else:
            for uid in result.killed:
                p = self.players[uid]
                lines.append(f"💀 <b>{p.name}</b> was found dead. They were the <b>{p.role.display_name} {p.role.emoji}</b>")
            for uid in result.sniped:
                p = self.players[uid]
                lines.append(f"🎯 <b>{p.name}</b> was eliminated by a Sniper. They were the <b>{p.role.display_name} {p.role.emoji}</b>")

        lines.extend(result.messages)

        await self.send(self.chat_id, "\n".join(lines))

        # Notify detectives of their findings (private)
        for t_id, role in result.inspected.items():
            detective = next(
                (p for p in self.players.values()
                 if p.is_alive and p.role == Role.DETECTIVE and p.night_target == t_id),
                None
            )
            if detective and not detective.is_ai:
                await self.send(
                    detective.user_id,
                    f"🔍 Investigation complete: <b>{self.players[t_id].name}</b> "
                    f"is a <b>{role.display_name} {role.emoji}</b>"
                )

        # Check win condition
        winner = RoleEngine.check_win(self.players)
        if winner:
            await self._end_game(winner)
            return

        await self._begin_day()

    # ──────────────────────────────────────────
    # Day phase
    # ──────────────────────────────────────────

    async def _begin_day(self) -> None:
        self.phase = Phase.DAY

        alive_list = "\n".join(
            f"  {'💀' if not p.is_alive else '✅'} {p.mention}"
            for p in self.players.values()
        )
        await self.send(
            self.chat_id,
            f"☀️ <b>Day {self.round}</b> — Discussion time!\n\n"
            f"<b>Alive players ({len(self.alive_players())}):</b>\n{alive_list}\n\n"
            f"🗣️ Discuss for {DAY_SPEECH_TIMEOUT}s, then voting begins!"
        )

        self._cancel_phase_task()
        self._phase_task = asyncio.create_task(self._day_timeout())

    async def _day_timeout(self) -> None:
        await asyncio.sleep(DAY_SPEECH_TIMEOUT)
        await self._begin_voting()

    async def skip_to_vote(self, user_id: int) -> str:
        """Host can skip day phase."""
        if user_id != self.host_id:
            return "❌ Only the host can skip."
        if self.phase != Phase.DAY:
            return "❌ Not in day phase."
        self._cancel_phase_task()
        asyncio.create_task(self._begin_voting())
        return "⏩ Skipping to vote!"

    # ──────────────────────────────────────────
    # Voting phase
    # ──────────────────────────────────────────

    async def _begin_voting(self) -> None:
        self.phase = Phase.VOTING
        self.vote_tracker.reset()

        alive = self.alive_players()
        await self.send(
            self.chat_id,
            f"🗳️ <b>Voting phase!</b>\n"
            f"Vote to eliminate a player.\n"
            f"⏱️ You have {VOTING_TIMEOUT} seconds.",
            vote_targets=[(p.user_id, p.name) for p in alive]
        )

        # AI votes
        await self._process_ai_votes()

        self._cancel_phase_task()
        self._phase_task = asyncio.create_task(self._voting_timeout())

    async def _process_ai_votes(self) -> None:
        for uid, player in self.players.items():
            if not player.is_alive or not player.is_ai:
                continue
            await asyncio.sleep(random.uniform(1.0, 3.0))
            target = AIEngine.choose_vote_target(
                voter=player,
                all_players=self.players,
                known_roles=self._known_roles,
                vote_history=self._vote_history,
                night_kills=self._all_kills,
            )
            if target and target in self.players:
                self._cast_vote_internal(uid, target)

    def cast_vote(self, voter_id: int, target_id: int) -> str:
        """Human player casts a vote. Returns feedback string."""
        if self.phase != Phase.VOTING:
            return "❌ Voting is not active."
        if voter_id not in self.players:
            return "❌ You are not in this game."
        voter = self.players[voter_id]
        if not voter.is_alive:
            return "❌ Dead players cannot vote."
        if target_id not in self.players:
            return "❌ Invalid target."
        if target_id == voter_id:
            return "❌ You cannot vote for yourself."
        if not self.players[target_id].is_alive:
            return "❌ That player is already dead."

        prev = self.vote_tracker.get_previous(voter_id)
        self._cast_vote_internal(voter_id, target_id)

        if prev and prev != target_id:
            return (f"🔄 Changed vote: <b>{self.players[prev].name}</b> → "
                    f"<b>{self.players[target_id].name}</b>")
        return f"✅ Voted for <b>{self.players[target_id].name}</b>"

    def _cast_vote_internal(self, voter_id: int, target_id: int) -> None:
        self.vote_tracker.cast(voter_id, target_id)
        # Record vote history for AI knowledge
        self._vote_history.setdefault(voter_id, []).append(target_id)

    async def _voting_timeout(self) -> None:
        await asyncio.sleep(VOTING_TIMEOUT)
        await self._resolve_vote()

    async def _resolve_vote(self) -> None:
        if self.phase != Phase.VOTING:
            return

        tally = self.vote_tracker.tally()
        if not tally:
            await self.send(self.chat_id, "🤷 No votes were cast. Nobody is eliminated.")
            await self._check_and_continue()
            return

        # Build result string
        tally_lines = []
        for uid, count in sorted(tally.items(), key=lambda x: -x[1]):
            tally_lines.append(f"  {self.players[uid].name}: {count} vote(s)")

        max_votes = max(tally.values())
        leaders = [uid for uid, v in tally.items() if v == max_votes]

        if len(leaders) > 1:
            # Tie — no elimination
            tied_names = ", ".join(self.players[uid].name for uid in leaders)
            await self.send(
                self.chat_id,
                f"📊 <b>Vote results:</b>\n" + "\n".join(tally_lines) +
                f"\n\n⚖️ <b>Tie between {tied_names}!</b> Nobody is eliminated."
            )
        else:
            eliminated_id = leaders[0]
            eliminated = self.players[eliminated_id]
            eliminated.is_alive = False

            await self.send(
                self.chat_id,
                f"📊 <b>Vote results:</b>\n" + "\n".join(tally_lines) +
                f"\n\n🪓 The town voted to eliminate <b>{eliminated.name}</b>.\n"
                f"They were the <b>{eliminated.role.display_name} {eliminated.role.emoji}</b>!"
            )

        await self._check_and_continue()

    async def _check_and_continue(self) -> None:
        winner = RoleEngine.check_win(self.players)
        if winner:
            await self._end_game(winner)
        else:
            await self._begin_night()

    # ──────────────────────────────────────────
    # End game
    # ──────────────────────────────────────────

    async def _end_game(self, winner: str) -> None:
        self.phase = Phase.ENDED
        self._cancel_phase_task()

        winner_emoji = {"town": "🏙️", "mafia": "🔫", "maniac": "🔪", "draw": "⚖️"}.get(winner, "🎭")
        winner_text  = {"town": "The Town wins!", "mafia": "The Mafia wins!",
                        "maniac": "The Maniac wins!", "draw": "It's a draw!"}.get(winner, winner)

        # Build final role reveal
        reveal_lines = []
        for p in self.players.values():
            status = "✅ Survived" if p.is_alive else "💀 Eliminated"
            reveal_lines.append(f"  {p.role.emoji} <b>{p.name}</b> — {p.role.display_name} [{status}]")

        await self.send(
            self.chat_id,
            f"{winner_emoji} <b>GAME OVER!</b>\n"
            f"<b>{winner_text}</b>\n\n"
            f"<b>Final roles:</b>\n" + "\n".join(reveal_lines) +
            f"\n\n📊 Game lasted {self.round} round(s).\n"
            f"Use /stats to see updated statistics!"
        )

        # Persist results
        player_data = [
            {
                "user_id": p.user_id,
                "name": p.name,
                "role": p.role.value,
                "survived": p.is_alive,
                "is_ai": p.is_ai,
            }
            for p in self.players.values()
        ]
        await self.db.end_game(self.game_id, winner, self.round, player_data)

        # Record individual stats
        for p in self.players.values():
            if p.is_ai:
                continue
            team = p.role.team
            won = (
                (winner == "town" and team == "town") or
                (winner == "mafia" and team == "mafia") or
                (winner == "maniac" and team == "maniac")
            )
            await self.db.record_game_result(
                user_id=p.user_id,
                username=p.name,
                role=p.role.value,
                won=won,
                survived=p.is_alive,
            )

    # ──────────────────────────────────────────
    # Utility
    # ──────────────────────────────────────────

    def _cancel_phase_task(self) -> None:
        if self._phase_task and not self._phase_task.done():
            self._phase_task.cancel()
        self._phase_task = None

    def is_ended(self) -> bool:
        return self.phase == Phase.ENDED

    def get_player(self, user_id: int) -> Optional[Player]:
        return self.players.get(user_id)

    def role_info(self, user_id: int) -> str:
        p = self.players.get(user_id)
        if not p:
            return "You are not in this game."
        lines = [f"{p.role.emoji} <b>You are the {p.role.display_name}!</b>\n"]

        role_desc = {
            Role.CIVILIAN:
                "You have no special ability. Use your wits to identify and vote out the Mafia!",
            Role.MAFIA:
                "Each night, coordinate with fellow Mafia to eliminate a town member.",
            Role.DOCTOR:
                "Each night, protect one player from death (can self-heal once in a row).",
            Role.DETECTIVE:
                "Each night, investigate one player to learn their true role.",
            Role.SNIPER:
                "You have ONE bullet. Use it wisely to eliminate a confirmed threat.",
            Role.MANIAC:
                "You win ALONE. Kill at night. Survive until you're the last one standing.",
        }
        lines.append(role_desc.get(p.role, ""))

        # Show mafia teammates
        if p.role == Role.MAFIA:
            teammates = [
                q.name for qid, q in self.players.items()
                if q.role == Role.MAFIA and qid != user_id
            ]
            if teammates:
                lines.append(f"\n🤝 Your Mafia partners: <b>{', '.join(teammates)}</b>")

        return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# GameRegistry — manages one GameManager per chat
# ──────────────────────────────────────────────────────────────────────────────

class GameRegistry:
    def __init__(self) -> None:
        self._games: dict[int, GameManager] = {}

    def get(self, chat_id: int) -> Optional[GameManager]:
        return self._games.get(chat_id)

    def create(self, chat_id: int, send_fn: SendFn, db) -> GameManager:
        gm = GameManager(chat_id=chat_id, send_fn=send_fn, db=db)
        self._games[chat_id] = gm
        return gm

    def remove(self, chat_id: int) -> None:
        self._games.pop(chat_id, None)

    def cleanup_ended(self) -> int:
        ended = [cid for cid, gm in self._games.items() if gm.is_ended()]
        for cid in ended:
            del self._games[cid]
        return len(ended)

    def active_count(self) -> int:
        return len(self._games)
