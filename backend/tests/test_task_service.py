"""Lifecycle integration tests use a real temporary SQLite database and file adapter."""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest


@pytest.fixture()
def service(tmp_path, monkeypatch):
    saved = {k: v for k, v in sys.modules.items() if k == 'app' or k.startswith('app.')}
    for k in saved:
        sys.modules.pop(k, None)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{tmp_path / "db.sqlite"}')
    monkeypatch.setenv('NOTE_OUTPUT_DIR', str(tmp_path / 'notes'))
    monkeypatch.setenv('OPENNOTER_JWT_SECRET', 'test-lifecycle-secret')
    from app.db.init_db import init_db
    from app.db.engine import SessionLocal
    from app.db.models.users import User
    from app.services.task_service import TaskService
    init_db()
    with SessionLocal.begin() as db:
        db.add(User(id=1, email='one@example.com'))
        db.add(User(id=2, email='two@example.com'))
    yield TaskService(tmp_path / 'notes')
    for k in list(sys.modules):
        if k == 'app' or k.startswith('app.'):
            sys.modules.pop(k, None)
    sys.modules.update(saved)


def claim(service, retry=False, task_id='task', user_id=1):
    return service.claim(task_id, user_id=user_id, platform='youtube',
                         video_url='https://youtube.com/watch?v=example',
                         request={'video_url': 'https://youtube.com/watch?v=example'}, retry=retry)


def test_concurrent_retry_claims_exactly_one_attempt(service):
    from app.services.task_service import TaskConflict
    first = claim(service)
    service.update('task', 'FAILED', attempt_id=first)
    def submit(_):
        try:
            return claim(service, retry=True)
        except TaskConflict:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        accepted = list(pool.map(submit, range(8)))
    assert sum(x is not None for x in accepted) == 1


def test_stale_attempt_cannot_update_or_overwrite_result(service):
    from app.services.task_service import StaleAttempt
    first = claim(service)
    service.update('task', 'FAILED', attempt_id=first)
    second = claim(service, retry=True)
    service.save_result('task', {'markdown': 'new'}, attempt_id=second)
    with pytest.raises(StaleAttempt):
        service.update('task', 'SUCCESS', attempt_id=first)
    with pytest.raises(StaleAttempt):
        service.save_result('task', {'markdown': 'old'}, attempt_id=first)
    assert service.storage.read('task')['markdown'] == 'new'
    assert service.snapshot('task')['attempt_id'] == second


def test_retry_cannot_transfer_ownership(service):
    from app.services.task_service import TaskConflict
    first = claim(service)
    service.update('task', 'FAILED', attempt_id=first)
    with pytest.raises(TaskConflict):
        claim(service, retry=True, user_id=2)
    assert service.snapshot('task')['user_id'] == 1


def test_quota_counts_attempts_after_artifact_and_note_deletion(service):
    from app.services.quota import _count_user_today
    from app.db.video_task_dao import delete_task_by_task_id
    first = claim(service)
    service.update('task', 'FAILED', attempt_id=first)
    second = claim(service, retry=True)
    service.update('task', 'FAILED', attempt_id=second)
    assert _count_user_today(1, service.storage.root) == 2
    delete_task_by_task_id('task')
    for path in service.storage.root.iterdir():
        path.unlink()
    assert _count_user_today(1, service.storage.root) == 2


def test_detail_uses_database_when_status_projection_is_missing(service):
    first = claim(service)
    service.update('task', 'FAILED', 'actual failure', attempt_id=first)
    for path in service.storage.root.iterdir():
        path.unlink()
    from app.routers.note import get_task_status
    response = get_task_status('task', SimpleNamespace(id=1, is_superuser=False))
    data = json.loads(response.body)['data']
    assert data['status'] == 'FAILED'
    assert data['message'] == 'actual failure'


def test_atomic_storage_failure_preserves_previous_result(service, monkeypatch):
    service.storage.write('task', {'markdown': 'previous'})
    import app.storage.task_files as files
    def fail(*args):
        raise OSError('disk full')
    monkeypatch.setattr(files.os, 'replace', fail)
    with pytest.raises(OSError):
        service.storage.write('task', {'markdown': 'next'})
    assert service.storage.read('task') == {'markdown': 'previous'}
    assert list(service.storage.root.glob('.*')) == []


