"""
bot.py — ULTRA PRO MAFIA BOT v3
======================================
Features:
  ✅ 10 roles (Civilian, Mafia, Doctor, Detective, Sniper, Maniac,
                Mayor, Bodyguard, Jester, Serial Killer)
  ✅ ELO rating system
  ✅ Achievement system (10 achievements)
  ✅ Last Will system
  ✅ Mafia night chat
  ✅ Ghost chat (dead players)
  ✅ Defense speech timer
  ✅ Mayor double vote
  ✅ Lover mechanic
  ✅ AI with personalities + day bluffs
  ✅ 3 languages (uz/ru/en)
  ✅ Game log & replay (/replay)
  ✅ Rate limiting
  ✅ Health check endpoint
  ✅ Auto cleanup
  ✅ Error handling everywhere

python-telegram-bot==21.6 | Python 3.10-3.13
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from collections import defaultdict
from typing import Optional

import aiohttp
from aiohttp import web
from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import Forbidden, BadRequest, TelegramError
from telegram.ext import (
    Application, ApplicationBuilder, CallbackQueryHandler,
    CommandHandler, ContextTypes, MessageHandler, filters,
)

from config import cfg, t, TEXTS
from database import Database
from game_manager import GameManager, GameRegistry
from keyboards import (
    language_keyboard, lobby_keyboard, main_menu_keyboard,
    night_action_keyboard, stats_keyboard, vote_keyboard,
)
from models import ACHIEVEMENTS, Phase, Role
from role_engine import RoleEngine

# ══════════════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════════════

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

# ══════════════════════════════════════════════════════════════════
# GLOBALS
# ══════════════════════════════════════════════════════════════════

db_instance = Database()
registry    = GameRegistry()

# Rate limiter: user_id -> list of timestamps
_rate_cache: dict[int, list[float]] = defaultdict(list)

# Users waiting to write last will
_will_pending: set[int] = set()
# Users waiting for lover link (host sets lovers)
_lover_setup: dict[int, Optional[int]] = {}


# ══════════════════════════════════════════════════════════════════
# RATE LIMITER
# ══════════════════════════════════════════════════════════════════

def _check_rate(user_id: int) -> bool:
    now = time.time()
    history = _rate_cache[user_id]
    history[:] = [ts for ts in history if now - ts < cfg.RATE_LIMIT_WINDOW]
    if len(history) >= cfg.RATE_LIMIT_MESSAGES:
        return False
    history.append(now)
    return True


# ══════════════════════════════════════════════════════════════════
# SEND HELPER
# ══════════════════════════════════════════════════════════════════

async def _send(
    app: Application, chat_id: int, text: str,
    vote_targets: Optional[list] = None,
    anonymous: bool = False,
    reply_markup_chat=None,
    **kwargs
) -> None:
    try:
        markup = None
        if vote_targets is not None:
            markup = vote_keyboard(vote_targets, anonymous)
        elif reply_markup_chat is not None:
            markup = reply_markup_chat
        await app.bot.send_message(
            chat_id=chat_id, text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
        )
    except Forbidden:
        logger.warning("Forbidden: %s", chat_id)
    except BadRequest as e:
        logger.warning("BadRequest %s: %s", chat_id, e)
    except TelegramError as e:
        logger.error("TelegramError %s: %s", chat_id, e)


def make_send_fn(app: Application):
    async def _s(chat_id: int, text: str, **kw) -> None:
        await _send(app, chat_id, text, **kw)
    return _s


# ══════════════════════════════════════════════════════════════════
# NIGHT DM HELPER
# ══════════════════════════════════════════════════════════════════

async def send_night_dm(app: Application, gm: GameManager, user_id: int) -> None:
    player = gm.get_player(user_id)
    if not player or not player.is_alive or not player.role.has_night_action:
        return
    if player.role == Role.SNIPER and player.sniper_used:
        await _send(app, user_id, "🎯 Siz allaqachon o'q ishlatdingiz. Bu kecha dam oling.")
        return

    targets = [(p.user_id, p.mention) for p in gm.alive_players() if p.user_id != user_id]
    verb = RoleEngine.get_night_action_verb(player.role)

    try:
        await app.bot.send_message(
            chat_id=user_id,
            text=t(gm.lang, "night_action", verb=verb),
            parse_mode=ParseMode.HTML,
            reply_markup=night_action_keyboard(targets, player.role.emoji),
        )
    except (Forbidden, BadRequest) as e:
        logger.warning("Night DM failed %s: %s", user_id, e)


# ══════════════════════════════════════════════════════════════════
# COMMANDS
# ══════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    lang = await db_instance.get_user_lang(user.id)
    await update.message.reply_text(
        t(lang, "welcome"),
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard(),
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    lang = await db_instance.get_user_lang(user.id)
    help_text = {
        "uz": """🎭 <b>MAFIA BOT v3 — Buyruqlar</b>

