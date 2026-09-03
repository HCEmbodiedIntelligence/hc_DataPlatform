"""Project-independent authenticated robot ingestion."""

from .models import RobotIngestAuthContext
from .service import RobotIngestService

__all__ = ["RobotIngestAuthContext", "RobotIngestService"]
