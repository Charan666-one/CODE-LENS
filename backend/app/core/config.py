from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    VERSION: str = "2.0"

    DATABASE_URL: str = "postgresql+asyncpg://codelens:codelens_secret@localhost:5432/codelens"

    REDIS_URL: str = "redis://localhost:6379"

    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "codelens_neo4j"

    OPENAI_API_KEY: str = "sk-placeholder"

    GITHUB_CLIENT_ID: str = "placeholder"
    GITHUB_CLIENT_SECRET: str = "placeholder"

    JWT_SECRET: str = "codelens-dev-secret-minimum-32-characters"
    JWT_EXPIRE_HOURS: int = 1

    MAX_REPO_SIZE_MB: int = 500
    MAX_CONCURRENT_JOBS: int = 5

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
