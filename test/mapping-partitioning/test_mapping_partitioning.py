__author__ = "Julián Arenas-Guerrero"
__credits__ = ["Julián Arenas-Guerrero"]

__license__ = "Apache-2.0"
__maintainer__ = "Julián Arenas-Guerrero"
__email__ = "arenas.guerrero.julian@outlook.com"


import os

import pytest

import morph_kgc

from rdflib.graph import Graph
from rdflib import compare

from morph_kgc.config.loaders import load_from_string
from morph_kgc.mapping.parser import MappingParser


PARTITIONINGS = ['PARTIAL-AGGREGATIONS', 'MAXIMAL', 'NO']


def _mapping_path():
    return os.path.join(os.path.dirname(os.path.realpath(__file__)), 'mapping.ttl')


def _config(mapping_partitioning, number_of_processes=None):
    processes = f'number_of_processes={number_of_processes}\n' if number_of_processes else ''
    return (
        f'[CONFIGURATION]\n'
        f'output_format=N-QUADS\n'
        f'mapping_partitioning={mapping_partitioning}\n'
        f'{processes}'
        f'[DataSource]\n'
        f'mappings={_mapping_path()}'
    )


def _partitions(mapping_partitioning):
    """The partition assigned to every rule under *mapping_partitioning*."""
    config = load_from_string(_config(mapping_partitioning, number_of_processes=1))
    rml_mapping = MappingParser(config).parse_mappings()
    return [rule.mapping_partition for rule in rml_mapping.rules]


@pytest.mark.parametrize('mapping_partitioning', PARTITIONINGS)
def test_partitioning_generates_the_same_graph(mapping_partitioning):
    """
    Every partitioning algorithm must materialize the same knowledge graph.

    The default number of processes is used on purpose: MAXIMAL and NO take the
    multiprocessing branch of the partitioner there, which is where they used to
    fail before reaching any data.
    """
    g = Graph()
    g.parse(os.path.join(os.path.dirname(os.path.realpath(__file__)), 'output.nq'))

    g_morph = morph_kgc.materialize(_config(mapping_partitioning))

    assert compare.isomorphic(g, g_morph)


@pytest.mark.parametrize('mapping_partitioning', PARTITIONINGS)
def test_partitioning_assigns_a_partition_to_every_rule(mapping_partitioning):
    """A rule left without a partition is one the partitioner silently skipped."""
    assert all(partition is not None for partition in _partitions(mapping_partitioning))


def test_no_partitioning_puts_every_rule_in_one_group():
    assert len(set(_partitions('NO'))) == 1


@pytest.mark.parametrize('mapping_partitioning', ['PARTIAL-AGGREGATIONS', 'MAXIMAL'])
def test_partitioning_splits_the_mapping(mapping_partitioning):
    """
    The two partitioning algorithms must actually group this mapping: its two
    triples maps have different subject templates, so they cannot share a group.
    """
    assert len(set(_partitions(mapping_partitioning))) > 1
