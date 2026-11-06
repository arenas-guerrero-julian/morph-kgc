# CLAUDE.md

Morph-KGC is a Python engine that turns heterogeneous data into RDF knowledge graphs by running R2RML and RML mappings over pandas DataFrames. It accepts R2RML, legacy RML (semweb.mmlab.be), RML 1.2 / RML-Core and YARRRML mappings. All of them are normalized into a single RML 1.2 representation, which is a flat list of `RMLRule` dataclasses. The rules are then partitioned into groups that cannot produce the same triple, and each group is materialized independently, in parallel on Linux. Output can be a file, a `set[str]`, an rdflib Graph/Dataset or a pyoxigraph Store. User documentation lives in a separate repo, **morph-kgc/morph-kgc-docs** (see CONTRIBUTING.md); update it when you change behaviour that users can see.

## Commands

```bash
pip install -e '.[test]'          # CI: uv pip install .[test]. If odfpy fails to build (install_layout error): pip install --use-pep517 odfpy, then retry
python -m pytest -q               # full suite. ALWAYS run from the repo root: ~180 mapping files use CWD-relative "test/..." rml:source paths
python -m pytest -q test/rml-core/json                                                     # one suite
python -m pytest -q test/issues/issue_145                                                  # one case dir
python -m pytest test/rml-core/csv/RMLTC0001a/test_RMLTC0001a_CSV.py::test_RMLTC0001a      # one test (-k RMLTC0001a matches the case in ~8 suites)
python -m morph_kgc config.ini    # CLI (also the `morph-kgc` console script, `-v` prints the version); output_format=JELLY needs morph-kgc[jelly]
python -m morph_kgc.bootstrapping config.ini   # direct-mapping YARRRML generator, see Gotchas
```

- Use `python -m pytest`, not a bare `pytest`, so the tests run with the interpreter that has `morph_kgc` installed.
- The full suite gives about 641 passed and 1 skipped (issue_350, needs pyjelly) in about 4 minutes on 4 CPUs. No test takes more than about 2 s.
- No test needs the real network or a database server. Relational tests use bundled SQLite `.db` files, and the HTTP tests start local servers.
- A full run prints about 9k DeprecationWarnings from rdflib, jsonpath and fork. They are expected; add `-p no:warnings` for readable output.
- pytest-xdist and pytest-timeout are not installed, so `-n` and `--timeout` fail.
- Test runs leave no files in the repo.
- Example configs in `examples/` use paths relative to their own directory, so run them from there. Set `output_file` outside the repo, because the default output is `knowledge-graph.nt` in the CWD.

Library usage (`src/morph_kgc/__init__.py`):
- `morph_kgc.materialize(config, python_source=None)` returns an rdflib `Graph`, or a `Dataset` when output_format is N-QUADS.
- `materialize_set(...)` returns `set[str]`. The statements have no trailing ` .`, and default-graph N-Quads strings end with a trailing space.
- `materialize_oxigraph(...)` returns a `pyoxigraph.Store`.
- `config` can be an INI path, an INI string, a dict or a `MorphConfig`.

CI (`.github/workflows/ci.yml`) runs `pytest` on ubuntu, windows and macos with Python 3.12 and 3.13. pyproject declares `>=3.10`. The version lives in `src/morph_kgc/_version.py` (hatchling).

## Architecture (end to end)

1. **Config** (`config/loaders.py`, `config/model.py`, `config/defaults.py`). `load_config` dispatches on the input type:
   - `MorphConfig` is returned as is.
   - `dict` goes to `load_from_dict`.
   - `PathLike` goes to `load_from_file`.
   - `str` goes to `load_from_file` if the path exists, otherwise to `load_from_string`.

   Each loader fills a `ConfigParser(ExtendedInterpolation)` and calls `_build_config`. That reads `[CONFIGURATION]`, creates a `DataSourceConfig` for every other section, and a `ResourceConfig` for each `[RESOURCE:<name>]` section. It ends with `MorphConfig(**cfg)`, whose `__post_init__` normalizes, validates and configures the `morph_kgc` logger. Then `__init__._apply_library_process_guard` forces `number_of_processes=1` on non-Linux platforms.

