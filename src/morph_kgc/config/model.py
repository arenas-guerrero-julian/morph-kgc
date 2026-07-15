__author__ = "Julián Arenas-Guerrero"
__credits__ = ["Julián Arenas-Guerrero"]
__license__ = "Apache-2.0"
__maintainer__ = "Julián Arenas-Guerrero"
__email__ = "arenas.guerrero.julian@outlook.com"

import errno
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .defaults import (
    DEFAULT_OUTPUT_FILE,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_OUTPUT_FORMAT,
    DEFAULT_SAFE_PERCENT_ENCODING,
    DEFAULT_LITERAL_ESCAPING_CHARS,
    DEFAULT_NA_VALUES,
    DEFAULT_MAPPING_PARTITIONING,
    DEFAULT_INFER_SQL_DATATYPES,
    DEFAULT_ENFORCE_SQL_FILTER_NULL,
    DEFAULT_NUMBER_OF_PROCESSES,
    DEFAULT_UDFS,
    DEFAULT_API_TOKEN,
    DEFAULT_LOGGING_LEVEL,
    DEFAULT_LOGGING_FILE,
)

LOGGER = logging.getLogger("morph_kgc")

# ---------------------------------------------------------------------------
# Valid option sets.
# ---------------------------------------------------------------------------
VALID_OUTPUT_FORMATS = {"N-TRIPLES", "N-QUADS", "JELLY"}
VALID_LOGGING_LEVELS = {"NOTSET", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
VALID_PARTITIONING = {"PARTIAL-AGGREGATIONS", "MAXIMAL", "NO", "FALSE", "OFF", "0"}
OUTPUT_FORMAT_FILE_EXTENSION = {
    "N-TRIPLES": ".nt",
    "N-QUADS": ".nq",
    "JELLY": ".jelly",
}


# ---------------------------------------------------------------------------
# Per-data-source configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DataSourceConfig:
    """Holds all options for a single ``[DATA_SOURCE_NAME]`` INI section."""

    name: str
    mappings: list[str]
    # Relational databases
    db_url: str = ""
    connect_args: str = ""
    # File-based sources
    file_path: str = ""

    def has_db_url(self) -> bool:
        return bool(self.db_url)

    def has_file_path(self) -> bool:
        return bool(self.file_path)

    def has_connect_args(self) -> bool:
        return bool(self.connect_args)

    def get_db_url_with_env(self) -> str:
        """Expand ``{ENV_VAR}`` placeholders in the DB URL."""
        return self.db_url.format(**os.environ)


# ---------------------------------------------------------------------------
# Main configuration model
# ---------------------------------------------------------------------------

@dataclass
class MorphConfig:
    """
    Validated, typed representation of a Morph-KGC configuration.

    Construct via one of the loader functions in ``config.loaders``:

        config = load_from_file("config.ini")
        config = load_from_string("[CONFIGURATION]\\noutput_format=N-QUADS")
        config = load_from_dict({"output_format": "N-QUADS", ...})
        config = load_from_cli()          # reads sys.argv

    Attribute names intentionally mirror the INI option names so that
    ``load_from_dict`` can be implemented with a simple ``**kwargs``.
    """

    # -- Data sources (required) ---------------------------------------------
    data_sources: dict[str, DataSourceConfig] = field(default_factory=dict)

    # -- Output --------------------------------------------------------------
    output_file: str = DEFAULT_OUTPUT_FILE
    output_dir: str = DEFAULT_OUTPUT_DIR
    output_format: str = DEFAULT_OUTPUT_FORMAT

    # -- Serialization -------------------------------------------------------
    safe_percent_encoding: str = DEFAULT_SAFE_PERCENT_ENCODING
    # Stored internally as list[str]; the loaders pass a comma-separated string
    # which ``__post_init__`` splits.
    literal_escaping_chars: list[str] = field(default_factory=list)

    # -- Data sources (global) -----------------------------------------------
    na_values: list[str] = field(default_factory=list)

    # -- Mapping -------------------------------------------------------------
    mapping_partitioning: str = DEFAULT_MAPPING_PARTITIONING
    infer_sql_datatypes: bool = DEFAULT_INFER_SQL_DATATYPES
    enforce_sql_filter_null: bool = DEFAULT_ENFORCE_SQL_FILTER_NULL

    # -- Execution -----------------------------------------------------------
    number_of_processes: int = DEFAULT_NUMBER_OF_PROCESSES

    # -- Functions / UDFs ----------------------------------------------------
    udfs: str = DEFAULT_UDFS
    api_token: str = DEFAULT_API_TOKEN

    # -- Logging -------------------------------------------------------------
    logging_level: str = DEFAULT_LOGGING_LEVEL
    logging_file: str = DEFAULT_LOGGING_FILE

    # Internal: serialized http_api_df (set by the engine, not by the user).
    # Kept here to preserve the existing inter-module contract during
    # incremental refactoring.
    _http_api_df_csv: str = field(default="", repr=False)

    # -----------------------------------------------------------------------

    def __post_init__(self) -> None:
        self._normalize_and_validate()

    # -----------------------------------------------------------------------
    # Normalization + validation
    # -----------------------------------------------------------------------

    def _normalize_and_validate(self) -> None:
        self._normalize_strings()
        self._validate_output_format()
        self._validate_logging_level()
        self._validate_mapping_partitioning()
        self._validate_number_of_processes()
        self._validate_paths()
        self._coerce_list_fields()
        self._setup_logging()
        self._log_config()

    def _normalize_strings(self) -> None:
        self.output_format = self.output_format.strip().upper()
        self.logging_level = self.logging_level.strip().upper()
        self.mapping_partitioning = self.mapping_partitioning.strip().upper()

    def _validate_output_format(self) -> None:
        if self.output_format not in VALID_OUTPUT_FORMATS:
            raise ValueError(
                f"'output_format' value '{self.output_format}' is not valid. "
                f"Must be one of: {sorted(VALID_OUTPUT_FORMATS)}."
            )

    def _validate_logging_level(self) -> None:
        if self.logging_level not in VALID_LOGGING_LEVELS:
            raise ValueError(
                f"'logging_level' value '{self.logging_level}' is not valid. "
                f"Must be one of: {sorted(VALID_LOGGING_LEVELS)}."
            )

    def _validate_mapping_partitioning(self) -> None:
        if self.mapping_partitioning not in VALID_PARTITIONING:
            raise ValueError(
                f"'mapping_partitioning' value '{self.mapping_partitioning}' is not valid. "
                f"Must be one of: {sorted(VALID_PARTITIONING)}."
            )

    def _validate_number_of_processes(self) -> None:
        if self.number_of_processes < 1:
            raise ValueError(
                f"'number_of_processes' must be >= 1, got {self.number_of_processes}."
            )

    def _validate_paths(self) -> None:
        _create_dirs_in_path(self.logging_file)

    def _coerce_list_fields(self) -> None:
        # na_values: split on comma; preserve empty string as a valid NA value
        if isinstance(self.na_values, str):
            self.na_values = list(set(self.na_values.split(",")))
        elif not self.na_values:
            self.na_values = list(set(DEFAULT_NA_VALUES.split(",")))

        # literal_escaping_chars: same splitting convention
        if isinstance(self.literal_escaping_chars, str):
            raw = self.literal_escaping_chars
            self.literal_escaping_chars = (
                raw.split(",") if raw else DEFAULT_LITERAL_ESCAPING_CHARS.split(",")
            )
        elif not self.literal_escaping_chars:
            self.literal_escaping_chars = DEFAULT_LITERAL_ESCAPING_CHARS.split(",")

    def _setup_logging(self) -> None:
        _configure_logger(self.logging_level, self.logging_file)

    def _log_config(self) -> None:
        LOGGER.debug(
            "CONFIGURATION: output_format=%s, partitioning=%s, processes=%d, "
            "output_file=%s, output_dir=%s",
            self.output_format,
            self.mapping_partitioning,
            self.number_of_processes,
            self.output_file,
            self.output_dir,
        )
        for name, ds in self.data_sources.items():
            LOGGER.debug("DATA SOURCE '%s': mappings=%s", name, ds.mappings)

    # -----------------------------------------------------------------------
    # Convenience predicates (mirror the original Config boolean methods)
    # -----------------------------------------------------------------------

    def is_multiprocessing_enabled(self) -> bool:
        return self.number_of_processes > 1

    def is_no_partitioning(self) -> bool:
        return self.mapping_partitioning in {"NO", "FALSE", "OFF", "0"}

    def is_partial_aggregations_partitioning(self) -> bool:
        return self.mapping_partitioning == "PARTIAL-AGGREGATIONS"

    def is_maximal_partitioning(self) -> bool:
        return self.mapping_partitioning == "MAXIMAL"

    # -----------------------------------------------------------------------
    # Output path helper
    # -----------------------------------------------------------------------

    def get_output_file_path(self, mapping_group: Optional[str] = None) -> str:
        """
        Returns the resolved output file path for a given mapping partition
        group name.  Mirrors ``Config.get_output_file_path`` from the original
        implementation.
        """
        extension = OUTPUT_FORMAT_FILE_EXTENSION[self.output_format]

        if self.output_dir:
            file_name = mapping_group or self.output_file or "knowledge-graph"
            return Path(self.output_dir, file_name).with_suffix(extension).as_posix()

        file_name = self.output_file or "knowledge-graph"
        return Path(file_name).with_suffix(extension).as_posix()

    # -----------------------------------------------------------------------
    # Data source accessors (preserve the original interface used downstream)
    # -----------------------------------------------------------------------

    def get_data_sources_sections(self) -> list[str]:
        return list(self.data_sources.keys())

    def has_multiple_data_sources(self) -> bool:
        return len(self.data_sources) > 1

    def get_mappings_files(self, source_name: str) -> list[str]:
        return self.data_sources[source_name].mappings

    def get_db_url(self, source_name: str) -> str:
        return self.data_sources[source_name].get_db_url_with_env()

    def has_db_url(self, source_name: str) -> bool:
        return self.data_sources[source_name].has_db_url()

    def get_file_path(self, source_name: str) -> str:
        return self.data_sources[source_name].file_path

    def has_file_path(self, source_name: str) -> bool:
        return self.data_sources[source_name].has_file_path()

    def get_connect_args(self, source_name: str) -> str:
        return self.data_sources[source_name].connect_args

    def has_connect_args(self, source_name: str) -> bool:
        return self.data_sources[source_name].has_connect_args()

    # -----------------------------------------------------------------------
    # Internal engine setter (used by __init__.py during materialization)
    # -----------------------------------------------------------------------

    def set_http_api_df_csv(self, csv: str) -> None:
        self._http_api_df_csv = csv

    def get_http_api_df_csv(self) -> str:
        return self._http_api_df_csv


# ---------------------------------------------------------------------------
# Module-private helpers
# ---------------------------------------------------------------------------

def _create_dirs_in_path(path: str) -> None:
    """Creates intermediate directories for a file path if they do not exist."""
    if not path:
        return
    parent = Path(path).parent
    if str(parent) == ".":
        return
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        if exc.errno != errno.EEXIST:
            raise


def _configure_logger(level: str, log_file: str) -> None:
    """Configures the morph_kgc root logger."""
    logger = logging.getLogger("morph_kgc")
    logger.setLevel(getattr(logging, level, logging.INFO))

    if not logger.handlers:
        handler: logging.Handler
        if log_file:
            _create_dirs_in_path(log_file)
            handler = logging.FileHandler(log_file)
        else:
            handler = logging.StreamHandler()

        formatter = logging.Formatter(
            fmt="%(asctime)s - %(name)s - %(levelname)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
