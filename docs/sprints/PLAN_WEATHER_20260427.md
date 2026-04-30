# Plan — Outil `weather` + agent spécialiste météo

## Décision architecturale : agent spécialiste ou jean-michel direct ?

**Verdict : agent spécialiste (`weather-specialist`).**

Justification comparée avec `clock` :

| Critère | `clock` | `weather` |
|--------|---------|-----------|
| Nombre d'appels outils | 1 | 1 (tool encapsule geocoding + API) |
| Interprétation du résultat | Aucune — renvoie l'heure brute | Nécessaire — convertir les codes WMO, sélectionner les variables pertinentes, formuler une réponse humaine |
| Paramétrage LLM requis | Aucun (timezone par défaut) | Élevé — extraire lieu, fenêtre temporelle (courant / prévision / passé), variables pertinentes depuis la requête utilisateur |
| Charge pour jean-michel | Triviale | Non triviale — analyser la requête, déduire la fenêtre, déléguer correctement |

Jean-Michel détecte qu'il s'agit d'une question météo → `delegate_to("weather-specialist", ...)`. Le spécialiste appelle le tool, interprète le JSON brut, retourne une réponse dans la langue détectée.

---

## Architecture de l'implémentation

### Vue d'ensemble des composants

```
Requête utilisateur ("il fait quoi à Montréal?")
        │
        ▼
  jean-michel (router)
  delegate_to("weather-specialist", briefing, expected)
        │
        ▼
  weather-specialist (specialist)
  ├─ appelle tool: weather(location, mode, forecast_days, ...)
  │     ├─ geocoding interne (Nominatim via geopy, si localisation textuelle)
  │     └─ HTTP GET api.open-meteo.com/v1/forecast
  └─ interprète JSON → return_to_user(answer)
```

### Fichiers à créer / modifier

| # | Fichier | Nature |
|---|---------|--------|
| 1 | `src/jeanmichel/tools/weather.py` | Nouveau — `SPEC: ToolSpec` stateless |
| 2 | `src/jeanmichel/tools/__init__.py` | Modifier — ajouter `weather` au registre |
| 3 | `db/schema.sql` | Modifier — agent + paradigmes + grant |
| 4 | `pyproject.toml` | Modifier — ajouter dépendance `geopy` |
| 5 | `tests/test_tools.py` | Modifier — ajouter `TestWeather` |

**Migration DB live :** pour une base existante, exécuter le bloc INSERT isolé (voir section DB) à la main ou via un script de migration.

---

## 1. Outil `weather` — `src/jeanmichel/tools/weather.py`

### Signature du handler

```python
def _handler(
    location: str,
    mode: str = "current",        # "current" | "forecast" | "history"
    forecast_days: int = 1,       # 1-16, ignoré si mode != "forecast"
    past_days: int = 1,           # 1-92, ignoré si mode != "history"
    timezone: str = "auto",       # IANA ou "auto" (résolu par l'API)
) -> str:  # JSON string
```

### Paramètre `location`

Deux formes acceptées :
- `"Montréal"`, `"Paris, FR"`, `"Tokyo"` → geocoding via Nominatim
- `"45.5088,-73.5878"` ou `"45.5088, -73.5878"` → lat/lon direct (pas de geocoding)

Le handler détecte la forme avec un regex simple :
```python
import re
_LAT_LON = re.compile(r'^-?\d+\.?\d*\s*,\s*-?\d+\.?\d*$')
```

### Geocoding (geopy + Nominatim)

Nominatim est gratuit, sans clé API, mondial. Contrainte : **1 req/s** (usage policy OpenStreetMap). Pour un assistant interactif, c'est acceptable.

```python
from geopy.geocoders import Nominatim
geolocator = Nominatim(user_agent="jean-michel/0.1")
loc = geolocator.geocode(location, timeout=5)
if loc is None:
    return '{"error": "Location not found: ' + location + '"}'
lat, lon = loc.latitude, loc.longitude
```

