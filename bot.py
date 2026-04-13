"""
╔══════════════════════════════════════════════════════════════╗
║          TELEGRAM AI BOT — Дело №417  ·  LEVEL 3            ║
║  Python 3.10+ | python-telegram-bot | OpenAI | Supabase      ║
║                                                              ║
║  Архитектура:                                                ║
║  • Каждый NPC — отдельная личность с правдой и ложью         ║
║  • Сеть отношений (trust_level, конфликты)                   ║
║  • Ложь одного NPC → влияет на других                        ║
║  • Breaking system: 5 стадий слома                           ║
║  • Contradiction detector: семантическое сравнение           ║
╚══════════════════════════════════════════════════════════════╝

УСТАНОВКА:
    pip install python-telegram-bot openai supabase

ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ (Railway → Variables):
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

# ══════════════════════════════════════════════════════════════
#  СТАДИИ СЛОМА
#  0–39  calm      держит версию, уверен
#  40–59 defensive уходит в защиту
#  60–79 stressed  путается, оговорки
#  80–99 breaking  срывается, противоречит себе
#  100   broken    сломан — правда или молчание
# ══════════════════════════════════════════════════════════════
STRESS_INCREMENT = {1: 10, 2: 20, 3: 35}   # по severity противоречия
TRUST_DECREMENT  = {1: -5, 2: -15, 3: -30} # по severity конфликта

# ══════════════════════════════════════════════════════════════
#  MASTER PROMPT — LEVEL 3
# ══════════════════════════════════════════════════════════════
MASTER_PROMPT = """
Ты — движок симуляции дела №417. Гибель Александра Войцеховского.
Официальная версия: самоубийство. Реальность — неизвестна.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
УЧАСТНИКИ ДЕЛА
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

• МАРТА   — жена. Знает больше, чем говорит. Внешне сдержана.
• ПАВЕЛ   — охранник. Был рядом в ту ночь. Нервничает при прямых вопросах.
• ОЛЕГ    — деловой партнёр. Финансовый конфликт с жертвой не разрешён.
• ИГОРЬ   — сын. Отстранён. Скрывает последний разговор с отцом.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ПРИНЦИПЫ СИМУЛЯЦИИ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. СУБЪЕКТИВНОСТЬ
   Каждый NPC видит только свой фрагмент событий.
   Никто не знает полную картину — включая тебя.
   Не раскрывай истину напрямую.

2. ИЗОЛЯЦИЯ ПРАВДЫ
   NPC не знают, что говорят другие.
   Если Марта сказала X, Павел не знает об этом —
   если только игрок сам не сообщит ему.

3. СИСТЕМА ОТНОШЕНИЙ
   Между NPC существуют trust_level и история конфликтов.
   Они передаются тебе в блоке RELATIONS.
   Низкий trust → NPC склонны обвинять друг друга.
   Высокий trust → защищают друг друга.

4. МЕХАНИКА ЛЖИ
   Если NPC_A лжёт → его ложь может противоречить версии NPC_B.
   Игрок может использовать это: "Марта сказала, что ты был на крыше."
   NPC реагирует эмоционально — в зависимости от своего stress_level
   и trust_level к Марте.

5. СТАДИИ СЛОМА (stress_level управляет поведением)
   [calm 0-39]     → уверен, держит версию
   [defensive 40-59] → уклоняется, короткие ответы
   [stressed 60-79]  → паузы, оговорки, мелкие расхождения
   [breaking 80-99]  → открытые противоречия, срывы, проговорки
   [broken 100]      → говорит правду или замолкает полностью

6. СЕТЬ ЛЖИ
   Одна ложь меняет всю картину:
   → другие NPC начинают противоречить этой лжи
   → доверие падает
   → появляются новые точки давления

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ФОРМАТ ОТВЕТА
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[Имя NPC — состояние]
Речь NPC...

