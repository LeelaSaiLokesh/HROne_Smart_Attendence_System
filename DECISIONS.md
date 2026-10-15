# DECISIONS.md

## 1. Indexes created and why

**Created indexes:**
- `employees.emp_code` (unique) - enforces employee uniqueness, supports create_employee and all lookups by emp_code
- `employees(department, emp_code)` - supports department filtering with emp_code ordering for list_employees
- `employees(department, joined_on)` - supports department_summary headcount queries (filter by dept + joined_on <= date)
- `attendance_logs(emp_code, date)` (unique) - enforces one record per employee per day (natural key), supports punch_in, punch_out, regularize, and employee_monthly
- `attendance_logs(date desc, emp_code asc)` - supports list_attendance sort order and date-range queries
- `attendance_logs(emp_code, date, status)` - supports filtered attendance queries and analytics pipelines

**Considered but rejected:**
- `attendance_logs(department, date)` - not useful because department lives on employees, not attendance_logs; would require a lookup anyway. The compound index on employees(department, joined_on) combined with attendance_logs(emp_code, date) is sufficient for all analytics.

## 2. Punch-in race condition

When two identical punch-in requests arrive simultaneously for the same employee and date:
1. Both requests pass employee existence check
2. Both construct the same document with emp_code and date
3. Both call `insert_one()` on attendance_logs
4. MongoDB's unique index on `(emp_code, date)` allows exactly one insert to succeed (returns 201)
5. The other request gets a DuplicateKeyError, which we catch and return 409
6. No check-then-insert race window exists because the uniqueness is enforced at the database level

## 3. Leaderboard ties at cutoff

Standard competition ranking (1, 2, 2, 4) is implemented in Python after the aggregation returns sorted results:
- Results are pre-sorted by total_late_minutes desc, emp_code asc
- Iterate through results; when late_minutes changes, rank = position + 1
- Include all employees with rank <= limit
- Also include any additional employees tied at the cutoff rank (rank == limit and same late_minutes as last included)
- This satisfies "return every employee whose rank <= limit, including all ties at the cutoff"

## 4. Department summary headcount with zero logs

The department_summary pipeline starts from `employees` collection:
1. Match employees with `joined_on <= month_end` (and optional department filter)
2. This includes employees who joined on or before month-end, even with zero attendance logs
3. `$lookup` attendance_logs for the month - `preserveNullAndEmptyArrays: true` keeps employees with no logs
4. `$group` by department sums headcount from the employee side (counts all matched employees)
5. Departments with zero qualifying headcount are filtered out by `$match: {headcount: {$gt: 0}}`
6. This ensures employees with zero logs are counted in headcount but don't contribute to present_days, late_count, etc.

## 5. Scaling to 100x data volume

For ~10M attendance records (100x the current ~100k):
- **Shard by emp_code**: Distribute attendance_logs across shards using hashed emp_code; employees collection can be replicated or sharded by department
- **Time-series collections**: Use MongoDB time-series collections for attendance_logs (automatic partitioning by date, compression)
- **Pre-aggregated materialized views**: Maintain daily/weekly/monthly rollups in separate collections updated via change streams or scheduled jobs
- **Read replicas**: Add read replicas for analytics queries to offload primary
- **Index optimization**: Add composite indexes for common query patterns; consider covered queries where possible
- **Pagination optimization**: Use cursor-based pagination (keyset pagination) instead of offset-based for deep pages