from __future__ import annotations

# ruff: noqa: PLC0414 -- retain legacy constant/helper import paths for callers
from pathlib import Path

from .artifacts import ArtifactStore
from .database import Database
from .repository import Repository
from .use_cases._shared import ALLOWED_TRANSITIONS as ALLOWED_TRANSITIONS
from .use_cases._shared import APPROVAL_PREDECESSOR as APPROVAL_PREDECESSOR
from .use_cases._shared import APPROVAL_REQUIRED_STATUS as APPROVAL_REQUIRED_STATUS
from .use_cases._shared import APPROVAL_RESULT_STATUS as APPROVAL_RESULT_STATUS
from .use_cases._shared import BACKGROUND_ROLE_HINTS as BACKGROUND_ROLE_HINTS
from .use_cases._shared import BED_GAIN_DB as BED_GAIN_DB
from .use_cases._shared import CAPTION_TIME as CAPTION_TIME
from .use_cases._shared import (
    DEFAULT_CREATIVE_TARGET_MINOR as DEFAULT_CREATIVE_TARGET_MINOR,
)
from .use_cases._shared import DEFAULT_RUN_CAP_MINOR as DEFAULT_RUN_CAP_MINOR
from .use_cases._shared import GENERATED_SOURCES as GENERATED_SOURCES
from .use_cases._shared import IMAGE_MIME as IMAGE_MIME
from .use_cases._shared import MAX_AUDIO_CUES as MAX_AUDIO_CUES
from .use_cases._shared import MAX_EMBEDDED_BYTES as MAX_EMBEDDED_BYTES
from .use_cases._shared import MAX_LAYER_BYTES as MAX_LAYER_BYTES
from .use_cases._shared import SENSITIVE_KEY_PARTS as SENSITIVE_KEY_PARTS
from .use_cases._shared import SPEND_CURRENCIES as SPEND_CURRENCIES
from .use_cases._shared import STUDIO_AUTO_SPEND_MINOR as STUDIO_AUTO_SPEND_MINOR
from .use_cases._shared import STUDIO_CAPABILITY_NAME as STUDIO_CAPABILITY_NAME
from .use_cases._shared import (
    STUDIO_READINESS_TTL_SECONDS as STUDIO_READINESS_TTL_SECONDS,
)
from .use_cases._shared import SUBJECT_ROLE_HINTS as SUBJECT_ROLE_HINTS
from .use_cases._shared import VIDEO_MIME as VIDEO_MIME
from .use_cases._shared import _as_float as _as_float
from .use_cases._shared import _caption_coverage as _caption_coverage
from .use_cases._shared import _image_mime as _image_mime
from .use_cases._shared import _slot_accepts_element as _slot_accepts_element
from .use_cases.ab_delivery import ABDeliveryUseCases
from .use_cases.animatic import AnimaticUseCases
from .use_cases.artifacts import ArtifactsUseCases
from .use_cases.assets import AssetsUseCases
from .use_cases.audio import AudioUseCases
from .use_cases.budget import BudgetUseCases
from .use_cases.captions import CaptionsUseCases
from .use_cases.change_request import ChangeRequestUseCases
from .use_cases.composition import CompositionUseCases
from .use_cases.critique import CritiqueUseCases
from .use_cases.elements import ElementsUseCases
from .use_cases.flow import FlowUseCases
from .use_cases.frames import FramesUseCases
from .use_cases.images import ImagesUseCases
from .use_cases.jobs import JobsUseCases
from .use_cases.motion import MotionUseCases
from .use_cases.narration import NarrationUseCases
from .use_cases.openmontage import OpenMontageUseCases
from .use_cases.planning import PlanningUseCases
from .use_cases.production import ProductionUseCases
from .use_cases.production_plan import ProductionPlanUseCases
from .use_cases.projects import ProjectsUseCases
from .use_cases.publication import PublicationUseCases
from .use_cases.qa import QaUseCases
from .use_cases.quality import QualityUseCases
from .use_cases.settlement import SettlementUseCases
from .use_cases.sourcing import SourcingUseCases
from .use_cases.studio_execution import StudioExecutionUseCases
from .use_cases.studio_packets import StudioPacketsUseCases
from .use_cases.tools import ToolsUseCases


class CreativeController(
    ProjectsUseCases,
    PlanningUseCases,
    ElementsUseCases,
    FlowUseCases,
    ChangeRequestUseCases,
    ABDeliveryUseCases,
    CritiqueUseCases,
    ProductionPlanUseCases,
    QualityUseCases,
    ProductionUseCases,
    SourcingUseCases,
    AssetsUseCases,
    AudioUseCases,
    ArtifactsUseCases,
    ToolsUseCases,
    BudgetUseCases,
    JobsUseCases,
    SettlementUseCases,
    StudioPacketsUseCases,
    StudioExecutionUseCases,
    CompositionUseCases,
    MotionUseCases,
    CaptionsUseCases,
    QaUseCases,
    AnimaticUseCases,
    FramesUseCases,
    NarrationUseCases,
    OpenMontageUseCases,
    PublicationUseCases,
    ImagesUseCases,
):
    """Compatible facade; feature behavior lives in use_cases/."""

    def __init__(self, database: Database, artifact_store: ArtifactStore):
        self.database = database
        self.database.initialize()
        self.repository = Repository(database)
        self.artifact_store = artifact_store

    @classmethod
    def open(cls, home: Path | str) -> CreativeController:
        root = Path(home).expanduser().resolve()
        return cls(
            database=Database(root / "state" / "pinaka.sqlite"),
            artifact_store=ArtifactStore(root),
        )
