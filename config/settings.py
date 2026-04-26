from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Supabase
    supabase_url: str
    supabase_service_key: str

    # Cloudflare R2
    r2_account_id: str
    r2_access_key_id: str
    r2_secret_access_key: str
    r2_bucket_name: str = "patient-documents"

    # Google Vision
    google_application_credentials: str = ""

    # OpenRouter
    openrouter_api_key: str
    openrouter_model: str = "google/gemini-2.0-flash-001"


settings = Settings()
