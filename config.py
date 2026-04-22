"""config.py — All configurable constants in one place."""
import os
from dataclasses import dataclass

@dataclass
class Config:
    # Bot
    BOT_TOKEN: str = os.environ.get("BOT_TOKEN", "")
    
    # Database
    DB_PATH: str = os.environ.get("DB_PATH", "mafia_v3.db")
    
    # Timings (seconds)
    NIGHT_TIMEOUT:    int = 45
    DAY_TIMEOUT:      int = 60
    VOTING_TIMEOUT:   int = 40
    DEFENSE_TIMEOUT:  int = 30
    LOBBY_TIMEOUT:    int = 300
    
    # Game limits
    MIN_PLAYERS: int = 4
    MAX_PLAYERS: int = 15
    MAX_AI:      int = 8
    
    # ELO
    ELO_BASE:    int = 1000
    ELO_K:       int = 32
    
    # Rate limiting
    RATE_LIMIT_MESSAGES: int = 5
    RATE_LIMIT_WINDOW:   int = 10
    
    # Features
    HEALTH_CHECK_PORT: int = int(os.environ.get("PORT", 8080))
    
    # Languages supported
    LANGUAGES = ["uz", "ru", "en"]
    DEFAULT_LANG = "uz"

cfg = Config()

# ── Translations ──────────────────────────────────────────────────

