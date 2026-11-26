"""Session-scoped recording commands; receipts come only from the robot identity."""

from typing import Literal

from .models import CaptureCommand, CaptureReceipt, CaptureSlot, CaptureView, Endpoint
from .store import LEASE


def current_state(
    slot: CaptureSlot, now: float
) -> Literal["recording", "saved", "interrupted", "error", "pending", "unknown"]:
    if slot.receipt:
        return slot.receipt.state
    if now < slot.expires:
        return "pending"
    return "unknown" if slot.delivered else "error"


def is_active(slot: CaptureSlot, state: str) -> bool:
    return state == "recording" or (state == "error" and slot.previous_state == "recording")


def view(row: Endpoint, owner: str, project: str, now: float) -> CaptureView:
    available = (
        row.capture_project == project and row.capture_tasks is not None and row.seen + LEASE > now
    )
    result = CaptureView(
        supported=available,
        revision=row.capture_revision,
        eligible=row.capture_tasks if available else [],
    )
    slot = row.capture
    if not slot:
        return result
    state = current_state(slot, now)
    if slot.owner != owner or slot.command.project_id != project:
        if state in {"pending", "unknown"} or is_active(slot, state):
            result.state = "busy"
        return result
    result.state = state
    result.active = is_active(slot, state)
    result.task_id = slot.command.task_id
    result.record_id = slot.record_id
    if slot.receipt:
        result.record_id = slot.receipt.record_id or slot.record_id
        result.detail = slot.receipt.detail
        result.upload = slot.receipt.upload
    elif state == "unknown":
        result.detail = "命令已送达，尚未收到执行结果"
    elif state == "error":
        result.detail = "命令在送达前已过期"
    return result


def enqueue(row: Endpoint, command: CaptureCommand, owner: str, now: float) -> None:
    slot = row.capture
    if slot and slot.owner == owner and slot.command == command:
        return
    state = view(row, owner, command.project_id, now)
    if not state.supported or command.revision != row.capture_revision:
        raise ValueError("机器人录制能力或状态已变化，请刷新")
    if state.state in {"busy", "pending", "unknown"}:
        raise ValueError("有未完成的录制命令")
    if command.action == "start":
        if state.active or command.task_id not in state.eligible:
            raise ValueError("机器人已在录制或任务不适用于当前机器人")
    elif not state.active or command.task_id != state.task_id:
        raise ValueError("该任务没有活动录制")
    row.capture_revision += 1
    row.capture = CaptureSlot(
        owner=owner,
        instance_id=row.instance_id,
        command=command,
        record_id=state.record_id if state.active else "",
        previous_state="recording" if state.active else "idle",
        expires=now + 10,
    )


def acknowledge(row: Endpoint, receipt: CaptureReceipt) -> None:
    slot = row.capture
    if not slot or slot.command.request_id != receipt.request_id or not slot.delivered:
        raise ValueError("录制回执与已发送的命令不匹配")
    if slot.instance_id != row.instance_id and receipt.state not in {"saved", "interrupted"}:
        raise ValueError("新实例只能核实上一实例的最终录制结果")
    if slot.record_id and receipt.record_id and slot.record_id != receipt.record_id:
        raise ValueError("录制回执引用了不同的记录")
    expected = "saved" if slot.command.action == "stop" else "recording"
    if receipt.state not in {expected, "error", "interrupted"}:
        raise ValueError("录制回执状态与命令不匹配")
    if receipt.state in {"recording", "saved"} and not receipt.record_id:
        raise ValueError("录制回执缺少记录标识")
    previous = slot.receipt
    if previous and (
        previous.record_id != receipt.record_id
        or (
            previous.state != receipt.state
            and not (previous.state == "recording" and receipt.state == "interrupted")
        )
    ):
        raise ValueError("录制回执与已确认结果冲突")
    if previous != receipt:
        row.capture_revision += 1
        slot.receipt = receipt
