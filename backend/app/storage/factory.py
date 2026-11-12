from app.core.settings import Settings
from app.storage.base import ObjectStore
from app.storage.local import LocalObjectStore


def get_object_store(settings: Settings) -> ObjectStore:
    if settings.object_store_backend == "local":
        return LocalObjectStore(settings.object_store_root)
    raise NotImplementedError("s3 backend arrives with S0.12 [OPT]")