<b>Guruh:</b>
/newgame — Lobby ochish
/join — Qo'shilish  
/leave — Chiqish
/startgame — Boshlash (host)
/endgame — Majburiy tugatish
/players — O'yinchilar ro'yxati
/stats — Statistika
/myrole — Mening rolim
/replay [ID] — O'yin tarixi
/setwill — Vasiyat yozish
/setlang — Til o'zgartirish

<b>Rollar:</b>
👤 Fuqaro • 🔫 Mafiya • 💊 Doktor • 🔍 Detektiv
🎯 Snayper • 🔪 Maniak • 👑 Mayor • 🛡️ Taniqchi
🤡 Jester • ⚔️ Serial Killer""",
        "en": """🎭 <b>MAFIA BOT v3 — Commands</b>

<b>Group:</b>
/newgame — Create lobby
/join — Join lobby
/leave — Leave lobby
/startgame — Start (host)
/endgame — Force end
/players — Player list
/stats — Statistics
/myrole — My role
/replay [ID] — Game replay
/setwill — Write last will
/setlang — Change language""",
        "ru": """🎭 <b>MAFIA BOT v3 — Команды</b>

<b>Группа:</b>
/newgame — Создать лобби
/join — Присоединиться
/leave — Выйти
/startgame — Начать (хост)
/endgame — Принудительно завершить
/players — Список игроков
/stats — Статистика
/myrole — Моя роль
/replay [ID] — Повтор игры
/setwill — Последняя воля
/setlang — Язык""",
    }
    await update.message.reply_text(
        help_text.get(lang, help_text["en"]),
        parse_mode=ParseMode.HTML,
    )


async def cmd_newgame(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_rate(update.effective_user.id):
        return
    chat_id = update.effective_chat.id
    user    = update.effective_user
    lang    = await db_instance.get_user_lang(user.id)

    registry.cleanup_ended()
    existing = registry.get(chat_id)
    if existing and not existing.is_ended():
        await update.message.reply_text(t(lang, "already_running"))
        return

    send_fn = make_send_fn(ctx.application)
    gm = registry.create(chat_id, send_fn, db_instance, lang)
    gm.join(user.id, user.full_name)

    await update.message.reply_text(
        f"🎮 <b>Yangi o'yin lobbisi!</b>\n🏠 Host: <b>{user.full_name}</b>\n\n"
        f"O'yinchilar (1/15):\n  ✅ {user.full_name}\n\nKamida 4 o'yinchi kerak.",
        parse_mode=ParseMode.HTML,
        reply_markup=lobby_keyboard(1, host=True),
    )


async def cmd_join(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_rate(update.effective_user.id):
        return
    chat_id = update.effective_chat.id
    user    = update.effective_user
    gm = registry.get(chat_id)

    if not gm or gm.is_ended():
        lang = await db_instance.get_user_lang(user.id)
        await update.message.reply_text(t(lang, "no_lobby"))
        return
    if gm.phase != Phase.LOBBY:
        await update.message.reply_text("❌ O'yin allaqachon boshlangan!")
        return
    if gm.join(user.id, user.full_name):
        count = gm.player_count()
        plist = "\n".join(f"  ✅ {p.mention}" for p in gm.players.values())
        await update.message.reply_text(
            t(gm.lang, "join_success", name=user.full_name, n=count) +
            f"\n\n<b>O'yinchilar:</b>\n{plist}",
            parse_mode=ParseMode.HTML,
            reply_markup=lobby_keyboard(count, host=(user.id == gm.host_id)),
        )
    else:
        await update.message.reply_text("⚠️ Allaqachon lobbida yoki joy yo'q.")


async def cmd_leave(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user    = update.effective_user
    gm = registry.get(chat_id)
    if not gm or gm.phase != Phase.LOBBY:
        await update.message.reply_text("❌ Faol lobby yo'q.")
        return
    if gm.leave(user.id):
        count = gm.player_count()
        await update.message.reply_text(
            f"👋 <b>{user.full_name}</b> chiqdi. ({count}/15)",
            parse_mode=ParseMode.HTML,
            reply_markup=lobby_keyboard(count, host=(user.id == gm.host_id)),
        )
    else:
        await update.message.reply_text("❌ Lobbida emassiz.")


async def cmd_startgame(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user    = update.effective_user
    gm = registry.get(chat_id)

    if not gm:
        await update.message.reply_text(t(gm.lang if gm else "uz", "no_lobby"))
        return
    if user.id != gm.host_id:
        await update.message.reply_text(t(gm.lang, "not_host"))
        return
    if gm.player_count() < cfg.MIN_PLAYERS:
        await update.message.reply_text(t(gm.lang, "need_players"))
        return

    await gm.start_game()

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
                await send_night_dm(ctx.application, gm, player.user_id)
        except Forbidden:
            await update.message.reply_text(
                f"⚠️ <b>{player.name}</b> ga xabar yuborib bo'lmadi. "
                f"Avval botga /start yuboring!",
                parse_mode=ParseMode.HTML,
            )


async def cmd_endgame(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user    = update.effective_user
    gm = registry.get(chat_id)
    if not gm:
        await update.message.reply_text("❌ Faol o'yin yo'q.")
        return
    try:
        member = await ctx.application.bot.get_chat_member(chat_id, user.id)
        is_admin = member.status in ("administrator", "creator")
    except Exception:
        is_admin = False
    if user.id != gm.host_id and not is_admin:
        await update.message.reply_text(t(gm.lang, "not_host"))
        return
    registry.remove(chat_id)
    await update.message.reply_text("🛑 O'yin majburiy tugatildi.")


async def cmd_players(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    gm = registry.get(update.effective_chat.id)
    if not gm:
        await update.message.reply_text("❌ Faol o'yin yo'q.")
        return
    lines = [f"👥 <b>O'yin #{gm.game_id} | Faza: {gm.phase.name}</b>\n"]
    for p in gm.players.values():
        icon = "✅" if p.is_alive else "💀"
        lines.append(f"  {icon} {p.mention}")
    lines.append(f"\n⏱️ Tur: {gm.round}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_myrole(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    gm = registry.find_game_for_user(user.id)
    if not gm:
        await update.message.reply_text("❌ Siz faol o'yinda emassiz.")
        return
    await update.message.reply_text(gm.role_info(user.id), parse_mode=ParseMode.HTML)


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    lang = await db_instance.get_user_lang(user.id)
    await update.message.reply_text(
        "📊 <b>Statistika</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=stats_keyboard(),
    )


async def cmd_replay(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    args = ctx.args
    if not args:
        await update.message.reply_text("❌ Foydalanish: /replay GAME_ID")
        return
    game_id = args[0].upper()
    data = await db_instance.get_game_log(game_id)
    if not data:
        await update.message.reply_text(f"❌ O'yin #{game_id} topilmadi.")
        return

    lines = [f"📼 <b>O'yin #{game_id} replays</b>\n",
             f"👥 {data['player_count']} o'yinchi | 🏆 G'olib: {data['winner']} | ⏱️ {data['rounds']} tur\n"]

    for entry in data["game_log"][:20]:  # max 20 events
        lines.append(f"[{entry['t'][11:16]}] Tur {entry['r']}: {entry['e']} — {entry['d']}")

    if len(data["game_log"]) > 20:
        lines.append(f"... va yana {len(data['game_log'])-20} hodisa")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_setwill(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    _will_pending.add(user.id)
    await update.message.reply_text(
        "📜 <b>Vasiyatingizni yozing:</b>\n"
        "O'lgandan keyin hammaga ko'rsatiladi. (Keyingi xabar sifatida yuboring)",
        parse_mode=ParseMode.HTML,
    )


async def cmd_setlang(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🌐 <b>Til tanlang / Choose language / Выберите язык:</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=language_keyboard(),
    )


async def cmd_setlovers(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Host sets up lovers: /setlovers @user1 @user2"""
    chat_id = update.effective_chat.id
    user    = update.effective_user
    gm = registry.get(chat_id)
    if not gm or user.id != gm.host_id or gm.phase != Phase.LOBBY:
        await update.message.reply_text("❌ Faqat host lobby paytida o'rnatishi mumkin.")
        return
    await update.message.reply_text(
        "💕 Lover mexanikasi:\nIkki o'yinchi biriktiriladi — biri o'lsa ikkinchisi ham o'ladi.\n"
        "Hozircha bu xususiyat /newgame dan keyin avtomatik o'rnatilmaydi."
    )


