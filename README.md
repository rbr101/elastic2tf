# elastic2tf

`elastic2tf.py` zet Kibana Dev Tools (Console) output om naar Terraform-code voor de
[`elastic/elasticstack`](https://registry.terraform.io/providers/elastic/elasticstack) provider.
Het type output (index templates, ILM, ingest pipelines, rollen, connectors, alerting rules,
data views, spaces, saved objects, …) wordt automatisch herkend.

Vereist alleen Python 3.8+ (geen extra packages).

## Inhoud

- [Snelstart](#snelstart)
- [Werkwijze](#werkwijze)
- [Direct ophalen via de API](#direct-ophalen-via-de-api)
- [Ondersteunde Dev Tools calls](#ondersteunde-dev-tools-calls)
- [Opties](#opties)
- [Output](#output)
- [Gedrag per resource-type](#gedrag-per-resource-type)
- [Objecten selecteren met --items](#objecten-selecteren-met---items)
- [Alerting rules en connectors](#alerting-rules-en-connectors)
- [Bestaande objecten importeren](#bestaande-objecten-importeren)
- [Problemen oplossen](#problemen-oplossen)
- [Versie](#versie)

## Snelstart

```bash
# 1. Voer in Dev Tools de gewenste GET-calls uit en kopieer de volledige output naar export.txt
# 2. Omzetten:
./elastic2tf.py -i export.txt --output-dir terraform/
# Of direct via de API (wachtwoord wordt gevraagd):
./elastic2tf.py --fetch --output-dir terraform/
./elastic2tf.py --list-types     # ondersteunde types
./elastic2tf.py --help           # alle opties
```

## Werkwijze

1. Plak in Kibana **Dev Tools > Console** de calls die je nodig hebt (zie de lijst hieronder).
   Selecteer ze allemaal en voer ze in één keer uit.
2. Kopieer de **volledige** response-output, inclusief de regels `# GET ... 200 OK`, naar een
   bestand, bijvoorbeeld `export.txt`. Het script gebruikt die regels om het type te herkennen.
   Losse JSON-bestanden per call kunnen ook; het type wordt dan uit de inhoud afgeleid.
3. Zet het om:

   ```bash
   ./elastic2tf.py -i export.txt --output-dir terraform/
   ```

4. Controleer de gegenereerde `.tf`-bestanden en de samenvatting op stderr. Voer daarna
   `terraform init` en `terraform plan` uit.

Input kan ook via stdin: `cat export.txt | ./elastic2tf.py > main.tf`.

## Direct ophalen via de API

In plaats van kopiëren uit Dev Tools kan het script de JSON zelf ophalen via de Elasticsearch- en
de Kibana-API. Daarvoor is een configbestand met de URL's en gebruikersnamen nodig.

1. Kopieer de voorbeeldconfig en vul `url` en `username` in:

   ```bash
   cp elastic2tf.example.ini elastic2tf.ini
   ```

   ```ini
   [elasticsearch]
   url      = https://elasticsearch.example.local:9200
   username = elastic
   # ca_cert  = /pad/naar/ca.crt

   [kibana]
   url      = https://kibana.example.local:5601
   username = elastic

   [fetch]
   spaces             = default
   datastream_pattern = logs-*
   saved_object_types = dashboard
   ```

2. Draai het script met `--fetch`, eventueel met wat je wilt ophalen
   (zie [Kiezen wat je ophaalt](#kiezen-wat-je-ophaalt)):

   ```bash
   ./elastic2tf.py --fetch --output-dir terraform/          # alles (of [fetch] types uit de config)
   ./elastic2tf.py --fetch ilm index_template -o es.tf      # alleen deze resources
   ```

3. Het **wachtwoord** wordt in de terminal gevraagd. Wat je typt is niet zichtbaar, en het
   wachtwoord wordt nergens opgeslagen. Hebben Elasticsearch en Kibana dezelfde `username`, dan
   wordt het maar één keer gevraagd. Een `password` in de config wordt geweigerd.

Het script zoekt de config in deze volgorde: `--config BESTAND`, `./elastic2tf.ini`,
`~/.config/elastic2tf.ini`. `elastic2tf.ini` staat in `.gitignore`.

### Kiezen wat je ophaalt

Na `--fetch` geef je op wat er opgehaald moet worden. Dat kan op drie manieren, door elkaar:

**1. Type** (zoals in `--list-types`), meerdere gescheiden door spaties of komma's:

```bash
./elastic2tf.py --fetch ilm index_template role -o es.tf
./elastic2tf.py --fetch alerting_rule,connector -o alerting.tf
```

**2. Terraform-resource**, met of zonder `elasticstack_`-prefix:

```bash
./elastic2tf.py --fetch elasticstack_elasticsearch_index_lifecycle elasticstack_kibana_alerting_rule -o x.tf
./elastic2tf.py --fetch elasticsearch_ingest_pipeline kibana_action_connector -o x.tf
```

**3. De Dev Tools-call zelf**, in dezelfde notatie als in de tabel
[Ondersteunde Dev Tools calls](#ondersteunde-dev-tools-calls). Zet de call tussen aanhalingstekens.
`GET` mag je weglaten; Kibana-calls beginnen met `kbn:`. Zo haal je ook één specifiek object op, of
iets uit een andere space:

```bash
# één ILM-policy en de index templates die met "mijn-" beginnen
./elastic2tf.py --fetch "GET _ilm/policy/mijn-policy" "GET _index_template/mijn-*" -o x.tf

# data streams met een ander pattern
./elastic2tf.py --fetch "GET _data_stream/metrics-*/_stats?human" -o dv.tf

# connectors uit space "ops", en één rule op ID
./elastic2tf.py --fetch "GET kbn:/s/ops/api/actions/connectors" "GET kbn:/api/alerting/rule/<id>" -o x.tf

# alleen Observability-rules, met een filter (spaties in de query mogen)
./elastic2tf.py --fetch "GET kbn:/api/alerting/rules/_find?filter=alert.attributes.consumer:(observability or logs)" -o rules.tf

# één dashboard met alles waar het naar verwijst (body als JSON achter het pad)
./elastic2tf.py --fetch 'POST kbn:/api/saved_objects/_export {"objects":[{"type":"dashboard","id":"<id>"}]}' -o dash.tf
```

Bij calls doet het script automatisch:

- `.../api/alerting/rules/_find`: alle pagina's ophalen;
- `.../api/saved_objects/_export`: `includeReferencesDeep` aanzetten (tenzij je het zelf opgeeft);
  zonder body worden de `saved_object_types` uit de config geëxporteerd;
- `GET kbn:.../api/data_views`: elke data view daarna apart ophalen (tijdveld, runtime fields).

Het type van de response wordt herkend aan de call, net als bij een Dev Tools-export. Bij een
type of resource gelden de `spaces`, `datastream_pattern` en `saved_object_types` uit de config;
een call gebruikt precies wat je opgeeft.

`--fetch` mag ook meerdere keren: `--fetch ilm --fetch "GET _security/role/lezer"`.

**Volgorde:** wat na `--fetch` staat gaat voor; anders `--only`; anders `[fetch] types` uit de
config; anders alles behalve `cluster_settings`.

In de config kun je hetzelfde zetten, met komma's of één per regel. Een regel met een call blijft
heel:

```ini
[fetch]
types = ilm, index_template, elasticstack_kibana_alerting_rule
    GET _security/role/lezer
    POST kbn:/api/saved_objects/_export {"objects":[{"type":"dashboard","id":"abc"}]}
```

### Configuratie

| Sectie | Sleutel | Betekenis |
|--------|---------|-----------|
| `[elasticsearch]` | `url` | URL van Elasticsearch, bijv. `https://es.example.local:9200` |
| | `username` | Gebruikersnaam (basic auth) |
| | `ca_cert` | Optioneel: CA-bestand voor een eigen CA (bij ECK: secret `<naam>-es-http-certs-public`) |
| | `verify_ssl` | Optioneel, default `true`. Alleen voor testen op `false` zetten. |
| | `timeout` | Optioneel: seconden per call (default 60) |
| `[kibana]` | `url`, `username`, `ca_cert`, `verify_ssl`, `timeout` | Idem voor Kibana. Zet een eventueel `basePath` in de URL. |
| `[fetch]` | `types` | Wat ophalen: types, Terraform-resources en/of calls (zie [Kiezen wat je ophaalt](#kiezen-wat-je-ophaalt)). Default: alles behalve `cluster_settings`. |
| | `spaces` | Kibana spaces waaruit connectors, rules, data views en saved objects komen (default `default`) |
| | `datastream_pattern` | Pattern voor data streams → data views (default `logs-*`) |
| | `saved_object_types` | Types voor de saved objects-export (default `dashboard`). `--saved-object-types` gaat voor. |
| | `tag_filter` | Zoals `--tag-filter` (komma-gescheiden) |
| | `filter_type` | Zoals `--title-filter-type`, bijv. `search` voor discover sessions |
| | `items` | Alleen deze objecten omzetten, zoals [`--items`](#objecten-selecteren-met---items) (komma's of regels, globs). `--items` gaat voor. |

Alleen de secties die nodig zijn voor de gevraagde types zijn verplicht. Bijvoorbeeld:
`--only ilm` heeft alleen `[elasticsearch]` nodig.

### Wat wordt opgehaald

| Type | Call |
|------|------|
| `datastream` | `GET _data_stream/<datastream_pattern>/_stats?human` |
| `index_template`, `component_template`, `ilm`, `slm`, `ingest_pipeline`, `role`, `role_mapping` | dezelfde `GET`-calls als in [Ondersteunde Dev Tools calls](#ondersteunde-dev-tools-calls) |
| `cluster_settings` | `GET _cluster/settings?flat_settings=true` (alleen als je erom vraagt) |
| `space` | `GET /api/spaces/space` |
| `connector` | `GET /api/actions/connectors`, per space |
| `kibana_data_view` | `GET /api/data_views` en daarna per data view `GET /api/data_views/data_view/<id>`, per space (dus met tijdveld en runtime fields) |
| `alerting_rule` | `GET /api/alerting/rules/_find`, per space, met paginering (alle pagina's) |
| `saved_objects` | `POST /api/saved_objects/_export` met `includeReferencesDeep`, per space |

Voor een andere space dan `default` gaan de Kibana-calls via `/s/<space>/api/...`, en krijgen de
resources automatisch de juiste `space_id`.

Bij het verbinden controleert het script eerst of inloggen lukt (`GET /` en `GET /api/status`) en
toont het de versie. Een call die faalt (bijv. SLM niet beschikbaar, of te weinig rechten) wordt
gemeld en overgeslagen; de rest gaat door. Met `--with-imports` wordt de cluster UUID automatisch
uit `GET /` gehaald, dus `--cluster-uuid` is dan niet nodig.

### Opgehaalde data bewaren

Met `--save-export BESTAND` worden de opgehaalde responses ook bewaard in Dev Tools-formaat. Dat
bestand kun je later opnieuw omzetten met `-i`, zonder opnieuw te verbinden:

```bash
./elastic2tf.py --fetch --save-export export.txt --output-dir tf/
./elastic2tf.py -i export.txt --only alerting_rule --report rules.csv --output-dir tf/
```

Let op: zo'n export kan gevoelige data bevatten. `export*.txt` staat in `.gitignore`.

`--fetch` kan samen met `-i`, bijvoorbeeld om opgehaalde rules te combineren met een eigen bestand.

### Benodigde rechten

Alleen leesrechten zijn nodig, bijvoorbeeld:

- cluster: `monitor`, `read_ilm`, `read_slm`, `read_pipeline` en `read_security`
  (of de `manage_*`-variant);
- indices: `monitor` op de data streams uit `datastream_pattern`;
- Kibana: leesrechten op Stack Management, Alerts and Insights en Dashboards in de opgegeven
  spaces.

Mist er een recht, dan wordt alleen dat type overgeslagen (HTTP 403 in de melding).

## Ondersteunde Dev Tools calls

### Elasticsearch

| Call | Terraform resource |
|------|--------------------|
| `GET _data_stream/logs-*/_stats?human` | `elasticstack_kibana_data_view` (gegroepeerd, zie [Data streams](#data-streams--data-views)) |
| `GET _index_template` | `elasticstack_elasticsearch_index_template` |
| `GET _component_template` | `elasticstack_elasticsearch_component_template` |
| `GET _ilm/policy` | `elasticstack_elasticsearch_index_lifecycle` |
| `GET _ingest/pipeline` | `elasticstack_elasticsearch_ingest_pipeline` |
| `GET _slm/policy` | `elasticstack_elasticsearch_snapshot_lifecycle` |
| `GET _security/role` | `elasticstack_elasticsearch_security_role` |
| `GET _security/role_mapping` | `elasticstack_elasticsearch_security_role_mapping` |
| `GET _cluster/settings?flat_settings=true` | `elasticstack_elasticsearch_cluster_settings` |

### Kibana

Gebruik de prefix `kbn:`. Voor een andere space dan `default`: `kbn:/s/<space-id>/api/...`.
De space wordt dan automatisch uit de call overgenomen.

| Call | Terraform resource |
|------|--------------------|
| `GET kbn:/api/alerting/rules/_find?per_page=1000` | `elasticstack_kibana_alerting_rule` |
| `GET kbn:/api/actions/connectors` | `elasticstack_kibana_action_connector` |
| `GET kbn:/api/data_views` | `elasticstack_kibana_data_view` |
| `GET kbn:/api/data_views/data_view/<id>` | `elasticstack_kibana_data_view` (met tijdveld en runtime fields) |
| `GET kbn:/api/spaces/space` | `elasticstack_kibana_space` |
| `POST kbn:/api/saved_objects/_export` | `elasticstack_kibana_import_saved_objects` |

Alleen Observability-rules ophalen:

```
GET kbn:/api/alerting/rules/_find?per_page=1000&filter=alert.attributes.consumer:(observability or logs or infrastructure or apm or slo or uptime or alerts)
```

Dashboards met alles waar ze naar verwijzen (aanbevolen):

```
POST kbn:/api/saved_objects/_export
{"objects":[{"type":"dashboard","id":"<id>"}],"includeReferencesDeep":true}

POST kbn:/api/saved_objects/_export
{"type":["dashboard"],"includeReferencesDeep":true}
```

`GET kbn:/api/saved_objects/_find?type=dashboard&per_page=1000` werkt ook, maar bevat geen
gerefereerde objecten (data views, visualisaties). Een `export.ndjson` uit
**Stack Management > Saved Objects** wordt ook herkend.

## Opties

### Algemeen

| Optie | Betekenis |
|-------|-----------|
| `-i`, `--input BESTAND` | Bestand met Dev Tools output. Herhaalbaar. Zonder `-i` wordt stdin gelezen. Type forceren: `-i ilm:policies.json`. |
| `-o`, `--output BESTAND` | Alles in één `.tf`-bestand. |
| `--output-dir MAP` | Eén `.tf`-bestand per type in deze map (aanbevolen). |
| (geen van beide) | Output naar stdout. |
| `--list-types` | Toon de ondersteunde types en stop. |
| `--only TYPE` | Alleen dit type omzetten. Herhaalbaar. |
| `--space-id ID` | Kibana space als die niet uit de call blijkt (default `default`). |
| `--include-managed` | Ook door Elastic/Fleet beheerde en reserved objecten meenemen. |
| `--no-keep-ids` | `rule_id` en `connector_id` niet overnemen; het doel genereert nieuwe. |
| `--with-provider` | Voegt `provider.tf` toe met het `terraform`- en `provider`-block. |
| `--with-moved` | Voegt `moved.tf` toe om een bestaande state van losse blocks (v0.3.x of `--no-for-each`) naar de `for_each`-adressen te verplaatsen. |
| `--no-for-each` | Elk object als los resource-block in plaats van één resource met `for_each` per type (zie [Output](#output)). |
| `--with-imports` | Voegt `imports.tf` toe met `import`-blocks (Terraform ≥ 1.5). |
| `--cluster-uuid UUID` | Cluster UUID (uit `GET /`) voor import-ID's van ES-resources. |
| `-V`, `--version` | Toon de versie. |

### Direct ophalen via de API

| Optie | Betekenis |
|-------|-----------|
| `--fetch [WAT ...]` | JSON ophalen via de Elasticsearch- en Kibana-API. `WAT` is een type, Terraform-resource of Dev Tools-call (zie [Kiezen wat je ophaalt](#kiezen-wat-je-ophaalt)). Herhaalbaar; kan samen met `-i`. |
| `--config BESTAND` | Configbestand (default `./elastic2tf.ini`, daarna `~/.config/elastic2tf.ini`). |
| `--save-export BESTAND` | Opgehaalde responses bewaren in Dev Tools-formaat, te hergebruiken met `-i`. |

### Data views

| Optie | Betekenis |
|-------|-----------|
| `--time-field VELD` | Tijdveld als dat niet bekend is (default `@timestamp`). |
| `--catch-all PATTERN` | Pattern van de overkoepelende data view (default `logs-*`). |
| `--no-catch-all` | Geen overkoepelende data view maken. |
| `--no-override` | Geen `override = true` op data views. Standaard staat `override = true` op alle data views (uit de Kibana API én uit data streams). |

### Selectie

| Optie | Betekenis |
|-------|-----------|
| `--items NAMEN` | Alleen deze objecten omzetten: komma-gescheiden namen, globs mogen (hoofdletterongevoelig, herhaalbaar). `naam` geldt voor alle types, `type:naam` voor één type. Zie [Objecten selecteren](#objecten-selecteren-met---items). |
| `--pipelines NAMEN` | Verouderd; gelijk aan `--items ingest_pipeline:NAMEN`. |

### Saved objects / dashboards

| Optie | Betekenis |
|-------|-----------|
| `--saved-object-types TYPES` | Types voor de saved objects-export bij `--fetch` (komma-gescheiden of herhaalbaar), bijv. `search` (discover sessions / saved searches) of `dashboard,search`. Gaat voor `saved_object_types` in de config; default `dashboard`. Gerefereerde objecten gaan mee. |
| `--title-filter GLOB` | Alleen dashboards waarvan de titel matcht (hoofdletterongevoelig, herhaalbaar), bijv. `'itoa*'`. Gerefereerde objecten gaan mee. |
| `--tag-filter GLOB` | Alleen objecten met een tag waarvan de naam matcht (hoofdletterongevoelig, herhaalbaar), bijv. `'*itoa*'`. Werkt op `--title-filter-type`; samen met `--title-filter` moeten beide matchen. Gerefereerde objecten (data views, tags) gaan mee. |
| `--title-filter-type TYPE` | Type waarop `--title-filter` en `--tag-filter` werken (default `dashboard`; discover sessions: `search`). |

### Alerting rules

| Optie | Betekenis |
|-------|-----------|
| `--all-rules` | Alle rules meenemen, niet alleen Observability (default: alleen Observability). |
| `--disabled` | Rules aanmaken met `enabled = false`. |
| `--no-rule-connectors` | Connectors van de rules niet automatisch ophalen/meenemen (zie [Connectors komen mee met de rules](#connectors-komen-mee-met-de-rules)). |
| `--target-connectors BESTAND` | Output van `GET kbn:/api/actions/connectors` op het **doel**cluster. Connectors worden op ID of naam gekoppeld. |
| `--recreate-preconfigured` | Preconfigured connectors van de bron als gewone connector aanmaken. |
| `--drop-missing-actions` | Actions weglaten waarvan de connector niet beheerd wordt en niet op het doel bestaat. |
| `--raw-connector-ids` | Connector-ID's als letterlijke string invullen in plaats van via `local.connector_ids`. |
| `--report BESTAND` | Rapport per rule wegschrijven. Eindigt het op `.csv`, dan als CSV (`;`-gescheiden, voor Excel); anders als tekst. |
| `--no-flapping` | Flapping-instellingen weglaten (voor Kibana < 8.16). |
| `--no-artifacts` | Gekoppelde dashboards en investigation guide weglaten (voor Stack < 9.1). |

### Voorbeelden

```bash
# Alles direct ophalen via de API en de ruwe data bewaren
./elastic2tf.py --fetch --save-export export.txt --output-dir tf/

# Alleen rules en connectors ophalen, met import-blocks (cluster UUID wordt opgehaald)
./elastic2tf.py --fetch alerting_rule connector --with-imports --output-dir tf/

# Alleen twee ingest pipelines omzetten
./elastic2tf.py --fetch --items ingest_pipeline:logs-app,logs-web -o pipelines.tf

# Eén ILM-policy direct ophalen
./elastic2tf.py --fetch "GET _ilm/policy/mijn-policy" -o ilm.tf

# Alles uit één export, één bestand per type
./elastic2tf.py -i export.txt --output-dir tf/

# Rules en connectors samen, in één bestand
./elastic2tf.py -i rules.json -i connectors.json -o alerting.tf

# Type forceren als het niet herkend wordt
./elastic2tf.py -i ilm:policies.json

# Alleen ILM en index templates, met provider- en import-blocks
./elastic2tf.py -i export.txt --only ilm --only index_template \
  --with-provider --with-imports --cluster-uuid AbCdEf123 --output-dir tf/

# Rules overzetten naar een ander cluster, met controle op connectors en een Excel-rapport
./elastic2tf.py -i rules.json -i connectors.json \
  --target-connectors doel-connectors.json --report rules.csv --output-dir tf/

# Alleen dashboards waarvan de titel met "itoa" begint
./elastic2tf.py -i dashboards.ndjson --title-filter 'itoa*' --output-dir tf/
# discover sessions met een tag waarin ITOA voorkomt (+ hun data views en tags)
./elastic2tf.py --fetch saved_objects --tag-filter '*itoa*' --title-filter-type search -o discover.tf
```

## Output

Met `--output-dir` komt elk type in een eigen bestand:

| Bestand | Inhoud |
|---------|--------|
| `data_views.tf` | data views (uit data streams en uit de Kibana API) |
| `index_templates.tf`, `component_templates.tf` | templates |
| `ilm_policies.tf`, `snapshot_lifecycle.tf` | ILM- en SLM-policies |
| `ingest_pipelines.tf` | ingest pipelines |
| `security_roles.tf`, `security_role_mappings.tf` | rollen en role mappings |
| `cluster_settings.tf` | cluster settings |
| `spaces.tf` | Kibana spaces |
| `connectors.tf` | action connectors |
| `alerting_rules.tf` | alerting rules, eventueel met een `locals`-block `connector_ids` |
| `saved_objects.tf` + `saved_objects.ndjson` | import van saved objects; de ndjson staat ernaast |
| `variables.tf` | variabelen per connector-secret (`conn1_user`, …) + `local.connector_secrets` (alleen als er connectors met secrets zijn) |
| `connector_secrets.tfvars.example` | placeholders voor de connector-secrets (niet bij output naar stdout) |
| `provider.tf` | alleen met `--with-provider` |
| `imports.tf` | alleen met `--with-imports` |
| `moved.tf` | alleen met `--with-moved` |

### for_each per resource-type

Per resource-type maakt het script **één** resource met `for_each` over een `locals`-map. De map
bevat per object alleen wat écht verschilt; de rest staat één keer in de resource:

- velden die bij **alle objecten gelijk** zijn (bijv. `space_id = "default"`, `time_field_name`)
  staan als vaste waarde in de resource;
- velden die **uit de sleutel volgen** worden `each.key` (bijv. `name`), of een variant daarop
  zoals `"${each.key}*"`;
- blijft er per object niets over, dan wordt de map een lijst: `toset([...])`.

Geneste blocks (ILM-fasen, `indices` van een rol, `actions` van een rule, …) worden `dynamic`
blocks. Voorbeeld met data views uit data streams:

```hcl
locals {
  data_views = toset([
    # logs-*: 40 data streams, 11.0tb
    "logs-",
    # logs-alert*: 1 data stream, 34.8mb
    "logs-alert",
    # logs-asset*: 3 data streams, 1.0gb
    "logs-asset",
  ])
}

resource "elasticstack_kibana_data_view" "data_views" {
  for_each = local.data_views

  space_id = "default"

  data_view = {
    id              = each.key
    name            = each.key
    title           = "${each.key}*"
    time_field_name = "@timestamp"
  }
}
```

Een nieuwe data view toevoegen is dan één regel in de lijst. Omdat gelijke waarden naar de
resource gaan, kan de vorm veranderen als je opnieuw genereert met andere objecten; pas dus de
input aan en genereer opnieuw, in plaats van de `.tf`-bestanden met de hand te wijzigen.

- **Sleutels** in de map zijn de naam van het object (ID bij data views en spaces). Bij dubbele
  namen krijgt de volgende ` (2)` erachter. Het Terraform-adres wordt bijvoorbeeld
  `elasticstack_elasticsearch_index_lifecycle.ilm_policies["mijn-policy"]`.
- **Velden die niet bij elk object voorkomen** worden in de resource opgehaald met
  `try(each.value.veld, null)`; zo kan elk object zijn eigen set velden hebben.
- **JSON-velden** (`settings`, `mappings`, `params`, `processors`, …) staan in de map als gewone
  HCL-objecten en worden in de resource met `jsonencode()` omgezet.
- Ook bij één object wordt `for_each` gebruikt, zodat het adres niet verandert als er later
  objecten bijkomen. Bij één object blijven alle velden in de map staan.
- `cluster_settings` blijft één los block (er is maar één cluster).
- Import-blocks en verwijzingen van rules naar connectors gebruiken automatisch de nieuwe adressen.

Met `--no-for-each` krijg je de oude vorm: elk object als los resource-block.

> **Overstappen vanaf v0.3.x of `--no-for-each`:** de adressen in de Terraform-state veranderen
> (van `type.naam` naar `type.label["sleutel"]`). Zonder maatregel ziet `terraform plan` dat als
> verwijderen + opnieuw aanmaken. Genereer daarom met **`--with-moved`**: dat maakt `moved.tf`
> met een `moved`-block per resource, zodat Terraform de bestaande resources in de state
> verplaatst. Controleer met `terraform plan` dat er alleen *moved* staat en geen *destroy*;
> na een geslaagde `apply` mag `moved.tf` weg. Of blijf `--no-for-each` gebruiken.

### Bestanden

Met `-o` komt alles in één bestand, met een scheidingsregel per onderdeel. Het
`saved_objects.ndjson`-bestand wordt dan naast dat bestand gezet. Zonder `-o` en `--output-dir`
gaat alles naar stdout en worden saved objects als heredoc ingesloten.

Elk bestand begint met `# Gegenereerd door <scriptnaam> vX.Y.Z`, met de naam waaronder het script
is uitgevoerd (ook als je het hebt hernoemd). Pas de bestanden niet met de
hand aan, maar genereer ze opnieuw.

Op stderr verschijnt een samenvatting: aantal gemaakte en overgeslagen resources per type, en
waarschuwingen (niet-ondersteunde velden, overlappende patterns, ontbrekende connectors).

## Gedrag per resource-type

### Beheerde objecten

Door Elastic of Fleet beheerde objecten worden standaard overgeslagen. Dat zijn objecten met
`managed: true` of `managed_by: fleet/elastic` in `_meta`, reserved rollen en role mappings,
preconfigured en system connectors, reserved spaces, en namen die met `.` beginnen.
Met `--include-managed` worden ze wel meegenomen.

### Data streams → data views

Data streams worden gegroepeerd op alles vóór de eerste punt:
`logs-system.syslog-default` wordt data view `logs-system` met pattern `logs-system*`.
Een `-default` aan het eind wordt weggelaten. Daarnaast komt er een overkoepelende data view
`logs-` met pattern `logs-*` bij (aan te passen met `--catch-all`, uit te zetten met
`--no-catch-all`). Het commentaar per data view toont het aantal streams en de totale grootte.
Overlappen patterns met streams uit een andere groep, dan volgt een waarschuwing.

### Kibana data views

`GET kbn:/api/data_views` geeft geen tijdveld terug. Het script neemt dan `--time-field` aan en
meldt dat. Gebruik `GET kbn:/api/data_views/data_view/<id>` voor het echte tijdveld, source
filters, runtime fields en field attributes. Field formats worden niet omgezet.

### ILM

Fasen `hot`, `warm`, `cold`, `frozen` en `delete` met de acties die de provider ondersteunt
(rollover, set_priority, forcemerge, shrink, readonly, searchable_snapshot, allocate, migrate,
downsample, unfollow, freeze, delete, wait_for_snapshot). Onbekende acties of velden worden
weggelaten met een waarschuwing.

### Security roles

Cluster-, index-, remote index- en applicatierechten, `run_as`, field security en document-level
queries worden overgenomen. `remote_cluster` wordt niet ondersteund door de provider.

### Cluster settings

`persistent` en `transient` settings worden plat gemaakt naar `setting`-blocks. Controleer goed of
je al deze settings echt via Terraform wilt beheren.

### Saved objects

Saved objects komen in een `elasticstack_kibana_import_saved_objects` resource met
`overwrite = true`. Bij `_find`-output waarschuwt het script als gerefereerde objecten ontbreken.
Gebruik dan liever `_export` met `includeReferencesDeep`.

### Discover sessions op tag

Discover sessions zijn saved objects van het type `search`. Exporteer die (met
`includeReferencesDeep`, zodat data views en tags meekomen) en filter op tagnaam:

```bash
./elastic2tf.py --fetch saved_objects --tag-filter '*itoa*' --title-filter-type search -o discover.tf
```

Alle discover sessions, zonder filter:

```bash
./elastic2tf.py --fetch saved_objects --saved-object-types search --output-dir tf/
```

of in de config:

```ini
[fetch]
saved_object_types = search
tag_filter = *itoa*
filter_type = search
```

Vanuit Dev Tools: `POST kbn:/api/saved_objects/_export {"type":["search"],"includeReferencesDeep":true}`
en het resultaat met `-i` en dezelfde opties. Een `_find`-response bevat de tags zelf niet, dus
daar werkt `--tag-filter` niet.

## Objecten selecteren met `--items`

Standaard worden alle (niet-beheerde) objecten omgezet. Met `--items` kies je welke, voor elk
resource-type:

```bash
# alleen deze ingest pipelines
./elastic2tf.py -i export.txt --items 'ingest_pipeline:logs-app,logs-web*' -o pipelines.tf

# per type iets anders
./elastic2tf.py --fetch --items 'ilm:logs-*,role:lezer,alerting_rule:CPU*' --output-dir tf/

# zonder type: geldt voor alle types (handig als je één type ophaalt)
./elastic2tf.py --fetch ingest_pipeline --items logs-app,logs-web -o pipelines.tf
```

- **`naam`** geldt voor alle types; **`type:naam`** alleen voor dat type. Het type geldt ook
  voor de namen die erna komen, tot het volgende `type:`: `ilm:a,b,role:c,d` betekent ILM `a`
  en `b`, rollen `c` en `d`. Wil je daarna weer namen voor alle types, gebruik dan een aparte
  `--items`. Als type kun je
  hetzelfde gebruiken als bij `--fetch`: het type (`ilm`), de Terraform-resource
  (`elasticstack_elasticsearch_index_lifecycle`) of de korte vorm (`index_lifecycle`).
- Types waarvoor geen `--items` gelden, worden gewoon helemaal omgezet. Met
  `--items ilm:logs-*` worden dus alleen de ILM-policies gefilterd.
- Namen zijn **hoofdletterongevoelig**; `*` en `?` werken als wildcard (zet ze tussen quotes).
- Herhaalbaar: `--items ilm:a,b --items 'role:ops-*'`.
- Een object dat je **exact** noemt, gaat ook mee als het door Elastic of Fleet beheerd wordt
  (bijv. `ingest_pipeline:logs-system@custom`). Via een glob gaan beheerde objecten alleen mee
  met `--include-managed`.
- De samenvatting toont per type hoeveel objecten niet geselecteerd zijn. Voor een naam die
  nergens gevonden wordt, volgt een waarschuwing.
- **Met `--fetch`**: staan er alleen `type:naam`-items en geef je achter `--fetch` niets op, dan
  worden alleen die types opgehaald. De items kunnen ook in de config: `[fetch] items = ...`.

Waarop gematcht wordt:

| Type | Naam |
|------|------|
| `index_template`, `component_template`, `ilm`, `slm`, `ingest_pipeline`, `role`, `role_mapping` | de naam |
| `cluster_settings` | de naam van de setting, bijv. `cluster_settings:cluster.routing.*` |
| `datastream` (data views uit data streams) | naam of pattern van de data view, bijv. `logs-nginx` of `logs-nginx*` |
| `kibana_data_view` / `data_view` | ID, naam of titel (geldt ook voor data views uit data streams) |
| `connector` | naam of ID |
| `alerting_rule` | naam of ID |
| `space` | ID of naam |
| `saved_objects` | titel van het dashboard (zoals `--title-filter`); gerefereerde objecten gaan mee |

## Alerting rules en connectors

### Connectors komen mee met de rules

Rules verwijzen naar connectors. Die connectors komen daarom automatisch mee:

- **Bij `--fetch`**: worden er rules opgehaald (type `alerting_rule` of een call naar
  `.../api/alerting/rules/...`), dan worden de connectors uit dezelfde space ook opgehaald.
- **Bij het omzetten**: een connector waar een omgezette rule naar verwijst, wordt altijd
  meegenomen, ook als `--items` voor connectors hem niet zou selecteren.
- **Filter je de rules met `--items`** en de connectors niet, dan komen alleen de connectors
  mee die de geselecteerde rules gebruiken. De samenvatting toont hoeveel connectors zijn
  weggelaten.
- Een preconfigured connector (uit `kibana.yml`) blijft, net als eerder, weg; gebruik
  `--recreate-preconfigured` om hem toch aan te maken.

```bash
# rule "CPU hoog" plus de connectors die hij gebruikt
./elastic2tf.py --fetch --items 'alerting_rule:CPU hoog' -o cpu.tf
```

Bestaan de connectors al op het doelcluster? Gebruik dan `--no-rule-connectors`: er wordt dan
niets automatisch opgehaald of meegenomen, en de rules verwijzen via `local.connector_ids`
naar de bestaande connectors (zie hieronder, en `--target-connectors`). Connectors die je zelf
in de input zet, worden wel gewoon omgezet. Wil je dat Terraform bestaande connectors
overneemt in plaats van nieuwe aan te maken, gebruik dan `--with-imports`.

### Connector-secrets

Kibana exporteert geen secrets (webhook-URL's, wachtwoorden, tokens). Het script maakt per
connector **losse Terraform-variabelen** per secret-veld, zodat de Terraform-runner (bijv. een
GitLab/GitHub-pipeline) ze uit CI/CD-variabelen kan vullen. Er hoeft niets met secrets in git.

Voor een webhook-connector met basic auth:

```hcl
# variables.tf (gegenereerd)
variable "conn1_user" {
  description = "Webhook ops (.webhook, bron-ID 1111...) — user"
  type        = string
  sensitive   = true
}

variable "conn1_password" { ... }

locals {
  connector_secrets = {
    # Webhook ops (.webhook, bron-ID 1111...)
    "11111111-1111-1111-1111-111111111111" = {
      user     = var.conn1_user
      password = var.conn1_password
    }
  }
}
```

In `connectors.tf` staat per connector
`secrets = try(jsonencode(local.connector_secrets[<bron-connector-ID>]), null)`; in de
`for_each`-map staat alleen het ID (`secrets_id`), want gevoelige waarden mogen niet in `for_each`.

**In de runner** zet je per variabele een (masked/protected) CI/CD-variabele met het voorvoegsel
`TF_VAR_`; Terraform pakt die automatisch op:

```
TF_VAR_conn1_user      = alerts-svc
TF_VAR_conn1_password  = ••••••••
```

Het script toont na afloop alle benodigde `TF_VAR_...`-namen en schrijft (met `-o` of
`--output-dir`) een `connector_secrets.tfvars.example` met **placeholders** naast de output.
Lokaal testen kan door dat bestand te kopiëren naar `connector_secrets.auto.tfvars`, de
placeholders te vervangen en het **niet** in git te zetten (`*.auto.tfvars` in `.gitignore`).

Naamgeving:

- Variabelen heten `<prefix>_<veld>`, met het veld in snake_case: `conn1_user`,
  `conn1_password`, `conn2_webhook_url` (Slack/Teams), `conn3_routing_key` (PagerDuty), enz.
- De prefix is standaard `conn1`, `conn2`, … in de volgorde van de connectors. **Let op:** komt er
  een connector bij of valt er een weg, dan kan de nummering verschuiven. Leg de namen daarom vast
  in de config (`elastic2tf.ini`, ook zonder `--fetch` gelezen), op connectornaam of bron-ID:

  ```ini
  [connector_secrets]
  Webhook ops = conn1
  Slack team  = slack_ops
  11111111-1111-1111-1111-111111111111 = pagerduty_prod
  ```

- `--secret-prefix PREFIX` verandert het voorvoegsel `conn` voor de automatische namen.
- Welke velden nodig zijn volgt uit het type en de config: een webhook zonder auth
  (`hasAuth: false`) krijgt geen variabelen, een webhook met SSL-certificaat krijgt
  `crt`/`key`/`password` of `pfx`/`password`, xMatters zonder basic auth `secretsUrl`.
- Een onbekend connector-type krijgt één variabele `<prefix>_secrets_json` met alle secrets als
  JSON-object (`{"veld":"waarde"}`).
- De variabelen hebben geen default: ontbreekt er een in de runner, dan stopt `plan` met een
  duidelijke melding in plaats van een connector zonder secrets aan te maken.

Oude vorm (t/m v0.8.2): `--connector-secrets-map` maakt weer één variabele
`connector_secrets` (`map(map(string))`, per bron-connector-ID een object met de secret-velden),
te vullen via een `.tfvars` of `TF_VAR_connector_secrets='{"<id>":{"webhookUrl":"https://..."}}'`.

### Overige details

- Standaard worden alleen **Observability**-rules omgezet (types `observability.*`, `logs.*`,
  `metrics.*`, `apm.*`, `slo.*`, uptime, synthetics, ML anomaly en `.es-query` met een
  Observability-consumer). Security-rules (`siem.*`) vallen af. Gebruik `--all-rules` voor alles.
- Zet de connectors in dezelfde run (`-i connectors.json`), dan verwijzen rules direct naar de
  connector-resources. Anders komt er een `locals { connector_ids = { ... } }` in
  `alerting_rules.tf`, met per connector bron-ID → doel-ID en een statusregel in commentaar.
- Met `--target-connectors` zoekt het script de connectors op het doelcluster op, eerst op ID en
  dan op naam, en vult het juiste ID in.
- De provider accepteert alleen UUID's als `connector_id`. Connectors met een ander ID krijgen
  een nieuw ID; rules verwijzen dan via de resource.
- **Secrets** worden niet door Kibana geëxporteerd; zie [Connector-secrets](#connector-secrets).
- Niet overgenomen (provider ondersteunt het niet): mute/snooze, system actions en
  `alerts_filter.query.filters` (alleen `kql` wordt overgenomen).
- Eén defecte rule houdt de rest niet tegen. Na afloop volgt een **rapport** per rule met de
  status: OK, verwerkt met opmerkingen, verwerkt maar apply faalt waarschijnlijk (connector
  ontbreekt op het doel), overgeslagen, of niet verwerkt (fout). Met `--report` gaat dat rapport
  ook naar een bestand.

## Bestaande objecten importeren

Met `--with-imports` maakt het script `import`-blocks, zodat Terraform bestaande objecten
overneemt in plaats van ze opnieuw aan te maken (Terraform ≥ 1.5).

- Kibana-resources (data views, connectors, rules) gebruiken `<space>/<id>`.
- Elasticsearch-resources en spaces hebben de cluster UUID nodig: geef `--cluster-uuid` mee
  (te vinden met `GET /`, veld `cluster_uuid`). Zonder UUID worden voor deze resources geen
  import-blocks gemaakt.

## Problemen oplossen

| Melding | Oplossing |
|---------|-----------|
| `Inloggen op ... mislukt: HTTP 401` | Gebruikersnaam of wachtwoord onjuist. |
| `... niet bereikbaar` | Controleer de `url`. Certificaatfout? Zet `ca_cert` in de config. |
| `Overgeslagen <type>: ... gaf HTTP 403` | De gebruiker mist leesrechten voor dat type. |
| `Onbekend in --fetch: '...'` | Gebruik een type uit `--list-types`, een `elasticstack_...`-resource of een call zoals `"GET _ilm/policy"`. |
| `Configbestand niet gevonden` | Maak `elastic2tf.ini` aan vanuit `elastic2tf.example.ini`, of geef `--config` mee. |
| `Kan JSON niet lezen op regel X, kolom Y` | Kopieer de response via het menu van de response in Console, of zet in Console > Settings *Triple quotes in output* uit en voer de call opnieuw uit. (Triple quotes worden normaal automatisch omgezet.) |
| `Niet ondersteund / niet herkend` | Forceer het type: `-i <type>:bestand` (zie `--list-types`). |
| `Overgeslagen: foutmelding in ...` | De call gaf een fout terug in Dev Tools; voer hem opnieuw uit. |
| `Dit inputbestand bevat Terraform-code` | Het inputbestand is eerder overschreven met `-o`. Exporteer opnieuw. |
| `-o ... is ook een inputbestand` | Kies een ander outputbestand, bijv. `-o alerts.tf`. |
| `total=N maar M in response` | Er zijn meer rules dan in één pagina passen; haal ook `&page=2` op. |
| `CONNECTORS: ... worden niet door deze Terraform aangemaakt` | Neem `GET kbn:/api/actions/connectors` mee in de input, of gebruik `--target-connectors`, `--recreate-preconfigured` of `--drop-missing-actions`. |

Een BOM aan het begin van een bestand (bijv. opgeslagen in Windows Kladblok) wordt automatisch
verwijderd.

## Versie

```bash
./elastic2tf.py --version
```

De versie staat ook bovenaan elk gegenereerd `.tf`-bestand
(`# Gegenereerd door <scriptnaam> vX.Y.Z`) en in de samenvatting op stderr,
zodat je altijd ziet met welke versie output gemaakt is.

Versioning volgt [Semantic Versioning](https://semver.org/):

- **MAJOR** – wijzigingen die bestaande output of CLI-opties breken
- **MINOR** – nieuwe resource-types of opties
- **PATCH** – bugfixes

### Nieuwe versie uitbrengen

1. Pas `__version__` aan in `elastic2tf.py`.
2. Voeg een sectie toe aan `CHANGELOG.md`.
3. Commit en tag: `git tag -a vX.Y.Z -m "vX.Y.Z" && git push --follow-tags`
