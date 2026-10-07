__author__ = "Julián Arenas-Guerrero"
__credits__ = ["Julián Arenas-Guerrero"]

__license__ = "Apache-2.0"
__maintainer__ = "Julián Arenas-Guerrero"
__email__ = "arenas.guerrero.julian@outlook.com"


import os
import sys

import morph_kgc
import pytest

from rdflib import Dataset
from morph_kgc.testing import assert_isomorphic

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from server import PASSWORD, USERNAME, VocabularyServer   # noqa: E402

TEST_DIR = os.path.dirname(os.path.realpath(__file__))
MAPPING = os.path.join(TEST_DIR, 'mapping.ttl')
VOCABULARY = os.path.join(TEST_DIR, 'disease_vocabulary.ttl')


def config(resource_options, mapping=MAPPING, processes=1):
    return (
        f'[CONFIGURATION]\n'
        f'output_format=N-QUADS\n'
        f'number_of_processes={processes}\n'
        f'[RESOURCE:disease_vocabulary]\n'
        f'{resource_options}\n'
        f'[DataSource]\n'
        f'mappings={mapping}'
    )


def expected_graph():
    g = Dataset()
    g.parse(os.path.join(TEST_DIR, 'output.nq'))
    return g


# ── SKOS vocabulary ───────────────────────────────────────────────────────────

def test_reconcile_vocabulary_from_file():
    """A vocabulary declared as a local path is indexed and reconciled against."""
    g_morph = morph_kgc.materialize(config(
        f'resource_type=SKOS_VOCABULARY\n'
        f'url={VOCABULARY}'
    ))

    assert_isomorphic(expected_graph(), g_morph)


def test_reconcile_vocabulary_over_http_with_basic_authentication():
    """The vocabulary is fetched once, with HTTP Basic Authentication."""
    with VocabularyServer() as server:
        g_morph = morph_kgc.materialize(config(
            f'resource_type=SKOS_VOCABULARY\n'
            f'url={server.url}/vocabulary\n'
            f'username={USERNAME}\n'
            f'password={PASSWORD}\n'
            f'format=turtle'
        ))

        # Initialized once, before any triple was materialized, even though the
        # mapping reconciles in four mapping groups.
        assert [path for path, _ in server.requests] == ['/vocabulary']

    assert_isomorphic(expected_graph(), g_morph)


def test_reconcile_vocabulary_over_http_without_credentials():
    """A protected vocabulary reports what is missing instead of failing blindly."""
    with VocabularyServer() as server:
        with pytest.raises(ValueError, match='Basic Authentication'):
            morph_kgc.materialize(config(
                f'resource_type=SKOS_VOCABULARY\n'
                f'url={server.url}/vocabulary'
            ))


@pytest.mark.parametrize('file_name', ['disease_vocabulary.nq', 'disease_vocabulary.trig'])
def test_reconcile_vocabulary_in_named_graphs_from_file(file_name):
    """A vocabulary serialized as quads is indexed across all its named graphs."""
    g_morph = morph_kgc.materialize(config(
        f'resource_type=SKOS_VOCABULARY\n'
        f'url={os.path.join(TEST_DIR, file_name)}'
    ))

    assert_isomorphic(expected_graph(), g_morph)


@pytest.mark.parametrize('path', ['/vocabulary.nq', '/vocabulary.trig'])
def test_reconcile_vocabulary_in_named_graphs_over_http(path):
    """
    A vocabulary served as quads is indexed across all its named graphs, even
    when served as text/plain: the extension then says it holds quads.
    """
    with VocabularyServer() as server:
        g_morph = morph_kgc.materialize(config(
            f'resource_type=SKOS_VOCABULARY\n'
            f'url={server.url}{path}\n'
            f'username={USERNAME}\n'
            f'password={PASSWORD}'
        ))

    assert_isomorphic(expected_graph(), g_morph)


def test_reconcile_vocabulary_with_multiple_processes():
    """Worker processes read the shared context back from the state directory."""
    g_morph = morph_kgc.materialize(config(
        f'resource_type=SKOS_VOCABULARY\n'
        f'url={VOCABULARY}',
        processes=4,
    ))

    assert_isomorphic(expected_graph(), g_morph)


