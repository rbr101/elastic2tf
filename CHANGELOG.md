# Changelog

Alle noemenswaardige wijzigingen in dit project. Formaat volgens
[Keep a Changelog](https://keepachangelog.com/nl/1.1.0/), versies volgens
[Semantic Versioning](https://semver.org/).

## [0.10.0] - 2026-11-26

### Toegevoegd
- De kop van elk gegenereerd bestand bevat nu ook het gebruikte commando, de gebruiker(s)
  waarmee is opgehaald (bij `--fetch`, met de url van Elasticsearch/Kibana) of de inputbestanden,
  en de datum en tijd van genereren.

## [0.9.1] - 2026-11-26

### Gewijzigd
- `policy_id` van Fleet agent- en integration policies is nu de naam in kleine letters met `-`
  in plaats van spaties (`Kafka consumer` → `kafka-consumer`); voorheen het bron-ID. Dubbele
  ID's krijgen `-2`, `-3`, … De gewijzigde ID's staan in het commentaar en in de samenvatting.
- Import-blocks alleen voor Fleet policies waarvan het ID gelijk blijft.

### Toegevoegd
- `--keep-policy-ids`: het bron-ID als `policy_id` houden (oude gedrag).

## [0.9.0] - 2026-11-26

### Toegevoegd
- **Fleet policies**: nieuwe types `fleet_agent_policy` (`GET kbn:/api/fleet/agent_policies`)
  → `elasticstack_fleet_agent_policy` en `fleet_integration_policy`
  (`GET kbn:/api/fleet/package_policies?format=simplified`) → `elasticstack_fleet_integration_policy`
  met de `inputs`-map, plus één `elasticstack_fleet_integration` (`skip_destroy = true`) per
  gebruikt package. Ook `agent_policies?full=true` en het oude inputs-formaat worden gelezen.
- `--fetch fleet` haalt agent- en integration policies op (per space, alle pagina's);
  `fleet_agent_policy` haalt de integration policies automatisch mee. Fleet zit niet in de
  standaardset van `--fetch`.
- `--items fleet_agent_policy:<naam>`: de integraties van de gekozen agent policy gaan mee.
- Secret-vars van integration policies worden `sensitive` variabelen `fleet_<policy>_<veld>`
  (te vullen met `TF_VAR_...`), met `fleet_secrets.tfvars.example`; zulke policies staan als
  losse resource (niet in de for_each-map).
- `--no-fleet-integrations`: geen package-resources genereren.
- Managed/preconfigured agent policies (bijv. ECK) en hun integraties worden standaard
  overgeslagen (`--include-managed` of exact noemen in `--items` neemt ze mee).

## [0.8.6] - 2026-11-19

### Opgelost
- Component templates met een failure store (`template.data_stream_options`) gaven
  `Unsupported block type` op oudere providers. De provider-constraint in `provider.tf` is nu
  `>= 0.15.2, < 1.0.0` (was `~> 0.11`); bestaande lock files bijwerken met `terraform init -upgrade`.
- `template.lifecycle` wordt niet meer geschreven bij component templates (de provider ondersteunt
  het daar niet); er volgt een waarschuwing. Bij index templates blijft het gewoon staan.

## [0.8.5] - 2026-11-19

### Gewijzigd
- `override = true` wordt nu **standaard** gezet op alle data views, ook die uit data streams
  (voorheen alleen met `--override` en alleen op data views uit de Kibana API).
  `--no-override` zet het uit; `--override` blijft werken maar is niet meer nodig.

## [0.8.4] - 2026-11-18

### Toegevoegd
- `--saved-object-types TYPES`: types voor de saved objects-export bij `--fetch` op de
  commandline (komma-gescheiden of herhaalbaar), bijv. `--saved-object-types search` voor
  discover sessions / saved searches of `dashboard,search`. Gaat voor `saved_object_types`
  in de config.

## [0.8.3] - 2026-11-17

### Gewijzigd
- Connector-secrets komen nu uit **losse variabelen per connector-veld** in plaats van één
  `map(map(string))`: bijv. `conn1_user` en `conn1_password` voor een webhook met basic auth.
  De runner vult ze met CI/CD-variabelen `TF_VAR_conn1_user`, `TF_VAR_conn1_password`.
  `variables.tf` bevat de variabelen (`sensitive`, zonder default) en
  `local.connector_secrets`; de connectors verwijzen naar `local.connector_secrets[<bron-ID>]`.
  **Let op:** bestaande `connector_secrets`-tfvars omzetten, of `--connector-secrets-map` gebruiken.

### Toegevoegd
- `connector_secrets.tfvars.example` met placeholders naast de output, en na afloop een lijst
  van de benodigde `TF_VAR_...`-namen.
- Config-sectie `[connector_secrets]`: vaste variabele-prefix per connectornaam of bron-ID
  (wordt ook zonder `--fetch` gelezen), zodat de namen niet verschuiven.
- `--secret-prefix PREFIX` (default `conn`).
- `--connector-secrets-map`: de oude vorm met `var.connector_secrets`.
- Velden worden uit de connector-config afgeleid: webhook zonder auth krijgt geen variabelen,
  SSL-webhook `crt`/`key`/`password` of `pfx`/`password`; onbekende types één
  `<prefix>_secrets_json`.

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
