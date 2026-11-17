#!/usr/bin/env python3
"""
elastic2tf.py — zet Elasticsearch/Kibana-configuratie (Dev Tools-output of direct via de API)
om naar Terraform-code voor de elastic/elasticstack provider. Het type output wordt automatisch
herkend. Per resource-type komt er één resource met for_each over een locals-map
(--no-for-each: elk object als los block).

SNELSTART
---------
1. Plak in Dev Tools de calls die je nodig hebt (zie lijst hieronder), selecteer
   ze allemaal en voer ze in één keer uit.
2. Kopieer de VOLLEDIGE response-output (inclusief de "# GET ... 200 OK"-regels)
   naar een bestand, bijv. export.txt. Losse JSON-bestanden per call kan ook.
3. ./elastic2tf.py -i export.txt --output-dir terraform/

Of direct via de API (geen kopiëren nodig):
   cp elastic2tf.example.ini elastic2tf.ini    # url + username invullen
   ./elastic2tf.py --fetch --output-dir terraform/
   Het wachtwoord wordt in de terminal gevraagd (niet zichtbaar, niet opgeslagen).

ONDERSTEUNDE DEV TOOLS CALLS
----------------------------
Elasticsearch
  GET _data_stream/logs-*/_stats?human        -> elasticstack_kibana_data_view (gegroepeerd, zie onder)
  GET _index_template                         -> elasticstack_elasticsearch_index_template
  GET _component_template                     -> elasticstack_elasticsearch_component_template
  GET _ilm/policy                             -> elasticstack_elasticsearch_index_lifecycle
  GET _ingest/pipeline                        -> elasticstack_elasticsearch_ingest_pipeline
  GET _slm/policy                             -> elasticstack_elasticsearch_snapshot_lifecycle
  GET _security/role                          -> elasticstack_elasticsearch_security_role
  GET _security/role_mapping                  -> elasticstack_elasticsearch_security_role_mapping
  GET _cluster/settings?flat_settings=true    -> elasticstack_elasticsearch_cluster_settings

Kibana (prefix kbn:, voor een andere space: kbn:/s/<space-id>/api/...)
  GET kbn:/api/alerting/rules/_find?per_page=1000&filter=alert.attributes.consumer:(observability or logs or infrastructure or apm or slo or uptime or alerts)
                                              -> elasticstack_kibana_alerting_rule
  GET kbn:/api/actions/connectors             -> elasticstack_kibana_action_connector
  GET kbn:/api/data_views                     -> elasticstack_kibana_data_view
  GET kbn:/api/data_views/data_view/<id>      -> elasticstack_kibana_data_view (met tijdveld, runtime fields)
  GET kbn:/api/spaces/space                   -> elasticstack_kibana_space
  POST kbn:/api/saved_objects/_export {"objects":[{"type":"dashboard","id":"<id>"}],"includeReferencesDeep":true}
  POST kbn:/api/saved_objects/_export {"type":["dashboard"],"includeReferencesDeep":true}   (alle dashboards)
  GET  kbn:/api/saved_objects/_find?type=dashboard&per_page=1000   (zonder gerefereerde objecten!)
  of een export.ndjson uit Stack Management > Saved Objects
                                              -> elasticstack_kibana_import_saved_objects

Gedrag
  - Door Elastic/Fleet beheerde objecten (managed, reserved, preconfigured,
    namen die met "." beginnen) worden standaard overgeslagen; --include-managed
    neemt ze wel mee.
  - Data streams worden gegroepeerd op alles vóór de eerste punt
    (logs-system.syslog-default -> data view "logs-system", pattern "logs-system*");
    "-default" aan het eind wordt weggelaten, en er komt een overkoepelende
    data view "logs-" met pattern "logs-*" bij (--catch-all / --no-catch-all).
  - Connector-secrets worden niet geëxporteerd door Kibana; ze komen uit
    var.connector_secrets.
  - Alerting rules verwijzen automatisch naar de connector-resources als de
    connectors in dezelfde run zitten; anders via een locals-map om te mappen.
  - Met --cluster-uuid worden ook import-blocks voor ES-resources gemaakt
    (--with-imports), Kibana-resources hebben die niet nodig.

Voorbeelden
  ./elastic2tf.py -i export.txt --output-dir tf/
  ./elastic2tf.py -i rules.json -i connectors.json -o alerting.tf
  ./elastic2tf.py -i ilm:policies.json            # type forceren (type:pad)
  ./elastic2tf.py --fetch --output-dir tf/ --save-export export.txt
  ./elastic2tf.py --fetch ilm index_template -o es.tf
  ./elastic2tf.py --fetch elasticstack_kibana_alerting_rule elasticstack_kibana_action_connector -o alerting.tf
  ./elastic2tf.py --fetch "GET _ilm/policy/mijn-policy" "GET kbn:/s/ops/api/actions/connectors" -o x.tf
  ./elastic2tf.py --fetch --items 'ingest_pipeline:logs-app,logs-web*' -o pipelines.tf
  ./elastic2tf.py -i export.txt --items 'ilm:logs-*,role:lezer,alerting_rule:CPU*' --output-dir tf/
  ./elastic2tf.py --list-types
"""

__version__ = "0.7.0"

import argparse
import fnmatch
import json
import os
import re
import sys
from collections import defaultdict

# naam van het script zoals het wordt uitgevoerd (ook na hernoemen of via een symlink)
SCRIPT_NAME = os.path.basename(sys.argv[0]) if sys.argv and sys.argv[0] not in ("", "-c") else "elastic2tf.py"

# =========================================================================== HCL helpers

IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
HCL_KEYWORDS = {"true", "false", "null", "for", "in", "if", "endif", "else", "endfor"}


def hcl_str(value) -> str:
    return json.dumps(str(value), ensure_ascii=False).replace("${", "$${").replace("%{", "%%{")


def hcl_key(key: str) -> str:
    return key if IDENT_RE.match(key) and key not in HCL_KEYWORDS else hcl_str(key)


class Raw(str):
    """Een kant-en-klare HCL-expressie (bijv. var.x of file(...)); wordt letterlijk overgenomen."""


def hcl_value(v, indent: int = 0) -> str:
    """JSON-waarde als leesbare HCL-expressie (voor jsonencode() en lijsten)."""
    pad, pad_in = "  " * indent, "  " * (indent + 1)
    if isinstance(v, Raw):
        return str(v)
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return json.dumps(v)
    if isinstance(v, str):
        return hcl_str(v)
    if isinstance(v, list):
        if not v:
            return "[]"
        if all(not isinstance(x, (dict, list)) for x in v):
            one = "[" + ", ".join(hcl_value(x) for x in v) + "]"
            if len(one) <= 100:
                return one
        return "[\n" + "\n".join(f"{pad_in}{hcl_value(x, indent + 1)}," for x in v) + f"\n{pad}]"
    if isinstance(v, dict):
        if not v:
            return "{}"
        keys = [hcl_key(k) for k in v]
        w = max(len(k) for k in keys)
        body = "\n".join(f"{pad_in}{k.ljust(w)} = {hcl_value(x, indent + 1)}"
                         for k, x in zip(keys, v.values()))
        return "{\n" + body + f"\n{pad}}}"
    return hcl_str(v)


def jsonenc(v, indent: int) -> str:
    return f"jsonencode({hcl_value(v, indent)})"


def tf_name(name: str, prefix: str = "r") -> str:
    n = re.sub(r"[^a-z0-9_]", "_", str(name).lower())
    n = re.sub(r"_+", "_", n).strip("_")
    if not n or n[0].isdigit():
        n = f"{prefix}_{n}".strip("_")
    return n[:64]


class Block:
    """Kleine HCL-builder: attributen worden uitgelijnd, blocks ingesprongen."""

    def __init__(self, header: str, indent: int = 0):
        self.header, self.indent, self.items = header, indent, []
        self.key = None  # sleutel in de for_each-map (anders: name-attribuut of resourcenaam)

    def attr(self, key, value, *, raw=False, skip_none=True):
        if value is None and skip_none:
            return self
        meta = ("raw", Raw(value)) if raw else ("val", value)
        rendered = value if raw else hcl_value(value, self.indent + 1)
        self.items.append(("attr", key, rendered, meta))
        return self

    def json(self, key, value, *, skip_empty=True):
        if value is None or (skip_empty and value in ({}, [])):
            return self
        self.items.append(("attr", key, jsonenc(value, self.indent + 1), ("json", value)))
        return self

    def jsonlist(self, key, values, *, skip_empty=False):
        """Lijst van objecten die elk met jsonencode() een string worden (bijv. processors)."""
        if values is None or (skip_empty and not values):
            return self
        pad = "  " * (self.indent + 2)
        rendered = ("[\n" + "\n".join(f"{pad}{jsonenc(x, self.indent + 2)}," for x in values)
                    + "\n" + "  " * (self.indent + 1) + "]") if values else "[]"
        self.items.append(("attr", key, rendered, ("jsonlist", list(values))))
        return self

    def block(self, header: str) -> "Block":
        b = Block(header, self.indent + 1)
        self.items.append(("block", b, None, None))
        return b

    def blank(self):
        self.items.append(("blank", None, None, None))
        return self

    def render(self) -> str:
        pad, pad_in = "  " * self.indent, "  " * (self.indent + 1)
        if not self.items:
            return f"{pad}{self.header} {{}}"
        out, run = [f"{pad}{self.header} {{"], []

        def flush():
            if run:
                w = max(len(k) for k, _ in run if "\n" not in _.split("\n")[0] or True)
                for k, v in run:
                    out.append(f"{pad_in}{k.ljust(w)} = {v}")
                run.clear()

        for kind, a, b, _ in self.items:
            if kind == "attr":
                if "\n" in b:          # multi-line waarde: eigen groep
                    flush()
                    if len(out) > 1 and out[-1] != "":
                        out.append("")
                    out.append(f"{pad_in}{a} = {b}")
                else:
                    run.append((a, b))
            elif kind == "block":
                flush()
                if len(out) > 1 and out[-1] != "":
                    out.append("")
                out.append(a.render())
            else:
                flush()
                out.append("")
        flush()
        # dubbele/trailing lege regels opruimen
        cleaned = []
        for line in out:
            if line == "" and cleaned and cleaned[-1] == "":
                continue
            cleaned.append(line)
        if cleaned[-1] == "":
            cleaned.pop()
        cleaned.append(f"{pad}}}")
        return "\n".join(cleaned)


# =========================================================================== input parsing

METHOD_COMMENT = re.compile(r"^#\s*(GET|POST|PUT|DELETE|HEAD|PATCH)\s+(\S+)", re.I)


TRIPLE_QUOTE_RE = re.compile(r'"""(.*?)"""', re.S)


def convert_triple_quotes(text: str) -> str:
    """Kibana Console toont strings met regeleinden als \"\"\"...\"\"\" (instelling
    'Triple quotes in output'). Zet die om naar geldige JSON-strings."""
    return TRIPLE_QUOTE_RE.sub(lambda m: json.dumps(m.group(1), ensure_ascii=False), text)


def json_error_context(text: str, pos: int, msg: str) -> str:
    line = text.count("\n", 0, pos) + 1
    col = pos - (text.rfind("\n", 0, pos) + 1) + 1
    src_line = text.splitlines()[line - 1] if text.splitlines() else ""
    start = max(0, col - 60)
    snippet = src_line[start:col + 40]
    marker = " " * (col - 1 - start) + "^"
    return (f"Kan JSON niet lezen op regel {line}, kolom {col}: {msg}\n"
            f"  {snippet}\n  {marker}\n"
            f"Tip: kopieer de response via het menu van de response in Console, of zet in "
            f"Console > Settings 'Triple quotes in output' uit en voer de call opnieuw uit.")