def test_reconcile_vocabulary_referenced_by_its_iri():
    """
    A mapping may name the vocabulary by the IRI that identifies it, while
    where it is downloaded from stays in the configuration file.
    """
    g_morph = morph_kgc.materialize(config(
        f'resource_type=SKOS_VOCABULARY\n'
        f'iri=https://example.org/vocabulary/disease\n'
        f'url={VOCABULARY}',
        mapping=os.path.join(TEST_DIR, 'mapping_vocabulary_iri.ttl'),
    ))

    assert_isomorphic(expected_graph(), g_morph)


def test_reconcile_case_insensitive_matching():
    """Case-insensitive matching reconciles values that differ in case only."""
    g_morph = morph_kgc.materialize(config(
        f'resource_type=SKOS_VOCABULARY\n'
        f'url={VOCABULARY}\n'
        f'matching=CASE-INSENSITIVE',
        mapping=os.path.join(TEST_DIR, 'mapping_uppercase.ttl'),
    ))

    g = Dataset()
    g.parse(os.path.join(TEST_DIR, 'output_reconciled.nq'))

    assert_isomorphic(g, g_morph)


def test_reconcile_from_yarrrml():
    """The same reconciliation expressed in YARRRML instead of RML-FNML."""
    g_morph = morph_kgc.materialize(config(
        f'resource_type=SKOS_VOCABULARY\n'
        f'url={VOCABULARY}',
        mapping=os.path.join(TEST_DIR, 'mapping.yarrrml'),
    ))

    g = Dataset()
    g.parse(os.path.join(TEST_DIR, 'output_reconciled.nq'))

    assert_isomorphic(g, g_morph)


def test_exact_matching_by_default():
    """Exact matching is the default, so a value in another case matches nothing."""
    g_morph = morph_kgc.materialize(config(
        f'resource_type=SKOS_VOCABULARY\n'
        f'url={VOCABULARY}',
        mapping=os.path.join(TEST_DIR, 'mapping_uppercase.ttl'),
    ))

    assert len(g_morph) == 0


def test_undeclared_resource():
    """A mapping reconciling against an undeclared resource says so."""
    with pytest.raises(ValueError, match='not declared in the configuration'):
        morph_kgc.materialize(
            f'[CONFIGURATION]\n'
            f'output_format=N-QUADS\n'
            f'[DataSource]\n'
            f'mappings={MAPPING}'
        )


def test_wrong_resource_type():
    """Reconciling a vocabulary against a SPARQL endpoint resource is reported."""
    with pytest.raises(ValueError, match='resource_type'):
        morph_kgc.materialize(config(
            f'resource_type=SPARQL_ENDPOINT\n'
            f'url={VOCABULARY}'
        ))


# ── SPARQL endpoint ───────────────────────────────────────────────────────────

SPARQL_MAPPING = os.path.join(TEST_DIR, 'mapping_sparql.ttl')
# The endpoint the mappings name, as in the discussion that requested the function.
MAPPING_ENDPOINT = 'https://data.com/database/query'

STUDIES_QUERY = """
            PREFIX schema: <https://schema.org/>
            SELECT ?entity_iri ?matching_value_1 WHERE {
                GRAPH ?g {
                    ?entity_iri a schema:MedicalStudy ;
                                schema:identifier ?matching_value_1 .
                }
            }
        """

DRUGS_QUERY = """
            PREFIX schema: <https://schema.org/>
            SELECT ?entity_iri ?matching_value_1 WHERE {
                GRAPH ?g { ?entity_iri a schema:Drug ; schema:name ?matching_value_1 . }
            }
        """


def sparql_config(resources='', mapping=SPARQL_MAPPING, processes=1):
    return (
        f'[CONFIGURATION]\n'
        f'output_format=N-QUADS\n'
        f'number_of_processes={processes}\n'
        f'{resources}\n'
        f'[DataSource]\n'
        f'mappings={mapping}'
    )


def endpoint_resource(url, extra_options='', name='clinical_trials', iri=MAPPING_ENDPOINT):
    """A resource declaring the credentials of the endpoint the mapping names."""
    return (
        f'[RESOURCE:{name}]\n'
        f'resource_type=SPARQL_ENDPOINT\n'
        f'{f"iri={iri}" if iri else ""}\n'
        f'url={url}\n'
        f'username={USERNAME}\n'
        f'password={PASSWORD}\n'
        f'{extra_options}'
    )


