"""POST /validate/json — validate a batch of JSON records against field rules."""

from fastapi import APIRouter

from ..schemas.validate import ValidateRequest, ValidateResponse
from ..services.json_validator import validate_records

router = APIRouter(prefix="/validate", tags=["validate"])


@router.post("/json", response_model=ValidateResponse)
def validate_json(payload: ValidateRequest) -> ValidateResponse:
    return validate_records(payload.records, payload.field_rules)