def parse_console_text(text: str):
    """Lees één of meer JSON-documenten, eventueel met Console "# GET ..." regels
    ertussen. Geeft [(hint_pad, doc), ...]."""
    text = text.lstrip("\ufeff")  # BOM (bijv. bestand opgeslagen in Windows/Notepad)
    first = next((l.strip() for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")), "")
    if re.match(r'^(locals|resource|terraform|variable|import|provider)\s*("|\{)', first):
        sys.exit("Dit inputbestand bevat Terraform-code, geen Dev Tools JSON-output. "
                 "Waarschijnlijk is het eerder overschreven met -o. Exporteer opnieuw vanuit Dev Tools.")
    if '"""' in text:
        text = convert_triple_quotes(text)
    dec = json.JSONDecoder()
    docs, hint, i, n = [], None, 0, len(text)
    while i < n:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            break
        if text[i] == "#":
            end = text.find("\n", i)
            end = n if end == -1 else end
            m = METHOD_COMMENT.match(text[i:end])
            if m:
                hint = m.group(2)
            i = end
            continue
        try:
            doc, j = dec.raw_decode(text, i)
        except json.JSONDecodeError as e:
            sys.exit(json_error_context(text, e.pos, e.msg))
        docs.append((hint, doc))
        i = j
    return group_ndjson(docs)


def group_ndjson(docs):
    """Opeenvolgende saved objects (ndjson export) samenvoegen tot één document."""
    out, buf, buf_hint = [], [], None

    def is_so(d):
        return isinstance(d, dict) and (
            ("type" in d and "id" in d and "attributes" in d) or "exportedCount" in d)

    for hint, d in docs:
        if is_so(d):
            if not buf:
                buf_hint = hint
            buf.append(d)
            continue
        if buf:
            out.append((buf_hint, {"__saved_objects__": buf}))
            buf = []
        out.append((hint, d))
    if buf:
        out.append((buf_hint, {"__saved_objects__": buf}))
    return out


def space_from_hint(hint):
    m = re.search(r"(?:kbn:)?/?s/([^/]+)/api/", hint or "")
    return m.group(1) if m else None


HINT_MAP = [  # volgorde is belangrijk
    ("_data_stream", "datastream"),
    ("_index_template", "index_template"),
    ("_component_template", "component_template"),
    ("_ilm/policy", "ilm"),
    ("_slm/policy", "slm"),
    ("_ingest/pipeline", "ingest_pipeline"),
    ("_security/role_mapping", "role_mapping"),
    ("_security/role", "role"),
    ("_cluster/settings", "cluster_settings"),
    ("api/alerting/rules", "alerting_rule"),
    ("api/alerting/rule/", "alerting_rule"),
    ("api/actions/connector", "connector"),
    ("api/data_views", "kibana_data_view"),
    ("api/spaces/space", "space"),
    ("api/saved_objects", "saved_objects"),
    ("api/kibana/dashboards", "saved_objects"),
]


def detect_type(hint, doc):
    if isinstance(doc, dict) and "error" in doc and "status" in doc:
        return "error"
    if hint:
        h = hint.lower()
        for needle, t in HINT_MAP:
            if needle in h:
                return t
    if isinstance(doc, dict):
        if "__saved_objects__" in doc:
            return "saved_objects"
        for key in ("saved_objects", "objects"):
            lst = doc.get(key)
            if isinstance(lst, list) and lst and isinstance(lst[0], dict) and "attributes" in lst[0]:
                return "saved_objects"
        if isinstance(doc.get("data_streams"), list):
            return "datastream"
        if "index_templates" in doc:
            return "index_template"
        if "component_templates" in doc:
            return "component_template"
        if isinstance(doc.get("data"), list) and doc["data"] and "rule_type_id" in doc["data"][0]:
            return "alerting_rule"
        if "rule_type_id" in doc:
            return "alerting_rule"
        if "data_view" in doc:
            return "kibana_data_view"
        if set(doc) & {"persistent", "transient"}:
            return "cluster_settings"
        vals = [v for v in doc.values() if isinstance(v, dict)]
        if vals and len(vals) == len(doc):
            v = vals[0]
            pol = v.get("policy") if isinstance(v.get("policy"), dict) else {}
            if "phases" in pol:
                return "ilm"
            if "schedule" in pol and "repository" in pol:
                return "slm"
            if "processors" in v:
                return "ingest_pipeline"
            if "rules" in v and ("roles" in v or "role_templates" in v):
                return "role_mapping"
            if set(v) & {"cluster", "indices", "applications", "run_as"}:
                return "role"
    if isinstance(doc, list) and doc and isinstance(doc[0], dict):
        if "connector_type_id" in doc[0]:
            return "connector"
        if "disabledFeatures" in doc[0]:
            return "space"
    return None


# =========================================================================== context

class Ctx:
    def __init__(self, args):
        self.args = args
        self.blocks = defaultdict(list)      # bestandsnaam -> [(Block, comment)]
        self.imports = []
        self.extra_files = {}                # bestandsnaam -> inhoud
        self.names = defaultdict(set)        # resource type -> gebruikte namen
        self.warnings = []
        self.stats = defaultdict(lambda: [0, 0])  # type -> [gemaakt, overgeslagen]
        self.connector_addr = {}             # connector-id -> tf address
        self.connector_refs = {}             # connector-id -> info (voor locals)
        self.need_secrets_var = False
        self.item_patterns = parse_items(args)       # type ("*" = alle types) -> [patterns]
        self.item_matched, self.item_unselected = set(), defaultdict(int)
        self.rule_connectors = set()   # connector-ID's waar de geselecteerde rules naar verwijzen
        self.rules_filtered = False    # rules gefilterd met --items -> alleen gebruikte connectors
        self.conn_unused = 0
        self.source_connectors = {}          # id -> {name, type, preconfigured}
        self.target_connectors = None        # {"ids": set, "names": {name: id}}
        self.dropped_actions = []
        self.rule_report = []                # per rule: status, name, id, type, reasons, connectors

    def unique(self, rtype, name, prefix="r"):
        base = tf_name(name, prefix)
        n, i = base, 2
        while n in self.names[rtype]:
            n, i = f"{base}_{i}", i + 1
        self.names[rtype].add(n)
        return n

    def add(self, file, kind, block: Block, comment=None):
        self.blocks[file].append((block, comment))
        self.stats[kind][0] += 1

    def skip(self, kind, n=1):
        self.stats[kind][1] += n

    def imp(self, address, import_id):
        if self.args.with_imports:
            self.imports.append(f"import {{\n  to = {address}\n  id = {hcl_str(import_id)}\n}}\n")

    def es_import(self, address, name):
        if self.args.with_imports and self.args.cluster_uuid:
            self.imp(address, f"{self.args.cluster_uuid}/{name}")

    def warn(self, msg):
        self.warnings.append(msg)

    def patterns(self, kind):
        """--items-patronen die voor dit type gelden (algemeen + specifiek)."""
        pats = self.item_patterns.get("*", []) + self.item_patterns.get(kind, [])
        if kind == "datastream":  # data views uit data streams vallen ook onder data_view:
            pats += self.item_patterns.get("kibana_data_view", [])
        return pats

    def matches(self, kind, *names):
        """--items-patronen die dit object raken ([] = geen; None = geen patronen voor dit type)."""
        pats = self.patterns(kind)
        if not pats:
            return None
        low = [str(n).lower() for n in names if n]
        return [p for p in pats if any(fnmatch.fnmatchcase(n, p.lower()) for n in low)]

    def select(self, kind, *names):
        """(geselecteerd, expliciet genoemd) volgens --items. Zonder patronen: alles, niet expliciet."""
        hits = self.matches(kind, *names)
        if hits is None:
            return True, False
        low = [str(n).lower() for n in names if n]
        if not hits:
            self.item_unselected[kind] += 1
            return False, False
        self.item_matched.update(hits)
        return True, any(p.lower() in low for p in hits)



def is_managed(name: str, meta) -> bool:
    meta = meta or {}
    return (name.startswith(".") or meta.get("managed") is True
            or meta.get("managed_by") in ("fleet", "ingest-manager", "elastic") )


# =========================================================================== converters (ES)

def conv_index_template(doc, ctx, space):
    for item in doc.get("index_templates", []):
        name, t = item["name"], item["index_template"]
        sel, explicit = ctx.select("index_template", name)
        if not sel:
            continue
        if not ctx.args.include_managed and not explicit and is_managed(name, t.get("_meta")):
            ctx.skip("index_template"); continue
        rn = ctx.unique("it", name)
        b = Block(f'resource "elasticstack_elasticsearch_index_template" "{rn}"')
        b.attr("name", name)
        b.attr("index_patterns", sorted(t.get("index_patterns", [])))
        if t.get("composed_of"):
            b.attr("composed_of", t["composed_of"])
        if t.get("ignore_missing_component_templates"):
            b.attr("ignore_missing_component_templates", t["ignore_missing_component_templates"])
        b.attr("priority", t.get("priority"))
        b.attr("version", t.get("version"))
        b.attr("allow_auto_create", t.get("allow_auto_create"))
        b.json("metadata", t.get("_meta"))
        if "data_stream" in t:
            ds = b.block("data_stream")
            ds.attr("hidden", t["data_stream"].get("hidden"))
            ds.attr("allow_custom_routing", t["data_stream"].get("allow_custom_routing"))
        render_template_block(b, t.get("template"))
        ctx.add("index_templates.tf", "index_template", b)
        ctx.es_import(f"elasticstack_elasticsearch_index_template.{rn}", name)


def render_template_block(parent: Block, tpl):
    if not tpl:
        return
    tb = parent.block("template")
    for alias, cfg in sorted((tpl.get("aliases") or {}).items()):
        ab = tb.block("alias")
        ab.attr("name", alias)
        if cfg.get("filter"):
            ab.json("filter", cfg["filter"])
        for k in ("index_routing", "routing", "search_routing", "is_hidden", "is_write_index"):
            ab.attr(k, cfg.get(k))
    lc = tpl.get("lifecycle") or {}
    if lc.get("data_retention"):
        tb.block("lifecycle").attr("data_retention", lc["data_retention"])
    fs = ((tpl.get("data_stream_options") or {}).get("failure_store")) or {}
    if fs:
        fsb = tb.block("data_stream_options").block("failure_store")
        fsb.attr("enabled", fs.get("enabled"))
        if (fs.get("lifecycle") or {}).get("data_retention"):
            fsb.block("lifecycle").attr("data_retention", fs["lifecycle"]["data_retention"])
    tb.json("settings", tpl.get("settings"))
    tb.json("mappings", tpl.get("mappings"))


def conv_component_template(doc, ctx, space):
    for item in doc.get("component_templates", []):
        name, t = item["name"], item["component_template"]
        sel, explicit = ctx.select("component_template", name)
        if not sel:
            continue
        if not ctx.args.include_managed and not explicit and is_managed(name, t.get("_meta")):
            ctx.skip("component_template"); continue
        rn = ctx.unique("ct", name)
        b = Block(f'resource "elasticstack_elasticsearch_component_template" "{rn}"')
        b.attr("name", name)
        b.attr("version", t.get("version"))
        b.json("metadata", t.get("_meta"))
        render_template_block(b, t.get("template"))
        ctx.add("component_templates.tf", "component_template", b)
        ctx.es_import(f"elasticstack_elasticsearch_component_template.{rn}", name)


ILM_ACTIONS = {
    "rollover": ["max_age", "max_docs", "max_size", "max_primary_shard_size", "max_primary_shard_docs",
                 "min_size", "min_docs", "min_age", "min_primary_shard_size", "min_primary_shard_docs"],
    "set_priority": ["priority"],
    "forcemerge": ["max_num_segments", "index_codec"],
    "shrink": ["number_of_shards", "max_primary_shard_size", "allow_write_after_shrink"],
    "readonly": [],
    "searchable_snapshot": ["snapshot_repository", "force_merge_index"],
    "allocate": ["number_of_replicas", "total_shards_per_node"],
    "migrate": ["enabled"],
    "downsample": ["fixed_interval", "wait_timeout"],
    "unfollow": [],
    "freeze": [],
    "delete": ["delete_searchable_snapshot"],
    "wait_for_snapshot": ["policy"],
}


def conv_ilm(doc, ctx, space):
    for name, body in sorted(doc.items()):
        pol = body.get("policy", {})
        sel, explicit = ctx.select("ilm", name)
        if not sel:
            continue
        if not ctx.args.include_managed and not explicit and is_managed(name, pol.get("_meta")):
            ctx.skip("ilm"); continue
        rn = ctx.unique("ilm", name)
        b = Block(f'resource "elasticstack_elasticsearch_index_lifecycle" "{rn}"')
        b.attr("name", name)
        b.json("metadata", pol.get("_meta"))
        for phase in ("hot", "warm", "cold", "frozen", "delete"):
            p = (pol.get("phases") or {}).get(phase)
            if p is None:
                continue
            pb = b.block(phase)
            pb.attr("min_age", p.get("min_age"))
            for action, cfg in (p.get("actions") or {}).items():
                if action not in ILM_ACTIONS:
                    ctx.warn(f"ILM {name}: actie '{action}' in fase {phase} niet ondersteund, weggelaten")
                    continue
                cfg = cfg or {}
                ab = pb.block(action)
                for k in ILM_ACTIONS[action]:
                    ab.attr(k, cfg.get(k))
                if action == "allocate":
                    for k in ("include", "exclude", "require"):
                        ab.json(k, cfg.get(k))
                unknown = set(cfg) - set(ILM_ACTIONS[action]) - {"include", "exclude", "require"}
                if unknown:
                    ctx.warn(f"ILM {name}: {action}.{', '.join(sorted(unknown))} niet ondersteund, weggelaten")
        ctx.add("ilm_policies.tf", "ilm", b)
        ctx.es_import(f"elasticstack_elasticsearch_index_lifecycle.{rn}", name)


def conv_slm(doc, ctx, space):
    for name, body in sorted(doc.items()):
        if not ctx.select("slm", name)[0]:
            continue
        pol = body.get("policy", {})
        cfg, ret = pol.get("config") or {}, pol.get("retention") or {}
        rn = ctx.unique("slm", name)
        b = Block(f'resource "elasticstack_elasticsearch_snapshot_lifecycle" "{rn}"')
        b.attr("name", name)
        b.attr("snapshot_name", pol.get("name"))
        b.attr("schedule", pol.get("schedule"))
        b.attr("repository", pol.get("repository"))
        idx = cfg.get("indices")
        if idx is not None:
            b.attr("indices", idx if isinstance(idx, list) else [idx])
        ew = cfg.get("expand_wildcards")
        if ew is not None:
            b.attr("expand_wildcards", ",".join(ew) if isinstance(ew, list) else ew)
        for k in ("ignore_unavailable", "include_global_state", "partial"):
            b.attr(k, cfg.get(k))
        if cfg.get("feature_states"):
            b.attr("feature_states", cfg["feature_states"])
        b.attr("expire_after", ret.get("expire_after"))
        b.attr("min_count", ret.get("min_count"))
        b.attr("max_count", ret.get("max_count"))
        b.json("metadata", cfg.get("metadata"))
        ctx.add("snapshot_lifecycle.tf", "slm", b)
        ctx.es_import(f"elasticstack_elasticsearch_snapshot_lifecycle.{rn}", name)


def name_filter(values):
    """--items a,b --items 'c*' -> ['a', 'b', 'c*'] (komma's en herhalen mag)."""
    return [x.strip() for v in values or [] for x in v.split(",") if x.strip()]


def parse_items(args):
    """--items (en het oude --pipelines) -> {type of '*': [patronen]}.
    'type:patroon' geldt alleen voor dat type (type, Terraform-resource of alias zoals bij --fetch)."""
    aliases, out = spec_aliases(), defaultdict(list)
    for value in getattr(args, "items", None) or []:
        current = "*"  # een type geldt ook voor de namen erna, tot het volgende type:
        for item in name_filter([value]):
            pre, sep, rest = item.partition(":")
            t = aliases.get(pre.strip().lower()) if sep else None
            if t and rest.strip():
                current, item = t, rest.strip()
            out[current].append(item)
    for item in name_filter(getattr(args, "pipelines", None)):
        out["ingest_pipeline"].append(item)
    return dict(out)


def conv_ingest_pipeline(doc, ctx, space):
    for name, p in sorted(doc.items()):
        sel, explicit = ctx.select("ingest_pipeline", name)  # expliciet genoemd: ook als beheerd
        if not sel:
            continue
        if not ctx.args.include_managed and not explicit and is_managed(name, p.get("_meta")):
            ctx.skip("ingest_pipeline"); continue
        rn = ctx.unique("pl", name)
        b = Block(f'resource "elasticstack_elasticsearch_ingest_pipeline" "{rn}"')
        b.attr("name", name)
        b.attr("description", p.get("description"))
        b.json("metadata", p.get("_meta"))
        b.jsonlist("processors", p.get("processors") or [])
        b.jsonlist("on_failure", p.get("on_failure"), skip_empty=True)
        ctx.add("ingest_pipelines.tf", "ingest_pipeline", b)
        ctx.es_import(f"elasticstack_elasticsearch_ingest_pipeline.{rn}", name)


def render_index_priv(parent: Block, header: str, ip: dict, remote=False):
    ib = parent.block(header)
    if remote:
        ib.attr("clusters", ip.get("clusters", []))
    ib.attr("names", ip.get("names", []))
    ib.attr("privileges", ip.get("privileges", []))
    ib.attr("allow_restricted_indices", ip.get("allow_restricted_indices"))
    q = ip.get("query")
    if q:
        if isinstance(q, str):
            ib.attr("query", q)
        else:
            ib.json("query", q)
    fs = ip.get("field_security")
    if fs:
        fb = ib.block("field_security")
        if fs.get("grant") is not None:
            fb.attr("grant", fs["grant"])
        if fs.get("except"):
            fb.attr("except", fs["except"])


def conv_role(doc, ctx, space):
    for name, r in sorted(doc.items()):
        meta = r.get("metadata") or {}
        sel, explicit = ctx.select("role", name)
        if not sel:
            continue
        if not ctx.args.include_managed and not explicit and (meta.get("_reserved") or meta.get("_deprecated")):
            ctx.skip("role"); continue
        rn = ctx.unique("role", name)
        b = Block(f'resource "elasticstack_elasticsearch_security_role" "{rn}"')
        b.attr("name", name)
        b.attr("description", r.get("description") or None)
        if r.get("cluster"):
            b.attr("cluster", sorted(r["cluster"]))
        if r.get("run_as"):
            b.attr("run_as", sorted(r["run_as"]))
        b.json("metadata", meta)
        if r.get("global"):
            b.json("global", r["global"])
        for ip in r.get("indices") or []:
            render_index_priv(b, "indices", ip)
        for ip in r.get("remote_indices") or []:
            render_index_priv(b, "remote_indices", ip, remote=True)
        for ap in r.get("applications") or []:
            ab = b.block("applications")
            ab.attr("application", ap.get("application"))
            ab.attr("privileges", ap.get("privileges", []))
            ab.attr("resources", ap.get("resources", []))
        if r.get("remote_cluster"):
            ctx.warn(f"Rol {name}: remote_cluster wordt niet ondersteund, weggelaten")
        ctx.add("security_roles.tf", "role", b)
        ctx.es_import(f"elasticstack_elasticsearch_security_role.{rn}", name)


def conv_role_mapping(doc, ctx, space):
    for name, m in sorted(doc.items()):
        meta = m.get("metadata") or {}
        sel, explicit = ctx.select("role_mapping", name)
        if not sel:
            continue
        if not ctx.args.include_managed and not explicit and meta.get("_reserved"):
            ctx.skip("role_mapping"); continue
        rn = ctx.unique("rm", name)
        b = Block(f'resource "elasticstack_elasticsearch_security_role_mapping" "{rn}"')
        b.attr("name", name)
        b.attr("enabled", m.get("enabled", True))
        if m.get("roles"):
            b.attr("roles", sorted(m["roles"]))
        b.json("role_templates", m.get("role_templates"))
        b.json("rules", m.get("rules") or {}, skip_empty=False)
        b.json("metadata", meta)
        ctx.add("security_role_mappings.tf", "role_mapping", b)
        ctx.es_import(f"elasticstack_elasticsearch_security_role_mapping.{rn}", name)


def flatten(prefix, v, out):
    if isinstance(v, dict):
        for k, x in v.items():
            flatten(f"{prefix}.{k}" if prefix else k, x, out)
    else:
        out[prefix] = v


def conv_cluster_settings(doc, ctx, space):
    b = Block('resource "elasticstack_elasticsearch_cluster_settings" "cluster"')
    total = 0
    for scope in ("persistent", "transient"):
        flat = {}
        flatten("", doc.get(scope) or {}, flat)
        flat = {k: v for k, v in flat.items() if ctx.select("cluster_settings", k)[0]}
        if not flat:
            continue
        sb = b.block(scope)
        for k in sorted(flat):
            v = flat[k]
            st = sb.block("setting")
            st.attr("name", k)
            if isinstance(v, list):
                st.attr("value_list", [str(x) for x in v])
            else:
                st.attr("value", str(v).lower() if isinstance(v, bool) else str(v))
            total += 1
    if not total:
        ctx.warn("Cluster settings: geen persistent/transient settings gevonden")
        return
    ctx.add("cluster_settings.tf", "cluster_settings", b,
            comment=f"{total} cluster settings — controleer of je deze echt via Terraform wilt beheren")
    if ctx.args.cluster_uuid:
        ctx.es_import("elasticstack_elasticsearch_cluster_settings.cluster",
                      "cluster-settings")


def conv_datastream(doc, ctx, space):
    args = ctx.args
    streams = []
    for s in doc.get("data_streams", []):
        name = s.get("data_stream") or s.get("name")
        if not name:
            continue
        if not args.include_managed and (name.startswith(".") or s.get("system") or s.get("hidden")):
            ctx.skip("datastream_data_view"); continue
        streams.append({"name": name, "bytes": s.get("store_size_bytes")})
    groups = {}
    for s in streams:
        prefix = s["name"].split(".", 1)[0]
        if prefix.endswith("-default") and "-" in prefix[: -len("-default")]:
            prefix = prefix[: -len("-default")]
        g = groups.setdefault(prefix, {"name": prefix, "pattern": prefix + "*", "members": [], "bytes": 0})
        g["members"].append(s["name"])
        g["bytes"] += s["bytes"] or 0
    glist = sorted(groups.values(), key=lambda g: g["name"])
    all_names = [s["name"] for s in streams]
    for g in glist:
        extra = [x for x in all_names if fnmatch.fnmatchcase(x, g["pattern"]) and x not in g["members"]]
        if extra:
            ctx.warn(f"Data view pattern {g['pattern']} matcht ook: {', '.join(extra)}")
    if not args.no_catch_all:
        cname = args.catch_all.rstrip("*")
        if not any(g["name"] == cname for g in glist):
            members = [x for x in all_names if fnmatch.fnmatchcase(x, args.catch_all)]
            glist.insert(0, {"name": cname, "pattern": args.catch_all, "members": members,
                             "bytes": sum(s["bytes"] or 0 for s in streams if s["name"] in members)})
    sp = space or args.space_id
    for g in glist:
        if not ctx.select("datastream", g["name"], g["pattern"])[0]:
            continue
        if ctx.names["dv_ids"] & {g["name"]}:
            continue  # al gemaakt vanuit een andere input
        ctx.names["dv_ids"].add(g["name"])
        ctx.names["dv_titles"].add(g["pattern"])
        rn = ctx.unique("dv", g["name"], "dv")
        b = Block(f'resource "elasticstack_kibana_data_view" "{rn}"')
        b.attr("space_id", sp)
        b.key = g["name"]
        b.attr("data_view", {"id": g["name"], "name": g["name"], "title": g["pattern"],
                             "time_field_name": args.time_field})
        n = len(g["members"])
        ctx.add("data_views.tf", "datastream_data_view", b,
                comment=f"{g['pattern']}: {n} data stream{'s' if n != 1 else ''}, {human(g['bytes'])}")
        ctx.imp(f"elasticstack_kibana_data_view.{rn}", f"{sp}/{g['name']}")


def human(b):
    s = float(b or 0)
    for u in ("b", "kb", "mb", "gb", "tb"):
        if s < 1024 or u == "tb":
            return f"{s:.0f}{u}" if u == "b" else f"{s:.1f}{u}"
        s /= 1024


# =========================================================================== converters (Kibana)

def conv_kibana_data_view(doc, ctx, space):
    dvs = doc.get("data_view")
    dvs = dvs if isinstance(dvs, list) else [dvs]
    sp = space or ctx.args.space_id
    for dv in dvs:
        if not dv:
            continue
        did = dv.get("id")
        if not ctx.select("kibana_data_view", did, dv.get("name"), dv.get("title"))[0]:
            continue
        if did in ctx.names["dv_ids"]:
            continue
        ctx.names["dv_ids"].add(did)
        rn = ctx.unique("dv", dv.get("name") or dv.get("title") or did, "dv")
        b = Block(f'resource "elasticstack_kibana_data_view" "{rn}"')
        b.attr("space_id", sp)
        b.attr("override", ctx.args.override or None)
        b.key = did
        attrs = {"id": did, "name": dv.get("name") or dv.get("title"), "title": dv.get("title")}
        if "timeFieldName" in dv:
            if dv["timeFieldName"]:
                attrs["time_field_name"] = dv["timeFieldName"]
        else:
            attrs["time_field_name"] = ctx.args.time_field
            ctx.stats["_dv_no_time"][0] += 1
        if dv.get("allowNoIndex") is not None:
            attrs["allow_no_index"] = dv["allowNoIndex"]
        sf = [x.get("value") for x in dv.get("sourceFilters") or [] if x.get("value")]
        if sf:
            attrs["source_filters"] = sf
        rt = {k: {"type": v.get("type"), "script_source": (v.get("script") or {}).get("source")}
              for k, v in (dv.get("runtimeFieldMap") or {}).items()}
        rt = {k: {kk: vv for kk, vv in v.items() if vv is not None} for k, v in rt.items()}
        if rt:
            attrs["runtime_field_map"] = rt
        fa = {k: {kk: vv for kk, vv in (("custom_label", v.get("customLabel")), ("count", v.get("count")))
                  if vv is not None}
              for k, v in (dv.get("fieldAttrs") or {}).items()}
        fa = {k: v for k, v in fa.items() if v}
        if fa:
            attrs["field_attrs"] = fa
        if dv.get("fieldFormats"):
            ctx.warn(f"Data view {did}: field_formats niet omgezet (handmatig toevoegen indien nodig)")
        b.attr("data_view", attrs)
        title = dv.get("title")
        if title in ctx.names["dv_titles"]:
            ctx.warn(f"Data view {did}: pattern '{title}' bestaat al als andere data view in deze output")
        ctx.names["dv_titles"].add(title)
        ctx.add("data_views.tf", "kibana_data_view", b)
        ctx.imp(f"elasticstack_kibana_data_view.{rn}", f"{sp}/{did}")


SECRET_HINTS = {
    ".slack": "webhookUrl", ".slack_api": "token", ".email": "user, password",
    ".webhook": "user, password (of crt/key/pfx)", ".pagerduty": "routingKey",
    ".teams": "webhookUrl", ".opsgenie": "apiKey", ".jira": "email, apiToken",
    ".servicenow": "username, password", ".servicenow-sir": "username, password",
    ".servicenow-itom": "username, password", ".xmatters": "user, password of secretsUrl",
    ".torq": "token", ".tines": "email, token", ".gen-ai": "apiKey", ".bedrock": "accessKey, secret",
    ".d3security": "token", ".resilient": "apiKeyId, apiKeySecret", ".swimlane": "apiToken",
    ".cases-webhook": "user, password",
}
NO_SECRET_TYPES = {".index", ".server-log"}


UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def conv_connector(doc, ctx, space):
    sp = space or ctx.args.space_id
    for c in doc:
        cid, ctype = c["id"], c.get("connector_type_id")
        ctx.source_connectors[cid] = {"name": c.get("name"), "type": ctype,
                                      "preconfigured": bool(c.get("is_preconfigured"))}
        if cid in ctx.rule_connectors:  # een geselecteerde rule gebruikt deze connector: altijd mee
            sel, explicit = True, bool(ctx.matches("connector", c.get("name"), cid))
            ctx.item_matched.update(ctx.matches("connector", c.get("name"), cid) or [])
        elif ctx.rules_filtered and ctx.matches("connector", c.get("name"), cid) is None:
            ctx.conn_unused += 1  # rules gefilterd, connectors niet: alleen de gebruikte connectors
            continue
        else:
            sel, explicit = ctx.select("connector", c.get("name"), cid)
        if not sel:
            continue
        if c.get("is_system_action"):
            ctx.skip("connector"); continue
        if c.get("is_preconfigured") and not (ctx.args.recreate_preconfigured or explicit):
            ctx.skip("connector"); continue
        rn = ctx.unique("conn", c.get("name") or cid, "conn")
        addr = f"elasticstack_kibana_action_connector.{rn}"
        ctx.connector_addr[cid] = f"{addr}.connector_id"
        b = Block(f'resource "{addr.split(".")[0]}" "{rn}"')
        # de provider accepteert alleen UUID's als connector_id
        if not ctx.args.no_keep_ids and UUID_RE.match(cid):
            b.attr("connector_id", cid)
        b.attr("space_id", sp)
        b.attr("name", c.get("name"))
        b.attr("connector_type_id", ctype)
        b.json("config", c.get("config"))
        comment = f"{c.get('name')} ({ctype})"
        if c.get("is_preconfigured"):
            comment += f" — was preconfigured ('{cid}') op de bron, hier als gewone connector met nieuw ID"
        elif not UUID_RE.match(cid):
            comment += (f" — bron-ID '{cid}' is geen UUID (provider vereist UUID), "
                        f"krijgt een nieuw ID; rules verwijzen naar deze resource")
        if ctype not in NO_SECRET_TYPES:
            ctx.need_secrets_var = True
            b.attr("secrets", f"lookup(var.connector_secrets, {hcl_str(cid)}, null)", raw=True)
            comment += f" — secrets: {SECRET_HINTS.get(ctype, 'zie Kibana connector docs')}"
        ctx.add("connectors.tf", "connector", b, comment=comment)
        ctx.imp(addr, f"{sp}/{cid}")


def conv_space(doc, ctx, space):
    for s in doc:
        sel, explicit = ctx.select("space", s.get("id"), s.get("name"))
        if not sel:
            continue
        if s.get("_reserved") and not explicit:
            ctx.skip("space"); continue
        rn = ctx.unique("space", s["id"], "space")
        b = Block(f'resource "elasticstack_kibana_space" "{rn}"')
        b.key = s["id"]
        b.attr("space_id", s["id"])
        b.attr("name", s.get("name"))
        b.attr("description", s.get("description") or None)
        b.attr("color", s.get("color"))
        b.attr("initials", s.get("initials"))
        b.attr("image_url", s.get("imageUrl"))
        b.attr("solution", s.get("solution"))
        if s.get("disabledFeatures"):
            b.attr("disabled_features", sorted(s["disabledFeatures"]))
        ctx.add("spaces.tf", "space", b)
        if ctx.args.cluster_uuid:
            ctx.imp(f"elasticstack_kibana_space.{rn}", f"{ctx.args.cluster_uuid}/{s['id']}")


SO_DROP_FIELDS = {"score", "sort", "namespaces", "error"}


def saved_objects_from(doc):
    """Ondersteunt: _export (ndjson), _find ({saved_objects:[...]}),
    _bulk_get / legacy dashboards export ({objects:[...]}) en losse objecten."""
    if isinstance(doc, list):
        return doc, False
    if "__saved_objects__" in doc:
        return doc["__saved_objects__"], False
    if isinstance(doc.get("saved_objects"), list):
        return doc["saved_objects"], True
    if isinstance(doc.get("objects"), list):
        return doc["objects"], True
    if "type" in doc and "attributes" in doc:
        return [doc], True
    return [], False


def filter_by_title(objs, ctx, pats):
    """Houd dashboards waarvan de titel matcht (glob, hoofdletterongevoelig) plus alles
    waar ze (indirect) naar verwijzen. De rest van de export valt weg."""
    pats = [p.lower() for p in pats]
    by_key = {(o.get("type"), o.get("id")): o for o in objs if "type" in o}
    roots = [k for k, o in by_key.items()
             if k[0] == ctx.args.title_filter_type
             and any(fnmatch.fnmatchcase((o.get("attributes", {}).get("title") or "").lower(), p)
                     for p in pats)]
    titles = [(by_key[k].get("attributes", {}).get("title") or "").lower() for k in roots]
    ctx.item_matched.update(p for p in ctx.patterns("saved_objects")
                            if any(fnmatch.fnmatchcase(t, p.lower()) for t in titles))
    keep, todo = set(), list(roots)
    while todo:
        k = todo.pop()
        if k in keep or k not in by_key:
            continue
        keep.add(k)
        todo += [(r.get("type"), r.get("id")) for r in by_key[k].get("references") or []]
    print(f"  filter op titel: {len(roots)} {ctx.args.title_filter_type}(s) gevonden, "
          f"{len(keep)} objecten inclusief referenties (van {len(by_key)})", file=sys.stderr)
    return [o for o in objs if (o.get("type"), o.get("id")) in keep]


def conv_saved_objects(doc, ctx, space):
    objs, from_find = saved_objects_from(doc)
    objs = [{k: v for k, v in o.items() if k not in SO_DROP_FIELDS}
            for o in objs if isinstance(o, dict) and not o.get("error")]
    pats = ctx.args.title_filter + ctx.patterns("saved_objects")
    if pats:
        objs = filter_by_title(objs, ctx, pats)
    real = [o for o in objs if "type" in o]
    if not real:
        ctx.warn("Saved objects: geen objecten gevonden in de response"
                 + (" (na --title-filter/--items)" if pats else ""))
        return
    if from_find:
        ids = {(o["type"], o["id"]) for o in real}
        missing = sorted({f"{r.get('type')}:{r.get('id')}" for o in real for r in o.get("references") or []
                          if (r.get("type"), r.get("id")) not in ids})
        if missing:
            ctx.warn(f"Saved objects: {len(missing)} gerefereerde objecten zitten niet in deze response "
                     f"(bijv. {', '.join(missing[:3])}). Gebruik liever _export met "
                     f"includeReferencesDeep, anders werken panels mogelijk niet op het doelcluster.")
    n = len([k for k in ctx.extra_files if k.startswith("saved_objects")]) + 1
    fname = "saved_objects.ndjson" if n == 1 else f"saved_objects_{n}.ndjson"
    content = "\n".join(json.dumps(o, ensure_ascii=False, separators=(",", ":")) for o in objs) + "\n"
    sp = space or ctx.args.space_id
    rn = ctx.unique("so", fname.replace(".ndjson", ""), "so")
    b = Block(f'resource "elasticstack_kibana_import_saved_objects" "{rn}"')
    b.attr("space_id", sp)
    b.attr("overwrite", True)
    if ctx.args.output_dir or ctx.args.output:
        ctx.extra_files[fname] = content
        b.attr("file_contents", f'file("${{path.module}}/{fname}")', raw=True)
    else:
        esc = content.replace("${", "$${").replace("%{", "%%{")
        b.attr("file_contents", "<<-EOT\n" + esc + "  EOT", raw=True)
    types = defaultdict(int)
    for o in real:
        types[o["type"]] += 1
    titles = [o.get("attributes", {}).get("title") for o in real if o["type"] == "dashboard"]
    comment = ", ".join(f"{v} {k}" for k, v in sorted(types.items()))
    if titles:
        comment += " | dashboards: " + ", ".join(t for t in titles if t)
    ctx.add("saved_objects.tf", "saved_objects", b, comment=comment)


def conv_alerting_rule(doc, ctx, space):
    args = ctx.args
    if isinstance(doc, dict) and "data" in doc:
        if doc.get("total", 0) > len(doc["data"]):
            ctx.warn(f"Alerting rules: total={doc['total']} maar {len(doc['data'])} in response — "
                     f"haal ook &page=2 op")
        rules = doc["data"]
    else:
        rules = [doc]
    sp = space or args.space_id
    for r in sorted(rules, key=lambda x: (str(x.get("name") or "") if isinstance(x, dict) else "").lower()):
        if not isinstance(r, dict):
            ctx.rule_report.append({"status": "fout", "name": "?", "id": "?", "type": "?",
                                    "reasons": [f"geen geldig rule-object ({type(r).__name__})"],
                                    "connectors": []})
            continue
        if not ctx.select("alerting_rule", r.get("name"), r.get("id"))[0]:
            continue
        entry = {"status": "ok", "name": r.get("name") or "(zonder naam)", "id": r.get("id") or "?",
                 "type": r.get("rule_type_id") or "?", "reasons": [], "connectors": []}
        ctx.rule_report.append(entry)
        missing = [k for k in ("id", "name", "rule_type_id") if not r.get(k)]
        if missing:
            entry["status"] = "fout"
            entry["reasons"].append(f"verplicht veld ontbreekt: {', '.join(missing)}")
            continue
        bad_fields = [k for k, t in (("actions", list), ("params", dict), ("tags", list), ("schedule", dict))
                      if r.get(k) is not None and not isinstance(r[k], t)]
        if bad_fields:
            entry["status"] = "fout"
            entry["reasons"].append("ongeldige structuur in: " + ", ".join(
                f"{k} (verwacht {'lijst' if k in ('actions', 'tags') else 'object'})" for k in bad_fields))
            continue
        if args.obs_only and not is_obs_rule(r):
            entry["status"] = "overgeslagen"
            entry["reasons"].append(f"geen Observability rule (type {r['rule_type_id']}, consumer "
                                    f"{r.get('consumer')}); gebruik --all-rules om mee te nemen")
            ctx.skip("alerting_rule")
            continue
        try:
            _conv_one_rule(r, ctx, sp, entry)
        except Exception as e:  # één kapotte rule mag de rest niet tegenhouden
            entry["status"] = "fout"
            entry["reasons"].append(f"fout bij omzetten: {type(e).__name__}: {e}")
            continue
        if entry["reasons"] and entry["status"] == "ok":
            entry["status"] = "opmerkingen"


def _conv_one_rule(r, ctx, sp, entry):
    args = ctx.args
    note = entry["reasons"].append
    rtype = r["rule_type_id"]
    if not r.get("consumer"):
        note("consumer ontbreekt, 'alerts' aangenomen")
    if not (r.get("schedule") or {}).get("interval"):
        note("schedule.interval ontbreekt, '1m' aangenomen")
    if r.get("mute_all") or r.get("muted_alert_ids"):
        note("rule is (deels) gemute op de bron; mute wordt niet door de provider ondersteund en niet overgenomen")
    if r.get("snooze_schedule"):
        note("snooze schedule wordt niet door de provider ondersteund en niet overgenomen")
    status = ((r.get("execution_status") or {}).get("status")
              or ((r.get("last_run") or {}).get("outcome")))
    if status in ("error", "failed"):
        msg = ((r.get("execution_status") or {}).get("error") or {}).get("message") \
              or "; ".join((r.get("last_run") or {}).get("outcome_msg") or [])
        note(f"rule faalde ook al op de bron: {msg[:150] or status}")
    if True:
        rn = ctx.unique("rule", r.get("name") or r["id"], "rule")
        actions = r.get("actions") or []
        has_freq = any(a.get("frequency") for a in actions)
        b = Block(f'resource "elasticstack_kibana_alerting_rule" "{rn}"')
        if not args.no_keep_ids:
            b.attr("rule_id", r["id"])
        b.attr("space_id", sp)
        b.attr("name", r.get("name"))
        b.attr("consumer", r.get("consumer") or "alerts")
        b.attr("rule_type_id", rtype)
        b.attr("interval", (r.get("schedule") or {}).get("interval", "1m"))
        b.attr("enabled", False if args.disabled else r.get("enabled", True))
        if not has_freq:
            b.attr("notify_when", r.get("notify_when") or None)
            b.attr("throttle", r.get("throttle") or None)
        if r.get("tags"):
            b.attr("tags", sorted(r["tags"]))
        b.attr("alert_delay", (r.get("alert_delay") or {}).get("active"))
        b.json("params", r.get("params") or {}, skip_empty=False)
        flap = r.get("flapping")
        if flap and flap.get("look_back_window") is not None and not args.no_flapping:
            items = [("look_back_window", flap["look_back_window"]),
                     ("status_change_threshold", flap.get("status_change_threshold"))]
            if flap.get("enabled") is not None:
                items.append(("enabled", flap["enabled"]))
            b.attr("flapping", dict(items))
        art = r.get("artifacts") or {}
        dashboards = [d["id"] for d in art.get("dashboards") or [] if d.get("id")]
        guide = (art.get("investigation_guide") or {}).get("blob")
        if (dashboards or guide) and not args.no_artifacts:
            a = {}
            if dashboards:
                a["dashboards"] = [{"id": d} for d in dashboards]
            if guide:
                a["investigation_guide"] = {"content": guide}
            b.attr("artifacts", a)
        for act in actions:
            if not act.get("id"):
                note(f"action zonder connector-ID ({act.get('connector_type_id')}) weggelaten")
                continue
            if args.drop_missing_actions and not connector_resolvable(ctx, act["id"]):
                ctx.dropped_actions.append((r.get("name"), act["id"]))
                note(f"action naar connector {act['id']} weggelaten (connector ontbreekt)")
                continue
            entry["connectors"].append(act["id"])
            ab = b.block("actions")
            ab.attr("id", connector_ref(ctx, act, r.get("name")), raw=True)
            ab.attr("group", act.get("group"))
            ab.json("params", act.get("params") or {}, skip_empty=False)
            fr = act.get("frequency") or {}
            if fr:
                fb = ab.block("frequency")
                fb.attr("summary", fr.get("summary"))
                fb.attr("notify_when", fr.get("notify_when"))
                fb.attr("throttle", fr.get("throttle") or None)
            af = act.get("alerts_filter") or {}
            q, tf = af.get("query") or {}, af.get("timeframe")
            if q.get("filters"):
                note("alerts_filter.query.filters niet ondersteund door de provider, weggelaten (alleen kql)")
            if q.get("kql") or tf:
                afb = ab.block("alerts_filter")
                afb.attr("kql", q.get("kql") or None)
                if tf:
                    tb = afb.block("timeframe")
                    tb.attr("days", tf.get("days") or None)
                    tb.attr("timezone", tf.get("timezone"))
                    tb.attr("hours_start", (tf.get("hours") or {}).get("start"))
                    tb.attr("hours_end", (tf.get("hours") or {}).get("end"))
        if r.get("system_actions"):
            note(f"{len(r['system_actions'])} system action(s) niet ondersteund door de provider, weggelaten")
        if flap and args.no_flapping:
            note("flapping-instellingen weggelaten (--no-flapping)")
        if (dashboards or guide) and args.no_artifacts:
            note("artifacts (gekoppelde dashboards/investigation guide) weggelaten (--no-artifacts)")
        ctx.add("alerting_rules.tf", "alerting_rule", b,
                comment=f"{r.get('name')} ({rtype}, bron-id {r.get('id')})")
        ctx.imp(f"elasticstack_kibana_alerting_rule.{rn}", f"{sp}/{r['id']}")


OBS_PREFIXES = ("observability.", "logs.", "metrics.", "apm.", "slo.", "xpack.uptime.",
                "xpack.synthetics.", "xpack.ml.anomaly_detection_alert", "datasetQuality.")


def scan_rule_connectors(ctx, docs):
    """Vooraf: welke connectors gebruiken de rules die omgezet worden? Die komen altijd mee."""
    has_rules = False
    for doc in docs:
        rules = doc.get("data") if isinstance(doc, dict) and "data" in doc else [doc]
        for r in rules or []:
            if not isinstance(r, dict):
                continue
            has_rules = True
            hits = ctx.matches("alerting_rule", r.get("name"), r.get("id"))
            if hits == [] or (ctx.args.obs_only and r.get("rule_type_id") and not is_obs_rule(r)):
                continue
            for act in r.get("actions") or []:
                if isinstance(act, dict) and act.get("id"):
                    ctx.rule_connectors.add(act["id"])
    ctx.rules_filtered = has_rules and ctx.matches("alerting_rule", "") is not None


def is_obs_rule(r):
    t, c = r.get("rule_type_id", ""), r.get("consumer", "")
    if t.startswith("siem."):
        return False
    return t.startswith(OBS_PREFIXES) or (
        t == ".es-query" and c in {"observability", "logs", "infrastructure", "apm", "slo", "uptime"})


def connector_ref(ctx, act, rule_name):
    """Wordt pas bij het renderen opgelost (connectors kunnen later in de input staan)."""
    cid = act["id"]
    info = ctx.connector_refs.setdefault(cid, {"type": act.get("connector_type_id", "?"), "rules": set()})
    info["rules"].add(rule_name)
    return f"@@CONN:{cid}@@"


def target_id_for(ctx, cid):
    """Zoek de connector op het doelcluster: zelfde ID, anders zelfde naam."""
    tgt = ctx.target_connectors
    if not tgt:
        return None
    if cid in tgt["ids"]:
        return cid
    name = (ctx.source_connectors.get(cid) or {}).get("name")
    return tgt["names"].get(name) if name else None


def connector_resolvable(ctx, cid) -> bool:
    return cid in ctx.connector_addr or target_id_for(ctx, cid) is not None


def resolve_connector_refs(ctx, text):
    def repl(m):
        cid = m.group(1)
        if cid in ctx.connector_addr:
            return ctx.connector_addr[cid]
        if ctx.args.raw_connector_ids:
            return hcl_str(target_id_for(ctx, cid) or cid)
        return f"local.connector_ids[{hcl_str(cid)}]"
    return re.sub(r"@@CONN:(.+?)@@", repl, text)


def connector_status(ctx, cid):
    src = ctx.source_connectors.get(cid)
    tid = target_id_for(ctx, cid)
    if tid:
        return tid, "OK: gevonden op doelcluster" + ("" if tid == cid else f" op naam '{src['name']}'")
    if ctx.target_connectors:
        what = "NIET gevonden op doelcluster"
    else:
        what = "onbekend of deze op het doelcluster bestaat (geef --target-connectors mee)"
    if src is None:
        return cid, f"{what}; zat niet in de connectors-export"
    if src["preconfigured"]:
        return cid, (f"{what}; preconfigured connector '{src['name']}' — moet in kibana.yml van het "
                     f"doel staan, of gebruik --recreate-preconfigured")
    return cid, f"{what}; '{src['name']}'"


def connector_locals(ctx):
    missing = {c: i for c, i in ctx.connector_refs.items() if c not in ctx.connector_addr}
    if not missing or ctx.args.raw_connector_ids:
        return ""
    w = max(len(hcl_str(c)) for c in missing)
    lines = ["locals {",
             "  # Connectors die niet door deze Terraform worden beheerd:",
             "  # bron-ID => ID op het doelcluster. Pas de rechterkant aan waar nodig.",
             "  connector_ids = {"]
    for cid, info in sorted(missing.items()):
        tid, status = connector_status(ctx, cid)
        lines.append(f"    {hcl_str(cid).ljust(w)} = {hcl_str(tid)}  # {info['type']}: {status}")
    return "\n".join(lines + ["  }", "}", ""])


def load_target_connectors(path):
    with open(path, encoding="utf-8") as f:
        docs = parse_console_text(f.read())
    items = []
    for _, d in docs:
        items += d if isinstance(d, list) else d.get("data", []) if isinstance(d, dict) else []
    return {"ids": {c["id"] for c in items if "id" in c},
            "names": {c["name"]: c["id"] for c in items if "name" in c and "id" in c}}


STATUS_LABEL = {
    "fout": "NIET VERWERKT (fout)",
    "overgeslagen": "NIET VERWERKT (overgeslagen)",
    "apply_faalt": "VERWERKT, MAAR APPLY FAALT WAARSCHIJNLIJK",
    "opmerkingen": "VERWERKT MET OPMERKINGEN",
    "ok": "OK",
}


def report_rules(ctx):
    rep = ctx.rule_report
    if not rep:
        return
    # connectors die niet beheerd worden en niet op het doel bestaan -> apply faalt
    for e in rep:
        if e["status"] in ("ok", "opmerkingen"):
            bad = [c for c in e["connectors"]
                   if c not in ctx.connector_addr and target_id_for(ctx, c) is None]
            for c in bad:
                _, why = connector_status(ctx, c)
                e["reasons"].insert(0, f"connector {c}: {why}")
            if bad:
                # met --target-connectors weten we het zeker; zonder is het een waarschuwing
                e["status"] = "apply_faalt" if ctx.target_connectors else "opmerkingen"

    counts = defaultdict(int)
    for e in rep:
        counts[e["status"]] += 1
    out = ["", "ALERTING RULES — rapport",
           "  " + ", ".join(f"{STATUS_LABEL[s].lower()}: {counts[s]}" for s in STATUS_LABEL if counts[s])]
    for status in ("fout", "overgeslagen", "apply_faalt", "opmerkingen"):
        items = [e for e in rep if e["status"] == status]
        if not items:
            continue
        out.append(f"\n  {STATUS_LABEL[status]} ({len(items)}):")
        for e in sorted(items, key=lambda x: x["name"].lower()):
            out.append(f"    - {e['name']}  [id {e['id']}, {e['type']}]")
            for reason in e["reasons"]:
                out.append(f"        • {reason}")
    print("\n".join(out), file=sys.stderr)

    path = ctx.args.report
    if path:
        if path.lower().endswith(".csv"):
            import csv
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f, delimiter=";")
                w.writerow(["status", "naam", "id", "rule_type_id", "redenen"])
                for e in sorted(rep, key=lambda x: (list(STATUS_LABEL).index(x["status"]), x["name"].lower())):
                    w.writerow([STATUS_LABEL[e["status"]], e["name"], e["id"], e["type"],
                                " | ".join(e["reasons"])])
        else:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(out).lstrip() + "\n\n  OK:\n" + "\n".join(
                    f"    - {e['name']}  [id {e['id']}, {e['type']}]"
                    for e in sorted(rep, key=lambda x: x["name"].lower()) if e["status"] == "ok") + "\n")
        print(f"\n  Rapport geschreven naar {path}", file=sys.stderr)


