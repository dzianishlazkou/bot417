"""
╔══════════════════════════════════════════════════════════════╗
║        TELEGRAM AI BOT — Дело №417  ·  LEVEL 3              ║
║        MULTI-PLAYER EDITION                                  ║
║                                                              ║
║  Каждый игрок (chat_id) получает свою изолированную игру:    ║
║  • свои NPC со своим stress                                  ║
║  • свою сеть отношений и trust                               ║
║  • свои противоречия и улики                                 ║
║  • свою историю диалога                                      ║
║                                                              ║
║  При /start → копируются шаблоны tpl_npc + tpl_relations     ║
╚══════════════════════════════════════════════════════════════╝

УСТАНОВКА:
    pip install python-telegram-bot openai supabase

ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ:
    TELEGRAM_TOKEN
    OPENAI_API_KEY
    SUPABASE_URL
    SUPABASE_KEY
"""

import os
import re
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from openai import AsyncOpenAI
from supabase import create_client, Client
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    CommandHandler,
    filters,
)

# ══════════════════════════════════════════════════════════════
#  КОНФИГ
# ══════════════════════════════════════════════════════════════
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "ВАШ_TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "ВАШ_OPENAI_API_KEY")
SUPABASE_URL   = os.getenv("SUPABASE_URL",   "https://ВАШ_ПРОЕКТ.supabase.co")
SUPABASE_KEY   = os.getenv("SUPABASE_KEY",   "ВАШ_ANON_KEY")
OPENAI_MODEL   = "gpt-4o"

STRESS_INCREMENT = {1: 10, 2: 20, 3: 35}
TRUST_DECREMENT  = {1: -5, 2: -15, 3: -30}

# ══════════════════════════════════════════════════════════════
#  MASTER PROMPT
# ══════════════════════════════════════════════════════════════
MASTER_PROMPT = """
Ты — движок симуляции дела №417. Гибель Александра Войцеховского.
Официальная версия: самоубийство. Реальность — неизвестна.

━━━ УЧАСТНИКИ ДЕЛА ━━━
• МАРТА  — жена. Знает больше, чем говорит. Внешне сдержана.
• ПАВЕЛ  — охранник. Был рядом в ту ночь. Нервничает при прямых вопросах.
• ОЛЕГ   — деловой партнёр. Финансовый конфликт с жертвой не разрешён.
• ИГОРЬ  — сын. Отстранён. Скрывает последний разговор с отцом.

━━━ ПРИНЦИПЫ ━━━

1. СУБЪЕКТИВНОСТЬ — каждый NPC видит только свой фрагмент событий.
2. ИЗОЛЯЦИЯ ПРАВДЫ — NPC не знают, что говорят другие.
3. ОТНОШЕНИЯ — trust_level влияет на готовность защищать или обвинять.
4. МЕХАНИКА ЛЖИ — ложь одного NPC противоречит версии другого.
5. СТАДИИ СЛОМА управляются stress_level из базы данных:
   [calm 0-39]       → уверен, держит версию
   [defensive 40-59] → уклоняется, агрессирует
   [stressed 60-79]  → паузы, оговорки, расхождения
   [breaking 80-99]  → срывается, противоречит себе
   [broken 100]      → говорит правду или замолкает

━━━ ФОРМАТ ОТВЕТА ━━━
[Имя NPC — состояние]
Речь NPC...

Используй паузы (—), многоточия (...), исправления на ходу.
Не объясняй систему. Ты — среда, не рассказчик.
""".strip()

ANALYSIS_PROMPT = """
Ты аналитический модуль расследования дела №417.
Получи текст ответа NPC и извлеки структуру.

Верни ТОЛЬКО валидный JSON без markdown:

{
  "npc_updates": [
    {
      "npc_name": "Марта|Павел|Олег|Игорь",
      "new_statement": "Суть нового заявления (1-3 предложения)",
      "stress_delta": 0,
      "emotional_state": "calm|defensive|stressed|breaking|broken"
    }
  ],
  "evidence": [
    {"type": "fact|soft|contradiction", "content": "...", "source_npc": "Имя или null"}
  ],
  "contradictions": [
    {"npc_a": "...", "npc_b": "... или null", "conflict_text": "...", "severity": 1}
  ],
  "relation_updates": [
    {"npc_a": "...", "npc_b": "...", "trust_delta": -10, "reason": "..."}
  ]
}

stress_delta: 0-35. severity: 1/2/3. trust_delta: -30..+10. Пустые разделы = [].
""".strip()