### Appel open-meteo (stdlib `urllib.request`, zéro dépendance supplémentaire)

L'API open-meteo est publique, gratuite (non commercial), sans clé. Pas de `requests` nécessaire.

**Endpoint de base :** `https://api.open-meteo.com/v1/forecast`

**Variables sélectionnées par `mode` :**

#### mode `"current"`
```
current=temperature_2m,relative_humidity_2m,apparent_temperature,
        precipitation,weather_code,cloud_cover,wind_speed_10m,
        wind_direction_10m,wind_gusts_10m,is_day
hourly=temperature_2m,precipitation_probability,weather_code
forecast_hours=12     # reste de la journée (~12h de prévision horaire)
past_days=0
```

#### mode `"forecast"`
```
daily=weather_code,temperature_2m_max,temperature_2m_min,
      apparent_temperature_max,apparent_temperature_min,
      precipitation_sum,precipitation_probability_max,
      wind_speed_10m_max,wind_gusts_10m_max,sunrise,sunset
forecast_days=<param>
```

#### mode `"history"`
```
daily=weather_code,temperature_2m_max,temperature_2m_min,
      precipitation_sum,wind_speed_10m_max
past_days=<param>
forecast_days=0
```

**Réponse retournée :** JSON brut tronqué si > 8 000 caractères (protection contre les réponses énormes).

### Codes WMO — aide à l'interprétation

Le tool inclut dans sa réponse un champ `wmo_descriptions` (dictionnaire des codes présents dans les données). Cela permet au LLM d'interpréter sans avoir à mémoriser le tableau WMO.

```json
{
  "wmo_descriptions": {
    "61": "Rain: slight intensity",
    "3": "Overcast"
  }
}
```

### Structure de retour JSON

```json
{
  "location": { "name": "Montréal, QC", "lat": 45.5088, "lon": -73.5878 },
  "timezone": "America/Toronto",
  "utc_offset_seconds": -14400,
  "mode": "current",
  "current": {
    "time": "2026-04-27T15:00",
    "temperature_2m": 12.4,
    "apparent_temperature": 9.1,
    "weather_code": 3,
    "precipitation": 0.0,
    "wind_speed_10m": 18.2,
    "is_day": 1
  },
  "forecast_hours": [ ... ],
  "wmo_descriptions": { "3": "Overcast" }
}
```

### Code complet indicatif

