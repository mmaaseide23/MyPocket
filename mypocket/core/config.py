from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# This file lives at mypocket/core/config.py; the repo root is two levels up
# (mypocket/core → mypocket → repo root). If you move this file, fix the count.
ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")

    database_url: str = f"sqlite:///{DATA_DIR / 'mypocket.db'}"

    teller_application_id: str | None = None
    teller_environment: str = "development"
    teller_cert_path: str | None = None
    teller_key_path: str | None = None

    etrade_consumer_key: str | None = None
    etrade_consumer_secret: str | None = None
    etrade_environment: str = "sandbox"


settings = Settings()
