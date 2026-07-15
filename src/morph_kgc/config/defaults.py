__author__ = "Julián Arenas-Guerrero"
__credits__ = ["Julián Arenas-Guerrero"]
__license__ = "Apache-2.0"
__maintainer__ = "Julián Arenas-Guerrero"
__email__ = "arenas.guerrero.julian@outlook.com"

import multiprocessing as mp

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
DEFAULT_OUTPUT_FILE: str = "knowledge-graph"
DEFAULT_OUTPUT_DIR: str = ""
DEFAULT_OUTPUT_FORMAT: str = "N-TRIPLES"

# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------
DEFAULT_SAFE_PERCENT_ENCODING: str = ""
# Comma-separated list stored as a string so it survives round-trips through
# INI files. The model converts it to list[str] on construction.
# See issue #321: ",\,\n,\r are always escaped.
DEFAULT_LITERAL_ESCAPING_CHARS: str = '",\n,\r'

# ---------------------------------------------------------------------------
# Data sources
# ---------------------------------------------------------------------------
# Empty string is valid (disables NA handling).
DEFAULT_NA_VALUES: str = ",nan"

# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------
DEFAULT_MAPPING_PARTITIONING: str = "PARTIAL-AGGREGATIONS"
DEFAULT_INFER_SQL_DATATYPES: bool = False
DEFAULT_ENFORCE_SQL_FILTER_NULL: bool = False

# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
DEFAULT_NUMBER_OF_PROCESSES: int = 2 * mp.cpu_count()

# ---------------------------------------------------------------------------
# Functions / UDFs
# ---------------------------------------------------------------------------
DEFAULT_UDFS: str = ""
DEFAULT_API_TOKEN: str = ""

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
DEFAULT_LOGGING_LEVEL: str = "INFO"
DEFAULT_LOGGING_FILE: str = ""
