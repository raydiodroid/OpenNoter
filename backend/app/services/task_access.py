"""Task authorization shared by notes, chat and static artifacts."""
from app.services.auth import auth_enforced
from app.services.task_service import TaskService
from app.utils.note_paths import UnsafeTaskId


def can_access_payload(payload, user):
    if payload.get("deleted"):
        return False
    owner = payload.get("user_id")
    if owner is not None:
        return user is not None and owner == user.id
    return bool(user and user.is_superuser) or (user is None and not auth_enforced())


def can_access_task(task_id, user, root=None):
    service = TaskService(root)
    try:
        state = service.snapshot(task_id)
        if state is None:
            state = service.storage.read(task_id, ".status.json")
        if state is None and service.storage.read(task_id) is not None:
            state = {"user_id": None}
        return state is not None and can_access_payload(state, user)
    except UnsafeTaskId:
        return False
