"""ai_engine.py — Smart AI with personalities and bluff messages."""
from __future__ import annotations
import random
from typing import Optional
from models import Player, Role, AIPersonality


class AIEngine:

    # Bluff messages for day chat per personality
    BLUFF_MESSAGES = {
        AIPersonality.AGGRESSIVE: [
            "Men {name} da shubhalayman! Unga ovoz bering!",
            "{name} aniq Mafiya! Hamma ovoz bersin!",
            "Siz nima deysiz {name}?! Javob bering!",
            "Bu o'yin {name} uchun juda oson ketmoqda...",
        ],
        AIPersonality.PARANOID: [
            "Hech kimga ishonmang... {name} ham shubhali.",
            "Kecha kimdir meni kuzatayotgandek edi...",
            "{name} jim turibdi — bu shubhali!",
            "Men hali ham kim Mafiya ekanini bilmayman, lekin {name}...",
        ],
        AIPersonality.LOGICAL: [
            "{name} ikkala ovozda bir xil nishon tanladi — mantiqsiz.",
            "Statistikaga ko'ra, {name} shubhali toifaga kiradi.",
            "Kecha 3 kishi o'ldi. Mafiya ko'proq. Hisob-kitob qilamiz.",
            "{name} Detektiv bo'lsa, nega indamayapti?",
        ],
        AIPersonality.RANDOM: [
            "Ha... bilmadim. {name} ga ovoz beraman balki.",
            "{name} yuzini ko'rganim yo'q 🤔",
            "Tasodifiy ovoz: {name}!",
            "🎲 Zar tashlash vaqti — {name}!",
        ],
    }

    @classmethod
    def get_bluff_message(cls, actor: Player, all_players: dict[int, Player]) -> Optional[str]:
        """Returns a bluff/discussion message for day phase."""
        alive_others = [p for uid, p in all_players.items()
                        if p.is_alive and uid != actor.user_id and not p.is_ai]
        if not alive_others:
            return None
        target = random.choice(alive_others)
        msgs = cls.BLUFF_MESSAGES.get(actor.ai_personality, cls.BLUFF_MESSAGES[AIPersonality.LOGICAL])
        return random.choice(msgs).format(name=target.name)

    @classmethod
    def choose_night_target(
        cls, actor: Player, all_players: dict[int, Player],
        known_roles: dict[int, Role], vote_history: dict[int, list[int]]
    ) -> Optional[int]:
        alive_others = [p for uid, p in all_players.items()
                        if p.is_alive and uid != actor.user_id]
        if not alive_others:
            return None

        if actor.role == Role.MAFIA:
            return cls._mafia_target(actor, alive_others, known_roles, actor.ai_personality)
        elif actor.role == Role.DOCTOR:
            return cls._doctor_target(actor, alive_others, known_roles)
        elif actor.role == Role.DETECTIVE:
            return cls._detective_target(alive_others, known_roles)
        elif actor.role == Role.SNIPER:
            return cls._sniper_target(actor, alive_others, known_roles)
        elif actor.role in (Role.MANIAC, Role.SERIAL_KILLER):
            return cls._solo_target(alive_others, actor.role)
        elif actor.role == Role.BODYGUARD:
            return cls._bodyguard_target(actor, alive_others, known_roles)
        return None

    @classmethod
    def choose_vote_target(
        cls, voter: Player, all_players: dict[int, Player],
        known_roles: dict[int, Role], vote_history: dict[int, list[int]],
        night_kills: list[int]
    ) -> Optional[int]:
        alive_others = [p for uid, p in all_players.items()
                        if p.is_alive and uid != voter.user_id]
        if not alive_others:
            return None

        if voter.role == Role.MAFIA:
            non_mafia = [p for p in alive_others if p.role != Role.MAFIA]
            targets = non_mafia if non_mafia else alive_others
            priority = {Role.DETECTIVE:10, Role.SNIPER:9, Role.DOCTOR:8,
                        Role.BODYGUARD:7, Role.MAYOR:6, Role.MANIAC:4, Role.CIVILIAN:2}
            weights = [float(priority.get(p.role, 1)) for p in targets]
            return cls._weighted_choice(targets, weights)

        if voter.role == Role.JESTER:
            # Jester wants to BE voted out — tries to look suspicious
            return None  # just let town vote naturally

        # Town: vote most suspicious
        weights = []
        for p in alive_others:
            w = 1.0
            if p.user_id in known_roles:
                w = 100.0 if known_roles[p.user_id] in (Role.MAFIA, Role.MANIAC, Role.SERIAL_KILLER) else 0.1
            elif voter.ai_personality == AIPersonality.AGGRESSIVE:
                w += random.uniform(0, 3)
            elif voter.ai_personality == AIPersonality.PARANOID:
                w += 1.5
            elif voter.ai_personality == AIPersonality.LOGICAL:
                # boost if target voted for known-innocent
                for vid, vtargets in vote_history.items():
                    if vid == p.user_id and any(t in night_kills for t in vtargets):
                        w += 2
            weights.append(w + random.uniform(0, 0.3))
        return cls._weighted_choice(alive_others, weights)

    # ── Private helpers ───────────────────────────────────────────

    @classmethod
    def _mafia_target(cls, actor, candidates, known_roles, personality):
        non_mafia = [p for p in candidates if p.role != Role.MAFIA]
        if not non_mafia:
            return random.choice(candidates).user_id

        priority = {
            Role.DETECTIVE:10, Role.SNIPER:9, Role.DOCTOR:8,
            Role.BODYGUARD:7, Role.SERIAL_KILLER:6, Role.MAYOR:5, Role.MANIAC:4, Role.CIVILIAN:2
        }
        weights = []
        for p in non_mafia:
            w = float(priority.get(p.role, 1))
            if p.user_id in known_roles and known_roles[p.user_id] in (Role.DETECTIVE, Role.SNIPER):
                w += 5  # confirmed threat
            if personality == AIPersonality.AGGRESSIVE:
                w += random.uniform(0, 2)
            weights.append(w)
        return cls._weighted_choice(non_mafia, weights)

    @classmethod
    def _doctor_target(cls, actor, candidates, known_roles):
        # Self-heal 20% chance
        if random.random() < 0.20:
            return actor.user_id
        # Protect confirmed detective
        for p in candidates:
            if known_roles.get(p.user_id) == Role.DETECTIVE:
                return p.user_id
        return random.choice(candidates).user_id

    @classmethod
    def _detective_target(cls, candidates, known_roles):
        unknown = [p for p in candidates if p.user_id not in known_roles and not p.is_ai]
        pool = unknown if unknown else candidates
        return random.choice(pool).user_id

    @classmethod
    def _sniper_target(cls, actor, candidates, known_roles):
        if actor.sniper_used:
            return None
        confirmed = [p for p in candidates if known_roles.get(p.user_id) == Role.MAFIA]
        if confirmed:
            return random.choice(confirmed).user_id
        # Fire if few players left
        if len(candidates) <= 5:
            suspicious = [p for p in candidates
                          if p.role not in (Role.DOCTOR, Role.DETECTIVE, Role.MAYOR, Role.BODYGUARD)]
            if suspicious:
                return random.choice(suspicious).user_id
        return None  # hold shot

    @classmethod
    def _solo_target(cls, candidates, role):
        # Maniac/SK prefers non-mafia targets
        if role == Role.SERIAL_KILLER:
            non_mafia = [p for p in candidates if p.role != Role.MAFIA]
            return random.choice(non_mafia if non_mafia else candidates).user_id
        town = [p for p in candidates if p.role in (Role.CIVILIAN, Role.DOCTOR, Role.DETECTIVE, Role.MAYOR)]
        return random.choice(town if town else candidates).user_id

    @classmethod
    def _bodyguard_target(cls, actor, candidates, known_roles):
        # Protect most valuable town player
        for p in candidates:
            if known_roles.get(p.user_id) == Role.DETECTIVE:
                return p.user_id
        priority = {Role.DETECTIVE:10, Role.MAYOR:8, Role.DOCTOR:7, Role.SNIPER:6, Role.CIVILIAN:2}
        weights = [float(priority.get(p.role, 1)) for p in candidates]
        return cls._weighted_choice(candidates, weights)

    @staticmethod
    def _weighted_choice(candidates: list, weights: list) -> int:
        total = sum(weights)
        if total == 0 or not candidates:
            return random.choice(candidates).user_id
        r = random.uniform(0, total)
        cum = 0.0
        for p, w in zip(candidates, weights):
            cum += w
            if r <= cum:
                return p.user_id
        return candidates[-1].user_id
