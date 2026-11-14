from app.core.settings import Settings
from app.storage.base import ObjectStore
from app.storage.local import LocalObjectStore


def get_object_store(settings: Settings) -> ObjectStore:
    if settings.object_store_backend == "local":
        return LocalObjectStore(settings.object_store_root)
    raise NotImplementedError("the s3 storage backend is not implemented; use OBJECT_STORE_BACKEND=local")
