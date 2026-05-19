"""
Definición del esquema de base de datos con SQLAlchemy ORM.

Tablas:
  - teams        : catálogo de equipos NBA
  - games        : partidos (cabecera del partido)
  - team_stats   : estadísticas por equipo y temporada
  - odds         : cuotas por partido y casa de apuestas
  - predictions  : predicciones del modelo
  - value_bets   : oportunidades de valor detectadas
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


class Base(DeclarativeBase):
    pass


# ── teams ─────────────────────────────────────────────────────────────────────

class Team(Base):
    __tablename__ = "teams"

    id            = Column(Integer, primary_key=True)          # nba_api team id
    full_name     = Column(String(100), nullable=False)
    abbreviation  = Column(String(10), nullable=False, unique=True)
    nickname      = Column(String(50))
    city          = Column(String(50))
    state         = Column(String(50))
    year_founded  = Column(Integer)
    created_at    = Column(DateTime, server_default=func.now())

    def __repr__(self):
        return f"<Team {self.abbreviation}>"


# ── games ─────────────────────────────────────────────────────────────────────

class Game(Base):
    __tablename__ = "games"
    __table_args__ = (
        UniqueConstraint("game_id", name="uq_game_id"),
    )

    id              = Column(Integer, primary_key=True, autoincrement=True)
    game_id         = Column(String(20), nullable=False)       # ID único de nba_api
    game_date       = Column(Date, nullable=False)
    home_team_id    = Column(Integer, nullable=False)
    visitor_team_id = Column(Integer, nullable=False)
    home_team_score = Column(Integer)
    visitor_team_score = Column(Integer)
    game_status     = Column(String(50))                       # Final, Live, Scheduled
    season          = Column(String(10))
    fetch_date      = Column(Date)
    created_at      = Column(DateTime, server_default=func.now())

    def __repr__(self):
        return f"<Game {self.game_id} {self.game_date}>"


# ── team_stats ────────────────────────────────────────────────────────────────

class TeamStat(Base):
    __tablename__ = "team_stats"
    __table_args__ = (
        UniqueConstraint("team_id", "season", name="uq_team_season_stats"),
    )

    id          = Column(Integer, primary_key=True, autoincrement=True)
    team_id     = Column(Integer, nullable=False)
    team_name   = Column(String(100))
    season      = Column(String(10), nullable=False)

    # Promedios ofensivos
    pts         = Column(Float)   # puntos por partido
    ast         = Column(Float)   # asistencias
    reb         = Column(Float)   # rebotes totales
    oreb        = Column(Float)   # rebotes ofensivos
    dreb        = Column(Float)   # rebotes defensivos
    stl         = Column(Float)   # robos
    blk         = Column(Float)   # tapones
    tov         = Column(Float)   # pérdidas
    fg_pct      = Column(Float)   # % campo
    fg3_pct     = Column(Float)   # % triples
    ft_pct      = Column(Float)   # % tiros libres

    # Rendimiento general
    w           = Column(Integer) # victorias
    l           = Column(Integer) # derrotas
    w_pct       = Column(Float)   # % victorias

    fetch_date  = Column(Date)
    created_at  = Column(DateTime, server_default=func.now())

    def __repr__(self):
        return f"<TeamStat {self.team_id} {self.season}>"


# ── odds ──────────────────────────────────────────────────────────────────────

class Odd(Base):
    __tablename__ = "odds"
    __table_args__ = (
        UniqueConstraint("game_id", "bookmaker", name="uq_odd_game_bookmaker"),
    )

    id              = Column(Integer, primary_key=True, autoincrement=True)
    game_id         = Column(String(20), nullable=False)
    home_team       = Column(String(100))
    away_team       = Column(String(100))
    bookmaker       = Column(String(50), nullable=False)
    home_odds       = Column(Float)
    away_odds       = Column(Float)
    commence_time   = Column(String(50))
    is_placeholder  = Column(Boolean, default=False)
    fetch_date      = Column(Date)
    created_at      = Column(DateTime, server_default=func.now())

    def __repr__(self):
        return f"<Odd {self.game_id} {self.bookmaker}>"


# ── predictions ───────────────────────────────────────────────────────────────

class Prediction(Base):
    __tablename__ = "predictions"
    __table_args__ = (
        UniqueConstraint("game_id", "model_version", name="uq_prediction_game_model"),
    )

    id               = Column(Integer, primary_key=True, autoincrement=True)
    game_id          = Column(String(20), nullable=False)
    game_date        = Column(Date)
    home_team_id     = Column(Integer)
    visitor_team_id  = Column(Integer)
    home_win_prob    = Column(Float)    # probabilidad victoria local [0-1]
    away_win_prob    = Column(Float)    # probabilidad victoria visitante [0-1]
    predicted_winner = Column(String(10))  # 'home' | 'away'
    home_win         = Column(Integer)  # resultado real: 1=local ganó, 0=visitante ganó, NULL=pendiente
    model_version    = Column(String(20), default="v1")
    fetch_date       = Column(Date)
    created_at       = Column(DateTime, server_default=func.now())

    def __repr__(self):
        return f"<Prediction {self.game_id} home_prob={self.home_win_prob:.2f}>"


# ── line_scores ───────────────────────────────────────────────────────────────

class LineScore(Base):
    __tablename__ = "line_scores"
    __table_args__ = (
        UniqueConstraint("game_id", "team_id", name="uq_line_score_game_team"),
    )

    id         = Column(Integer, primary_key=True, autoincrement=True)
    game_id    = Column(String(20), nullable=False)
    game_date  = Column(Date)
    team_id    = Column(Integer, nullable=False)
    pts        = Column(Integer)         # puntos anotados
    fetch_date = Column(Date)
    created_at = Column(DateTime, server_default=func.now())

    def __repr__(self):
        return f"<LineScore {self.game_id} team={self.team_id} pts={self.pts}>"


# ── value_bets ────────────────────────────────────────────────────────────────

class ValueBet(Base):
    __tablename__ = "value_bets"
    __table_args__ = (
        UniqueConstraint("game_id", "bookmaker", "side", name="uq_value_game_bk_side"),
    )

    id              = Column(Integer, primary_key=True, autoincrement=True)
    game_id         = Column(String(20), nullable=False)
    game_date       = Column(Date)
    bookmaker       = Column(String(50))
    side            = Column(String(10))   # 'home' | 'away'
    team_name       = Column(String(100))
    model_prob      = Column(Float)        # probabilidad del modelo
    odds            = Column(Float)        # cuota decimal
    value           = Column(Float)        # (prob * odds) - 1
    is_value_bet    = Column(Boolean)      # value > 0
    fetch_date      = Column(Date)
    created_at      = Column(DateTime, server_default=func.now())

    def __repr__(self):
        return f"<ValueBet {self.game_id} {self.side} value={self.value:.3f}>"