# =========================================================================== direct ophalen (--fetch)

DEFAULT_CONFIG_PATHS = ["elastic2tf.ini", os.path.expanduser("~/.config/elastic2tf.ini")]
# cluster_settings staat er bewust niet in: alleen op verzoek (types = ... in de config of --only)
DEFAULT_FETCH_TYPES = ["space", "connector", "datastream", "kibana_data_view", "component_template",
                       "index_template", "ilm", "slm", "ingest_pipeline", "role", "role_mapping",
                       "alerting_rule", "saved_objects"]
KIBANA_TYPES = {"space", "connector", "kibana_data_view", "alerting_rule", "saved_objects"}


class FetchError(Exception):
    def __init__(self, status, msg):
        super().__init__(f"HTTP {status}: {msg}")
        self.status = status


class Api:
    """Minimale REST-client (alleen standaardbibliotheek) met basic auth."""

    def __init__(self, label, url, username, password, ssl_ctx, timeout, kibana=False):
        import base64
        self.label, self.url, self.kibana = label, url.rstrip("/"), kibana
        self.ssl_ctx, self.timeout = ssl_ctx, timeout
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}", "Accept": "application/json"}
        if kibana:
            self.headers["kbn-xsrf"] = "true"

    def call(self, method, path, body=None, text=False):
        import urllib.error
        import urllib.request
        data, headers = None, dict(self.headers)
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        path = path.replace(" ", "%20")
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self.ssl_ctx) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            try:
                j = json.loads(detail)
                err = j.get("error")
                detail = (err.get("reason") or err.get("type")) if isinstance(err, dict) else \
                    (j.get("message") or err or detail)
            except (ValueError, AttributeError):
                pass
            raise FetchError(e.code, str(detail)[:300])
        except urllib.error.URLError as e:
            sys.exit(f"{self.label} ({self.url}) niet bereikbaar: {e.reason}\n"
                     f"Controleer de url in de config; bij een eigen CA: ca_cert = /pad/naar/ca.crt")
        return raw if text else json.loads(raw or "null")


