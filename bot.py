"""
bot.py — Main entry point. Telegram handlers, routing, error recovery.

python-telegram-bot v20+ (async).

Usage:
    BOT_TOKEN=your_token python bot.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Optional

from telegram import Update, Message, CallbackQuery, BotCommand
from telegram.constants import ParseMode
from telegram.error import Forbidden, BadRequest, TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from database import Database
from game_manager import GameManager, GameRegistry
from keyboards import (
    day_phase_keyboard,
    lobby_keyboard,
    main_menu_keyboard,
    night_action_keyboard,
    stats_keyboard,
    vote_keyboard,
)
from models import Phase, Role

# ──────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("mafia_bot.log"),
    ]
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)


# ──────────────────────────────────────────────
# Globals (initialized in main)
# ──────────────────────────────────────────────

db       = Database()
registry = GameRegistry()


# ──────────────────────────────────────────────
# Helper: send wrapper (used by GameManager)
# ──────────────────────────────────────────────

async def send_message(
    app: Application,
    chat_id: int,
    text: str,
    vote_targets: Optional[list[tuple[int, str]]] = None,
    **kwargs
) -> None:
    """
    Universal send helper injected into GameManager.
    Handles both group chat announcements and private DMs.
    Silently swallows Forbidden (user hasn't started bot DM).
    """
    try:
        reply_markup = None
        if vote_targets is not None:
            reply_markup = vote_keyboard(vote_targets)

        await app.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
        )
    except Forbidden:
        logger.warning("Cannot send to chat_id=%s (Forbidden)", chat_id)
    except BadRequest as e:
        logger.warning("BadRequest sending to %s: %s", chat_id, e)
    except TelegramError as e:
        logger.error("TelegramError sending to %s: %s", chat_id, e)


def make_send_fn(app: Application):
    """Curried send function for injection into GameManager."""
    async def _send(chat_id: int, text: str, **kwargs) -> None:
        await send_message(app, chat_id, text, **kwargs)
    return _send


# ──────────────────────────────────────────────
# Command: /start
# ──────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🎭 <b>Welcome to ULTRA PRO MAFIA BOT!</b>\n\n"
        "A fully-featured Mafia party game with:\n"
        "• 👤 Civilians  • 🔫 Mafia  • 💊 Doctor\n"
        "• 🔍 Detective  • 🎯 Sniper  • 🔪 Maniac\n\n"
        "Use /newgame in a group chat to start!\n"
        "Use /help to see all commands.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard(),
    )


# ──────────────────────────────────────────────
# Command: /newgame
# ──────────────────────────────────────────────

async def cmd_newgame(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user    = update.effective_user

    # Clean up ended games
    registry.cleanup_ended()

    existing = registry.get(chat_id)
    if existing and not existing.is_ended():
        await update.message.reply_text(
            "⚠️ A game is already running! Use /endgame to force-end it first."
        )
        return

    send_fn = make_send_fn(ctx.application)
    gm = registry.create(chat_id, send_fn, db)
    gm.join(user.id, user.full_name)

    await update.message.reply_text(
        f"🎮 <b>New Mafia game lobby created!</b>\n"
        f"🏠 Host: <b>{user.full_name}</b>\n\n"
        f"Players (1/10):\n  ✅ {user.full_name}\n\n"
        f"Minimum 4 players to start. Tap <b>Join Game</b>!",
        parse_mode=ParseMode.HTML,
        reply_markup=lobby_keyboard(1, host=True),
    )


# ──────────────────────────────────────────────
# Command: /join
# ──────────────────────────────────────────────

async def cmd_join(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _handle_join(update, ctx)


async def _handle_join(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user    = update.effective_user
    gm = registry.get(chat_id)

    if not gm or gm.is_ended():
        await update.message.reply_text("❌ No active lobby. Use /newgame to start one.")
        return
    if gm.phase != Phase.LOBBY:
        await update.message.reply_text("❌ Game already in progress!")
        return

    if gm.join(user.id, user.full_name):
        count = gm.player_count()
        player_list = "\n".join(
            f"  ✅ {p.mention}" for p in gm.players.values()
        )
        await update.message.reply_text(
            f"✅ <b>{user.full_name}</b> joined! ({count}/10)\n\n"
            f"<b>Players:</b>\n{player_list}",
            parse_mode=ParseMode.HTML,
            reply_markup=lobby_keyboard(count, host=(user.id == gm.host_id)),
        )
    else:
        await update.message.reply_text("⚠️ You're already in the lobby (or it's full).")


# ──────────────────────────────────────────────
# Command: /leave
# ──────────────────────────────────────────────

async def cmd_leave(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user    = update.effective_user
    gm = registry.get(chat_id)

    if not gm or gm.phase != Phase.LOBBY:
        await update.message.reply_text("❌ No active lobby to leave.")
        return

    if gm.leave(user.id):
        await update.message.reply_text(
            f"👋 <b>{user.full_name}</b> left the lobby. ({gm.player_count()}/10)",
            parse_mode=ParseMode.HTML,
            reply_markup=lobby_keyboard(gm.player_count(), host=(user.id == gm.host_id)),
        )
    else:
        await update.message.reply_text("❌ You're not in the lobby.")


# ──────────────────────────────────────────────
# Command: /start_game (host only)
# ──────────────────────────────────────────────

async def cmd_startgame(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user    = update.effective_user
    gm = registry.get(chat_id)

    if not gm:
        await update.message.reply_text("❌ No active lobby. Use /newgame first.")
        return
    if user.id != gm.host_id:
        await update.message.reply_text("❌ Only the host can start the game.")
        return

    await gm.start_game()

    # DM each human player their role
    for player in gm.players.values():
        if player.is_ai:
            continue
        role_msg = gm.role_info(player.user_id)
        try:
            await ctx.application.bot.send_message(
                chat_id=player.user_id,
                text=role_msg,
                parse_mode=ParseMode.HTML,
            )
            # If player has night action, send action keyboard
            if player.role.has_night_action and gm.phase == Phase.NIGHT:
                await _send_night_action_dm(ctx.application, gm, player.user_id)
        except Forbidden:
            await update.message.reply_text(
                f"⚠️ Couldn't DM <b>{player.name}</b> — they need to start me first!\n"
                f"Send /start to @{ctx.application.bot.username} in private.",
                parse_mode=ParseMode.HTML,
            )


async def _send_night_action_dm(app: Application, gm: GameManager, user_id: int) -> None:
    """Send the night action keyboard to a specific human player."""
    player = gm.get_player(user_id)
    if not player or not player.is_alive or not player.role.has_night_action:
        return
    if player.role == Role.SNIPER and player.sniper_used:
        await app.bot.send_message(
            chat_id=user_id,
            text="🎯 You've already used your sniper shot. Rest tonight.",
            parse_mode=ParseMode.HTML,
        )
        return

    alive_targets = [
        (p.user_id, p.name)
        for p in gm.alive_players()
        if p.user_id != user_id
    ]

    from role_engine import RoleEngine
    action_verb = RoleEngine.get_night_action_verb(player.role)

    try:
        await app.bot.send_message(
            chat_id=user_id,
            text=f"🌙 <b>Night action time!</b>\n{action_verb}:",
            parse_mode=ParseMode.HTML,
            reply_markup=night_action_keyboard(alive_targets, player.role.emoji),
        )
    except (Forbidden, BadRequest) as e:
        logger.warning("Couldn't send night DM to %s: %s", user_id, e)


# ──────────────────────────────────────────────
# Command: /endgame (host/admin)
# ──────────────────────────────────────────────

async def cmd_endgame(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user    = update.effective_user
    gm = registry.get(chat_id)

    if not gm:
        await update.message.reply_text("❌ No active game.")
        return

    # Allow host or any admin
    chat_member = await ctx.application.bot.get_chat_member(chat_id, user.id)
    is_admin = chat_member.status in ("administrator", "creator")

    if user.id != gm.host_id and not is_admin:
        await update.message.reply_text("❌ Only the host or an admin can end the game.")
        return

    registry.remove(chat_id)
    await update.message.reply_text("🛑 Game forcefully ended.")


# ──────────────────────────────────────────────
# Command: /stats
# ──────────────────────────────────────────────

async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📊 <b>Statistics</b>\nChoose what to view:",
        parse_mode=ParseMode.HTML,
        reply_markup=stats_keyboard(),
    )


# ──────────────────────────────────────────────
# Command: /myrole (in-game DM reminder)
# ──────────────────────────────────────────────

async def cmd_myrole(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    chat_id = update.effective_chat.id
    gm = registry.get(chat_id)

    if not gm or gm.phase == Phase.LOBBY or gm.is_ended():
        # Try all active games
        for cid, g in registry._games.items():
            if user.id in g.players:
                await update.message.reply_text(
                    g.role_info(user.id), parse_mode=ParseMode.HTML
                )
                return
        await update.message.reply_text("❌ You're not in an active game.")
        return

    if user.id not in gm.players:
        await update.message.reply_text("❌ You're not in this game.")
        return

    await update.message.reply_text(
        gm.role_info(user.id), parse_mode=ParseMode.HTML
    )


# ──────────────────────────────────────────────
# Command: /players
# ──────────────────────────────────────────────

async def cmd_players(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    gm = registry.get(chat_id)

    if not gm:
        await update.message.reply_text("❌ No active game.")
        return

    lines = [f"👥 <b>Players in game #{gm.game_id}:</b>\n"]
    for p in gm.players.values():
        icon = "✅" if p.is_alive else "💀"
        ai_tag = " 🤖" if p.is_ai else ""
        lines.append(f"  {icon} {p.name}{ai_tag}")

    lines.append(f"\nPhase: <b>{gm.phase.name}</b> | Round: <b>{gm.round}</b>")
    await update.message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML
    )


# ──────────────────────────────────────────────
# Command: /help
# ──────────────────────────────────────────────

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    help_text = """
🎭 <b>MAFIA BOT — Commands</b>

<b>In Group Chat:</b>
/newgame — Create a new game lobby
/join — Join the current lobby
/leave — Leave the lobby
/startgame — Start (host only)
/endgame — Force end (host/admin)
/players — List all players
/stats — View statistics
/myrole — Show your role reminder

<b>In Private DM:</b>
(Use inline buttons for night actions)
/myrole — Your role info

<b>Roles:</b>
👤 Civilian — No ability; vote wisely
🔫 Mafia — Kill 1 per night (team)
💊 Doctor — Protect 1 per night
🔍 Detective — Inspect role per night
🎯 Sniper — 1-shot unblockable kill
🔪 Maniac — Solo killer; survive alone

<b>Win conditions:</b>
🏙️ Town wins when Mafia + Maniac are gone
🔫 Mafia wins when equal to Town
🔪 Maniac wins as last survivor
"""
    await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)


# ──────────────────────────────────────────────
# Callback query handler
# ──────────────────────────────────────────────

async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query: CallbackQuery = update.callback_query
    await query.answer()

    data    = query.data or ""
    user    = query.from_user
    chat_id = query.message.chat_id

    try:
        if data == "noop":
            return

        # ── Lobby callbacks ──────────────────────────────────────
        elif data == "join":
            gm = registry.get(chat_id)
            if not gm or gm.phase != Phase.LOBBY:
                await query.answer("No active lobby!", show_alert=True)
                return
            if gm.join(user.id, user.full_name):
                count = gm.player_count()
                player_list = "\n".join(f"  ✅ {p.mention}" for p in gm.players.values())
                await query.edit_message_text(
                    f"🎮 <b>Lobby</b> — {count}/10 players\n\n"
                    f"<b>Players:</b>\n{player_list}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=lobby_keyboard(count, host=(user.id == gm.host_id)),
                )
            else:
                await query.answer("You're already in the lobby!", show_alert=True)

        elif data == "leave":
            gm = registry.get(chat_id)
            if gm and gm.leave(user.id):
                count = gm.player_count()
                await query.answer(f"👋 You left the lobby.")
                player_list = "\n".join(f"  ✅ {p.mention}" for p in gm.players.values())
                await query.edit_message_text(
                    f"🎮 <b>Lobby</b> — {count}/10 players\n\n"
                    f"<b>Players:</b>\n{player_list if player_list else '(empty)'}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=lobby_keyboard(count, host=(user.id == gm.host_id)),
                )
            else:
                await query.answer("You're not in the lobby.", show_alert=True)

        elif data == "add_ai":
            gm = registry.get(chat_id)
            if not gm or gm.phase != Phase.LOBBY:
                await query.answer("No active lobby!", show_alert=True)
                return
            if user.id != gm.host_id:
                await query.answer("Only the host can add AI players.", show_alert=True)
                return
            if gm.add_ai_player():
                count = gm.player_count()
                player_list = "\n".join(f"  ✅ {p.mention}" for p in gm.players.values())
                await query.edit_message_text(
                    f"🎮 <b>Lobby</b> — {count}/10 players\n\n"
                    f"<b>Players:</b>\n{player_list}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=lobby_keyboard(count, host=True),
                )
            else:
                await query.answer("Can't add more AI players (max 6 or lobby full).", show_alert=True)

        elif data == "start":
            gm = registry.get(chat_id)
            if not gm:
                await query.answer("No lobby found!", show_alert=True)
                return
            if user.id != gm.host_id:
                await query.answer("Only the host can start!", show_alert=True)
                return
            if gm.player_count() < 4:
                await query.answer("Need at least 4 players!", show_alert=True)
                return

            await query.edit_message_text(
                "⏳ Starting game...",
                parse_mode=ParseMode.HTML,
            )
            await gm.start_game()

            # DM roles to human players
            for player in gm.players.values():
                if player.is_ai:
                    continue
                try:
                    await ctx.application.bot.send_message(
                        chat_id=player.user_id,
                        text=gm.role_info(player.user_id),
                        parse_mode=ParseMode.HTML,
                    )
                    if player.role.has_night_action and gm.phase == Phase.NIGHT:
                        await _send_night_action_dm(ctx.application, gm, player.user_id)
                except Forbidden:
                    await ctx.application.bot.send_message(
                        chat_id=chat_id,
                        text=f"⚠️ Can't DM <b>{player.name}</b>. They need to /start the bot in private.",
                        parse_mode=ParseMode.HTML,
                    )

        # ── Night action callbacks (in DM) ───────────────────────
        elif data.startswith("night:"):
            target_id_str = data.split(":", 1)[1]
            if target_id_str == "skip":
                await query.edit_message_text("✅ You chose to skip your night action.")
                return

            target_id = int(target_id_str)

            # Find which game this user is in
            gm = _find_game_for_user(user.id)
            if not gm:
                await query.answer("You're not in an active game.", show_alert=True)
                return

            feedback = await gm.record_night_action(user.id, target_id)
            await query.edit_message_text(feedback, parse_mode=ParseMode.HTML)

        # ── Voting callbacks ─────────────────────────────────────
        elif data.startswith("vote:"):
            target_str = data.split(":", 1)[1]
            gm = registry.get(chat_id)
            if not gm:
                await query.answer("No active game.", show_alert=True)
                return

            if target_str == "skip":
                await query.answer("You abstained from voting.")
                return

            target_id = int(target_str)
            feedback = gm.cast_vote(user.id, target_id)
            await query.answer(feedback)

        # ── Day phase callbacks ──────────────────────────────────
        elif data == "skip_to_vote":
            gm = registry.get(chat_id)
            if not gm:
                return
            result = await gm.skip_to_vote(user.id)
            await query.answer(result)

        elif data == "player_list":
            gm = registry.get(chat_id)
            if not gm:
                await query.answer("No active game.", show_alert=True)
                return
            alive = gm.alive_players()
            names = "\n".join(f"✅ {p.mention}" for p in alive)
            await query.answer(f"Alive ({len(alive)}):\n{names}", show_alert=True)

        elif data == "my_role":
            gm = _find_game_for_user(user.id)
            if not gm:
                await query.answer("You're not in a game.", show_alert=True)
                return
            # Send private DM instead of alert for longer text
            try:
                await ctx.application.bot.send_message(
                    chat_id=user.id,
                    text=gm.role_info(user.id),
                    parse_mode=ParseMode.HTML,
                )
                await query.answer("Role info sent to your DM!")
            except Forbidden:
                info = gm.role_info(user.id)
                # strip HTML for alert
                plain = info.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
                await query.answer(plain[:200], show_alert=True)

        # ── Stats callbacks ──────────────────────────────────────
        elif data == "leaderboard" or data == "stats_menu":
            rows = await db.get_leaderboard()
            if not rows:
                await query.answer("No stats yet! Play some games first.", show_alert=True)
                return
            lines = ["🏆 <b>Leaderboard</b>\n"]
            medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 10
            for i, r in enumerate(rows):
                lines.append(
                    f"{medals[i]} <b>{r['username']}</b> — "
                    f"{r['wins']}W / {r['games_played']}G ({r['win_rate']}%)"
                )
            try:
                await ctx.application.bot.send_message(
                    chat_id=user.id,
                    text="\n".join(lines),
                    parse_mode=ParseMode.HTML,
                )
                await query.answer("Leaderboard sent to your DM!")
            except Forbidden:
                await query.answer("\n".join(lines)[:200], show_alert=True)

        elif data == "my_stats":
            stats = await db.get_player_stats(user.id)
            if not stats:
                await query.answer("You haven't played any games yet!", show_alert=True)
                return
            win_rate = round(stats["wins"] / max(stats["games_played"], 1) * 100, 1)
            role_lines = "\n".join(
                f"  {Role(r).emoji} {r}: {c} games"
                for r, c in sorted(stats["role_history"].items(), key=lambda x: -x[1])
            )
            msg = (
                f"📊 <b>Stats for {stats['username']}</b>\n\n"
                f"🎮 Games: <b>{stats['games_played']}</b>\n"
                f"🏆 Wins: <b>{stats['wins']}</b> ({win_rate}%)\n"
                f"💀 Losses: <b>{stats['losses']}</b>\n"
                f"🛡️ Survived: <b>{stats['survived_games']}</b>\n\n"
                f"<b>Role history:</b>\n{role_lines or '  None yet'}"
            )
            try:
                await ctx.application.bot.send_message(
                    chat_id=user.id, text=msg, parse_mode=ParseMode.HTML
                )
                await query.answer("Stats sent to your DM!")
            except Forbidden:
                await query.answer(msg[:200], show_alert=True)

        elif data == "global_stats":
            g = await db.get_global_stats()
            if not g or not g.get("total_games"):
                await query.answer("No global stats yet!", show_alert=True)
                return
            msg = (
                f"🌍 <b>Global Stats</b>\n\n"
                f"Total games: <b>{int(g['total_games'] or 0)}</b>\n"
                f"🏙️ Town wins: <b>{int(g['town_wins'] or 0)}</b>\n"
                f"🔫 Mafia wins: <b>{int(g['mafia_wins'] or 0)}</b>\n"
                f"🔪 Maniac wins: <b>{int(g['maniac_wins'] or 0)}</b>\n"
                f"Avg players/game: <b>{round(g['avg_players'] or 0, 1)}</b>\n"
                f"Avg rounds/game: <b>{round(g['avg_rounds'] or 0, 1)}</b>"
            )
            await query.answer(msg[:200], show_alert=True)

        elif data == "howto":
            await query.answer(
                "🎭 Mafia: Night=kills, Day=discuss+vote.\n"
                "Town wins by eliminating all Mafia+Maniac.\n"
                "Mafia wins when equal to Town.\n"
                "Maniac wins as last survivor.",
                show_alert=True
            )

        elif data == "new_game":
            await query.answer("Use /newgame in a group chat to start!")

    except Exception as e:
        logger.exception("Error in callback_handler (data=%s): %s", data, e)
        try:
            await query.answer("⚠️ An error occurred. Please try again.", show_alert=True)
        except Exception:
            pass


# ──────────────────────────────────────────────
# Utility
# ──────────────────────────────────────────────

def _find_game_for_user(user_id: int) -> Optional[GameManager]:
    """Search all active games for a user."""
    for gm in registry._games.values():
        if user_id in gm.players:
            return gm
    return None


# ──────────────────────────────────────────────
# Error handler
# ──────────────────────────────────────────────

async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled exception: %s", ctx.error, exc_info=ctx.error)

    # Silently ignore user-facing errors (connection resets, etc.)
    if isinstance(ctx.error, (Forbidden, BadRequest)):
        return

    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ An unexpected error occurred. The developers have been notified."
            )
        except Exception:
            pass


# ──────────────────────────────────────────────
# Periodic cleanup task
# ──────────────────────────────────────────────

async def cleanup_task(app: Application) -> None:
    """Runs every 5 minutes to clean up ended games."""
    while True:
        await asyncio.sleep(300)
        removed = registry.cleanup_ended()
        if removed:
            logger.info("Cleaned up %d ended game(s)", removed)


# ──────────────────────────────────────────────
# Bot setup
# ──────────────────────────────────────────────

async def post_init(app: Application) -> None:
    await db.connect()
    logger.info("Database ready.")

    # Set bot commands
    await app.bot.set_my_commands([
        BotCommand("newgame",   "Create a new game lobby"),
        BotCommand("join",      "Join the current lobby"),
        BotCommand("leave",     "Leave the lobby"),
        BotCommand("startgame", "Start the game (host only)"),
        BotCommand("endgame",   "Force-end current game"),
        BotCommand("players",   "Show player list"),
        BotCommand("myrole",    "Show your role"),
        BotCommand("stats",     "View statistics"),
        BotCommand("help",      "Show help"),
    ])
    logger.info("Bot commands registered.")

    # Start background cleanup
    asyncio.create_task(cleanup_task(app))


async def post_shutdown(app: Application) -> None:
    await db.close()
    logger.info("Database closed.")


def main() -> None:
    token = os.environ.get("BOT_TOKEN")
    if not token:
        logger.error("BOT_TOKEN environment variable not set!")
        sys.exit(1)

    app = (
        ApplicationBuilder()
        .token(token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # Register handlers
    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("help",      cmd_help))
    app.add_handler(CommandHandler("newgame",   cmd_newgame))
    app.add_handler(CommandHandler("join",      cmd_join))
    app.add_handler(CommandHandler("leave",     cmd_leave))
    app.add_handler(CommandHandler("startgame", cmd_startgame))
    app.add_handler(CommandHandler("endgame",   cmd_endgame))
    app.add_handler(CommandHandler("players",   cmd_players))
    app.add_handler(CommandHandler("myrole",    cmd_myrole))
    app.add_handler(CommandHandler("stats",     cmd_stats))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_error_handler(error_handler)

    logger.info("🎭 ULTRA PRO MAFIA BOT starting...")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
