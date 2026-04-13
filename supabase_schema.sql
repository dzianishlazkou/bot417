-- ══════════════════════════════════════════════════════════════
--  ДЕЛО №417 — LEVEL 3 · MULTI-PLAYER EDITION
--  Каждый игрок (chat_id) получает свою изолированную игру.
--
--  Supabase → SQL Editor → New query → Run All
-- ══════════════════════════════════════════════════════════════


-- ════════════════════════════════════════════
--  ШАБЛОНЫ (глобальные, не принадлежат игроку)
--  Копируются в игровые таблицы при /start
-- ════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS tpl_npc (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    role        TEXT NOT NULL,
    personality TEXT,
    truth_layer TEXT
);

INSERT INTO tpl_npc (name, role, personality, truth_layer) VALUES
  ('Марта', 'wife',
   'Сдержанная, контролирует эмоции. Говорит мало, но точно.',
   'Знала о финансовых проблемах мужа. Видела Павла той ночью у чёрного входа.'),

  ('Павел', 'guard',
   'Нервный, избегает зрительного контакта. Быстро раздражается.',
   'Не был на посту в момент гибели. Куда ходил — не говорит.'),

  ('Олег', 'partner',
   'Уверенный, деловой. Умеет уходить от прямых ответов.',
   'Финансовый конфликт с жертвой не был разрешён. Последняя встреча — за 2 дня до гибели.'),

  ('Игорь', 'son',
   'Замкнутый, минимум слов. Скрывает последний разговор с отцом.',
   'Отец звонил ему за 40 минут до смерти. Разговор длился 3 минуты.')
ON CONFLICT (name) DO NOTHING;


CREATE TABLE IF NOT EXISTS tpl_relations (
    id                BIGSERIAL PRIMARY KEY,
    npc_a             TEXT NOT NULL,
    npc_b             TEXT NOT NULL,
    relationship_type TEXT NOT NULL,
    trust_level       INT  NOT NULL DEFAULT 0,
    known_conflicts   TEXT DEFAULT ''
);

INSERT INTO tpl_relations (npc_a, npc_b, relationship_type, trust_level, known_conflicts) VALUES
  ('Марта', 'Павел',  'neutral',   10, ''),
  ('Марта', 'Олег',   'conflict', -20, '• Марта не доверяла деловым отношениям мужа с Олегом'),
  ('Марта', 'Игорь',  'family',   60,  ''),
  ('Павел',  'Олег',  'neutral',   0,  ''),
  ('Павел',  'Игорь', 'neutral',   5,  ''),
  ('Олег',   'Игорь', 'neutral', -10, '• Игорь винит Олега в финансовом давлении на отца')
ON CONFLICT DO NOTHING;


-- ════════════════════════════════════════════
--  ИГРОВЫЕ ТАБЛИЦЫ (player_id = Telegram chat_id)
-- ════════════════════════════════════════════

-- 1. NPC состояние игрока
CREATE TABLE IF NOT EXISTS npc_table (
    id              BIGSERIAL PRIMARY KEY,
    player_id       BIGINT NOT NULL,
    name            TEXT   NOT NULL,
    role            TEXT   NOT NULL,
    personality     TEXT,
    truth_layer     TEXT,
    stress_level    INT    NOT NULL DEFAULT 0
                    CHECK (stress_level BETWEEN 0 AND 100),
    emotional_state TEXT   NOT NULL DEFAULT 'calm'
                    CHECK (emotional_state IN ('calm','defensive','stressed','breaking','broken')),
    UNIQUE (player_id, name)
);

-- 2. Хронология показаний
CREATE TABLE IF NOT EXISTS npc_memory (
    id              BIGSERIAL PRIMARY KEY,
    player_id       BIGINT NOT NULL,
    npc_name        TEXT   NOT NULL,
    memory_log      TEXT   NOT NULL DEFAULT '',
    last_statement  TEXT,
    last_update     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (player_id, npc_name)
);