def read_config(path):
    import configparser
    paths = [path] if path else DEFAULT_CONFIG_PATHS
    found = next((p for p in paths if os.path.exists(p)), None)
    if not found:
        sys.exit(f"Configbestand niet gevonden ({', '.join(paths)}). "
                 f"Kopieer elastic2tf.example.ini naar elastic2tf.ini en vul url en username in.")
    cp = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    cp.read(found, encoding="utf-8")
    if cp.has_section("elasticsearch") and cp.has_option("elasticsearch", "password") or \
            cp.has_section("kibana") and cp.has_option("kibana", "password"):
        sys.exit(f"{found}: zet geen wachtwoord in de config; het wordt bij het starten gevraagd.")
    return found, cp


def make_ssl_ctx(section):
    import ssl
    if not section.getboolean("verify_ssl", fallback=True):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return ssl.create_default_context(cafile=section.get("ca_cert") or None)


def split_list(value):
    return [x.strip() for x in re.split(r"[,\s]+", value or "") if x.strip()]


def connect(cp, need_es, need_kbn):
    """Maakt de clients; vraagt per gebruikersnaam één keer het wachtwoord."""
    import getpass
    passwords, apis = {}, {}
    for key, label, needed in (("elasticsearch", "Elasticsearch", need_es), ("kibana", "Kibana", need_kbn)):
        if not needed:
            continue
        if not cp.has_section(key) or not cp[key].get("url") or not cp[key].get("username"):
            sys.exit(f"Config mist [{key}] met url en username (nodig voor de gevraagde types).")
        sec = cp[key]
        user = sec["username"]
        if user not in passwords:
            passwords[user] = getpass.getpass(f"Wachtwoord voor {user} ({label} {sec['url']}): ")
        apis[key] = Api(label, sec["url"], user, passwords[user], make_ssl_ctx(sec),
                        sec.getfloat("timeout", fallback=60), kibana=(key == "kibana"))
    # direct controleren of inloggen lukt, zodat een typefout meteen opvalt
    for key, path in (("elasticsearch", "/"), ("kibana", "/api/status")):
        if key in apis:
            try:
                info = apis[key].call("GET", path)
            except FetchError as e:
                hint = " (gebruikersnaam of wachtwoord onjuist)" if e.status == 401 else ""
                sys.exit(f"Inloggen op {apis[key].label} mislukt: {e}{hint}")
            if key == "elasticsearch":
                apis["cluster_uuid"] = (info or {}).get("cluster_uuid")
                ver = ((info or {}).get("version") or {}).get("number")
            else:
                ver = (((info or {}).get("version") or {}).get("number"))
            print(f"  Verbonden met {apis[key].label} {apis[key].url}" + (f" (v{ver})" if ver else ""),
                  file=sys.stderr)
    return apis


