# 🎭 ULTRA PRO MAFIA BOT v2

A production-grade Telegram Mafia party game bot with clean OOP architecture,
smart AI players, full role system, SQLite stats, and polished inline UI.

---

## Features

| Feature | Details |
|---------|---------|
| **Roles** | Civilian, Mafia, Doctor, Detective, Sniper (1-shot), Maniac (solo win) |
| **State Machine** | LOBBY → NIGHT → DAY → VOTING → (loop) → ENDED |
| **Smart AI** | Weighted heuristics, not random — Mafia targets power roles, Doctor protects most-voted, Sniper holds shot for confirmed Mafia |
| **Anti-spam voting** | One vote per player, changeable; tie = no elimination |
| **SQLite stats** | Game history, win rates, role breakdown, leaderboard |
| **Multi-chat** | One `GameManager` per chat, fully isolated |
| **Auto cleanup** | Ended games purged every 5 minutes |
| **Error handling** | Graceful fallback everywhere, zero crash on invalid input |
| **Modern UI** | Inline keyboards for all interactions |

---

## Setup

### 1. Get a Bot Token
1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. `/newbot` → follow prompts
3. Copy the token

### 2. Install
```bash
pip install -r requirements.txt
```

### 3. Run
```bash
BOT_TOKEN=your_token_here python bot.py
```

Or create a `.env` file and use `python-dotenv`:
```
BOT_TOKEN=123456:ABC...
```

---

## Architecture

```
mafia_bot/
├── bot.py           # Entry point, Telegram handlers, routing
├── game_manager.py  # State machine: GameManager + GameRegistry
├── role_engine.py   # Pure logic: night resolution, win conditions
├── ai_engine.py     # Smart AI decision-making (weighted heuristics)
├── models.py        # Data classes: Player, Role, Phase, NightResult
├── keyboards.py     # All InlineKeyboard builders
├── database.py      # Async SQLite (game history, player stats)
└── requirements.txt
```

### Layer diagram
```
bot.py (Telegram I/O)
    │
    ├── GameRegistry (manages all active games)
    │       └── GameManager (state machine per chat)
    │               ├── RoleEngine (pure night/win logic)
    │               └── AIEngine (smart AI decisions)
    │
    ├── Database (async SQLite)
    └── keyboards.py (UI builders)
```

---

## How to Play (in Telegram)

1. Add bot to a group chat
2. `/newgame` — creates lobby
3. Players tap **Join Game** or type `/join`
4. Host can **Add AI Player** to fill slots
5. Host taps **Start Game** (or `/startgame`) when 4+ players ready
6. Each player receives their **secret role** via DM
7. **Night:** Special roles use DM inline buttons to act
8. **Day:** Discuss in group chat
9. **Vote:** Tap vote buttons to eliminate a suspect
10. Repeat until win condition!

---

## Roles

| Role | Team | Night Action |
|------|------|-------------|
| 👤 Civilian | Town | None |
| 🔫 Mafia | Mafia | Kill 1 player (majority vote) |
| 💊 Doctor | Town | Protect 1 player from death |
| 🔍 Detective | Town | Learn 1 player's true role |
| 🎯 Sniper | Town | ONE unblockable kill (use wisely!) |
| 🔪 Maniac | Solo | Kill 1 player — win by surviving alone |

### Win Conditions
- 🏙️ **Town wins**: All Mafia and Maniac are eliminated
- 🔫 **Mafia wins**: Mafia count ≥ remaining Town + Maniac
- 🔪 **Maniac wins**: Last player standing

---

## Commands

| Command | Description |
|---------|-------------|
| `/newgame` | Create a lobby (group chat) |
| `/join` | Join the lobby |
| `/leave` | Leave the lobby |
| `/startgame` | Start the game (host only) |
| `/endgame` | Force-end (host/admin) |
| `/players` | Show player list and status |
| `/myrole` | DM reminder of your role |
| `/stats` | View statistics & leaderboard |
| `/help` | Full help text |

---

## Configuration

Edit constants in `game_manager.py`:
```python
NIGHT_ACTION_TIMEOUT = 45   # seconds for night phase
DAY_SPEECH_TIMEOUT   = 60   # seconds for discussion
VOTING_TIMEOUT       = 40   # seconds for voting
```

---

## Database

SQLite file: `mafia_bot.db` (auto-created on first run)

Tables:
- `games` — full game records with player snapshot
- `player_stats` — per-user win rates, role history

---

## Deployment (Systemd)

```ini
# /etc/systemd/system/mafia_bot.service
[Unit]
Description=Ultra Pro Mafia Bot
After=network.target

[Service]
WorkingDirectory=/opt/mafia_bot
Environment=BOT_TOKEN=your_token_here
ExecStart=/usr/bin/python3 bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl enable --now mafia_bot
journalctl -u mafia_bot -f
```
