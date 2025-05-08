import os

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))


def get_data_dir():
    data_path = os.path.join(PROJECT_ROOT, "data")
    os.makedirs(data_path, exist_ok=True)
    return data_path


def get_model_dir(subdir: str = "whisper") -> str:
    base = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../models"))
    path = os.path.join(base, subdir)
    os.makedirs(path, exist_ok=True)
    return path


if __name__ == '__main__':
    print(get_data_dir())
