import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.api.campaigns import router as campaign_router
from app.api.calls import router as call_router

# Ensure recordings directory exists
os.makedirs("recordings", exist_ok=True)

app = FastAPI(
    title="Calling Platform API",
    version="1.0.0",
)

app.mount("/recordings", StaticFiles(directory="recordings"), name="recordings")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(call_router, prefix="/api", tags=["Calls"])
app.include_router(campaign_router, prefix="/api", tags=["Campaigns"])


@app.get("/")
def home():
    return {
        "status": "running",
        "message": "Backend is working",
    }