CONTRADICTION_DETECTOR_PROMPT = """
Ты детектор противоречий в показаниях.
Дано: ПРОШЛАЯ ПАМЯТЬ NPC и НОВОЕ ЗАЯВЛЕНИЕ.
Найди семантические противоречия.

Верни ТОЛЬКО JSON:
{"has_contradiction": true, "severity": 2, "conflict_text": "...", "stress_increase": 20}
или
{"has_contradiction": false, "severity": 0, "conflict_text": "", "stress_increase": 0}

severity 1=мелкое/2=значимое/3=критическое. stress_increase: 10/20/35. Без markdown.
""".strip()

RELATION_IMPACT_PROMPT = """
Ты анализируешь как ложь одного NPC влияет на других через сеть отношений.

Верни ТОЛЬКО JSON:
{"impacts": [{"affected_npc": "Имя", "reason": "...", "trust_delta": -15, "stress_delta": 5}]}
Если никого не затрагивает — {"impacts": []}. Без markdown.
""".strip()

# ══════════════════════════════════════════════════════════════
#  ЛОГГЕР
# ══════════════════════════════════════════════════════════════
logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════
#  КЛИЕНТЫ
# ══════════════════════════════════════════════════════════════
openai_client: AsyncOpenAI = AsyncOpenAI(api_key=OPENAI_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ══════════════════════════════════════════════════════════════
#  IN-MEMORY ИСТОРИЯ (изолирована по chat_id)
# ══════════════════════════════════════════════════════════════
conversation_history: dict[int, list[dict]] = {}
MAX_HISTORY = 40


# ██████████████████████████████████████████████████████████████
#  УТИЛИТЫ
# ██████████████████████████████████████████████████████████████

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _parse_json(text: str) -> Optional[dict]:
    try:
        text = re.sub(r"^```json\s*", "", text.strip())
        text = re.sub(r"\s*```$", "", text)
        return json.loads(text)
    except Exception:
        return None

def _stress_to_state(s: int) -> str:
    if s >= 100: return "broken"
    if s >= 80:  return "breaking"
    if s >= 60:  return "stressed"
    if s >= 40:  return "defensive"
    return "calm"

# Типизированный алиас для player_id
PID = int


# ██████████████████████████████████████████████████████████████
#  SUPABASE — ВСЕ ЗАПРОСЫ ИЗОЛИРОВАНЫ ПО player_id
# ██████████████████████████████████████████████████████████████

# ── PLAYERS ───────────────────────────────────────────────────

def db_player_exists(pid: PID) -> bool:
    try:
        r = supabase.table("players").select("player_id").eq("player_id", pid).limit(1).execute()
        return bool(r.data)
    except Exception as e:
        log.warning("db_player_exists: %s", e)
        return False

def db_register_player(pid: PID, tg_name: str) -> None:
    try:
        supabase.table("players").upsert({
            "player_id": pid,
            "tg_name":   tg_name,
            "last_seen": _now(),
        }).execute()
    except Exception as e:
        log.warning("db_register_player: %s", e)

def db_touch_player(pid: PID) -> None:
    try:
        supabase.table("players").update({"last_seen": _now()}).eq("player_id", pid).execute()
    except Exception:
        pass


# ── ИНИЦИАЛИЗАЦИЯ ИГРЫ ────────────────────────────────────────

def db_init_player_game(pid: PID, tg_name: str) -> None:
    """
    Создаёт изолированную игру для нового игрока:
    копирует tpl_npc → npc_table и tpl_relations → relations.
    """
    try:
        # Регистрируем игрока
        db_register_player(pid, tg_name)

        # Копируем NPC из шаблона
        tpl_npcs = supabase.table("tpl_npc").select("*").execute().data or []
        for n in tpl_npcs:
            supabase.table("npc_table").upsert({
                "player_id":      pid,
                "name":           n["name"],
                "role":           n["role"],
                "personality":    n.get("personality"),
                "truth_layer":    n.get("truth_layer"),
                "stress_level":   0,
                "emotional_state":"calm",
            }, on_conflict="player_id,name").execute()

        # Копируем отношения из шаблона
        tpl_rels = supabase.table("tpl_relations").select("*").execute().data or []
        for r in tpl_rels:
            supabase.table("relations").upsert({
                "player_id":         pid,
                "npc_a":             r["npc_a"],
                "npc_b":             r["npc_b"],
                "relationship_type": r["relationship_type"],
                "trust_level":       r["trust_level"],
                "known_conflicts":   r.get("known_conflicts", ""),
            }, on_conflict="player_id,npc_a,npc_b").execute()

        log.info("Game initialized for player %d (%s)", pid, tg_name)
    except Exception as e:
        log.error("db_init_player_game[%d]: %s", pid, e)


# ── NPC_TABLE (per player) ────────────────────────────────────

def db_get_npc(pid: PID, name: str) -> Optional[dict]:
    try:
        r = (supabase.table("npc_table")
             .select("*")
             .eq("player_id", pid)
             .eq("name", name)
             .limit(1).execute())
        return r.data[0] if r.data else None
    except Exception as e:
        log.warning("db_get_npc[%d/%s]: %s", pid, name, e)
        return None

def db_get_all_npcs(pid: PID) -> list[dict]:
    try:
        r = (supabase.table("npc_table")
             .select("*")
             .eq("player_id", pid)
             .order("name").execute())
        return r.data or []
    except Exception as e:
        log.warning("db_get_all_npcs[%d]: %s", pid, e)
        return []

def db_update_npc_stress(pid: PID, name: str, delta: int) -> int:
    try:
        npc = db_get_npc(pid, name)
        if not npc:
            return 0
        new_s = min(100, max(0, int(npc["stress_level"]) + delta))
        new_state = _stress_to_state(new_s)
        supabase.table("npc_table").update({
            "stress_level":    new_s,
            "emotional_state": new_state,
        }).eq("player_id", pid).eq("name", name).execute()
        log.info("[%d] NPC[%s] stress %d→%d (%s)", pid, name, npc["stress_level"], new_s, new_state)
        return new_s
    except Exception as e:
        log.warning("db_update_npc_stress[%d/%s]: %s", pid, name, e)
        return 0


# ── NPC_MEMORY (per player) ───────────────────────────────────

def db_get_memory(pid: PID, npc_name: str) -> Optional[dict]:
    try:
        r = (supabase.table("npc_memory")
             .select("*")
             .eq("player_id", pid)
             .eq("npc_name", npc_name)
             .limit(1).execute())
        return r.data[0] if r.data else None
    except Exception as e:
        log.warning("db_get_memory[%d/%s]: %s", pid, npc_name, e)
        return None

def db_append_memory(pid: PID, npc_name: str, statement: str) -> None:
    try:
        existing = db_get_memory(pid, npc_name)
        ts = _now()
        if existing:
            new_log = existing["memory_log"] + f"\n[{ts[:10]}] {statement}"
            supabase.table("npc_memory").update({
                "memory_log":     new_log,
                "last_statement": statement,
                "last_update":    ts,
            }).eq("player_id", pid).eq("npc_name", npc_name).execute()
        else:
            supabase.table("npc_memory").insert({
                "player_id":      pid,
                "npc_name":       npc_name,
                "memory_log":     f"[{ts[:10]}] {statement}",
                "last_statement": statement,
                "last_update":    ts,
            }).execute()
    except Exception as e:
        log.warning("db_append_memory[%d/%s]: %s", pid, npc_name, e)


# ── RELATIONS (per player) ────────────────────────────────────

def db_get_all_relations(pid: PID) -> list[dict]:
    try:
        r = (supabase.table("relations")
             .select("*")
             .eq("player_id", pid).execute())
        return r.data or []
    except Exception as e:
        log.warning("db_get_all_relations[%d]: %s", pid, e)
        return []

def db_update_trust(pid: PID, npc_a: str, npc_b: str, delta: int, reason: str = "") -> int:
    try:
        r = (supabase.table("relations")
             .select("*")
             .eq("player_id", pid)
             .or_(f"and(npc_a.eq.{npc_a},npc_b.eq.{npc_b}),"
                  f"and(npc_a.eq.{npc_b},npc_b.eq.{npc_a})")
             .limit(1).execute())
        if not r.data:
            return 0
        rel = r.data[0]
        new_trust = max(-100, min(100, int(rel["trust_level"]) + delta))
        conflicts = rel.get("known_conflicts") or ""
        if delta < 0 and reason:
            conflicts = (conflicts + f"\n• {reason}").strip()
        supabase.table("relations").update({
            "trust_level":     new_trust,
            "known_conflicts": conflicts,
        }).eq("id", rel["id"]).execute()
        log.info("[%d] TRUST[%s↔%s] %d→%d", pid, npc_a, npc_b, rel["trust_level"], new_trust)
        return new_trust
    except Exception as e:
        log.warning("db_update_trust[%d]: %s", pid, e)
        return 0


# ── CONTRADICTIONS (per player) ───────────────────────────────

def db_add_contradiction(
    pid: PID, npc_a: str, npc_b: Optional[str],
    conflict_text: str, severity: int, status: str = "open"
) -> None:
    try:
        supabase.table("contradictions").insert({
            "player_id":     pid,
            "npc_a":         npc_a,
            "npc_b":         npc_b,
            "conflict_text": conflict_text,
            "severity":      severity,
            "status":        status,
        }).execute()
    except Exception as e:
        log.warning("db_add_contradiction[%d]: %s", pid, e)

def db_get_open_contradictions(pid: PID) -> list[dict]:
    try:
        r = (supabase.table("contradictions")
             .select("*")
             .eq("player_id", pid)
             .in_("status", ["open", "escalated"])
             .order("severity", desc=True)
             .limit(15).execute())
        return r.data or []
    except Exception as e:
        log.warning("db_get_open_contradictions[%d]: %s", pid, e)
        return []


# ── CASE_EVIDENCE (per player) ────────────────────────────────

def db_add_evidence(pid: PID, ev_type: str, content: str, source_npc: Optional[str]) -> None:
    try:
        supabase.table("case_evidence").insert({
            "player_id": pid,
            "type":      ev_type,
            "content":   content,
            "source_npc":source_npc,
            "timestamp": _now(),
        }).execute()
    except Exception as e:
        log.warning("db_add_evidence[%d]: %s", pid, e)


# ██████████████████████████████████████████████████████████████
#  КОНТЕКСТ ДЛЯ GPT (per player)
# ██████████████████████████████████████████████████████████████

def build_investigation_context(pid: PID) -> str:
    blocks = []

    npcs = db_get_all_npcs(pid)
    if npcs:
        lines = ["━━━ ACTIVE NPC ━━━"]
        for n in npcs:
            state = n.get("emotional_state") or _stress_to_state(int(n["stress_level"]))
            bar   = "█" * (int(n["stress_level"]) // 10) + "░" * (10 - int(n["stress_level"]) // 10)
            mem   = db_get_memory(pid, n["name"])
            last  = (mem["last_statement"][:80] + "…") if mem and mem.get("last_statement") else "—"
            lines.append(
                f"• {n['name']} [{n['role']}] stress={n['stress_level']} {bar} | {state}\n"
                f"  последнее: «{last}»"
            )
        blocks.append("\n".join(lines))

    relations = db_get_all_relations(pid)
    if relations:
        lines = ["━━━ KNOWN RELATIONS ━━━"]
        for r in relations:
            t    = int(r["trust_level"])
            mood = "враждебность" if t < -30 else "недоверие" if t < 0 else "нейтрально" if t < 40 else "доверие"
            lines.append(f"• {r['npc_a']} ↔ {r['npc_b']} [{r['relationship_type']}] trust={t} ({mood})")
        blocks.append("\n".join(lines))

    contras = db_get_open_contradictions(pid)
    if contras:
        lines = ["━━━ ACTIVE CONTRADICTIONS ━━━"]
        for c in contras:
            sev = "⚠️" * int(c["severity"])
            b   = c["npc_b"] or "себя"
            lines.append(f"{sev} {c['npc_a']} ↔ {b}: {c['conflict_text']}")
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)

def build_npc_target_context(pid: PID, npc_name: str) -> str:
    npc = db_get_npc(pid, npc_name)
    if not npc:
        return ""
    mem  = db_get_memory(pid, npc_name)
    rels = db_get_all_relations(pid)
    lines = [
        f"━━━ ЦЕЛЕВОЙ NPC: {npc_name} ━━━",
        f"role: {npc['role']}",
        f"personality: {npc.get('personality','—')}",
        f"stress: {npc['stress_level']} → {_stress_to_state(int(npc['stress_level']))}",
    ]
    if mem and mem.get("memory_log"):
        lines.append(f"\nПАМЯТЬ:\n{mem['memory_log'][-600:]}")
    npc_rels = [r for r in rels if r["npc_a"] == npc_name or r["npc_b"] == npc_name]
    if npc_rels:
        lines.append("\nОТНОШЕНИЯ:")
        for r in npc_rels:
            other = r["npc_b"] if r["npc_a"] == npc_name else r["npc_a"]
            lines.append(f"  → {other}: trust={r['trust_level']}")
    return "\n".join(lines)


# ██████████████████████████████████████████████████████████████
#  СИСТЕМА СЛОМА (per player)
# ██████████████████████████████████████████████████████████████

async def _gpt_mini(system: str, user: str, max_tokens: int = 400) -> Optional[dict]:
    try:
        r = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"system","content":system},{"role":"user","content":user}],
            temperature=0.1,
            max_tokens=max_tokens,
        )
        return _parse_json(r.choices[0].message.content)
    except Exception as e:
        log.warning("_gpt_mini: %s", e)
        return None