2. **Single entry point**: `materializer/pipeline.py:materialize_pipeline(config, python_source, executor, output)`. Every API path and the CLI go through it. `output` is `'file'`, `'set'`, `'graph'` or `'oxigraph'`.

3. **Mapping parsing** (`mapping/parser.py:MappingParser.parse_mappings`). For each data-source section:
   - `_load_mapping_graph` merges all of the section's files into one rdflib graph. `.yarrrml`, `.yml` and `.yaml` files go to `formats/yarrrml.py:load_yarrrml`. Every other file goes to `formats/rml.py:load_rml_graph`, which rewrites legacy predicates.
   - `formats/r2rml.py:r2rml_to_rml` runs on every graph.
   - `normalizer.py:normalize_mapping_graph` does the graph-level rewrites.
   - `_translate_fnml_to_rml` handles old-style FNML.
   - `validate_mapping` runs next.
   - `_graph_to_rml_mapping` runs the big SPARQL query `_RML_PARSING_QUERY` and produces an `RMLMapping` (`mapping/model.py`). It holds a flat `list[RMLRule]` (one rule per TM × POM × predicateMap × objectMap × graphMap × rdf:type), `fnml_executions` and `http_api_entries`.

   After that come `_preprocess_mappings` (dedup, the config `file_path` override, `_complete_source_types` which sets `LogicalSource.format_`, delimiter stripping and self-join removal), `_infer_datatypes` (RDB only), `validate_mappings` and finally `MappingPartitioner.partition_mappings()`. **Partitioning happens inside parsing**: it sets `rule.mapping_partition`, labels of the form `'S-P-O-G'`.

4. **Execution**:
   - `_asserted_groups` buckets the rules by partition and keeps only `triples_map_type == rml:TriplesMap`. Non-asserted TMs are never materialized on their own. They are read only as `rml:tripleTermMap` targets or as `rml:parentTriplesMap` parents (their subject map only).
   - `executor.py:make_executor` picks `MultiprocessExecutor` (an mp.Pool with a dead-worker watchdog) only when `number_of_processes>1` and on Linux. Otherwise it picks `SequentialExecutor`.
   - `functions/state.py:initialize_contexts` builds the contexts of stateful functions; `release_contexts` runs in a `finally` block.

5. **Per rule** (`pipeline._materialize`, stages in `materializer/stages/`):
   - `references.collect_references` lists the source columns the rule needs.
   - `load.load_data` calls `source.get_adapter(format_).get_data(...)`, then turns every value into `str`, drops rows holding na_values and drops duplicates.
   - For a referencing object map, `join.merge_data` inner-joins child and parent. Parent columns get the `parent_` prefix.
   - `terms.materialize_terms` fills the `subject`, `predicate` and `object` columns: IRI encoding, literal escaping, lang/datatype/direction, FNML and `<<( s p o )>>` triple terms.
   - `assemble.assemble_triples` builds the `triple` column. The graph term is added only when the output is N-QUADS; with N-TRIPLES, graph maps are ignored.

   A rule with no references does not read its source; it materializes from a single dummy row.

6. **Output**:
   - `'file'`: `utils.prepare_output_files` deletes the old files, then each group appends `triple.rstrip()+' .\n'` to `config.get_output_file_path(partition)`. Duplicates are removed only inside a group, so partition disjointness is what keeps the file correct.
   - Other modes: `_collect` unions the group sets, then `_triples_to_graph` (parses as nquads) or `_triples_to_oxigraph` runs.
   - The CLI (`__main__.py`) uses `'file'`, except JELLY, which builds a graph and calls `graph.serialize(format='jelly')`.

## Subsystem map

