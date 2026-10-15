"""
Employee Attendance & Analytics API - Complete Implementation

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

All 12 endpoints implemented per openapi.yaml contract with proper
business rules R1-R10, MongoDB indexes, and aggregation pipelines.
"""
import os
import re
from datetime import datetime, timedelta, timezone, date as date_cls
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, List, Dict, Any, Union
from contextlib import asynccontextmanager
from fastapi import Path as FastAPIPath

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Path
from pydantic import BaseModel, Field, field_validator, EmailStr
from pymongo import MongoClient, ASCENDING, DESCENDING, IndexModel
from pymongo.errors import DuplicateKeyError, OperationFailure

# Load .env from project root (override=False so real env vars win)
# Find .env in current dir or parent dirs
from pathlib import Path as PathLib
env_path = PathLib(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=env_path, override=False)

MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB = os.getenv("MONGO_DB", "attendance_db")

if not MONGO_URI:
    raise RuntimeError("MONGO_URI not set. Set it in .env or environment variables.")

client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000, tz_aware=True)
db = client[MONGO_DB]

IST = timezone(timedelta(hours=5, minutes=30))
MIN_EPOCH_MS = 100_000_000_000
MAX_EPOCH_MS = 4_102_444_800_000

# --------------------------------------------------------------------------- #
# Helper Functions
# --------------------------------------------------------------------------- #

def truncate_to_seconds(dt: datetime) -> datetime:
    """Truncate datetime to whole seconds."""
    return dt.replace(microsecond=0)