def test_status_projection_failure_does_not_hide_database_state(service, monkeypatch):
    first = claim(service)
    monkeypatch.setattr(service.storage, 'write', lambda *args: (_ for _ in ()).throw(OSError('disk full')))
    service.update('task', 'FAILED', 'failure', attempt_id=first)
    assert service.snapshot('task')['status'] == 'FAILED'


def test_restart_recovers_tasks_without_files_and_without_loading_transcriber(service):
    claim(service)
    for path in service.storage.root.iterdir():
        path.unlink()
    assert service.recover('restart') == 1
    assert service.snapshot('task')['status'] == 'FAILED'
    assert 'app.transcriber.transcriber_provider' not in sys.modules


def test_generator_construction_does_not_load_transcriber(service, monkeypatch):
    from app.services.note import NoteGenerator
    monkeypatch.setattr(NoteGenerator, '_init_transcriber', lambda _: pytest.fail('eager model load'))
    generator = NoteGenerator(user_id=1)
    assert generator.transcriber is None


def test_worker_initialization_failure_is_terminal(service, monkeypatch):
    first = claim(service)
    from app.services.note import NoteGenerator
    from app.services.task_worker import submit_note
    monkeypatch.setattr(NoteGenerator, '__init__', lambda *args, **kwargs: (_ for _ in ()).throw(ValueError('bad config')))
    future = submit_note('task', user_id=1, attempt_id=first, root=service.storage.root)
    future.result(timeout=5)
    assert service.snapshot('task')['status'] == 'FAILED'


def test_delete_running_task_is_rejected(service):
    claim(service)
    from app.routers.note import delete_task_by_id
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        delete_task_by_id('task', SimpleNamespace(id=1, is_superuser=False))
    assert exc.value.status_code == 409
    assert not service.snapshot('task')['deleted']


def test_missing_success_artifact_is_terminal_failure(service):
    attempt = claim(service)
    service.update('task', 'SUCCESS', attempt_id=attempt)
    from app.routers.note import get_task_status
    data = json.loads(get_task_status('task', SimpleNamespace(id=1, is_superuser=False)).body)['data']
    assert data['status'] == 'FAILED'


def test_asr_uses_own_key_endpoint_and_model(service, monkeypatch):
    from app.services import credential_resolver as cr
    from app.db.models.user_credentials import CAP_ASR
    monkeypatch.setattr(cr.byok_config, 'get_asr_mode', lambda: 'required')
    monkeypatch.setattr(cr, '_load_credential', lambda uid, cap: SimpleNamespace(
        capability=CAP_ASR, base_url='https://own.example/v1', model_name='own-asr', api_key='encrypted'))
    monkeypatch.setattr(cr.credential_crypto, 'reveal', lambda *args, **kw: 'own-key')
    own = cr.resolve_asr(1, online=True)
    assert (own.api_key, own.base_url, own.model_name) == ('own-key', 'https://own.example/v1', 'own-asr')


def test_required_asr_never_falls_back_to_site(service, monkeypatch):
    from app.services import credential_resolver as cr
    monkeypatch.setattr(cr.byok_config, 'get_asr_mode', lambda: 'required')
    monkeypatch.setattr(cr, '_load_credential', lambda *args: None)
    with pytest.raises(cr.CredentialRequired):
        cr.resolve_asr(1, online=True)
    assert cr.resolve_asr(1, online=False) is None  # local computation incurs no upstream charge


def test_schema_upgrade_is_idempotent_and_preserves_old_tasks(service, tmp_path, monkeypatch):
    from sqlalchemy import create_engine, inspect, text
    from app.db import init_db as module
    engine = create_engine(f'sqlite:///{tmp_path / "old.sqlite"}')
    with engine.begin() as db:
        db.execute(text('CREATE TABLE video_tasks (id INTEGER PRIMARY KEY, task_id VARCHAR(255) UNIQUE, video_id VARCHAR(255), platform VARCHAR(50), created_at TIMESTAMP)'))
        db.execute(text("INSERT INTO video_tasks VALUES (1, 'old-task', 'video', 'youtube', '2025-01-01 00:00:00')"))
    monkeypatch.setattr(module, 'get_engine', lambda: engine)
    module.init_db()
    module.init_db()
    assert {'attempt_id', 'request_json', 'deleted_at', 'status'} <= {c['name'] for c in inspect(engine).get_columns('video_tasks')}
    with engine.connect() as db:
        assert db.execute(text('SELECT count(*) FROM task_attempts')).scalar_one() == 1
        assert db.execute(text('SELECT task_id FROM video_tasks')).scalar_one() == 'old-task'
    engine.dispose()


