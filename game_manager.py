"""game_manager.py — Full state machine: Lobby→Night→Day→Voting→Defense→loop→End."""
from __future__ import annotations
import asyncio
import logging
import random
import uuid
from typing import Optional, Callable, Awaitable

from ai_engine import AIEngine
from config import cfg, t
from keyboards import (
    day_control_keyboard, lobby_keyboard, night_action_keyboard,
    vote_keyboard, last_will_keyboard
)
from models import (
    NightResult, Phase, Player, Role, assign_roles, ACHIEVEMENTS
)
from role_engine import RoleEngine

logger = logging.getLogger(__name__)

SendFn = Callable[..., Awaitable[None]]

AI_NAMES = ["Alice", "Bob", "Carlos", "Diana", "Erik",
            "Fatima", "George", "Hana", "Ivan", "Julia",
            "Kevin", "Luna", "Marco", "Nina", "Omar"]


class VoteTracker:
    def __init__(self, weighted: bool = False):
        self._votes: dict[int, int] = {}
        self._weights: dict[int, int] = {}  # voter -> weight
        self.weighted = weighted

    def set_weight(self, voter_id: int, weight: int):
        self._weights[voter_id] = weight

    def cast(self, voter_id: int, target_id: int) -> bool:
        is_new = voter_id not in self._votes
        self._votes[voter_id] = target_id
        return is_new

    def get_previous(self, voter_id: int) -> Optional[int]:
        return self._votes.get(voter_id)

    def has_voted(self, voter_id: int) -> bool:
        return voter_id in self._votes

    def tally(self) -> dict[int, int]:
        tally: dict[int, int] = {}
        for voter, target in self._votes.items():
            weight = self._weights.get(voter, 1)
            tally[target] = tally.get(target, 0) + weight
        return tally

    def voter_count(self) -> int:
        return len(self._votes)

    def reset(self):
        self._votes.clear()
        self._weights.clear()