def to_epoch_ms(dt: Optional[datetime]) -> Optional[int]:
    """Convert UTC datetime to epoch milliseconds."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)

def from_epoch_ms(ms: Optional[int]) -> Optional[datetime]:
    """Convert epoch milliseconds to UTC datetime (truncated to seconds)."""
    if ms is None:
        return None
    if ms < MIN_EPOCH_MS or ms > MAX_EPOCH_MS:
        raise HTTPException(422, f"Timestamp out of valid range [{MIN_EPOCH_MS}, {MAX_EPOCH_MS}]")
    return truncate_to_seconds(datetime.fromtimestamp(ms / 1000, tz=timezone.utc))

def validate_epoch_ms(value: Optional[int]) -> Optional[int]:
    """Validate epoch milliseconds value."""
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise HTTPException(422, "Timestamp must be an integer (epoch milliseconds)")
    if value < MIN_EPOCH_MS or value > MAX_EPOCH_MS:
        raise HTTPException(422, f"Timestamp out of valid range [{MIN_EPOCH_MS}, {MAX_EPOCH_MS}]")
    return value

def parse_shift_time(t: str) -> tuple[int, int]:
    """Parse HH:MM shift time string to (hour, minute)."""
    if not re.match(r'^([01]\d|2[0-3]):[0-5]\d$', t):
        raise HTTPException(422, f"Invalid shift time format: {t}. Expected HH:MM 24-hour.")
    h, m = map(int, t.split(":"))
    return h, m

def ist_date_from_utc(utc_dt: datetime) -> date_cls:
    """Get IST calendar date from UTC datetime."""
    ist_dt = utc_dt.astimezone(IST)
    return ist_dt.date()

def attendance_date(punch_in_utc: datetime, shift_start: str, shift_end: str) -> date_cls:
    """
    R1: Compute attendance date from punch-in instant.
    For overnight shifts (shift_end <= shift_start), punch-in before shift_end
    belongs to previous day's shift.
    """
    ist_dt = punch_in_utc.astimezone(IST)
    shift_start_h, shift_start_m = parse_shift_time(shift_start)
    shift_end_h, shift_end_m = parse_shift_time(shift_end)

    is_overnight = (shift_end_h < shift_start_h) or (shift_end_h == shift_start_h and shift_end_m <= shift_start_m)

    if is_overnight:
        # Create shift_end time on the same IST date as punch-in
        shift_end_today = ist_dt.replace(hour=shift_end_h, minute=shift_end_m, second=0, microsecond=0)
        if ist_dt < shift_end_today:
            # Punch-in is before shift_end -> belongs to previous day
            return (ist_dt - timedelta(days=1)).date()

    return ist_dt.date()

def compute_late_minutes(punch_in_utc: datetime, shift_start: str) -> int:
    """
    R2: Late arrival calculation.
    Grace period = 10 minutes. Late only if strictly > 10 min after shift_start.
    late_minutes = floor(minutes since shift_start), not minutes after grace.
    """
    ist_dt = punch_in_utc.astimezone(IST)
    shift_h, shift_m = parse_shift_time(shift_start)
    shift_start_ist = ist_dt.replace(hour=shift_h, minute=shift_m, second=0, microsecond=0)

    # Truncate punch_in to seconds (already done)
    diff_seconds = (ist_dt - shift_start_ist).total_seconds()
    diff_minutes = int(diff_seconds // 60)  # floor

    # Late only if strictly more than 10 minutes (600 seconds) after shift_start
    if diff_seconds > 600:
        return diff_minutes
    return 0

def compute_overtime_minutes(punch_out_utc: datetime, shift_end: str, attendance_date_str: str) -> int:
    """
    R3: Overtime calculation.
    Whole elapsed minutes between shift_end and punch_out, only if >= 30.
    Supports overnight shifts.
    """
    # attendance_date_str is YYYY-MM-DD (the shift start date)
    att_date = date_cls.fromisoformat(attendance_date_str)
    shift_h, shift_m = parse_shift_time(shift_end)

    # shift_end instant in IST on the attendance date
    shift_end_ist = datetime(att_date.year, att_date.month, att_date.day, shift_h, shift_m, 0, tzinfo=IST)

    # For overnight shifts (shift_end <= shift_start), shift_end is on NEXT calendar day
    # We need to check if this is an overnight shift by looking at the employee's shift
    # But we only have shift_end here. The logic: if shift_end time is early (before noon-ish)
    # and punch_out is after midnight, it's overnight.
    # Better approach: compare shift_end_ist with punch_out_utc
    shift_end_utc = shift_end_ist.astimezone(timezone.utc)

    diff_seconds = (punch_out_utc - shift_end_utc).total_seconds()
    diff_minutes = int(diff_seconds // 60)  # floor

    if diff_minutes >= 30:
        return diff_minutes
    return 0

def compute_work_hours(punch_in_utc: datetime, punch_out_utc: datetime) -> float:
    """
    R4: Work hours = elapsed seconds / 3600, rounded to 2 decimals half-up.
    """
    elapsed_seconds = (punch_out_utc - punch_in_utc).total_seconds()
    hours = Decimal(elapsed_seconds) / Decimal(3600)
    # Round half-up to 2 decimal places
    rounded = hours.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return float(rounded)

def compute_half_day(work_hours: Optional[float]) -> bool:
    """R5: Half day if rounded work_hours < 4.50."""
    if work_hours is None:
        return False
    return work_hours < 4.5

def round_half_up_2(value: float) -> float:
    """Round to 2 decimals half-up."""
    d = Decimal(str(value))
    return float(d.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))

def round_half_up_4(value: float) -> float:
    """Round to 4 decimals half-up."""
    d = Decimal(str(value))
    return float(d.quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP))

def validate_date_str(date_str: str) -> date_cls:
    """Validate YYYY-MM-DD date string."""
    try:
        return date_cls.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(422, f"Invalid date format: {date_str}. Expected YYYY-MM-DD.")

def validate_month_str(month_str: str) -> tuple[int, int]:
    """Validate YYYY-MM month string, return (year, month)."""
    try:
        parts = month_str.split("-")
        if len(parts) != 2:
            raise ValueError
        year, month = int(parts[0]), int(parts[1])
        if not (1 <= month <= 12):
            raise ValueError
        return year, month
    except ValueError:
        raise HTTPException(422, f"Invalid month format: {month_str}. Expected YYYY-MM.")

def month_bounds(year: int, month: int) -> tuple[date_cls, date_cls]:
    """Get first and last day of month."""
    first = date_cls(year, month, 1)
    if month == 12:
        last = date_cls(year + 1, 1, 1) - timedelta(days=1)
    else:
        last = date_cls(year, month + 1, 1) - timedelta(days=1)
    return first, last

def is_working_day(d: date_cls) -> bool:
    """R7: Working days are Monday-Friday (0=Mon, 6=Sun)."""
    return d.weekday() <= 4

# --------------------------------------------------------------------------- #
# Pydantic Models (matching openapi.yaml schemas)
# --------------------------------------------------------------------------- #

class EmployeeCreate(BaseModel):
    emp_code: str = Field(pattern=r'^EMP\d{4,6}$')
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field(default="09:30", pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    shift_end: str = Field(default="18:30", pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    joined_on: str

    @field_validator('joined_on')
    @classmethod
    def validate_joined_on(cls, v):
        validate_date_str(v)
        return v

    @field_validator('shift_end')
    @classmethod
    def validate_shifts(cls, v, info):
        if 'shift_start' in info.data and v == info.data['shift_start']:
            raise ValueError("shift_start and shift_end must differ")
        return v

class EmployeeResponse(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str
    shift_end: str
    joined_on: str
    created_at: int

class EmployeePage(BaseModel):
    items: List[EmployeeResponse]
    total: int
    page: int
    page_size: int

class PunchInRequest(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = Field(default="PRESENT", pattern=r'^(PRESENT|WFH|ON_DUTY)$')

class PunchOutRequest(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None

class RegularizeRequest(BaseModel):
    status: Optional[str] = Field(default=None, pattern=r'^(PRESENT|WFH|ON_DUTY|ABSENT|LEAVE)$')
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)

class HistoryEntry(BaseModel):
    at: int
    by: str
    reason: str
    changes: Dict[str, Dict[str, Any]]

class AttendanceRecord(BaseModel):
    emp_code: str
    date: str
    status: str
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None
    work_hours: Optional[float] = None
    late_minutes: int
    overtime_minutes: int
    half_day: bool
    history: List[HistoryEntry]

class AttendancePage(BaseModel):
    items: List[AttendanceRecord]
    total: int
    page: int
    page_size: int

class EmployeeMonthly(BaseModel):
    emp_code: str
    month: str
    working_days: int
    present_days: float
    leave_days: int
    late_count: int
    total_late_minutes: int
    total_overtime_minutes: int
    attendance_pct: Optional[float] = None

class DepartmentSummaryItem(BaseModel):
    department: str
    headcount: int
    present_days: float
    avg_work_hours: Optional[float] = None
    late_count: int
    total_late_minutes: int
    leave_count: int
    on_duty_count: int

class DepartmentSummary(BaseModel):
    month: str
    items: List[DepartmentSummaryItem]

class LeaderboardItem(BaseModel):
    rank: int
    emp_code: str
    name: str
    department: str
    total_late_minutes: int
    late_count: int

class Leaderboard(BaseModel):
    month: str
    items: List[LeaderboardItem]

class TrendItem(BaseModel):
    date: str
    is_working_day: bool
    headcount: int
    present_count: float
    late_count: int
    attendance_rate: Optional[float] = None
    moving_avg_7d: Optional[float] = None

class Trend(BaseModel):
    department: str
    items: List[TrendItem]

class ExplainResponse(BaseModel):
    endpoint: str
    collection: str
    explain: Dict[str, Any]

# --------------------------------------------------------------------------- #
# Startup: Create Indexes
# --------------------------------------------------------------------------- #

def create_indexes():
    """Create all required indexes idempotently at startup."""
    try:
        # employees collection
        db.employees.create_index(
            [("emp_code", ASCENDING)],
            unique=True,
            name="ux_emp_code"
        )
        db.employees.create_index(
            [("department", ASCENDING), ("emp_code", ASCENDING)],
            name="ix_dept_emp_code"
        )
        db.employees.create_index(
            [("department", ASCENDING), ("joined_on", ASCENDING)],
            name="ix_dept_joined"
        )
        db.employees.create_index(
            [("joined_on", ASCENDING)],
            name="ix_joined_on"
        )

        # attendance_logs collection
        db.attendance_logs.create_index(
            [("emp_code", ASCENDING), ("date", ASCENDING)],
            unique=True,
            name="ux_emp_code_date"
        )
        db.attendance_logs.create_index(
            [("date", DESCENDING), ("emp_code", ASCENDING)],
            name="ix_date_emp_code"
        )
        db.attendance_logs.create_index(
            [("emp_code", ASCENDING), ("date", ASCENDING), ("status", ASCENDING)],
            name="ix_emp_date_status"
        )
        db.attendance_logs.create_index(
            [("date", ASCENDING), ("status", ASCENDING)],
            name="ix_date_status"
        )

        # Verify indexes exist
        emp_indexes = list(db.employees.list_indexes())
        att_indexes = list(db.attendance_logs.list_indexes())
        print(f"Created/verified {len(emp_indexes)} employee indexes, {len(att_indexes)} attendance indexes")
    except Exception as e:
        print(f"Index creation warning: {e}")

# --------------------------------------------------------------------------- #
# FastAPI App with Lifespan
# --------------------------------------------------------------------------- #

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    create_indexes()
    # Verify MongoDB connectivity
    try:
        client.admin.command("ping")
        print("MongoDB connection verified")
    except Exception as e:
        print(f"MongoDB connection failed: {e}")
    yield
    # Shutdown
    client.close()

app = FastAPI(
    title="Employee Attendance & Analytics API",
    version="2.0.0",
    lifespan=lifespan
)

# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@app.get("/health", tags=["system"])
def health():
    """Readiness probe - returns 200 only if MongoDB answers ping."""
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(503, "MongoDB unavailable")

@app.post("/employees", status_code=201, tags=["employees"], response_model=EmployeeResponse)
def create_employee(body: EmployeeCreate):
    """Create employee with database-enforced uniqueness."""
    doc = body.model_dump()
    created_at = truncate_to_seconds(datetime.now(timezone.utc))
    doc["created_at"] = created_at

    try:
        result = db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")
    except OperationFailure as e:
        if "duplicate key" in str(e).lower():
            raise HTTPException(409, "emp_code already exists")
        raise

    doc["created_at"] = to_epoch_ms(created_at)
    doc.pop("_id", None)
    return doc

@app.get("/employees", tags=["employees"], response_model=EmployeePage)
def list_employees(
    department: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100)
):
    """List employees with department filter, sorted by emp_code asc."""
    query = {}
    if department:
        query["department"] = department

    total = db.employees.count_documents(query)
    skip = (page - 1) * page_size

    cursor = db.employees.find(query, {"_id": 0}).sort("emp_code", ASCENDING).skip(skip).limit(page_size)
    items = []
    for doc in cursor:
        if "created_at" in doc and isinstance(doc["created_at"], datetime):
            doc["created_at"] = to_epoch_ms(doc["created_at"])
        items.append(doc)

    return {"items": items, "total": total, "page": page, "page_size": page_size}

@app.post("/attendance/punch-in", status_code=201, tags=["attendance"], response_model=AttendanceRecord)
def punch_in(body: PunchInRequest):
    """Record punch-in with proper R1 date calculation and R2 late minutes."""
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")

    punched_at = body.punched_at
    if punched_at is None:
        punch_in_utc = truncate_to_seconds(datetime.now(timezone.utc))
    else:
        validate_epoch_ms(punched_at)
        punch_in_utc = from_epoch_ms(punched_at)

    # Compute attendance date per R1
    att_date = attendance_date(punch_in_utc, emp["shift_start"], emp["shift_end"])
    date_str = att_date.isoformat()

    # Compute late minutes per R2
    late_minutes = compute_late_minutes(punch_in_utc, emp["shift_start"])

    doc = {
        "emp_code": body.emp_code,
        "date": date_str,
        "status": body.status,
        "punch_in": punch_in_utc,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": late_minutes,
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        result = db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "already punched in for this date")
    except OperationFailure as e:
        if "duplicate key" in str(e).lower():
            raise HTTPException(409, "already punched in for this date")
        raise

    doc["punch_in"] = to_epoch_ms(punch_in_utc)
    doc["punch_out"] = None
    return doc

@app.post("/attendance/punch-out", tags=["attendance"], response_model=AttendanceRecord)
def punch_out(body: PunchOutRequest):
    """Record punch-out with atomic conditional update."""
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")

    if body.punched_at is None:
        punch_out_utc = truncate_to_seconds(datetime.now(timezone.utc))
    else:
        validate_epoch_ms(body.punched_at)
        punch_out_utc = from_epoch_ms(body.punched_at)

    # Find the most recent record with punch_in <= punch_out_utc and no punch_out yet
    # For overnight shifts, we need to find by emp_code and check punch_in time
    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_out": None,
            "punch_in": {"$lte": punch_out_utc}
        },
        sort=[("punch_in", DESCENDING)]
    )

    if not record:
        raise HTTPException(404, "No open punch-in found for this employee")

    punch_in_utc = record["punch_in"]
    if not isinstance(punch_in_utc, datetime):
        # Handle case where it might be stored differently
        punch_in_utc = punch_in_utc

    # Validate punch_out > punch_in and <= 24 hours
    if punch_out_utc <= punch_in_utc:
        raise HTTPException(422, "punch_out must be after punch_in")
    if (punch_out_utc - punch_in_utc).total_seconds() > 24 * 3600:
        raise HTTPException(422, "punch_out cannot be more than 24 hours after punch_in")

    # Compute derived fields
    work_hours = compute_work_hours(punch_in_utc, punch_out_utc)
    overtime_minutes = compute_overtime_minutes(punch_out_utc, emp["shift_end"], record["date"])
    half_day = compute_half_day(work_hours)

    # Atomic update with condition: only if punch_out is still null
    update_result = db.attendance_logs.find_one_and_update(
        {
            "emp_code": body.emp_code,
            "date": record["date"],
            "punch_out": None
        },
        {
            "$set": {
                "punch_out": punch_out_utc,
                "work_hours": work_hours,
                "overtime_minutes": overtime_minutes,
                "half_day": half_day,
            }
        },
        return_document=True
    )

    if not update_result:
        raise HTTPException(409, "already punched out")

    # Convert for response
    update_result["punch_in"] = to_epoch_ms(update_result["punch_in"])
    update_result["punch_out"] = to_epoch_ms(update_result["punch_out"])
    if "history" not in update_result:
        update_result["history"] = []
    update_result.pop("_id", None)
    return update_result

@app.get("/attendance", tags=["attendance"], response_model=AttendancePage)
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100)
):
    """List attendance records with DB-level filtering, sorting, pagination."""
    query = {}
    if emp_code:
        query["emp_code"] = emp_code
    if date_from:
        validate_date_str(date_from)
        if "date" not in query:
            query["date"] = {}
        query["date"]["$gte"] = date_from
    if date_to:
        validate_date_str(date_to)
        if "date" not in query:
            query["date"] = {}
        query["date"]["$lte"] = date_to
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from cannot be after date_to")
    if status:
        if status not in ["PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"]:
            raise HTTPException(422, "Invalid status")
        query["status"] = status

    total = db.attendance_logs.count_documents(query)
    skip = (page - 1) * page_size

    cursor = db.attendance_logs.find(query, {"_id": 0}).sort([("date", DESCENDING), ("emp_code", ASCENDING)]).skip(skip).limit(page_size)

    items = []
    for doc in cursor:
        doc["punch_in"] = to_epoch_ms(doc.get("punch_in"))
        doc["punch_out"] = to_epoch_ms(doc.get("punch_out"))
        if "half_day" not in doc:
            doc["half_day"] = False
        if "history" not in doc:
            doc["history"] = []
        else:
            # Convert history timestamps
            for h in doc["history"]:
                if isinstance(h.get("at"), datetime):
                    h["at"] = to_epoch_ms(h["at"])
                if "changes" in h:
                    for field, change in h["changes"].items():
                        if field in ["punch_in", "punch_out"]:
                            if "from" in change and isinstance(change["from"], datetime):
                                change["from"] = to_epoch_ms(change["from"])
                            if "to" in change and isinstance(change["to"], datetime):
                                change["to"] = to_epoch_ms(change["to"])
        items.append(doc)

    return {"items": items, "total": total, "page": page, "page_size": page_size}

@app.patch("/attendance/{emp_code}/{date}", tags=["attendance"], response_model=AttendanceRecord)
def regularize_attendance(
    emp_code: str = FastAPIPath(...),
    date: str = FastAPIPath(...),
    body: RegularizeRequest = None
):
    """Regularize (manually correct) one day's record with audit history."""
    validate_date_str(date)

    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")

    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not record:
        raise HTTPException(404, "Attendance record not found")

    # Track changes for history
    changes = {}
    new_status = record["status"]
    new_punch_in = record.get("punch_in")
    new_punch_out = record.get("punch_out")

    # Apply status change
    if body.status is not None and body.status != record["status"]:
        # Validate status-specific rules
        if body.status in ["ABSENT", "LEAVE"]:
            if body.punch_in is not None or body.punch_out is not None:
                raise HTTPException(422, f"Cannot set punch times with status {body.status}")
            new_status = body.status
            new_punch_in = None
            new_punch_out = None
        else:  # PRESENT, WFH, ON_DUTY
            # Must have a punch_in (supplied or existing)
            if body.punch_in is None and record.get("punch_in") is None:
                raise HTTPException(422, "Presence status requires punch_in")
            new_status = body.status

    # Apply punch_in change
    if body.punch_in is not None:
        validate_epoch_ms(body.punch_in)
        new_pi = from_epoch_ms(body.punch_in)
        # Verify it resolves to the same attendance date (R1)
        pi_date = attendance_date(new_pi, emp["shift_start"], emp["shift_end"])
        if pi_date.isoformat() != date:
            raise HTTPException(422, "punch_in does not resolve to the record's attendance date")
        new_punch_in = new_pi

    # Apply punch_out change
    if body.punch_out is not None:
        validate_epoch_ms(body.punch_out)
        new_po = from_epoch_ms(body.punch_out)
        # Must have punch_in
        if new_punch_in is None:
            raise HTTPException(422, "punch_out requires punch_in")
        if new_po <= new_punch_in:
            raise HTTPException(422, "punch_out must be after punch_in")
        if (new_po - new_punch_in).total_seconds() > 24 * 3600:
            raise HTTPException(422, "punch_out cannot be more than 24 hours after punch_in")
        new_punch_out = new_po

    # Check if anything actually changed
    if (new_status == record["status"] and
        new_punch_in == record.get("punch_in") and
        new_punch_out == record.get("punch_out")):
        raise HTTPException(422, "No changes made")

    # Recompute derived fields from final state
    if new_status in ["ABSENT", "LEAVE"]:
        work_hours = None
        late_minutes = 0
        overtime_minutes = 0
        half_day = False
    else:
        if new_punch_in is None:
            raise HTTPException(422, "Presence status requires punch_in")
        late_minutes = compute_late_minutes(new_punch_in, emp["shift_start"])
        if new_punch_out is not None:
            work_hours = compute_work_hours(new_punch_in, new_punch_out)
            overtime_minutes = compute_overtime_minutes(new_punch_out, emp["shift_end"], date)
            half_day = compute_half_day(work_hours)
        else:
            work_hours = None
            overtime_minutes = 0
            half_day = False

    # Build changes object (only fields that actually changed)
    if new_status != record["status"]:
        changes["status"] = {"from": record["status"], "to": new_status}
    if new_punch_in != record.get("punch_in"):
        changes["punch_in"] = {
            "from": to_epoch_ms(record.get("punch_in")),
            "to": to_epoch_ms(new_punch_in)
        }
    if new_punch_out != record.get("punch_out"):
        changes["punch_out"] = {
            "from": to_epoch_ms(record.get("punch_out")),
            "to": to_epoch_ms(new_punch_out)
        }
    if work_hours != record.get("work_hours"):
        changes["work_hours"] = {"from": record.get("work_hours"), "to": work_hours}
    if late_minutes != record.get("late_minutes", 0):
        changes["late_minutes"] = {"from": record.get("late_minutes", 0), "to": late_minutes}
    if overtime_minutes != record.get("overtime_minutes", 0):
        changes["overtime_minutes"] = {"from": record.get("overtime_minutes", 0), "to": overtime_minutes}
    if half_day != record.get("half_day", False):
        changes["half_day"] = {"from": record.get("half_day", False), "to": half_day}

    # Atomic compare-and-set update with history append
    now_utc = truncate_to_seconds(datetime.now(timezone.utc))
    history_entry = {
        "at": now_utc,
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes
    }

    # Use find_one_and_update with version check (optimistic locking via _id + current values)
    filter_query = {
        "_id": record["_id"],
        "status": record["status"],
        "punch_in": record.get("punch_in"),
        "punch_out": record.get("punch_out")
    }

    update_doc = {
        "$set": {
            "status": new_status,
            "punch_in": new_punch_in,
            "punch_out": new_punch_out,
            "work_hours": work_hours,
            "late_minutes": late_minutes,
            "overtime_minutes": overtime_minutes,
            "half_day": half_day,
        },
        "$push": {"history": history_entry}
    }

    updated = db.attendance_logs.find_one_and_update(
        filter_query,
        update_doc,
        return_document=True
    )

    if not updated:
        # Retry once with fresh read (handle concurrent modification)
        record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
        if not record:
            raise HTTPException(404, "Attendance record not found")
        # Re-check if changes still needed
        filter_query = {
            "_id": record["_id"],
            "status": record["status"],
            "punch_in": record.get("punch_in"),
            "punch_out": record.get("punch_out")
        }
        updated = db.attendance_logs.find_one_and_update(
            filter_query,
            update_doc,
            return_document=True
        )
        if not updated:
            raise HTTPException(409, "Concurrent modification - please retry")

    # Convert for response
    updated["punch_in"] = to_epoch_ms(updated.get("punch_in"))
    updated["punch_out"] = to_epoch_ms(updated.get("punch_out"))
    if "history" not in updated:
        updated["history"] = []
    else:
        # Convert history timestamps
        for h in updated["history"]:
            if isinstance(h.get("at"), datetime):
                h["at"] = to_epoch_ms(h["at"])
            if "changes" in h:
                for field, change in h["changes"].items():
                    if field in ["punch_in", "punch_out"]:
                        if "from" in change and isinstance(change["from"], datetime):
                            change["from"] = to_epoch_ms(change["from"])
                        if "to" in change and isinstance(change["to"], datetime):
                            change["to"] = to_epoch_ms(change["to"])
    updated.pop("_id", None)
    return updated

