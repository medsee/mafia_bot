"""role_engine.py — Night resolution, win conditions, all roles."""
from __future__ import annotations
from typing import Optional
from models import NightResult, Player, Role


class RoleEngine:

    @staticmethod
    def resolve_night(players: dict[int, Player]) -> NightResult:
        result = NightResult()
        alive = {uid: p for uid, p in players.items() if p.is_alive}

        mafia_votes: dict[int, int] = {}
        maniac_target: Optional[int] = None
        sk_target: Optional[int]     = None
        doctor_heal: Optional[int]   = None
        detective_targets: list[int] = []
        sniper_target: Optional[int] = None
        bg_protections: dict[int, int] = {}  # target -> bodyguard

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
            elif player.role == Role.SERIAL_KILLER:
                sk_target = t
            elif player.role == Role.DOCTOR:
                doctor_heal = t
            elif player.role == Role.DETECTIVE:
                detective_targets.append(t)
            elif player.role == Role.SNIPER and not player.sniper_used:
                sniper_target = t
            elif player.role == Role.BODYGUARD:
                bg_protections[t] = uid

        # ── 1. Sniper (unblockable) ──
        if sniper_target is not None:
            sniper = next((p for p in alive.values() if p.role == Role.SNIPER), None)
            if sniper:
                sniper.sniper_used = True
                # Bodyguard can intercept sniper too
                if sniper_target in bg_protections:
                    bg_id = bg_protections[sniper_target]
                    players[bg_id].is_alive = False
                    result.bg_died.append((bg_id, sniper_target))
                    result.guarded.append(sniper_target)
                else:
                    players[sniper_target].is_alive = False
                    result.sniped.append(sniper_target)

        # ── 2. Mafia kill ──
        mafia_kill: Optional[int] = None
        if mafia_votes:
            mafia_kill = max(mafia_votes, key=lambda k: mafia_votes[k])

        # ── 3. Doctor heal ──
        if doctor_heal is not None:
            result.healed.append(doctor_heal)

        # ── 4. Apply mafia kill ──
        if mafia_kill is not None and mafia_kill not in result.sniped:
            if mafia_kill in result.healed:
                result.messages.append("doctor_saved")
            elif mafia_kill in bg_protections:
                bg_id = bg_protections[mafia_kill]
                players[bg_id].is_alive = False
                result.bg_died.append((bg_id, mafia_kill))
                result.guarded.append(mafia_kill)
            else:
                players[mafia_kill].is_alive = False
                result.killed.append(mafia_kill)

        # ── 5. Serial Killer (immune to mafia, unaffected by doctor) ──
        if sk_target is not None and players[sk_target].is_alive:
            if sk_target in bg_protections:
                bg_id = bg_protections[sk_target]
                players[bg_id].is_alive = False
                result.bg_died.append((bg_id, sk_target))
            else:
                players[sk_target].is_alive = False
                result.killed.append(sk_target)

        # ── 6. Maniac kill ──
        if maniac_target is not None and players[maniac_target].is_alive:
            if maniac_target in result.healed:
                result.messages.append("doctor_saved_maniac")
            elif maniac_target in bg_protections:
                bg_id = bg_protections[maniac_target]
                players[bg_id].is_alive = False
                result.bg_died.append((bg_id, maniac_target))
            else:
                players[maniac_target].is_alive = False
                result.killed.append(maniac_target)

        # ── 7. Detective ──
        for t in detective_targets:
            result.inspected[t] = players[t].role

        # ── 8. Lover chain deaths ──
        newly_dead = set(result.killed + result.sniped + [bg for bg, _ in result.bg_died])
        lover_deaths = []
        for uid in list(newly_dead):
            p = players[uid]
            if p.lover_id and p.lover_id in players:
                lover = players[p.lover_id]
                if lover.is_alive:
                    lover.is_alive = False
                    lover_deaths.append(p.lover_id)
        result.killed.extend(lover_deaths)

        return result

    @staticmethod
    def check_win(players: dict[int, Player]) -> Optional[str]:
        alive = [p for p in players.values() if p.is_alive]
        if not alive:
            return "draw"

        mafia_alive  = [p for p in alive if p.role == Role.MAFIA]
        solo_alive   = [p for p in alive if p.role in (Role.MANIAC, Role.SERIAL_KILLER)]
        town_alive   = [p for p in alive if p.role not in (Role.MAFIA, Role.MANIAC, Role.SERIAL_KILLER)]

        # Solo wins: last standing
        if len(alive) == 1 and solo_alive:
            return "solo"

        # Solo wins: only solo vs nothing
        if not town_alive and not mafia_alive and solo_alive:
            return "solo"

        # Town wins
        if not mafia_alive and not solo_alive:
            return "town"

        # Mafia wins
        if len(mafia_alive) >= len(town_alive) + len(solo_alive):
            return "mafia"

        return None

    @staticmethod
    def get_night_action_verb(role: Role) -> str:
        return {
            Role.MAFIA:          "🔫 Kim o'ldirilsin",
            Role.DOCTOR:         "💊 Kimni himoya qilasiz",
            Role.DETECTIVE:      "🔍 Kimni tekshirasiz",
            Role.SNIPER:         "🎯 BIR MARTA otish nishoni",
            Role.MANIAC:         "🔪 Qurbon nishoni",
            Role.BODYGUARD:      "🛡️ Kimni himoya qilasiz",
            Role.SERIAL_KILLER:  "⚔️ Qurbon nishoni",
        }.get(role, "🎯 Nishon tanlang")