def sparql_mapping(tmp_path, *replacements, mapping=SPARQL_MAPPING):
    """A copy of *mapping* with each (old, new) replacement applied."""
    with open(mapping, encoding='utf-8') as f:
        text = f.read()
    for old, new in replacements:
        assert old in text
        text = text.replace(old, new)
    path = tmp_path / os.path.basename(mapping)
    path.write_text(text, encoding='utf-8', newline='\n')
    return str(path)


def expected_sparql_graph(file_name='output_sparql.nq'):
    g = Dataset()
    g.parse(os.path.join(TEST_DIR, file_name))
    return g


@pytest.mark.parametrize('method', ['GET', 'POST'])
def test_reconcile_entity_over_sparql(method):
    """
    The query of the mapping is sent once, as written, with the credentials the
    configuration file declares for the endpoint, and its answer is reused for
    every value.
    """
    with VocabularyServer() as server:
        g_morph = morph_kgc.materialize(sparql_config(
            endpoint_resource(f'{server.url}/sparql', f'method={method}')
        ))

        assert server.requests == [('/sparql', STUDIES_QUERY)]
        assert server.methods == [method]

    assert_isomorphic(expected_sparql_graph(), g_morph)


def test_reconcile_mapping_of_the_discussion():
    """
    The mapping of the feature request, written in the legacy FNML vocabulary
    and naming the function with the morph-fn prefix, runs as written against a
    store whose default graph includes the named graphs.
    """
    with VocabularyServer() as server:
        g_morph = morph_kgc.materialize(sparql_config(
            endpoint_resource(f'{server.url}/sparql/union'),
            mapping=os.path.join(TEST_DIR, 'mapping_sparql_discussion.ttl'),
        ))

        assert [path for path, _ in server.requests] == ['/sparql/union']

    assert_isomorphic(expected_sparql_graph(), g_morph)


def test_query_without_graph_pattern_misses_the_named_graphs(caplog):
    """
    A query with no GRAPH pattern reads the default graph only, which on many
    stores leaves out the named graphs: no entity is found, and the log says why.
    """
    with VocabularyServer() as server:
        g_morph = morph_kgc.materialize(sparql_config(
            endpoint_resource(f'{server.url}/sparql'),
            mapping=os.path.join(TEST_DIR, 'mapping_sparql_discussion.ttl'),
        ))

    assert len(g_morph) == 0
    assert 'GRAPH ?g' in caplog.text


def test_endpoint_url_in_the_mapping_only(tmp_path):
    """An endpoint needing no credentials needs no configuration at all."""
    with VocabularyServer() as server:
        mapping = sparql_mapping(
            tmp_path, (MAPPING_ENDPOINT, f'{server.url}/public/sparql')
        )
        g_morph = morph_kgc.materialize(sparql_config(mapping=mapping))

        assert server.requests == [('/public/sparql', STUDIES_QUERY)]

    assert_isomorphic(expected_sparql_graph(), g_morph)


def test_endpoint_needing_undeclared_credentials(tmp_path):
    """An endpoint denying access says where its credentials are declared."""
    with VocabularyServer() as server:
        mapping = sparql_mapping(tmp_path, (MAPPING_ENDPOINT, f'{server.url}/sparql'))
        with pytest.raises(ValueError, match=r"'username' and 'password'.*\[RESOURCE:<name>\]"):
            morph_kgc.materialize(sparql_config(mapping=mapping))


def test_endpoint_matched_by_url_from_the_environment(tmp_path, monkeypatch):
    """The URL of a resource matches the mapping once its placeholders are replaced."""
    with VocabularyServer() as server:
        monkeypatch.setenv('CLINICAL_TRIALS_ENDPOINT', f'{server.url}/sparql')
        mapping = sparql_mapping(tmp_path, (MAPPING_ENDPOINT, f'{server.url}/sparql'))
        g_morph = morph_kgc.materialize(sparql_config(
            endpoint_resource('{CLINICAL_TRIALS_ENDPOINT}', iri=''),
            mapping=mapping,
        ))

    assert_isomorphic(expected_sparql_graph(), g_morph)


def test_endpoint_url_from_an_unset_variable(tmp_path, monkeypatch, caplog):
    """A resource whose URL cannot be known is said to be passed over."""
    monkeypatch.delenv('CLINICAL_TRIALS_ENDPOINT', raising=False)
    with VocabularyServer() as server:
        mapping = sparql_mapping(tmp_path, (MAPPING_ENDPOINT, f'{server.url}/sparql'))
        with pytest.raises(ValueError, match="'username' and 'password'"):
            morph_kgc.materialize(sparql_config(
                endpoint_resource('{CLINICAL_TRIALS_ENDPOINT}', iri=''),
                mapping=mapping,
            ))

    assert "'CLINICAL_TRIALS_ENDPOINT', which is not set" in caplog.text


