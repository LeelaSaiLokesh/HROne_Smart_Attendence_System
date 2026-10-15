# REVIEW.md

List every defect you found in the starter `app/main.py` (helpers and endpoints). For each one:

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | `health` endpoint (line 72-74) | Always returns `{"status": "ok"}` without checking MongoDB connectivity. Should return 503 when MongoDB is unavailable. | Call `/health` when MongoDB is down - returns 200 instead of 503. | Added actual ping check in health endpoint; returns 503 on connection failure. |
| 2 | `create_employee` (line 77-85) | Uses check-then-insert pattern (race condition). No validation of emp_code format, email format, shift_start != shift_end. Returns created_at as datetime instead of epoch ms. | Concurrent duplicate creates can both succeed; invalid data accepted; response doesn't match schema. | Use unique index + insert_one (atomic); added Pydantic validation for all fields; convert created_at to epoch ms. |
| 3 | `list_employees` (line 88-96) | Pagination skip is wrong: `page * page_size` instead of `(page-1) * page_size`. Total count doesn't apply department filter. | Page 1 skips first page_size items; total shows all employees not filtered. | Fixed skip to `(page-1)*page_size`; total uses filtered query. |
| 4 | `compute_late_minutes` (line 33-37) | Grace period logic wrong: returns minutes if > 10, but should return 0 if <= 10 minutes late. Example: 09:40:00 for 09:30 shift should be 0, but returns 10. | Punch-in at exactly 10 min after shift shows 10 late minutes instead of 0. | Fixed: if diff_minutes > 10 return diff_minutes else 0. Also added truncation to seconds. |
| 5 | `punch_in` (line 99-121) | Doesn't handle overnight shifts (R1). Uses check-then-insert for duplicate detection. Doesn't validate status is PRESENT/WFH/ON_DUTY. Doesn't truncate timestamp to seconds. | Overnight shift punch-in gets wrong date; concurrent punch-ins race; invalid status accepted. | Use attendance_date() helper for R1; unique index for atomic duplicate detection; validate status; truncate timestamps. |
| 6 | `list_attendance` (line 124-150) | Loads all docs into memory, sorts in Python (not scalable). Pagination done in Python after loading all. Total is len of loaded docs. | Slow on 100k records; memory issues; wrong total if filtered. | Use DB-level find with sort, skip, limit; count_documents with same filter. |
| 7 | `punch_out` | Missing entirely. | N/A | Implemented with atomic conditional update, proper overtime/work_hours/half_day calc. |
| 8 | `regularize_attendance` (PATCH /attendance/{emp_code}/{date}) | Missing entirely. | N/A | Implemented with audit history, optimistic locking, recompute derived fields. |
| 9 | Employee monthly analytics | Missing entirely. | N/A | Implemented with MongoDB aggregation pipeline. |
| 10 | Department summary analytics | Missing entirely. | N/A | Implemented with MongoDB aggregation across employees + attendance_logs. |
| 11 | Late leaderboard analytics | Missing entirely. | N/A | Implemented with aggregation + Python ranking for standard competition (1,2,2,4). |
| 12 | Department trend analytics | Missing entirely. | N/A | Implemented with aggregation using $range/$map for date generation, $lookup for headcount/attendance, $setWindowFields for 7-day moving avg. |
| 13 | Explain endpoint | Missing entirely. | N/A | Implemented with db.command("explain") for all 5 endpoints, serializable output. |
| 14 | `compute_work_hours` (line 40-41) | Uses Python's round() (banker's rounding) instead of decimal half-up. | 8.985 rounds to 8.98 instead of 8.99. | Use Decimal with ROUND_HALF_UP. |
| 15 | `compute_overtime` (line 44-47) | Doesn't handle overnight shifts correctly; shift_end parsed as same date. | Overnight shift punch-out calculates wrong overtime. | Use attendance date to construct shift_end_ist, convert to UTC, handle overnight. |
| 16 | No indexes created at startup | Starter has no index creation. | Queries slow on large datasets; no uniqueness enforcement. | Added create_indexes() at startup with all required indexes. |
| 17 | No .env loading with proper precedence | load_dotenv() without path or override=False. | .env not found if cwd differs; env vars don't take precedence. | Load .env from project root with override=False. |
| 18 | Timestamp validation missing | No validation of epoch ms range, type, or truncation. | Seconds, floats, strings, out-of-range accepted. | Added validate_epoch_ms(), truncate_to_seconds(), from_epoch_ms()/to_epoch_ms() helpers. |
| 19 | Response model mismatch | Missing half_day in list_attendance for legacy records; history timestamps not converted. | ValidationError on response serialization. | Default half_day=False; convert history timestamps in list_attendance and regularize. |

Also note anything you looked at and decided was **not** a defect, and why:

| # | Where | Why it's not a defect |
|---|---|---|
| 1 | `IST = timezone(timedelta(hours=5, minutes=30))` | Correct IST timezone definition (UTC+05:30). |
| 2 | `PunchInIn.punched_at` default None | Correct per contract - defaults to current time if omitted. |
| 3 | Sample data in sample_data/ | Correct shapes matching DATA_MODEL.md. |
| 4 | sample_seed.py uses json_util with tz_aware=True | Correctly loads BSON dates as timezone-aware datetimes. |
| 5 | requirements.txt | All necessary dependencies listed (fastapi, uvicorn, pymongo, pydantic, python-dotenv). Added email-validator at runtime. |