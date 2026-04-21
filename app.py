import logging
import random
import asyncio
import sqlite3
from collections import defaultdict
from uuid import uuid4

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import *

TOKEN = "8779339832:AAEVc-vTgxPzDEUNG6vDDecka4QVxOelMfw"

logging.basicConfig(level=logging.INFO)

# =========================
# DATABASE
# =========================
conn = sqlite3.connect("mafia.db")
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS users(
    user_id INTEGER PRIMARY KEY,
    name TEXT,
    xp INTEGER DEFAULT 0,
    wins INTEGER DEFAULT 0,
    losses INTEGER DEFAULT 0
)
""")
conn.commit()


def update_user(uid, name, win):
    cur.execute("INSERT OR IGNORE INTO users(user_id,name) VALUES (?,?)", (uid, name))
    if win:
        cur.execute("UPDATE users SET wins=wins+1,xp=xp+15 WHERE user_id=?", (uid,))
    else:
        cur.execute("UPDATE users SET losses=losses+1,xp=xp+5 WHERE user_id=?", (uid,))
    conn.commit()


def get_profile(uid):
    cur.execute("SELECT wins,losses,xp FROM users WHERE user_id=?", (uid,))
    return cur.fetchone()


def get_top():
    cur.execute("SELECT name,xp FROM users ORDER BY xp DESC LIMIT 10")
    return cur.fetchall()


# =========================
# GAME MANAGER
# =========================
class Game:
    def __init__(self, chat_id, creator):
        self.chat_id = chat_id
        self.creator = creator
        self.players = {}
        self.roles = {}
        self.alive = set()
        self.room_code = str(uuid4())[:6]
        self.votes = defaultdict(int)
        self.voted = set()
        self.night_actions = {}
        self.message_id = None

    def add_player(self, user):
        if user.id not in self.players:
            self.players[user.id] = user.first_name
            self.alive.add(user.id)

    def add_ai(self):
        ai_id = random.randint(1000000, 9999999)
        self.players[ai_id] = f"🤖AI_{ai_id%100}"
        self.alive.add(ai_id)

    def assign_roles(self):
        players = list(self.players.keys())
        random.shuffle(players)

        mafia_count = max(1, len(players)//4)

        roles = (
            ["mafia"] * mafia_count +
            ["doctor", "detective", "sniper", "maniac"]
        )
        roles += ["civilian"] * (len(players)-len(roles))
        random.shuffle(roles)

        for p, r in zip(players, roles):
            self.roles[p] = r


games = {}

# =========================
# COMMANDS
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔥 SUPER ULTIMATE MAFIA\n/create")


async def create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user

    game = Game(chat_id, user.id)
    games[chat_id] = game

    await update.message.reply_text(
        f"🆕 Room\n🔑 {game.room_code}\n/join {game.room_code}"
    )


async def join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    game = games.get(update.effective_chat.id)
    user = update.effective_user

    if context.args and context.args[0] != game.room_code:
        return await update.message.reply_text("❌ Code xato")

    game.add_player(user)
    await update.message.reply_text(f"✅ {user.first_name}")


async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    p = get_profile(update.effective_user.id)
    if not p:
        return await update.message.reply_text("No data")

    await update.message.reply_text(
        f"🏆 {p[0]} | 💀 {p[1]} | XP {p[2]}"
    )


async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = get_top()
    text = "🏆 TOP:\n"
    for n, xp in data:
        text += f"{n} - {xp}\n"
    await update.message.reply_text(text)


async def start_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    game = games.get(update.effective_chat.id)

    if len(game.players) < 4:
        # AI qo‘shamiz
        for _ in range(4 - len(game.players)):
            game.add_ai()

    game.assign_roles()

    for p, r in game.roles.items():
        if p < 1000000000:
            try:
                await context.bot.send_message(p, f"🎭 {r}")
            except:
                pass

    await loop(context, game)


# =========================
# LOOP
# =========================
async def loop(context, game):
    while True:
        await night(context, game)
        if await check_win(context, game): break

        await day(context, game)
        if await check_win(context, game): break


# =========================
# NIGHT
# =========================
async def night(context, game):
    game.night_actions = {}

    # AI actions
    for p in game.alive:
        if p > 1000000:
            role = game.roles[p]
            target = random.choice(list(game.alive))
            game.night_actions[role] = target

    # players
    for p in game.alive:
        if p > 1000000:
            continue

        keyboard = []
        for t in game.alive:
            if t != p:
                keyboard.append([InlineKeyboardButton(
                    game.players[t],
                    callback_data=f"n:{game.roles[p]}:{t}:{game.chat_id}"
                )])

        try:
            await context.bot.send_message(
                p,
                "🌙 Choose:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except:
            pass

    await countdown_edit(context, game, "🌙 Night", 15)
    await resolve_night(context, game)


async def resolve_night(context, game):
    mafia = [v for k, v in game.night_actions.items() if k == "mafia"]
    doctor = game.night_actions.get("doctor")

    dead = None
    if mafia:
        t = random.choice(mafia)
        if t != doctor:
            dead = t
            game.alive.discard(dead)

    await context.bot.send_message(
        game.chat_id,
        f"🌅 {'💀 '+game.players[dead] if dead else '😇 No death'}"
    )


# =========================
# DAY
# =========================
async def day(context, game):
    game.votes = defaultdict(int)
    game.voted = set()

    keyboard = []
    for p in game.alive:
        keyboard.append([InlineKeyboardButton(
            game.players[p],
            callback_data=f"v:{p}:{game.chat_id}"
        )])

    msg = await context.bot.send_message(
        game.chat_id,
        "🗳 Voting",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    game.message_id = msg.message_id

    await countdown_edit(context, game, "🗳 Vote", 15)
    await resolve_votes(context, game)


async def resolve_votes(context, game):
    if not game.votes:
        return

    out = max(game.votes, key=game.votes.get)
    game.alive.discard(out)

    await context.bot.send_message(
        game.chat_id,
        f"🚫 {game.players[out]}"
    )


# =========================
# CALLBACK
# =========================
async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    data = q.data.split(":")
    action = data[0]

    if action == "n":
        _, role, target, chat_id = data
        games[int(chat_id)].night_actions[role] = int(target)

    elif action == "v":
        _, target, chat_id = data
        game = games[int(chat_id)]
        uid = q.from_user.id

        if uid in game.voted:
            return await q.answer("Already voted")

        game.voted.add(uid)
        game.votes[int(target)] += 1


# =========================
# UTIL
# =========================
async def countdown_edit(context, game, title, sec):
    msg = await context.bot.send_message(game.chat_id, f"{title}: {sec}")
    for i in range(sec-1, -1, -1):
        await asyncio.sleep(1)
        try:
            await context.bot.edit_message_text(
                f"{title}: {i}",
                chat_id=game.chat_id,
                message_id=msg.message_id
            )
        except:
            pass


async def check_win(context, game):
    mafia = [p for p in game.alive if game.roles[p]=="mafia"]
    others = [p for p in game.alive if game.roles[p]!="mafia"]

    if not mafia:
        await context.bot.send_message(game.chat_id, "🏆 CIVILIANS WIN")
        for p in game.players:
            update_user(p, game.players[p], True)
        return True

    if len(mafia) >= len(others):
        await context.bot.send_message(game.chat_id, "💀 MAFIA WIN")
        for p in game.players:
            update_user(p, game.players[p], False)
        return True

    return False


# =========================
# MAIN
# =========================
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("create", create))
    app.add_handler(CommandHandler("join", join))
    app.add_handler(CommandHandler("start_game", start_game))
    app.add_handler(CommandHandler("profile", profile))
    app.add_handler(CommandHandler("top", top))

    app.add_handler(CallbackQueryHandler(callback))

    print("🔥 SUPER ULTIMATE BOT RUNNING")
    app.run_polling()


if __name__ == "__main__":
    main()
