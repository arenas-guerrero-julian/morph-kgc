__author__ = "Julián Arenas-Guerrero"
__credits__ = ["Julián Arenas-Guerrero"]

__license__ = "Apache-2.0"
__maintainer__ = "Julián Arenas-Guerrero"
__email__ = "arenas.guerrero.julian@outlook.com"


import os
import select
import signal
import subprocess
import sys

import morph_kgc
import pytest

from contextlib import contextmanager, suppress
from rdflib import Dataset
from morph_kgc.testing import assert_isomorphic


TEST_DIR = os.path.dirname(os.path.realpath(__file__))
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(TEST_DIR)))


def config(state_dir='', udf='udf.py', names='country_names.json'):
    mapping_path = os.path.join(TEST_DIR, 'mapping.ttl')
    udf_path = os.path.join(TEST_DIR, udf)
    names_path = os.path.join(TEST_DIR, names)

    return (
        f'[CONFIGURATION]\n'
        f'output_format=N-QUADS\n'
        f'udfs={udf_path}\n'
        f'state_dir={state_dir}\n'
        f'[RESOURCE:country_names]\n'
        f'resource_type=JSON_FILE\n'
        f'url={names_path}\n'
        f'[DataSource]\n'
        f'mappings={mapping_path}'
    )


def expected_graph():
    g = Dataset()
    g.parse(os.path.join(TEST_DIR, 'output.nq'))
    return g


linux_only = pytest.mark.skipif(
    'linux' not in sys.platform, reason='worker processes are only used on Linux')


@contextmanager
def materialization_process(udf):
    """
    A run with *udf* and two worker processes, in a process of its own that
    leads a process group with its workers: whatever is left of the run when
    the test is over is killed, so that a run that hangs cannot hang the tests.
    """
    script = (
        'import signal\n'
        'import morph_kgc\n'
        'from morph_kgc.config.loaders import load_config\n'
        # Ctrl-C interrupts the run, even if the tests run with SIGINT ignored.
        'signal.signal(signal.SIGINT, signal.default_int_handler)\n'
        f'cfg = load_config({config(udf=udf)!r})\n'
        'cfg.number_of_processes = 2\n'
        'morph_kgc.materialize(cfg)\n'
    )
    # The morph_kgc under test, wherever it is imported from.
    src_dir = os.path.dirname(os.path.dirname(morph_kgc.__file__))
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(
        path for path in (src_dir, os.environ.get('PYTHONPATH')) if path))

    run = subprocess.Popen(
        [sys.executable, '-c', script], cwd=ROOT_DIR, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=True,
    )
    try:
        yield run
    finally:
        # The outputs of the run are closed once the run and its workers ended.
        if not run.stdout.closed:
            with suppress(ProcessLookupError):
                os.killpg(run.pid, signal.SIGKILL)
            run.communicate()


def end_of(run, timeout):
    """
    The standard error of *run* once the run and its workers, which share its
    outputs, ended; None if they did not end within *timeout* seconds.
    """
    try:
        return run.communicate(timeout=timeout)[1]
    except subprocess.TimeoutExpired:
        return None


def test_stateful_udf():
    """The shared context is built once and used by every function invocation."""
    g_morph = morph_kgc.materialize(config())

    assert_isomorphic(expected_graph(), g_morph)


def test_stateful_udf_temporary_state_is_removed():
    """The state directory the engine creates does not outlive the run."""
    import tempfile
    from glob import glob
    from morph_kgc.config.loaders import load_config

    pattern = os.path.join(tempfile.gettempdir(), 'morph-kgc-state-*')
    before = set(glob(pattern))

    cfg = load_config(config())
    morph_kgc.materialize(cfg)

    # Neither on disk nor in the configuration the run was handed.
    assert set(glob(pattern)) == before
    assert cfg.state_dir == ''


def test_stateful_udf_failing_initializer_leaves_no_state():
    """A failing initializer cleans up the state directory it opened."""
    import tempfile
    from glob import glob
    from morph_kgc.config.loaders import load_config
    from morph_kgc.functions.registry import FunctionRegistry

    pattern = os.path.join(tempfile.gettempdir(), 'morph-kgc-state-*')

    cfg = load_config(config())
    # Load the UDFs so that the registry cache holds the entry to patch.
    FunctionRegistry.get('http://example.com/countryName', cfg)
    entry = FunctionRegistry._udf_caches[cfg.udfs]['http://example.com/countryName']
    initializer = entry['initializer']

    def failing_initializer(initialization):
        raise ValueError('the vocabulary is unreachable')

    entry['initializer'] = failing_initializer
    before = set(glob(pattern))

    try:
        with pytest.raises(ValueError, match='unreachable'):
            morph_kgc.materialize(cfg)
    finally:
        entry['initializer'] = initializer

    assert set(glob(pattern)) == before
    assert cfg.state_dir == ''


def test_stateful_udf_configured_state_dir(tmp_path, monkeypatch):
    """
    The contexts are written inside a configured state directory, in a
    subdirectory of the run removed when the run ends: the directory and
    whatever else it holds are left alone, and so is the configuration.
    """
    from glob import glob
    from morph_kgc.config.loaders import load_config
    from morph_kgc.functions import executor
    from morph_kgc.functions.state import get_context

    state_dir = tmp_path / 'state'
    state_dir.mkdir()
    (state_dir / 'unrelated.txt').write_text('kept')

    context_files = []

    def spying_get_context(function_iri, run_config):
        context_files.extend(glob(os.path.join(state_dir, '*', '*.pickle')))
        return get_context(function_iri, run_config)

    monkeypatch.setattr(executor, 'get_context', spying_get_context)

    cfg = load_config(config(state_dir=str(state_dir)))
    # A single process, so that the spy sees the contexts being read.
    cfg.number_of_processes = 1
    g_morph = morph_kgc.materialize(cfg)

    assert_isomorphic(expected_graph(), g_morph)
    assert context_files
    assert os.listdir(state_dir) == ['unrelated.txt']
    assert (state_dir / 'unrelated.txt').read_text() == 'kept'
    assert cfg.state_dir == str(state_dir)


