"""
╔══════════════════════════════════════════════════════════════╗
║     DETECTIVE BOT — MULTI-CASE · MULTI-PLAYER · LEVEL 3     ║
║                                                              ║
║  Дело №417 (рус) — Смерть Войцеховского                     ║
║  Sprawa nr 7 (pol) — Zatoka Spokoju                          ║
║                                                              ║
║  • Каждый игрок выбирает дело при /start                     ║
║  • Полная изоляция по player_id + scenario_id                ║
║  • Бот отвечает на языке игрока (авто-детект)                ║
║  • Breaking system: 5 стадий слома NPC                       ║
║  • Lie network: ложь одного влияет на других                 ║
╚══════════════════════════════════════════════════════════════╝

УСТАНОВКА:
    pip install python-telegram-bot openai supabase

ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ:
    TELEGRAM_TOKEN · OPENAI_API_KEY · SUPABASE_URL · SUPABASE_KEY
"""

import os, re, json, logging
from datetime import datetime, timezone
from typing import Optional

from openai import AsyncOpenAI
from supabase import create_client, Client
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, ContextTypes,
    MessageHandler, CommandHandler,
    CallbackQueryHandler, filters,
)

# ══════════════════════════════════════════════════════════════
#  КОНФИГ
# ══════════════════════════════════════════════════════════════
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "ВАШ_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "ВАШ_KEY")
SUPABASE_URL   = os.getenv("SUPABASE_URL",   "https://ВАШ.supabase.co")
SUPABASE_KEY   = os.getenv("SUPABASE_KEY",   "ВАШ_ANON_KEY")
OPENAI_MODEL   = "gpt-4o"

STRESS_INCREMENT = {1: 10, 2: 20, 3: 35}
TRUST_DECREMENT  = {1: -5, 2: -15, 3: -30}

# ══════════════════════════════════════════════════════════════
#  MASTER PROMPTS — по одному на каждое дело
# ══════════════════════════════════════════════════════════════

MASTER_PROMPTS = {

"case417": """
Ты — движок симуляции дела №417. Гибель Александра Войцеховского.
Официальная версия: самоубийство. Реальность — неизвестна.

Отвечай на том языке, на котором пишет игрок.

━━━ УЧАСТНИКИ ━━━
• МАРТА  — жена. Сдержанная. Знает больше, чем говорит.
• ПАВЕЛ  — охранник. Нервничает. Что-то скрывает о той ночи.
• ОЛЕГ   — деловой партнёр. Уверен в себе. Финансовый конфликт с жертвой.
• ИГОРЬ  — сын. Замкнутый. Скрывает последний звонок отца.

━━━ ПРАВИЛА ━━━
1. Каждый NPC видит только свой фрагмент событий.
2. NPC не знают, что говорят другие — если игрок сам не скажет им.
3. trust_level влияет на готовность говорить: низкий → защита, высокий → откровенность.
4. Ложь одного NPC противоречит версии другого — используй это.
5. СТАДИИ СЛОМА управляются stress_level из контекста:
   calm(0-39) → держит версию
   defensive(40-59) → уклоняется, агрессирует
   stressed(60-79) → паузы, оговорки
   breaking(80-99) → срывается, противоречит себе
   broken(100) → говорит правду или замолкает

━━━ ФОРМАТ ━━━
[Имя NPC — состояние]
Речь NPC...

Паузы (—), многоточия (...), исправления на ходу.
Не объясняй систему. Ты — среда, не рассказчик.
""".strip(),

"sprawa7": """
Jesteś silnikiem symulacji Sprawy nr 7. Śmierć Roberta Kalinowskiego.
Oficjalna wersja policji: nieszczęśliwy wypadek. Rzeczywistość — nieznana.

Odpowiadaj w języku, którym pisze gracz.

━━━ UCZESTNICY ━━━
• MAREK ZAWADZKI  — wspólnik biznesowy. Spokojny, rzeczowy. Ma coś do ukrycia.
• ALICJA KALINOWSKA — żona ofiary. Elegancka, emocjonalna. Podejrzewa Marka.
• TOMASZ WRONA    — barman. Małomówny. Boi się. Ma córkę Zosię.
• ZOSIA WRONA     — córka barmana, 16 lat. Nieufna. Widziała więcej niż mówi.
• KOMISARZ HELENA BĄK — policjantka. Pragmatyczna. Sprawa miała być zamknięta szybko.

━━━ ZASADY ━━━
1. Każda postać widzi tylko swój fragment wydarzeń.
2. NPC nie wiedzą co mówią inni — chyba że gracz im powie.
3. trust_level wpływa na gotowość mówienia.
4. Kłamstwo jednego NPC może być sprzeczne z wersją innego.
5. STADIA ZAŁAMANIA zarządzane przez stress_level z kontekstu:
   calm(0-39) → pewny siebie, trzyma wersję
   defensive(40-59) → unika, agresywny
   stressed(60-79) → przerwy, przejęzyczenia
   breaking(80-99) → sprzeczności, załamanie emocjonalne
   broken(100) → mówi prawdę lub milczy

━━━ FORMAT ━━━
[Imię NPC — stan]
Wypowiedź NPC...

Pauzy (—), wielokropki (...), poprawki w locie.
Nie tłumacz systemu. Jesteś środowiskiem, nie narratorem.
""".strip(),

}

