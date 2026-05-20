"""
Estado de lesiones e indisponibilidades de jugadores ATP.

Fuente: ESPN API (sin API key, igual que NBA).
URL:    https://site.api.espn.com/apis/site/v2/sports/tennis/atp/injuries

El endpoint devuelve una lista de jugadores con su estado actual:
  - "Out"          → fuera completamente (retiro confirmado del torneo)
  - "Day-To-Day"   → posible baja de último momento
  - "Questionable" → estado incierto, puede jugar
  - "Inactive"     → en descanso / sin torneo activo

En tenis, a diferencia del basketball, los retiros de ÚLTIMO MOMENTO son frecuentes
(lesiones durante el partido). Este módulo reporta el estado pre-partido.

Funciones públicas:
  - get_atp_injuries()           → dict {player_name: InjuryInfo}
  - get_player_injury_status(name) → str | None
  - is_player_available(name)    → bool  (False si está "Out")
"""
from __future__ import annotations

import time
from typing import Dict, Optional, TypedDict

import requests

from utils.logger import get_logger

logger = get_logger(__name__)

# ── Constantes ────────────────────────────────────────────────────────────────

_ESPN_ATP_INJURIES_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/tennis/atp/injuries"
)
_TIMEOUT   = 10    # segundos
_CACHE_TTL = 3600  # 1 hora — las lesiones ATP no cambian minuto a minuto

# Porcentaje de impacto por status (futuro uso en el modelo)
_STATUS_AVAILABILITY = {
    "out":          0.00,
    "day-to-day":   0.70,
    "questionable": 0.85,
    "inactive":     0.00,
    "probable":     0.95,
}


class InjuryInfo(TypedDict):
    status: str           # Estado original de ESPN
    description: str      # Descripción/motivo (p.ej. "Right wrist strain")
    availability: float   # Disponibilidad estimada 0.0–1.0
    source: str           # Siempre "espn"


# ── Caché en memoria (sin dependencias externas) ──────────────────────────────

_injuries_cache: dict = {}   # {"data": {...}, "ts": float}


def _fetch_raw_injuries() -> dict:
    """
    Descarga y parsea el JSON de ESPN ATP injuries.
    Retorna {player_name: InjuryInfo}.  Cachea por 1 hora.
    """
    global _injuries_cache
    now = time.time()

    if _injuries_cache and (now - _injuries_cache.get("ts", 0)) < _CACHE_TTL:
        return _injuries_cache["data"]

    try:
        resp = requests.get(_ESPN_ATP_INJURIES_URL, timeout=_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
    except requests.exceptions.RequestException as exc:
        logger.warning("ESPN ATP injuries API falló: %s", exc)
        return _injuries_cache.get("data", {})

    injuries: Dict[str, InjuryInfo] = {}

    # La estructura del JSON de ESPN puede variar; manejamos ambos formatos
    # conocidos: {"injuries": [{...}]} o directamente [{...}]
    items = payload
    if isinstance(payload, dict):
        items = payload.get("injuries", payload.get("items", []))

    for item in items:
        try:
            # Estructura típica ESPN injuries
            athlete = item.get("athlete", item)
            full_name = (
                athlete.get("displayName")
                or athlete.get("fullName")
                or athlete.get("name", "")
            )
            if not full_name:
                continue

            injury_data = item.get("injuries", [{}])
            injury      = injury_data[0] if injury_data else {}
            status_raw  = (
                injury.get("status", item.get("status", ""))
                or ""
            ).lower()
            desc = (
                injury.get("description", item.get("description", ""))
                or ""
            )

            availability = _STATUS_AVAILABILITY.get(status_raw, 1.0)

            injuries[full_name] = InjuryInfo(
                status=status_raw or "unknown",
                description=desc,
                availability=availability,
                source="espn",
            )

        except Exception as exc:
            logger.debug("Error parseando jugador lesionado ATP: %s", exc)
            continue

    _injuries_cache = {"data": injuries, "ts": now}
    logger.info("Lesiones ATP ESPN: %d jugadores con estado reportado", len(injuries))
    return injuries


# ── API pública ───────────────────────────────────────────────────────────────

def get_atp_injuries() -> Dict[str, InjuryInfo]:
    """
    Devuelve todos los jugadores ATP con estado de lesión reportado.

    Returns:
        {player_name: {"status": str, "description": str,
                       "availability": float, "source": str}}
        Dict vacío si la API no está disponible o no hay lesiones reportadas.
    """
    return _fetch_raw_injuries()


def get_player_injury_status(player_name: str) -> Optional[str]:
    """
    Devuelve el estado de lesión de un jugador específico.

    Args:
        player_name: Nombre completo (ej. "Novak Djokovic").

    Returns:
        Estado como string ('out', 'day-to-day', 'questionable', etc.)
        None si el jugador no aparece en el reporte (= presumiblemente sano).
    """
    injuries = get_atp_injuries()

    # Búsqueda exacta primero
    if player_name in injuries:
        return injuries[player_name]["status"]

    # Búsqueda parcial por apellido (último recurso)
    name_lower = player_name.lower()
    for known_name, info in injuries.items():
        if name_lower in known_name.lower() or known_name.lower() in name_lower:
            return info["status"]

    return None   # No reportado → asumimos disponible


def is_player_available(player_name: str) -> bool:
    """
    Indica si un jugador está disponible para jugar (no está 'Out' o 'Inactive').

    Returns:
        True  si no hay reporte de lesión o si el status permite jugar.
        False si el jugador está confirmado como 'Out' o 'Inactive'.
    """
    status = get_player_injury_status(player_name)
    if status is None:
        return True   # Sin reporte = disponible
    return status not in ("out", "inactive")


def get_availability_factor(player_name: str) -> float:
    """
    Devuelve el factor de disponibilidad (0.0–1.0) para usar en el modelo.
    1.0 = 100% disponible, 0.0 = confirmado fuera.
    """
    injuries = get_atp_injuries()
    if player_name in injuries:
        return injuries[player_name]["availability"]

    # Búsqueda parcial
    name_lower = player_name.lower()
    for known_name, info in injuries.items():
        if name_lower in known_name.lower() or known_name.lower() in name_lower:
            return info["availability"]

    return 1.0   # Sin reporte = disponible al 100%