```python
"""Tool: weather — current conditions, forecast and recent history via open-meteo."""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from ._base import ToolSpec

_LAT_LON_RE = re.compile(r'^(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)$')

_WMO_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Rime fog",
    51: "Drizzle (light)", 53: "Drizzle (moderate)", 55: "Drizzle (dense)",
    61: "Rain (slight)", 63: "Rain (moderate)", 65: "Rain (heavy)",
    66: "Freezing rain (light)", 67: "Freezing rain (heavy)",
    71: "Snowfall (slight)", 73: "Snowfall (moderate)", 75: "Snowfall (heavy)",
    77: "Snow grains",
    80: "Rain showers (slight)", 81: "Rain showers (moderate)", 82: "Rain showers (violent)",
    85: "Snow showers (slight)", 86: "Snow showers (heavy)",
    95: "Thunderstorm", 96: "Thunderstorm with hail (slight)", 99: "Thunderstorm with hail (heavy)",
}

_BASE_URL = "https://api.open-meteo.com/v1/forecast"
_MAX_RESPONSE_CHARS = 8_000


def _geocode(location: str) -> tuple[float, float, str]:
    """Return (lat, lon, display_name). Raises ValueError on failure."""
    try:
        from geopy.geocoders import Nominatim
    except ImportError:
        raise ValueError("geopy is not installed. Run: pip install geopy")
    geolocator = Nominatim(user_agent="jean-michel/0.1", timeout=5)
    result = geolocator.geocode(location)
    if result is None:
        raise ValueError(f"Location not found: {location!r}")
    return result.latitude, result.longitude, result.address


def _fetch_weather(params: dict) -> dict:
    url = _BASE_URL + "?" + urllib.parse.urlencode(params, doseq=True)
    with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


def _extract_wmo(data: dict) -> dict[str, str]:
    codes: set[int] = set()
    for section in ("current", "hourly", "daily"):
        obj = data.get(section, {})
        wc = obj.get("weather_code")
        if isinstance(wc, list):
            codes.update(int(c) for c in wc if c is not None)
        elif wc is not None:
            codes.add(int(wc))
    return {str(c): _WMO_CODES.get(c, "Unknown code") for c in sorted(codes)}


def _handler(
    location: str,
    mode: str = "current",
    forecast_days: int = 1,
    past_days: int = 1,
    timezone: str = "auto",
) -> str:
    try:
        m = _LAT_LON_RE.match(location.strip())
        if m:
            lat, lon = float(m.group(1)), float(m.group(2))
            display_name = location.strip()
        else:
            lat, lon, display_name = _geocode(location)
    except ValueError as e:
        return json.dumps({"error": str(e)})
    except Exception as e:
        return json.dumps({"error": f"Geocoding failed: {e}"})

    params: dict = {
        "latitude": lat,
        "longitude": lon,
        "timezone": timezone,
    }

    if mode == "current":
        params["current"] = [
            "temperature_2m", "relative_humidity_2m", "apparent_temperature",
            "precipitation", "weather_code", "cloud_cover",
            "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m", "is_day",
        ]
        params["hourly"] = ["temperature_2m", "precipitation_probability", "weather_code"]
        params["forecast_hours"] = 12
        params["past_days"] = 0

    elif mode == "forecast":
        fc = max(1, min(forecast_days, 16))
        params["daily"] = [
            "weather_code", "temperature_2m_max", "temperature_2m_min",
            "apparent_temperature_max", "apparent_temperature_min",
            "precipitation_sum", "precipitation_probability_max",
            "wind_speed_10m_max", "wind_gusts_10m_max", "sunrise", "sunset",
        ]
        params["forecast_days"] = fc

    elif mode == "history":
        pd = max(1, min(past_days, 92))
        params["daily"] = [
            "weather_code", "temperature_2m_max", "temperature_2m_min",
            "precipitation_sum", "wind_speed_10m_max",
        ]
        params["past_days"] = pd
        params["forecast_days"] = 0

    else:
        return json.dumps({"error": f"Unknown mode: {mode!r}. Use 'current', 'forecast' or 'history'."})

    try:
        data = _fetch_weather(params)
    except Exception as e:
        return json.dumps({"error": f"open-meteo request failed: {e}"})

    result = {
        "location": {"name": display_name, "lat": lat, "lon": lon},
        "timezone": data.get("timezone"),
        "utc_offset_seconds": data.get("utc_offset_seconds"),
        "mode": mode,
    }
    for key in ("current", "hourly", "daily"):
        if key in data:
            result[key] = data[key]
    result["wmo_descriptions"] = _extract_wmo(data)

    serialized = json.dumps(result)
    if len(serialized) > _MAX_RESPONSE_CHARS:
        serialized = serialized[:_MAX_RESPONSE_CHARS] + '...[truncated]"}'
    return serialized


SPEC = ToolSpec(
    name="weather",
    description=(
        "Fetch weather data for a location. "
        "Modes: 'current' (now + next ~12h), 'forecast' (up to 16 days), "
        "'history' (past 1-92 days). "
        "location can be a city name ('Montreal', 'Paris, FR') or "
        "decimal coordinates ('45.51,-73.59'). "
        "Returns JSON with conditions, units, and WMO code descriptions."
    ),
    parameters={
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": (
                    "City name or 'lat,lon' decimal string. "
                    "Examples: 'Montreal', 'Paris, France', '48.85,2.35'."
                ),
            },
            "mode": {
                "type": "string",
                "enum": ["current", "forecast", "history"],
                "description": (
                    "'current': conditions now + 12h hourly preview. "
                    "'forecast': daily aggregates for N days ahead. "
                    "'history': daily aggregates for N past days."
                ),
            },
            "forecast_days": {
                "type": "integer",
                "description": "Number of days ahead (1-16). Only used with mode='forecast'.",
            },
            "past_days": {
                "type": "integer",
                "description": "Number of past days (1-92). Only used with mode='history'.",
            },
            "timezone": {
                "type": "string",
                "description": (
                    "IANA timezone name (e.g. 'America/Montreal'). "
                    "Default 'auto' resolves automatically from coordinates."
                ),
            },
        },
        "required": ["location"],
    },
    handler=_handler,
)
```

