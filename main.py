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

# Keywords used to identify common database/cache container images.
DATABASE_IMAGE_KEYWORDS = ("postgres", "mysql", "mariadb", "mongo", "redis")

# Initialize FastAPI application.
app = FastAPI(
    title="Docker Compose Analyzer",
    description="Upload a Docker Compose YAML file and extract service metadata.",
    version="1.0.0",
)


def normalize_to_list(value: Any) -> List[Any]:
    """Normalize any supported value to a list; missing values become []."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def normalize_environment(value: Any) -> Union[List[Any], Dict[str, Any]]:
    """
    Normalize environment to either a list or dict.
    Missing/invalid values become an empty list.
    """
    if value is None:
        return []
    if isinstance(value, (list, dict)):
        return value
    return [value]


def is_database_image(image: Optional[str]) -> bool:
    """
    Detect database-like services by image name.
    Includes common engines: postgres, mysql, mariadb, mongo, redis.
    """
    if not image:
        return False
    lowered = image.lower()
    return any(keyword in lowered for keyword in DATABASE_IMAGE_KEYWORDS)


def is_redis_image(image: Optional[str]) -> bool:
    """Redis-specific check used for cloud service recommendation override."""
    return bool(image and "redis" in image.lower())


def is_publicly_exposed_ports(ports: List[Any]) -> bool:
    """
    Detect likely public exposure from ports using two heuristics:
    1) Port mappings that include 80/443 in host/container side.
    2) Explicit 0.0.0.0 host binding.

    Supports short and long docker-compose port syntaxes.
    """
    for port in ports:
        # Short syntax examples: "8080:80", "0.0.0.0:443:443", "80"
        if isinstance(port, str):
            lowered = port.lower()
            if "0.0.0.0" in lowered:
                return True

            # Extract all numeric components from the mapping to detect 80/443.
            numeric_parts = [part for part in lowered.split(":") if part.isdigit()]
            if any(num in {"80", "443"} for num in numeric_parts):
                return True

        # Numeric short syntax example: 80
        elif isinstance(port, int):
            if port in {80, 443}:
                return True

        # Long syntax example:
        # - target: 80
        #   published: "8080"
        #   host_ip: "0.0.0.0"
        elif isinstance(port, dict):
            host_ip = str(port.get("host_ip", "")).lower()
            target = str(port.get("target", ""))
            published = str(port.get("published", ""))

            if host_ip == "0.0.0.0":
                return True
            if target in {"80", "443"} or published in {"80", "443"}:
                return True

    return False


def has_persistent_storage(volumes: List[Any]) -> bool:
    """If any volume entries are present, treat the service as stateful/persistent."""
    return len(volumes) > 0


def suggest_cloud_service(image: Optional[str], database_flag: bool) -> str:
    """
    Recommend a target managed service:
    - redis images -> Managed Cache Service
    - other database images -> Managed Relational Database Service
    - otherwise -> Virtual Machine or Container Service
    """
    if is_redis_image(image):
        return "Managed Cache Service"
    if database_flag:
        return "Managed Relational Database Service"
    return "Virtual Machine or Container Service"


def assess_exposure_risk(is_database: bool, is_publicly_exposed: bool) -> str:
    """Assess network exposure risk based on public accessibility and database sensitivity."""
    if is_publicly_exposed and is_database:
        return "High"
    if is_publicly_exposed and not is_database:
        return "Medium"
    return "Low"


def assess_data_loss_risk(is_database: bool, has_persistent_storage_flag: bool) -> str:
    """Assess data-loss risk based on statefulness and database classification."""
    if is_database and has_persistent_storage_flag:
        return "High"
    if has_persistent_storage_flag:
        return "Medium"
    return "Low"


def assess_migration_complexity(total_services: int) -> str:
    """Assess overall migration complexity using total compose service count."""
    if total_services > 6:
        return "High"
    if total_services > 3:
        return "Medium"
    return "Low"


def build_risk_assessment(
    is_database: bool,
    is_publicly_exposed: bool,
    has_persistent_storage_flag: bool,
    total_services: int,
) -> Dict[str, str]:
    """Assemble per-service risk fields using deterministic scoring rules."""
    return {
        "exposure_risk": assess_exposure_risk(is_database, is_publicly_exposed),
        "data_loss_risk": assess_data_loss_risk(is_database, has_persistent_storage_flag),
        "migration_complexity": assess_migration_complexity(total_services),
    }


def assess_overall_migration_risk(
    total_stateful_services: int,
    has_publicly_exposed_database: bool,
) -> str:
    """Assess global migration risk based on exposure of databases and stateful footprint."""
    if has_publicly_exposed_database:
        return "High"
    if total_stateful_services > 2:
        return "Medium"
    return "Low"


def build_infrastructure_summary(services: Dict[str, Dict[str, Any]]) -> Dict[str, Union[int, str]]:
    """Build aggregate infrastructure counters and overall migration risk level."""
    total_services = len(services)
    total_database_services = sum(1 for svc in services.values() if svc.get("is_database"))
    total_publicly_exposed_services = sum(
        1 for svc in services.values() if svc.get("is_publicly_exposed")
    )
    total_stateful_services = sum(
        1 for svc in services.values() if svc.get("has_persistent_storage")
    )

    has_publicly_exposed_database = any(
        svc.get("is_database") and svc.get("is_publicly_exposed") for svc in services.values()
    )

    return {
        "total_services": total_services,
        "total_database_services": total_database_services,
        "total_publicly_exposed_services": total_publicly_exposed_services,
        "total_stateful_services": total_stateful_services,
        "overall_migration_risk": assess_overall_migration_risk(
            total_stateful_services=total_stateful_services,
            has_publicly_exposed_database=has_publicly_exposed_database,
        ),
    }


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
          "volumes": ["./app:/usr/share/nginx/html"],
          "is_database": false,
          "is_publicly_exposed": true,
          "has_persistent_storage": true,
          "suggested_cloud_service": "Virtual Machine or Container Service",
          "risk_assessment": {
            "exposure_risk": "Medium",
            "data_loss_risk": "Medium",
            "migration_complexity": "Low"
          }
        },
        "db": {
          "image": "postgres:16",
          "ports": ["5432:5432"],
          "environment": {
            "POSTGRES_DB": "app",
            "POSTGRES_USER": "postgres"
          },
          "volumes": ["db_data:/var/lib/postgresql/data"],
          "is_database": true,
          "is_publicly_exposed": false,
          "has_persistent_storage": true,
          "suggested_cloud_service": "Managed Relational Database Service",
          "risk_assessment": {
            "exposure_risk": "Low",
            "data_loss_risk": "High",
            "migration_complexity": "Low"
          }
        }
      },
      "infrastructure_summary": {
        "total_services": 2,
        "total_database_services": 1,
        "total_publicly_exposed_services": 1,
        "total_stateful_services": 2,
        "overall_migration_risk": "Low"
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

    # Build normalized, nested service metadata required by Helix Infra.
    normalized_services: Dict[str, Dict[str, Any]] = {}
    service_count_total = len(services_data)

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

        # Derived intelligence fields.
        # - Database check is keyword-based on image name.
        # - Public exposure checks common internet-facing ports and host binding.
        # - Persistent storage is inferred from declared volumes.
        database_flag = is_database_image(image_value)
        publicly_exposed_flag = is_publicly_exposed_ports(ports)
        persistent_storage_flag = has_persistent_storage(volumes)

        normalized_services[service_name_str] = {
            "image": image_value,
            "ports": ports,
            "environment": environment,
            "volumes": volumes,
            "is_database": database_flag,
            "is_publicly_exposed": publicly_exposed_flag,
            "has_persistent_storage": persistent_storage_flag,
            "suggested_cloud_service": suggest_cloud_service(image_value, database_flag),
            "risk_assessment": build_risk_assessment(
                is_database=database_flag,
                is_publicly_exposed=publicly_exposed_flag,
                has_persistent_storage_flag=persistent_storage_flag,
                total_services=service_count_total,
            ),
        }

    # Return well-structured JSON payload.
    infrastructure_summary = build_infrastructure_summary(normalized_services)

    response_payload = {
        "service_count": len(normalized_services),
        "services": normalized_services,
        "infrastructure_summary": infrastructure_summary,
    }

    return JSONResponse(content=response_payload)
