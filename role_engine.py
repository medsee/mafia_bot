"""
role_engine.py — Pure logic layer. Takes player state, returns NightResult.
No Telegram, no I/O. 100% testable in isolation.
"""
from __future__ import annotations

from typing import Optional
from models import NightResult, Phase, Player, Role


class RoleEngine:
    """
    Resolves all night actions for a single game round.

    Priority order (simultaneous resolution with priorities):
      1. Sniper shot  (instant, unblockable)
      2. Mafia kill   (blockable by doctor)
      3. Maniac kill  (blockable by doctor)
      4. Doctor heal  (blocks kill on target)
      5. Detective inspect (always succeeds)
    """

    @staticmethod
    def resolve_night(players: dict[int, Player]) -> NightResult:
        result = NightResult()
        alive = {uid: p for uid, p in players.items() if p.is_alive}

        # Collect actions
        mafia_votes: dict[int, int] = {}   # target_id -> vote count
        maniac_target: Optional[int] = None
        doctor_heal: Optional[int] = None
        detective_targets: list[int] = []
        sniper_target: Optional[int] = None

        for uid, player in alive.items():
            if not player.has_acted or player.night_target is None:
                continue
            t = player.night_target
            if t not in players or not players[t].is_alive:
                continue  # invalid target silently ignored

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

        # ── 1. Sniper (instant, unblockable) ──
        if sniper_target is not None:
            sniper = next((p for p in alive.values() if p.role == Role.SNIPER), None)
            if sniper:
                sniper.sniper_used = True
                players[sniper_target].is_alive = False
                result.sniped.append(sniper_target)

        # ── 2. Mafia kill (majority vote) ──
        mafia_kill: Optional[int] = None
        if mafia_votes:
            mafia_kill = max(mafia_votes, key=lambda k: mafia_votes[k])

        # ── 3. Doctor heal ──
        if doctor_heal is not None:
            result.healed.append(doctor_heal)

        # ── 4. Apply mafia kill (unless healed or already sniped) ──
        if mafia_kill is not None:
            if mafia_kill in result.healed:
                result.messages.append(
                    f"🩺 The Doctor saved someone from the Mafia tonight!"
                )
            elif mafia_kill not in result.sniped:
                players[mafia_kill].is_alive = False
                result.killed.append(mafia_kill)

        # ── 5. Maniac kill (unless healed or already dead) ──
        if maniac_target is not None:
            if maniac_target in result.healed:
                result.messages.append(
                    "🩺 The Doctor unknowingly saved someone from a mysterious attacker!"
                )
            elif maniac_target not in result.sniped and players[maniac_target].is_alive:
                players[maniac_target].is_alive = False
                result.killed.append(maniac_target)

        # ── 6. Detective results (private, returned in result) ──
        for t in detective_targets:
            result.inspected[t] = players[t].role

        return result

    @staticmethod
    def check_win(players: dict[int, Player]) -> Optional[str]:
        """
        Returns winning team string, or None if game continues.
        Win conditions:
          - 'town'   : all mafia AND maniac eliminated
          - 'mafia'  : mafia count >= town count (maniac counts as enemy)
          - 'maniac' : maniac is last player alive (or only non-mafia alive 1v1 with mafia)
        """
        alive = [p for p in players.values() if p.is_alive]
        if not alive:
            return "draw"

        mafia_alive  = [p for p in alive if p.role == Role.MAFIA]
        maniac_alive = [p for p in alive if p.role == Role.MANIAC]
        town_alive   = [p for p in alive if p.role not in (Role.MAFIA, Role.MANIAC)]

        # Maniac wins: sole survivor
        if len(alive) == 1 and maniac_alive:
            return "maniac"

        # Maniac wins: only maniac vs mafia left (no town), maniac outnumbers or equals
        if not town_alive and maniac_alive and not mafia_alive:
            return "maniac"

        # Town wins: no mafia and no maniac
        if not mafia_alive and not maniac_alive:
            return "town"

        # Mafia wins: mafia >= all others
        if len(mafia_alive) >= len(town_alive) + len(maniac_alive):
            return "mafia"

        return None  # game continues

    @staticmethod
    def get_night_action_verb(role: Role) -> str:
        return {
            Role.MAFIA:     "🔫 Choose your kill target",
            Role.DOCTOR:    "💊 Choose who to protect",
            Role.DETECTIVE: "🔍 Choose who to investigate",
            Role.SNIPER:    "🎯 Choose your ONE-SHOT target",
            Role.MANIAC:    "🔪 Choose your kill target",
        }.get(role, "")
