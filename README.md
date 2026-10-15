# HR Smart Attendance System

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-green)](https://fastapi.tiangolo.com/)
[![MongoDB](https://img.shields.io/badge/MongoDB-6.0%2B-brightgreen)](https://www.mongodb.com/)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

Employee Attendance & Analytics API built with **FastAPI**, **MongoDB Atlas**, and **PyMongo**. Implements all 12 endpoints per the HROne engineering assignment contract (`openapi.yaml`).

---

## 🚀 Quick Start

### Prerequisites
- **Python 3.11+**
- **MongoDB 6.0+** (Atlas Free Tier M0 or local Docker)
- **pip** / **virtualenv**

### 1. Clone & Setup
```bash
# Clone repository
git clone <repository-url>
cd SDT_candidate_kit

# Create virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure MongoDB
Create `.env` in project root:
```env
MONGO_URI=mongodb+srv://<username>:<password>@<cluster>.mongodb.net/
MONGO_DB=attendance_db
```
> ⚠️ **Security**: Never commit `.env`. The `.gitignore` excludes it. Environment variables take precedence over `.env` (loaded with `override=False`).

### 3. Verify MongoDB Connection
```bash
python -c "
from pymongo import MongoClient
import os
from dotenv import load_dotenv
load_dotenv()
client = MongoClient(os.getenv('MONGO_URI'), serverSelectionTimeoutMS=10000)
client.admin.command('ping')
print('✅ MongoDB connection OK')
"
```
**Expected Output:**
```
✅ MongoDB connection OK
```

### 4. Load Sample Data (Optional)
```bash
python sample_seed.py
```
**Expected Output:**
```
loaded  6 documents into 'attendance_db.employees'
loaded  9 documents into 'attendance_db.attendance_logs'
```
> ⚠️ **Warning**: This drops and recreates `employees` and `attendance_logs` collections. Run only against a disposable development database.

### 5. Start the API Server
```bash
uvicorn app.main:app --port 8000
```
**Expected Output:**
```
INFO:     Started server process [xxxx]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
Created/verified 4 employee indexes, 5 attendance indexes
MongoDB connection verified
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

### 6. Verify API is Running
Open in browser:
- **API Base**: http://localhost:8000
- **Interactive Docs (Swagger)**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

**Health Check:**
```bash
curl http://localhost:8000/health
```
**Expected Response:**
```json
{"status": "ok"}
```

---

## 📚 API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Readiness probe (200 if MongoDB OK, 503 otherwise) |
| `POST` | `/employees` | Create employee |
| `GET` | `/employees` | List employees (filter by department, pagination) |
| `POST` | `/attendance/punch-in` | Record punch-in |
| `POST` | `/attendance/punch-out` | Record punch-out |
| `GET` | `/attendance` | List attendance records (filters, pagination) |
| `PATCH` | `/attendance/{emp_code}/{date}` | Regularize (correct) attendance |
| `GET` | `/analytics/employees/{emp_code}/monthly` | Employee monthly summary |
| `GET` | `/analytics/departments/summary` | Department monthly summary |
| `GET` | `/analytics/leaderboard/late` | Late-comers leaderboard |
| `GET` | `/analytics/departments/{department}/trend` | Department daily trend |
| `GET` | `/admin/explain/{endpoint}` | MongoDB explain plan |

---

## 🧪 Example API Usage

### Create Employee
```bash
curl -X POST http://localhost:8000/employees \
  -H "Content-Type: application/json" \
  -d '{
    "emp_code": "EMP1001",
    "name": "John Doe",
    "email": "john@company.com",
    "department": "Engineering",
    "shift_start": "09:30",
    "shift_end": "18:30",
    "joined_on": "2026-01-15"
  }'
```
**Response (201):**
```json
{
  "emp_code": "EMP1001",
  "name": "John Doe",
  "email": "john@company.com",
  "department": "Engineering",
  "shift_start": "09:30",
  "shift_end": "18:30",
  "joined_on": "2026-01-15",
  "created_at": 1792050233000
}
```

### Punch In
```bash
curl -X POST http://localhost:8000/attendance/punch-in \
  -H "Content-Type: application/json" \
  -d '{"emp_code": "EMP1001", "punched_at": 1783503000000, "status": "PRESENT"}'
```
**Response (201):**
```json
{
  "emp_code": "EMP1001",
  "date": "2026-07-08",
  "status": "PRESENT",
  "punch_in": 1783503000000,
  "punch_out": null,
  "work_hours": null,
  "late_minutes": 0,
  "overtime_minutes": 0,
  "half_day": false,
  "history": []
}
```

### Punch Out
```bash
curl -X POST http://localhost:8000/attendance/punch-out \
  -H "Content-Type: application/json" \
  -d '{"emp_code": "EMP1001", "punched_at": 1783515600000}'
```
**Response (200):**
```json
{
  "emp_code": "EMP1001",
  "date": "2026-07-08",
  "status": "PRESENT",
  "punch_in": 1783503000000,
  "punch_out": 1783515600000,
  "work_hours": 9.0,
  "late_minutes": 0,
  "overtime_minutes": 0,
  "half_day": false,
  "history": []
}
```

### Regularize Attendance
```bash
curl -X PATCH http://localhost:8000/attendance/EMP1001/2026-07-08 \
  -H "Content-Type: application/json" \
  -d '{
    "status": "WFH",
    "reason": "work from home",
    "regularized_by": "hr.admin"
  }'
```
**Response (200):**
```json
{
  "emp_code": "EMP1001",
  "date": "2026-07-08",
  "status": "WFH",
  "punch_in": 1783503000000,
  "punch_out": 1783515600000,
  "work_hours": 9.0,
  "late_minutes": 0,
  "overtime_minutes": 0,
  "half_day": false,
  "history": [
    {
      "at": 1792050233000,
      "by": "hr.admin",
      "reason": "work from home",
      "changes": {
        "status": {"from": "PRESENT", "to": "WFH"}
      }
    }
  ]
}
```

### Employee Monthly Analytics
```bash
curl "http://localhost:8000/analytics/employees/EMP0001/monthly?month=2026-07"
```
**Response (200):**
```json
{
  "emp_code": "EMP0001",
  "month": "2026-07",
  "working_days": 23,
  "present_days": 2.0,
  "leave_days": 0,
  "late_count": 0,
  "total_late_minutes": 0,
  "total_overtime_minutes": 0,
  "attendance_pct": 8.7
}
```

### Department Summary
```bash
curl "http://localhost:8000/analytics/departments/summary?month=2026-07"
```
**Response (200):**
```json
{
  "month": "2026-07",
  "items": [
    {
      "department": "Engineering",
      "headcount": 4,
      "present_days": 3.0,
      "avg_work_hours": 9.04,
      "late_count": 0,
      "total_late_minutes": 0,
      "leave_count": 0,
      "on_duty_count": 0
    },
    {
      "department": "Sales",
      "headcount": 4,
      "present_days": 1.5,
      "avg_work_hours": 6.29,
      "late_count": 1,
      "total_late_minutes": 35,
      "leave_count": 1,
      "on_duty_count": 0
    }
  ]
}
```

### Late Leaderboard
```bash
curl "http://localhost:8000/analytics/leaderboard/late?month=2026-07&limit=10"
```
**Response (200):**
```json
{
  "month": "2026-07",
  "items": [
    {
      "rank": 1,
      "emp_code": "EMP0003",
      "name": "Neha Iyer",
      "department": "Sales",
      "total_late_minutes": 35,
      "late_count": 1
    }
  ]
}
```

### Department Trend
```bash
curl "http://localhost:8000/analytics/departments/Engineering/trend?from=2026-07-01&to=2026-07-10"
```
**Response (200):**
```json
{
  "department": "Engineering",
  "items": [
    {
      "date": "2026-07-01",
      "is_working_day": true,
      "headcount": 1,
      "present_count": 0.0,
      "late_count": 0,
      "attendance_rate": 0.0,
      "moving_avg_7d": 0.0
    },
    {
      "date": "2026-07-06",
      "is_working_day": true,
      "headcount": 1,
      "present_count": 1.0,
      "late_count": 0,
      "attendance_rate": 1.0,
      "moving_avg_7d": 0.25
    }
  ]
}
```

### Explain Endpoint
```bash
curl "http://localhost:8000/admin/explain/employee_monthly?emp_code=EMP0001&month=2026-07"
```
**Response (200):** Raw MongoDB explain output with `executionStats` showing `IXSCAN` on `ux_emp_code_date` index.

---

## 🧪 Run Tests

### Automated Test Suite
```bash
python -c "
import requests
tests = [
    ('GET', '/health', 200),
    ('GET', '/employees', 200),
    ('GET', '/attendance', 200),
    ('GET', '/analytics/employees/EMP0001/monthly?month=2026-07', 200),
    ('GET', '/analytics/departments/summary?month=2026-07', 200),
    ('GET', '/analytics/leaderboard/late?month=2026-07', 200),
    ('GET', '/analytics/departments/Engineering/trend?from=2026-07-01&to=2026-07-10', 200),
    ('GET', '/admin/explain/attendance_list?emp_code=EMP0001', 200),
    ('GET', '/admin/explain/employee_monthly?emp_code=EMP0001&month=2026-07', 200),
    ('GET', '/admin/explain/department_summary?month=2026-07', 200),
    ('GET', '/admin/explain/late_leaderboard?month=2026-07', 200),
    ('GET', '/admin/explain/department_trend?department=Engineering&from=2026-07-01&to=2026-07-10', 200),
]
for m, p, exp in tests:
    r = requests.request(m, f'http://localhost:8000{p}')
    status = '✅ PASS' if r.status_code == exp else f'❌ FAIL (got {r.status_code})'
    print(f'{status}: {m} {p}')
"
```
**Expected Output:**
```
✅ PASS: GET /health
✅ PASS: GET /employees
✅ PASS: GET /attendance
✅ PASS: GET /analytics/employees/EMP0001/monthly?month=2026-07
✅ PASS: GET /analytics/departments/summary?month=2026-07
✅ PASS: GET /analytics/leaderboard/late?month=2026-07
✅ PASS: GET /analytics/departments/Engineering/trend?from=2026-07-01&to=2026-07-10
✅ PASS: GET /admin/explain/attendance_list?emp_code=EMP0001
✅ PASS: GET /admin/explain/employee_monthly?emp_code=EMP0001&month=2026-07
✅ PASS: GET /admin/explain/department_summary?month=2026-07
✅ PASS: GET /admin/explain/late_leaderboard?month=2026-07
✅ PASS: GET /admin/explain/department_trend?department=Engineering&from=2026-07-01&to=2026-07-10
```

---

## 🏗️ Project Structure

```
SDT_candidate_kit/
├── app/
│   ├── __init__.py
│   └── main.py              # All application code (FastAPI, models, endpoints)
├── sample_data/
│   ├── employees.json       # Sample employees (MongoDB Extended JSON)
│   └── attendance_logs.json # Sample attendance logs
├── sample_seed.py           # Loads sample data into MongoDB
├── requirements.txt         # Python dependencies
├── openapi.yaml             # API contract (authoritative)
├── DATA_MODEL.md            # MongoDB storage model (authoritative)
├── PROBLEM_STATEMENT.docx   # Assignment description
├── REVIEW.md                # Starter code defects review (19 defects)
├── DECISIONS.md             # Design decisions (5 questions)
├── README.md                # This file
├── .gitignore               # Excludes .env, .venv, __pycache__, *.pyc
└── .env                     # Local config (NOT committed)
```

---

## 🔑 Key Implementation Highlights

### Business Rules (R1-R10) - All Implemented
| Rule | Description | Implementation |
|------|-------------|----------------|
| **R1** | IST timezone (UTC+05:30); overnight shifts | `attendance_date()` helper with overnight logic |
| **R2** | 10-min grace; late = floor(min since shift_start) if >10min | `compute_late_minutes()` with 600s threshold |
| **R3** | Overtime >=30min, floor minutes | `compute_overtime_minutes()` with shift_end in IST |
| **R4** | Work hours = (punch_out-punch_in)/3600, half-up 2dp | `Decimal.quantize(ROUND_HALF_UP)` |
| **R5** | Half day if work_hours < 4.50 | `compute_half_day()` on rounded value |
| **R6** | PRESENT/WFH/ON_DUTY = present | Status validation in all endpoints |
| **R7** | Working days Mon-Fri; joined_on bound | `is_working_day()`, month bounds from joined_on |
| **R8** | Half-up rounding (2dp general, 4dp rates) | `round_half_up_2()` / `round_half_up_4()` |
| **R9** | Headcount includes zero-log employees | Department summary starts from `employees` collection |
| **R10** | Page starts at 1, page_size max 100 | Query param validation + `(page-1)*page_size` |

### Concurrency Safety
| Operation | Mechanism |
|-----------|-----------|
| Employee creation | Unique index on `emp_code` (atomic `insert_one`) |
| Punch-in | Unique compound index on `(emp_code, date)` |
| Punch-out | Conditional `find_one_and_update` with `punch_out: null` |
| Regularization | Optimistic locking via `_id` + current field values |

### Database Indexes (Created at Startup)
```python
# employees
ux_emp_code          # unique on emp_code
ix_dept_emp_code     # (department, emp_code)
ix_dept_joined       # (department, joined_on)
ix_joined_on         # joined_on (for dept summary optimization)

# attendance_logs
ux_emp_code_date     # unique on (emp_code, date)
ix_date_emp_code     # (date desc, emp_code asc)
ix_emp_date_status   # (emp_code, date, status)
ix_date_status       # (date, status)
```

### Analytics - MongoDB Aggregation Only
- **Employee Monthly**: `$match` → `$project` (is_weekday, is_present, is_leave) → `$group`
- **Department Summary**: `$lookup` attendance_logs → `$unwind` → `$group` by department
- **Late Leaderboard**: `$match` → `$group` by emp_code → `$lookup` employees → Python ranking (1,2,2,4)
- **Department Trend**: `$range` + `$map` for date sequence → `$lookup` headcount/attendance → `$setWindowFields` for 7-day MA

---

## 🛠️ Troubleshooting

| Issue | Solution |
|-------|----------|
| `ModuleNotFoundError: pymongo` | Run `pip install -r requirements.txt` |
| `ModuleNotFoundError: email_validator` | Run `pip install email-validator` |
| `ServerSelectionTimeoutError` | Check `.env` MONGO_URI; verify Atlas Network Access allows your IP |
| `409 Conflict on punch-in` | Employee already punched in for that date (unique constraint) |
| `422 Validation Error` | Check timestamp format (epoch ms, not seconds), date format (YYYY-MM-DD) |
| `503 on /health` | MongoDB unavailable; check connection string and network |
| Port 8000 in use | Kill existing process: `taskkill /PID <pid> /F` (Windows) |

---

## 📋 Submission Checklist

- [x] All 12 endpoints implemented per `openapi.yaml`
- [x] `app/main.py` contains all implementation code
- [x] `requirements.txt` includes all dependencies
- [x] `uvicorn app.main:app --port 8000` starts successfully
- [x] `MONGO_URI` / `MONGO_DB` loaded from environment (`.env` supported)
- [x] MongoDB indexes created idempotently at startup
- [x] Business rules R1-R10 correctly implemented
- [x] Concurrency safety via atomic DB operations
- [x] Analytics use MongoDB aggregation pipelines (no Python loops)
- [x] Explain endpoints return real `executionStats` with IXSCAN
- [x] `REVIEW.md` documents 19 starter defects
- [x] `DECISIONS.md` answers 5 design questions
- [x] `.gitignore` excludes `.env`, `.venv`, `__pycache__`, `*.pyc`
- [x] No secrets, Dockerfile, or virtual environment committed

---

## 📄 License

This project is submitted for the HROne Engineering Internship Assignment evaluation.

---

**Built with ❤️ using FastAPI, MongoDB, and PyMongo**