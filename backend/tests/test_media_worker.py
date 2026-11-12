import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from tests.test_auth_sessions import sessions  # noqa: F401


@pytest.fixture
def worker(sessions, monkeypatch, tmp_path):
    from app.services import media_worker
    monkeypatch.setenv('MEDIA_JOBS_DIR', str(tmp_path / 'jobs'))
    return media_worker


@pytest.mark.parametrize('bad', ['/etc/passwd', '/job/../private/key', '/job2/result.json', 'relative.mp3'])
def test_artifacts_cannot_escape_workspace(worker, bad):
    workspace = worker.MediaWorkspace(None)
    with pytest.raises(ValueError):
        workspace.path(bad)


def test_symlink_and_directory_results_rejected(worker, tmp_path):
    workspace = worker.MediaWorkspace(None)
    outside = tmp_path / 'secret'
    outside.write_text('secret')
    (workspace.root / 'result.json').symlink_to(outside)
    with pytest.raises(ValueError):
        workspace.path('/job/result.json')
    with pytest.raises(ValueError):
        workspace.path('/job')


def test_transcriber_gets_only_resolved_asr_credential(worker, monkeypatch):
    from app.services import credential_resolver
    credential = credential_resolver.Credential('asr-key', 'https://speech.example/v1', 'whisper', 'own')
    monkeypatch.setattr(credential_resolver, 'resolve_asr', lambda *a, **kw: credential)
    workspace = Mock()
    workspace.remote_path.return_value = '/job/audio.mp3'
    workspace.call.return_value = {'full_text': 'hello', 'segments': [], 'language': 'en'}
    owner = SimpleNamespace(user_id=7, transcriber_type='groq', transcriber_source='own', model_size='base')
    assert worker.WorkerTranscriber(workspace, owner).transcript('/input').full_text == 'hello'
    args = workspace.call.call_args.kwargs
    assert args['credential'] == {'api_key': 'asr-key', 'base_url': 'https://speech.example/v1', 'model_name': 'whisper', 'source': 'own'}
    assert set(args) == {'path', 'engine', 'model_size', 'credential'}


def test_broker_unavailable_never_falls_back_to_local(worker, monkeypatch):
    workspace = worker.MediaWorkspace(None)
    connection = Mock()
    connection.request.side_effect = ConnectionRefusedError()
    monkeypatch.setattr(worker, 'UnixConnection', lambda *a, **kw: connection)
    monkeypatch.setattr(worker, 'EgressLease', lambda *a, **kw: SimpleNamespace(url='http://ticket:x@proxy', close=lambda: None))
    with pytest.raises(ConnectionRefusedError):
        workspace.call('subtitles', platform='youtube', url='https://youtube.com/watch?v=id')
    connection.close.assert_called_once()


@pytest.fixture
def broker(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location('media_broker_test', Path(__file__).parents[1] / 'media_broker.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setenv('MEDIA_MODELS_PATH', '/trusted/models')
    return module


def test_broker_uses_subpath_mount_nonroot_and_fixed_resource_limits(broker):
    job = 'a' * 32
    (broker.ROOT / job).mkdir()
    args = broker.command(job, 'test-container')
    assert '--user' in args and '10001:0' in args
    assert '--read-only' in args and '--cap-drop=ALL' in args
    assert '--security-opt=no-new-privileges' in args and '--memory=2g' in args
    assert f'type=volume,src={broker.VOLUME},dst=/job,volume-subpath={job},volume-nocopy' in args
    assert not any('docker.sock' in arg or 'JWT' in arg or '/app/config' in arg for arg in args)
    offline = broker.command(job, 'offline', networked=False)
    assert offline[offline.index('--network') + 1] == 'none'


@pytest.mark.parametrize('job', ['../secret', '/etc', 'a'*31, 'a'*32+',dst=/host', '--privileged'])
def test_broker_rejects_mount_injection(broker, job):
    with pytest.raises(ValueError):
        broker.command(job, 'test-container')


def test_timeout_kills_container_and_releases_slot(broker, monkeypatch):
    import subprocess
    job = 'b'*32
    (broker.ROOT / job).mkdir()
    process = Mock()
    process.communicate.side_effect = subprocess.TimeoutExpired('fixed-command', 1)
    monkeypatch.setattr(broker.subprocess, 'Popen', lambda *a, **kw: process)
    cleanup = Mock()
    monkeypatch.setattr(broker.subprocess, 'run', cleanup)
    with pytest.raises(ValueError, match='timed out'):
        broker.execute({'job_id': job, 'operation': 'download', 'cookie': 'private'})
    process.kill.assert_called_once()
    assert cleanup.call_args.args[0][:3] == ['docker', 'rm', '-f']
    assert job not in broker.ACTIVE
    assert broker.SLOTS.acquire(blocking=False)
    broker.SLOTS.release()


def test_cleanup_refuses_active_workspace(broker):
    job = 'c'*32
    (broker.ROOT / job).mkdir()
    broker.ACTIVE.add(job)
    with pytest.raises(ValueError):
        broker.cleanup(job)
    assert (broker.ROOT / job).exists()
    broker.ACTIVE.clear()
    broker.cleanup(job)
    assert not (broker.ROOT / job).exists()