Используй: паузы (—), многоточия (...), исправления на ходу.
Не объясняй систему. Не давай подсказок. Ты — среда, не рассказчик.
""".strip()

# ══════════════════════════════════════════════════════════════
#  ПРОМПТЫ АНАЛИЗА (gpt-4o-mini)
# ══════════════════════════════════════════════════════════════

ANALYSIS_PROMPT = """
Ты аналитический модуль расследования дела №417.
Получи текст ответа NPC и извлеки структуру.

Верни ТОЛЬКО валидный JSON без markdown:

{
  "npc_updates": [
    {
      "npc_name": "Имя NPC (Марта|Павел|Олег|Игорь)",
      "new_statement": "Точная суть нового заявления (1-3 предложения)",
      "stress_delta": 0,
      "emotional_state": "calm|defensive|stressed|breaking|broken"
    }
  ],
  "evidence": [
    {
      "type": "fact|soft|contradiction",
      "content": "Суть",
      "source_npc": "Имя или null"
    }
  ],
  "contradictions": [
    {
      "npc_a": "Кто противоречит",
      "npc_b": "Кому противоречит (NPC или null)",
      "conflict_text": "Суть конфликта",
      "severity": 1
    }
  ],
  "relation_updates": [
    {
      "npc_a": "Имя",
      "npc_b": "Имя",
      "trust_delta": -10,
      "reason": "причина изменения доверия"
    }
  ]
}

stress_delta: сколько добавить к стрессу (0-35).
severity: 1/2/3. trust_delta: от -30 до +10.
Если раздел пуст — верни [].
""".strip()

CONTRADICTION_DETECTOR_PROMPT = """
Ты детектор противоречий в показаниях.

Тебе дано:
1. ВСЯ ПРОШЛАЯ ПАМЯТЬ NPC (что говорил раньше)
2. НОВОЕ ЗАЯВЛЕНИЕ (что сказал сейчас)

Найди семантические противоречия. Верни ТОЛЬКО JSON:

{
  "has_contradiction": true,
  "severity": 2,
  "conflict_text": "Раньше утверждал X, теперь говорит Y",
  "stress_increase": 20
}

Если противоречия нет — {"has_contradiction": false, "severity": 0, "conflict_text": "", "stress_increase": 0}
severity: 1 мелкое / 2 значимое / 3 критическое.
stress_increase: 10 / 20 / 35.
Только JSON, без markdown.
""".strip()

RELATION_IMPACT_PROMPT = """
Ты анализируешь как ложь одного NPC влияет на других.

Тебе дано:
- NPC который солгал или противоречит себе
- Суть противоречия
- Текущие отношения между NPC

Верни список NPC которых это затрагивает и как:

{
  "impacts": [
    {
      "affected_npc": "Имя",
      "reason": "Почему ложь NPC_A влияет на NPC_B",
      "trust_delta": -15,
      "stress_delta": 5
    }
  ]
}

