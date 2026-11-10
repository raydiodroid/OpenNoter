"""Persist the site LLM default; ASR keeps its existing transcriber configuration."""
import json
import os
import tempfile
from pathlib import Path
from threading import RLock

LOCK = RLock()


class ModelDefaults:
    def __init__(self, path='config/model_defaults.json'):
        self.path = Path(path)

    def get(self):
        if not self.path.exists():
            return {'provider_id': None, 'model_name': None}
        # A corrupt explicit default must not silently route content elsewhere.
        data = json.loads(self.path.read_text())
        return {'provider_id': data.get('provider_id'), 'model_name': data.get('model_name')}

    def set(self, provider_id, model_name):
        from app.services.model_selection import resolve_model_selection
        if bool(provider_id) != bool(model_name):
            raise ValueError('请选择完整的供应商和模型')
        if provider_id:
            if provider_id.startswith('own:'):
                raise ValueError('全站默认只能使用站点模型')
            resolve_model_selection(provider_id, model_name)
        data = {'provider_id': provider_id or None, 'model_name': model_name or None}
        with LOCK:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, path = tempfile.mkstemp(dir=self.path.parent, prefix='.model-defaults-')
            try:
                with os.fdopen(fd, 'w') as f:
                    json.dump(data, f, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(path, self.path)
            finally:
                if os.path.exists(path):
                    os.unlink(path)
        return data


model_defaults = ModelDefaults()