# ══════════════════════════════════════════════════════════════
#  PROMPTS АНАЛИЗА
# ══════════════════════════════════════════════════════════════

ANALYSIS_PROMPT = """
You are an analytical module for a detective investigation game.
Parse the NPC response text and extract structured data.

Return ONLY valid JSON without markdown:

{
  "npc_updates": [
    {
      "npc_name": "exact NPC name from the text",
      "new_statement": "brief summary of what they said (1-3 sentences)",
      "stress_delta": 0,
      "emotional_state": "calm|defensive|stressed|breaking|broken"
    }
  ],
  "evidence": [
    {"type": "fact|soft|contradiction", "content": "...", "source_npc": "name or null"}
  ],
  "contradictions": [
    {"npc_a": "...", "npc_b": "... or null", "conflict_text": "...", "severity": 1}
  ],
  "relation_updates": [
    {"npc_a": "...", "npc_b": "...", "trust_delta": -10, "reason": "..."}
  ]
}

stress_delta: 0-35. severity: 1/2/3. trust_delta: -30..+10. Empty sections = [].
""".strip()

CONTRADICTION_DETECTOR_PROMPT = """
You are a contradiction detector for witness statements.
Given: PAST MEMORY of NPC and NEW STATEMENT.
Find semantic contradictions.

Return ONLY JSON (no markdown):
{"has_contradiction": true, "severity": 2, "conflict_text": "...", "stress_increase": 20}
or if none:
{"has_contradiction": false, "severity": 0, "conflict_text": "", "stress_increase": 0}

severity 1=minor / 2=significant / 3=critical. stress_increase: 10/20/35.
""".strip()

RELATION_IMPACT_PROMPT = """
You analyze how one NPC's lie affects others through the relationship network.
Return ONLY JSON (no markdown):
{"impacts": [{"affected_npc": "Name", "reason": "...", "trust_delta": -15, "stress_delta": 5}]}
If no impact: {"impacts": []}
""".strip()

# ══════════════════════════════════════════════════════════════
#  ЛОГГЕР + КЛИЕНТЫ
# ══════════════════════════════════════════════════════════════
logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

