"""
Esquema de base de datos ATP con SQLAlchemy ORM.

Tablas:
  atp_players    : catálogo de jugadores (de Jeff Sackmann)
  atp_matches    : historial + partidos programados
  atp_rankings   : ranking ATP semanal
  atp_predictions: predicciones del modelo
  atp_odds       : cuotas por partido y casa de apuestas
  atp_value_bets : oportunidades de valor detectadas

Diseño consciente:
  - Completamente aislado de las tablas NBA (prefijo 'atp_' en todos los nombres)
  - Usa la misma Base de SQLAlchemy que NBA (tablas en el mismo esquema PostgreSQL)
    pero sin colisiones de nombres
"""
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase


class ATPBase(DeclarativeBase):
    """Base separada de la NBA para mayor aislamiento."""
    pass


# ── atp_players ───────────────────────────────────────────────────────────────

class ATPPlayer(ATPBase):
    """Catálogo de jugadores ATP (fuente: Jeff Sackmann atp_players.csv)."""
    __tablename__ = "atp_players"
    __table_args__ = (
        UniqueConstraint("player_id", name="uq_atp_player_id"),
    )

    id         = Column(Integer, primary_key=True, autoincrement=True)
    player_id  = Column(Integer, nullable=False)          # ID Sackmann
    name_first = Column(String(80))
    name_last  = Column(String(80), nullable=False)
    full_name  = Column(String(160))                      # computed: first + last
    hand       = Column(String(1))                        # R / L / U
    dob        = Column(Date)
    ioc        = Column(String(3))                        # código país IOC
    height_cm  = Column(Integer)
    created_at = Column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        return f"<ATPPlayer {self.player_id} {self.full_name}>"


# ── atp_matches ───────────────────────────────────────────────────────────────

class ATPMatch(ATPBase):
    """
    Partido ATP.  Cubre tanto historial (para entrenamiento) como partidos
    programados del día (para predicción).

    Para partidos históricos: winner_id / loser_id están poblados.
    Para partidos sin resultado: player1_id / player2_id se usan en su lugar
    y winner_id / loser_id quedan NULL hasta la reconciliación.
    """
    __tablename__ = "atp_matches"
    __table_args__ = (
        UniqueConstraint("match_id", name="uq_atp_match_id"),
    )

    id             = Column(Integer, primary_key=True, autoincrement=True)
    match_id       = Column(String(40), nullable=False)   # tourney_id + match_num
    tourney_id     = Column(String(20))
    tourney_name   = Column(String(100))
    surface        = Column(String(10))                   # Hard / Clay / Grass
    tourney_level  = Column(String(2))                    # G / M / A / F / D
    tourney_date   = Column(Date)
    round          = Column(String(5))                    # R128 / QF / SF / F …
    best_of        = Column(Integer)                      # 3 o 5

    # Jugadores — rellenados de winner/loser para histórico,
    # o como player1/player2 para partidos sin resultado aún
    winner_id      = Column(Integer)
    loser_id       = Column(Integer)
    player1_id     = Column(Integer)                      # para partidos programados
    player2_id     = Column(Integer)

    score          = Column(String(50))
    minutes        = Column(Integer)

    # Estadísticas del ganador
    w_ace          = Column(Integer)
    w_df           = Column(Integer)
    w_svpt         = Column(Integer)
    w_1st_in       = Column(Integer)
    w_1st_won      = Column(Integer)
    w_2nd_won      = Column(Integer)
    w_bp_saved     = Column(Integer)
    w_bp_faced     = Column(Integer)

    # Estadísticas del perdedor
    l_ace          = Column(Integer)
    l_df           = Column(Integer)
    l_svpt         = Column(Integer)
    l_1st_in       = Column(Integer)
    l_1st_won      = Column(Integer)
    l_2nd_won      = Column(Integer)
    l_bp_saved     = Column(Integer)
    l_bp_faced     = Column(Integer)

    fetch_date     = Column(Date)
    created_at     = Column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        return f"<ATPMatch {self.match_id} {self.tourney_name} {self.round}>"


# ── atp_rankings ──────────────────────────────────────────────────────────────