Если никого не затрагивает — {"impacts": []}. Только JSON.
""".strip()

# ══════════════════════════════════════════════════════════════
#  ЛОГГЕР
# ══════════════════════════════════════════════════════════════
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════
#  КЛИЕНТЫ
# ══════════════════════════════════════════════════════════════
openai_client: AsyncOpenAI = AsyncOpenAI(api_key=OPENAI_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ══════════════════════════════════════════════════════════════
#  IN-MEMORY ИСТОРИЯ (per chat_id)
# ══════════════════════════════════════════════════════════════
conversation_history: dict[int, list[dict]] = {}
MAX_HISTORY = 40


# ██████████████████████████████████████████████████████████████
#  SUPABASE — СЛОЙ БД
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


# ── NPC_TABLE ─────────────────────────────────────────────────

def db_get_npc(name: str) -> Optional[dict]:
    try:
        r = supabase.table("npc_table").select("*").eq("name", name).limit(1).execute()
        return r.data[0] if r.data else None
    except Exception as e:
        log.warning("db_get_npc[%s]: %s", name, e)
        return None

def db_get_all_npcs() -> list[dict]:
    try:
        r = supabase.table("npc_table").select("*").order("name").execute()
        return r.data or []
    except Exception as e:
        log.warning("db_get_all_npcs: %s", e)
        return []

def db_update_npc_stress(name: str, delta: int) -> int:
    """Добавляет delta к stress_level NPC. Возвращает новый уровень."""
    try:
        npc = db_get_npc(name)
        if not npc:
            return 0
        new_stress = min(100, max(0, int(npc["stress_level"]) + delta))
        new_state  = _stress_to_state(new_stress)
        supabase.table("npc_table").update({
            "stress_level":    new_stress,
            "emotional_state": new_state,
        }).eq("name", name).execute()
        log.info("NPC[%s] stress %d→%d (%s)", name, npc["stress_level"], new_stress, new_state)
        return new_stress
    except Exception as e:
        log.warning("db_update_npc_stress[%s]: %s", name, e)
        return 0


# ── NPC_MEMORY ────────────────────────────────────────────────

def db_get_memory(npc_name: str) -> Optional[dict]:
    try:
        r = (supabase.table("npc_memory")
             .select("*").eq("npc_name", npc_name).limit(1).execute())
        return r.data[0] if r.data else None
    except Exception as e:
        log.warning("db_get_memory[%s]: %s", npc_name, e)
        return None

def db_append_memory(npc_name: str, statement: str) -> None:
    """Добавляет новое заявление к memory_log NPC."""
    try:
        existing = db_get_memory(npc_name)
        ts = _now()
        if existing:
            new_log = existing["memory_log"] + f"\n[{ts[:10]}] {statement}"
            supabase.table("npc_memory").update({
                "memory_log":      new_log,
                "last_statement":  statement,
                "last_update":     ts,
            }).eq("npc_name", npc_name).execute()
        else:
            supabase.table("npc_memory").insert({
                "npc_name":       npc_name,
                "memory_log":     f"[{ts[:10]}] {statement}",
                "last_statement": statement,
                "last_update":    ts,
            }).execute()
    except Exception as e:
        log.warning("db_append_memory[%s]: %s", npc_name, e)


# ── RELATIONS ─────────────────────────────────────────────────

def db_get_relation(npc_a: str, npc_b: str) -> Optional[dict]:
    try:
        # Ищем в обоих направлениях
        r = (supabase.table("relations")
             .select("*")
             .or_(f"and(npc_a.eq.{npc_a},npc_b.eq.{npc_b}),"
                  f"and(npc_a.eq.{npc_b},npc_b.eq.{npc_a})")
             .limit(1).execute())
        return r.data[0] if r.data else None
    except Exception as e:
        log.warning("db_get_relation[%s↔%s]: %s", npc_a, npc_b, e)
        return None

def db_get_all_relations() -> list[dict]:
    try:
        r = supabase.table("relations").select("*").execute()
        return r.data or []
    except Exception as e:
        log.warning("db_get_all_relations: %s", e)
        return []

def db_update_trust(npc_a: str, npc_b: str, delta: int, reason: str = "") -> int:
    """Изменяет trust_level между двумя NPC. Возвращает новый уровень."""
    try:
        rel = db_get_relation(npc_a, npc_b)
        if not rel:
            return 0
        new_trust = max(-100, min(100, int(rel["trust_level"]) + delta))

        # Добавляем конфликт в known_conflicts если trust упал
        conflicts = rel.get("known_conflicts") or ""
        if delta < 0 and reason:
            conflicts = (conflicts + f"\n• {reason}").strip()

        supabase.table("relations").update({
            "trust_level":     new_trust,
            "known_conflicts": conflicts,
        }).eq("id", rel["id"]).execute()

        log.info("RELATION[%s↔%s] trust %d→%d", npc_a, npc_b, rel["trust_level"], new_trust)
        return new_trust
    except Exception as e:
        log.warning("db_update_trust[%s↔%s]: %s", npc_a, npc_b, e)
        return 0


# ── CONTRADICTIONS ────────────────────────────────────────────

def db_add_contradiction(
    npc_a: str, npc_b: Optional[str],
    conflict_text: str, severity: int,
    status: str = "open"
) -> None:
    try:
        supabase.table("contradictions").insert({
            "npc_a":         npc_a,
            "npc_b":         npc_b,
            "conflict_text": conflict_text,
            "severity":      severity,
            "status":        status,
        }).execute()
    except Exception as e:
        log.warning("db_add_contradiction: %s", e)

def db_get_open_contradictions() -> list[dict]:
    try:
        r = (supabase.table("contradictions")
             .select("*")
             .in_("status", ["open", "escalated"])
             .order("severity", desc=True)
             .limit(15).execute())
        return r.data or []
    except Exception as e:
        log.warning("db_get_open_contradictions: %s", e)
        return []


# ── CASE_EVIDENCE ─────────────────────────────────────────────

def db_add_evidence(ev_type: str, content: str, source_npc: Optional[str]) -> None:
    try:
        supabase.table("case_evidence").insert({
            "type":       ev_type,
            "content":    content,
            "source_npc": source_npc,
            "timestamp":  _now(),
        }).execute()
    except Exception as e:
        log.warning("db_add_evidence: %s", e)


# ██████████████████████████████████████████████████████████████
#  КОНТЕКСТ ДЛЯ GPT — сборка из всех таблиц
# ██████████████████████████████████████████████████████████████

def build_investigation_context() -> str:
    """Собирает полный контекст расследования для системного промпта."""
    blocks = []

    # ── Состояние NPC ─────────────────────────────────────────
    npcs = db_get_all_npcs()
    if npcs:
        lines = ["━━━ ACTIVE NPC ━━━"]
        for n in npcs:
            state = n.get("emotional_state") or _stress_to_state(int(n["stress_level"]))
            stress_bar = "█" * (int(n["stress_level"]) // 10) + "░" * (10 - int(n["stress_level"]) // 10)
            mem = db_get_memory(n["name"])
            last = (mem["last_statement"][:80] + "…") if mem and mem.get("last_statement") else "—"
            lines.append(
                f"• {n['name']} [{n['role']}] | stress={n['stress_level']} {stress_bar} | {state}\n"
                f"  последнее: «{last}»"
            )
        blocks.append("\n".join(lines))

    # ── Отношения ─────────────────────────────────────────────
    relations = db_get_all_relations()
    if relations:
        lines = ["━━━ KNOWN RELATIONS ━━━"]
        for r in relations:
            trust = int(r["trust_level"])
            mood = "враждебность" if trust < -30 else "недоверие" if trust < 0 else "нейтралитет" if trust < 40 else "доверие"
            lines.append(f"• {r['npc_a']} ↔ {r['npc_b']} [{r['relationship_type']}] trust={trust} ({mood})")
            if r.get("known_conflicts"):
                for cf in r["known_conflicts"].split("\n"):
                    if cf.strip():
                        lines.append(f"    {cf.strip()}")
        blocks.append("\n".join(lines))

    # ── Открытые противоречия ─────────────────────────────────
    contras = db_get_open_contradictions()
    if contras:
        lines = ["━━━ ACTIVE CONTRADICTIONS ━━━"]
        for c in contras:
            sev = "⚠️" * int(c["severity"])
            b   = c["npc_b"] or "себя"
            lines.append(f"{sev} {c['npc_a']} ↔ {b}: {c['conflict_text']}")
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


def build_npc_target_context(npc_name: str) -> str:
    """Дополнительный контекст при давлении на конкретного NPC."""
    npc = db_get_npc(npc_name)
    if not npc:
        return ""

    mem  = db_get_memory(npc_name)
    rels = db_get_all_relations()

    lines = [f"━━━ ЦЕЛЕВОЙ NPC: {npc_name} ━━━"]
    lines.append(f"role: {npc['role']}")
    lines.append(f"personality: {npc.get('personality','—')}")
    lines.append(f"stress: {npc['stress_level']} → {_stress_to_state(int(npc['stress_level']))}")

    if mem and mem.get("memory_log"):
        lines.append(f"\nПАМЯТЬ (хронология):\n{mem['memory_log'][-600:]}")

    # Отношения этого NPC
    npc_rels = [r for r in rels if r["npc_a"] == npc_name or r["npc_b"] == npc_name]
    if npc_rels:
        lines.append("\nОТНОШЕНИЯ:")
        for r in npc_rels:
            other = r["npc_b"] if r["npc_a"] == npc_name else r["npc_a"]
            trust = int(r["trust_level"])
            lines.append(f"  → {other}: trust={trust}")

    return "\n".join(lines)


# ██████████████████████████████████████████████████████████████
#  СИСТЕМА СЛОМА — ЯДРО
# ██████████████████████████████████████████████████████████████

async def _gpt_mini(system: str, user: str, max_tokens: int = 400) -> Optional[dict]:
    """Вспомогательный вызов gpt-4o-mini с парсингом JSON."""
    try:
        r = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": system},
                      {"role": "user",   "content": user}],
            temperature=0.1,
            max_tokens=max_tokens,
        )
        return _parse_json(r.choices[0].message.content)
    except Exception as e:
        log.warning("_gpt_mini error: %s", e)
        return None


async def run_contradiction_check(npc_name: str, new_statement: str) -> Optional[str]:
    """
    Сравнивает новое заявление NPC с его памятью.
    При найденном противоречии: поднимает стресс, фиксирует в БД.
    Возвращает строку-уведомление для вставки в промпт (или None).
    """
    mem = db_get_memory(npc_name)
    if not mem or not mem.get("memory_log"):
        return None  # первое появление

    result = await _gpt_mini(
        CONTRADICTION_DETECTOR_PROMPT,
        f"ПРОШЛАЯ ПАМЯТЬ [{npc_name}]:\n{mem['memory_log']}\n\nНОВОЕ ЗАЯВЛЕНИЕ:\n{new_statement}"
    )
    if not result or not result.get("has_contradiction"):
        return None

    severity      = int(result.get("severity", 1))
    conflict_text = result.get("conflict_text", "")
    stress_inc    = int(result.get("stress_increase", STRESS_INCREMENT[severity]))

    new_stress = db_update_npc_stress(npc_name, stress_inc)
    new_state  = _stress_to_state(new_stress)

    db_add_contradiction(npc_name, None, conflict_text, severity)
    db_add_evidence("contradiction", conflict_text, npc_name)

    sev_label = {1: "minor", 2: "significant", 3: "CRITICAL"}[severity]
    log.info("BREAK[%s] %s | stress→%d (%s)", npc_name, sev_label, new_stress, new_state)

    alert = (
        f"\n\n━━━ ⚡ СИСТЕМА СЛОМА [{npc_name}] ━━━\n"
        f"Противоречие ({sev_label}): {conflict_text}\n"
        f"stress → {new_stress} | состояние → {new_state}\n"
    )
    if new_stress >= 100:
        alert += "💀 NPC СЛОМАН. Больше не может держать версию.\n"
    elif new_stress >= 80:
        alert += "🔴 NPC на грани. Возможны непроизвольные признания.\n"
    return alert


async def run_lie_network_effect(
    liar_name: str, conflict_text: str, relations: list[dict]
) -> None:
    """
    Ложь одного NPC → волновой эффект на других через сеть отношений.
    Понижает trust и добавляет стресс затронутым NPC.
    """
    if not relations:
        return

    rel_context = "\n".join(
        f"• {r['npc_a']} ↔ {r['npc_b']}: {r['relationship_type']}, trust={r['trust_level']}"
        for r in relations
    )

    result = await _gpt_mini(
        RELATION_IMPACT_PROMPT,
        f"NPC КОТОРЫЙ СОЛГАЛ: {liar_name}\n"
        f"СУТЬ ПРОТИВОРЕЧИЯ: {conflict_text}\n\n"
        f"ТЕКУЩИЕ ОТНОШЕНИЯ:\n{rel_context}",
        max_tokens=500,
    )
    if not result:
        return

    for impact in result.get("impacts", []):
        affected = impact.get("affected_npc", "")
        reason   = impact.get("reason", "")
        t_delta  = int(impact.get("trust_delta", 0))
        s_delta  = int(impact.get("stress_delta", 0))

        if affected and t_delta:
            db_update_trust(liar_name, affected, t_delta, reason)
        if affected and s_delta:
            db_update_npc_stress(affected, s_delta)

        if affected and t_delta < -10:
            log.info("LIE NETWORK: %s→%s trust%d stress+%d", liar_name, affected, t_delta, s_delta)


# ██████████████████████████████████████████████████████████████
#  OPENAI — ОСНОВНОЙ ЗАПРОС
# ██████████████████████████████████████████████████████████████

def get_history(chat_id: int) -> list[dict]:
    return conversation_history.setdefault(chat_id, [])

def trim_history(chat_id: int) -> None:
    h = conversation_history.get(chat_id, [])
    if len(h) > MAX_HISTORY:
        conversation_history[chat_id] = h[-MAX_HISTORY:]


async def ask_openai(
    chat_id: int,
    user_text: str,
    extra_context: str = "",
) -> str:
    history = get_history(chat_id)
    history.append({"role": "user", "content": user_text})

    investigation_ctx = build_investigation_context()
    system = MASTER_PROMPT
    if investigation_ctx:
        system += "\n\n" + investigation_ctx
    if extra_context:
        system += extra_context

    response = await openai_client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{"role": "system", "content": system}] + history,
        temperature=0.92,
        max_tokens=1600,
    )

    reply = response.choices[0].message.content.strip()
    history.append({"role": "assistant", "content": reply})
    trim_history(chat_id)
    return reply


# ██████████████████████████████████████████████████████████████
#  ФОНОВЫЙ АНАЛИЗ И ПЕРСИСТЕНЦИЯ
# ██████████████████████████████████████████████████████████████

async def analyze_and_persist(reply: str) -> None:
    """
    После каждого ответа GPT:
    1. Извлекает структуру (NPC, улики, противоречия, изменения trust)
    2. Для каждого NPC запускает contradiction check
    3. При найденном противоречии — запускает lie network effect
    4. Сохраняет всё в Supabase
    """
    data = await _gpt_mini(ANALYSIS_PROMPT, reply, max_tokens=900)
    if not data:
        return

    relations = db_get_all_relations()

    # ── NPC: память + слом ────────────────────────────────────
    for upd in data.get("npc_updates", []):
        name      = (upd.get("npc_name") or "").strip()
        statement = (upd.get("new_statement") or "").strip()
        if not name or not statement:
            continue

        # Проверяем противоречие ПЕРЕД записью
        contra_alert = await run_contradiction_check(name, statement)

        # Если противоречие — запускаем волновой эффект
        if contra_alert:
            conflict_line = [
                l for l in contra_alert.split("\n")
                if "Противоречие" in l
            ]
            conflict_text = conflict_line[0].replace("Противоречие", "").strip(": ()") if conflict_line else ""
            if conflict_text:
                await run_lie_network_effect(name, conflict_text, relations)

        # Записываем новое заявление в память
        db_append_memory(name, statement)

        # Обновляем stress по оценке анализа
        s_delta = int(upd.get("stress_delta", 0))
        if s_delta:
            db_update_npc_stress(name, s_delta)

    # ── Улики ─────────────────────────────────────────────────
    for ev in data.get("evidence", []):
        if ev.get("content"):
            db_add_evidence(ev.get("type", "soft"), ev["content"], ev.get("source_npc"))

    # ── Противоречия из анализа текста ───────────────────────
    for con in data.get("contradictions", []):
        if con.get("conflict_text"):
            severity = int(con.get("severity", 1))
            db_add_contradiction(
                con.get("npc_a", "?"),
                con.get("npc_b"),
                con["conflict_text"],
                severity,
            )
            # Доп. эффект: снижаем trust между конфликтующими NPC
            if con.get("npc_b"):
                db_update_trust(
                    con["npc_a"], con["npc_b"],
                    TRUST_DECREMENT.get(severity, -10),
                    con["conflict_text"][:80],
                )

    # ── Изменения отношений ───────────────────────────────────
    for rel in data.get("relation_updates", []):
        a, b   = rel.get("npc_a", ""), rel.get("npc_b", "")
        delta  = int(rel.get("trust_delta", 0))
        reason = rel.get("reason", "")
        if a and b and delta:
            db_update_trust(a, b, delta, reason)

    log.info(
        "Persisted ← %d NPC | %d ev | %d contra | %d rel",
        len(data.get("npc_updates", [])),
        len(data.get("evidence", [])),
        len(data.get("contradictions", [])),
        len(data.get("relation_updates", [])),
    )


# ██████████████████████████████████████████████████████████████
#  TELEGRAM HANDLERS
# ██████████████████████████████████████████████████████████████

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    name = update.effective_user.first_name or "Детектив"
    await update.message.reply_text(
        f"Дело №417 открыто, {name}.\n\n"
        "Александр Войцеховский найден мёртвым.\n"
        "Официальная версия — самоубийство.\n\n"
        "Четыре человека. Четыре версии. Одна правда.\n\n"
        "• МАРТА  — жена\n"
        "• ПАВЕЛ  — охранник\n"
        "• ОЛЕГ   — партнёр\n"
        "• ИГОРЬ  — сын\n\n"
        "Задавай вопросы. Сравнивай версии. Дави на противоречия.\n\n"
        "/status — состояние NPC и сеть конфликтов\n"
        "/npc [имя] — досье на конкретного NPC\n"
        "/clear — сбросить историю сессии"
    )


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conversation_history.pop(update.effective_chat.id, None)
    await update.message.reply_text(
        "🗑️ Сессия сброшена. База данных сохранена."
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    state_icon = {
        "calm": "🟢", "defensive": "🟡",
        "stressed": "🟠", "breaking": "🔴", "broken": "💀",
    }

    try:
        lines = ["📋 ДЕЛО №417 — СТАТУС\n"]

        # NPC
        npcs = db_get_all_npcs()
        if npcs:
            lines.append("🧠 NPC:")
            for n in npcs:
                s     = int(n["stress_level"])
                state = n.get("emotional_state") or _stress_to_state(s)
                bar   = "█" * (s // 10) + "░" * (10 - s // 10)
                icon  = state_icon.get(state, "⚪")
                lines.append(f"  {icon} {n['name']} [{n['role']}]: [{bar}] {s}")

        lines.append("")

        # Отношения
        rels = db_get_all_relations()
        if rels:
            lines.append("🔗 Сеть отношений:")
            for r in rels:
                t = int(r["trust_level"])
                bar = "█" * max(0, (t + 100) // 20) + "░" * (10 - max(0, (t + 100) // 20))
                lines.append(f"  {r['npc_a']} ↔ {r['npc_b']}: [{bar}] {t}")

        lines.append("")

        # Противоречия
        contras = db_get_open_contradictions()
        if contras:
            lines.append("⚡ Открытые противоречия:")
            for c in contras[:6]:
                sev  = "⚠️" * int(c["severity"])
                b    = c["npc_b"] or "себя"
                stat = c.get("status", "open")
                lines.append(f"  {sev} {c['npc_a']} ↔ {b} [{stat}]: {c['conflict_text'][:60]}")
        else:
            lines.append("⚡ Противоречий не зафиксировано")

        # Улики
        try:
            ev_res = (supabase.table("case_evidence")
                      .select("type, content")
                      .order("id", desc=True)
                      .limit(4).execute())
            if ev_res.data:
                lines.append("\n📦 Последние улики:")
                tag_map = {"fact": "🔵", "soft": "🟡", "contradiction": "🔴"}
                for e in ev_res.data:
                    lines.append(f"  {tag_map.get(e['type'],'⚪')} {e['content'][:70]}")
        except Exception:
            pass

        await update.message.reply_text("\n".join(lines))

    except Exception as ex:
        log.error("cmd_status: %s", ex)
        await update.message.reply_text("⚠️ Ошибка получения данных.")


async def cmd_npc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Досье конкретного NPC: /npc Марта"""
    args = context.args
    if not args:
        await update.message.reply_text("Укажи имя: /npc Марта")
        return

    name = args[0].capitalize()
    npc  = db_get_npc(name)
    if not npc:
        await update.message.reply_text(f"NPC «{name}» не найден в базе.")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    mem   = db_get_memory(name)
    rels  = db_get_all_relations()
    npc_rels = [r for r in rels if r["npc_a"] == name or r["npc_b"] == name]

    s     = int(npc["stress_level"])
    state = npc.get("emotional_state") or _stress_to_state(s)
    bar   = "█" * (s // 10) + "░" * (10 - s // 10)
    state_icon = {"calm":"🟢","defensive":"🟡","stressed":"🟠","breaking":"🔴","broken":"💀"}

    lines = [
        f"👤 {name} [{npc['role']}]",
        f"Состояние: {state_icon.get(state,'⚪')} {state}",
        f"Стресс:    [{bar}] {s}",
        f"Характер:  {npc.get('personality','—')}",
        "",
    ]

    if mem and mem.get("memory_log"):
        log_preview = mem["memory_log"][-400:]
        lines += ["📝 Хронология показаний:", log_preview, ""]

    if npc_rels:
        lines.append("🔗 Отношения:")
        for r in npc_rels:
            other = r["npc_b"] if r["npc_a"] == name else r["npc_a"]
            t     = int(r["trust_level"])
            mood  = "враг" if t < -50 else "недоверие" if t < 0 else "нейтрально" if t < 40 else "доверяет"
            lines.append(f"  → {other}: {t} ({mood})")
            if r.get("known_conflicts"):
                for cf in r["known_conflicts"].split("\n")[-2:]:
                    if cf.strip():
                        lines.append(f"     {cf.strip()}")

    await update.message.reply_text("\n".join(lines))


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id   = update.effective_chat.id
    user_text = update.message.text

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    # Определяем упомянутых NPC → подгружаем их детальный контекст
    extra = ""
    npc_names = ["Марта", "Павел", "Олег", "Игорь",
                 "Marта", "Pavel", "Oleg", "Igor"]
    for name in ["Марта", "Павел", "Олег", "Игорь"]:
        if name.lower() in user_text.lower():
            extra += "\n\n" + build_npc_target_context(name)

    try:
        reply = await ask_openai(chat_id, user_text, extra)
    except Exception as e:
        log.error("OpenAI error: %s", e)
        await update.message.reply_text("⚠️ Ошибка AI. Попробуй ещё раз.")
        return

    await update.message.reply_text(reply)

    # Фоновый анализ — не блокирует пользователя
    context.application.create_task(analyze_and_persist(reply))


# ██████████████████████████████████████████████████████████████
#  ТОЧКА ВХОДА
# ██████████████████████████████████████████████████████████████

def main() -> None:
    log.info("Запуск — Дело №417 Level 3 (Multi-NPC Network)")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("clear",  cmd_clear))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("npc",    cmd_npc))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    log.info("Бот запущен. Level 3 активен.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