openai_client: AsyncOpenAI = AsyncOpenAI(api_key=OPENAI_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

conversation_history: dict[int, list[dict]] = {}
MAX_HISTORY = 40

# Игроки ожидающие выбора дела
pending_scenario: set[int] = set()


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

PID = int
SID = str   # scenario_id


# ██████████████████████████████████████████████████████████████
#  SUPABASE — все запросы изолированы по (player_id, scenario_id)
# ██████████████████████████████████████████████████████████████

# ── PLAYERS ───────────────────────────────────────────────────

def db_get_player(pid: PID) -> Optional[dict]:
    try:
        r = supabase.table("players").select("*").eq("player_id", pid).limit(1).execute()
        return r.data[0] if r.data else None
    except Exception as e:
        log.warning("db_get_player[%d]: %s", pid, e)
        return None

def db_get_player_scenario(pid: PID) -> Optional[str]:
    p = db_get_player(pid)
    return p["scenario_id"] if p else None

def db_register_player(pid: PID, tg_name: str, scenario_id: str) -> None:
    try:
        supabase.table("players").upsert({
            "player_id":   pid,
            "tg_name":     tg_name,
            "scenario_id": scenario_id,
            "last_seen":   _now(),
        }).execute()
    except Exception as e:
        log.warning("db_register_player: %s", e)

def db_touch_player(pid: PID) -> None:
    try:
        supabase.table("players").update({"last_seen": _now()}).eq("player_id", pid).execute()
    except Exception:
        pass


# ── ИНИЦИАЛИЗАЦИЯ ИГРЫ ────────────────────────────────────────

def db_init_game(pid: PID, tg_name: str, sid: SID) -> None:
    """Копирует шаблоны NPC и отношений для игрока по выбранному делу."""
    try:
        db_register_player(pid, tg_name, sid)

        # Удаляем старые данные этого дела для игрока (если был reset)
        for table in ["npc_table", "npc_memory", "relations", "contradictions", "case_evidence"]:
            supabase.table(table).delete().eq("player_id", pid).eq("scenario_id", sid).execute()

        # Копируем NPC
        npcs = supabase.table("tpl_npc").select("*").eq("scenario_id", sid).execute().data or []
        for n in npcs:
            supabase.table("npc_table").insert({
                "player_id": pid, "scenario_id": sid,
                "name": n["name"], "role": n["role"],
                "personality": n.get("personality"),
                "truth_layer": n.get("truth_layer"),
                "lies": n.get("lies"),
                "stress_level": 0, "emotional_state": "calm",
            }).execute()

        # Копируем отношения
        rels = supabase.table("tpl_relations").select("*").eq("scenario_id", sid).execute().data or []
        for r in rels:
            supabase.table("relations").insert({
                "player_id": pid, "scenario_id": sid,
                "npc_a": r["npc_a"], "npc_b": r["npc_b"],
                "relationship_type": r["relationship_type"],
                "trust_level": r["trust_level"],
                "known_conflicts": r.get("known_conflicts", ""),
            }).execute()

        log.info("Game init: player=%d scenario=%s", pid, sid)
    except Exception as e:
        log.error("db_init_game[%d/%s]: %s", pid, sid, e)


# ── NPC_TABLE ─────────────────────────────────────────────────

def db_get_npc(pid: PID, sid: SID, name: str) -> Optional[dict]:
    try:
        r = (supabase.table("npc_table").select("*")
             .eq("player_id", pid).eq("scenario_id", sid).eq("name", name)
             .limit(1).execute())
        return r.data[0] if r.data else None
    except Exception as e:
        log.warning("db_get_npc[%d/%s/%s]: %s", pid, sid, name, e)
        return None

def db_get_all_npcs(pid: PID, sid: SID) -> list[dict]:
    try:
        r = (supabase.table("npc_table").select("*")
             .eq("player_id", pid).eq("scenario_id", sid).order("name").execute())
        return r.data or []
    except Exception as e:
        log.warning("db_get_all_npcs: %s", e)
        return []

def db_update_npc_stress(pid: PID, sid: SID, name: str, delta: int) -> int:
    try:
        npc = db_get_npc(pid, sid, name)
        if not npc:
            return 0
        new_s = min(100, max(0, int(npc["stress_level"]) + delta))
        new_state = _stress_to_state(new_s)
        supabase.table("npc_table").update({
            "stress_level": new_s, "emotional_state": new_state,
        }).eq("player_id", pid).eq("scenario_id", sid).eq("name", name).execute()
        log.info("[%d/%s] NPC[%s] stress %d→%d", pid, sid, name, npc["stress_level"], new_s)
        return new_s
    except Exception as e:
        log.warning("db_update_npc_stress: %s", e)
        return 0


# ── NPC_MEMORY ────────────────────────────────────────────────

def db_get_memory(pid: PID, sid: SID, npc_name: str) -> Optional[dict]:
    try:
        r = (supabase.table("npc_memory").select("*")
             .eq("player_id", pid).eq("scenario_id", sid).eq("npc_name", npc_name)
             .limit(1).execute())
        return r.data[0] if r.data else None
    except Exception as e:
        log.warning("db_get_memory: %s", e)
        return None

def db_append_memory(pid: PID, sid: SID, npc_name: str, statement: str) -> None:
    try:
        existing = db_get_memory(pid, sid, npc_name)
        ts = _now()
        if existing:
            new_log = existing["memory_log"] + f"\n[{ts[:10]}] {statement}"
            supabase.table("npc_memory").update({
                "memory_log": new_log, "last_statement": statement, "last_update": ts,
            }).eq("player_id", pid).eq("scenario_id", sid).eq("npc_name", npc_name).execute()
        else:
            supabase.table("npc_memory").insert({
                "player_id": pid, "scenario_id": sid, "npc_name": npc_name,
                "memory_log": f"[{ts[:10]}] {statement}",
                "last_statement": statement, "last_update": ts,
            }).execute()
    except Exception as e:
        log.warning("db_append_memory: %s", e)


# ── RELATIONS ─────────────────────────────────────────────────

def db_get_all_relations(pid: PID, sid: SID) -> list[dict]:
    try:
        r = (supabase.table("relations").select("*")
             .eq("player_id", pid).eq("scenario_id", sid).execute())
        return r.data or []
    except Exception as e:
        log.warning("db_get_all_relations: %s", e)
        return []

def db_update_trust(pid: PID, sid: SID, npc_a: str, npc_b: str, delta: int, reason: str = "") -> int:
    try:
        rels = db_get_all_relations(pid, sid)
        rel = next(
            (r for r in rels if
             (r["npc_a"] == npc_a and r["npc_b"] == npc_b) or
             (r["npc_a"] == npc_b and r["npc_b"] == npc_a)),
            None
        )
        if not rel:
            return 0
        new_trust = max(-100, min(100, int(rel["trust_level"]) + delta))
        conflicts = rel.get("known_conflicts") or ""
        if delta < 0 and reason:
            conflicts = (conflicts + f"\n• {reason}").strip()
        supabase.table("relations").update({
            "trust_level": new_trust, "known_conflicts": conflicts,
        }).eq("id", rel["id"]).execute()
        return new_trust
    except Exception as e:
        log.warning("db_update_trust: %s", e)
        return 0


# ── CONTRADICTIONS ────────────────────────────────────────────

def db_add_contradiction(pid: PID, sid: SID, npc_a: str, npc_b: Optional[str],
                         conflict_text: str, severity: int) -> None:
    try:
        supabase.table("contradictions").insert({
            "player_id": pid, "scenario_id": sid,
            "npc_a": npc_a, "npc_b": npc_b,
            "conflict_text": conflict_text, "severity": severity,
        }).execute()
    except Exception as e:
        log.warning("db_add_contradiction: %s", e)

def db_get_open_contradictions(pid: PID, sid: SID) -> list[dict]:
    try:
        r = (supabase.table("contradictions").select("*")
             .eq("player_id", pid).eq("scenario_id", sid)
             .in_("status", ["open", "escalated"])
             .order("severity", desc=True).limit(15).execute())
        return r.data or []
    except Exception as e:
        log.warning("db_get_open_contradictions: %s", e)
        return []


# ── CASE_EVIDENCE ─────────────────────────────────────────────

def db_add_evidence(pid: PID, sid: SID, ev_type: str, content: str, source_npc: Optional[str]) -> None:
    try:
        supabase.table("case_evidence").insert({
            "player_id": pid, "scenario_id": sid,
            "type": ev_type, "content": content,
            "source_npc": source_npc, "timestamp": _now(),
        }).execute()
    except Exception as e:
        log.warning("db_add_evidence: %s", e)


# ██████████████████████████████████████████████████████████████
#  КОНТЕКСТ ДЛЯ GPT
# ██████████████████████████████████████████████████████████════

def build_investigation_context(pid: PID, sid: SID) -> str:
    blocks = []

    npcs = db_get_all_npcs(pid, sid)
    if npcs:
        lines = ["━━━ ACTIVE NPC ━━━"]
        for n in npcs:
            state = n.get("emotional_state") or _stress_to_state(int(n["stress_level"]))
            bar   = "█" * (int(n["stress_level"]) // 10) + "░" * (10 - int(n["stress_level"]) // 10)
            mem   = db_get_memory(pid, sid, n["name"])
            last  = (mem["last_statement"][:80] + "…") if mem and mem.get("last_statement") else "—"
            lines.append(
                f"• {n['name']} [{n['role']}] stress={n['stress_level']} {bar} | {state}\n"
                f"  last: «{last}»"
            )
        blocks.append("\n".join(lines))

    rels = db_get_all_relations(pid, sid)
    if rels:
        lines = ["━━━ RELATIONS ━━━"]
        for r in rels:
            t    = int(r["trust_level"])
            mood = "hostile" if t < -30 else "distrust" if t < 0 else "neutral" if t < 40 else "trust"
            lines.append(f"• {r['npc_a']} ↔ {r['npc_b']} [{r['relationship_type']}] trust={t} ({mood})")
        blocks.append("\n".join(lines))

    contras = db_get_open_contradictions(pid, sid)
    if contras:
        lines = ["━━━ ACTIVE CONTRADICTIONS ━━━"]
        for c in contras:
            sev = "⚠️" * int(c["severity"])
            b   = c["npc_b"] or "self"
            lines.append(f"{sev} {c['npc_a']} ↔ {b}: {c['conflict_text']}")
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)

def build_npc_target_context(pid: PID, sid: SID, npc_name: str) -> str:
    npc = db_get_npc(pid, sid, npc_name)
    if not npc:
        return ""
    mem  = db_get_memory(pid, sid, npc_name)
    rels = db_get_all_relations(pid, sid)
    lines = [
        f"━━━ TARGET NPC: {npc_name} ━━━",
        f"role: {npc['role']}",
        f"personality: {npc.get('personality','—')}",
        f"stress: {npc['stress_level']} → {_stress_to_state(int(npc['stress_level']))}",
    ]
    if mem and mem.get("memory_log"):
        lines.append(f"\nMEMORY LOG:\n{mem['memory_log'][-600:]}")
    npc_rels = [r for r in rels if r["npc_a"] == npc_name or r["npc_b"] == npc_name]
    if npc_rels:
        lines.append("\nRELATIONS:")
        for r in npc_rels:
            other = r["npc_b"] if r["npc_a"] == npc_name else r["npc_a"]
            lines.append(f"  → {other}: trust={r['trust_level']}")
    return "\n".join(lines)


# ██████████████████████████████████████████████████████████████
#  СИСТЕМА СЛОМА
# ██████████████████████████████████████████████████████████████

async def _gpt_mini(system: str, user: str, max_tokens: int = 400) -> Optional[dict]:
    try:
        r = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"system","content":system},{"role":"user","content":user}],
            temperature=0.1, max_tokens=max_tokens,
        )
        return _parse_json(r.choices[0].message.content)
    except Exception as e:
        log.warning("_gpt_mini: %s", e)
        return None