| Path | Contents |
|---|---|
| `src/morph_kgc/__init__.py`, `__main__.py` | Public API wrappers and the CLI |
| `config/` | `loaders.py` (INI/dict/CLI to config), `model.py` (`MorphConfig`, `DataSourceConfig`, `ResourceConfig`, accessors such as `get_db_url(section)`, `has_file_path`, `get_output_file_path`, `find_resource`), `defaults.py` |
| `constants/` | Vocabulary and option names, star-re-exported by `constants/__init__.py` (`from ..constants import X`) |
| `mapping/` | `parser.py` (orchestrator plus SPARQL), `normalizer.py`, `model.py` (dataclasses), `partitioner.py`, `formats/{rml,r2rml,yarrrml}.py` |
| `materializer/` | `pipeline.py`, `executor.py`, `stages/{load,references,join,terms,assemble}.py` |
| `source/` | Adapter registry in `protocol.py`, registration in `__init__.py`; `file.py` (CSV/Excel/Parquet/JSON/XML/geo/DuckDB), `relational.py` (SQLAlchemy), `property_graph.py` (Neo4j), `http_api.py`, `python_data.py` (in-memory) |
| `functions/` | FNML: `bif_decorator.py`, `registry.py`, `executor.py`, `param_resolver.py`, `state.py`, `tables.py` (DuckDB/Parquet), `udf_loader.py`, `grel/*_functions.py`, `grel/safe_eval.py`, `reconciliation.py` |
| `reconciliation/` | SKOS and SPARQL index builders (`skos.py`, `sparql.py`, `index.py`) |
| `http.py` | stdlib HTTP client used only by HTTP API sources and reconciliation |
| `testing.py` | `assert_isomorphic` (RDFC-1.0 canonicalization through pyoxigraph; compares graph names and blank nodes) |
| `bootstrapping/` | Generates direct-mapping YARRRML from an RDB schema |

## How to add common things

**New CONFIGURATION option** (touch all five places, or the option is silently dropped):
1. Add a `DEFAULT_*` constant in `config/defaults.py`.
2. Add a `MorphConfig` field in `config/model.py`, named exactly like the INI key (values are passed as `**kwargs`).
3. Add the key to `_CONFIGURATION_KEYS` in `config/loaders.py`, which flat dicts need.
4. Read it in `_extract_configuration_options` with `get`, `get_nullable`, `getbool` or `getint`.
5. Validate it in `MorphConfig._normalize_and_validate`.

Then document it in `examples/configuration-file/default_config.ini`. That file is only an example; the defaults actually used come from `config/defaults.py`, and the two have drifted (for example, `na_values`).

**New per-data-source key**: add a `DataSourceConfig` field, add the key to `_DATA_SOURCE_KEYS` and `_extract_data_sources`, and expose it through a `MorphConfig` accessor that takes the section name. Callers look it up via `rule.logical_source.config_section_name`.

**New source type**:
1. Add the type string to `constants/sources.py`. If the type is detected by file extension, also add it to `FILE_SOURCE_TYPES`. That registers it to `FileAdapter`, so you must add a branch in `FileAdapter.get_data` (`source/file.py`), or it raises "unrecognised file source type".
2. For non-file kinds, write a class with `get_data(self, config, rml_rule, references, python_source=None, rml_mapping=None) -> pd.DataFrame` and register it in `source/__init__.py` with `register_adapter(TYPE, instance)`. Keys are uppercased.
3. Make `mapping/parser.py:_complete_source_types` produce that string. A reference formulation outside the rml: namespace becomes its whole IRI uppercased. Database-like sources go next to the db_url/CYPHER/GQL branch.
4. The returned DataFrame's column names must equal the reference strings exactly.
5. Import optional libraries lazily inside the function, and add an extra in pyproject (plus the `all` extra).

**New built-in function**: in `functions/grel/<family>_functions.py`, add `@bif(fun_id="<IRI>", kwarg="<param IRI>" | (alias IRIs...))`. Import it with `from ..registry import bif`.
- Optional params need Python defaults.
- Return a scalar, a list (exploded into several triples) or `None` (no triple). Returning `''` also drops the row.
- A new module registers only if it is imported: add it to `grel/__init__.py`, or add `from . import x` in `functions/registry.py`.
- `_register` silently overwrites an existing IRI, so do not reuse one.
- For a stateful function, use `@stateful_bif(fun_id, initializer, context_parameter='context', vectorized=...)`. The context must be picklable. Vectorized functions get per-row lists and must return exactly `len(rows)` results.
- Shared IRIs go in `constants/functions.py`.

**New mapping construct / term-map field**:
1. Add the URI to `constants/rml.py`.
2. Add the variable to `_RML_PARSING_QUERY` (including the FILTER IN lists).
3. Add the field to `TermMap` in `mapping/model.py`.
4. Populate it in `_graph_to_rml_mapping`.
5. Add it to `_get_term_map_identity`, or `_drop_duplicates` merges rules that differ only in that field.
6. Downstream, declare the columns it needs in `stages/references.py` and dispatch it in `stages/terms.py` (`_write_term` / `_write_object_term`).
7. If it affects grouping, add an invariant in `partitioner.py`.