---

## 2. Modification de `tools/__init__.py`

```python
from . import weather as _weather_mod

def build_registry(conv_folder: Path) -> dict[str, ToolSpec]:
    conv_read_file_spec = _conv_read_file_mod.make_spec(conv_folder)
    return {
        _clock_mod.SPEC.name: _clock_mod.SPEC,
        conv_read_file_spec.name: conv_read_file_spec,
        _weather_mod.SPEC.name: _weather_mod.SPEC,   # ← ajout
    }
```

---

## 3. DB seeds (`db/schema.sql`)

### Agent `weather-specialist`

```sql
INSERT INTO agents (code, name, role, mission, thinking_mode, temperature, active, created_at, modified_at) VALUES
  ('weather-specialist', 'Weather Specialist', 'specialist',
   'Retrieve weather data (current, forecast, or past) for a requested location and time window using the weather tool. Interpret the raw JSON and present the relevant information clearly. Never invent or extrapolate meteorological data.',
   1, 0.1, 1, datetime('now'), datetime('now'));
```

`temperature=0.1` : réponses météo très déterministes.
`thinking_mode=1` : activé pour déduire mode/fenêtre depuis le briefing.

### Nouvelle section + catégorie (si absente)

La section `process` et la catégorie `execution` existent déjà. On peut y rattacher un paradigme `weather_api_first`. Mais pour éviter de polluer les globaux, on crée des paradigmes **non-globaux** sous la catégorie `safety/scope` qui existe déjà, ou on crée une catégorie dédiée `meteorology` sous `process`.

**Option retenue :** nouvelle catégorie `meteorology` sous `process`. Section `process` et catégorie `meteorology` sont créées seulement si inexistantes.

```sql
-- Catégorie meteorology sous process
INSERT INTO categories (section_id, code, title, order_priority, active, created_at, modified_at) VALUES
  ((SELECT id FROM sections WHERE code='process'),
   'meteorology', 'Meteorology', 50, 1, datetime('now'), datetime('now'));

-- Paradigme 1 : API d'abord, jamais de mémoire météo
INSERT INTO paradigms (category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at) VALUES
((SELECT c.id FROM categories c JOIN sections s ON s.id=c.section_id WHERE s.code='process' AND c.code='meteorology'),
 'weather_api_required', 'Weather data from API only',
 '- Never use your training data to answer meteorological questions.
- All weather information MUST come from the weather tool response.
- If the tool returns an error or no data, report the failure explicitly — do not guess.',
 'Prevents the LLM from confabulating climate data from its parametric memory.',
 0, 10, 1, datetime('now'), datetime('now')),

-- Paradigme 2 : interpréter fidèlement, sans sur-inférence
((SELECT c.id FROM categories c JOIN sections s ON s.id=c.section_id WHERE s.code='process' AND c.code='meteorology'),
 'weather_faithful_report', 'Faithful weather report',
 '- Report only what the tool returned. Do not infer future trends beyond the returned data.
- Use the wmo_descriptions field to translate numeric weather codes.
- Present temperatures, precipitation and wind with their units as returned by the API.
- If the user asked about a specific day not covered by the returned window, say so explicitly.',
 'Prevents over-interpretation of meteorological data.',
 0, 20, 1, datetime('now'), datetime('now'));
```

