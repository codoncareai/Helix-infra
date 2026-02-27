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

from typing import Any, Dict, List, Optional, Union

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
      "service_count": 2,
      "services": {
        "web": {
          "image": "nginx:latest",
          "ports": ["8080:80"],
          "environment": ["ENV=production", "DEBUG=false"],
          "volumes": ["./app:/usr/share/nginx/html"]
        },
        "db": {
          "image": "postgres:16",
          "ports": ["5432:5432"],
          "environment": {
            "POSTGRES_DB": "app",
            "POSTGRES_USER": "postgres"
          },
          "volumes": ["db_data:/var/lib/postgresql/data"]
        }
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

    def normalize_to_list(value: Any) -> List[Any]:
        """Normalize any supported value to a list; missing values become []."""
        if value is None:
            return []
        if isinstance(value, list):
            return value
        return [value]

    def normalize_environment(
        value: Any,
    ) -> Union[List[Any], Dict[str, Any]]:
        """
        Normalize environment to either a list or dict.
        Missing/invalid values become an empty list.
        """
        if value is None:
            return []
        if isinstance(value, (list, dict)):
            return value
        return [value]

    # Build normalized, nested service metadata required by Helix Infra.
    normalized_services: Dict[str, Dict[str, Any]] = {}

    # Iterate through each service and extract requested fields.
    for service_name, config in services_data.items():
        service_name_str = str(service_name)

        # Ensure each service config is a dictionary before extraction.
        service_config = config if isinstance(config, dict) else {}

        image_value: Optional[str] = (
            str(service_config["image"])
            if service_config.get("image") is not None
            else None
        )
        ports = normalize_to_list(service_config.get("ports"))
        environment = normalize_environment(service_config.get("environment"))
        volumes = normalize_to_list(service_config.get("volumes"))

        normalized_services[service_name_str] = {
            "image": image_value,
            "ports": ports,
            "environment": environment,
            "volumes": volumes,
        }

    # Return well-structured JSON payload.
    response_payload = {
        "service_count": len(normalized_services),
        "services": normalized_services,
    }

    return JSONResponse(content=response_payload)
