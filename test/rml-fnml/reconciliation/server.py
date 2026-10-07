__author__ = "Julián Arenas-Guerrero"
__license__ = "Apache-2.0"

"""
A throwaway HTTP server used by the reconciliation tests: it serves the SKOS
vocabulary and answers SPARQL queries, both behind HTTP Basic Authentication,
so that the tests exercise the remote code paths without reaching the network.

The SPARQL endpoints evaluate queries over the knowledge graph of
``clinical_trials.trig``, which keeps every entity in a named graph of its own:

- ``/sparql`` has a default graph that does not include the named graphs, as
  Fuseki, Oxigraph or Stardog by default.
- ``/sparql/union`` has a default graph that is the union of the named graphs,
  as GraphDB or Virtuoso.
- ``/public/sparql`` is ``/sparql`` without authentication.
- ``/public/sparql/slow`` is ``/public/sparql`` taking longer than a second
  to answer.
"""

import os
import threading
import time
from base64 import b64encode
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

from rdflib import Dataset

USERNAME = 'vocabulary_user'
PASSWORD = 's3cr3t'

TEST_DIR = os.path.dirname(os.path.realpath(__file__))

# Served as text/plain, as some servers do with files they do not know.
VOCABULARY_FILES = {
    '/vocabulary': ('disease_vocabulary.ttl', 'text/turtle'),
    '/vocabulary.nq': ('disease_vocabulary.nq', 'text/plain'),
    '/vocabulary.trig': ('disease_vocabulary.trig', 'application/trig'),
}


def _knowledge_graph(default_union):
    dataset = Dataset(default_union=default_union)
    dataset.parse(os.path.join(TEST_DIR, 'clinical_trials.trig'), format='trig')
    return dataset


# SPARQL endpoint path -> whether its default graph is the union of the others.
SPARQL_ENDPOINTS = {
    '/sparql': False,
    '/sparql/union': True,
    '/public/sparql': False,
    '/public/sparql/slow': False,
}

# Paths answered without authentication.
PUBLIC_PREFIX = '/public/'


def _expected_authorization():
    credentials = b64encode(f'{USERNAME}:{PASSWORD}'.encode()).decode()
    return f'Basic {credentials}'


class _Handler(BaseHTTPRequestHandler):
    """Serves the vocabulary and the SPARQL endpoints."""

    # Requests reaching the server, so that tests can assert the vocabulary is
    # fetched (and each query sent) exactly once, and the HTTP methods they used.
    requests = []
    methods = []

    def do_GET(self):
        path = urlsplit(self.path).path
        query = parse_qs(urlsplit(self.path).query).get('query', [''])[0]
        self._dispatch(path, query)

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode()
        query = parse_qs(body).get('query', [''])[0]
        self._dispatch(urlsplit(self.path).path, query)

    def _dispatch(self, path, query):
        if (
            not path.startswith(PUBLIC_PREFIX)
            and self.headers.get('Authorization') != _expected_authorization()
        ):
            self._respond(401, b'unauthorized', 'text/plain')
            return

        # A mapping checked out with Windows line endings sends its query with
        # them; the tests compare queries whatever the platform.
        type(self).requests.append((path, query.replace('\r\n', '\n')))
        type(self).methods.append(self.command)

        if path in VOCABULARY_FILES:
            file_name, content_type = VOCABULARY_FILES[path]
            with open(os.path.join(TEST_DIR, file_name), 'rb') as f:
                self._respond(200, f.read(), content_type)
        elif path in SPARQL_ENDPOINTS:
            if path.endswith('/slow'):
                time.sleep(1.5)
            try:
                results = _knowledge_graph(SPARQL_ENDPOINTS[path]).query(query)
            except Exception as exc:
                self._respond(400, str(exc).encode(), 'text/plain')
                return
            self._respond(
                200,
                results.serialize(format='json'),
                'application/sparql-results+json',
            )
        else:
            self._respond(404, b'not found', 'text/plain')

    def _respond(self, status, body, content_type):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        """Keep the test output clean."""


class VocabularyServer:
    """Context manager starting the server on an ephemeral port."""

    def __init__(self):
        self._server = None
        self._thread = None

    def __enter__(self):
        _Handler.requests = []
        _Handler.methods = []
        self._server = HTTPServer(('127.0.0.1', 0), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc_info):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    @property
    def url(self):
        host, port = self._server.server_address
        return f'http://{host}:{port}'

    @property
    def requests(self):
        return list(_Handler.requests)

    @property
    def methods(self):
        return list(_Handler.methods)