# Terraform-resourcenamen (met en zonder prefix) -> type
RESOURCE_TYPES = {
    "elasticstack_elasticsearch_index_template": "index_template",
    "elasticstack_elasticsearch_component_template": "component_template",
    "elasticstack_elasticsearch_index_lifecycle": "ilm",
    "elasticstack_elasticsearch_snapshot_lifecycle": "slm",
    "elasticstack_elasticsearch_ingest_pipeline": "ingest_pipeline",
    "elasticstack_elasticsearch_security_role": "role",
    "elasticstack_elasticsearch_security_role_mapping": "role_mapping",
    "elasticstack_elasticsearch_cluster_settings": "cluster_settings",
    "elasticstack_kibana_alerting_rule": "alerting_rule",
    "elasticstack_kibana_action_connector": "connector",
    "elasticstack_kibana_data_view": "kibana_data_view",
    "elasticstack_kibana_space": "space",
    "elasticstack_kibana_import_saved_objects": "saved_objects",
}
HTTP_METHODS = ("GET", "POST", "PUT", "HEAD")


def spec_aliases():
    a = {t: t for t in CONVERTERS}
    for res, t in RESOURCE_TYPES.items():
        short = res[len("elasticstack_"):]
        a[res] = a[short] = a[short.split("_", 1)[1]] = t
    return a