async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    try:
        chat_id = update.effective_chat.id
        member  = await ctx.application.bot.get_chat_member(chat_id, user.id)
        if member.status not in ("administrator", "creator"):
            return
    except Exception:
        return

    stats = await db_instance.get_global_stats()
    text = (
        f"🔧 <b>Admin Panel</b>\n\n"
        f"🎮 Faol o'yinlar: <b>{registry.active_count()}</b>\n"
        f"📊 Jami o'yinlar: <b>{int(stats.get('total_games') or 0)}</b>\n"
        f"👥 O'rtacha o'yinchi: <b>{round(stats.get('avg_players') or 0, 1)}</b>\n\n"
        f"Buyruqlar:\n"
        f"/endgame — O'yinni tugatish\n"
        f"/players — O'yinchilar\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ══════════════════════════════════════════════════════════════════
# MESSAGE HANDLER (for last will, mafia/ghost chat)
# ══════════════════════════════════════════════════════════════════

async def message_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    user = update.effective_user
    text = update.message.text

    # Last will writing
    if user.id in _will_pending:
        _will_pending.discard(user.id)
        will = text[:200]
        await db_instance.save_last_will(user.id, will)
        # Also update in-game if player is in game
        gm = registry.find_game_for_user(user.id)
        if gm:
            p = gm.get_player(user.id)
            if p:
                p.last_will = will
        await update.message.reply_text("📜 Vasiyat saqlandi!")
        return

    # Mafia chat (only in private, only if in a game as mafia)
    if update.effective_chat.type == "private":
        gm = registry.find_game_for_user(user.id)
        if gm and gm.phase == Phase.NIGHT:
            p = gm.get_player(user.id)
            if p and p.role == Role.MAFIA and p.is_alive:
                sent = await gm.relay_mafia_chat(user.id, text)
                if sent:
                    await update.message.reply_text("🔫 Mafiya chatiga yuborildi.", quote=True)
                    return

        # Ghost chat
        if gm and user.id in gm._ghost_members:
            sent = await gm.relay_ghost_chat(user.id, text)
            if sent:
                await update.message.reply_text("👻 Arvohlar chatiga yuborildi.", quote=True)


# ══════════════════════════════════════════════════════════════════
# CALLBACK HANDLER
# ══════════════════════════════════════════════════════════════════

async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data    = query.data or ""
    user    = query.from_user
    chat_id = query.message.chat_id

    if not _check_rate(user.id):
        await query.answer("⚠️ Juda tez! Biroz kuting.", show_alert=True)
        return

    try:
        # ── Language ────────────────────────────────────────────
        if data.startswith("lang:"):
            lang = data.split(":")[1]
            if lang in ("uz", "ru", "en"):
                await db_instance.set_user_lang(user.id, lang)
                names = {"uz": "O'zbek 🇺🇿", "ru": "Русский 🇷🇺", "en": "English 🇬🇧"}
                await query.edit_message_text(
                    f"✅ Til o'zgartirildi: <b>{names[lang]}</b>",
                    parse_mode=ParseMode.HTML
                )
            return

        elif data == "lang_menu":
            await query.edit_message_text(
                "🌐 <b>Til tanlang:</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=language_keyboard()
            )
            return

        # ── Lobby ────────────────────────────────────────────────
        elif data == "noop":
            return

        elif data == "join":
            gm = registry.get(chat_id)
            if not gm or gm.phase != Phase.LOBBY:
                await query.answer("Faol lobby yo'q!", show_alert=True); return
            if gm.join(user.id, user.full_name):
                count = gm.player_count()
                plist = "\n".join(f"  ✅ {p.mention}" for p in gm.players.values())
                await query.edit_message_text(
                    f"🎮 <b>Lobby</b> — {count}/15\n\n<b>O'yinchilar:</b>\n{plist}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=lobby_keyboard(count, host=(user.id == gm.host_id)),
                )
            else:
                await query.answer("Allaqachon qo'shilgansiz yoki joy yo'q!", show_alert=True)

        elif data == "leave":
            gm = registry.get(chat_id)
            if gm and gm.leave(user.id):
                count = gm.player_count()
                plist = "\n".join(f"  ✅ {p.mention}" for p in gm.players.values()) or "(bo'sh)"
                await query.edit_message_text(
                    f"🎮 <b>Lobby</b> — {count}/15\n\n<b>O'yinchilar:</b>\n{plist}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=lobby_keyboard(count, host=(user.id == gm.host_id)),
                )
            else:
                await query.answer("Lobbida emassiz.", show_alert=True)

        elif data == "add_ai":
            gm = registry.get(chat_id)
            if not gm or gm.phase != Phase.LOBBY:
                await query.answer("Faol lobby yo'q!", show_alert=True); return
            if user.id != gm.host_id:
                await query.answer("Faqat host AI qo'sha oladi!", show_alert=True); return
            if gm.add_ai_player():
                count = gm.player_count()
                plist = "\n".join(f"  ✅ {p.mention}" for p in gm.players.values())
                await query.edit_message_text(
                    f"🎮 <b>Lobby</b> — {count}/15\n\n<b>O'yinchilar:</b>\n{plist}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=lobby_keyboard(count, host=True),
                )
            else:
                await query.answer("Maksimal AI yoki to'liq.", show_alert=True)

        elif data == "start":
            gm = registry.get(chat_id)
            if not gm:
                await query.answer("Lobby topilmadi!", show_alert=True); return
            if user.id != gm.host_id:
                await query.answer("Faqat host boshlay oladi!", show_alert=True); return
            if gm.player_count() < cfg.MIN_PLAYERS:
                await query.answer(f"Kamida {cfg.MIN_PLAYERS} o'yinchi kerak!", show_alert=True); return

            await query.edit_message_text("⏳ O'yin boshlanmoqda...", parse_mode=ParseMode.HTML)
            await gm.start_game()

            for player in gm.players.values():
                if player.is_ai: continue
                try:
                    await ctx.application.bot.send_message(
                        chat_id=player.user_id,
                        text=gm.role_info(player.user_id),
                        parse_mode=ParseMode.HTML,
                    )
                    if player.role.has_night_action and gm.phase == Phase.NIGHT:
                        await send_night_dm(ctx.application, gm, player.user_id)
                except Forbidden:
                    await ctx.application.bot.send_message(
                        chat_id=chat_id,
                        text=f"⚠️ <b>{player.name}</b> ga DM yuborib bo'lmadi. /start ni botga yuboring!",
                        parse_mode=ParseMode.HTML,
                    )

        # ── Night actions ────────────────────────────────────────
        elif data.startswith("night:"):
            ts = data.split(":", 1)[1]
            if ts == "skip":
                await query.edit_message_text("✅ Kecha harakati o'tkazib yuborildi.")
                return
            gm = registry.find_game_for_user(user.id)
            if not gm:
                await query.answer("O'yinda emassiz.", show_alert=True); return
            fb = await gm.record_night_action(user.id, int(ts))
            await query.edit_message_text(fb, parse_mode=ParseMode.HTML)

        # ── Voting ───────────────────────────────────────────────
        elif data.startswith("vote:"):
            ts = data.split(":", 1)[1]
            gm = registry.get(chat_id)
            if not gm:
                await query.answer("Faol o'yin yo'q.", show_alert=True); return
            if ts == "skip":
                await query.answer("Betaraf qoldingiz.")
                return
            fb = gm.cast_vote(user.id, int(ts))
            await query.answer(fb)

        # ── Day controls ─────────────────────────────────────────
        elif data == "skip_to_vote":
            gm = registry.get(chat_id)
            if gm:
                result = await gm.skip_to_vote(user.id)
                await query.answer(result)

        elif data == "player_list":
            gm = registry.get(chat_id)
            if not gm:
                await query.answer("Faol o'yin yo'q.", show_alert=True); return
            alive = gm.alive_players()
            names = "\n".join(f"✅ {p.mention}" for p in alive)
            await query.answer(f"Tirik ({len(alive)}):\n{names}"[:200], show_alert=True)

        elif data == "my_role":
            gm = registry.find_game_for_user(user.id)
            if not gm:
                await query.answer("O'yinda emassiz.", show_alert=True); return
            try:
                await ctx.application.bot.send_message(
                    chat_id=user.id,
                    text=gm.role_info(user.id),
                    parse_mode=ParseMode.HTML,
                )
                await query.answer("DM ga yuborildi!")
            except Forbidden:
                await query.answer(gm.role_info(user.id)[:200], show_alert=True)

        elif data == "mayor_reveal":
            gm = registry.find_game_for_user(user.id)
            if not gm:
                await query.answer("O'yinda emassiz.", show_alert=True); return
            result = await gm.mayor_reveal(user.id)
            await query.answer(result)

        elif data == "write_will":
            _will_pending.add(user.id)
            await query.answer("Vasiyatingizni yozing (keyingi xabar)!")

        # ── Stats ────────────────────────────────────────────────
        elif data in ("leaderboard", "stats_menu"):
            rows = await db_instance.get_leaderboard()
            if not rows:
                await query.answer("Hali statistika yo'q!", show_alert=True); return
            medals = ["🥇","🥈","🥉"] + ["🏅"]*20
            lines = ["🏆 <b>ELO Reytingi</b>\n"]
            for i, r in enumerate(rows):
                lines.append(
                    f"{medals[i]} <b>{r['username']}</b> — "
                    f"ELO: {r['elo']} | {r['wins']}G ({r['win_rate']}%)"
                )
            try:
                await ctx.application.bot.send_message(
                    chat_id=user.id, text="\n".join(lines), parse_mode=ParseMode.HTML
                )
                await query.answer("DM ga yuborildi!")
            except Forbidden:
                await query.answer("\n".join(lines)[:200], show_alert=True)

        elif data == "my_stats":
            lang = await db_instance.get_user_lang(user.id)
            stats = await db_instance.get_player_stats(user.id)
            if not stats:
                await query.answer("Hali o'yin o'ynamagansiz!", show_alert=True); return
            wr = round(stats["wins"] / max(stats["games_played"], 1) * 100, 1)
            role_lines = "\n".join(
                f"  {Role(r).emoji} {r}: {c}"
                for r, c in sorted(stats["role_history"].items(), key=lambda x: -x[1])
            )
            ach_line = " ".join(
                ACHIEVEMENTS[k].emoji for k in stats["achievements"] if k in ACHIEVEMENTS
            ) or "—"
            msg = (
                f"📊 <b>{stats['username']}</b>\n\n"
                f"🎮 O'yinlar: {stats['games_played']}\n"
                f"🏆 G'alabalar: {stats['wins']} ({wr}%)\n"
                f"💀 Mag'lubiyat: {stats['losses']}\n"
                f"🛡️ Omon qoldi: {stats['survived_games']}\n"
                f"📈 ELO: <b>{stats['elo']}</b>\n"
                f"🔥 Eng uzun streak: {stats['best_streak']}\n\n"
                f"<b>Rollar:</b>\n{role_lines or '—'}\n\n"
                f"<b>Yutuqlar:</b> {ach_line}"
            )
            try:
                await ctx.application.bot.send_message(
                    chat_id=user.id, text=msg, parse_mode=ParseMode.HTML
                )
                await query.answer("Stats DM ga yuborildi!")
            except Forbidden:
                await query.answer(msg[:200], show_alert=True)

        elif data == "my_achievements":
            stats = await db_instance.get_player_stats(user.id)
            if not stats:
                await query.answer("Hali yutuq yo'q!", show_alert=True); return
            earned = stats["achievements"]
            lines = ["🏅 <b>Yutuqlar</b>\n"]
            for key, ach in ACHIEVEMENTS.items():
                if key in earned:
                    lines.append(f"{ach.emoji} <b>{ach.name}</b> — {ach.desc}")
                else:
                    lines.append(f"🔒 <i>{ach.name}</i>")
            try:
                await ctx.application.bot.send_message(
                    chat_id=user.id,
                    text="\n".join(lines),
                    parse_mode=ParseMode.HTML,
                )
                await query.answer("Yutuqlar DM ga yuborildi!")
            except Forbidden:
                await query.answer("\n".join(lines)[:200], show_alert=True)

        elif data == "global_stats":
            g = await db_instance.get_global_stats()
            if not g or not g.get("total_games"):
                await query.answer("Global statistika yo'q!", show_alert=True); return
            msg = (
                f"🌍 Global Statistika\n"
                f"Jami o'yinlar: {int(g['total_games'] or 0)}\n"
                f"🏙️ Shahar: {int(g['town_wins'] or 0)}\n"
                f"🔫 Mafiya: {int(g['mafia_wins'] or 0)}\n"
                f"🔪 Solo: {int(g['solo_wins'] or 0)}\n"
                f"🤡 Jester: {int(g['jester_wins'] or 0)}\n"
                f"O'rtacha o'yinchi: {round(g['avg_players'] or 0, 1)}\n"
                f"O'rtacha tur: {round(g['avg_rounds'] or 0, 1)}"
            )
            await query.answer(msg[:200], show_alert=True)

        elif data == "howto":
            await query.answer(
                "🌙 Kecha: rollar harakat qiladi\n"
                "☀️ Kun: muhokama\n🗳️ Ovoz: kimdir chiqariladi\n"
                "🏙️ Shahar g'alaba: Mafiya+Maniak yo'qolsa\n"
                "🔫 Mafiya g'alaba: Shahar tenglashsa\n"
                "🤡 Jester: chiqarilsa g'olib",
                show_alert=True
            )

        elif data == "new_game":
            await query.answer("Guruh chatda /newgame yuboring!")

    except Exception as e:
        logger.exception("Callback error (data=%s): %s", data, e)
        try:
            await query.answer("⚠️ Xatolik yuz berdi.", show_alert=True)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════
# ERROR HANDLER
# ══════════════════════════════════════════════════════════════════

async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled: %s", ctx.error, exc_info=ctx.error)
    if isinstance(ctx.error, (Forbidden, BadRequest)):
        return
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("⚠️ Kutilmagan xatolik.")
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════
# HEALTH CHECK (for Railway/Render uptime)
# ══════════════════════════════════════════════════════════════════

async def health_check(request) -> web.Response:
    return web.json_response({
        "status": "ok",
        "active_games": registry.active_count(),
    })


async def start_health_server() -> None:
    app = web.Application()
    app.router.add_get("/health", health_check)
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", cfg.HEALTH_CHECK_PORT)
    await site.start()
    logger.info("Health check running on port %d", cfg.HEALTH_CHECK_PORT)


# ══════════════════════════════════════════════════════════════════
# STARTUP / SHUTDOWN
# ══════════════════════════════════════════════════════════════════

async def post_init(app: Application) -> None:
    await db_instance.connect()
    logger.info("Database ready.")

    await app.bot.set_my_commands([
        BotCommand("newgame",   "Yangi o'yin lobbisi"),
        BotCommand("join",      "Lobbyga qo'shilish"),
        BotCommand("leave",     "Lobbydan chiqish"),
        BotCommand("startgame", "O'yinni boshlash (host)"),
        BotCommand("endgame",   "O'yinni tugatish"),
        BotCommand("players",   "O'yinchilar ro'yxati"),
        BotCommand("myrole",    "Mening rolim"),
        BotCommand("stats",     "Statistika"),
        BotCommand("replay",    "O'yin tarixi"),
        BotCommand("setwill",   "Vasiyat yozish"),
        BotCommand("setlang",   "Til o'zgartirish"),
        BotCommand("help",      "Yordam"),
    ])

    # Start health check server
    try:
        await start_health_server()
    except Exception as e:
        logger.warning("Health server failed: %s", e)

    # Background cleanup
    async def _cleanup():
        while True:
            await asyncio.sleep(300)
            n = registry.cleanup_ended()
            if n:
                logger.info("Cleaned %d games", n)

    asyncio.create_task(_cleanup())
    logger.info("🎭 ULTRA PRO MAFIA BOT v3 ready!")


async def post_shutdown(app: Application) -> None:
    await db_instance.close()
    logger.info("Shutdown complete.")


# ══════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════

def main() -> None:
    token = os.environ.get("BOT_TOKEN") or cfg.BOT_TOKEN
    if not token:
        logger.error("BOT_TOKEN topilmadi!")
        sys.exit(1)

    app = (
        ApplicationBuilder()
        .token(token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # Commands
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
    app.add_handler(CommandHandler("replay",    cmd_replay))
    app.add_handler(CommandHandler("setwill",   cmd_setwill))
    app.add_handler(CommandHandler("setlang",   cmd_setlang))
    app.add_handler(CommandHandler("admin",     cmd_admin))
    app.add_handler(CommandHandler("setlovers", cmd_setlovers))

    # Callbacks + messages
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    app.add_error_handler(error_handler)

    logger.info("🎭 Starting ULTRA PRO MAFIA BOT v3...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
