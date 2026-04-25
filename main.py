import logging

from fastapi import FastAPI
from dotenv import load_dotenv
from routers.intake import router

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

app = FastAPI(title="Care_Circle_Service")

app.include_router(router, prefix="/api/v1")


@app.get("/")
def health_check():
    return {"status": "ok"}
