"""
ai_engine.py — Smart AI behavior for bot-controlled players.

AI decision-making uses weighted heuristics, not random choices:
  - Mafia AI: targets most suspicious player, avoids killing own teammates
  - Doctor AI: protects most-targeted or most-valuable player
  - Detective AI: inspects least-known players first
  - Sniper AI: holds shot for confirmed mafia or uses on most suspicious
  - Maniac AI: unpredictable, targets town players preferentially
  - Town AI voting: votes most suspicious player
"""
from __future__ import annotations

import random
from typing import Optional
from models import Player, Role


class AIEngine:
    """
    Stateless AI decision engine. All methods are pure functions.
    Pass in game state, get back a target user_id.
    """

    # Suspicion weights assigned to roles from AI perspective
    _SUSPICION_WEIGHTS = {
        Role.MAFIA:     10,
        Role.MANIAC:    8,
        Role.CIVILIAN:  3,
        Role.DOCTOR:    2,
        Role.DETECTIVE: 2,
        Role.SNIPER:    2,
    }

    @classmethod
    def choose_night_target(
        cls,
        actor: Player,
        all_players: dict[int, Player],
        known_roles: dict[int, Role],   # detective-revealed roles (accumulated)
        vote_history: dict[int, list[int]],  # player_id -> list of who they voted for
    ) -> Optional[int]:
        """Returns user_id of AI's chosen night target, or None if skipping."""
        alive_others = [
            p for uid, p in all_players.items()
            if p.is_alive and uid != actor.user_id
        ]
        if not alive_others:
            return None

        if actor.role == Role.MAFIA:
            return cls._mafia_target(actor, alive_others, known_roles, vote_history)
        elif actor.role == Role.DOCTOR:
            return cls._doctor_target(actor, alive_others, known_roles, vote_history)
        elif actor.role == Role.DETECTIVE:
            return cls._detective_target(actor, alive_others, known_roles)
        elif actor.role == Role.SNIPER:
            return cls._sniper_target(actor, alive_others, known_roles, vote_history)
        elif actor.role == Role.MANIAC:
            return cls._maniac_target(alive_others, known_roles)
        return None

    @classmethod
    def choose_vote_target(
        cls,
        voter: Player,
        all_players: dict[int, Player],
        known_roles: dict[int, Role],
        vote_history: dict[int, list[int]],
        night_kills: list[int],
    ) -> Optional[int]:
        """Returns user_id to vote for during day phase."""
        alive_others = [
            p for uid, p in all_players.items()
            if p.is_alive and uid != voter.user_id
        ]
        if not alive_others:
            return None

        if voter.role == Role.MAFIA:
            # Mafia votes for town members — prioritize powerful roles
            town_targets = [p for p in alive_others
                            if p.role not in (Role.MAFIA,)
                            or all_players[p.user_id].user_id not in known_roles]
            if not town_targets:
                town_targets = alive_others
            weights = cls._voting_weights_vs_town(town_targets, known_roles, vote_history)
            return cls._weighted_choice(town_targets, weights)

        # Town/maniac: vote most suspicious
        weights = cls._suspicion_weights(alive_others, known_roles, vote_history, night_kills)
        return cls._weighted_choice(alive_others, weights)

    # ──────────────────────────────────────────
    # Private helpers
    # ──────────────────────────────────────────

    @classmethod
    def _mafia_target(
        cls, actor: Player, candidates: list[Player],
        known_roles: dict[int, Role], vote_history: dict[int, list[int]]
    ) -> int:
        # Never target fellow mafia
        non_mafia = [p for p in candidates if p.role != Role.MAFIA]
        if not non_mafia:
            return random.choice(candidates).user_id

        # Prioritize: detective > sniper > doctor > maniac > civilian
        priority = {
            Role.DETECTIVE: 10, Role.SNIPER: 9, Role.DOCTOR: 8,
            Role.MANIAC: 4, Role.CIVILIAN: 2
        }
        weights = [priority.get(p.role, 1) for p in non_mafia]

        # Boost if confirmed by known_roles
        for i, p in enumerate(non_mafia):
            if p.user_id in known_roles:
                weights[i] += 5

        return cls._weighted_choice(non_mafia, weights)

    @classmethod
    def _doctor_target(
        cls, actor: Player, candidates: list[Player],
        known_roles: dict[int, Role], vote_history: dict[int, list[int]]
    ) -> int:
        # Self-heal 25% chance if no known threats
        if random.random() < 0.25:
            return actor.user_id

        # Protect the most voted player (most likely to be targeted)
        vote_counts: dict[int, int] = {}
        for votes in vote_history.values():
            for vid in votes:
                vote_counts[vid] = vote_counts.get(vid, 0) + 1

        # Prioritize protecting detective if known
        for p in candidates:
            if known_roles.get(p.user_id) == Role.DETECTIVE:
                return p.user_id

        # Otherwise protect most-voted alive player
        alive_ids = [p.user_id for p in candidates]
        sorted_by_votes = sorted(alive_ids, key=lambda uid: vote_counts.get(uid, 0), reverse=True)
        if sorted_by_votes:
            # With 60% probability protect most voted, else random
            if random.random() < 0.6:
                return sorted_by_votes[0]

        return random.choice(candidates).user_id

    @classmethod
    def _detective_target(
        cls, actor: Player, candidates: list[Player],
        known_roles: dict[int, Role]
    ) -> int:
        # Inspect players not yet investigated
        unknown = [p for p in candidates if p.user_id not in known_roles]
        if not unknown:
            unknown = candidates

        # Slightly prefer players who have voted aggressively (suspicious behavior)
        return random.choice(unknown).user_id

    @classmethod
    def _sniper_target(
        cls, actor: Player, candidates: list[Player],
        known_roles: dict[int, Role], vote_history: dict[int, list[int]]
    ) -> Optional[int]:
        if actor.sniper_used:
            return None

        # If we KNOW someone is mafia, shoot them
        confirmed_mafia = [p for p in candidates
                           if known_roles.get(p.user_id) == Role.MAFIA]
        if confirmed_mafia:
            return random.choice(confirmed_mafia).user_id

        # Otherwise wait unless > 60% of game done (conservative strategy)
        # AI sniper fires if player count is getting low
        if len(candidates) <= 4:
            weights = cls._suspicion_weights(candidates, known_roles, vote_history, [])
            return cls._weighted_choice(candidates, weights)

        # Hold shot (return None to skip action)
        return None

    @classmethod
    def _maniac_target(
        cls, candidates: list[Player], known_roles: dict[int, Role]
    ) -> int:
        # Maniac prefers civilians and avoids killing mafia (to let chaos reign)
        # But remains unpredictable — 30% chance to target anyone
        if random.random() < 0.3:
            return random.choice(candidates).user_id

        town = [p for p in candidates if p.role in (Role.CIVILIAN, Role.DOCTOR, Role.DETECTIVE)]
        if town:
            return random.choice(town).user_id
        return random.choice(candidates).user_id

    @classmethod
    def _suspicion_weights(
        cls, candidates: list[Player],
        known_roles: dict[int, Role],
        vote_history: dict[int, list[int]],
        night_kills: list[int],
    ) -> list[float]:
        """Higher weight = more suspicious."""
        weights = []
        for p in candidates:
            w = 1.0

            # Known role via detective: confirmed mafia/maniac gets max weight
            if p.user_id in known_roles:
                known = known_roles[p.user_id]
                if known in (Role.MAFIA, Role.MANIAC):
                    w = 100.0
                else:
                    w = 0.2  # confirmed innocent → low suspicion
                weights.append(w)
                continue

            # Players who voted for recently killed town members are more suspicious
            for killer_id, targets in vote_history.items():
                if killer_id == p.user_id:
                    for t in targets:
                        if t in night_kills:
                            w += 2.0  # voted for someone mafia later killed

            # Slight randomness (human unpredictability simulation)
            w += random.uniform(0, 0.5)
            weights.append(w)
        return weights

    @classmethod
    def _voting_weights_vs_town(
        cls, candidates: list[Player],
        known_roles: dict[int, Role],
        vote_history: dict[int, list[int]],
    ) -> list[float]:
        """Mafia voting weights: high for powerful roles."""
        priority = {
            Role.DETECTIVE: 10, Role.SNIPER: 8, Role.DOCTOR: 7,
            Role.MANIAC: 5, Role.CIVILIAN: 2
        }
        return [float(priority.get(p.role, 1)) for p in candidates]

    @staticmethod
    def _weighted_choice(candidates: list[Player], weights: list[float]) -> int:
        if not candidates:
            raise ValueError("No candidates")
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
