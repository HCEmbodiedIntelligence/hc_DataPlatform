"""HTTP adapters share the package components owned by this application only."""

from typing import Annotated, cast

from fastapi import Depends, Request

from .service import EpisodePackageService


def application_packages(request: Request) -> EpisodePackageService | None:
    # Authorization runs in the endpoint before unavailable-state handling.
    # An unconfigured app cannot borrow another application's package service.
    return cast(EpisodePackageService | None, getattr(request.app.state, "episode_packages", None))


PackageServices = Annotated[EpisodePackageService | None, Depends(application_packages)]
