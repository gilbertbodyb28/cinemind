"""Public request/response models. Provider tokens never appear on these types."""

from datetime import datetime
from typing import List, Optional
import uuid

from pydantic import BaseModel, EmailStr, Field


class GenerateBody(BaseModel):
    model: Optional[str] = None


class User(BaseModel):
    user_id: str
    email: str
    name: str
    picture: Optional[str] = None
    created_at: datetime
    auth_provider: str = "local"
    has_password: bool = False


class ConnectionsUpdate(BaseModel):
    trakt_client_id: Optional[str] = None
    trakt_access_token: Optional[str] = None
    trakt_refresh_token: Optional[str] = None
    trakt_expires_at: Optional[int] = None
    simkl_client_id: Optional[str] = None
    simkl_access_token: Optional[str] = None
    plex_url: Optional[str] = None
    plex_token: Optional[str] = None
    ollama_url: Optional[str] = None
    ollama_model: Optional[str] = None
    anilist_access_token: Optional[str] = None
    seer_url: Optional[str] = None
    seer_api_key: Optional[str] = None
    tmdb_api_key: Optional[str] = None
    tvdb_api_key: Optional[str] = None
    ui_theme: Optional[str] = None
    glass_intensity: Optional[int] = None


class ConnectionsStatus(BaseModel):
    trakt_connected: bool = False
    trakt_username: Optional[str] = None
    trakt_expires_at: Optional[int] = None
    trakt_client_id_configured: bool = False
    simkl_connected: bool = False
    simkl_username: Optional[str] = None
    simkl_client_id_configured: bool = False
    plex_connected: bool = False
    plex_url: Optional[str] = None
    ollama_url: str
    ollama_model: str
    tmdb_configured: bool = False
    tvdb_configured: bool = False
    anilist_connected: bool = False
    anilist_username: Optional[str] = None
    anilist_client_id_configured: bool = False
    seer_connected: bool = False
    seer_url: Optional[str] = None


class HistoryItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    year: Optional[int] = None
    type: str  # movie | show
    genres: List[str] = []
    rating: Optional[float] = None
    poster: Optional[str] = None
    watched_at: Optional[str] = None
    source: str  # trakt | simkl | plex | demo


class Recommendation(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    year: int
    type: str
    genres: List[str]
    poster: str
    backdrop: Optional[str] = None
    synopsis: str
    match_score: int
    why: str
    tmdb_rating: float
    saved: bool = False
    dismissed: bool = False


class RegisterBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(min_length=1, max_length=80)


class LoginBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class SimklPoll(BaseModel):
    user_code: str


class DevicePoll(BaseModel):
    device_code: str