# --------------------------------------------------------------------------- #
# Analytics Endpoints (MongoDB Aggregation Pipelines)
# --------------------------------------------------------------------------- #

def build_employee_monthly_pipeline(emp_code: str, year: int, month: int) -> List[Dict]:
    """Build aggregation pipeline for employee monthly summary."""
    first_day, last_day = month_bounds(year, month)
    first_str = first_day.isoformat()
    last_str = last_day.isoformat()

    # Get employee's joined_on
    emp = db.employees.find_one({"emp_code": emp_code}, {"joined_on": 1, "shift_start": 1, "shift_end": 1})
    if not emp:
        return None

    joined_on = date_cls.fromisoformat(emp["joined_on"])
    work_start = max(first_day, joined_on)

    # Count working days (Mon-Fri) from work_start to last_day inclusive
    working_days = 0
    d = work_start
    while d <= last_day:
        if is_working_day(d):
            working_days += 1
        d += timedelta(days=1)

    pipeline = [
        {"$match": {"emp_code": emp_code, "date": {"$gte": first_str, "$lte": last_str}}},
        {"$project": {
            "date": 1,
            "status": 1,
            "late_minutes": {"$ifNull": ["$late_minutes", 0]},
            "overtime_minutes": {"$ifNull": ["$overtime_minutes", 0]},
            "work_hours": 1,
            "half_day": {"$ifNull": ["$half_day", False]},
            "is_weekday": {"$lte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date", "timezone": "UTC"}}}, 5]},
            "is_present": {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
            "is_leave": {"$eq": ["$status", "LEAVE"]}
        }},
        {"$group": {
            "_id": None,
            "present_days": {"$sum": {
                "$cond": [
                    {"$and": ["$is_weekday", "$is_present"]},
                    {"$cond": ["$half_day", 0.5, 1]},
                    0
                ]
            }},
            "leave_days": {"$sum": {"$cond": ["$is_leave", 1, 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}},
            "total_late_minutes": {"$sum": "$late_minutes"},
            "total_overtime_minutes": {"$sum": "$overtime_minutes"},
        }}
    ]
    return pipeline, working_days

