from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/datacrazy"
    api_master_key: str = "dc-master-key-change-me"
    meta_test_event_code: str = ""
    anthropic_api_key: str = ""
    datacrazy_api_url: str = "https://api.g1.datacrazy.io"
    datacrazy_api_token: str = ""

    class Config:
        env_file = ".env"


settings = Settings()