def test_delete_marks_database_before_artifact_cleanup(service, monkeypatch):
    attempt = claim(service)
    service.update('task', 'FAILED', attempt_id=attempt)
    def cleanup(task_id):
        assert service.snapshot(task_id)['deleted']
        return []
    monkeypatch.setattr(service.storage, 'delete', cleanup)
    assert service.delete('task') == ([], True)
    from app.services.task_access import can_access_task
    assert not can_access_task('task', SimpleNamespace(id=1, is_superuser=False), root=service.storage.root)


def test_deletion_tombstones_are_owner_scoped(service):
    from app.db.video_task_dao import list_tasks_for_user
    attempt = claim(service)
    service.update('task', 'FAILED', attempt_id=attempt)
    service.delete('task')
    assert list_tasks_for_user(1)[1] == 0
    items, total = list_tasks_for_user(1, include_deleted=True)
    assert total == 1 and items[0]['deleted'] and items[0]['attempt_id'] == attempt
    assert list_tasks_for_user(2, include_deleted=True)[1] == 0


def test_storage_quota_survives_missing_status_projection(service, monkeypatch, tmp_path):
    from app.services.quota import _user_storage_mb
    claim(service)
    monkeypatch.setenv('OUT_DIR', str(tmp_path / 'screenshots'))
    service.storage.write('task', {'markdown': 'x' * 1024})
    (service.storage.root / 'task.status.json').unlink()
    (service.storage.root / 'task_abcd_transcript.json').write_text('x' * 1024)
    screenshots = tmp_path / 'screenshots' / 'task'
    screenshots.mkdir(parents=True)
    (screenshots / 'one.png').write_bytes(b'x' * 1024)
    assert _user_storage_mb(1, service.storage.root) >= 3072 / 1024 / 1024
    assert _user_storage_mb(2, service.storage.root) == 0


def test_delayed_worker_does_not_run_under_a_new_attempt(service, monkeypatch):
    first = claim(service)
    service.update('task', 'FAILED', attempt_id=first)
    second = claim(service, retry=True)
    from app.services.note import NoteGenerator
    from app.services.task_worker import submit_note
    monkeypatch.setattr(NoteGenerator, '__init__', lambda *args, **kw: pytest.fail('stale work started'))
    submit_note('task', user_id=1, attempt_id=first, root=service.storage.root).result(timeout=5)
    assert service.snapshot('task')['attempt_id'] == second
    assert service.snapshot('task')['status'] == 'PENDING'


def test_executor_deduplicates_and_bounds_pending_work(service, monkeypatch):
    import threading
    from app.services.task_serial_executor import ConcurrentTaskExecutor
    executor = ConcurrentTaskExecutor(max_workers=1)
    release = threading.Event()
    monkeypatch.setenv('TASK_MAX_QUEUED', '1')
    try:
        future = executor.submit_task('one', lambda: release.wait(5))
        with pytest.raises(ValueError):
            executor.submit_task('one', lambda: None)
        with pytest.raises(ValueError):
            executor.submit_task('two', lambda: None)
        assert executor.active_task_ids() == frozenset({'one'})
    finally:
        release.set()
        future.result(timeout=5)
        executor.shutdown()


def test_reaper_never_promotes_stale_file_state_over_database(service):
    import os
    import time
    from app.services.task_interrupt import reap_stale_tasks
    attempt = claim(service)
    service.update('task', 'FAILED', 'authoritative failure', attempt_id=attempt)
    service.storage.write('task', {'status': 'TRANSCRIBING', 'user_id': 1}, '.status.json')
    os.utime(service.storage.root / 'task.status.json', (time.time() - 7200,) * 2)
    result = reap_stale_tasks(note_dir=service.storage.root, dry_run=False)
    assert result['reaped'] == 0
    assert service.snapshot('task')['message'] == 'authoritative failure'