def is_call_spec(spec):
    s = spec.strip()
    return (s.split(None, 1)[0].upper() in HTTP_METHODS or s.startswith(("_", "/", "kbn:"))
            or "/" in s.split(None, 1)[0])


def parse_call(spec):
    """'[METHOD] pad [json-body]' -> (method, kibana?, pad, body). Zelfde notatie als Dev Tools."""
    parts = spec.strip().split(None, 1)
    method = "GET"
    if parts[0].upper() in HTTP_METHODS:
        method, spec = parts[0].upper(), (parts[1] if len(parts) > 1 else "")
    spec = spec.strip()
    if not spec:
        sys.exit("Lege call in --fetch")
    # body = alles vanaf de eerste '{' of '[' na een spatie; spaties in de query mogen gewoon
    m = re.search(r"\s+(?=[\[{])", spec)
    path, body = (spec[:m.start()], spec[m.end():]) if m else (spec, None)
    path = re.sub(r"\s+", " ", path.strip())
    if body is not None:
        try:
            body = json.loads(body)
        except ValueError as e:
            sys.exit(f"Body van '{spec}' is geen geldige JSON: {e}")
    kibana = path.startswith("kbn:")
    if kibana:
        path = path[4:]
    return method, kibana, "/" + path.lstrip("/"), body


def expand_specs(specs):
    """Specs uit --fetch / config: type, Terraform-resource of Dev Tools-call.
    Geeft (types, calls) terug."""
    aliases, types, calls = spec_aliases(), [], []
    for spec in specs:
        if is_call_spec(spec):
            calls.append(parse_call(spec))
            continue
        for word in [w for w in re.split(r"[,\s]+", spec) if w]:
            t = aliases.get(word.lower())
            if not t:
                sys.exit(f"Onbekend in --fetch: '{word}'. Gebruik een type (--list-types), een Terraform-"
                         f"resource (bijv. elasticstack_elasticsearch_index_lifecycle) of een call "
                         f"(bijv. \"GET _ilm/policy/mijn-policy\" of \"kbn:/api/actions/connectors\").")
            if t not in types:
                types.append(t)
    return types, calls


def config_specs(value):
    """[fetch] types: komma's of regels; een regel met een call blijft heel (body kan komma's hebben)."""
    out = []
    for line in (value or "").splitlines():
        line = line.strip()
        if line:
            out += [line] if is_call_spec(line) else [x for x in re.split(r"[,\s]+", line) if x]
    return out


def fetch_all(args, cp):
    """Haalt de JSON op via de API's. Geeft [(hint, doc)] (zoals parse_console_text)."""
    fc = cp["fetch"] if cp.has_section("fetch") else {}
    specs = args.fetch or args.only or config_specs(fc.get("types"))
    spaces = split_list(fc.get("spaces")) or ["default"]
    ds_pattern = fc.get("datastream_pattern") or "logs-*"
    so_types = split_list(fc.get("saved_object_types")) or ["dashboard"]
    if not args.items and not args.pipelines:  # --items op de commandline gaat voor
        if fc.get("items"):
            args.items = [",".join(x.strip() for x in re.split(r"[,\n]+", fc.get("items")) if x.strip())]
        if fc.get("pipelines"):
            args.pipelines = [",".join(split_list(fc.get("pipelines")))]
    item_types = parse_items(args)
    if not specs and item_types and "*" not in item_types:  # alleen type:naam -> alleen die types ophalen
        specs = sorted(item_types)
    types, calls = expand_specs(specs) if specs else (list(DEFAULT_FETCH_TYPES), [])

    # types omzetten naar calls; expand=True: data view-lijst per ID verder ophalen
    plan = []  # (method, kibana, pad, body, expand)
    es_calls = {
        "datastream": f"/_data_stream/{ds_pattern}/_stats?human",
        "index_template": "/_index_template",
        "component_template": "/_component_template",
        "ilm": "/_ilm/policy",
        "slm": "/_slm/policy",
        "ingest_pipeline": "/_ingest/pipeline",
        "role": "/_security/role",
        "role_mapping": "/_security/role_mapping",
        "cluster_settings": "/_cluster/settings?flat_settings=true",
    }
    for t in types:
        if t in es_calls:
            plan.append(("GET", False, es_calls[t], None, False))
    if "space" in types:
        plan.append(("GET", True, "/api/spaces/space", None, False))
    for sp in spaces:
        pre = "" if sp == "default" else f"/s/{sp}"
        if "connector" in types:
            plan.append(("GET", True, pre + "/api/actions/connectors", None, False))
        if "kibana_data_view" in types:
            plan.append(("GET", True, pre + "/api/data_views", None, True))
        if "alerting_rule" in types:
            plan.append(("GET", True, pre + "/api/alerting/rules/_find", None, False))
        if "saved_objects" in types:
            plan.append(("POST", True, pre + "/api/saved_objects/_export", None, False))
    # een lijst-call voor data views wordt ook per ID aangevuld (tijdveld, runtime fields)
    plan += [(m, k, pth, b, k and m == "GET" and pth.split("?")[0].rstrip("/").endswith("/api/data_views"))
             for m, k, pth, b in calls]

    # rules verwijzen naar connectors: haal die uit dezelfde space mee (tenzij --no-rule-connectors)
    if not args.no_rule_connectors:
        have = {pth.split("?")[0] for m, k, pth, b, e in plan if k}
        for m, k, pth, b, e in list(plan):
            if k and re.search(r"/api/alerting/rules?(/|$)", pth):
                pre = pth[: pth.index("/api/")]
                cpath = pre + "/api/actions/connectors"
                if cpath not in have:
                    have.add(cpath)
                    plan.append(("GET", True, cpath, None, False))
                    print(f"  Connectors van de rules worden meegehaald: GET kbn:{cpath}", file=sys.stderr)

    need_kbn = any(k for _, k, _, _, _ in plan)
    need_es = any(not k for _, k, _, _, _ in plan) or (args.with_imports and not args.cluster_uuid)
    apis = connect(cp, need_es, need_kbn)
    if args.with_imports and not args.cluster_uuid and apis.get("cluster_uuid"):
        args.cluster_uuid = apis["cluster_uuid"]
        print(f"  Cluster UUID voor import-blocks: {args.cluster_uuid}", file=sys.stderr)

    results, export = [], []

    def keep(method, hint, doc, export_text=None):
        results.append((hint, doc))
        export.append(f"# {method} {hint} 200 OK\n" + (
            export_text if export_text is not None else json.dumps(doc, indent=2, ensure_ascii=False)))

    def with_query(path, **params):
        from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
        u = urlsplit(path)
        q = dict(parse_qsl(u.query))
        q.update({k: str(v) for k, v in params.items() if k not in q or k == "page"})
        return urlunsplit(("", "", u.path, urlencode(q, safe=":()* "), ""))

    for method, kibana, path, body, expand in plan:
        api = apis["kibana" if kibana else "elasticsearch"]
        hint = ("kbn:" + path) if kibana else path.lstrip("/")
        hint_path = hint.split("?", 1)[0] if kibana else hint
        try:
            if kibana and "/api/alerting/rules/_find" in path:   # alle pagina's ophalen
                rules, page = [], 1
                while True:
                    r = api.call(method, with_query(path, per_page=100, page=page))
                    rules += r.get("data") or []
                    if not r.get("data") or len(rules) >= (r.get("total") or 0):
                        break
                    page += 1
                keep(method, hint_path, {"page": 1, "per_page": len(rules), "total": len(rules), "data": rules})
            elif kibana and "/api/saved_objects/_export" in path:  # ndjson
                b = dict(body or {"type": so_types})
                b.setdefault("includeReferencesDeep", True)
                b.setdefault("excludeExportDetails", True)
                text = api.call("POST", path, b, text=True)
                objs = [json.loads(line) for line in text.splitlines() if line.strip()]
                if objs:
                    keep("POST", hint_path, {"__saved_objects__": objs}, text.strip())
                else:
                    print(f"  Geen saved objects in {hint}", file=sys.stderr)
            elif expand:  # data views: lijst, daarna elk apart (tijdveld, runtime fields)
                pre = path[: path.index("/api/")]
                for dv in api.call("GET", path).get("data_view") or []:
                    p = f"{pre}/api/data_views/data_view/{dv['id']}"
                    try:
                        keep("GET", "kbn:" + p, api.call("GET", p))
                    except FetchError as e:
                        print(f"  Data view {dv.get('id')} overgeslagen: {e}", file=sys.stderr)
            else:
                keep(method, hint, api.call(method, path, body))
        except FetchError as e:
            print(f"  Overgeslagen: {method} {hint} gaf {e}", file=sys.stderr)

    print(f"  {len(results)} responses opgehaald", file=sys.stderr)
    if args.save_export:
        with open(args.save_export, "w", encoding="utf-8") as f:
            f.write("\n\n".join(export) + "\n")
        print(f"  Ruwe responses bewaard in {args.save_export} (opnieuw te gebruiken met -i)", file=sys.stderr)
    return results


# =========================================================================== for_each-output

