# -*- coding: utf-8 -*-
"""
Main FastAPI application
"""

# Load .env file first, before any other imports
from dotenv import load_dotenv
load_dotenv()  # Loads from .env in current directory or parent directories

import os

# Completely disable OpenTelemetry SDK to prevent metrics export errors
# Must be set BEFORE importing any Google Cloud libraries
os.environ["OTEL_SDK_DISABLED"] = "true"
os.environ["OTEL_METRICS_EXPORTER"] = "none"
os.environ["OTEL_TRACES_EXPORTER"] = "none"
os.environ["OTEL_LOGS_EXPORTER"] = "none"
os.environ["SPANNER_ENABLE_BUILT_IN_METRICS"] = "false"
os.environ["SPANNER_ENABLE_EXTENDED_TRACING"] = "false" 
os.environ["SPANNER_ENABLE_METRICS"] = "false"
os.environ["GOOGLE_CLOUD_DISABLE_METRICS"] = "true"

import uvicorn
from inspect import signature
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .api.private_network import PrivateNetworkAccessMiddleware
from fastapi.responses import FileResponse

from .api.projects import router as projects_router
from .api.embedded import router as embedded_router
from .api.relational import router as relational_router
from .api.database import router as database_router
from .api.google import router as google_router
from .api.settings import router as settings_router
from .api.admin import router as admin_router

# Create FastAPI app
app = FastAPI(
    title="Kineviz Database Proxy",
    description="Secure middleware for connecting GraphXR to databases",
    version=__version__,
    docs_url="/docs",
    redoc_url="/redoc"
)

# Add CORS middleware
cors_private_network_options = {}
if "allow_private_network" in signature(CORSMiddleware).parameters:
    # Newer Starlette rejects private-network preflights unless explicitly enabled.
    cors_private_network_options["allow_private_network"] = True

app.add_middleware(
    CORSMiddleware,
    # Echo the requesting origin so credentialed requests work across origins.
    allow_origin_regex=".*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    **cors_private_network_options,
)

# GraphXR calls this proxy directly from the browser, so Chrome's Private Network
# Access preflight has to be answered as well. Keep the header fallback for older
# Starlette versions, wrapping CORS so it can update the preflight response.
app.add_middleware(PrivateNetworkAccessMiddleware)

# Include API routers
app.include_router(admin_router)  # Admin auth (login/logout/status)
app.include_router(projects_router)
# Before database_router on purpose: its generic /api/{database_type}/{project_name}
# route would otherwise swallow /api/embedded/... .
app.include_router(embedded_router)
# Before database_router for the same reason as embedded_router: its generic
# /api/{database_type}/{project_name} pattern would otherwise swallow /api/relational/...
app.include_router(relational_router)
app.include_router(database_router)
app.include_router(google_router)
app.include_router(settings_router)


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy", 
        "message": "Kineviz Database Proxy is running",
        "version": __version__,
        "environment": "development",
        "auto_reload": "enabled"
    }

# Mount static files from package
static_dir = Path(__file__).parent / "static"
frontend_dist = Path(__file__).parent.parent.parent / "frontend" / "dist"
 
if static_dir.exists() or frontend_dist.exists():
    static_dir = frontend_dist if frontend_dist.exists() else static_dir
    print(f"[INFO] Serving static files from: {static_dir}")
    # Development: Use frontend dist directory
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    
    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        """Serve the frontend application for all unmatched paths"""
        index_file = static_dir / "index.html"
        if index_file.exists():
            return FileResponse(str(index_file))
        return {"message": "Frontend not available"}
        
else:
    @app.get("/")
    async def serve_fallback():
        """Fallback when frontend is not built"""
        return {
            "message": "Frontend not available", 
            "hint": "Run 'python scripts/build_frontend.py' to build and package frontend"
        }


def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Kineviz Database Proxy")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=9080, help="Port to bind to")
    parser.add_argument("--ui", action="store_true", help="Enable UI mode")
    parser.add_argument("--dev", action="store_true", help="Development mode with hot reload")
    
    args = parser.parse_args()
    
    # Configure logging
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    
    # Run the server
    uvicorn.run(
        "kineviz_database_proxy.main:app",
        host=args.host,
        port=args.port,
        reload=args.dev,
        log_level="info" if not args.dev else "debug"
    )


if __name__ == "__main__":
    main()