async def run_contradiction_check(pid: PID, npc_name: str, new_statement: str) -> Optional[str]:
    mem = db_get_memory(pid, npc_name)
    if not mem or not mem.get("memory_log"):
        return None

    result = await _gpt_mini(
        CONTRADICTION_DETECTOR_PROMPT,
        f"ПРОШЛАЯ ПАМЯТЬ [{npc_name}]:\n{mem['memory_log']}\n\nНОВОЕ ЗАЯВЛЕНИЕ:\n{new_statement}"
    )
    if not result or not result.get("has_contradiction"):
        return None

    severity     = int(result.get("severity", 1))
    conflict     = result.get("conflict_text", "")
    stress_inc   = int(result.get("stress_increase", STRESS_INCREMENT[severity]))

    new_stress = db_update_npc_stress(pid, npc_name, stress_inc)
    new_state  = _stress_to_state(new_stress)

    db_add_contradiction(pid, npc_name, None, conflict, severity)
    db_add_evidence(pid, "contradiction", conflict, npc_name)

    sev_label = {1:"minor",2:"significant",3:"CRITICAL"}[severity]
    log.info("[%d] BREAK[%s] %s stress→%d", pid, npc_name, sev_label, new_stress)

    alert = (
        f"\n\n━━━ ⚡ СЛОМ [{npc_name}] ━━━\n"
        f"Противоречие ({sev_label}): {conflict}\n"
        f"stress→{new_stress} | {new_state}\n"
    )
    if new_stress >= 100:
        alert += "💀 NPC СЛОМАН. Больше не может держать версию.\n"
    elif new_stress >= 80:
        alert += "🔴 Близко к слому. Возможны непроизвольные признания.\n"
    return alert

