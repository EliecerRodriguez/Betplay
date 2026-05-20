"""
Capa de acceso a datos (DAL).

Gestiona la conexión a PostgreSQL/Supabase y las operaciones de escritura
con deduplicación basada en las claves únicas definidas en models.py.

Uso típico:
    from database.repository import DatabaseRepository

    repo = DatabaseRepository()
    repo.upsert_teams(teams_df)
    repo.upsert_team_stats(team_stats_df)
    repo.upsert_games(games_df)
    repo.upsert_odds(odds_df)
    repo.close()
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from config.settings import DATABASE_URL
from sports.nba.database.models import Base, Game, LineScore, Odd, Prediction, Team, TeamStat, ValueBet
from utils.logger import get_logger

logger = get_logger(__name__)


class DatabaseRepository:
    """
    Repositorio principal.  Crea las tablas si no existen y expone métodos
    upsert para cada entidad.
    """

    def __init__(self, database_url: Optional[str] = None) -> None:
        url = database_url or DATABASE_URL
        self.engine = create_engine(url, pool_pre_ping=True)
        self._SessionFactory = sessionmaker(bind=self.engine)
        self._create_tables()

    # ── Infraestructura ───────────────────────────────────────────────────────

    # SQL de migración/vistas — se aplica en cada inicio (todo usa IF NOT EXISTS / CREATE OR REPLACE)
    _SETUP_SQL = [
        # ── Columna home_win en tabla existente ─────────────────────────────
        "ALTER TABLE predictions ADD COLUMN IF NOT EXISTS home_win INTEGER",

        # ── Vista de accuracy por modelo ─────────────────────────────────────
        """
        CREATE OR REPLACE VIEW v_model_accuracy AS
        SELECT
            model_version,
            COUNT(*)                                                            AS total_games,
            COUNT(*) FILTER (WHERE home_win IS NOT NULL)                        AS total_with_result,
            ROUND(
                AVG(CASE
                    WHEN home_win IS NOT NULL
                         AND ((predicted_winner = 'home' AND home_win = 1)
                           OR (predicted_winner = 'away' AND home_win = 0))
                    THEN 1.0 ELSE 0.0
                END) * 100, 2
            )                                                                   AS accuracy_pct,
            COUNT(*) FILTER (WHERE
                home_win IS NOT NULL
                AND ((predicted_winner = 'home' AND home_win = 1)
                  OR (predicted_winner = 'away' AND home_win = 0))
            )                                                                   AS correct
        FROM predictions
        WHERE model_version IS NOT NULL
        GROUP BY model_version
        ORDER BY model_version
        """,

        # ── Vista de historial por partido ───────────────────────────────────
        """
        CREATE OR REPLACE VIEW v_backtest_history AS
        SELECT
            p.game_id,
            p.game_date,
            p.home_team,
            p.away_team,
            p.predicted_winner,
            p.home_win_prob,
            p.away_win_prob,
            p.home_win,
            p.model_version,
            CASE
                WHEN p.home_win IS NULL THEN 'pending'
                WHEN (p.predicted_winner = 'home' AND p.home_win = 1)
                  OR (p.predicted_winner = 'away' AND p.home_win = 0) THEN 'correct'
                ELSE 'incorrect'
            END                                                                 AS result
        FROM predictions p
        ORDER BY p.game_date DESC
        """,
    ]

    def _create_tables(self) -> None:
        """Crea todas las tablas definidas en models.py y aplica migraciones/vistas."""
        try:
            Base.metadata.create_all(self.engine)
            logger.info("Esquema de base de datos verificado/creado")
        except Exception as exc:
            logger.error("Error al crear tablas: %s", exc, exc_info=True)
            raise
        # Aplicar migraciones idempotentes
        try:
            with self.engine.connect() as conn:
                for stmt in self._SETUP_SQL:
                    conn.execute(text(stmt.strip()))
                conn.commit()
            logger.info("Migraciones y vistas Supabase aplicadas correctamente")
        except Exception as exc:
            # No es fatal — puede fallar si el driver no soporta DDL o no hay permisos
            logger.warning("No se pudieron aplicar migraciones automáticas: %s", exc)

    def get_session(self) -> Session:
        return self._SessionFactory()

    def close(self) -> None:
        self.engine.dispose()
        logger.info("Conexión a base de datos cerrada")

    # ── Helpers internos ──────────────────────────────────────────────────────

    @staticmethod
    def _to_date(value) -> Optional[date]:
        """Convierte string o NaT a date de Python."""
        if value is None or (hasattr(value, "__class__") and value.__class__.__name__ == "NaTType"):
            return None
        if isinstance(value, date):
            return value
        try:
            return pd.to_datetime(str(value)).date()
        except Exception:
            return None

    def _upsert(
        self,
        session: Session,
        model_class,
        rows: list[dict],
        constraint_name: str,
        update_cols: list[str],
    ) -> int:
        """
        Ejecuta un INSERT ... ON CONFLICT DO UPDATE (upsert) por lotes.

        Args:
            session:         Sesión SQLAlchemy activa.
            model_class:     Clase ORM objetivo.
            rows:            Lista de dicts con los datos a insertar.
            constraint_name: Nombre de la restricción UNIQUE en la tabla.
            update_cols:     Columnas a actualizar en caso de conflicto.

        Returns:
            Número de filas procesadas.
        """
        if not rows:
            return 0

        stmt = pg_insert(model_class.__table__).values(rows)
        update_dict = {col: stmt.excluded[col] for col in update_cols}
        stmt = stmt.on_conflict_do_update(
            constraint=constraint_name,
            set_=update_dict,
        )

        session.execute(stmt)
        return len(rows)

    # ── Teams ─────────────────────────────────────────────────────────────────

    def upsert_teams(self, df: pd.DataFrame) -> int:
        """Inserta o actualiza equipos. Clave única: abbreviation."""
        if df.empty:
            logger.warning("upsert_teams: DataFrame vacío, sin operación")
            return 0

        rows = [
            {
                "id":           int(row.get("id", 0)),
                "full_name":    str(row.get("full_name", "")),
                "abbreviation": str(row.get("abbreviation", "")),
                "nickname":     str(row.get("nickname", "")) or None,
                "city":         str(row.get("city", "")) or None,
                "state":        str(row.get("state", "")) or None,
                "year_founded": int(row["year_founded"]) if row.get("year_founded") else None,
            }
            for _, row in df.iterrows()
        ]

        with self.get_session() as session:
            count = self._upsert(
                session, Team, rows,
                constraint_name="teams_abbreviation_key",
                update_cols=["full_name", "nickname", "city", "state", "year_founded"],
            )
            session.commit()

        logger.info("upsert_teams: %d equipos procesados", count)
        return count

    # ── Games ─────────────────────────────────────────────────────────────────

    def upsert_games(self, df: pd.DataFrame) -> int:
        """Inserta o actualiza partidos. Clave única: game_id."""
        if df.empty:
            logger.warning("upsert_games: DataFrame vacío, sin operación")
            return 0

        rows = []
        for _, row in df.iterrows():
            rows.append({
                "game_id":            str(row.get("game_id", "")),
                "game_date":          self._to_date(row.get("game_date_est") or row.get("game_date")),
                "home_team_id":       int(row.get("home_team_id", 0)),
                "visitor_team_id":    int(row.get("visitor_team_id", 0)),
                "home_team_score":    int(row["home_team_score"]) if row.get("home_team_score") else None,
                "visitor_team_score": int(row["visitor_team_score"]) if row.get("visitor_team_score") else None,
                "game_status":        str(row.get("game_status_text", "")) or None,
                "season":             str(row.get("season", "")) or None,
                "fetch_date":         self._to_date(row.get("fetch_date")),
            })

        with self.get_session() as session:
            count = self._upsert(
                session, Game, rows,
                constraint_name="uq_game_id",
                update_cols=[
                    "game_date", "home_team_score", "visitor_team_score",
                    "game_status", "fetch_date",
                ],
            )
            session.commit()

        logger.info("upsert_games: %d partidos procesados", count)
        return count

    # ── TeamStats ─────────────────────────────────────────────────────────────

    def upsert_team_stats(self, df: pd.DataFrame) -> int:
        """Inserta o actualiza estadísticas de equipo. Clave: team_id + season."""
        if df.empty:
            logger.warning("upsert_team_stats: DataFrame vacío, sin operación")
            return 0

        # Mapeo flexible: algunas columnas pueden tener nombres distintos
        col = lambda name, alt=None: df[name] if name in df.columns else (df[alt] if alt and alt in df.columns else None)

        rows = []
        for _, row in df.iterrows():
            rows.append({
                "team_id":   int(row.get("team_id", 0)),
                "team_name": str(row.get("team_name", "")) or None,
                "season":    str(row.get("season", "")) or None,
                "pts":       float(row["pts"])    if row.get("pts")    is not None else None,
                "ast":       float(row["ast"])    if row.get("ast")    is not None else None,
                "reb":       float(row["reb"])    if row.get("reb")    is not None else None,
                "oreb":      float(row["oreb"])   if row.get("oreb")   is not None else None,
                "dreb":      float(row["dreb"])   if row.get("dreb")   is not None else None,
                "stl":       float(row["stl"])    if row.get("stl")    is not None else None,
                "blk":       float(row["blk"])    if row.get("blk")    is not None else None,
                "tov":       float(row["tov"])    if row.get("tov")    is not None else None,
                "fg_pct":    float(row["fg_pct"]) if row.get("fg_pct") is not None else None,
                "fg3_pct":   float(row["fg3_pct"])if row.get("fg3_pct")is not None else None,
                "ft_pct":    float(row["ft_pct"]) if row.get("ft_pct") is not None else None,
                "w":         int(row["w"])        if row.get("w")      is not None else None,
                "l":         int(row["l"])        if row.get("l")      is not None else None,
                "w_pct":     float(row["w_pct"])  if row.get("w_pct")  is not None else None,
                "fetch_date":self._to_date(row.get("fetch_date")),
            })

        update_cols = [
            "team_name","pts","ast","reb","oreb","dreb","stl","blk",
            "tov","fg_pct","fg3_pct","ft_pct","w","l","w_pct","fetch_date",
        ]

        with self.get_session() as session:
            count = self._upsert(
                session, TeamStat, rows,
                constraint_name="uq_team_season_stats",
                update_cols=update_cols,
            )
            session.commit()

        logger.info("upsert_team_stats: %d registros procesados", count)
        return count

    # ── Odds ──────────────────────────────────────────────────────────────────

    def upsert_odds(self, df: pd.DataFrame) -> int:
        """Inserta o actualiza cuotas. Clave: game_id + bookmaker."""
        if df.empty:
            logger.warning("upsert_odds: DataFrame vacío, sin operación")
            return 0

        rows = []
        for _, row in df.iterrows():
            raw_gid = row.get("game_id", "")
            # Normalizar: NaN flotante o string 'nan' → ''
            if raw_gid is None or (isinstance(raw_gid, float)):
                raw_gid = ""
            raw_gid = str(raw_gid).strip()
            if raw_gid.lower() in ("", "nan", "none"):
                raw_gid = ""
            # Si no hay game_id real, generar uno corto y único por partido/fecha
            if not raw_gid:
                import hashlib
                key = f"{row.get('home_team','')}{row.get('away_team','')}{row.get('fetch_date','')}"
                raw_gid = "co_" + hashlib.md5(key.encode()).hexdigest()[:13]
            rows.append({
                "game_id":        raw_gid[:20],
                "home_team":      str(row.get("home_team", "")) or None,
                "away_team":      str(row.get("away_team", "")) or None,
                "bookmaker":      str(row.get("bookmaker", "")),
                "home_odds":      float(row["home_odds"]) if row.get("home_odds") is not None else None,
                "away_odds":      float(row["away_odds"]) if row.get("away_odds") is not None else None,
                "commence_time":  str(row.get("commence_time", "")) or None,
                "is_placeholder": bool(row.get("is_placeholder", False)),
                "fetch_date":     self._to_date(row.get("fetch_date")),
            })

        with self.get_session() as session:
            count = self._upsert(
                session, Odd, rows,
                constraint_name="uq_odd_game_bookmaker",
                update_cols=["home_odds", "away_odds", "is_placeholder", "fetch_date"],
            )
            session.commit()

        logger.info("upsert_odds: %d cuotas procesadas", count)
        return count

    # ── Predictions ───────────────────────────────────────────────────────────

    def upsert_predictions(self, df: pd.DataFrame) -> int:
        """Inserta o actualiza predicciones. Clave: game_id + model_version."""
        if df.empty:
            logger.warning("upsert_predictions: DataFrame vacío, sin operación")
            return 0

        rows = []
        for _, row in df.iterrows():
            rows.append({
                "game_id":          str(row.get("game_id", "")),
                "game_date":        self._to_date(row.get("game_date")),
                "home_team_id":     int(row["home_team_id"])    if row.get("home_team_id")    is not None else None,
                "visitor_team_id":  int(row["visitor_team_id"]) if row.get("visitor_team_id") is not None else None,
                "home_win_prob":    float(row["home_win_prob"]),
                "away_win_prob":    float(row["away_win_prob"]),
                "predicted_winner": str(row.get("predicted_winner", "")),
                "home_win":         int(row["home_win"]) if row.get("home_win") is not None and str(row.get("home_win")) not in ("", "nan") else None,
                "model_version":    str(row.get("model_version", "v1")),
                "fetch_date":       self._to_date(row.get("fetch_date")),
            })

        with self.get_session() as session:
            count = self._upsert(
                session, Prediction, rows,
                constraint_name="uq_prediction_game_model",
                update_cols=["home_win_prob", "away_win_prob", "predicted_winner", "home_win", "fetch_date"],
            )
            session.commit()

        logger.info("upsert_predictions: %d predicciones procesadas", count)
        return count

    def upsert_prediction_results(self, df: pd.DataFrame) -> int:
        """
        Actualiza SOLO la columna home_win de predicciones existentes una vez
        se conoce el resultado real. Clave: game_id + model_version.

        Args:
            df: DataFrame con columnas game_id, model_version, home_win.
        """
        if df.empty:
            logger.warning("upsert_prediction_results: DataFrame vacio, sin operacion")
            return 0

        rows = []
        for _, row in df.iterrows():
            hw = row.get("home_win")
            if hw is None or str(hw) in ("", "nan"):
                continue
            rows.append({
                "game_id":          str(row.get("game_id", "")),
                "game_date":        self._to_date(row.get("game_date")),
                "home_team_id":     int(row["home_team_id"])    if row.get("home_team_id")    is not None else None,
                "visitor_team_id":  int(row["visitor_team_id"]) if row.get("visitor_team_id") is not None else None,
                "home_win_prob":    float(row.get("home_win_prob", 0.5)),
                "away_win_prob":    float(row.get("away_win_prob", 0.5)),
                "predicted_winner": str(row.get("predicted_winner", "")),
                "home_win":         int(hw),
                "model_version":    str(row.get("model_version", "v1")),
                "fetch_date":       self._to_date(row.get("fetch_date")),
            })

        if not rows:
            return 0

        with self.get_session() as session:
            count = self._upsert(
                session, Prediction, rows,
                constraint_name="uq_prediction_game_model",
                update_cols=["home_win"],
            )
            session.commit()

        logger.info("upsert_prediction_results: %d resultados actualizados", count)
        return count

    # ── ValueBets ─────────────────────────────────────────────────────────────

    def upsert_value_bets(self, df: pd.DataFrame) -> int:
        """Inserta o actualiza value bets. Clave: game_id + bookmaker + side."""
        if df.empty:
            logger.warning("upsert_value_bets: DataFrame vacío, sin operación")
            return 0

        rows = []
        for _, row in df.iterrows():
            rows.append({
                "game_id":      str(row.get("game_id", "")),
                "game_date":    self._to_date(row.get("game_date")),
                "bookmaker":    str(row.get("bookmaker", "")),
                "side":         str(row.get("side", "")),
                "team_name":    str(row.get("team_name", "")) or None,
                "model_prob":   float(row["model_prob"]),
                "odds":         float(row["odds"]),
                "value":        float(row["value"]),
                "is_value_bet": bool(row.get("is_value_bet", False)),
                "fetch_date":   self._to_date(row.get("fetch_date")),
            })

        with self.get_session() as session:
            count = self._upsert(
                session, ValueBet, rows,
                constraint_name="uq_value_game_bk_side",
                update_cols=["model_prob", "odds", "value", "is_value_bet", "fetch_date"],
            )
            session.commit()

        logger.info("upsert_value_bets: %d value bets procesadas", count)
        return count

    # ── LineScores ────────────────────────────────────────────────────────────

    def upsert_line_scores(self, df: pd.DataFrame) -> int:
        """
        Inserta o actualiza marcadores por equipo por partido.
        Clave unica: game_id + team_id.

        Args:
            df: DataFrame con columnas game_id, team_id, pts y opcionalmente
                game_date, fetch_date.
        """
        if df.empty:
            logger.warning("upsert_line_scores: DataFrame vacio, sin operacion")
            return 0

        rows = []
        for _, row in df.iterrows():
            pts_val = row.get("pts")
            rows.append({
                "game_id":   str(row.get("game_id", "")),
                "game_date": self._to_date(row.get("game_date")),
                "team_id":   int(row["team_id"]) if row.get("team_id") is not None else None,
                "pts":       int(pts_val) if pts_val is not None and str(pts_val) not in ("", "nan") else None,
                "fetch_date": self._to_date(row.get("fetch_date")),
            })

        with self.get_session() as session:
            count = self._upsert(
                session, LineScore, rows,
                constraint_name="uq_line_score_game_team",
                update_cols=["pts", "game_date", "fetch_date"],
            )
            session.commit()

        logger.info("upsert_line_scores: %d marcadores procesados", count)
        return count
