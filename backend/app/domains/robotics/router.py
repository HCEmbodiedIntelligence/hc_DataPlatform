"""Composition root for the current 110-operation P14-P17 API."""

from fastapi import APIRouter

from .calibrations.router import router as calibration_router
from .robots.router import router as robot_router
from .schemas import *  # noqa: F403 - FastAPI resolves deferred annotations in this module.
from .schemas.router import router as schema_router

router = APIRouter()
router.routes.extend(robot_router.routes)
router.routes.extend(calibration_router.routes)
router.routes.extend(schema_router.routes)