Other related changes:
- Shortcut properties: the dict in `normalizer._expand_constant_shortcut_properties`.
- Legacy or R2RML aliases: `_LEGACY_PREDICATE_MAP` in `formats/rml.py`, or `_R2RML_TO_RML_*` in `formats/r2rml.py`. Not `normalizer.rml_legacy_to_rml`, which is dead code.
- Semantic validation: add a method called from `MappingParser.validate_mappings`.

**New output format**: add it to `VALID_OUTPUT_FORMATS` and `OUTPUT_FORMAT_FILE_EXTENSION` in `config/model.py`, which is the copy that is actually validated, and keep `constants/output.py` in sync. If the format cannot be written line by line, add a CLI branch in `__main__.py` like the JELLY one. A new library return type is a branch at the end of `materialize_pipeline` plus a wrapper in `__init__.py`.

**Regression test for issue N**: create `test/issues/issue_N/` holding the data, `mapping.ttl` with `rml:source` relative to the repo root (e.g. `"test/issues/issue_N/data.csv"`), `output.nq` and `test_issue_N.py`. Copy `test/issues/issue_145/test_issue_145.py`: parse `output.nq` into an `rdflib.Dataset`, build an inline INI string with absolute paths built from `os.path.dirname(os.path.realpath(__file__))`, call `morph_kgc.materialize`, then `assert_isomorphic(expected, actual)`.

## Testing conventions

- Each case is a directory: `test/<suite>/<CASEID>/` holding `mapping.ttl` (or `.yml`/`.yarrrml`/`.rml`), the data, `output.nq` and `test_<CASEID>[_<FORMAT>].py`. The test function is named `test_<CASEID>`. The config is always an inline INI string.
- **There are no `__init__.py` files under test/, so every `test_*.py` basename must be unique repo-wide.** Suite suffixes (`_CSV`, `_JSON`, `_XML`, `_SQLITE`, `_DICT`, `_DF`...) keep them unique.
- Suites:
  - `rml-core/{csv,json,xml,tabular}`
  - `r2rml` (SQLite `resource.db`, `infer_sql_datatypes=yes`)
  - `rml-tv` (DuckDB `rml:query` over files)
  - `rml-in-memory` (`materialize(config, {'variable1': obj})` with `sd:name`)
  - `rml-fnml`: one directory per function case, grouped by family. `reconciliation/` starts local 127.0.0.1 HTTP servers, and `stateful_udf/` tests the worker lifecycle.
  - `http-api` (local `api_server.py`)
  - `yarrrml` (`test_yarrrml_translation.py` unit tests)
  - `geo`
  - `issues`
- `test/rml12`: rdflib 7 cannot parse RDF 1.2 triple terms or `@en--ltr`, so these tests compare N-Triples lines through the `rml12` fixture in `test/rml12/conftest.py` (`rml12.assert_matches(__file__, 'output.nt', 'N-TRIPLES')`, `assert_rejected`, `materialize_within`). Add a row to `test/rml12/README.md` for each new case.
- Expected errors: use `pytest.raises` or `rml12.assert_rejected`. Do not copy the legacy `try: ...; assert False / except: assert True` pattern. It always passes, because the bare `except:` also catches the `AssertionError` (49 test files use it).
- Optional dependencies outside `[test]`: use `pytest.importorskip` (see issue_350 / pyjelly). Network: never call real hosts; start a local `http.server` in a thread, as `test/rml-fnml/reconciliation/server.py` and `test/http-api/api_server.py` do.
- FNML execution ids are random blank-node ids that change on every parse; never hard-code them.
- Disabled tests:
  - 21 files lack the `test_` prefix and are not collected: `rml-core/{csv,json,xml}/RMLTC0019a/b` and `0020a/b`, plus `r2rml/R2RMLTC00{03a,16b-e,19a,19b,20a,20b}`.
  - All `test/direct-graph` modules are wrapped in a string, so bootstrapping has no running tests.

## Gotchas