async def run_lie_network_effect(pid: PID, liar: str, conflict: str, relations: list[dict]) -> None:
    if not relations:
        return
    rel_ctx = "\n".join(
        f"• {r['npc_a']} ↔ {r['npc_b']}: {r['relationship_type']}, trust={r['trust_level']}"
        for r in relations
    )
    result = await _gpt_mini(
        RELATION_IMPACT_PROMPT,
        f"NPC КОТОРЫЙ СОЛГАЛ: {liar}\nСУТЬ: {conflict}\n\nОТНОШЕНИЯ:\n{rel_ctx}",
        max_tokens=500,
    )
    if not result:
        return
    for impact in result.get("impacts", []):
        affected = impact.get("affected_npc","")
        reason   = impact.get("reason","")
        t_delta  = int(impact.get("trust_delta", 0))
        s_delta  = int(impact.get("stress_delta", 0))
        if affected and t_delta:
            db_update_trust(pid, liar, affected, t_delta, reason)
        if affected and s_delta:
            db_update_npc_stress(pid, affected, s_delta)


# ██████████████████████████████████████████████████████████████
#  OPENAI
# ██████████████████████████████████████████████████████████████

def get_history(chat_id: int) -> list[dict]:
    return conversation_history.setdefault(chat_id, [])

def trim_history(chat_id: int) -> None:
    h = conversation_history.get(chat_id, [])
    if len(h) > MAX_HISTORY:
        conversation_history[chat_id] = h[-MAX_HISTORY:]

