"""
FastAPI application for analyzing Docker Compose YAML files.

Run locally:
1) Install dependencies:
   pip install fastapi uvicorn pyyaml python-multipart

2) Start the server:
   uvicorn main:app --reload

3) Test endpoint:
   POST http://127.0.0.1:8000/analyze
   form-data key: file (upload docker-compose.yml)
"""

from typing import Any, Dict, List

import yaml
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

# Initialize FastAPI application.
app = FastAPI(
    title="Docker Compose Analyzer",
    description="Upload a Docker Compose YAML file and extract service metadata.",
    version="1.0.0",
)


@app.post("/analyze")
async def analyze_compose(file: UploadFile = File(...)) -> JSONResponse:
    """
    Analyze a Docker Compose YAML file and extract key service details.

    Expected request:
    - multipart/form-data with one file field named `file`

    JSON response example:
    {
      "services": ["web", "db"],
      "images": ["nginx:latest", "postgres:16"],
      "ports": {
        "web": ["8080:80"],
        "db": ["5432:5432"]
      },
      "environment": {
        "web": ["ENV=production", "DEBUG=false"],
        "db": {
          "POSTGRES_DB": "app",
          "POSTGRES_USER": "postgres"
        }
      },
      "volumes": {
        "web": ["./app:/usr/share/nginx/html"],
        "db": ["db_data:/var/lib/postgresql/data"]
      }
    }
    """

    # Basic content-type/filename validation.
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided.")

    # Read uploaded file bytes and decode as UTF-8 text.
    try:
        raw_bytes = await file.read()
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail="File must be UTF-8 encoded text.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Unable to read uploaded file: {exc}",
        ) from exc

    # Parse YAML safely into Python structures.
    try:
        parsed: Dict[str, Any] = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {exc}") from exc

    # Validate that the top-level structure is a mapping with services.
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="YAML root must be a mapping/object.")

    services_data = parsed.get("services")
    if not isinstance(services_data, dict):
        raise HTTPException(
            status_code=400,
            detail="Compose file must include a 'services' mapping.",
        )

    # Prepare extracted data containers.
    service_names: List[str] = []
    images: List[str] = []
    ports_by_service: Dict[str, List[Any]] = {}
    env_by_service: Dict[str, Any] = {}
    volumes_by_service: Dict[str, List[Any]] = {}

    # Iterate through each service and extract requested fields.
    for service_name, config in services_data.items():
        service_names.append(str(service_name))

        # Ensure each service config is a dictionary before extraction.
        service_config = config if isinstance(config, dict) else {}

        image_value = service_config.get("image")
        if image_value is not None:
            images.append(str(image_value))

        ports = service_config.get("ports", [])
        ports_by_service[str(service_name)] = ports if isinstance(ports, list) else [ports]

        environment = service_config.get("environment", [])
        env_by_service[str(service_name)] = environment

        volumes = service_config.get("volumes", [])
        volumes_by_service[str(service_name)] = (
            volumes if isinstance(volumes, list) else [volumes]
        )

    # Return well-structured JSON payload.
    response_payload = {
        "services": service_names,
        "images": images,
        "ports": ports_by_service,
        "environment": env_by_service,
        "volumes": volumes_by_service,
    }

    return JSONResponse(content=response_payload)
