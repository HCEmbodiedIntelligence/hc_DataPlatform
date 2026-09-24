# E raw recording processing

The E robot now uploads sealed original MCAP/MP4 plus capture configuration and events. The platform, not the robot, creates a temporary SQLite index, associates source timestamps, prepares preview media and determines sample validity. An unsliced upload is not a training-ready episode.

`robot.recording.register.requested.v1` outbox delivery starts `RobotRecordingWorkflow` on E's robot queue. Preparation heartbeats, retries three times, and records FAILED durably. An authenticated owner can retry via `/api/v1/robot-ingest/uploads/{upload_id}/recording:retry` with a persistent UUID request ID. No new upload or Raw is allocated by retry. Immutable derived receipts are verified before reuse; multipart completion response loss is reconciled against stored size/hash.

After platform slicing, existing real QC/media/annotation/publishing and LeRobot export run. Original action validity events, source timestamps, missing frames and invalid intervals remain constraints; they are never repaired into valid training samples. A recording's final incomplete interpolation bracket is invalid rather than fabricated after stop.

The offline E Dockerfile adds PyAV 15.1.0 from the local vendor wheel. `backend/uv.lock` pins the same version. `vendor/README.md` records offline wheel provenance. This is an E image and data-volume change only; no physical robot is started. The platform needs no robot checkout mount.

Evidence scripts: `prepare-recording-v3.py` creates the E synthetic collection task; `accept-recording-v3.py` exercises preview, two slices, QC, annotation, publication and real export; `prove-recording-v3.py` verifies actual durable receipt replay while forbidding reprocessing. The independent LeRobot reader and logs are under plan `integration/20260923/evidence/recording-v3/`.

This release preserves concurrent public field/depth work (`recording_fields.py`, depth encoder, MCAP public fields). Their original source mappings are retained. E tests establish the synthetic software path; U2 real low-speed teleoperation and real-camera capture remain pending.