**Config loading**
- A mistyped config path given as a `str` is parsed as INI text and fails with `MissingSectionHeaderError`. A missing `Path` silently loads zero data sources. Any config that yields zero rules then fails in the partitioner with the confusing `ValueError: max() iterable argument is empty`.
- ExtendedInterpolation applies: a literal `$` (for example in a password) must be written `$$`.
- Only an exact `[CONFIGURATION]` section is read; `[configuration]` is silently ignored.
- Unknown keys are silently ignored everywhere. Every non-CONFIGURATION, non-`RESOURCE:` section becomes a data source.
- Dict configs: values are `str()`-ed, so `mappings` must be a comma-separated string. Mixing top-level scalars with section dicts crashes.
- Mapping paths resolve against the CWD, not the config file's directory. A directory entry adds all files directly inside it (non-recursive), and an unknown extension is parsed as Turtle.
- `db_url` expands `{VAR}` with `str.format(**os.environ)`: an unset variable raises KeyError and literal braces break it.
- `ResourceConfig` expands variables lazily. Credentials written inside URLs are rejected by `http.py`; put them in `[RESOURCE:<name>]` sections.
- `get_output_file_path` forces the extension (`kg.ttl` becomes `kg.nt`). With `output_dir`, each partition is written to `<output_dir>/<S-P-O-G>.<ext>`. File mode deletes existing output first.
- `MorphConfig` construction configures the logger only once per process, so a later config's `logging_file` is ignored. `_apply_library_process_guard` mutates a passed-in `MorphConfig`.
- `na_values` defaults to `",nan"` (`DEFAULT_NA_VALUES`). An empty setting becomes `['']`, so NA handling can never be switched off completely. To keep empty strings, list only other values (e.g. `na_values=nan`). Any NULL in any referenced column drops the row for that rule.

**Mapping semantics**
- A TM typed with an extra class (`a rml:TriplesMap, ex:Other`) gets duplicate rules that are never materialized.
- A TM with no POM is re-typed `rml:NonAssertedTriplesMap`. `rml:class` counts as a POM.
- `normalizer.remove_string_datatypes` deletes every `(?, rml:constant, xsd:string)` triple. That erases `xsd:string` datatypes, and also any constant term whose value is the IRI `xsd:string`.
- Legacy `ql:` reference formulations are never translated (`QL_*` in `constants/rml_legacy.py` is unused). Legacy JSON/XML sources work only because the file extension is checked first. Without a known extension, the source type becomes the uppercased `ql:` IRI and `get_adapter` raises KeyError.
- Delimited SQL identifiers are stripped; downstream code assumes bare column names.
- Triples-map IDs must be unique across sections. Files within one section are merged, so the same TM IRI in two files becomes one TM.
- Legacy RML coverage is partial (for example, legacy `rml:languageMap` is silently dropped). Remote YARRRML URLs are unsupported (the loader uses `open()`).
- `rml:query` without a section `db_url` becomes format `CSV` and runs through DuckDB, with paths relative to the CWD. With `db_url`, the format is always `RDB` (or `PGDB` for CYPHER/GQL); the dialect registrations (MYSQL...) are never reached through the parser.
- In-memory sources work only through the `rml:DataFrame` / `rml:Dictionary` reference formulations. The `startswith('{')` branch in `_complete_source_types` is dead code.
- A config `file_path` overrides the source of every rule in its section.

**Sources**
- The JSON reading logic exists in three drifted copies: `file._read_json`, `python_data._read_inmemory_json` and `http_api._fetch_http_api`. A fix in one usually belongs in all three.
- JSON, XML and HTTP sources require `rml:iterator`.
- XML references use ElementTree `findall`, not full XPath.
- Remote JSON and XML are fetched with plain `urllib`; CSV and Parquet URLs go to pandas. None of these use `http.py`.
- HTTP API: only Authorization, Accept, KeyId and User-Agent are sent as headers; every other field is sent as a query parameter.
- In-memory DataFrames are altered: `PythonDataAdapter.get_data` removes every `"` from string values.
- `connect_args` in a data-source section is parsed with `ast.literal_eval`, so it must be a Python dict literal.
- Sources are re-read for every rule and every join parent; nothing is cached. All values become `str`, so join keys are compared as strings.

