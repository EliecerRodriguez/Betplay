-- =============================================================================
-- Betplay NBA — Configuración Neon (PostgreSQL serverless)
-- =============================================================================
-- Ejecutar este script una sola vez en Neon > SQL Editor (Dashboard → SQL).
-- Requisitos:
--   1. Proyecto Neon creado (plan gratuito).
--   2. Variable de entorno DATABASE_URL configurada (local y GitHub Actions).
--
-- NOTA: Neon NO dispone de las extensiones pg_cron / pg_net de Supabase.
--       La orquestación diaria (reconcile + pipelines NBA/ATP) la ejecuta
--       GitHub Actions (workflows .github/workflows/*.yml), por lo que estos
--       jobs NO se recrean aquí. Este script solo define esquema y vistas.
-- =============================================================================

-- ── 1. Migración: añadir columna home_win a predictions si no existe ──────────
ALTER TABLE IF EXISTS predictions
  ADD COLUMN IF NOT EXISTS home_win INTEGER;

-- ── 2. Crear tabla line_scores si no existe ───────────────────────────────────
CREATE TABLE IF NOT EXISTS line_scores (
    id         SERIAL PRIMARY KEY,
    game_id    VARCHAR(20)  NOT NULL,
    game_date  DATE,
    team_id    INTEGER      NOT NULL,
    pts        INTEGER,
    fetch_date DATE,
    created_at TIMESTAMPTZ  DEFAULT now(),
    CONSTRAINT uq_line_score_game_team UNIQUE (game_id, team_id)
);

-- Índices de rendimiento
CREATE INDEX IF NOT EXISTS idx_line_scores_game_date ON line_scores(game_date);
CREATE INDEX IF NOT EXISTS idx_predictions_game_date ON predictions(game_date);
CREATE INDEX IF NOT EXISTS idx_predictions_home_win  ON predictions(home_win)
    WHERE home_win IS NULL;

-- ── 3. Vista de precisión del modelo ─────────────────────────────────────────
-- Usada por el endpoint /api/accuracy para leer directamente desde la BD.
CREATE OR REPLACE VIEW v_model_accuracy AS
SELECT
    model_version,
    COUNT(*)                                          AS total_predictions,
    SUM(CASE WHEN home_win IS NOT NULL THEN 1 END)    AS resolved_predictions,
    ROUND(
        100.0 * SUM(
            CASE
                WHEN home_win IS NOT NULL
                 AND (
                     (predicted_winner = 'home' AND home_win = 1)
                  OR (predicted_winner = 'away' AND home_win = 0)
                 )
                THEN 1 ELSE 0
            END
        ) / NULLIF(SUM(CASE WHEN home_win IS NOT NULL THEN 1 END), 0),
        1
    )                                                 AS accuracy_pct,
    AVG(CASE WHEN home_win IS NOT NULL
             THEN ABS(home_win_prob - home_win) END)  AS avg_prob_error,
    MIN(game_date)                                    AS first_game,
    MAX(game_date)                                    AS last_game
FROM predictions
GROUP BY model_version
ORDER BY model_version;

-- ── 4. Vista de historial del backtest ───────────────────────────────────────
CREATE OR REPLACE VIEW v_backtest_history AS
SELECT
    p.game_date,
    p.game_id,
    p.home_team_id,
    p.visitor_team_id,
    p.predicted_winner,
    p.home_win_prob,
    p.away_win_prob,
    p.home_win                AS actual_result,
    p.model_version,
    CASE
        WHEN p.home_win IS NULL THEN 'pending'
        WHEN (p.predicted_winner = 'home' AND p.home_win = 1)
          OR (p.predicted_winner = 'away' AND p.home_win = 0) THEN 'correct'
        ELSE 'incorrect'
    END                       AS outcome
FROM predictions p
ORDER BY p.game_date DESC, p.game_id;

-- ── 5. Orquestación diaria ──────────────────────────────────────────────────
-- Neon no ofrece pg_cron ni pg_net. La automatización diaria se mantiene en
-- GitHub Actions (ver .github/workflows/):
--   • reconcile.yml      → 08:00 UTC (actualiza predictions.home_win)
--   • daily_pipeline.yml → tras reconcile (pipeline NBA)
--   • atp_pipeline.yml   → tras reconcile (pipeline ATP)
--   • catchup.yml        → manual (reconciliación de días pasados)
-- Para pruebas locales: database/cron_local.bat (opcional, legacy).
-- =============================================================================