async def run_contradiction_check(pid: PID, sid: SID, npc_name: str, new_statement: str) -> Optional[str]:
    mem = db_get_memory(pid, sid, npc_name)
    if not mem or not mem.get("memory_log"):
        return None
    result = await _gpt_mini(
        CONTRADICTION_DETECTOR_PROMPT,
        f"PAST MEMORY [{npc_name}]:\n{mem['memory_log']}\n\nNEW STATEMENT:\n{new_statement}"
    )
    if not result or not result.get("has_contradiction"):
        return None

    severity   = int(result.get("severity", 1))
    conflict   = result.get("conflict_text", "")
    stress_inc = int(result.get("stress_increase", STRESS_INCREMENT[severity]))

    new_stress = db_update_npc_stress(pid, sid, npc_name, stress_inc)
    new_state  = _stress_to_state(new_stress)

    db_add_contradiction(pid, sid, npc_name, None, conflict, severity)
    db_add_evidence(pid, sid, "contradiction", conflict, npc_name)

    sev_label = {1:"minor",2:"significant",3:"CRITICAL"}[severity]
    log.info("[%d/%s] BREAK[%s] %s stress→%d", pid, sid, npc_name, sev_label, new_stress)

    alert = (
        f"\n\n━━━ ⚡ BREAKING [{npc_name}] ━━━\n"
        f"Contradiction ({sev_label}): {conflict}\n"
        f"stress→{new_stress} | state→{new_state}\n"
    )
    if new_stress >= 100:
        alert += "💀 NPC BROKEN. Cannot hold version anymore.\n"
    elif new_stress >= 80:
        alert += "🔴 Near breaking. Involuntary admissions possible.\n"
    return alert