def test_endpoint_declared_without_url():
    """A resource naming the endpoint by its IRI must say where to query it."""
    with pytest.raises(ValueError, match="does not declare the 'url'"):
        morph_kgc.materialize(sparql_config(
            f'[RESOURCE:clinical_trials]\n'
            f'resource_type=SPARQL_ENDPOINT\n'
            f'iri={MAPPING_ENDPOINT}\n'
        ))


def test_several_reconciliations_in_one_mapping():
    """
    Each execution pairs its query with its endpoint, so a mapping reconciles
    against several knowledge graphs. The same query sent to the same endpoint
    by two executions is sent once.
    """
    with VocabularyServer() as server:
        g_morph = morph_kgc.materialize(sparql_config(
            endpoint_resource(f'{server.url}/sparql') + (
                f'\n[RESOURCE:public]\n'
                f'resource_type=SPARQL_ENDPOINT\n'
                f'iri=https://data.com/public/query\n'
                f'url={server.url}/public/sparql\n'
            ),
            mapping=os.path.join(TEST_DIR, 'mapping_sparql_several.ttl'),
        ))

        assert sorted(server.requests) == sorted([
            ('/public/sparql', STUDIES_QUERY),
            ('/sparql', DRUGS_QUERY),
            ('/sparql', STUDIES_QUERY),
        ])

    assert_isomorphic(expected_sparql_graph('output_sparql_several.nq'), g_morph)


def test_value_matching_several_entities(tmp_path):
    """A value matching several entities yields one triple per entity."""
    query = (
        'SELECT ?entity_iri ("NCT07667218" AS ?matching_value_1) '
        'WHERE { GRAPH ?g { ?entity_iri a <https://schema.org/MedicalStudy> } }'
    )
    with VocabularyServer() as server:
        mapping = sparql_mapping(
            tmp_path,
            (MAPPING_ENDPOINT, f'{server.url}/public/sparql'),
            (STUDIES_QUERY, query),
        )
        g_morph = morph_kgc.materialize(sparql_config(mapping=mapping))

    assert {str(o) for o in g_morph.objects()} == {
        f'https://data.com/id/{i}' for i in range(1, 5)
    }


def test_case_insensitive_matching_in_the_mapping(tmp_path):
    """
    Values are matched exactly, so a mapping matching them whatever their case
    lowercases both the values of the query and the values it reconciles.
    """
    with open(os.path.join(TEST_DIR, 'clinical_trials.csv'), encoding='utf-8') as f:
        rows = f.read().replace('NCT07667218', 'nct07667218').replace('NCT07667205', 'Nct07667205')
    (tmp_path / 'clinical_trials.csv').write_text(rows, encoding='utf-8')
    source = str(tmp_path / 'clinical_trials.csv').replace('\\', '/')

    lowercase_query = (
        'PREFIX schema: <https://schema.org/> '
        'SELECT ?entity_iri ?matching_value_1 WHERE { GRAPH ?g { '
        '?entity_iri a schema:MedicalStudy ; schema:identifier ?identifier } '
        'BIND(LCASE(?identifier) AS ?matching_value_1) }'
    )
    lowercase_value = (
        '[ rml:functionExecution <#LowerCase> ] ] .\n\n'
        '<#LowerCase>\n'
        '    rml:function grel:toLowerCase ;\n'
        '    rml:input [\n'
        '        rml:parameter grel:valueParameter ;\n'
        '        rml:inputValueMap [ rml:reference "clinical_trial_id" ]\n'
        '    ] .\n'
    )

    with VocabularyServer() as server:
        endpoint = (MAPPING_ENDPOINT, f'{server.url}/public/sparql')
        source_file = ('test/rml-fnml/reconciliation/clinical_trials.csv', source)
        exact = morph_kgc.materialize(sparql_config(
            mapping=sparql_mapping(tmp_path, endpoint, source_file),
        ))
        case_insensitive = morph_kgc.materialize(sparql_config(
            mapping=sparql_mapping(
                tmp_path, endpoint, source_file,
                (STUDIES_QUERY, lowercase_query),
                ('[ rml:reference "clinical_trial_id" ]\n    ] .\n', lowercase_value),
            ),
        ))

    assert len(exact) == 1
    assert_isomorphic(expected_sparql_graph(), case_insensitive)


