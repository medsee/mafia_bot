"""keyboards.py — All InlineKeyboard builders."""
from __future__ import annotations
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def lobby_keyboard(player_count: int, host: bool = False) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("🙋 Qo'shilish", callback_data="join"),
         InlineKeyboardButton("🚪 Chiqish",    callback_data="leave")],
        [InlineKeyboardButton("🤖 AI o'yinchi qo'sh", callback_data="add_ai")],
    ]
    if host and player_count >= 4:
        buttons.append([InlineKeyboardButton(
            f"▶️ O'yinni boshlash ({player_count} o'yinchi)", callback_data="start")])
    elif host:
        buttons.append([InlineKeyboardButton(
            f"⚠️ Kamida 4 kerak ({player_count}/4)", callback_data="noop")])
    return InlineKeyboardMarkup(buttons)


def vote_keyboard(targets: list[tuple[int, str]], anonymous: bool = False) -> InlineKeyboardMarkup:
    label = "🗳️" if not anonymous else "🕵️"
    buttons = [[InlineKeyboardButton(f"{label} {name}", callback_data=f"vote:{uid}")]
               for uid, name in targets]
    buttons.append([InlineKeyboardButton("⏭️ Betaraf", callback_data="vote:skip")])
    return InlineKeyboardMarkup(buttons)


def night_action_keyboard(targets: list[tuple[int, str]], emoji: str) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(f"{emoji} {name}", callback_data=f"night:{uid}")]
               for uid, name in targets]
    buttons.append([InlineKeyboardButton("⏭️ O'tkazib yuborish", callback_data="night:skip")])
    return InlineKeyboardMarkup(buttons)


def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🇺🇿 O'zbek",  callback_data="lang:uz"),
        InlineKeyboardButton("🇷🇺 Русский", callback_data="lang:ru"),
        InlineKeyboardButton("🇬🇧 English", callback_data="lang:en"),
    ]])


def stats_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏆 Reyting",    callback_data="leaderboard"),
         InlineKeyboardButton("📊 Mening stats", callback_data="my_stats")],
        [InlineKeyboardButton("🌍 Global stats", callback_data="global_stats"),
         InlineKeyboardButton("🏅 Yutuqlar",   callback_data="my_achievements")],
    ])


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎮 Yangi o'yin",  callback_data="new_game"),
         InlineKeyboardButton("📊 Statistika",   callback_data="stats_menu")],
        [InlineKeyboardButton("🌐 Til",          callback_data="lang_menu"),
         InlineKeyboardButton("❓ Yordam",       callback_data="howto")],
    ])


def day_control_keyboard(is_host: bool) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("📋 O'yinchilar", callback_data="player_list"),
         InlineKeyboardButton("🃏 Mening rolim", callback_data="my_role")],
    ]
    if is_host:
        buttons.append([InlineKeyboardButton("⏩ Ovozga o'tish", callback_data="skip_to_vote")])
    return InlineKeyboardMarkup(buttons)


def mayor_reveal_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("👑 O'zimni oshkor qilaman!", callback_data="mayor_reveal"),
        InlineKeyboardButton("🤫 Yashirin qolaman",       callback_data="noop"),
    ]])


def last_will_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📜 Vasiyat yozish", callback_data="write_will"),
    ]])