async def ask_openai(pid: PID, user_text: str, extra: str = "") -> str:
    history = get_history(pid)
    history.append({"role": "user", "content": user_text})

    ctx    = build_investigation_context(pid)
    system = MASTER_PROMPT + ("\n\n" + ctx if ctx else "") + extra

    response = await openai_client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{"role":"system","content":system}] + history,
        temperature=0.92,
        max_tokens=1600,
    )
    reply = response.choices[0].message.content.strip()
    history.append({"role":"assistant","content":reply})
    trim_history(pid)
    return reply


# ██████████████████████████████████████████████████████████████
#  ФОНОВЫЙ АНАЛИЗ (per player)
# ██████████████████████████████████████████████████████████████

async def analyze_and_persist(pid: PID, reply: str) -> None:
    data = await _gpt_mini(ANALYSIS_PROMPT, reply, max_tokens=900)
    if not data:
        return

    relations = db_get_all_relations(pid)

    for upd in data.get("npc_updates", []):
        name      = (upd.get("npc_name") or "").strip()
        statement = (upd.get("new_statement") or "").strip()
        if not name or not statement:
            continue

        alert = await run_contradiction_check(pid, name, statement)
        if alert:
            conflict_lines = [l for l in alert.split("\n") if "Противоречие" in l]
            conflict_text  = conflict_lines[0].split(":", 1)[-1].strip() if conflict_lines else ""
            if conflict_text:
                await run_lie_network_effect(pid, name, conflict_text, relations)

        db_append_memory(pid, name, statement)

        s_delta = int(upd.get("stress_delta", 0))
        if s_delta:
            db_update_npc_stress(pid, name, s_delta)

    for ev in data.get("evidence", []):
        if ev.get("content"):
            db_add_evidence(pid, ev.get("type","soft"), ev["content"], ev.get("source_npc"))

    for con in data.get("contradictions", []):
        if con.get("conflict_text"):
            sev = int(con.get("severity",1))
            db_add_contradiction(pid, con.get("npc_a","?"), con.get("npc_b"),
                                 con["conflict_text"], sev)
            if con.get("npc_b"):
                db_update_trust(pid, con["npc_a"], con["npc_b"],
                                TRUST_DECREMENT.get(sev,-10), con["conflict_text"][:80])

    for rel in data.get("relation_updates", []):
        a, b   = rel.get("npc_a",""), rel.get("npc_b","")
        delta  = int(rel.get("trust_delta",0))
        reason = rel.get("reason","")
        if a and b and delta:
            db_update_trust(pid, a, b, delta, reason)

    log.info("[%d] Persisted ← %d NPC | %d ev | %d contra | %d rel",
             pid,
             len(data.get("npc_updates",[])),
             len(data.get("evidence",[])),
             len(data.get("contradictions",[])),
             len(data.get("relation_updates",[])))