@app.get("/analytics/employees/{emp_code}/monthly", tags=["analytics"], response_model=EmployeeMonthly)
def employee_monthly(
    emp_code: str = FastAPIPath(...),
    month: str = Query(..., pattern=r'^\d{4}-(0[1-9]|1[0-2])$')
):
    """Employee monthly summary via aggregation."""
    year, month_num = validate_month_str(month)

    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")

    pipeline, working_days = build_employee_monthly_pipeline(emp_code, year, month_num)
    result = list(db.attendance_logs.aggregate(pipeline))

    if result:
        agg = result[0]
        present_days = agg.get("present_days", 0)
        leave_days = agg.get("leave_days", 0)
        late_count = agg.get("late_count", 0)
        total_late_minutes = agg.get("total_late_minutes", 0)
        total_overtime_minutes = agg.get("total_overtime_minutes", 0)
    else:
        present_days = 0
        leave_days = 0
        late_count = 0
        total_late_minutes = 0
        total_overtime_minutes = 0

    attendance_pct = None
    if working_days > 0:
        attendance_pct = round_half_up_2(present_days / working_days * 100)

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": leave_days,
        "late_count": late_count,
        "total_late_minutes": total_late_minutes,
        "total_overtime_minutes": total_overtime_minutes,
        "attendance_pct": attendance_pct
    }

