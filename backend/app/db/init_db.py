from app.db.models.models import Model
from app.db.models.providers import Provider
from app.db.models.video_tasks import VideoTask
from app.db.models.users import User  # noqa: F401  建表需要先导入
from app.db.models.api_keys import ApiKey  # noqa: F401
from app.db.models.user_credentials import UserCredential  # noqa: F401  BYOK
from app.db.models.user_cookies import UserCookie  # noqa: F401  BYOK Cookie
from app.db.engine import get_engine, Base


def init_db():
    engine = get_engine()

    Base.metadata.create_all(bind=engine)