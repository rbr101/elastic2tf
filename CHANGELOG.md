# Changelog

Alle noemenswaardige wijzigingen in dit project. Formaat volgens
[Keep a Changelog](https://keepachangelog.com/nl/1.1.0/), versies volgens
[Semantic Versioning](https://semver.org/).

## [0.3.0] - 2026-11-16

Eerste publieke versie.

### Toegevoegd
- `devtools2tf.py`: zet Kibana Dev Tools (Console) output om naar Terraform-code voor de
  `elastic/elasticstack` provider; het type output wordt automatisch herkend.
- Converters voor: data streams → data views, Kibana data views, index/component templates,
  ILM, SLM, ingest pipelines, security roles & role mappings, cluster settings, Kibana spaces,
  action connectors, alerting rules (met rapport per rule) en saved objects/dashboards.
- `--fetch`: JSON direct ophalen via de Elasticsearch- en Kibana-API. URL's en gebruikersnamen
  staan in `elastic2tf.ini` (zie `elastic2tf.example.ini`); het wachtwoord wordt verborgen in de
  terminal gevraagd en nergens opgeslagen.
- `--fetch WAT ...`: kies wat je ophaalt met een type (`ilm`), een Terraform-resource
  (`elasticstack_elasticsearch_index_lifecycle`, prefix optioneel) of de Dev Tools-call zelf
  (`"GET _ilm/policy/mijn-policy"`, `"GET kbn:/s/ops/api/actions/connectors"`, met optionele
  JSON-body). Ook te zetten in `[fetch] types` in de config.
- Kibana-calls per space, paginering van alerting rules, data views per ID (met tijdveld en
  runtime fields) en saved objects-export met referenties.
- `--config` en `--save-export` (opgehaalde responses bewaren in Dev Tools-formaat, te
  hergebruiken met `-i`). Met `--fetch --with-imports` wordt de cluster UUID automatisch opgehaald.
- Versie-informatie: `--version`, versie in de header van gegenereerde bestanden en in de
  samenvatting.
- Documentatie van het script in de README.

[0.3.0]: https://github.com/rbr101/elastic2tf/releases/tag/v0.3.0