async def run_lie_network_effect(pid: PID, sid: SID, liar: str, conflict: str) -> None:
    rels = db_get_all_relations(pid, sid)
    if not rels:
        return
    rel_ctx = "\n".join(
        f"• {r['npc_a']} ↔ {r['npc_b']}: {r['relationship_type']}, trust={r['trust_level']}"
        for r in rels
    )
    result = await _gpt_mini(
        RELATION_IMPACT_PROMPT,
        f"LIAR NPC: {liar}\nCONTRADICTION: {conflict}\n\nRELATIONS:\n{rel_ctx}",
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
            db_update_trust(pid, sid, liar, affected, t_delta, reason)
        if affected and s_delta:
            db_update_npc_stress(pid, sid, affected, s_delta)


# ██████████████████████████████████████████████████████████████
#  OPENAI — ОСНОВНОЙ ЗАПРОС
# ██████████████████████████████████████████████████████████████

def get_history(pid: PID) -> list[dict]:
    return conversation_history.setdefault(pid, [])

def trim_history(pid: PID) -> None:
    h = conversation_history.get(pid, [])
    if len(h) > MAX_HISTORY:
        conversation_history[pid] = h[-MAX_HISTORY:]

async def ask_openai(pid: PID, sid: SID, user_text: str, extra: str = "") -> str:
    history = get_history(pid)
    history.append({"role": "user", "content": user_text})

    master = MASTER_PROMPTS.get(sid, MASTER_PROMPTS["case417"])
    ctx    = build_investigation_context(pid, sid)
    system = master + ("\n\n" + ctx if ctx else "") + extra

    response = await openai_client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{"role":"system","content":system}] + history,
        temperature=0.92,
        max_tokens=1600,
    )
    reply = response.choices[0].message.content.strip()
    history.append({"role": "assistant", "content": reply})
    trim_history(pid)
    return reply