def test_reaper_dry_run_counts_database_tasks_without_projection(service):
    from datetime import datetime, timedelta, timezone
    from app.db.models.video_tasks import VideoTask
    from app.services.task_interrupt import reap_stale_tasks
    claim(service)
    with service.sessions.begin() as db:
        db.query(VideoTask).update({'updated_at': datetime.now(timezone.utc) - timedelta(hours=2)})
    (service.storage.root / 'task.status.json').unlink()
    result = reap_stale_tasks(note_dir=service.storage.root, dry_run=True)
    assert result['stale'] == 1
    assert service.snapshot('task')['status'] == 'PENDING'
    assert reap_stale_tasks(note_dir=service.storage.root, dry_run=False)['reaped'] == 1


def test_missing_local_model_fails_only_when_transcription_is_needed(service, monkeypatch):
    from app.transcriber import model_readiness, transcriber_provider
    monkeypatch.setattr(model_readiness, '_check_whisper_model_exists', lambda _: False)
    monkeypatch.setattr(transcriber_provider, 'get_whisper_transcriber', lambda *a, **kw: pytest.fail('download started'))
    with pytest.raises(RuntimeError, match='尚未下载就绪'):
        transcriber_provider.get_transcriber('fast-whisper', model_size='base', require_ready=True)


def test_personal_asr_does_not_require_site_whisper_download(service, monkeypatch):
    from app.services import credential_resolver
    from app.transcriber import transcriber_provider
    credential = credential_resolver.Credential('key', 'https://own.example/v1', 'asr', 'own')
    monkeypatch.setattr(credential_resolver, 'resolve_asr', lambda *a, **kw: credential)
    transcriber = transcriber_provider.get_transcriber('fast-whisper', user_id=1, require_ready=True)
    assert transcriber.credential is credential


def test_submission_ledger_survives_user_and_task_maintenance_deletion(service):
    from app.db.models.users import User
    from app.db.models.task_attempts import TaskAttempt
    from app.db.video_task_dao import delete_task_by_task_id
    from app.routers.auth import admin_delete_user
    attempt = claim(service)
    service.update('task', 'FAILED', attempt_id=attempt)
    with service.sessions() as db:
        admin_delete_user(1, admin=SimpleNamespace(id=2), db=db)
    with service.sessions() as db:
        assert db.get(User, 1) is None
        assert db.get(TaskAttempt, attempt).user_id is None
    assert delete_task_by_task_id('task', soft=False)
    with service.sessions() as db:
        assert db.get(TaskAttempt, attempt) is not None


def test_http_submission_duplicate_result_and_retry_contract(service, monkeypatch):
    import threading
    import time
    from dataclasses import dataclass
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routers import note as router
    from app.services.note import NoteGenerator
    from app.db.models.task_attempts import TaskAttempt

    @dataclass
    class Result:
        markdown: str = '# Integration note'
        transcript: dict = None
        audio_meta: dict = None

    release = threading.Event()
    monkeypatch.setattr(router, 'resolve_model_selection', lambda *a, **kw: ('test', 'model'))
    monkeypatch.setattr(NoteGenerator, 'generate', lambda *a, **kw: (release.wait(5), Result())[1])
    monkeypatch.setattr(router.VectorStoreManager, 'index_task', lambda *a, **kw: None)
    app = FastAPI()
    app.include_router(router.router, prefix='/api')
    app.dependency_overrides[router.get_optional_user] = lambda: SimpleNamespace(id=1, is_superuser=False)
    payload = {'url': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'}
    try:
        with TestClient(app) as client:
            response = client.post('/api/notes', json=payload)
            assert response.status_code == 200, response.text
            task_id = response.json()['data']['task_id']
            duplicate = client.post('/api/notes', json={**payload, 'task_id': task_id})
            assert duplicate.status_code == 409, duplicate.text
            release.set()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                result = client.get(f'/api/tasks/{task_id}').json()['data']
                if result['status'] == 'SUCCESS':
                    break
                time.sleep(0.01)
            assert result['status'] == 'SUCCESS'
            assert result['result']['markdown'] == '# Integration note'
            assert result['request']['video_url'] == payload['url']
            # Wait for indexing/callback cleanup before the explicit retry.
            while router.task_serial_executor.is_active(task_id) and time.monotonic() < deadline:
                time.sleep(0.01)
            retry = client.post('/api/notes', json={**payload, 'task_id': task_id})
            assert retry.status_code == 200, retry.text
            with service.sessions() as db:
                assert db.query(TaskAttempt).filter_by(task_id=task_id).count() == 2
            while router.task_serial_executor.is_active(task_id) and time.monotonic() < deadline:
                time.sleep(0.01)
    finally:
        release.set()