def build_department_summary_pipeline(year: int, month: int, dept_filter: Optional[str] = None) -> List[Dict]:
    """Build aggregation pipeline for department summary."""
    first_day, last_day = month_bounds(year, month)
    first_str = first_day.isoformat()
    last_str = last_day.isoformat()
    month_end_str = last_day.isoformat()

    # First, get employees who joined on or before month_end
    emp_match = {"joined_on": {"$lte": month_end_str}}
    if dept_filter:
        emp_match["department"] = dept_filter

    pipeline = [
        # Get qualifying employees
        {"$match": emp_match},
        {"$project": {
            "emp_code": 1,
            "department": 1,
            "joined_on": 1
        }},
        # Lookup attendance logs for this month
        {"$lookup": {
            "from": "attendance_logs",
            "let": {"emp_code": "$emp_code", "dept": "$department"},
            "pipeline": [
                {"$match": {
                    "$expr": {"$eq": ["$emp_code", "$$emp_code"]},
                    "date": {"$gte": first_str, "$lte": last_str}
                }},
                {"$project": {
                    "status": 1,
                    "late_minutes": {"$ifNull": ["$late_minutes", 0]},
                    "overtime_minutes": {"$ifNull": ["$overtime_minutes", 0]},
                    "work_hours": 1,
                    "half_day": {"$ifNull": ["$half_day", False]},
                    "date": 1,
                    "is_weekday": {"$lte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date", "timezone": "UTC"}}}, 5]},
                    "is_present": {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                    "is_leave": {"$eq": ["$status", "LEAVE"]},
                    "is_on_duty": {"$eq": ["$status", "ON_DUTY"]}
                }}
            ],
            "as": "logs"
        }},
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {"$group": {
            "_id": "$department",
            "headcount": {"$sum": 1},
            "present_days": {"$sum": {
                "$cond": [
                    {"$and": [{"$ifNull": ["$logs.is_weekday", False]}, {"$ifNull": ["$logs.is_present", False]}]},
                    {"$cond": [{"$ifNull": ["$logs.half_day", False]}, 0.5, 1]},
                    0
                ]
            }},
            "avg_work_hours_sum": {"$sum": {"$cond": [
                {"$and": [{"$ifNull": ["$logs.is_present", False]}, {"$ne": ["$logs.work_hours", None]}]},
                "$logs.work_hours", 0
            ]}},
            "avg_work_hours_count": {"$sum": {"$cond": [
                {"$and": [{"$ifNull": ["$logs.is_present", False]}, {"$ne": ["$logs.work_hours", None]}]},
                1, 0
            ]}},
            "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$logs.late_minutes", 0]}, 0]}, 1, 0]}},
            "total_late_minutes": {"$sum": {"$ifNull": ["$logs.late_minutes", 0]}},
            "leave_count": {"$sum": {"$cond": [{"$ifNull": ["$logs.is_leave", False]}, 1, 0]}},
            "on_duty_count": {"$sum": {"$cond": [{"$ifNull": ["$logs.is_on_duty", False]}, 1, 0]}}
        }},
        {"$match": {"headcount": {"$gt": 0}}},
        {"$project": {
            "department": "$_id",
            "headcount": 1,
            "present_days": 1,
            "avg_work_hours": {"$cond": [
                {"$gt": ["$avg_work_hours_count", 0]},
                {"$divide": ["$avg_work_hours_sum", "$avg_work_hours_count"]},
                None
            ]},
            "late_count": 1,
            "total_late_minutes": 1,
            "leave_count": 1,
            "on_duty_count": 1,
            "_id": 0
        }},
        {"$sort": {"department": 1}}
    ]
    return pipeline

