# Discovery of Programmatically Accessible Information Sources for AI Agents

This document lists reliable, structured, and programmatically accessible information sources across various domains, suitable for serving as a 'source of truth' for an AI agent.

## Summary Table

| Name | Domain | Added Value | Access Method |
| :--- | :--- | :--- | :--- |
| **Wikidata** | Encyclopedic | Structured knowledge graph with rich relations | SPARQL / API (JSON) |
| **DBpedia** | Encyclopedic | Structured, machine-readable version of Wikipedia | SPARQ endpoint |
| **arXiv** | Scientific | Access to a massive repository of scientific pre-prints | API (REST/JSON) |
| **PubMed** | Scientific | Comprehensive, authoritative biomedical literature | API (E-utilities/XML/JSON) |
| **Crossref** | Scientific | Metadata for scholarly works via DOIs | API (REST/JSON) |
| **OpenAlex** | Scientific | Open, large-scale catalog of global scholarly works | API (REST/JSON) |
| **OpenStreetMap** | Geographic | Highly detailed, community-driven global map data | Overpass API (JSON/XML) |
| **GeoNames** | Geographic | Global database of place names and coordinates | API (REST/JSON) |
| **Stack Overflow** | Technical | High-quality, community-vetted programming Q&A | API (REST/JSON) |
| **GitHub** | Technical | Repository metadata, code, and developer activity | API (REST/JSON) |
| **MDN Web Docs** | Technical | Authoritative documentation for web technologies | API / Data Dumps |
| **News API** | News | Real-time retrieval of news and blog articles | API (REST/JSON) |
| **OpenWeather** | Weather | Global current, forecast, and historical weather data | API (REST/JSON) |
| **Open-Meteo** | Weather | Free, high-resolution weather forecasts | API (REST/JSON) |
| **National Weather Service (NWS)** | Weather | Critical US weather alerts and observations | API (REST/JSON) |
| **INSEE / French Public Data** | Economics/Gov | Indicators on labor markets and public statistics | API (JSON/Open Data) |

## Detailed Breakdown

### 1. Encyclopedic & General Knowledge
*   **Wikidata**: A collaborative, multilingual knowledge base. It provides the underlying structured data for Wikipedia.
*   **DBpedia**: Extracts structured information from Wikipedia, making it accessible via SPARQL queries.

### 2. Scientific & Academic
*   **arXiv**: Essential for staying updated on the latest research in physics, math, and computer science.
*   **PubMed**: The gold standard for biomedical and life sciences information.
*   **Crossref**: Provides the metadata necessary to link and identify scholarly publications.
*   **OpenAlex**: A powerful, open alternative to proprietary citation indexes.

### 3. Geographic & Mapping
*   **OpenStreetMap (OSM)**: The "Wikipedia of maps," providing granular, editable geographic data.
*   **GeoNames**: A massive repository of geographical names, useful for geocoding and spatial lookups.

### 4. Technical & Programming
*   **Stack Overflow**: The primary source for troubleshooting and programming logic.
*   **GitHub**: Provides context on codebases, commits, and software development trends.
*   **MDN Web Docs**: The definitive source for web standards (HTML, CSS, JS).

### 5. News & Current Events
*   **News API**: Aggregates news from various global sources into a single, searchable interface.
*   **RSS Feeds**: Still a highly reliable method for subscribing to specific, high-authority publication streams.

### 6. Specialized Domains (Weather, Economics, etc.)
*   **OpenWeather / Open-Meteo**: Critical for agents requiring environmental context.
*   **National Weather Service**: High-reliability data for US-based meteorological events.
*   **French Public Data (e.g., INSEE/Labo SN)**: Provides structured economic and social indicators (e.g., labor market dynamics).
