import pytest

from app.services.asr_model_catalog import transcription_models


@pytest.mark.parametrize('name', [
    'whisper-1', 'whisper-large-v3-turbo', 'distil-whisper/distil-large-v3',
    'FunAudioLLM/SenseVoiceSmall', 'sensevoice', 'paraformer', 'paraformer-en',
    'fun-asr-nano', 'FunASR', 'FireRedASR2', 'Qwen/Qwen3-ASR-1.7B',
    'gpt-transcribe', 'gpt-4o-mini-transcribe-2025-12-15', 'custom_stt_v2',
])
def test_known_transcription_names_are_suggested_without_capability_metadata(name):
    assert transcription_models([{'id': name, 'supported_endpoint_types': ['openai']}]) == [name]


@pytest.mark.parametrize('name', [
    'gpt-6', 'qwen27b', 'gpt-image-2', 'Qwen3-Embedding-0.6B', 'breeze-tts2',
    'fireredtts3', 'CosyVoice2', 'gpt-audio', 'unknown-alias', 'speech',
    'whisper-chat', 'asr-image', 'gpt-realtime-whisper', 'gpt-live-transcribe',
    'notwhisper', 'not-asr-tts',
])
def test_unrelated_realtime_and_unknown_models_are_not_suggested(name):
    assert transcription_models([name]) == []


def test_capabilities_override_names_and_do_not_treat_generic_audio_as_transcription():
    models = [
        {'id': 'custom-one', 'supported_endpoints': ['audio/transcriptions']},
        {'id': 'custom-two', 'capabilities': {'transcription': True, 'chat': True}},
        {'id': 'custom-three', 'pipeline_tag': 'automatic-speech-recognition'},
        {'id': 'custom-four', 'task': 'speech_to_text'},
        {'id': 'custom-five', 'supported_endpoint_types': ['openai', 'audio-transcription']},
        {'id': 'custom-six', 'tasks': ['chat', 'asr']},
        {'id': 'whisper-1', 'capabilities': {'transcription': False}},
        {'id': 'sensevoice', 'supported_endpoints': ['chat/completions']},
        {'id': 'paraformer', 'task': 'image-generation'},
        {'id': 'unknown', 'capabilities': ['audio']},
        {'id': 'other', 'modalities': ['audio', 'text']},
        {'id': 'speech', 'capabilities': {'transcription': 'false'}},
    ]
    assert transcription_models(models) == sorted(['custom-one', 'custom-two', 'custom-three',
                                                 'custom-four', 'custom-five', 'custom-six'])


def test_filters_mixed_gateway_catalog_and_preserves_original_ids():
    items = ['sensevoice', {'id': 'sensevoice'}, {'id': ' paraformer-en '},
             {'id': 'fun-asr-nano'}, {'id': 'gpt-6'}, {'id': 'gpt-image-2'},
             {'id': 'fireredtts3'}, {'id': 'breeze-tts2'}, {'id': 'Qwen3-Embedding-0.6B'},
             {}, {'id': ['invalid']}, None, False, 1, '', {'id': '  '}]
    assert transcription_models(items) == ['fun-asr-nano', 'paraformer-en', 'sensevoice']