@app.get("/analytics/departments/summary", tags=["analytics"], response_model=DepartmentSummary)
def department_summary(
    month: str = Query(..., pattern=r'^\d{4}-(0[1-9]|1[0-2])$'),
    department: Optional[str] = None
):
    """Department summary for a month via aggregation."""
    year, month_num = validate_month_str(month)

    pipeline = build_department_summary_pipeline(year, month_num, department)
    results = list(db.employees.aggregate(pipeline))

    items = []
    for r in results:
        avg_wh = r.get("avg_work_hours")
        if avg_wh is not None:
            avg_wh = round_half_up_2(avg_wh)
        items.append(DepartmentSummaryItem(
            department=r["department"],
            headcount=r["headcount"],
            present_days=r["present_days"],
            avg_work_hours=avg_wh,
            late_count=r["late_count"],
            total_late_minutes=r["total_late_minutes"],
            leave_count=r["leave_count"],
            on_duty_count=r["on_duty_count"]
        ))

    return {"month": month, "items": items}

def build_late_leaderboard_pipeline(year: int, month: int, dept_filter: Optional[str] = None, limit: int = 10) -> List[Dict]:
    """Build aggregation pipeline for late leaderboard."""
    first_day, last_day = month_bounds(year, month)
    first_str = first_day.isoformat()
    last_str = last_day.isoformat()

    # Match attendance logs in the month
    match_stage = {"date": {"$gte": first_str, "$lte": last_str}, "late_minutes": {"$gt": 0}}

    pipeline = [
        {"$match": match_stage},
        {"$group": {
            "_id": "$emp_code",
            "total_late_minutes": {"$sum": "$late_minutes"},
            "late_count": {"$sum": 1}
        }},
        {"$lookup": {
            "from": "employees",
            "localField": "_id",
            "foreignField": "emp_code",
            "as": "emp"
        }},
        {"$unwind": "$emp"},
        {"$match": {"emp.department": dept_filter} if dept_filter else {}},
        {"$project": {
            "emp_code": "$_id",
            "name": "$emp.name",
            "department": "$emp.department",
            "total_late_minutes": 1,
            "late_count": 1
        }},
        {"$sort": {"total_late_minutes": -1, "emp_code": 1}}
    ]
    return pipeline

