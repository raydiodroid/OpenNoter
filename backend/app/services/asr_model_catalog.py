"""Conservative suggestions for /audio/transcriptions; manual model IDs stay valid."""
import re


CAPABILITY_FIELDS = ('capabilities', 'task', 'tasks', 'pipeline_tag', 'endpoints',
                     'supported_endpoints', 'supported_endpoint_types')
ASR_CAPABILITIES = {'asr', 'stt', 'transcribe', 'transcription', 'transcriptions',
                    'audio-transcription', 'speech-to-text', 'automatic-speech-recognition'}
OTHER_CAPABILITIES = {'chat', 'chat-completions', 'text-generation', 'image-generation',
                      'text-to-speech', 'tts', 'audio-generation', 'embedding', 'embeddings',
                      'rerank', 'realtime'}
ASR_NAME = re.compile(
    r'(?:^|[/_.:-])(?:whisper|sensevoice(?:small|large)?|paraformer|funasr|fireredasr\d*|'
    r'asr|stt|transcribe|transcription|speech-to-text)(?:$|[/_.:-])'
)
OTHER_NAME = re.compile(
    r'(?:^|[/_.:-])(?:chat|image|tts\d*|text-to-speech|embeddings?|reranker?|realtime|live)'
    r'(?:$|[/_.:-])'
)


def _labels(value):
    if isinstance(value, str):
        yield value.strip().lower().replace('_', '-').rstrip('/')
    elif isinstance(value, list):
        for item in value:
            yield from _labels(item)
    elif isinstance(value, dict):
        # Capability maps only count affirmative flags, never {"asr": false}.
        for key, enabled in value.items():
            if enabled is True:
                yield from _labels(key)


def _is_asr_capability(label):
    return label in ASR_CAPABILITIES or ('/' + label).endswith('/audio/transcriptions')


def _is_other_capability(label):
    return label in OTHER_CAPABILITIES or ('/' + label).endswith((
        '/chat/completions', '/images/generations', '/images/edits', '/audio/speech',
        '/embeddings', '/realtime',
    ))


def transcription_models(items):
    names = set()
    for item in items:
        name = item.get('id') if isinstance(item, dict) else item
        if not isinstance(name, str) or not name.strip():
            continue
        name = name.strip()
        labels = {label for field in CAPABILITY_FIELDS
                  for label in _labels(item.get(field))} if isinstance(item, dict) else set()
        # Explicit transcription support also covers models with several capabilities.
        if any(_is_asr_capability(label) for label in labels):
            names.add(name)
            continue
        explicitly_disabled = isinstance(item, dict) and any(
            isinstance(item.get(field), dict) and any(
                enabled is False and any(_is_asr_capability(label) for label in _labels(key))
                for key, enabled in item[field].items()
            ) for field in CAPABILITY_FIELDS
        )
        if explicitly_disabled or any(_is_other_capability(label) for label in labels):
            continue
        # Generic "openai" / "audio" metadata does not establish ASR support.
        normalized = name.lower().replace('_', '-')
        if ASR_NAME.search(normalized) and not OTHER_NAME.search(normalized):
            names.add(name)
    return sorted(names)