-- 3. Сеть отношений
CREATE TABLE IF NOT EXISTS relations (
    id                BIGSERIAL PRIMARY KEY,
    player_id         BIGINT NOT NULL,
    npc_a             TEXT   NOT NULL,
    npc_b             TEXT   NOT NULL,
    relationship_type TEXT   NOT NULL,
    trust_level       INT    NOT NULL DEFAULT 0
                      CHECK (trust_level BETWEEN -100 AND 100),
    known_conflicts   TEXT   DEFAULT '',
    UNIQUE (player_id, npc_a, npc_b)
);

-- 4. Противоречия
CREATE TABLE IF NOT EXISTS contradictions (
    id            BIGSERIAL PRIMARY KEY,
    player_id     BIGINT NOT NULL,
    npc_a         TEXT   NOT NULL,
    npc_b         TEXT,
    conflict_text TEXT   NOT NULL,
    severity      INT    NOT NULL DEFAULT 1
                  CHECK (severity BETWEEN 1 AND 3),
    status        TEXT   NOT NULL DEFAULT 'open'
                  CHECK (status IN ('open','escalated','resolved')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 5. Улики
CREATE TABLE IF NOT EXISTS case_evidence (
    id         BIGSERIAL PRIMARY KEY,
    player_id  BIGINT NOT NULL,
    type       TEXT   NOT NULL
               CHECK (type IN ('fact','soft','contradiction')),
    content    TEXT   NOT NULL,
    source_npc TEXT,
    timestamp  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 6. Реестр игроков (создаётся при /start)
CREATE TABLE IF NOT EXISTS players (
    player_id   BIGINT PRIMARY KEY,
    tg_name     TEXT,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen   TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- ════════════════════════════════════════════
--  Индексы
-- ════════════════════════════════════════════

CREATE INDEX IF NOT EXISTS idx_npc_player       ON npc_table      (player_id);
CREATE INDEX IF NOT EXISTS idx_npc_stress       ON npc_table      (player_id, stress_level DESC);
CREATE INDEX IF NOT EXISTS idx_mem_player       ON npc_memory     (player_id);
CREATE INDEX IF NOT EXISTS idx_rel_player       ON relations      (player_id);
CREATE INDEX IF NOT EXISTS idx_contra_player    ON contradictions (player_id, status);
CREATE INDEX IF NOT EXISTS idx_contra_severity  ON contradictions (player_id, severity DESC);
CREATE INDEX IF NOT EXISTS idx_ev_player        ON case_evidence  (player_id, timestamp DESC);


-- ════════════════════════════════════════════
--  Отключить RLS
-- ════════════════════════════════════════════

ALTER TABLE tpl_npc        DISABLE ROW LEVEL SECURITY;
ALTER TABLE tpl_relations  DISABLE ROW LEVEL SECURITY;
ALTER TABLE npc_table      DISABLE ROW LEVEL SECURITY;
ALTER TABLE npc_memory     DISABLE ROW LEVEL SECURITY;
ALTER TABLE relations      DISABLE ROW LEVEL SECURITY;
ALTER TABLE contradictions DISABLE ROW LEVEL SECURITY;
ALTER TABLE case_evidence  DISABLE ROW LEVEL SECURITY;
ALTER TABLE players        DISABLE ROW LEVEL SECURITY;


-- ════════════════════════════════════════════
--  Проверка
-- ════════════════════════════════════════════
SELECT 'tpl_npc'       AS tbl, COUNT(*) FROM tpl_npc
UNION ALL SELECT 'tpl_relations', COUNT(*) FROM tpl_relations
UNION ALL SELECT 'npc_table',     COUNT(*) FROM npc_table
UNION ALL SELECT 'npc_memory',    COUNT(*) FROM npc_memory
UNION ALL SELECT 'relations',     COUNT(*) FROM relations
UNION ALL SELECT 'contradictions',COUNT(*) FROM contradictions
UNION ALL SELECT 'case_evidence', COUNT(*) FROM case_evidence
UNION ALL SELECT 'players',       COUNT(*) FROM players;