### Grant outil pour `weather-specialist`

```sql
INSERT INTO agent_tools (agent_id, tool_code) VALUES
  ((SELECT id FROM agents WHERE code='weather-specialist'), 'weather');
```

### Binding paradigmes non-globaux → `weather-specialist`

```sql
INSERT INTO agent_paradigms (agent_id, paradigm_id)
SELECT a.id, p.id FROM agents a, paradigms p
WHERE a.code='weather-specialist'
  AND p.code IN ('weather_api_required', 'weather_faithful_report',
                 'audit_phase');
-- audit_phase inclus : force le spécialiste à analyser le briefing avant d'appeler le tool
```

> `audit_phase` est déjà dans la DB (`is_global=0`). On le bind explicitement à `weather-specialist` pour qu'il analyse d'abord le briefing (mode? fenêtre? localisation?) avant tout appel.

---

## 4. Dépendance `geopy` (`pyproject.toml`)

```toml
dependencies = [
  ...
  "geopy>=2.4.0",   # geocoding Nominatim (libre, sans clé)
  ...
]
```

**Justification `geopy` vs stdlib :** Nominatim impose un `User-Agent` custom. `geopy` gère ça proprement et sérialise les URL correctement. Pas d'API key requise.

**Alternative sans dépendance supplémentaire :** utiliser l'API de géocodage open-meteo elle-même (`https://geocoding-api.open-meteo.com/v1/search?name=Montreal`). C'est plus simple mais moins précis pour des requêtes ambiguës. À évaluer.

> **Recommandation** : commencer par l'API open-meteo geocoding (même fournisseur, zéro dep supplémentaire). Si la précision est insuffisante, basculer sur Nominatim + geopy.

---

## 5. Tests (`tests/test_tools.py` — classe `TestWeather`)

Le handler fait des appels réseau réels. Tests à deux niveaux :

### Tests unitaires (sans réseau)

Mocker `_geocode` et `_fetch_weather` (ou injecter via `unittest.mock.patch`).

```python
class TestWeather:
    def test_lat_lon_format_bypasses_geocoding(self, monkeypatch):
        # "45.5,-73.5" → pas d'appel geocode
        ...

    def test_unknown_mode_returns_error(self):
        from jeanmichel.tools.weather import SPEC
        result = json.loads(SPEC.handler(location="Paris", mode="bogus"))
        assert "error" in result

    def test_wmo_descriptions_included(self, monkeypatch):
        # mock _fetch_weather → data avec weather_code=3
        # vérifie que wmo_descriptions contient "3": "Overcast"
        ...

    def test_response_truncation(self, monkeypatch):
        # mock retourne un payload > 8000 chars
        # vérifie que le retour est tronqué
        ...
```

### Smoke test réseau (optionnel, marqué `@pytest.mark.network`)

```python
@pytest.mark.network
def test_current_weather_montreal():
    result = json.loads(SPEC.handler(location="Montreal"))
    assert "current" in result
    assert "temperature_2m" in result["current"]
```

---

## 6. Comportement attendu en conditions réelles

### Requête simple : météo actuelle

```
Utilisateur: "il fait quel temps à Montréal?"
Jean-Michel: delegate_to("weather-specialist",
    briefing="The human asks for current weather in Montreal.",
    expected="Current conditions and brief 12h outlook for Montreal.")
weather-specialist: weather(location="Montreal", mode="current")
→ JSON avec temp, code WMO, vent, pluie
weather-specialist: return_to_user("Il fait actuellement 12°C à Montréal...")
```

### Requête avec fenêtre temporelle

```
"est-ce qu'il va pleuvoir cette semaine à Paris?"
Jean-Michel: delegate_to("weather-specialist",
    briefing="User wants precipitation forecast for this week in Paris.",
    expected="7-day precipitation forecast for Paris.")
weather-specialist: weather(location="Paris", mode="forecast", forecast_days=7)
```

### Requête météo passée