def test_stateful_udf_runs_sharing_state_dir(tmp_path, monkeypatch):
    """
    Two runs sharing a configured state directory keep to their own contexts,
    even when one runs while the other has built its contexts but not yet
    read them.
    """
    from morph_kgc.config.loaders import load_config
    from morph_kgc.functions import executor
    from morph_kgc.functions.state import get_context

    state_dir = str(tmp_path / 'state')
    other_names = tmp_path / 'other_country_names.json'
    other_names.write_text('{"ES": "Espana"}')

    other_triples = set()

    def get_context_after_other_run(function_iri, run_config):
        # The other run happens on the first read only, and reads its own
        # contexts as usual.
        monkeypatch.setattr(executor, 'get_context', get_context)
        other_config = load_config(config(state_dir=state_dir, names=str(other_names)))
        other_config.number_of_processes = 1
        other_triples.update(morph_kgc.materialize_set(other_config))
        return get_context(function_iri, run_config)

    monkeypatch.setattr(executor, 'get_context', get_context_after_other_run)

    cfg = load_config(config(state_dir=state_dir))
    # A single process, so that the other run starts before the contexts of
    # this one are read.
    cfg.number_of_processes = 1
    g_morph = morph_kgc.materialize(cfg)

    assert_isomorphic(expected_graph(), g_morph)
    assert {triple.strip() for triple in other_triples} == {
        '<http://example.com/Madrid> <http://example.com/country> "Espana"'}
    assert os.listdir(state_dir) == []


def test_stateful_udf_context_is_written_atomically(tmp_path):
    """A context file is never seen half-written: it is complete or absent."""
    from morph_kgc.functions import state

    files_while_writing = []

    class Context:
        def __reduce__(self):
            files_while_writing.extend(os.listdir(tmp_path))
            return dict, ()

    state._persist_context(str(tmp_path), 'http://example.com/countryName', Context())

    context_files = os.listdir(tmp_path)
    assert len(context_files) == 1
    assert context_files[0] not in files_while_writing


@linux_only
def test_stateful_udf_killed_worker_fails_the_run():
    """
    A worker process that dies, as when the operating system kills it for
    running out of memory, fails the run instead of hanging it.
    """
    with materialization_process('udf_killed_worker.py') as run:
        stderr = end_of(run, timeout=30)

    assert stderr is not None, 'The run hangs when a worker process dies.'
    assert run.returncode == 1
    assert 'A worker process died' in stderr


@linux_only
def test_stateful_udf_workers_end_with_the_run():
    """
    The worker processes end promptly when the run is interrupted (with Ctrl-C)
    while they are materializing.
    """
    with materialization_process('udf_slow_worker.py') as run:
        # Once a worker is materializing.
        assert select.select([run.stdout], [], [], 30)[0]
        assert run.stdout.readline() == 'materializing\n'

        os.kill(run.pid, signal.SIGINT)
        stderr = end_of(run, timeout=10)

    assert stderr is not None, 'The worker processes outlive the run.'


def test_stateful_udf_initializer_runs_once():
    """The initializer is executed a single time, before any triple is built."""
    from morph_kgc.config.loaders import load_config
    from morph_kgc.functions.registry import FunctionRegistry
    from morph_kgc.functions import state

    cfg = load_config(config())
    # Loading the UDFs eagerly lets us count the initializer invocations.
    initializer = FunctionRegistry.get('http://example.com/countryName', cfg).initializer

    calls = []

    def counting_initializer(initialization):
        calls.append(initialization.function_iri)
        return initializer(initialization)

    FunctionRegistry._udf_caches[cfg.udfs]['http://example.com/countryName'][
        'initializer'] = counting_initializer
    state._LOADED_CONTEXTS.clear()

    try:
        # A single process, so that the count is not spread over workers.
        cfg.number_of_processes = 1
        g_morph = morph_kgc.materialize(cfg)
    finally:
        FunctionRegistry._udf_caches[cfg.udfs]['http://example.com/countryName'][
            'initializer'] = initializer
        state._LOADED_CONTEXTS.clear()

    assert calls == ['http://example.com/countryName']
    assert_isomorphic(expected_graph(), g_morph)


def test_no_state_directory_without_stateful_functions():
    """A mapping using no stateful function pays nothing for the feature."""
    from morph_kgc.config.loaders import load_config

    udf_dir = os.path.join(os.path.dirname(TEST_DIR), 'udf')
    cfg = load_config(
        f'[CONFIGURATION]\n'
        f'output_format=N-QUADS\n'
        f'udfs={os.path.join(udf_dir, "udf.py")}\n'
        f'[DataSource]\n'
        f'mappings={os.path.join(udf_dir, "mapping.ttl")}'
    )
    morph_kgc.materialize(cfg)

    assert cfg.state_dir == ''


def test_stateful_udf_without_initialization():
    """A stateful function cannot be executed before its context is built."""
    from morph_kgc.config.loaders import load_config
    from morph_kgc.functions.state import get_context
    from morph_kgc.functions import state

    cfg = load_config(config())
    state._LOADED_CONTEXTS.clear()

    with pytest.raises(FileNotFoundError):
        get_context('http://example.com/countryName', cfg)
