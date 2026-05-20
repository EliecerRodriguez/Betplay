-- =============================================================================
-- Betplay NBA — Configuración Supabase
-- =============================================================================
-- Ejecutar este script en Supabase > SQL Editor (una sola vez).
-- Requisitos:
--   1. Extensión pg_cron habilitada (Database > Extensions > pg_cron)
--   2. Extensión pg_net habilitada  (Database > Extensions > pg_net)
--   3. Variable de entorno DATABASE_URL configurada en el servidor local
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
-- Usada por el endpoint /api/accuracy para leer directamente desde Supabase.
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

-- ── 5. pg_cron: jobs automáticos ─────────────────────────────────────────────
-- Requiere que el servidor local con el código Python sea accesible vía HTTP
-- en la URL definida en BETPLAY_API_URL (ej. https://tu-dominio.com)
-- Alternativa: usar Supabase Edge Functions para llamar al endpoint.
--
-- Nota: reemplazar 'http://localhost:8000' con tu URL real de producción.
--       Si el servidor no es accesible desde Supabase, usar GitHub Actions
--       o un cron local (ver database/cron_local.bat).

-- Job 1: Reconciliar resultados de ayer (cada día a las 06:00 UTC = 01:00 ET)
SELECT cron.schedule(
    'betplay-reconcile-results',
    '0 6 * * *',
    $$
    SELECT net.http_get(
        url := 'http://localhost:8000/api/reconcile',
        headers := '{"Content-Type": "application/json"}'::jsonb,
        timeout_milliseconds := 60000
    );
    $$
);

-- Job 2: Pipeline de predicciones de hoy (cada día a las 14:00 UTC = 09:00 ET)
-- Esto funciona si el servidor local tiene una URL pública accesible.
-- Si no, usar el cron local (cron_local.bat).
-- SELECT cron.schedule(
--     'betplay-daily-pipeline',
--     '0 14 * * *',
--     $$
--     SELECT net.http_get(
--         url := 'http://localhost:8000/api/analysis',
--         headers := '{"Content-Type": "application/json"}'::jsonb,
--         timeout_milliseconds := 120000
--     );
--     $$
-- );

-- ── 6. Ver jobs registrados ───────────────────────────────────────────────────
-- SELECT * FROM cron.job;

-- ── 7. Eliminar un job (si necesario) ────────────────────────────────────────
-- SELECT cron.unschedule('betplay-reconcile-results');