def test_reconcile_entity_over_sparql_with_multiple_processes():
    """Worker processes read the indexes back from the state directory."""
    with VocabularyServer() as server:
        g_morph = morph_kgc.materialize(sparql_config(
            endpoint_resource(f'{server.url}/sparql'), processes=4,
        ))

    assert_isomorphic(expected_sparql_graph(), g_morph)


def test_reconcile_entity_over_sparql_from_yarrrml():
    """The query survives YARRRML as a block scalar, comments included."""
    with VocabularyServer() as server:
        g_morph = morph_kgc.materialize(sparql_config(
            endpoint_resource(f'{server.url}/sparql'),
            mapping=os.path.join(TEST_DIR, 'mapping_sparql.yarrrml'),
        ))

        assert '# The entities are described in named graphs.\n' in server.requests[0][1]

    assert_isomorphic(expected_sparql_graph(), g_morph)


@pytest.mark.parametrize('query, error', [
    (
        'SELECT ?study ?matching_value_1 WHERE { GRAPH ?g { ?study '
        '<https://schema.org/identifier> ?matching_value_1 } }',
        r'does not project \?entity_iri\. .* It projects: \?study \?matching_value_1',
    ),
    (
        'SELECT ?entity_iri ?identifier WHERE { GRAPH ?g { ?entity_iri '
        '<https://schema.org/identifier> ?identifier } }',
        r'does not project \?matching_value_1\.',
    ),
    (
        'SELECT ?entity_iri ?matching_value_1 ?matching_value_2 WHERE { GRAPH ?g { '
        '?entity_iri <https://schema.org/identifier> ?matching_value_1, ?matching_value_2 } }',
        r'projects \?matching_value_2',
    ),
])
def test_query_projecting_other_variables(tmp_path, query, error):
    """A query not following the variable convention fails instead of matching nothing."""
    with VocabularyServer() as server:
        mapping = sparql_mapping(
            tmp_path,
            (MAPPING_ENDPOINT, f'{server.url}/public/sparql'),
            (STUDIES_QUERY, query),
        )
        with pytest.raises(ValueError, match=error):
            morph_kgc.materialize(sparql_config(mapping=mapping))


@pytest.mark.parametrize('replacement', [
    # No query at all.
    ('rml:parameter morph-fn:sparqlQuery ;', 'rml:parameter morph-fn:unused ;'),
    # A query read from the data, which is not known before it is read.
    (f'rml:inputValue """{STUDIES_QUERY}"""',
     'rml:inputValueMap [ rml:reference "clinical_trial_id" ]'),
])
def test_query_not_given_as_a_constant(tmp_path, replacement):
    """The query is sent before any data is read, so the mapping must give it."""
    mapping = sparql_mapping(tmp_path, replacement)
    with pytest.raises(ValueError, match='sparqlQuery.*once, to a constant'):
        morph_kgc.materialize(sparql_config(mapping=mapping))


@pytest.mark.parametrize('endpoint, error', [
    ('clinical_trials', r'must be an http\(s\) URL'),
    ('test/rml-fnml/reconciliation/clinical_trials.trig', r'must be an http\(s\) URL'),
    ('https://user:s3cr3t@data.com/database/query', 'holds credentials'),
])
def test_endpoint_not_a_plain_url(tmp_path, endpoint, error):
    """The endpoint is an http(s) URL, and its credentials stay out of the mapping."""
    mapping = sparql_mapping(tmp_path, (MAPPING_ENDPOINT, endpoint))
    with pytest.raises(ValueError, match=error):
        morph_kgc.materialize(sparql_config(mapping=mapping))


def test_query_bound_twice(tmp_path):
    """An execution sends one query, not one per binding."""
    mapping = sparql_mapping(tmp_path, (
        'rml:parameter morph-fn:sparqlQuery ;',
        'rml:parameter morph-fn:sparqlQuery ;\n        rml:inputValue "SELECT * {}" ;',
    ))
    with pytest.raises(ValueError, match='sparqlQuery.*once, to a constant'):
        morph_kgc.materialize(sparql_config(mapping=mapping))


