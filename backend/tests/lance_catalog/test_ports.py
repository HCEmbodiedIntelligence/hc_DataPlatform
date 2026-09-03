from hc_data_platform.lance_catalog import FakeStepReader, StepReaderPort, StepRecord


def test_fake_step_reader_satisfies_runtime_port() -> None:
    reader = FakeStepReader(
        "dataset-a",
        3,
        [StepRecord(rollout_id="rollout-a", step_index=0, timestamp_ns=0)],
    )

    assert isinstance(reader, StepReaderPort)
    assert reader.read_steps("dataset-a", "rollout-a", 0, 1).dataset_version == 3
