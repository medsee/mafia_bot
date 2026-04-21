"""
keyboards.py — All inline keyboard builders for Telegram UI.
Clean, reusable, no game logic here.
"""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def lobby_keyboard(player_count: int, host: bool = False) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton("🙋 Join Game", callback_data="join"),
            InlineKeyboardButton("🚪 Leave",     callback_data="leave"),
        ],
        [
            InlineKeyboardButton("🤖 Add AI Player", callback_data="add_ai"),
        ],
    ]
    if host and player_count >= 4:
        buttons.append([
            InlineKeyboardButton(f"▶️ Start Game ({player_count} players)", callback_data="start")
        ])
    elif host:
        buttons.append([
            InlineKeyboardButton(f"⚠️ Need 4+ players ({player_count}/4)", callback_data="noop")
        ])
    return InlineKeyboardMarkup(buttons)


def vote_keyboard(targets: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    """targets: list of (user_id, display_name)"""
    buttons = []
    for uid, name in targets:
        buttons.append([
            InlineKeyboardButton(f"🗳️ {name}", callback_data=f"vote:{uid}")
        ])
    buttons.append([
        InlineKeyboardButton("⏭️ Skip (abstain)", callback_data="vote:skip")
    ])
    return InlineKeyboardMarkup(buttons)


def night_action_keyboard(targets: list[tuple[int, str]], action_label: str) -> InlineKeyboardMarkup:
    """For DM night actions."""
    buttons = []
    for uid, name in targets:
        buttons.append([
            InlineKeyboardButton(f"{action_label} {name}", callback_data=f"night:{uid}")
        ])
    return InlineKeyboardMarkup(buttons)


def day_phase_keyboard(is_host: bool) -> InlineKeyboardMarkup:
    buttons = []
    if is_host:
        buttons.append([
            InlineKeyboardButton("⏩ Skip to Vote", callback_data="skip_to_vote")
        ])
    buttons.append([
        InlineKeyboardButton("📋 Player List", callback_data="player_list"),
        InlineKeyboardButton("🃏 My Role",     callback_data="my_role"),
    ])
    return InlineKeyboardMarkup(buttons)


def stats_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🏆 Leaderboard",  callback_data="leaderboard"),
        InlineKeyboardButton("📊 My Stats",     callback_data="my_stats"),
    ], [
        InlineKeyboardButton("🌍 Global Stats", callback_data="global_stats"),
    ]])


def confirm_keyboard(action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirm", callback_data=f"confirm:{action}"),
        InlineKeyboardButton("❌ Cancel",  callback_data="cancel"),
    ]])


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🎮 New Game",  callback_data="new_game"),
        InlineKeyboardButton("📊 Stats",     callback_data="stats_menu"),
    ], [
        InlineKeyboardButton("❓ How to Play", callback_data="howto"),
    ]])