# ██████████████████████████████████████████████████████████████
#  ФОНОВЫЙ АНАЛИЗ
# ██████████████████████████████████████████████████████████████

async def analyze_and_persist(pid: PID, sid: SID, reply: str) -> None:
    data = await _gpt_mini(ANALYSIS_PROMPT, reply, max_tokens=900)
    if not data:
        return

    for upd in data.get("npc_updates", []):
        name      = (upd.get("npc_name") or "").strip()
        statement = (upd.get("new_statement") or "").strip()
        if not name or not statement:
            continue
        alert = await run_contradiction_check(pid, sid, name, statement)
        if alert:
            conflict_lines = [l for l in alert.split("\n") if "Contradiction" in l or "Противоречие" in l]
            conflict_text  = conflict_lines[0].split(":", 1)[-1].strip() if conflict_lines else ""
            if conflict_text:
                await run_lie_network_effect(pid, sid, name, conflict_text)
        db_append_memory(pid, sid, name, statement)
        s_delta = int(upd.get("stress_delta", 0))
        if s_delta:
            db_update_npc_stress(pid, sid, name, s_delta)

    for ev in data.get("evidence", []):
        if ev.get("content"):
            db_add_evidence(pid, sid, ev.get("type","soft"), ev["content"], ev.get("source_npc"))

    for con in data.get("contradictions", []):
        if con.get("conflict_text"):
            sev = int(con.get("severity", 1))
            db_add_contradiction(pid, sid, con.get("npc_a","?"), con.get("npc_b"), con["conflict_text"], sev)
            if con.get("npc_b"):
                db_update_trust(pid, sid, con["npc_a"], con["npc_b"],
                                TRUST_DECREMENT.get(sev,-10), con["conflict_text"][:80])

    for rel in data.get("relation_updates", []):
        a, b, delta = rel.get("npc_a",""), rel.get("npc_b",""), int(rel.get("trust_delta",0))
        if a and b and delta:
            db_update_trust(pid, sid, a, b, delta, rel.get("reason",""))

    log.info("[%d/%s] ← %d NPC | %d ev | %d contra | %d rel", pid, sid,
             len(data.get("npc_updates",[])), len(data.get("evidence",[])),
             len(data.get("contradictions",[])), len(data.get("relation_updates",[])))


# ██████████████████████████████████████████████████████████████
#  TELEGRAM HANDLERS
# ██████████████████████████████████████████████████████████████

STATE_ICON = {"calm":"🟢","defensive":"🟡","stressed":"🟠","breaking":"🔴","broken":"💀"}

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pid = update.effective_chat.id
    conversation_history.pop(pid, None)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🇷🇺 Дело №417 — Смерть Войцеховского", callback_data="scenario:case417")],
        [InlineKeyboardButton("🇵🇱 Sprawa nr 7 — Zatoka Spokoju",      callback_data="scenario:sprawa7")],
    ])
    await update.message.reply_text(
        "🕵️ DETECTIVE BOT\n\n"
        "Wybierz sprawę / Выбери дело:\n",
        reply_markup=keyboard,
    )