FOR_EACH_LABELS = {
    "elasticstack_elasticsearch_index_template": "index_templates",
    "elasticstack_elasticsearch_component_template": "component_templates",
    "elasticstack_elasticsearch_index_lifecycle": "ilm_policies",
    "elasticstack_elasticsearch_snapshot_lifecycle": "slm_policies",
    "elasticstack_elasticsearch_ingest_pipeline": "ingest_pipelines",
    "elasticstack_elasticsearch_security_role": "roles",
    "elasticstack_elasticsearch_security_role_mapping": "role_mappings",
    "elasticstack_kibana_data_view": "data_views",
    "elasticstack_kibana_action_connector": "connectors",
    "elasticstack_kibana_alerting_rule": "alerting_rules",
    "elasticstack_kibana_space": "spaces",
    "elasticstack_kibana_import_saved_objects": "saved_objects",
}
# blocks die meerdere keren mogen voorkomen: in de data altijd een lijst
REPEATABLE_BLOCKS = {"actions", "indices", "remote_indices", "applications", "alias", "setting"}
HEADER_RE = re.compile(r'^resource "([^"]+)" "([^"]+)"$')


def block_schema(blocks, schema=None):
    """Samengevoegd schema van een aantal Blocks: key -> ("attr", soort) of ("block", schema, max_aantal)."""
    schema = {} if schema is None else schema
    for b in blocks:
        counts = defaultdict(int)
        for kind, a, _, meta in b.items:
            if kind == "attr":
                cur = schema.get(a)
                if cur is None:
                    schema[a] = ("attr", meta[0])
                elif cur[0] == "attr" and cur[1] != meta[0]:
                    schema[a] = ("attr", "mixed")
            elif kind == "block":
                counts[a.header] += 1
                cur = schema.get(a.header)
                sub = cur[1] if cur else {}
                block_schema([a], sub)
                schema[a.header] = ("block", sub, max(counts[a.header], cur[2] if cur else 0))
    return schema


def block_data(b, schema):
    """De waarden van één Block als HCL-data (voor de locals-map)."""
    out = {}
    for kind, a, _, meta in b.items:
        if kind == "attr":
            soort, v = meta
            if schema[a][1] == "mixed":  # soort verschilt per object (bijv. query als string of JSON)
                if soort == "json":
                    v = json.dumps(v, ensure_ascii=False, separators=(",", ":"))
                elif soort == "jsonlist":
                    v = [json.dumps(x, ensure_ascii=False, separators=(",", ":")) for x in v]
            out[a] = v
        elif kind == "block":
            sub = block_data(a, schema[a.header][1])
            if schema[a.header][2] > 1 or a.header in REPEATABLE_BLOCKS:
                out.setdefault(a.header, []).append(sub)
            else:
                out[a.header] = sub
    return out


def presence(datas, key):
    return all(key in d for d in datas)


def derive(values, keys, indent):
    """Expressie voor een veld als het bij alle objecten gelijk is of uit de sleutel volgt, anders None."""
    if len(values) < 2:
        return None
    if all(isinstance(v, str) and not isinstance(v, Raw) for v in values):
        if all(v == k for v, k in zip(values, keys)):
            return Raw("each.key")
        k0, v0 = keys[0], values[0]
        if v0.startswith(k0) and len(v0) > len(k0):
            sfx = v0[len(k0):]
            if all(v == k + sfx for v, k in zip(values, keys)):
                return Raw('"${each.key}' + hcl_str(sfx)[1:])
        if v0.endswith(k0) and len(v0) > len(k0):
            pfx = v0[: -len(k0)]
            if all(v == pfx + k for v, k in zip(values, keys)):
                return Raw(hcl_str(pfx)[:-1] + '${each.key}"')
    first = values[0]
    if not isinstance(first, dict) and all(type(v) is type(first) and v == first for v in values):
        return first if isinstance(first, Raw) else Raw(hcl_value(first, indent))
    return None


def hoist(schema, datas, keys):
    """Haalt velden die gelijk zijn of uit de sleutel volgen uit de data en geeft
    {veld: expressie} terug voor in de resource. Werkt op attributen en één niveau diep in objecten."""
    out = {}
    for key, spec in schema.items():
        if spec[0] != "attr" or spec[1] not in ("val", "raw") or not presence(datas, key):
            continue
        values = [d[key] for d in datas]
        expr = derive(values, keys, 1)
        if expr is not None:
            out[key] = expr
            for d in datas:
                del d[key]
            continue
        if not all(isinstance(v, dict) and not isinstance(v, Raw) for v in values):
            continue
        fields = []  # (veld, expressie)
        names = list(dict.fromkeys(f for v in values for f in v))
        derived = {}
        for f in names:
            if all(f in v for v in values):
                e = derive([v[f] for v in values], keys, 2)
                if e is not None:
                    derived[f] = e
        if not derived:
            continue
        ref = f"each.value.{key}" if IDENT_RE.match(key) else f"each.value[{hcl_str(key)}]"
        for f in names:
            if f in derived:
                fields.append((f, str(derived[f])))
            else:
                r = f"{ref}.{f}" if IDENT_RE.match(f) else f"{ref}[{hcl_str(f)}]"
                fields.append((f, r if all(f in v for v in values) else f"try({r}, null)"))
            for v in values:
                if f in derived:
                    v.pop(f, None)
        for d in datas:
            if not d[key]:
                del d[key]
        w = max(len(hcl_key(f)) for f, _ in fields)
        out[key] = Raw("{\n" + "\n".join(f"    {hcl_key(f).ljust(w)} = {e}" for f, e in fields) + "\n  }")
    return out


def template(parent: Block, schema, datas, var, used=(), hoisted=None):
    """Vult `parent` met attributen/dynamic blocks die naar `var` (each.value / x.value) verwijzen.
    `used`: iterator-namen van de omliggende dynamic blocks."""
    # eerst de attributen, dan de dynamic blocks
    for key, spec in sorted(schema.items(), key=lambda kv: kv[1][0] == "block"):
        if hoisted and key in hoisted:
            parent.attr(key, hoisted[key], raw=True)
            continue
        always = presence(datas, key)
        ref = f"{var}.{key}" if IDENT_RE.match(key) else f'{var}[{hcl_str(key)}]'
        if spec[0] == "attr":
            soort = spec[1]
            if soort == "json":
                expr = f"jsonencode({ref})"
            elif soort == "jsonlist":
                expr = f"[for v in {ref} : jsonencode(v)]"
            else:
                expr = ref
            parent.attr(key, expr if always else f"try({expr}, null)", raw=True)
        else:
            sub, many = spec[1], spec[2] > 1 or key in REPEATABLE_BLOCKS
            if many:
                fe = ref if always else f"try({ref}, [])"
                children = [c for d in datas for c in d.get(key, [])]
            else:
                fe = f"[{ref}]" if always else f"try([{ref}], [])"
                children = [d[key] for d in datas if key in d]
            it = key if key not in used else f"{key}_{len(used)}"  # zelfde naam als ouder: eigen iterator
            dyn = parent.block(f'dynamic "{key}"')
            dyn.attr("for_each", fe, raw=True)
            if it != key:
                dyn.attr("iterator", it, raw=True)
            template(dyn.block("content"), sub, children, f"{it}.value", (*used, it))


def render_for_each(entries, addr_map):
    """[(Block, comment)] van één bestand -> HCL met per resource-type een locals-map en één resource.
    Vult addr_map met oud adres -> nieuw adres (voor imports en verwijzingen)."""
    groups, order = defaultdict(list), []
    for b, comment in entries:
        m = HEADER_RE.match(b.header)
        rtype = m.group(1) if m else None
        if rtype not in FOR_EACH_LABELS:
            rtype = ("__single__", id(b))
        if rtype not in groups:
            order.append(rtype)
        groups[rtype].append((b, comment))
    out = []
    for rtype in order:
        items = groups[rtype]
        if isinstance(rtype, tuple):
            b, comment = items[0]
            out.append((f"# {comment}\n" if comment else "") + b.render() + "\n")
            continue
        label = FOR_EACH_LABELS[rtype]
        schema = block_schema([b for b, _ in items])
        keys, datas, keylist = set(), [], []
        for b, comment in items:
            rn = HEADER_RE.match(b.header).group(2)
            name = next((m[1] for k, a, _, m in b.items if k == "attr" and a == "name" and m[0] == "val"), None)
            key = str(b.key or name or rn)
            base, i = key, 2
            while key in keys:
                key, i = f"{base} ({i})", i + 1
            keys.add(key)
            addr_map[f"{rtype}.{rn}"] = f"{rtype}.{label}[{hcl_str(key)}]"
            keylist.append(key)
            datas.append(block_data(b, schema))
        hoisted = hoist(schema, datas, keylist)
        comments = [c for _, c in items]
        if all(not d for d in datas):  # niets per object over: alleen de sleutels
            lines = [(f"    # {c}\n" if c else "") + f"    {hcl_str(k)}," for k, c in zip(keylist, comments)]
            out.append("locals {\n  " + label + " = toset([\n" + "\n".join(lines) + "\n  ])\n}\n")
        else:
            lines = [(f"    # {c}\n" if c else "") + f"    {hcl_str(k)} = {hcl_value(d, 2)}"
                     for k, d, c in zip(keylist, datas, comments)]
            out.append("locals {\n  " + label + " = {\n" + "\n\n".join(lines) + "\n  }\n}\n")
        res = Block(f'resource "{rtype}" "{label}"')
        res.attr("for_each", f"local.{label}", raw=True)
        res.blank()
        template(res, schema, datas, "each.value", hoisted=hoisted)
        out.append(res.render() + "\n")
    return "\n".join(out)


def apply_addr_map(text, addr_map):
    if not addr_map:
        return text
    pat = re.compile(r"(?<![\w.\"])(" + "|".join(re.escape(k) for k in sorted(addr_map, key=len, reverse=True))
                     + r")(?![\w-])")
    return pat.sub(lambda m: addr_map[m.group(1)], text)


# =========================================================================== main

CONVERTERS = {
    "datastream": (conv_datastream, "GET _data_stream/<pattern>/_stats?human -> kibana_data_view (gegroepeerd)"),
    "kibana_data_view": (conv_kibana_data_view, "GET kbn:/api/data_views[/data_view/<id>] -> kibana_data_view"),
    "index_template": (conv_index_template, "GET _index_template -> elasticsearch_index_template"),
    "component_template": (conv_component_template, "GET _component_template -> elasticsearch_component_template"),
    "ilm": (conv_ilm, "GET _ilm/policy -> elasticsearch_index_lifecycle"),
    "slm": (conv_slm, "GET _slm/policy -> elasticsearch_snapshot_lifecycle"),
    "ingest_pipeline": (conv_ingest_pipeline, "GET _ingest/pipeline -> elasticsearch_ingest_pipeline"),
    "role": (conv_role, "GET _security/role -> elasticsearch_security_role"),
    "role_mapping": (conv_role_mapping, "GET _security/role_mapping -> elasticsearch_security_role_mapping"),
    "cluster_settings": (conv_cluster_settings, "GET _cluster/settings -> elasticsearch_cluster_settings"),
    "connector": (conv_connector, "GET kbn:/api/actions/connectors -> kibana_action_connector"),
    "alerting_rule": (conv_alerting_rule, "GET kbn:/api/alerting/rules/_find -> kibana_alerting_rule"),
    "space": (conv_space, "GET kbn:/api/spaces/space -> kibana_space"),
    "saved_objects": (conv_saved_objects, "POST kbn:/api/saved_objects/_export -> kibana_import_saved_objects"),
}
# connectors vóór rules, zodat rules naar de connector-resources kunnen verwijzen
ORDER = ["space", "connector", "datastream", "kibana_data_view", "component_template", "index_template",
         "ilm", "slm", "ingest_pipeline", "role", "role_mapping", "cluster_settings",
         "alerting_rule", "saved_objects"]

PROVIDER_BLOCK = '''terraform {
  required_providers {
    elasticstack = {
      source  = "elastic/elasticstack"
      version = "~> 0.11"
    }
  }
}

provider "elasticstack" {
  elasticsearch {}
  kibana {}
}
'''

