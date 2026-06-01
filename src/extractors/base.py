from abc import ABC, abstractmethod

from src.models import VideoMeta


class BaseExtractor(ABC):
    platform: str = ""

    @abstractmethod
    def match(self, url: str) -> bool: ...

    @abstractmethod
    def extract(self, url: str) -> VideoMeta: ...