class ATPRanking(ATPBase):
    """Ranking ATP semanal (fuente: Sackmann atp_rankings_current.csv)."""
    __tablename__ = "atp_rankings"
    __table_args__ = (
        UniqueConstraint("ranking_date", "player_id", name="uq_atp_ranking_date_player"),
    )

    id           = Column(Integer, primary_key=True, autoincrement=True)
    ranking_date = Column(Date, nullable=False)
    rank         = Column(Integer, nullable=False)
    player_id    = Column(Integer, nullable=False)
    points       = Column(Integer)
    created_at   = Column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        return f"<ATPRanking #{self.rank} player={self.player_id} {self.ranking_date}>"


# ── atp_predictions ───────────────────────────────────────────────────────────

class ATPPrediction(ATPBase):
    """Predicción del modelo para un partido ATP."""
    __tablename__ = "atp_predictions"
    __table_args__ = (
        UniqueConstraint("match_id", "fetch_date", name="uq_atp_prediction_match_date"),
    )

    id              = Column(Integer, primary_key=True, autoincrement=True)
    match_id        = Column(String(40), nullable=False)
    fetch_date      = Column(Date, nullable=False)

    player1_id      = Column(Integer)
    player2_id      = Column(Integer)
    player1_name    = Column(String(100))
    player2_name    = Column(String(100))

    tourney_name    = Column(String(100))
    surface         = Column(String(10))
    tourney_level   = Column(String(2))
    round           = Column(String(5))

    # Probabilidades del modelo
    p1_win_prob     = Column(Float)
    p2_win_prob     = Column(Float)

    # Elo pre-partido
    p1_elo          = Column(Float)
    p2_elo          = Column(Float)
    elo_diff        = Column(Float)
    elo_win_prob    = Column(Float)   # predicción solo basada en Elo

    # Monte Carlo (Fase 5)
    mc_p1_win_prob  = Column(Float)
    mc_blend_prob   = Column(Float)

    # Resultado real (reconciliación)
    p1_won          = Column(Integer)   # 1 = p1 ganó, 0 = p2 ganó, NULL = sin resultado

    model_version   = Column(String(20))
    created_at      = Column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        return f"<ATPPrediction {self.match_id} p1={self.p1_win_prob:.2f}>"


# ── atp_odds ──────────────────────────────────────────────────────────────────

class ATPOdd(ATPBase):
    """Cuotas por partido y casa de apuestas."""
    __tablename__ = "atp_odds"
    __table_args__ = (
        UniqueConstraint("match_id", "bookmaker", "fetch_date",
                         name="uq_atp_odds_match_book_date"),
    )

    id            = Column(Integer, primary_key=True, autoincrement=True)
    match_id      = Column(String(40), nullable=False)
    fetch_date    = Column(Date, nullable=False)

    player1_name  = Column(String(100))
    player2_name  = Column(String(100))
    bookmaker     = Column(String(50))

    p1_odds       = Column(Float)    # cuota decimal jugador 1
    p2_odds       = Column(Float)    # cuota decimal jugador 2

    tourney_name  = Column(String(100))
    surface       = Column(String(10))
    round         = Column(String(5))
    commence_time = Column(String(30))

    created_at    = Column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        return f"<ATPOdd {self.match_id} {self.bookmaker}>"


# ── atp_value_bets ────────────────────────────────────────────────────────────

class ATPValueBet(ATPBase):
    """Oportunidades de valor detectadas para partidos ATP."""
    __tablename__ = "atp_value_bets"
    __table_args__ = (
        UniqueConstraint("match_id", "bookmaker", "side", "fetch_date",
                         name="uq_atp_vbet_match_book_side_date"),
    )

    id           = Column(Integer, primary_key=True, autoincrement=True)
    match_id     = Column(String(40), nullable=False)
    fetch_date   = Column(Date, nullable=False)

    player_name  = Column(String(100))
    side         = Column(String(10))                   # 'p1' o 'p2'
    bookmaker    = Column(String(50))
    odds         = Column(Float)
    model_prob   = Column(Float)
    value        = Column(Float)                        # (prob * odds) - 1
    kelly        = Column(Float)                        # fracción Kelly
    tourney_name = Column(String(100))
    surface      = Column(String(10))

    created_at   = Column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        return f"<ATPValueBet {self.match_id} {self.side} value={self.value:.3f}>"
