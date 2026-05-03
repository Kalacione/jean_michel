# Sources de données ouvertes — Sélection pour jean-michel

Source : https://github.com/orgs/datasets/repositories (161 repos, parcourus le 2026-05-03)

Critère de sélection : utilité pour alimenter un **outil Python** (appel à la volée) ou servir de **données de référence statiques** embarquées (lookup tables, codes normalisés). Les datasets purement analytiques ou ML (breast-cancer, tic-tac-toe, etc.) sont exclus.

---

## Priorité haute — Outils Python à créer

### Finance & marchés

| Repo | Description | Usage envisagé |
|------|-------------|----------------|
| [exchange-rates](https://github.com/datasets/exchange-rates) | Taux de change depuis la Fed US | Tool `exchange_rates` — conversion devises, contexte économique |
| [gold-prices](https://github.com/datasets/gold-prices) | Prix de l'or (série temporelle) | Tool `commodity_price` ou enrichissement `finance-specialist` |
| [oil-prices](https://github.com/datasets/oil-prices) | Brent + WTI depuis l'EIA | Idem — prix baril en temps quasi-réel |
| [natural-gas](https://github.com/datasets/natural-gas) | Prix gaz naturel (Henry Hub) | Idem |
| [s-and-p-500](https://github.com/datasets/s-and-p-500) | Historique de l'indice S&P 500 | `finance-specialist` — contexte marché |
| [s-and-p-500-companies](https://github.com/datasets/s-and-p-500-companies) | Liste des 500 + financials | Lookup ticker → nom + secteur |
| [nasdaq-listings](https://github.com/datasets/nasdaq-listings) | Sociétés listées au Nasdaq | Lookup ticker |

> **Note :** ces repos contiennent des CSV mis à jour périodiquement, pas des APIs live. Pour un vrai outil temps réel, coupler avec une API financière (Yahoo Finance, Alpha Vantage…). Les repos servent de fallback / référence offline.

### Géographie & localisation

| Repo | Description | Usage envisagé |
|------|-------------|----------------|
| [country-codes](https://github.com/datasets/country-codes) | ISO 3166 + ITU + ISO 4217 + fuseaux | **Lookup table centrale** — normaliser noms de pays dans tous les outils |
| [world-cities](https://github.com/datasets/world-cities) | Villes majeures mondiales (lat/lon) | Améliorer la résolution géographique du `weather` tool |
| [airport-codes](https://github.com/datasets/airport-codes) | IATA/ICAO + coordonnées + timezone | Tool `airport_info` ou enrichissement météo pour aéroports |
| [country-list](https://github.com/datasets/country-list) | ISO 2 codes → noms pays (CSV/JSON) | Lookup léger, zéro dep |
| [continent-codes](https://github.com/datasets/continent-codes) | 7 continents + codes 2 lettres | Lookup pour contextualiser les pays |
| [un-locode](https://github.com/datasets/un-locode) | Codes UN pour localisations commerce/transport | Utile si un spécialiste logistique est ajouté |

### Climat & environnement

| Repo | Description | Usage envisagé |
|------|-------------|----------------|
| [co2-ppm](https://github.com/datasets/co2-ppm) | CO2 atmosphérique (Mauna Loa, mensuel) | Enrichir un futur `environment-specialist` |
| [co2-ppm-daily](https://github.com/datasets/co2-ppm-daily) | CO2 quotidien | Idem, granularité plus fine |
| [global-temp](https://github.com/datasets/global-temp) | Séries temp globales (NASA GISS + NOAA) | Contexte climatique long terme — complément au `weather-specialist` |
| [sea-level-rise](https://github.com/datasets/sea-level-rise) | Montée des eaux (CSIRO + NASA) | Idem |
| [glacier-mass-balance](https://github.com/datasets/glacier-mass-balance) | Bilan de masse des glaciers de référence | Idem |

### Économie & indicateurs macro

| Repo | Description | Usage envisagé |
|------|-------------|----------------|
| [gdp](https://github.com/datasets/gdp) | PIB mondial en USD courants (Banque mondiale) | Contexte pour `comparator-specialist` |
| [inflation](https://github.com/datasets/inflation) | Inflation annuelle + déflateur PIB | Idem |
| [cpi](https://github.com/datasets/cpi) | Indice des prix à la consommation mondial | Idem |
| [population](https://github.com/datasets/population) | Population par pays/région (Banque mondiale) | Contexte démographique — enrichit réponses comparatives |
| [ppp](https://github.com/datasets/ppp) | Parité de pouvoir d'achat | Comparaisons économiques internationales |

---

## Priorité moyenne — Données de référence utiles

| Repo | Description | Notes |
|------|-------------|-------|
| [currency-codes](https://github.com/datasets/currency-codes) | ISO 4217 — codes + noms monnaies | Complément à `country-codes` |
| [language-codes](https://github.com/datasets/language-codes) | ISO 639-1 + 639-2 | Potentiellement utile pour le système lui-même (détection langue) |
| [openflights](https://github.com/datasets/openflights) | Aéroports, compagnies, routes | Base riche pour un futur `travel-specialist` |
| [finance-vix](https://github.com/datasets/finance-vix) | VIX (volatilité marché) historique | `finance-specialist` — jauge de stress marché |
| [commodity-prices](https://github.com/datasets/commodity-prices) | 53 matières premières + 10 indices (1980-2016) | Données historiques — moins utile pour temps réel |
| [corruption-perceptions-index](https://github.com/datasets/corruption-perceptions-index) | CPI Transparency International | Enrichit des analyses géopolitiques ou comparatives |
| [world-happiness-report](https://github.com/datasets/world-happiness-report) | World Happiness Report data | Idem — score par pays |
| [threatened-species](https://github.com/datasets/threatened-species) | Liste rouge IUCN | `environment-specialist` potentiel |
| [gini-index](https://github.com/datasets/gini-index) | Indice de Gini (inégalités) | Comparaisons socio-économiques |
| [media-types](https://github.com/datasets/media-types) | MIME types + extensions | Lookup technique interne |

---

## Priorité basse — Intéressant mais hors scope immédiat

| Repo | Description | Notes |
|------|-------------|-------|
| [harmonized-system](https://github.com/datasets/harmonized-system) | Codes HS douaniers (178 étoiles) | Utile seulement si spécialiste commerce/import-export |
| [geoip2-ipv4](https://github.com/datasets/geoip2-ipv4) | Géolocalisation IP (CC0) | Utile si jean-michel expose une API web |
| [co2-fossil-by-nation](https://github.com/datasets/co2-fossil-by-nation) | Émissions CO2 par pays depuis 1751 | Historique long — enrichit environnement |
| [eu-emissions-trading-system](https://github.com/datasets/eu-emissions-trading-system) | Données ETS européen | Spécialisé UE |
| [imf-weo](https://github.com/datasets/imf-weo) | World Economic Outlook FMI | Recoupement avec `gdp` + `inflation` |
| [world-development-indicators](https://github.com/datasets/world-development-indicators) | Indicateurs développement Banque mondiale | Large, à filtrer |
| [seshat](https://github.com/datasets/seshat) | Global History Databank | Historique humain long terme — niche mais riche |

---

## Exclus et pourquoi

- **Datasets London-only** (london-transport, london-crime, london-air-quality…) — trop géographiquement limités pour un assistant généraliste
- **Datasets ML/médical** (breast-cancer, hepatitis, lymph, tic-tac-toe…) — training data, pas utiles à la volée
- **Données US-only très spécifiques** (employment-us, cpi-us, house-prices-us…) — pertinents seulement si un `us-specialist` est créé
- **Repos infrastructure** (commons, collective, labs, core-datasets) — méta-catalogues, pas des données
- **Données obsolètes** (crunchbase-data 2015, etc.)

---

## Outils Python à envisager en priorité

Sur la base de cette sélection, les outils les plus naturels à implémenter :

1. **`exchange_rates`** — taux de change via une API live (ECB, Open Exchange Rates, Frankfurter.app) + fallback sur le dataset Fed. Alimenterait un `finance-specialist`.
2. **`country_lookup`** — lookup statique `country-codes` + `currency-codes` embarqués. Outil de référence réutilisable par tous les spécialistes (normalisation pays/monnaie/ISO). Zéro appel réseau.
3. **`population`** + **`gdp`** — données macro injectables dans le `comparator-specialist` pour contextualiser automatiquement des comparaisons entre pays.
4. **`co2_trend`** — CO2 quotidien + températures globales pour un futur `environment-specialist`.
