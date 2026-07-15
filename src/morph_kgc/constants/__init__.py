__author__ = "Julián Arenas-Guerrero"
__license__ = "Apache-2.0"

"""
constants package — backward-compatible re-export façade
=========================================================
Every name that was previously importable from morph_kgc.constants is still
reachable here, so callers using ``from .constants import *`` or explicit
named imports require zero changes.

Prefer importing from the focused sub-modules directly in new code:

    from morph_kgc.constants.rml        import RML_NAMESPACE, RML_TRIPLES_MAP_CLASS
    from morph_kgc.constants.rml        import RML_TRIPLE_TERM_MAP          # RML 1.2
    from morph_kgc.constants.rml        import RML_NON_ASSERTED_TRIPLES_MAP_CLASS
    from morph_kgc.constants.rml        import RML_DIRECTION_MAP            # RML 1.2
    from morph_kgc.constants.rml_legacy import RML_LEGACY_NAMESPACE         # compat only
    from morph_kgc.constants.r2rml      import R2RML_NAMESPACE
    from morph_kgc.constants.fnml       import RML_EXECUTION, FNO_NAMESPACE
    from morph_kgc.constants.xsd        import XSD_INTEGER, XSD_BOOLEAN
    from morph_kgc.constants.rdf        import RDF_TYPE, RDF_REIFIES
    from morph_kgc.constants.sources    import RDB, CSV, FILE_SOURCE_TYPES
    from morph_kgc.constants.output     import NTRIPLES, NQUADS, JELLY
    from morph_kgc.constants.mapping    import MAXIMAL_PARTITIONING
    from morph_kgc.constants.misc       import LOGGING_NAMESPACE
"""

from .sources    import *   # noqa: F401,F403
from .output     import *   # noqa: F401,F403
from .mapping    import *   # noqa: F401,F403
from .r2rml      import *   # noqa: F401,F403
from .rml        import *   # noqa: F401,F403
from .rml_legacy import *   # noqa: F401,F403
from .fnml       import *   # noqa: F401,F403
from .xsd        import *   # noqa: F401,F403
from .rdf        import *   # noqa: F401,F403
from .misc       import *   # noqa: F401,F403
