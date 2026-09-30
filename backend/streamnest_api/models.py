from pydantic import BaseModel, Field


class ResolveRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    adult_confirmed: bool = False


class HelperDownloadRequest(BaseModel):
    url: str = Field(min_length=12, max_length=8192)
    platform: str = Field(min_length=2, max_length=16)
    headers: dict[str, str] = Field(default_factory=dict)


class LocalCoreResolveRequest(BaseModel):
    url: str = Field(min_length=8, max_length=4096)


class LocalCoreTaskRequest(BaseModel):
    url: str = Field(min_length=8, max_length=4096)
    quality: str = Field(min_length=2, max_length=24)
    format: str = Field(pattern=r"^mp4$")


class ImageCollectionRequest(BaseModel):
    tickets: list[str] = Field(min_length=1, max_length=50)
    title: str = Field(min_length=1, max_length=240)


class FormatOption(BaseModel):
    id: str
    label: str
    detail: str
    size: str
    kind: str
    download_ticket: str


class ResolveResponse(BaseModel):
    platform: str
    title: str
    author: str
    duration: str
    duration_seconds: int | None
    thumbnail: str | None
    formats: list[FormatOption]


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


class PrepareResponse(BaseModel):
    file_ticket: str
    filename: str


class DownloadJobResponse(BaseModel):
    job_id: str
    status: str
    progress: float
    downloaded_bytes: int
    total_bytes: int | None
    speed: float | None
    eta: int | None
    file_ticket: str | None
    filename: str | None
    error: str | None