**Partitioning and execution**
- Groups must never be able to emit the same triple. File output has no cross-group dedup, so a partitioner change that breaks disjointness produces duplicate lines.
- The default `number_of_processes` is 2×cpu_count on every platform. Library calls on non-Linux cap it at 1, and `make_executor` uses `MultiprocessExecutor` only on Linux. The start method is never set, so Python's default applies (fork on Linux up to 3.13).
- `MultiprocessExecutor` pickles `rml_mapping`, `config` and `python_source` for each group, and silently drops any extra kwargs.
- MAXIMAL partitioning opens its own Pool during parsing, with no platform check.
- `'file'` mode never forwards `python_source`, so in-memory sources cannot be used with file output.
- `AsyncExecutor` is never selected by `make_executor`.

**Functions**
- Registration is an import side effect, and `registry.py` must star-import `bif_decorator` before importing `.grel`.
- Built-ins shadow UDFs that use the same IRI.
- UDF files are cached per path for the life of the process.
- Unknown function IRIs raise KeyError only when a rule using them executes.
- Parameter IRIs the decorator does not declare are silently ignored.
- Function-valued object maps default to literals; reconciliation needs `rml:termType rml:IRI`.
- Known broken built-in: `built-in.ttl#hash_iri` raises NameError, because `sha256` is imported only inside another function in `grel/other_functions.py`.
- Several IRIs are registered twice, and the later definition wins: math_mod, string_contains, string_trim, string_md5, string_sha1, unicodestring-s.
- Python function names do not always match the IRI. For example, `string_chomp` is implemented by a function named `string_contains`.
- Never `eval` data. Use `grel/safe_eval.safe_eval_expression`.
- Stateful functions work only through `materialize_pipeline` (the library API or the CLI). Calling `materialize_rule` or `execute_fnml` directly raises FileNotFoundError. `initialize_contexts` temporarily rewrites `config.state_dir`.

**Bootstrapping**
- `bootstrapping/__main__.py` uses its own plain `ConfigParser` (no ExtendedInterpolation, no `MorphConfig`).
- `db_url` must be in a section named exactly `[DataSource1]`. Options go in `[BOOTSTRAPPING]` (`base_iri`, `build_mappings`).
- It writes `direct_mapping.yaml` to `output_dir`, or to the CWD.
- It needs SQLAlchemy and PyYAML, which is not declared as a dependency.

**Packaging**
- The Dockerfile is stale: it uses Python 3.9, while the project requires `>=3.10`.
- `utils.triples_to_file` and `utils.configure_logger` are dead code.
- The `examples/` scripts are not tested; `examples/dict` is broken as shipped.

## Code conventions

- Module header dunders: `__author__` and `__license__ = "Apache-2.0"` (older files also have `__credits__`, `__maintainer__` and `__email__`). Newer modules add `from __future__ import annotations` and a long layout docstring. Sections are separated by banner comments (`# ── Name ──`).
- Logging: `LOGGER = logging.getLogger(LOGGING_NAMESPACE)` with f-strings, INFO for progress and DEBUG for details.
- Compare RML IRIs with constants (`RML_TEMPLATE`, `RML_IRI`...), never with string literals. Vocabulary constants are named `<NS>_<NAME>`, with `_SHORTCUT` and `_CLASS` suffixes.
- Mapping rule dataclasses (`TermMap`, `LogicalSource`, `RMLRule`...) are `@dataclass(slots=True)` and hold only plain `str`. The `RMLMapping` container is a plain dataclass. nothing downstream of `mapping/model.py` needs rdflib.
- Graph transforms have the shape `f(graph) -> graph`, mutate in place, and wrap SPARQL results in `list(...)` before mutating.
- DataFrame stages use fixed column names: `subject`, `predicate`, `object`, `_graph`, `triple`. Temporary columns start with `_`, and joined parent columns carry the `parent_` or `parent{n}_` prefix.
- Errors are built-in exceptions with actionable messages: ValueError for user input, quoting the option, IRI or section involved. Semantic mapping validation in the parser raises a bare `Exception` that names the TM. Neither the source package nor the materializer defines custom exception classes.
- Optional dependencies are imported lazily, with an install hint such as `pip install 'morph-kgc[jelly]'`.
- Functions passed to `mp.Pool` must be module-level so they can be pickled.
- Comments cite issue numbers (e.g. `# issue #81`).
- PRs target `main`; include a unit test where possible.