def test_endpoint_declared_twice():
    """Two resources declaring the endpoint the mapping names are ambiguous."""
    with pytest.raises(ValueError, match='declared by several resources'):
        morph_kgc.materialize(sparql_config(
            endpoint_resource('https://staging.data.com/query')
            + '\n' + endpoint_resource('https://test.data.com/query', name='other')
        ))


def test_options_that_are_not_access_options(caplog):
    """Options that no longer belong to the configuration file are reported."""
    with VocabularyServer() as server:
        g_morph = morph_kgc.materialize(sparql_config(
            endpoint_resource(
                f'{server.url}/sparql',
                'query=SELECT ?concept ?value WHERE { ?concept ?p ?value }\n'
                'matching=CASE-INSENSITIVE',
            )
        ))

    assert_isomorphic(expected_sparql_graph(), g_morph)
    assert "Option 'matching' of resource 'clinical_trials' is ignored" in caplog.text
    assert "Option 'query' of resource 'clinical_trials' is ignored" in caplog.text


def test_endpoint_rejecting_the_query(tmp_path):
    """What the endpoint says about a query it rejects is reported."""
    with VocabularyServer() as server:
        mapping = sparql_mapping(
            tmp_path,
            (MAPPING_ENDPOINT, f'{server.url}/public/sparql'),
            (STUDIES_QUERY, 'SELECT ?entity_iri ?matching_value_1 WHERE { GRAPH ?g { '),
        )
        with pytest.raises(ValueError, match='answered 400 Bad Request: .+'):
            morph_kgc.materialize(sparql_config(mapping=mapping))


def test_query_that_is_not_a_select_query(tmp_path):
    """The answer to a query other than a SELECT query is not taken for one."""
    with VocabularyServer() as server:
        mapping = sparql_mapping(
            tmp_path,
            (MAPPING_ENDPOINT, f'{server.url}/public/sparql'),
            (STUDIES_QUERY, 'ASK { GRAPH ?g { ?s ?p ?o } }'),
        )
        with pytest.raises(ValueError, match='must be a SELECT query'):
            morph_kgc.materialize(sparql_config(mapping=mapping))


def test_entities_that_are_not_iris(tmp_path, caplog):
    """Solutions binding the entity to something other than an IRI are left out."""
    query = (
        'PREFIX schema: <https://schema.org/> '
        'SELECT ?entity_iri ?matching_value_1 WHERE { GRAPH ?g { '
        '?study a schema:MedicalStudy ; schema:identifier ?matching_value_1 } '
        'BIND(IF(?study = <https://data.com/id/1>, STR(?study), ?study) AS ?entity_iri) }'
    )
    with VocabularyServer() as server:
        mapping = sparql_mapping(
            tmp_path,
            (MAPPING_ENDPOINT, f'{server.url}/public/sparql'),
            (STUDIES_QUERY, query),
        )
        g_morph = morph_kgc.materialize(sparql_config(mapping=mapping))

    assert {str(o) for o in g_morph.objects()} == {
        'https://data.com/id/2', 'https://data.com/id/3',
    }
    assert 'other than an IRI in 1 solution(s)' in caplog.text


def test_query_too_long_for_a_url(tmp_path):
    """A query too long to be sent in a URL is sent with POST."""
    long_query = STUDIES_QUERY + '#' * 5000 + '\n'
    with VocabularyServer() as server:
        endpoint = (MAPPING_ENDPOINT, f'{server.url}/public/sparql')
        morph_kgc.materialize(sparql_config(mapping=sparql_mapping(tmp_path, endpoint)))
        morph_kgc.materialize(sparql_config(
            mapping=sparql_mapping(tmp_path, endpoint, (STUDIES_QUERY, long_query)),
        ))

        assert server.methods == ['GET', 'POST']
        assert server.requests[1][1] == long_query


def test_endpoint_answering_too_late(tmp_path):
    """A query the endpoint answers too late says how to wait longer."""
    with VocabularyServer() as server:
        mapping = sparql_mapping(tmp_path, (MAPPING_ENDPOINT, f'{server.url}/public/sparql/slow'))
        with pytest.raises(ValueError, match="did not answer the query.*'timeout'"):
            morph_kgc.materialize(sparql_config(
                f'[RESOURCE:slow]\n'
                f'resource_type=SPARQL_ENDPOINT\n'
                f'url={server.url}/public/sparql/slow\n'
                f'timeout=1',
                mapping=mapping,
            ))
