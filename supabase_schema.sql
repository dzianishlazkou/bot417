-- ══════════════════════════════════════════════════════════════
--  ДЕЛО №417 — LEVEL 3 SQL Schema
--  Supabase → SQL Editor → New query → Run All
-- ══════════════════════════════════════════════════════════════

-- ════════════════════════════════
--  1. NPC_TABLE — личности
-- ════════════════════════════════
CREATE TABLE IF NOT EXISTS npc_table (
    id              BIGSERIAL PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    role            TEXT NOT NULL,          -- wife / guard / partner / son
    personality     TEXT,
    truth_layer     TEXT,                   -- что знает на самом деле (скрыто от игрока)
    stress_level    INT  NOT NULL DEFAULT 0
                    CHECK (stress_level BETWEEN 0 AND 100),
    emotional_state TEXT NOT NULL DEFAULT 'calm'
                    CHECK (emotional_state IN ('calm','defensive','stressed','breaking','broken'))
);

-- Начальные NPC
INSERT INTO npc_table (name, role, personality, truth_layer, stress_level, emotional_state)
VALUES
  ('Марта', 'wife',
   'Сдержанная, контролирует эмоции. Говорит мало, но точно.',
   'Знала о финансовых проблемах мужа. Видела Павла той ночью у черного входа.',
   0, 'calm'),

  ('Павел', 'guard',
   'Нервный, избегает зрительного контакта. Быстро раздражается.',
   'Не был на посту в момент гибели. Куда ходил — не говорит.',
   0, 'calm'),

  ('Олег', 'partner',
   'Уверенный, деловой. Умеет уходить от прямых ответов.',
   'Финансовый конфликт с жертвой не был разрешён. Последняя встреча была за 2 дня до гибели.',
   0, 'calm'),

  ('Игорь', 'son',
   'Замкнутый, минимум слов. Скрывает последний разговор с отцом.',
   'Отец звонил ему за 40 минут до смерти. Разговор длился 3 минуты.',
   0, 'calm')
ON CONFLICT (name) DO NOTHING;


-- ════════════════════════════════
--  2. NPC_MEMORY — хронология показаний
-- ════════════════════════════════
CREATE TABLE IF NOT EXISTS npc_memory (
    id              BIGSERIAL PRIMARY KEY,
    npc_name        TEXT        NOT NULL UNIQUE,
    memory_log      TEXT        NOT NULL DEFAULT '',   -- накопительный лог
    last_statement  TEXT,                              -- последнее заявление
    last_update     TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- ════════════════════════════════
--  3. RELATIONS — сеть отношений
-- ════════════════════════════════
CREATE TABLE IF NOT EXISTS relations (
    id                BIGSERIAL PRIMARY KEY,
    npc_a             TEXT NOT NULL,
    npc_b             TEXT NOT NULL,
    relationship_type TEXT NOT NULL,   -- family / conflict / business / neutral
    trust_level       INT  NOT NULL DEFAULT 0
                      CHECK (trust_level BETWEEN -100 AND 100),
    known_conflicts   TEXT DEFAULT ''  -- накопительный лог конфликтов
);

-- Начальная сеть отношений
INSERT INTO relations (npc_a, npc_b, relationship_type, trust_level, known_conflicts)
VALUES
  ('Марта', 'Павел',  'neutral',   10,  ''),
  ('Марта', 'Олег',   'conflict', -20,  '• Марта не доверяла деловым отношениям мужа с Олегом'),
  ('Марта', 'Игорь',  'family',   60,  ''),
  ('Павел',  'Олег',  'neutral',   0,  ''),
  ('Павел',  'Игорь', 'neutral',   5,  ''),
  ('Олег',   'Игорь', 'neutral',  -10, '• Игорь винит Олега в финансовом давлении на отца')
ON CONFLICT DO NOTHING;


-- ════════════════════════════════
--  4. CONTRADICTIONS
-- ════════════════════════════════
CREATE TABLE IF NOT EXISTS contradictions (
    id            BIGSERIAL PRIMARY KEY,
    npc_a         TEXT NOT NULL,
    npc_b         TEXT,
    conflict_text TEXT NOT NULL,
    severity      INT  NOT NULL DEFAULT 1
                  CHECK (severity BETWEEN 1 AND 3),
    status        TEXT NOT NULL DEFAULT 'open'
                  CHECK (status IN ('open', 'escalated', 'resolved')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- ════════════════════════════════
--  5. CASE_EVIDENCE
-- ════════════════════════════════
CREATE TABLE IF NOT EXISTS case_evidence (
    id         BIGSERIAL PRIMARY KEY,
    type       TEXT NOT NULL
               CHECK (type IN ('fact', 'soft', 'contradiction')),
    content    TEXT NOT NULL,
    source_npc TEXT,
    timestamp  TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- ════════════════════════════════
--  Индексы
-- ════════════════════════════════
CREATE INDEX IF NOT EXISTS idx_npc_name          ON npc_table (name);
CREATE INDEX IF NOT EXISTS idx_npc_stress        ON npc_table (stress_level DESC);
CREATE INDEX IF NOT EXISTS idx_memory_npc        ON npc_memory (npc_name);
CREATE INDEX IF NOT EXISTS idx_relations_a       ON relations (npc_a);
CREATE INDEX IF NOT EXISTS idx_relations_b       ON relations (npc_b);
CREATE INDEX IF NOT EXISTS idx_contra_status     ON contradictions (status);
CREATE INDEX IF NOT EXISTS idx_contra_severity   ON contradictions (severity DESC);
CREATE INDEX IF NOT EXISTS idx_evidence_ts       ON case_evidence (timestamp DESC);


-- ════════════════════════════════
--  Отключить RLS (сервисный ключ)
-- ════════════════════════════════
ALTER TABLE npc_table      DISABLE ROW LEVEL SECURITY;
ALTER TABLE npc_memory     DISABLE ROW LEVEL SECURITY;
ALTER TABLE relations      DISABLE ROW LEVEL SECURITY;
ALTER TABLE contradictions DISABLE ROW LEVEL SECURITY;
ALTER TABLE case_evidence  DISABLE ROW LEVEL SECURITY;


-- ════════════════════════════════
--  Проверка
-- ════════════════════════════════
SELECT 'npc_table'      AS tbl, COUNT(*) AS rows FROM npc_table
UNION ALL
SELECT 'npc_memory'     AS tbl, COUNT(*) AS rows FROM npc_memory
UNION ALL
SELECT 'relations'      AS tbl, COUNT(*) AS rows FROM relations
UNION ALL
SELECT 'contradictions' AS tbl, COUNT(*) AS rows FROM contradictions
UNION ALL
SELECT 'case_evidence'  AS tbl, COUNT(*) AS rows FROM case_evidence;