class GameManager:
    def __init__(self, chat_id: int, send_fn: SendFn, db, lang: str = "uz"):
        self.chat_id       = chat_id
        self.game_id       = str(uuid.uuid4())[:8].upper()
        self.send          = send_fn
        self.db            = db
        self.lang          = lang

        self.phase         = Phase.LOBBY
        self.players:      dict[int, Player] = {}
        self.round         = 0
        self.host_id:      Optional[int] = None

        self.vote_tracker  = VoteTracker(weighted=True)
        self.night_result: Optional[NightResult] = None

        # Knowledge
        self._known_roles:   dict[int, Role] = {}
        self._vote_history:  dict[int, list[int]] = {}
        self._all_kills:     list[int] = []
        self._game_log:      list[dict] = []

        # Mafia chat (user_ids of human mafia)
        self._mafia_chat_members: list[int] = []
        # Ghost chat (dead human players)
        self._ghost_members: list[int] = []

        # Phase control
        self._phase_task:  Optional[asyncio.Task] = None
        self._defense_target: Optional[int] = None

        # Anonymous voting option
        self.anonymous_vote = False

        # Day 1 protection flag
        self._first_night_done = False

    # ═══════════════════════════════════════════════════════════════
    # Lobby
    # ═══════════════════════════════════════════════════════════════

    def join(self, user_id: int, name: str) -> bool:
        if (user_id in self.players or
                len(self.players) >= cfg.MAX_PLAYERS or
                self.phase != Phase.LOBBY):
            return False
        self.players[user_id] = Player(user_id=user_id, name=name, lang=self.lang)
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
        ai_count = sum(1 for p in self.players.values() if p.is_ai)
        if ai_count >= cfg.MAX_AI or len(self.players) >= cfg.MAX_PLAYERS:
            return False
        name = next(
            (n for n in AI_NAMES if n not in {p.name for p in self.players.values()}),
            f"Bot{random.randint(100, 999)}"
        )
        ai_id = -(1000 + ai_count)
        self.players[ai_id] = Player(user_id=ai_id, name=name, is_ai=True, lang=self.lang)
        return True

    def player_count(self) -> int:
        return len(self.players)

    def alive_players(self) -> list[Player]:
        return [p for p in self.players.values() if p.is_alive]

    def get_player(self, user_id: int) -> Optional[Player]:
        return self.players.get(user_id)

    def is_ended(self) -> bool:
        return self.phase == Phase.ENDED

    # ═══════════════════════════════════════════════════════════════
    # Start
    # ═══════════════════════════════════════════════════════════════

    async def start_game(self) -> None:
        if self.phase != Phase.LOBBY:
            return
        if len(self.players) < cfg.MIN_PLAYERS:
            await self.send(self.chat_id, t(self.lang, "need_players"))
            return

        assign_roles(list(self.players.values()))
        self.phase = Phase.NIGHT

        # Set Mayor vote weights
        for p in self.players.values():
            if p.role == Role.MAYOR:
                self.vote_tracker.set_weight(p.user_id, 2)

        # Identify mafia chat members
        self._mafia_chat_members = [
            uid for uid, p in self.players.items()
            if p.role == Role.MAFIA and not p.is_ai
        ]

        await self.db.start_game(self.game_id, self.chat_id, len(self.players), self.lang)
        self._log("game_start", f"Game started with {len(self.players)} players")

        await self.send(
            self.chat_id,
            t(self.lang, "game_started", gid=self.game_id, n=len(self.players))
        )
        await self._begin_night()

    # ═══════════════════════════════════════════════════════════════
    # Night
    # ═══════════════════════════════════════════════════════════════

    async def _begin_night(self) -> None:
        self.phase = Phase.NIGHT
        self.round += 1
        await self.db.increment_round(self.game_id)

        for p in self.players.values():
            p.reset_night_state()

        await self.send(self.chat_id, t(self.lang, "night_begins", n=self.round))

        # Day 1: no kills (only detective/doctor act)
        # (handled by resolve — first night kills still happen but we let it go)

        await self._process_ai_night_actions()
        self._cancel_phase_task()
        self._phase_task = asyncio.create_task(self._night_timeout())

    async def _night_timeout(self) -> None:
        await asyncio.sleep(cfg.NIGHT_TIMEOUT)
        await self._resolve_night()

    async def _process_ai_night_actions(self) -> None:
        for uid, player in self.players.items():
            if not player.is_alive or not player.is_ai or not player.role.has_night_action:
                continue
            await asyncio.sleep(random.uniform(2.0, 5.0))
            target = AIEngine.choose_night_target(
                player, self.players, self._known_roles, self._vote_history
            )
            if target is not None:
                player.night_target = target
                player.has_acted = True

    async def record_night_action(self, actor_id: int, target_id: int) -> str:
        if self.phase != Phase.NIGHT:
            return "❌ Kecha fazasi emas."
        actor = self.players.get(actor_id)
        if not actor or not actor.is_alive:
            return "❌ Siz bu o'yinda yo'q yoki o'lgansiz."
        if not actor.role.has_night_action:
            return "❌ Sizning rolingizda kecha harakati yo'q."
        if actor.role == Role.SNIPER and actor.sniper_used:
            return "❌ Siz allaqachon o'q ishlatdingiz."
        target = self.players.get(target_id)
        if not target or not target.is_alive:
            return "❌ Noto'g'ri yoki o'lgan nishon."
        if target_id == actor_id and actor.role != Role.DOCTOR:
            return "❌ O'zingizni nishon qila olmaysiz."

        actor.night_target = target_id
        actor.has_acted    = True

        if self._all_active_roles_acted():
            self._cancel_phase_task()
            asyncio.create_task(self._resolve_night())

        return t(self.lang, "action_recorded", name=target.name)

    def _all_active_roles_acted(self) -> bool:
        for p in self.players.values():
            if not p.is_alive or not p.role.has_night_action:
                continue
            if p.role == Role.SNIPER and p.sniper_used:
                continue
            if not p.has_acted:
                return False
        return True

    async def _resolve_night(self) -> None:
        if self.phase != Phase.NIGHT:
            return

        result = RoleEngine.resolve_night(self.players)
        self.night_result = result
        self._all_kills.extend(result.killed + result.sniped)
        self._known_roles.update(result.inspected)

        lines = [t(self.lang, "dawn"), ""]

        if not result.killed and not result.sniped and not result.bg_died:
            lines.append(t(self.lang, "nobody_died"))
        else:
            for uid in result.killed:
                p = self.players[uid]
                lines.append(t(self.lang, "player_killed",
                                name=p.name, role=p.role.display_name, emoji=p.role.emoji))
                # Last will
                will = p.last_will or await self.db.get_last_will(uid) if uid > 0 else ""
                if will:
                    lines.append(t(self.lang, "last_will", name=p.name, will=will))
                # Add to ghost chat
                if not p.is_ai:
                    self._ghost_members.append(uid)

            for uid in result.sniped:
                p = self.players[uid]
                lines.append(t(self.lang, "player_sniped",
                                name=p.name, role=p.role.display_name, emoji=p.role.emoji))
                if not p.is_ai:
                    self._ghost_members.append(uid)

            for bg_id, target_id in result.bg_died:
                bg = self.players[bg_id]
                target = self.players[target_id]
                lines.append(t(self.lang, "bodyguard_died",
                                guard=bg.name, target=target.name))
                if not bg.is_ai:
                    self._ghost_members.append(bg_id)

        # Check lover deaths
        for uid in result.killed:
            p = self.players[uid]
            if p.lover_id and p.lover_id in self.players:
                lover = self.players[p.lover_id]
                if not lover.is_alive and p.lover_id not in result.killed:
                    lines.append(t(self.lang, "lover_died", name=lover.name))

        if "doctor_saved" in result.messages:
            lines.append(t(self.lang, "doctor_saved"))

        await self.send(self.chat_id, "\n".join(lines))

        # Detective DM results
        for t_id, role in result.inspected.items():
            detective = next(
                (p for p in self.players.values()
                 if p.is_alive and p.role == Role.DETECTIVE and p.night_target == t_id and not p.is_ai),
                None
            )
            if detective:
                await self.send(
                    detective.user_id,
                    f"🔍 <b>{self.players[t_id].name}</b> — <b>{role.display_name}</b> {role.emoji}"
                )

        self._log("night_end", f"Round {self.round}: killed={result.killed}, sniped={result.sniped}")

        winner = RoleEngine.check_win(self.players)
        if winner:
            await self._end_game(winner)
        else:
            await self._begin_day()

    # ═══════════════════════════════════════════════════════════════
    # Day
    # ═══════════════════════════════════════════════════════════════

    async def _begin_day(self) -> None:
        self.phase = Phase.DAY

        alive_list = "\n".join(
            f"  {'✅' if p.is_alive else '💀'} {p.mention}"
            for p in self.players.values()
        )

        # AI bluff messages
        asyncio.create_task(self._ai_day_chat())

        await self.send(
            self.chat_id,
            t(self.lang, "day_phase",
              n=self.round, alive=len(self.alive_players()),
              list=alive_list, sec=cfg.DAY_TIMEOUT),
            reply_markup_chat=day_control_keyboard(is_host=True)
        )

        self._cancel_phase_task()
        self._phase_task = asyncio.create_task(self._day_timeout())

    async def _ai_day_chat(self) -> None:
        """AI players send bluff messages during day discussion."""
        alive_ai = [p for p in self.players.values() if p.is_alive and p.is_ai]
        for ai in alive_ai:
            await asyncio.sleep(random.uniform(5, 20))
            if self.phase != Phase.DAY:
                break
            msg = AIEngine.get_bluff_message(ai, self.players)
            if msg:
                await self.send(self.chat_id, f"🤖 <b>{ai.name}:</b> {msg}")

    async def _day_timeout(self) -> None:
        await asyncio.sleep(cfg.DAY_TIMEOUT)
        await self._begin_voting()

    async def skip_to_vote(self, user_id: int) -> str:
        if user_id != self.host_id:
            return t(self.lang, "not_host")
        if self.phase != Phase.DAY:
            return "❌ Kun fazasi emas."
        self._cancel_phase_task()
        asyncio.create_task(self._begin_voting())
        return "⏩ Ovoz berishga o'tilmoqda!"

    # ═══════════════════════════════════════════════════════════════
    # Voting
    # ═══════════════════════════════════════════════════════════════

    async def _begin_voting(self) -> None:
        self.phase = Phase.VOTING
        self.vote_tracker.reset()

        # Reset mayor weights
        for p in self.players.values():
            if p.role == Role.MAYOR and p.is_revealed:
                self.vote_tracker.set_weight(p.user_id, 2)

        alive = self.alive_players()
        await self.send(
            self.chat_id,
            t(self.lang, "voting_phase", sec=cfg.VOTING_TIMEOUT),
            vote_targets=[(p.user_id, p.mention) for p in alive],
            anonymous=self.anonymous_vote
        )

        await self._process_ai_votes()
        self._cancel_phase_task()
        self._phase_task = asyncio.create_task(self._voting_timeout())

    async def _process_ai_votes(self) -> None:
        for uid, player in self.players.items():
            if not player.is_alive or not player.is_ai:
                continue
            await asyncio.sleep(random.uniform(1.0, 4.0))
            if self.phase != Phase.VOTING:
                break
            target = AIEngine.choose_vote_target(
                player, self.players, self._known_roles,
                self._vote_history, self._all_kills
            )
            if target and target in self.players:
                self.vote_tracker.cast(uid, target)
                self._vote_history.setdefault(uid, []).append(target)

    def cast_vote(self, voter_id: int, target_id: int) -> str:
        if self.phase != Phase.VOTING:
            return "❌ Ovoz berish faol emas."
        voter = self.players.get(voter_id)
        if not voter or not voter.is_alive:
            return "❌ Siz ovoz bera olmaysiz."
        target = self.players.get(target_id)
        if not target or not target.is_alive:
            return "❌ Noto'g'ri nishon."
        if target_id == voter_id:
            return "❌ O'zingizga ovoz bera olmaysiz."
        prev = self.vote_tracker.get_previous(voter_id)
        self.vote_tracker.cast(voter_id, target_id)
        self._vote_history.setdefault(voter_id, []).append(target_id)
        if prev and prev != target_id:
            prev_name = self.players.get(prev, Player(-1, "?")).name
            return f"🔄 Ovoz o'zgartirildi: <b>{prev_name}</b> → <b>{target.name}</b>"
        return f"✅ <b>{target.name}</b> ga ovoz berildi"

    async def _voting_timeout(self) -> None:
        await asyncio.sleep(cfg.VOTING_TIMEOUT)
        await self._resolve_vote()

    async def _resolve_vote(self) -> None:
        if self.phase != Phase.VOTING:
            return

        tally = self.vote_tracker.tally()
        if not tally:
            await self.send(self.chat_id, t(self.lang, "no_votes"))
            await self._check_and_continue()
            return

        # Build tally display
        tally_lines = [
            f"  {self.players[uid].mention}: {v} ovoz"
            for uid, v in sorted(tally.items(), key=lambda x: -x[1])
            if uid in self.players
        ]

        max_votes = max(tally.values())
        leaders = [uid for uid, v in tally.items() if v == max_votes and uid in self.players]

        if len(leaders) > 1:
            names = ", ".join(self.players[uid].name for uid in leaders)
            await self.send(self.chat_id,
                f"📊 <b>Ovozlar:</b>\n" + "\n".join(tally_lines) +
                "\n\n" + t(self.lang, "tie_vote", names=names))
            await self._check_and_continue()
            return

        condemned_id = leaders[0]
        condemned = self.players[condemned_id]

        # Check Jester win
        if condemned.role == Role.JESTER:
            condemned.is_alive = False
            await self.send(self.chat_id,
                f"📊 <b>Ovozlar:</b>\n" + "\n".join(tally_lines) +
                "\n\n" + t(self.lang, "jester_wins", name=condemned.name))
            self._log("jester_win", condemned.name)
            await self._end_game("jester")
            return

        # Defense speech phase
        self._defense_target = condemned_id
        self.phase = Phase.DEFENSE

        await self.send(self.chat_id,
            f"📊 <b>Ovozlar:</b>\n" + "\n".join(tally_lines) +
            "\n\n" + t(self.lang, "defense_speech",
                       name=condemned.name, sec=cfg.DEFENSE_TIMEOUT))

        self._cancel_phase_task()
        self._phase_task = asyncio.create_task(self._defense_timeout(condemned_id))

    async def _defense_timeout(self, condemned_id: int) -> None:
        await asyncio.sleep(cfg.DEFENSE_TIMEOUT)
        await self._execute_player(condemned_id)

    async def _execute_player(self, player_id: int) -> None:
        if self.phase not in (Phase.DEFENSE, Phase.VOTING):
            return
        player = self.players.get(player_id)
        if not player:
            return

        player.is_alive = False
        if not player.is_ai:
            self._ghost_members.append(player_id)

        will = player.last_will or (await self.db.get_last_will(player_id) if player_id > 0 else "")

        msg = t(self.lang, "eliminated",
                name=player.name, role=player.role.display_name, emoji=player.role.emoji)
        if will:
            msg += "\n\n" + t(self.lang, "last_will", name=player.name, will=will)

        # Lover death
        if player.lover_id and player.lover_id in self.players:
            lover = self.players[player.lover_id]
            if lover.is_alive:
                lover.is_alive = False
                msg += "\n\n" + t(self.lang, "lover_died", name=lover.name)
                if not lover.is_ai:
                    self._ghost_members.append(player.lover_id)

        await self.send(self.chat_id, msg)
        self._log("eliminated", f"{player.name} ({player.role.value})")
        await self._check_and_continue()

    async def _check_and_continue(self) -> None:
        winner = RoleEngine.check_win(self.players)
        if winner:
            await self._end_game(winner)
        else:
            await self._begin_night()

    # ═══════════════════════════════════════════════════════════════
    # Mafia & Ghost Chat
    # ═══════════════════════════════════════════════════════════════

    async def relay_mafia_chat(self, sender_id: int, text: str) -> bool:
        """Relay message to all human mafia members. Returns True if sent."""
        if sender_id not in self._mafia_chat_members:
            return False
        sender = self.players.get(sender_id)
        if not sender:
            return False
        for uid in self._mafia_chat_members:
            if uid != sender_id:
                await self.send(uid, t(self.lang, "mafia_chat", msg=f"{sender.name}: {text}"))
        return True

    async def relay_ghost_chat(self, sender_id: int, text: str) -> bool:
        """Relay message to all dead human players."""
        if sender_id not in self._ghost_members:
            return False
        sender = self.players.get(sender_id)
        if not sender:
            return False
        for uid in self._ghost_members:
            if uid != sender_id:
                await self.send(uid, t(self.lang, "ghost_chat", name=sender.name, msg=text))
        return True

    # ═══════════════════════════════════════════════════════════════
    # Mayor reveal
    # ═══════════════════════════════════════════════════════════════

    async def mayor_reveal(self, user_id: int) -> str:
        player = self.players.get(user_id)
        if not player or player.role != Role.MAYOR:
            return "❌ Siz Mayor emassiz."
        if player.is_revealed:
            return "❌ Siz allaqachon oshkor qilgansiz."
        player.is_revealed = True
        self.vote_tracker.set_weight(user_id, 2)
        await self.send(self.chat_id,
            f"👑 <b>{player.name}</b> o'zini MAYOR ekanini oshkor qildi! "
            f"Uning ovozi IKKI HISOB bo'ladi!")
        return "✅ Oshkor qildingiz!"

    # ═══════════════════════════════════════════════════════════════
    # End game
    # ═══════════════════════════════════════════════════════════════

    async def _end_game(self, winner: str) -> None:
        self.phase = Phase.ENDED
        self._cancel_phase_task()

        winner_emoji = {
            "town": "🏙️", "mafia": "🔫", "solo": "🔪",
            "jester": "🤡", "draw": "⚖️"
        }.get(winner, "🎭")

        winner_text_key = {
            "town": "win_town", "mafia": "win_mafia",
            "solo": "win_maniac", "jester": "win_jester", "draw": "win_draw"
        }.get(winner, "win_town")

        reveal = "\n".join(
            f"  {p.role.emoji} <b>{p.name}</b> — {p.role.display_name} "
            f"[{'✅' if p.is_alive else '💀'}]"
            for p in self.players.values()
        )

        await self.send(
            self.chat_id,
            t(self.lang, "game_over",
              emoji=winner_emoji,
              winner=t(self.lang, winner_text_key),
              roles=reveal,
              rounds=self.round)
        )

        # Persist
        player_data = [
            {"user_id": p.user_id, "name": p.name, "role": p.role.value,
             "survived": p.is_alive, "is_ai": p.is_ai}
            for p in self.players.values()
        ]
        self._log("game_end", f"Winner: {winner}")
        await self.db.end_game(self.game_id, winner, self.round, player_data, self._game_log)

        # Update stats for human players
        elos = [1000]
        for p in self.players.values():
            if p.user_id > 0:
                stats = await self.db.get_player_stats(p.user_id)
                if stats:
                    elos.append(stats["elo"])
        avg_elo = sum(elos) // len(elos)

        for p in self.players.values():
            if p.is_ai or p.user_id < 0:
                continue
            won = (
                (winner == "town"   and p.role.team == "town") or
                (winner == "mafia"  and p.role.team == "mafia") or
                (winner == "solo"   and p.role.team == "solo") or
                (winner == "jester" and p.role == Role.JESTER)
            )
            result = await self.db.record_game_result(
                p.user_id, p.name, p.role.value, won, p.is_alive, avg_elo
            )
            # Notify player of ELO change and new achievements
            elo_sign = "+" if result["elo_change"] >= 0 else ""
            notif = f"📊 ELO: <b>{result['new_elo']}</b> ({elo_sign}{result['elo_change']})"
            if result["new_achievements"]:
                ach_names = ", ".join(
                    f"{ACHIEVEMENTS[k].emoji} {ACHIEVEMENTS[k].name}"
                    for k in result["new_achievements"]
                    if k in ACHIEVEMENTS
                )
                notif += f"\n🏅 Yangi yutuq: <b>{ach_names}</b>!"
            try:
                await self.send(p.user_id, notif)
            except Exception:
                pass

    # ═══════════════════════════════════════════════════════════════
    # Utility
    # ═══════════════════════════════════════════════════════════════

    def _cancel_phase_task(self) -> None:
        if self._phase_task and not self._phase_task.done():
            self._phase_task.cancel()
        self._phase_task = None

    def _log(self, event: str, detail: str = "") -> None:
        from datetime import datetime
        self._game_log.append({
            "t": datetime.utcnow().isoformat()[:19],
            "e": event,
            "d": detail,
            "r": self.round,
        })

    def role_info(self, user_id: int) -> str:
        p = self.players.get(user_id)
        if not p:
            return "Siz bu o'yinda yo'qsiz."
        lang = p.lang
        desc = p.role.description(lang)
        lines = [t(lang, "your_role", emoji=p.role.emoji, role=p.role.display_name), desc]
        if p.role == Role.MAFIA:
            mates = [q.name for qid, q in self.players.items()
                     if q.role == Role.MAFIA and qid != user_id]
            if mates:
                lines.append(f"\n🤝 Hamkorlar: <b>{', '.join(mates)}</b>")
        if p.role == Role.SERIAL_KILLER:
            lines.append("\n⚔️ Mafiya sizi o'ldira olmaydi!")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# Registry
# ══════════════════════════════════════════════════════════════════

class GameRegistry:
    def __init__(self):
        self._games: dict[int, GameManager] = {}

    def get(self, chat_id: int) -> Optional[GameManager]:
        return self._games.get(chat_id)

    def create(self, chat_id: int, send_fn: SendFn, db, lang: str = "uz") -> GameManager:
        gm = GameManager(chat_id, send_fn, db, lang)
        self._games[chat_id] = gm
        return gm

    def remove(self, chat_id: int) -> None:
        self._games.pop(chat_id, None)

    def cleanup_ended(self) -> int:
        ended = [cid for cid, gm in self._games.items() if gm.is_ended()]
        for cid in ended:
            del self._games[cid]
        return len(ended)

    def find_game_for_user(self, user_id: int) -> Optional[GameManager]:
        for gm in self._games.values():
            if user_id in gm.players:
                return gm
        return None

    def active_count(self) -> int:
        return len(self._games)