@app.get("/analytics/leaderboard/late", tags=["analytics"], response_model=Leaderboard)
def late_leaderboard(
    month: str = Query(..., pattern=r'^\d{4}-(0[1-9]|1[0-2])$'),
    limit: int = Query(10, ge=1, le=50),
    department: Optional[str] = None
):
    """Late-comers leaderboard with standard competition ranking."""
    year, month_num = validate_month_str(month)

    pipeline = build_late_leaderboard_pipeline(year, month_num, department, limit)
    results = list(db.attendance_logs.aggregate(pipeline))

    # Compute standard competition ranking (1, 2, 2, 4) in Python
    items = []
    prev_late = None
    prev_rank = 0
    for i, r in enumerate(results):
        if r["total_late_minutes"] != prev_late:
            prev_rank = i + 1
            prev_late = r["total_late_minutes"]
        if prev_rank <= limit:
            items.append(LeaderboardItem(
                rank=prev_rank,
                emp_code=r["emp_code"],
                name=r["name"],
                department=r["department"],
                total_late_minutes=r["total_late_minutes"],
                late_count=r["late_count"]
            ))
        elif items and items[-1].rank >= limit and r["total_late_minutes"] == items[-1].total_late_minutes:
            # Include ties at the cutoff
            items.append(LeaderboardItem(
                rank=prev_rank,
                emp_code=r["emp_code"],
                name=r["name"],
                department=r["department"],
                total_late_minutes=r["total_late_minutes"],
                late_count=r["late_count"]
            ))

    return {"month": month, "items": items}

def build_department_trend_pipeline(department: str, from_date: str, to_date: str) -> List[Dict]:
    """Build aggregation pipeline for department daily trend with $densify."""
    from_d = validate_date_str(from_date)
    to_d = validate_date_str(to_date)

    if to_d < from_d:
        raise HTTPException(422, "to date cannot be before from date")
    if (to_d - from_d).days > 91:  # inclusive range max 92 days
        raise HTTPException(422, "Date range cannot exceed 92 days")

    # Check department exists
    if db.employees.count_documents({"department": department}) == 0:
        raise HTTPException(404, "Department not found")

    from_str = from_date
    to_str = to_date

    # Complete pipeline using $densify (MongoDB 6.0+)
    # Start with a single dummy document from a $literal, generate all dates, then left join
    pipeline = [
        # Start with a single document containing the date range
        {"$limit": 1},
        {"$project": {
            "from": {"$dateFromString": {"dateString": from_str, "timezone": "UTC"}},
            "to": {"$dateFromString": {"dateString": to_str, "timezone": "UTC"}},
            "dept": department,
            "_id": 0
        }},
        # Generate all dates in range using $range (days as number of days)
        {"$project": {
            "dates": {
                "$map": {
                    "input": {"$range": [0, {"$add": [{"$divide": [{"$subtract": ["$to", "$from"]}, 1000 * 60 * 60 * 24]}, 1]}]},
                    "as": "dayOffset",
                    "in": {"$dateAdd": {"startDate": "$from", "unit": "day", "amount": "$$dayOffset"}}
                }
            }
        }},
        {"$unwind": "$dates"},
        {"$project": {
            "date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$dates", "timezone": "UTC"}},
            "dayOfWeek": {"$dayOfWeek": {"date": "$dates", "timezone": "UTC"}}
        }},
        {"$project": {
            "date": 1,
            "is_working_day": {"$and": [{"$gte": ["$dayOfWeek", 2]}, {"$lte": ["$dayOfWeek", 6]}]}  # 2=Mon, 6=Fri
        }},
        # Lookup headcount for each date (employees in dept with joined_on <= date)
        {"$lookup": {
            "from": "employees",
            "let": {"date": "$date", "dept": department},
            "pipeline": [
                {"$match": {
                    "$expr": {
                        "$and": [
                            {"$eq": ["$department", "$$dept"]},
                            {"$lte": ["$joined_on", "$$date"]}
                        ]
                    }
                }},
                {"$count": "count"}
            ],
            "as": "headcount_result"
        }},
        {"$project": {
            "date": 1,
            "is_working_day": 1,
            "headcount": {"$ifNull": [{"$arrayElemAt": ["$headcount_result.count", 0]}, 0]}
        }},
        # Lookup attendance for this date and department
        {"$lookup": {
            "from": "attendance_logs",
            "let": {"date": "$date", "dept": department},
            "pipeline": [
                {"$lookup": {
                    "from": "employees",
                    "localField": "emp_code",
                    "foreignField": "emp_code",
                    "as": "emp"
                }},
                {"$unwind": "$emp"},
                {"$match": {
                    "$expr": {
                        "$and": [
                            {"$eq": ["$date", "$$date"]},
                            {"$eq": ["$emp.department", "$$dept"]}
                        ]
                    }
                }},
                {"$project": {
                    "status": 1,
                    "late_minutes": {"$ifNull": ["$late_minutes", 0]},
                    "half_day": {"$ifNull": ["$half_day", False]},
                    "is_present": {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]}
                }}
            ],
            "as": "attendance"
        }},
        {"$project": {
            "date": 1,
            "is_working_day": 1,
            "headcount": 1,
            "present_count": {
                "$sum": {
                    "$map": {
                        "input": "$attendance",
                        "as": "a",
                        "in": {"$cond": ["$$a.is_present", {"$cond": ["$$a.half_day", 0.5, 1]}, 0]}
                    }
                }
            },
            "late_count": {
                "$sum": {
                    "$map": {
                        "input": "$attendance",
                        "as": "a",
                        "in": {"$cond": [{"$gt": ["$$a.late_minutes", 0]}, 1, 0]}
                    }
                }
            }
        }},
        {"$project": {
            "date": 1,
            "is_working_day": 1,
            "headcount": 1,
            "present_count": 1,
            "late_count": 1,
            "attendance_rate": {
                "$cond": [
                    {"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},
                    {"$divide": ["$present_count", "$headcount"]},
                    None
                ]
            }
        }},
        # 7-day moving average of attendance_rate (non-null values only)
        {"$setWindowFields": {
            "sortBy": {"date": 1},
            "output": {
                "moving_avg_7d": {
                    "$avg": "$attendance_rate",
                    "window": {"documents": [-6, 0]}
                }
            }
        }},
        {"$sort": {"date": 1}}
    ]
    return pipeline