```
"quel temps faisait-il à Tokyo la semaine dernière?"
weather-specialist: weather(location="Tokyo", mode="history", past_days=7)
```

> **Limite :** `past_days` max = 92 via le forecast API. Pour du vrai historique (années passées), il faudra l'API `https://archive-api.open-meteo.com/v1/archive` — hors scope MVP.

---

## 7. Points d'attention et risques

### Localisation utilisateur par défaut

Le `user_profile.toml` contient une description textuelle. Jean-Michel peut en déduire la ville par défaut pour les requêtes sans localisation explicite ("il fait quel temps ?"). Le briefing envoyé au spécialiste devrait inclure cette info. **Pas de changement de code nécessaire** — le `user_profile` est déjà injecté dans le prompt system de jean-michel via `PromptContext`.

### Nominatim rate limit

Nominatim : max 1 requête/seconde. Usage normal d'un assistant interactif = OK. Pas de rate limiter nécessaire au MVP.

### open-meteo usage policy

Gratuit pour usage non-commercial, < 10 000 appels/jour. Pour usage commercial : prefix URL `customer-api.open-meteo.com` + `apikey`. Hors scope MVP.

### Gestion des erreurs réseau

Le handler catch les exceptions réseau et les retourne comme JSON `{"error": "..."}`. L'orchestrateur transmet ce résultat au LLM comme tool_response. Le spécialiste lit l'erreur et la reporte à l'utilisateur plutôt que d'inventer une réponse (paradigme `weather_api_required`).

### API open-meteo geocoding vs Nominatim

Open-meteo propose sa propre API de géocodage :
```
GET https://geocoding-api.open-meteo.com/v1/search?name=Montreal&count=1&language=fr
```
Retourne `{"results": [{"latitude": 45.5, "longitude": -73.57, "name": "Montréal", ...}]}`.

**Avantages :** même fournisseur, zéro dep supplémentaire (`urllib.request`), bon pour les noms de villes simples.
**Inconvénients :** moins précis pour les requêtes ambiguës, pas de reverse geocoding.

**Recommandation finale :** implémenter d'abord avec l'API open-meteo geocoding, sans dépendance supplémentaire. Ajouter `geopy`/Nominatim en fallback uniquement si la précision est insuffisante.

---

## 8. Ordre d'implémentation recommandé

1. `src/jeanmichel/tools/weather.py` — handler + SPEC (avec open-meteo geocoding)
2. `src/jeanmichel/tools/__init__.py` — enregistrement dans le registre
3. `db/schema.sql` — INSERT agent, catégorie, paradigmes, grant, bindings
4. Migration de la DB live : exécuter les INSERT uniquement (pas re-init complète)
5. `tests/test_tools.py` — `TestWeather` avec mocks réseau
6. Smoke test manuel via `./jm.sh` : "il fait quoi à Lyon?"

---

## Annexe — URL open-meteo exemple (mode current, Montréal)

```
https://api.open-meteo.com/v1/forecast
  ?latitude=45.5088
  &longitude=-73.5878
  &timezone=auto
  &current=temperature_2m,relative_humidity_2m,apparent_temperature,
           precipitation,weather_code,cloud_cover,
           wind_speed_10m,wind_direction_10m,wind_gusts_10m,is_day
  &hourly=temperature_2m,precipitation_probability,weather_code
  &forecast_hours=12
  &past_days=0
```

Réponse JSON (extrait) :
```json
{
  "latitude": 45.5,
  "longitude": -73.5,
  "timezone": "America/Toronto",
  "current": {
    "time": "2026-04-27T15:00",
    "temperature_2m": 12.4,
    "weather_code": 3,
    "wind_speed_10m": 18.2,
    "is_day": 1
  },
  "hourly": {
    "time": ["2026-04-27T00:00", ...],
    "temperature_2m": [6.2, 5.9, ...],
    "precipitation_probability": [5, 5, ...],
    "weather_code": [2, 2, ...]
  }
}
```
