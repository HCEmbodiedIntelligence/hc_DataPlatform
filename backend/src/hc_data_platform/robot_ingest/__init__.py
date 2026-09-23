"""Project-independent authenticated robot ingestion."""

from .models import RobotIngestAuthContext


def __getattr__(name: str):
    # Temporal imports the workflow's parent package inside its sandbox. Keep
    # authentication/cryptography and database clients out of that import path.
    if name == "RobotIngestService":
        from .service import RobotIngestService

        return RobotIngestService
    raise AttributeError(name)


__all__ = ["RobotIngestAuthContext", "RobotIngestService"]
