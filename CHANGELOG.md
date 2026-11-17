# Changelog

Alle noemenswaardige wijzigingen in dit project. Formaat volgens
[Keep a Changelog](https://keepachangelog.com/nl/1.1.0/), versies volgens
[Semantic Versioning](https://semver.org/).

## [0.8.2] - 2026-11-17

### Toegevoegd
- `--tag-filter GLOB`: saved objects filteren op tagnaam (glob, hoofdletterongevoelig,
  herhaalbaar), bijv. alleen discover sessions met ITOA in de tag:
  `--fetch saved_objects --tag-filter '*itoa*' --title-filter-type search`.
  Gerefereerde objecten (data views, tags) gaan mee; samen met `--title-filter` moeten beide matchen.
- Config: `[fetch] tag_filter = ...` en `filter_type = ...`.
- Met `--fetch` wordt het type van het filter (bijv. `search`) automatisch meegenomen in de
  export; `saved_object_types` hoeft daarvoor niet aangepast te worden.
- De gevonden objecten worden met hun tags getoond; de comment in `saved_objects.tf` noemt ook
  discover sessions.

### Gewijzigd
- `--title-filter-type` geldt ook voor `--tag-filter`.

### Let op
- `--tag-filter` werkt alleen op `_export`-output met `includeReferencesDeep` (zoals `--fetch`
  doet). Een `_find`-response bevat de tags zelf niet; het script waarschuwt dan.

## [0.7.1] - 2026-11-17

### Gewijzigd
- `var.connector_secrets` is nu `map(map(string))`: per bron-connector-ID een object met de
  secret-velden, zonder `jsonencode()` (functies zijn in `.tfvars` niet toegestaan). De
  `.tf`-code doet `try(jsonencode(var.connector_secrets[id]), null)`.
  **Let op:** bestaande `.tfvars` met JSON-strings omzetten naar objecten.
- In de `for_each`-map van connectors staat alleen het ID (`secrets_id`), niet de secrets:
  gevoelige waarden zijn niet toegestaan in `for_each`.

### Toegevoegd
- `--with-moved`: maakt `moved.tf` om een bestaande state van losse blocks (v0.3.x of
  `--no-for-each`) naar de `for_each`-adressen te verplaatsen, in plaats van destroy/create.

## [0.7.0] - 2026-11-17

### Toegevoegd
- Connectors komen automatisch mee met de alerting rules die ze gebruiken:
  - `--fetch` haalt bij rules ook de connectors uit dezelfde space op;
  - een connector waar een omgezette rule naar verwijst wordt altijd omgezet, ook als
    `--items` hem niet selecteert;
  - zijn de rules gefilterd met `--items` (en de connectors niet), dan komen alleen de gebruikte
    connectors mee.
- `--no-rule-connectors`: het oude gedrag (connectors niet automatisch meenemen; rules verwijzen
  via `local.connector_ids` naar bestaande connectors).

## [0.6.0] - 2026-11-17

### Toegevoegd
- `--items NAMEN`: per resource-type kiezen welke objecten worden omgezet. `naam` geldt voor
  alle types, `type:naam` voor één type (zelfde type-namen als bij `--fetch`; het type geldt
  ook voor de namen erna: `ilm:a,b,role:c`); globs,
  hoofdletterongevoelig, komma-gescheiden en herhaalbaar. Werkt voor alle types: templates,
  ILM, SLM, pipelines, rollen, role mappings, cluster settings, data views, connectors, rules,
  spaces en saved objects (op dashboardtitel). Exact genoemde beheerde objecten gaan ook mee.
- Met `--fetch` en alleen `type:naam`-items worden alleen die types opgehaald.
- Config: `[fetch] items = ...`.

### Gewijzigd
- `--pipelines` (en `[fetch] pipelines`) is verouderd en gelijk aan `--items ingest_pipeline:...`;
  het blijft werken. Namen worden nu hoofdletterongevoelig vergeleken.

## [0.5.0] - 2026-11-17

### Toegevoegd
- `--pipelines NAMEN`: alleen de opgegeven ingest pipelines omzetten (komma-gescheiden, globs
  mogen, herhaalbaar). Exact genoemde beheerde pipelines gaan ook mee; ontbrekende namen geven
  een waarschuwing. Ook in de config: `[fetch] pipelines = ...`.

## [0.4.1] - 2026-11-17

### Gewijzigd
- Compactere `for_each`-output: velden die bij alle objecten gelijk zijn staan één keer in de
  resource; velden die uit de sleutel volgen worden `each.key` (of bijv. `"${each.key}*"`).
  Blijft er per object niets over, dan wordt de `locals`-map een `toset([...])`-lijst. Data views
  uit data streams zijn zo één regel per data view.

## [0.4.0] - 2026-11-16

### Gewijzigd
- Het script heet nu `elastic2tf.py` (was `devtools2tf.py`), gelijk aan de naam van het project.
- Terraform-output: per resource-type één resource met `for_each` over een `locals`-map, in
  plaats van een los block per object. Geneste blocks worden `dynamic` blocks; velden die niet
  bij elk object voorkomen via `try()`. Import-blocks en connector-verwijzingen gebruiken de
  nieuwe adressen. **Let op:** de adressen in de state veranderen; zie de README.

### Toegevoegd
- `--no-for-each`: de oude output (elk object als los resource-block).

## [0.3.1] - 2026-11-16

### Gewijzigd
- De header van gegenereerde bestanden en de samenvatting tonen de naam waaronder het script is
  uitgevoerd, in plaats van altijd `devtools2tf.py`.

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

[0.7.1]: https://github.com/rbr101/elastic2tf/releases/tag/v0.7.1
[0.7.0]: https://github.com/rbr101/elastic2tf/releases/tag/v0.7.0
[0.6.0]: https://github.com/rbr101/elastic2tf/releases/tag/v0.6.0
[0.5.0]: https://github.com/rbr101/elastic2tf/releases/tag/v0.5.0
[0.4.1]: https://github.com/rbr101/elastic2tf/releases/tag/v0.4.1
[0.4.0]: https://github.com/rbr101/elastic2tf/releases/tag/v0.4.0
[0.3.1]: https://github.com/rbr101/elastic2tf/releases/tag/v0.3.1
[0.3.0]: https://github.com/rbr101/elastic2tf/releases/tag/v0.3.0