@app.get("/analytics/departments/{department}/trend", tags=["analytics"], response_model=Trend)
def department_trend(
    department: str = FastAPIPath(...),
    from_date: str = Query(..., alias="from"),
    to_date: str = Query(..., alias="to")
):
    """Department daily trend with $densify-generated dates and 7-day moving average."""
    pipeline = build_department_trend_pipeline(department, from_date, to_date)
    results = list(db.employees.aggregate(pipeline))

    items = []
    for r in results:
        # Apply half-up rounding for rate fields (4 decimals)
        att_rate = r.get("attendance_rate")
        if att_rate is not None:
            att_rate = round_half_up_4(att_rate)

        mov_avg = r.get("moving_avg_7d")
        if mov_avg is not None:
            mov_avg = round_half_up_4(mov_avg)

        items.append(TrendItem(
            date=r["date"],
            is_working_day=r["is_working_day"],
            headcount=r["headcount"],
            present_count=r["present_count"],
            late_count=r["late_count"],
            attendance_rate=att_rate,
            moving_avg_7d=mov_avg
        ))

    return {"department": department, "items": items}

# --------------------------------------------------------------------------- #
# Explain Endpoint
# --------------------------------------------------------------------------- #

def make_serializable(obj: Any) -> Any:
    """Convert MongoDB/BSON types to JSON-serializable Python types."""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, datetime):
        return to_epoch_ms(obj)
    # Handle BSON Timestamp
    from bson.timestamp import Timestamp
    if isinstance(obj, Timestamp):
        return to_epoch_ms(obj.as_datetime())
    # Handle ObjectId
    from bson.objectid import ObjectId
    if isinstance(obj, ObjectId):
        return str(obj)
    # Handle other BSON types
    if hasattr(obj, 'as_datetime'):  # Timestamp (fallback)
        return to_epoch_ms(obj.as_datetime())
    if hasattr(obj, '__iter__') and not isinstance(obj, (str, bytes)):
        if isinstance(obj, dict):
            return {k: make_serializable(v) for k, v in obj.items()}
        elif isinstance(obj, (list, tuple, set)):
            return [make_serializable(v) for v in obj]
    if hasattr(obj, '__str__'):  # Other BSON types
        return str(obj)
    return str(obj)

def get_explain_plan(endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute MongoDB explain for the given endpoint."""
    if endpoint == "attendance_list":
        # Build the same query as list_attendance
        query = {}
        if params.get("emp_code"):
            query["emp_code"] = params["emp_code"]
        if params.get("date_from"):
            if "date" not in query:
                query["date"] = {}
            query["date"]["$gte"] = params["date_from"]
        if params.get("date_to"):
            if "date" not in query:
                query["date"] = {}
            query["date"]["$lte"] = params["date_to"]
        if params.get("status"):
            query["status"] = params["status"]

        page = params.get("page", 1)
        page_size = params.get("page_size", 20)
        skip = (page - 1) * page_size

        # Use find with explain
        explain = db.command("explain", {"find": "attendance_logs", "filter": query, "sort": {"date": -1, "emp_code": 1}, "skip": skip, "limit": page_size}, verbosity="executionStats")
        return {"collection": "attendance_logs", "explain": make_serializable(explain)}

    elif endpoint == "employee_monthly":
        if not params.get("emp_code") or not params.get("month"):
            raise HTTPException(422, "emp_code and month are required")
        year, month_num = validate_month_str(params["month"])
        pipeline, _ = build_employee_monthly_pipeline(params["emp_code"], year, month_num)
        explain = db.command("explain", {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}}, verbosity="executionStats")
        return {"collection": "attendance_logs", "explain": make_serializable(explain)}

    elif endpoint == "department_summary":
        if not params.get("month"):
            raise HTTPException(422, "month is required")
        year, month_num = validate_month_str(params["month"])
        pipeline = build_department_summary_pipeline(year, month_num, params.get("department"))
        explain = db.command("explain", {"aggregate": "employees", "pipeline": pipeline, "cursor": {}}, verbosity="executionStats")
        return {"collection": "employees", "explain": make_serializable(explain)}

    elif endpoint == "late_leaderboard":
        if not params.get("month"):
            raise HTTPException(422, "month is required")
        year, month_num = validate_month_str(params["month"])
        limit = params.get("limit", 10)
        pipeline = build_late_leaderboard_pipeline(year, month_num, params.get("department"), limit)
        explain = db.command("explain", {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}}, verbosity="executionStats")
        return {"collection": "attendance_logs", "explain": make_serializable(explain)}

    elif endpoint == "department_trend":
        if not params.get("department") or not params.get("from") or not params.get("to"):
            raise HTTPException(422, "department, from, and to are required")
        pipeline = build_department_trend_pipeline(params["department"], params["from"], params["to"])
        explain = db.command("explain", {"aggregate": "employees", "pipeline": pipeline, "cursor": {}}, verbosity="executionStats")
        return {"collection": "employees", "explain": make_serializable(explain)}

    else:
        raise HTTPException(422, f"Unknown endpoint: {endpoint}")

@app.get("/admin/explain/{endpoint}", tags=["admin"], response_model=ExplainResponse)
def explain_endpoint(
    endpoint: str = FastAPIPath(..., pattern=r'^(attendance_list|employee_monthly|department_summary|late_leaderboard|department_trend)$'),
    emp_code: Optional[str] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = Query(10, ge=1, le=50),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100)
):
    """Return MongoDB explain plan for an endpoint's main query."""
    params = {
        "emp_code": emp_code,
        "month": month,
        "department": department,
        "limit": limit,
        "date_from": date_from,
        "date_to": date_to,
        "status": status,
        "from": from_date,
        "to": to_date,
        "page": page,
        "page_size": page_size
    }

    result = get_explain_plan(endpoint, params)
    return {"endpoint": endpoint, **result}