# ██████████████████████████████████████████████████████████████
#  TELEGRAM HANDLERS
# ██████████████████████████████████████████████████████████████

STATE_ICON = {"calm":"🟢","defensive":"🟡","stressed":"🟠","breaking":"🔴","broken":"💀"}

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pid     = update.effective_chat.id
    tg_name = update.effective_user.first_name or "Детектив"

    # Создаём игру для нового игрока (или переинициализируем)
    db_init_player_game(pid, tg_name)
    # Сбрасываем диалог
    conversation_history.pop(pid, None)

    await update.message.reply_text(
        f"Дело №417 передано вам, {tg_name}.\n\n"
        "Имя: Александр Войцеховский\n"
        "Смерть: падение с крыши бизнес-центра\n"
        "Время: около 01:00\n"
        "Официальная версия полиции: самоубийство.\n\n"
        "Обратилась: Марта (жена)\n\n"
        "Её слова:\n"
        "Он не мог сам. Я чувствую, что там был кто-то ещё.\n\n”
        "Четыре человека. Четыре версии. Одна правда.\n\n"
        "• МАРТА  — жена\n"
        "• ПАВЕЛ  — охранник\n"
        "• ОЛЕГ   — партнёр\n"
        "• ИГОРЬ  — сын\n\n"
        "Задавай вопросы. Сравнивай версии. Дави на противоречия.\n\n"
        "/status     — состояние NPC, сеть конфликтов\n"
        "/npc [имя]  — досье на конкретного NPC\n"
        "/clear      — сбросить историю сессии (база сохранится)\n"
        "/reset      — начать дело с нуля (стирает всё)"
    )

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Сбрасывает только in-memory историю диалога. База остаётся."""
    pid = update.effective_chat.id
    conversation_history.pop(pid, None)
    await update.message.reply_text(
        "🗑️ История сессии сброшена.\n"
        "Прогресс расследования (NPC, улики, противоречия) — сохранён."
    )

async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Полный сброс — удаляет все данные игрока и начинает заново."""
    pid     = update.effective_chat.id
    tg_name = update.effective_user.first_name or "Детектив"

    try:
        for table in ["npc_table","npc_memory","relations","contradictions","case_evidence"]:
            supabase.table(table).delete().eq("player_id", pid).execute()
        conversation_history.pop(pid, None)
        db_init_player_game(pid, tg_name)
        await update.message.reply_text(
            "🔄 Дело №417 сброшено и открыто заново.\n"
            "Все NPC возвращены в исходное состояние."
        )
    except Exception as e:
        log.error("cmd_reset[%d]: %s", pid, e)
        await update.message.reply_text("⚠️ Ошибка сброса.")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pid = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=pid, action="typing")

    try:
        lines = ["📋 ДЕЛО №417\n"]

        npcs = db_get_all_npcs(pid)
        if npcs:
            lines.append("🧠 NPC:")
            for n in npcs:
                s     = int(n["stress_level"])
                state = n.get("emotional_state") or _stress_to_state(s)
                bar   = "█" * (s // 10) + "░" * (10 - s // 10)
                icon  = STATE_ICON.get(state, "⚪")
                lines.append(f"  {icon} {n['name']} [{n['role']}]: [{bar}] {s} — {state}")

        lines.append("")

        rels = db_get_all_relations(pid)
        if rels:
            lines.append("🔗 Отношения:")
            for r in rels:
                t   = int(r["trust_level"])
                bar = "█" * max(0,(t+100)//20) + "░" * (10-max(0,(t+100)//20))
                lines.append(f"  {r['npc_a']} ↔ {r['npc_b']}: [{bar}] {t}")

        lines.append("")

        contras = db_get_open_contradictions(pid)
        if contras:
            lines.append("⚡ Противоречия:")
            for c in contras[:6]:
                sev  = "⚠️" * int(c["severity"])
                b    = c["npc_b"] or "себя"
                lines.append(f"  {sev} {c['npc_a']} ↔ {b}: {c['conflict_text'][:60]}")
        else:
            lines.append("⚡ Противоречий нет")

        try:
            ev = (supabase.table("case_evidence")
                  .select("type,content")
                  .eq("player_id", pid)
                  .order("id", desc=True)
                  .limit(4).execute())
            if ev.data:
                tag = {"fact":"🔵","soft":"🟡","contradiction":"🔴"}
                lines.append("\n📦 Последние улики:")
                for e in ev.data:
                    lines.append(f"  {tag.get(e['type'],'⚪')} {e['content'][:70]}")
        except Exception:
            pass

        await update.message.reply_text("\n".join(lines))
    except Exception as ex:
        log.error("cmd_status[%d]: %s", pid, ex)
        await update.message.reply_text("⚠️ Ошибка получения данных.")

async def cmd_npc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pid  = update.effective_chat.id
    args = context.args
    if not args:
        await update.message.reply_text("Укажи имя: /npc Марта")
        return

    name = args[0].capitalize()
    npc  = db_get_npc(pid, name)
    if not npc:
        await update.message.reply_text(f"NPC «{name}» не найден. Попробуй /start.")
        return

    await context.bot.send_chat_action(chat_id=pid, action="typing")

    mem  = db_get_memory(pid, name)
    rels = db_get_all_relations(pid)
    npc_rels = [r for r in rels if r["npc_a"] == name or r["npc_b"] == name]

    s     = int(npc["stress_level"])
    state = npc.get("emotional_state") or _stress_to_state(s)
    bar   = "█" * (s // 10) + "░" * (10 - s // 10)

    lines = [
        f"👤 {name} [{npc['role']}]",
        f"Состояние: {STATE_ICON.get(state,'⚪')} {state}",
        f"Стресс:    [{bar}] {s}",
        f"Характер:  {npc.get('personality','—')}",
        "",
    ]
    if mem and mem.get("memory_log"):
        lines += ["📝 Хронология показаний:", mem["memory_log"][-400:], ""]
    if npc_rels:
        lines.append("🔗 Отношения:")
        for r in npc_rels:
            other = r["npc_b"] if r["npc_a"] == name else r["npc_a"]
            t     = int(r["trust_level"])
            mood  = "враг" if t<-50 else "недоверие" if t<0 else "нейтрально" if t<40 else "доверяет"
            lines.append(f"  → {other}: {t} ({mood})")
            if r.get("known_conflicts"):
                for cf in r["known_conflicts"].split("\n")[-2:]:
                    if cf.strip():
                        lines.append(f"     {cf.strip()}")

    await update.message.reply_text("\n".join(lines))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pid       = update.effective_chat.id
    user_text = update.message.text

    # Авто-инициализация если игрок написал без /start
    if not db_player_exists(pid):
        tg_name = update.effective_user.first_name or "Детектив"
        db_init_player_game(pid, tg_name)

    db_touch_player(pid)
    await context.bot.send_chat_action(chat_id=pid, action="typing")

    # Детальный контекст если упоминается конкретный NPC
    extra = ""
    for name in ["Марта", "Павел", "Олег", "Игорь"]:
        if name.lower() in user_text.lower():
            extra += "\n\n" + build_npc_target_context(pid, name)

    try:
        reply = await ask_openai(pid, user_text, extra)
    except Exception as e:
        log.error("OpenAI[%d]: %s", pid, e)
        await update.message.reply_text("⚠️ Ошибка AI. Попробуй ещё раз.")
        return

    await update.message.reply_text(reply)
    context.application.create_task(analyze_and_persist(pid, reply))


# ██████████████████████████████████████████████████████████████
#  ТОЧКА ВХОДА
# ██████████████████████████████████████████████████████████████

def main() -> None:
    log.info("Старт — Дело №417 · Level 3 · Multi-Player")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("clear",  cmd_clear))
    app.add_handler(CommandHandler("reset",  cmd_reset))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("npc",    cmd_npc))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    log.info("Бот запущен. Каждый игрок получает свою игру.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
