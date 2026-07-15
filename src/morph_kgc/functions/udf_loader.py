__author__ = "Julián Arenas-Guerrero"
__license__ = "Apache-2.0"

"""
UDF Loader
==========
Dynamically loads user-defined functions from a Python file specified in
the config.  Exposes the same @udf decorator pattern as the @bif decorator
used for built-in functions.

Public API
----------
load_udfs(config) -> dict[str, {"function": callable, "parameters": dict}]
"""

from types import ModuleType
import sys

_UDF_DECORATOR_CODE = """udf_dict = {}
def udf(fun_id, **params):
    def wrapper(funct):
        udf_dict[fun_id] = {}
        udf_dict[fun_id]['function'] = funct
        udf_dict[fun_id]['parameters'] = params
        return funct
    return wrapper
"""


def load_udfs(config) -> dict:
    """
    Load UDFs from the file path returned by config.get_udfs().
    Returns an empty dict when no UDF file is configured.
    """
    udfs_path = config.udfs
    if not udfs_path:
        return {}

    with open(udfs_path, "r") as f:
        udfs_code = f.read()

    udfs_code = _UDF_DECORATOR_CODE + udfs_code

    udf_mod = ModuleType("udfs")
    sys.modules["udfs"] = udf_mod
    exec(udfs_code, udf_mod.__dict__)   # noqa: S102

    return udf_mod.udf_dict
