import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

from routers.auth import router as auth_router
from routers.onboarding import router as onboarding_router
from routers.documents import router as documents_router

app = FastAPI(
    title="CareCircle Service",
    description="Backend API for CareCircle — patient onboarding, document extraction, and health monitoring.",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tighten to frontend origin in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router,       prefix="/api/auth",      tags=["Auth"])
app.include_router(onboarding_router, prefix="/api/onboarding", tags=["Onboarding"])
app.include_router(documents_router,  prefix="/api/documents",  tags=["Documents"])


@app.get("/", tags=["Health"])
def health_check():
    return {"status": "ok", "version": "2.0.0"}
