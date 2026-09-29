"""Collection freezing/archiving on the existing scoped Episode delivery outbox."""

import hashlib
import io
import time
import zipfile

from hc_data_platform.annotation.delivery_models import RequestAnnotationDelivery
from hc_data_platform.security import AuthContext

from .collection_delivery_models import CollectionDeliveryMember, CollectionDeliveryPlan
from .collection_export import (
    CollectionSource,
    contract,
    preflight,
    selected_info,
    write_collection,
)
from .delivery_repository import failure, scope_key
from .models import canonical, digest
from .postgres import PackageConflict


class CollectionDelivery:
    def __init__(self, owner):
        self.owner = owner

    def authorize(self, plan, auth):
        for member in plan.members:
            if member.annotation is not None:
                if self.owner.annotations is None:
                    raise failure(
                        "ANNOTATIONS_UNAVAILABLE", "Annotation delivery is not configured.", 503
                    )
                task = self.owner.annotations.authorize(member.annotation.binding.task_id, auth)
                if task.data_ref != member.data:
                    raise PackageConflict("selected annotation task differs from collection data")

    def freeze(self, body, auth):
        members, sources = [], []
        for selected in sorted(body.members, key=lambda m: m.data.data_version_id):
            data = selected.data
            if data.identity.scope != scope_key():
                raise PermissionError("collection selection differs from authorized scope")
            package = self.owner.packages.files.open(data.identity, data.data_version_id)
            if package.receipt.data != data:
                raise PackageConflict("collection data differs from its committed reference")
            annotation = None
            if selected.annotation is not None:
                if self.owner.annotations is None:
                    raise failure(
                        "ANNOTATIONS_UNAVAILABLE", "Annotation delivery is not configured.", 503
                    )
                task = self.owner.annotations.authorize(selected.annotation.task_id, auth)
                if task.data_ref != data:
                    raise PackageConflict("selected annotation is bound to different data")
                annotation, _ = self.owner.annotations._freeze(
                    selected.annotation.task_id,
                    RequestAnnotationDelivery(
                        **selected.annotation.model_dump(exclude={"task_id"})
                    ),
                    auth,
                )
            _, depth, _ = selected_info(package, body.modalities)
            evidence = package.document("hc/modalities.json")["depth"]
            profiles = {
                feature: package.document(evidence[feature]["evidence_path"] + "/profile.json")[
                    "profile_sha256"
                ]
                for feature in depth
                if body.modalities == "rgbd"
            }
            members.append(
                CollectionDeliveryMember(
                    data=data,
                    manifest_sha256=package.receipt.manifest_sha256,
                    annotation=annotation,
                    selected_depth_profiles=profiles,
                    excluded_depth_features=tuple(sorted(depth))
                    if body.modalities == "rgb"
                    else (),
                )
            )
            sources.append(CollectionSource(package))
        preflight(sources, body.modalities)
        info, _, _ = selected_info(sources[0].package, body.modalities)
        return (
            CollectionDeliveryPlan(
                members=tuple(members),
                modalities=body.modalities,
                contract_sha256=digest(contract(info)),
            ),
            None,
            None,
        )

    def sources(self, plan):
        # Worker identity is constructed only after the persisted outbox event is verified.
        organization, project, region = scope_key()
        auth = AuthContext(
            subject_id="collection-delivery-worker",
            service_identity=True,
            capabilities=frozenset({"annotation_task.read", "export.download"}),
            organization_ids=frozenset({organization}),
            project_ids=frozenset({project}),
            region_codes=frozenset({region}),
            scope_pairs=frozenset({(project, region)}),
            organization_scope_triples=frozenset({(organization, project, region)}),
        )
        result = []
        for member in plan.members:
            package = self.owner.packages.files.open(
                member.data.identity, member.data.data_version_id
            )
            if (
                package.receipt.data != member.data
                or package.receipt.manifest_sha256 != member.manifest_sha256
            ):
                raise PackageConflict("collection source differs from frozen plan")
            annotation, track, review = None, None, None
            if member.annotation is not None:
                selected = member.annotation
                annotation = self.owner.annotations.snapshot_for_plan(selected, auth)
                track = selected.binding.track
                review = {
                    "format": "hc-annotation-review/1",
                    "review": selected.review.model_dump(mode="json"),
                    "approval": selected.approval.model_dump(mode="json")
                    if selected.approval
                    else None,
                    "submission": selected.submission.model_dump(mode="json")
                    if selected.submission
                    else None,
                }
            info, depth, _ = selected_info(package, plan.modalities)
            if digest(contract(info)) != plan.contract_sha256:
                raise PackageConflict("collection contract differs from frozen plan")
            evidence = package.document("hc/modalities.json")["depth"]
            profiles = {
                feature: package.document(evidence[feature]["evidence_path"] + "/profile.json")[
                    "profile_sha256"
                ]
                for feature in depth
                if plan.modalities == "rgbd"
            }
            if (
                profiles != member.selected_depth_profiles
                or (tuple(sorted(depth)) if plan.modalities == "rgb" else ())
                != member.excluded_depth_features
            ):
                raise PackageConflict("collection depth selection differs from frozen plan")
            result.append(CollectionSource(package, annotation, track, review))
        return result

    def build(self, plan, directory, archive_path, row, checkpoint):
        from .delivery import inventory_document

        self.owner.repository.phase(row, "projecting")
        root = directory / "collection"
        manifest, _ = write_collection(
            root, self.sources(plan), modalities=plan.modalities, checkpoint=checkpoint
        )
        extra = {
            "collection/hc/collection_manifest.json": canonical(manifest),
            "delivery.json": inventory_document(plan),
        }
        inventory = [
            ("collection/" + entry["path"], entry["size"], entry["sha256"], root / entry["path"])
            for entry in manifest["files"]
        ] + [
            (name, len(body), hashlib.sha256(body).hexdigest(), body)
            for name, body in extra.items()
        ]
        self.owner.repository.pin_totals(row, len(inventory), sum(item[1] for item in inventory))
        self.owner.repository.phase(row, "archiving")
        count = size = 0
        last_report = time.monotonic()
        with zipfile.ZipFile(
            archive_path, "w", compression=zipfile.ZIP_STORED, allowZip64=True
        ) as archive:
            for name, expected_size, expected_hash, origin in sorted(inventory):
                entry = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                entry.external_attr = 0o100644 << 16
                checksum, read_size = hashlib.sha256(), 0
                with (
                    (
                        io.BytesIO(origin) if isinstance(origin, bytes) else origin.open("rb")
                    ) as source,
                    archive.open(entry, "w", force_zip64=True) as output,
                ):
                    for block in iter(lambda: source.read(1024**2), b""):
                        checkpoint()
                        read_size += len(block)
                        if read_size > expected_size:
                            raise PackageConflict("collection member exceeds frozen size")
                        checksum.update(block)
                        output.write(block)
                        size += len(block)
                        if time.monotonic() - last_report >= 1:
                            self.owner.repository.progress(row, count, size)
                            last_report = time.monotonic()
                if checksum.hexdigest() != expected_hash or read_size != expected_size:
                    raise PackageConflict("collection member differs from frozen inventory")
                count += 1
                self.owner.repository.progress(row, count, size)
        checksum = hashlib.sha256()
        with archive_path.open("rb") as source:
            for block in iter(lambda: source.read(1024**2), b""):
                checkpoint()
                checksum.update(block)
        return checksum.hexdigest(), archive_path.stat().st_size