SECRETS_VAR = '''variable "connector_secrets" {
  description = "Secrets per connector-ID als JSON-string, bijv. jsonencode({ webhookUrl = \\"...\\" })"
  type        = map(string)
  sensitive   = true
  default     = {}
}
'''


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("-i", "--input", action="append", default=[],
                   help="Bestand met Dev Tools output (herhaalbaar). Type forceren: -i ilm:bestand.json")
    p.add_argument("-o", "--output", help="Alles in één .tf-bestand")
    p.add_argument("--output-dir", help="Eén .tf-bestand per type in deze map")
    p.add_argument("--list-types", action="store_true", help="Toon ondersteunde types en stop")
    p.add_argument("--only", action="append", default=[], help="Alleen deze types (herhaalbaar)")
    p.add_argument("--space-id", default="default",
                   help="Kibana space als die niet uit de call (kbn:/s/<space>/...) blijkt")
    p.add_argument("--include-managed", action="store_true",
                   help="Ook door Elastic/Fleet beheerde en reserved objecten meenemen")
    p.add_argument("--no-keep-ids", action="store_true",
                   help="rule_id/connector_id niet overnemen (doel genereert nieuwe)")
    p.add_argument("--with-provider", action="store_true", help="terraform/provider block toevoegen")
    p.add_argument("--no-for-each", action="store_true",
                   help="Elk object als los resource-block (zoals vóór v0.4.0) i.p.v. één resource "
                        "met for_each over een locals-map per type")
    p.add_argument("--with-imports", action="store_true", help="import-blocks toevoegen (TF >= 1.5)")
    p.add_argument("--cluster-uuid", help="Cluster UUID voor import-IDs van ES-resources (GET /)")

    g = p.add_argument_group("direct ophalen via de API")
    g.add_argument("--fetch", nargs="*", action="extend", metavar="WAT",
                   help="JSON direct ophalen via de Elasticsearch- en Kibana-API (i.p.v. of naast -i); "
                        "wachtwoord wordt in de terminal gevraagd. Optioneel wat: type (ilm), "
                        "Terraform-resource (elasticstack_elasticsearch_index_lifecycle) of call "
                        "(\"GET _ilm/policy/naam\", \"kbn:/s/ops/api/actions/connectors\"). "
                        "Zonder waarde: [fetch] types uit de config, anders alles.")
    g.add_argument("--config", help="Configbestand met url/username (default: ./elastic2tf.ini, "
                                    "daarna ~/.config/elastic2tf.ini)")
    g.add_argument("--save-export", metavar="BESTAND",
                   help="Opgehaalde responses ook bewaren in Dev Tools-formaat (te hergebruiken met -i)")

    g = p.add_argument_group("data views")
    g.add_argument("--time-field", default="@timestamp")
    g.add_argument("--catch-all", default="logs-*")
    g.add_argument("--no-catch-all", action="store_true")
    g.add_argument("--override", action="store_true", help="override = true op data views")

    p.add_argument("--items", action="append", default=[], metavar="NAMEN",
                   help="Alleen deze objecten omzetten: komma-gescheiden namen, globs mogen "
                        "(hoofdletterongevoelig, herhaalbaar). 'naam' geldt voor alle types, "
                        "'type:naam' alleen voor dat type; het type geldt ook voor de namen erna, "
                        "bijv. 'ilm:logs-*,metrics-*,role:lezer'. "
                        "Exact genoemde objecten gaan ook mee als ze beheerd zijn.")
    p.add_argument("--pipelines", action="append", default=[], metavar="NAMEN",
                   help="Verouderd: gelijk aan --items ingest_pipeline:NAMEN")

    g = p.add_argument_group("saved objects / dashboards")
    g.add_argument("--title-filter", action="append", default=[],
                   help="Alleen dashboards waarvan de titel matcht (glob, hoofdletterongevoelig, "
                        "herhaalbaar), bijv. 'itoa*'. Gerefereerde objecten gaan mee.")
    g.add_argument("--title-filter-type", default="dashboard",
                   help="Type waarop --title-filter werkt (default: dashboard)")

    g = p.add_argument_group("alerting rules")
    g.add_argument("--all-rules", dest="obs_only", action="store_false",
                   help="Niet alleen Observability rules (default: alleen Observability)")
    g.add_argument("--disabled", action="store_true", help="Rules als enabled = false")
    g.add_argument("--no-rule-connectors", action="store_true",
                   help="Connectors van de rules niet automatisch ophalen/meenemen (bijv. als ze al op "
                        "het doel bestaan); rules verwijzen dan via local.connector_ids")
    g.add_argument("--raw-connector-ids", action="store_true")
    g.add_argument("--target-connectors",
                   help="Output van GET kbn:/api/actions/connectors op het DOELcluster; "
                        "connectors worden op ID of naam gekoppeld")
    g.add_argument("--recreate-preconfigured", action="store_true",
                   help="Preconfigured connectors van de bron als gewone connector aanmaken")
    g.add_argument("--report", help="Rapport per rule wegschrijven (.csv voor Excel, anders tekst)")
    g.add_argument("--drop-missing-actions", action="store_true",
                   help="Actions weglaten waarvan de connector niet beheerd wordt én niet op "
                        "het doel bestaat (rule wordt dan zonder die action aangemaakt)")
    g.add_argument("--no-flapping", action="store_true", help="Voor Kibana < 8.16")
    g.add_argument("--no-artifacts", action="store_true", help="Voor Stack < 9.1")
    args = p.parse_args()

    if args.list_types:
        for k in ORDER:
            print(f"  {k:20} {CONVERTERS[k][1]}")
        return

    # ---- inlezen
    items = []  # (type, space, doc, bron)
    sources = args.input or ([] if args.fetch is not None else ["-"])
    if args.save_export and args.fetch is None:
        sys.exit("--save-export werkt alleen samen met --fetch")
    if args.save_export and args.output and \
            os.path.realpath(args.save_export) == os.path.realpath(args.output):
        sys.exit("--save-export en -o mogen niet hetzelfde bestand zijn")

    # voorkom dat de output een inputbestand overschrijft
    def real(pth):
        return os.path.realpath(pth.split(":", 1)[1] if ":" in pth and not os.path.exists(pth)
                                and pth.split(":", 1)[0] in CONVERTERS else pth)
    inputs = {real(s) for s in sources if s != "-"}
    if args.target_connectors:
        inputs.add(real(args.target_connectors))
    if args.output and os.path.realpath(args.output) in inputs:
        sys.exit(f"-o {args.output} is ook een inputbestand en zou overschreven worden. "
                 f"Gebruik een .tf-bestand als output, bijv. -o alerts.tf")
    if args.output and not args.output.endswith(".tf"):
        print(f"Let op: output '{args.output}' eindigt niet op .tf — Terraform leest alleen *.tf-bestanden.",
              file=sys.stderr)
    batches = []  # (bron, geforceerd type, [(hint, doc)])
    if args.fetch is not None:
        cfg_path, cp = read_config(args.config)
        print(f"Ophalen via API (config: {cfg_path})", file=sys.stderr)
        batches.append(("api", None, fetch_all(args, cp)))
    for src in sources:
        forced = None
        if ":" in src and src.split(":", 1)[0] in CONVERTERS and not os.path.exists(src):
            forced, src = src.split(":", 1)
        text = sys.stdin.read() if src == "-" else open(src, encoding="utf-8").read()
        batches.append((src, forced, parse_console_text(text)))
    for src, forced, docs in batches:
        for hint, doc in docs:
            t = forced or detect_type(hint, doc)
            label = f"{src}" + (f" ({hint})" if hint else "")
            if t == "error":
                print(f"Overgeslagen: foutmelding in {label}: "
                      f"{json.dumps(doc.get('error'))[:150]}", file=sys.stderr)
                continue
            if t is None:
                if isinstance(doc, dict):
                    shape = "object met keys: " + ", ".join(list(doc)[:8])
                elif isinstance(doc, list):
                    first = doc[0] if doc else None
                    shape = f"lijst van {len(doc)}" + (
                        f", eerste item keys: {', '.join(list(first)[:8])}" if isinstance(first, dict) else "")
                else:
                    shape = type(doc).__name__
                print(f"Niet ondersteund / niet herkend: {label} — {shape}\n"
                      f"  Tip: type forceren met -i <type>:{src} (zie --list-types)", file=sys.stderr)
                continue
            if args.only and t not in args.only:
                continue
            items.append((t, space_from_hint(hint), doc, label))

    if not items:
        sys.exit("Niets om om te zetten.")

    ctx = Ctx(args)
    if args.target_connectors:
        ctx.target_connectors = load_target_connectors(args.target_connectors)
    if not args.no_rule_connectors:
        scan_rule_connectors(ctx, [doc for it, _, doc, _ in items if it == "alerting_rule"])
    for t in ORDER:
        for it, space, doc, label in items:
            if it == t:
                CONVERTERS[t][0](doc, ctx, space)

    # ---- output samenstellen
    header = f"# Gegenereerd door {SCRIPT_NAME} v{__version__} — niet handmatig aanpassen, opnieuw genereren.\n"
    files, addr_map = {}, {}
    for fname, entries in ctx.blocks.items():
        if args.no_for_each:
            body = "\n".join((f"# {c}\n" if c else "") + b.render() + "\n" for b, c in entries)
        else:
            body = render_for_each(entries, addr_map)
        files[fname] = header + "\n" + resolve_connector_refs(ctx, body)
    loc = connector_locals(ctx)
    if loc:
        files["alerting_rules.tf"] = files.get("alerting_rules.tf", header) .replace(
            header, header + "\n" + loc, 1)
    if ctx.need_secrets_var:
        files["variables.tf"] = header + "\n" + SECRETS_VAR
    if args.with_provider:
        files["provider.tf"] = header + "\n" + PROVIDER_BLOCK
    if ctx.imports:
        files["imports.tf"] = header + "\n" + "\n".join(ctx.imports)
    files = {k: apply_addr_map(v, addr_map) for k, v in files.items()}

    if args.output_dir:
        os.makedirs(args.output_dir, exist_ok=True)
        for fname, content in {**files, **ctx.extra_files}.items():
            with open(os.path.join(args.output_dir, fname), "w", encoding="utf-8") as f:
                f.write(content)
        written = args.output_dir
    else:
        order = ["provider.tf", "variables.tf"] + [k for k in files if k not in
                                                   ("provider.tf", "variables.tf", "imports.tf")] + ["imports.tf"]
        combined = header + "".join(
            f"\n# ---------------------------------------------------------------- {k}\n"
            + files[k].replace(header, "", 1) for k in order if k in files)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(combined)
            base = os.path.dirname(os.path.abspath(args.output))
            for fname, content in ctx.extra_files.items():
                with open(os.path.join(base, fname), "w", encoding="utf-8") as f:
                    f.write(content)
            written = args.output
        else:
            sys.stdout.write(combined)
            written = "stdout"

    # ---- samenvatting
    print(f"\n{SCRIPT_NAME} v{__version__} — geschreven naar {written}:", file=sys.stderr)
    for t in ORDER + ["datastream_data_view"]:
        made, skipped = ctx.stats.get(t, [0, 0])
        if made or skipped:
            extra = f" ({skipped} overgeslagen: beheerd/reserved/gefilterd)" if skipped else ""
            print(f"  {t:22} {made}{extra}", file=sys.stderr)
    if ctx.stats["_dv_no_time"][0]:
        print(f"  Let op: {ctx.stats['_dv_no_time'][0]} data views uit de lijst-API hebben geen tijdveld-info; "
              f"'{args.time_field}' is aangenomen. Gebruik GET kbn:/api/data_views/data_view/<id> voor exacte waarden.",
              file=sys.stderr)
    if ctx.item_patterns:
        missing = [p for pats in ctx.item_patterns.values() for p in pats if p not in ctx.item_matched]
        if missing:
            ctx.warn(f"--items: niets gevonden voor: {', '.join(dict.fromkeys(missing))}")
        for kind, n in sorted(ctx.item_unselected.items()):
            print(f"  {kind}: {n} niet geselecteerd door --items", file=sys.stderr)
    if ctx.conn_unused:
        print(f"  connector: {ctx.conn_unused} niet meegenomen (niet gebruikt door de geselecteerde rules)",
              file=sys.stderr)
    for w in ctx.warnings:
        print(f"  Let op: {w}", file=sys.stderr)

    report_rules(ctx)

    unresolved = [c for c in ctx.connector_refs
                  if c not in ctx.connector_addr and target_id_for(ctx, c) is None]
    if unresolved:
        print(f"\n  CONNECTORS: {len(unresolved)} connector(s) waar rules naar verwijzen worden niet door "
              f"deze Terraform aangemaakt" + (" en bestaan niet op het doelcluster:" if ctx.target_connectors
                                               else " (niet gecontroleerd op het doel):"), file=sys.stderr)
        for cid in sorted(unresolved):
            _, status = connector_status(ctx, cid)
            rules = sorted(n for n in ctx.connector_refs[cid]["rules"] if n)
            print(f"    - {cid}: {status}\n      gebruikt door {len(rules)} rule(s): "
                  f"{', '.join(rules)[:150]}", file=sys.stderr)
        print("    Oplossing: neem GET kbn:/api/actions/connectors mee in de input, "
              "gebruik --target-connectors, --recreate-preconfigured of --drop-missing-actions.",
              file=sys.stderr)
    if ctx.dropped_actions:
        print(f"\n  {len(ctx.dropped_actions)} action(s) weggelaten (--drop-missing-actions):", file=sys.stderr)
        for rule, cid in ctx.dropped_actions:
            print(f"    - {rule}: connector {cid}", file=sys.stderr)
    if ctx.need_secrets_var:
        print("  Connector-secrets invullen via var.connector_secrets (bijv. in een *.auto.tfvars, niet in git).",
              file=sys.stderr)


if __name__ == "__main__":
    main()