async def callback_scenario(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    pid     = query.message.chat_id
    sid     = query.data.split(":")[1]
    tg_name = query.from_user.first_name or "Detective"

    db_init_game(pid, tg_name, sid)
    conversation_history.pop(pid, None)
    pending_scenario.discard(pid)

    if sid == "case417":
        text = (
            f"Дело №417 открыто, {tg_name}.\n\n"
            "Александр Войцеховский найден мёртвым.\n"
            "Официальная версия — самоубийство.\n\n"
            "• МАРТА — жена\n• ПАВЕЛ — охранник\n"
            "• ОЛЕГ — партнёр\n• ИГОРЬ — сын\n\n"
            "Задавай вопросы. Дави на противоречия.\n\n"
            "/status · /npc [имя] · /switch · /reset"
        )
    else:
        text = (
            f"Sprawa nr 7 otwarta, {tg_name}.\n\n"
            "Robert Kalinowski znaleziony martwy w jachtklubie.\n"
            "Oficjalna wersja — nieszczęśliwy wypadek.\n\n"
            "• MAREK ZAWADZKI — wspólnik\n• ALICJA KALINOWSKA — żona\n"
            "• TOMASZ WRONA — barman\n• ZOSIA WRONA — świadek\n"
            "• KOMISARZ HELENA BĄK — policja\n\n"
            "Zadawaj pytania. Szukaj sprzeczności.\n\n"
            "/status · /npc [imię] · /switch · /reset"
        )

    await query.edit_message_text(text)

async def cmd_switch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Переключиться на другое дело."""
    await cmd_start(update, context)

async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pid     = update.effective_chat.id
    tg_name = update.effective_user.first_name or "Detective"
    sid     = db_get_player_scenario(pid)
    if not sid:
        await update.message.reply_text("Сначала выбери дело: /start")
        return
    db_init_game(pid, tg_name, sid)
    conversation_history.pop(pid, None)
    label = "Дело №417 сброшено." if sid == "case417" else "Sprawa nr 7 zresetowana."
    await update.message.reply_text(f"🔄 {label}")

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conversation_history.pop(update.effective_chat.id, None)
    await update.message.reply_text("🗑️ Historia sesji wyczyszczona / История сессии сброшена.")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pid = update.effective_chat.id
    sid = db_get_player_scenario(pid)
    if not sid:
        await update.message.reply_text("Выбери дело: /start")
        return
    await context.bot.send_chat_action(chat_id=pid, action="typing")
    try:
        title = "Дело №417" if sid == "case417" else "Sprawa nr 7"
        lines = [f"📋 {title}\n"]

        npcs = db_get_all_npcs(pid, sid)
        if npcs:
            lines.append("🧠 NPC:")
            for n in npcs:
                s     = int(n["stress_level"])
                state = n.get("emotional_state") or _stress_to_state(s)
                bar   = "█" * (s // 10) + "░" * (10 - s // 10)
                lines.append(f"  {STATE_ICON.get(state,'⚪')} {n['name']} [{n['role']}]: [{bar}] {s}")

        lines.append("")
        rels = db_get_all_relations(pid, sid)
        if rels:
            lines.append("🔗 Relations:")
            for r in rels:
                t   = int(r["trust_level"])
                bar = "█" * max(0,(t+100)//20) + "░" * (10-max(0,(t+100)//20))
                lines.append(f"  {r['npc_a']} ↔ {r['npc_b']}: [{bar}] {t}")

        lines.append("")
        contras = db_get_open_contradictions(pid, sid)
        if contras:
            lines.append("⚡ Contradictions:")
            for c in contras[:5]:
                sev = "⚠️" * int(c["severity"])
                b   = c["npc_b"] or "self"
                lines.append(f"  {sev} {c['npc_a']} ↔ {b}: {c['conflict_text'][:60]}")
        else:
            lines.append("⚡ No contradictions yet")

        try:
            ev = (supabase.table("case_evidence").select("type,content")
                  .eq("player_id", pid).eq("scenario_id", sid)
                  .order("id", desc=True).limit(4).execute())
            if ev.data:
                tag = {"fact":"🔵","soft":"🟡","contradiction":"🔴"}
                lines.append("\n📦 Evidence:")
                for e in ev.data:
                    lines.append(f"  {tag.get(e['type'],'⚪')} {e['content'][:70]}")
        except Exception:
            pass

        await update.message.reply_text("\n".join(lines))
    except Exception as ex:
        log.error("cmd_status: %s", ex)
        await update.message.reply_text("⚠️ Error fetching data.")

async def cmd_npc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pid  = update.effective_chat.id
    sid  = db_get_player_scenario(pid)
    args = context.args
    if not sid:
        await update.message.reply_text("Выбери дело: /start"); return
    if not args:
        await update.message.reply_text("Usage: /npc Марта  или  /npc Marek"); return

    name = " ".join(args).strip()
    # Попытка найти по точному имени или началу имени
    all_npcs = db_get_all_npcs(pid, sid)
    matched  = next((n for n in all_npcs if n["name"].lower().startswith(name.lower())), None)
    if not matched:
        await update.message.reply_text(f"NPC '{name}' not found. Try /status for names."); return

    npc_name = matched["name"]
    await context.bot.send_chat_action(chat_id=pid, action="typing")

    npc  = matched
    mem  = db_get_memory(pid, sid, npc_name)
    rels = db_get_all_relations(pid, sid)
    npc_rels = [r for r in rels if r["npc_a"] == npc_name or r["npc_b"] == npc_name]

    s     = int(npc["stress_level"])
    state = npc.get("emotional_state") or _stress_to_state(s)
    bar   = "█" * (s // 10) + "░" * (10 - s // 10)

    lines = [
        f"👤 {npc_name} [{npc['role']}]",
        f"State:  {STATE_ICON.get(state,'⚪')} {state}",
        f"Stress: [{bar}] {s}",
        f"Personality: {npc.get('personality','—')}",
        "",
    ]
    if mem and mem.get("memory_log"):
        lines += ["📝 Statement log:", mem["memory_log"][-400:], ""]
    if npc_rels:
        lines.append("🔗 Relations:")
        for r in npc_rels:
            other = r["npc_b"] if r["npc_a"] == npc_name else r["npc_a"]
            t     = int(r["trust_level"])
            mood  = "hostile" if t<-50 else "distrust" if t<0 else "neutral" if t<40 else "trusts"
            lines.append(f"  → {other}: {t} ({mood})")
            if r.get("known_conflicts"):
                for cf in r["known_conflicts"].split("\n")[-2:]:
                    if cf.strip():
                        lines.append(f"     {cf.strip()}")

    await update.message.reply_text("\n".join(lines))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pid       = update.effective_chat.id
    user_text = update.message.text

    # Авто-инициализация без /start
    sid = db_get_player_scenario(pid)
    if not sid:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🇷🇺 Дело №417", callback_data="scenario:case417")],
            [InlineKeyboardButton("🇵🇱 Sprawa nr 7", callback_data="scenario:sprawa7")],
        ])
        await update.message.reply_text("Wybierz sprawę / Выбери дело:", reply_markup=keyboard)
        return

    db_touch_player(pid)
    await context.bot.send_chat_action(chat_id=pid, action="typing")

    # Детальный контекст для упомянутых NPC
    extra = ""
    all_npcs = db_get_all_npcs(pid, sid)
    for npc in all_npcs:
        if npc["name"].lower() in user_text.lower():
            extra += "\n\n" + build_npc_target_context(pid, sid, npc["name"])

    try:
        reply = await ask_openai(pid, sid, user_text, extra)
    except Exception as e:
        log.error("OpenAI[%d]: %s", pid, e)
        await update.message.reply_text("⚠️ AI error. Try again.")
        return

    await update.message.reply_text(reply)
    context.application.create_task(analyze_and_persist(pid, sid, reply))


# ██████████████████████████████████████████████████████████████
#  ТОЧКА ВХОДА
# ██████████████████████████████████████████████████████████████

def main() -> None:
    log.info("Start — Detective Bot · Multi-Case · Multi-Player")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("clear",  cmd_clear))
    app.add_handler(CommandHandler("reset",  cmd_reset))
    app.add_handler(CommandHandler("switch", cmd_switch))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("npc",    cmd_npc))
    app.add_handler(CallbackQueryHandler(callback_scenario, pattern="^scenario:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    log.info("Bot running. Two cases available.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
