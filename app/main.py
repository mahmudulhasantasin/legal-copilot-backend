import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.database import Base, engine
from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.contract import router as contract_router
from app.api.lawyer import router as lawyer_router

import os
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse





# Automatic SQL Database Tables Creation
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Legal Copilot & ContractMind Engine Backend",
    description="Production-Ready AI-Powered Legal Assistant for Bangladesh Jurisdiction",
    version="1.0.0"
)

# CORS Middleware Setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Router Registration
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(contract_router)
app.include_router(lawyer_router)

@app.get("/")
def root_health_check():
    return {
        "status": "online",
        "engine": "Legal Copilot & ContractMind BD Engine",
        "version": "1.0.0"
    }

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)