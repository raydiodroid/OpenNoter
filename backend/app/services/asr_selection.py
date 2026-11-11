"""Per-request ASR choice. Empty means automatic; explicit choices retain source."""
from app.services.asr_providers import AsrProviderService
from app.services.byok_config import byok_config
from app.services.credential_resolver import resolve_asr, CredentialRequired, CredentialBroken


def validate_asr_selection(selection, user_id):
    if not selection or selection == 'auto':
        return None
    if selection == 'own:asr':
        resolve_asr(user_id, online=True, source='own')
        return selection
    entry = AsrProviderService.require_enabled(selection)
    if entry['engine'] == 'groq' and byok_config.get_asr_mode() == 'required':
        raise CredentialRequired('当前策略要求个人转写凭据，不能选择站点在线转写')
    if entry['id'] == 'mlx-whisper':
        from app.transcriber.transcriber_provider import MLX_WHISPER_AVAILABLE
        if not MLX_WHISPER_AVAILABLE:
            raise ValueError('当前服务器不支持 MLX Whisper')
    if selection in ('fast-whisper', 'mlx-whisper'):
        from app.services.transcriber_config_manager import TranscriberConfigManager
        from app.transcriber.model_readiness import _check_whisper_model_exists, _check_mlx_whisper_model_exists
        size = TranscriberConfigManager().get_whisper_model_size()
        check = _check_whisper_model_exists if selection == 'fast-whisper' else _check_mlx_whisper_model_exists
        if not check(size):
            raise ValueError('请先在音频转写设置中下载并选择本地模型')
    return selection


def list_asr_choices(user_id):
    mode = byok_config.get_asr_mode()
    choices = []
    if user_id is not None and mode != 'shared':
        try:
            own = resolve_asr(user_id, online=True, source='own')
            choices.append({'id': 'own:asr', 'label': f'个人转写 · {own.model_name}', 'source': 'own'})
        except (CredentialRequired, CredentialBroken):
            pass  # Automatic selection still returns the actionable policy error.
    import platform
    for p in AsrProviderService.list():
        if not p['enabled'] or (mode == 'required' and p['engine'] == 'groq'):
            continue
        if p['id'] == 'mlx-whisper' and platform.system() != 'Darwin':
            continue
        try:
            validate_asr_selection(p['id'], user_id)
        except (ValueError, CredentialRequired):
            continue
        choices.append({'id': p['id'], 'label': p['name'] + (f" · {p['model_name']}" if p['model_name'] else ''), 'source': 'shared'})
    return choices
