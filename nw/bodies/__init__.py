"""Body schemas for nw's project-graph annotations.

Importing this package registers the schemas with lacing so any annotation
with a matching ``body_schema_uri`` validates correctly. The schemas are:

- ``annot://schema/section/v1``        — timeline section (verse, scene-1, …)
- ``annot://schema/shot/v1``           — renderable visual unit
- ``annot://schema/character-ref/v1``  — pointer to a character folder
- ``annot://schema/environment-ref/v1`` — pointer to an environment folder
- ``annot://schema/decision/v1``       — provenance-rich decision log entry

These are deliberately small and project-agnostic. Reelee will be able to
walk the same graph for freshness analysis ("what's downstream of this
character description?") without bespoke storage.
"""

from .character_ref import CHARACTER_REF_BODY_SCHEMA_URI, CharacterRefBodyV1
from .decision import DECISION_BODY_SCHEMA_URI, DecisionBodyV1
from .environment_ref import ENVIRONMENT_REF_BODY_SCHEMA_URI, EnvironmentRefBodyV1
from .section import SECTION_BODY_SCHEMA_URI, SectionBodyV1
from .shot import SHOT_BODY_SCHEMA_URI, ShotBodyV1


__all__ = [
    "CHARACTER_REF_BODY_SCHEMA_URI",
    "CharacterRefBodyV1",
    "DECISION_BODY_SCHEMA_URI",
    "DecisionBodyV1",
    "ENVIRONMENT_REF_BODY_SCHEMA_URI",
    "EnvironmentRefBodyV1",
    "SECTION_BODY_SCHEMA_URI",
    "SectionBodyV1",
    "SHOT_BODY_SCHEMA_URI",
    "ShotBodyV1",
]