TEXTS = {
    "uz": {
        "welcome": "🎭 <b>ULTRA PRO MAFIA BOT v3 ga xush kelibsiz!</b>\n\nRollar: 👤 Fuqaro • 🔫 Mafiya • 💊 Doktor\n🔍 Detektiv • 🎯 Snayper • 🔪 Maniak\n👑 Mayor • 🛡️ Taniqchi • 🤡 Jester\n\nGuruh chatda /newgame bilan boshlang!",
        "game_started": "🎭 <b>O'yin #{gid} boshlandi!</b>\n👥 {n} o'yinchi — rollar taqsimlandi!\nShaxsiy xabaringizni tekshiring!",
        "night_begins": "🌙 <b>{n}-kecha</b>\nShahar uxlaydi. Maxsus rollar, DM ni tekshiring.",
        "dawn": "☀️ <b>Tong otdi...</b>",
        "nobody_died": "🕊️ Bu kecha tinch kechdi — hech kim vafot etmadi!",
        "player_killed": "💀 <b>{name}</b> o'lgan topildi. U <b>{role}</b> edi {emoji}",
        "player_sniped": "🎯 <b>{name}</b> Snayper tomonidan yo'q qilindi. U <b>{role}</b> edi {emoji}",
        "doctor_saved": "🩺 Doktor kimnidir Mafiyadan saqlab qoldi!",
        "day_phase": "☀️ <b>{n}-kun</b> — Muhokama!\n\n<b>Tirik ({alive}):</b>\n{list}\n\n🗣️ {sec}s muhokama, keyin ovoz berish!",
        "voting_phase": "🗳️ <b>Ovoz berish!</b>\nO'yinchini chiqarib yuborish uchun ovoz bering. ⏱️ {sec}s",
        "no_votes": "🤷 Ovoz berilmadi. Hech kim chiqarilmadi.",
        "tie_vote": "⚖️ <b>{names}</b> o'rtasida tenglik! Hech kim chiqarilmadi.",
        "eliminated": "🪓 <b>{name}</b> chiqarib yuborildi! U <b>{role}</b> {emoji} edi!",
        "game_over": "{emoji} <b>O'YIN TUGADI!</b>\n<b>{winner}</b>\n\n<b>Yakuniy rollar:</b>\n{roles}\n\n📊 {rounds} tur davom etdi.",
        "your_role": "{emoji} <b>Siz — {role}!</b>\n\n{desc}",
        "last_will": "📜 <b>{name}ning vasiyati:</b>\n<i>{will}</i>",
        "join_success": "✅ <b>{name}</b> qo'shildi! ({n}/15)",
        "need_players": "❌ Kamida 4 o'yinchi kerak!",
        "already_running": "⚠️ O'yin allaqachon ketmoqda!",
        "not_host": "❌ Faqat host boshlashi mumkin.",
        "no_lobby": "❌ Faol lobby yo'q. /newgame dan foydalaning.",
        "defense_speech": "⚖️ <b>{name}</b>, {sec}s ichida o'zingizni himoya qiling!",
        "night_action": "🌙 <b>Kecha harakati!</b>\n{verb}:",
        "action_recorded": "✅ <b>{name}</b> ga harakat qayd etildi.",
        "win_town": "🏙️ Shahar g'olib keldi!",
        "win_mafia": "🔫 Mafiya g'olib keldi!",
        "win_maniac": "🔪 Maniak g'olib keldi!",
        "win_jester": "🤡 Jester g'olib keldi!",
        "win_draw": "⚖️ Durrang!",
        "mafia_chat": "🔫 <b>Mafiya chati:</b> {msg}",
        "ghost_chat": "👻 <b>{name} (arvoh):</b> {msg}",
        "bodyguard_died": "🛡️ <b>{guard}</b> o'z hayotini <b>{target}</b> uchun qurbon qildi!",
        "lover_died": "💔 <b>{name}</b> sevgilisi bilan birga o'ldi!",
        "jester_wins": "🤡 <b>{name}</b> chiqarib yuborildi va u JESTER edi — u g'olib!",
    },
    "ru": {
        "welcome": "🎭 <b>Добро пожаловать в ULTRA PRO MAFIA BOT v3!</b>\n\nРоли: 👤 Мирный • 🔫 Мафия • 💊 Доктор\n🔍 Детектив • 🎯 Снайпер • 🔪 Маньяк\n👑 Мэр • 🛡️ Телохранитель • 🤡 Шут\n\nИспользуйте /newgame в группе!",
        "game_started": "🎭 <b>Игра #{gid} началась!</b>\n👥 {n} игроков — роли розданы!\nПроверьте личные сообщения!",
        "night_begins": "🌙 <b>Ночь {n}</b>\nГород засыпает. Специальные роли, проверьте ЛС.",
        "dawn": "☀️ <b>Наступает рассвет...</b>",
        "nobody_died": "🕊️ Ночь прошла спокойно — никто не погиб!",
        "player_killed": "💀 <b>{name}</b> найден мёртвым. Он был <b>{role}</b> {emoji}",
        "player_sniped": "🎯 <b>{name}</b> устранён Снайпером. Он был <b>{role}</b> {emoji}",
        "doctor_saved": "🩺 Доктор спас кого-то от Мафии!",
        "day_phase": "☀️ <b>День {n}</b> — Обсуждение!\n\n<b>Живые ({alive}):</b>\n{list}\n\n🗣️ {sec}с обсуждения, затем голосование!",
        "voting_phase": "🗳️ <b>Голосование!</b>\nГолосуйте за исключение игрока. ⏱️ {sec}с",
        "no_votes": "🤷 Голосов не было. Никто не исключён.",
        "tie_vote": "⚖️ Ничья между <b>{names}</b>! Никто не исключён.",
        "eliminated": "🪓 <b>{name}</b> исключён! Он был <b>{role}</b> {emoji}!",
        "game_over": "{emoji} <b>ИГРА ОКОНЧЕНА!</b>\n<b>{winner}</b>\n\n<b>Роли:</b>\n{roles}\n\n📊 {rounds} раунд(ов).",
        "your_role": "{emoji} <b>Вы — {role}!</b>\n\n{desc}",
        "last_will": "📜 <b>Завещание {name}:</b>\n<i>{will}</i>",
        "join_success": "✅ <b>{name}</b> присоединился! ({n}/15)",
        "need_players": "❌ Нужно минимум 4 игрока!",
        "already_running": "⚠️ Игра уже идёт!",
        "not_host": "❌ Только хост может начать.",
        "no_lobby": "❌ Нет активного лобби. Используйте /newgame.",
        "defense_speech": "⚖️ <b>{name}</b>, у вас {sec}с на защиту!",
        "night_action": "🌙 <b>Ночное действие!</b>\n{verb}:",
        "action_recorded": "✅ Действие на <b>{name}</b> записано.",
        "win_town": "🏙️ Город победил!",
        "win_mafia": "🔫 Мафия победила!",
        "win_maniac": "🔪 Маньяк победил!",
        "win_jester": "🤡 Шут победил!",
        "win_draw": "⚖️ Ничья!",
        "mafia_chat": "🔫 <b>Чат мафии:</b> {msg}",
        "ghost_chat": "👻 <b>{name} (призрак):</b> {msg}",
        "bodyguard_died": "🛡️ <b>{guard}</b> пожертвовал собой ради <b>{target}</b>!",
        "lover_died": "💔 <b>{name}</b> умер вместе со своим возлюбленным!",
        "jester_wins": "🤡 <b>{name}</b> исключён и он был ШУТОМ — он победил!",
    },
    "en": {
        "welcome": "🎭 <b>Welcome to ULTRA PRO MAFIA BOT v3!</b>\n\nRoles: 👤 Civilian • 🔫 Mafia • 💊 Doctor\n🔍 Detective • 🎯 Sniper • 🔪 Maniac\n👑 Mayor • 🛡️ Bodyguard • 🤡 Jester\n\nUse /newgame in a group chat to start!",
        "game_started": "🎭 <b>Game #{gid} starts!</b>\n👥 {n} players — roles assigned!\nCheck your private messages!",
        "night_begins": "🌙 <b>Night {n}</b>\nThe city sleeps. Special roles, check your DMs.",
        "dawn": "☀️ <b>Dawn breaks...</b>",
        "nobody_died": "🕊️ The night was peaceful — nobody died!",
        "player_killed": "💀 <b>{name}</b> was found dead. They were the <b>{role}</b> {emoji}",
        "player_sniped": "🎯 <b>{name}</b> was sniped. They were the <b>{role}</b> {emoji}",
        "doctor_saved": "🩺 The Doctor saved someone from the Mafia!",
        "day_phase": "☀️ <b>Day {n}</b> — Discussion!\n\n<b>Alive ({alive}):</b>\n{list}\n\n🗣️ Discuss for {sec}s, then voting!",
        "voting_phase": "🗳️ <b>Voting phase!</b>\nVote to eliminate a player. ⏱️ {sec}s",
        "no_votes": "🤷 No votes cast. Nobody eliminated.",
        "tie_vote": "⚖️ Tie between <b>{names}</b>! Nobody eliminated.",
        "eliminated": "🪓 <b>{name}</b> was eliminated! They were the <b>{role}</b> {emoji}!",
        "game_over": "{emoji} <b>GAME OVER!</b>\n<b>{winner}</b>\n\n<b>Final roles:</b>\n{roles}\n\n📊 {rounds} round(s).",
        "your_role": "{emoji} <b>You are the {role}!</b>\n\n{desc}",
        "last_will": "📜 <b>{name}'s Last Will:</b>\n<i>{will}</i>",
        "join_success": "✅ <b>{name}</b> joined! ({n}/15)",
        "need_players": "❌ Need at least 4 players!",
        "already_running": "⚠️ A game is already running!",
        "not_host": "❌ Only the host can start.",
        "no_lobby": "❌ No active lobby. Use /newgame.",
        "defense_speech": "⚖️ <b>{name}</b>, you have {sec}s to defend yourself!",
        "night_action": "🌙 <b>Night action time!</b>\n{verb}:",
        "action_recorded": "✅ Action recorded on <b>{name}</b>.",
        "win_town": "🏙️ The Town wins!",
        "win_mafia": "🔫 The Mafia wins!",
        "win_maniac": "🔪 The Maniac wins!",
        "win_jester": "🤡 The Jester wins!",
        "win_draw": "⚖️ It's a draw!",
        "mafia_chat": "🔫 <b>Mafia chat:</b> {msg}",
        "ghost_chat": "👻 <b>{name} (ghost):</b> {msg}",
        "bodyguard_died": "🛡️ <b>{guard}</b> sacrificed themselves for <b>{target}</b>!",
        "lover_died": "💔 <b>{name}</b> died alongside their lover!",
        "jester_wins": "🤡 <b>{name}</b> was eliminated and they were the JESTER — they win!",
    }
}

def t(lang: str, key: str, **kwargs) -> str:
    """Get translated text."""
    lang = lang if lang in TEXTS else "en"
    text = TEXTS[lang].get(key, TEXTS["en"].get(key, key))
    try:
        return text.format(**kwargs)
    except (KeyError, ValueError):
        return text
