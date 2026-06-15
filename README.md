# BAMS - Biometric Attendance Management System

## 1. Project Overview

BAMS is an industrial attendance management system designed for factory, warehouse, and site operations where attendance accuracy matters for payroll, auditability, and fraud resistance.

This repository currently contains the **Django + Django REST Framework backend**. The mobile app is developed separately in Flutter and integrates with the APIs described here.

The backend is responsible for:

- employee enrollment
- multi-sample face embedding storage
- warehouse/geofence validation
- attendance marking
- attendance history
- verification audit logging
- suspicious attempt review
- missing punch-out correction workflow
- production security and API controls

This system is intentionally designed around **verification**, not blind identification:

- old prototype question: "Who is this face among all employees?"
- current production question: "Does this face match the employee code submitted from an approved warehouse premises?"

That change is the core architectural improvement in this project.

---

## 2. Main Technology Stack

### Backend

- Python
- Django
- Django REST Framework
- MySQL
- JWT authentication for admin/HR APIs

### Face Recognition

- DeepFace
- Facenet embeddings
- multiple detector backends with strict detection flow

### Deployment Target

- Gunicorn
- Nginx
- MySQL
- Linux server

---

## 3. Business Problem This System Solves

The system is built to solve real operational attendance problems:

- employees marking attendance from the wrong location
- one employee being falsely matched to another
- duplicate attendance attempts
- disputed attendance records
- missing punch-out records
- poor auditability for HR

To address that, the backend now enforces:

- warehouse-based location validation
- employee-code-based claimed identity verification
- strict face verification threshold and ambiguity margin
- multiple face samples per employee
- verification logging for accepted and rejected attempts
- correction trail for HR/admin actions

---

## 4. Current System Design

### 4.1 High-Level Flows

There are two major flows in the system:

1. Employee Enrollment
2. Attendance Marking

### 4.2 Employee Enrollment Flow

HR/admin registers an employee with:

- `employeecode`
- `attendancecode`
- `name`
- `fathername`
- `warehouseid`
- optional `latlong`
- multiple face photos

Backend behavior:

1. validates required fields
2. validates uploaded images
3. extracts embeddings from each enrollment image
4. checks whether those faces match or are too similar to already enrolled employees
5. creates the employee
6. stores one embedding row per image in `EmployeeFaceEmbedding`

Important rule:

- the system no longer depends on a single photo
- the system no longer stores face data in the old employee-level embedding field
- the source of truth is now `EmployeeFaceEmbedding`

### 4.3 Attendance Marking Flow

Attendance is intentionally **open/public** at the API level, but not open logically.

The attendance client submits:

- `currentlocation`
- `employeecode` or `attendancecode`
- live face frame image
- optional `deviceinfo`

Backend behavior:

1. validate request fields
2. validate current location format
3. find active warehouse within geofence radius
4. load the claimed employee by submitted code
5. record the actual warehouse where the punch is happening
6. extract face embedding from uploaded frame
7. verify face **only against that claimed employee**
8. decide:
   - `check_in`
   - `check_out`
   - `already_done`
   - `cooldown`
   - `ambiguous_face`
   - rejection
9. store verification audit fields
10. store verification attempt log

This is safer than matching against the entire employee population.

---

## 5. Repository Structure

```text
D:\BAMS
|-- attendance/          # Core attendance app
|-- BAMS/                # Django project settings and root config
|-- media/               # Uploaded media files
|-- manage.py
|-- requirements.txt
|-- Procfile
|-- .env
```

### Important Backend Files

- [D:\BAMS\attendance\models.py](/D:/BAMS/attendance/models.py)
- [D:\BAMS\attendance\views.py](/D:/BAMS/attendance/views.py)
- [D:\BAMS\attendance\face_utils.py](/D:/BAMS/attendance/face_utils.py)
- [D:\BAMS\attendance\serializers.py](/D:/BAMS/attendance/serializers.py)
- [D:\BAMS\attendance\urls.py](/D:/BAMS/attendance/urls.py)
- [D:\BAMS\attendance\tests.py](/D:/BAMS/attendance/tests.py)
- [D:\BAMS\BAMS\settings.py](/D:/BAMS/BAMS/settings.py)

---

## 6. Database Design

### 6.1 `Warehousedetail`

Stores active warehouse information.

Key fields:

- `full_name`
- `short_name`
- `latlong`
- `status`
- `remarks`
- `created_at`

Purpose:

- warehouse list for HR/admin
- geofence validation during attendance

### 6.2 `RegisteredEmployee`

Stores employee master data.

Key fields:

- `employeecode` (unique)
- `attendancecode` (unique)
- `name`
- `fathername`
- `warehouseid`
- `latlong`
- `is_active`
- `created_at`

Purpose:

- employee identity
- attendance code ownership
- warehouse assignment

Important note:

- the old legacy single-embedding field has been removed
- face data is no longer stored here

### 6.3 `EmployeeFaceEmbedding`

Stores one face embedding row per employee sample.

Key fields:

- `employee`
- `embedding`
- `sample_label`
- `sample_order`
- `angle_label`
- `capture_source`
- `capture_session_id`
- `quality_score`
- `is_primary`
- `created_at`

Purpose:

- store multiple reference samples per employee
- improve verification accuracy across angle/lighting variation
- support guided capture on mobile

### 6.4 `EmployeeAttendanceApp`

Stores actual attendance records.

Key fields:

- `employee` (stable FK to employee ID)
- `date`
- `timein`
- `fulltimeout`
- `deviceinfo`
- `assignedlocation`
- `checkinlocation`
- `checkoutlocation`
- `checkin_warehouse`
- `checkout_warehouse`
- `submitted_employeecode`
- `submitted_attendancecode`
- `verification_confidence`
- `verification_margin`
- `verification_decision`
- `verification_method`
- `matched_warehouse`
- `correction_reason`
- `correction_action`
- `corrected_at`
- `corrected_by`
- `created_at`
- `updated_at`

Constraints:

- one attendance record per employee per date

Purpose:

- check-in/check-out tracking
- auditability
- correction tracking

### 6.5 `AttendanceVerificationAttempt`

Stores verification attempts, including failed and ambiguous attempts.

Key fields:

- `employee`
- `submitted_employeecode`
- `submitted_attendancecode`
- `currentlocation`
- `matched_warehouse`
- `deviceinfo`
- `verification_confidence`
- `verification_margin`
- `verification_decision`
- `verification_method`
- `attempt_status`
- `failure_reason`
- `created_at`

Purpose:

- fraud review
- disputed attendance investigation
- operational analytics

---

## 7. How Face Recognition Works

### 7.1 Runtime Strategy

The matcher does **not** compare a face against all employees during attendance.

Instead:

1. employee submits employee code or attendance code
2. backend loads that employee only
3. backend compares live embedding against that employee's stored face samples

This avoids the dangerous "best of all employees" approach.

### 7.2 Embedding Storage Format

Each embedding is stored in the database as:

- a JSON vector string
- inside the `embedding` text field of `EmployeeFaceEmbedding`

Example:

```json
[0.0142, -0.3841, 0.2218, ...]
```

If an employee has 3 enrollment images:

- 3 rows are created in `EmployeeFaceEmbedding`
- each row stores 1 vector

This means:

- we do **not** merge multiple embeddings into one field
- we keep one embedding per sample row

### 7.3 Matching Rules

Face verification uses:

- `FACE_MATCH_THRESHOLD`
- `FACE_MATCH_MARGIN`

Current default behavior:

- match must be above threshold
- if top score is too close to second-best score, it becomes ambiguous
- no reliable face detection means rejection

### 7.4 Detector Backends

Configured detector order:

1. `retinaface`
2. `ssd`
3. `opencv`

Reason:

- prefer strongest detector first
- fall back only if earlier detectors fail

### 7.5 Image Validation

Before DeepFace runs, uploaded images are validated for:

- allowed extension
- file size
- image readability
- resolution limits

This protects the API from bad uploads and reduces unnecessary expensive processing.

---

## 8. Attendance Decision Logic

### 8.1 Success Cases

If verification succeeds:

- if no attendance record exists today -> `check_in`
- if today's record exists with no `fulltimeout` -> `check_out`
- if today's record already has `fulltimeout` -> `already_done`

### 8.2 Rejection Cases

The backend rejects attendance when:

- no `frame` is submitted
- no `currentlocation` is submitted
- employee code/attendance code is missing
- `currentlocation` is invalid
- user is outside warehouse geofence
- employee code is not recognized
- no reliable face is detected
- face does not match submitted employee
- request conflicts at DB level

### 8.3 Ambiguity Case

If the face score is not strong enough or is too close to a competing score:

- response status becomes `ambiguous_face`
- attendance is not marked

This is intentional. It is better to reject than to wrongly mark another employee.

### 8.4 Cooldown Case

If the employee was recognized too recently:

- status becomes `cooldown`

This reduces accidental rapid duplicate actions.

---

## 9. Security Design

### 9.1 Admin/HR APIs

The following are protected with admin/staff access:

- warehouse list
- employee list/create/update/delete
- attendance records
- attendance attempt review
- dashboard stats
- attendance correction

### 9.2 Attendance Endpoint

Attendance marking remains `AllowAny` by design because the product requires an open attendance station flow.

However, it is protected by backend checks:

- geofence validation
- claimed employee code
- active warehouse premises
- face verification
- throttling
- audit logging

### 9.3 Production Security Settings

Configured in [D:\BAMS\BAMS\settings.py](/D:/BAMS/BAMS/settings.py):

- `DEBUG` via environment
- `ALLOWED_HOSTS` via environment
- `CSRF_TRUSTED_ORIGINS`
- secure cookies
- HSTS
- SSL redirect
- JWT auth for protected APIs
- DRF throttling

---

## 10. API Endpoints

Base paths are defined in [D:\BAMS\attendance\urls.py](/D:/BAMS/attendance/urls.py).

### 10.1 Warehouses

`GET /api/warehouses`

- admin/staff only
- returns active warehouses

### 10.2 Employees

`GET /api/employees`

- admin/staff only
- list active employees

`POST /api/employees`

- admin/staff only
- create employee with multi-photo enrollment

Expected fields:

- `name`
- `fathername`
- `employeecode`
- `attendancecode`
- `warehouseid`
- optional `latlong`
- `photos[]`
- optional `angle_labels[]`
- optional `angle_labels_csv`
- optional `capture_session_id`

`GET /api/employees/<pk>`

- admin/staff only
- get employee details

`PUT /api/employees/<pk>`

- admin/staff only
- update employee data
- can replace face samples

`DELETE /api/employees/<pk>`

- admin/staff only
- soft deactivate employee

### 10.3 Attendance Marking

`POST /api/attendance/mark`

Public endpoint.

`POST /api/attendance/precheck`

Public endpoint used by the mobile app before opening the camera. It validates the submitted code and confirms the device is inside an active warehouse geofence.

Expected fields:

- `currentlocation`
- `frame`
- `employeecode` or `attendancecode`
- optional `deviceinfo`

Possible responses:

- `success` + `check_in`
- `success` + `check_out`
- `already_done`
- `cooldown`
- `ambiguous_face`
- `error`

### 10.4 Attendance Records

`GET /api/attendance/records`

- admin/staff only
- query params:
  - `date`
  - `attendancecode`

Returns:

- employee identity
- check-in/check-out times
- duration
- verification fields
- correction fields
- location fields

### 10.5 Attendance Attempt Review

`GET /api/attendance/attempts`

- admin/staff only

Optional query params:

- `start_date`
- `end_date`
- `status`
- `warehouse`
- `employeecode`
- `attendancecode`

Returns:

- summary counts by attempt status
- failure reason summary
- result list of verification attempts

### 10.6 Attendance Correction

`POST /api/attendance/records/<pk>/correct-checkout`

- admin/staff only

Expected fields:

- `fulltimeout` as ISO datetime
- `correction_reason`
- optional `checkoutlocation`

Behavior:

- fills missing check-out
- stores correction metadata
- records correction attempt log

### 10.7 Dashboard Stats

`GET /api/hr/stats`

- admin/staff only
- returns today summary counts and recent activity

---

## 11. Sample Metadata Contract

The enrollment API now supports optional metadata per capture session.

### `angle_labels`

This describes the pose for each enrollment image, for example:

- `front`
- `left`
- `right`

The mobile app can send:

- repeated multipart values `angle_labels[]`
- or comma-separated `angle_labels_csv`

### `capture_session_id`

This identifies the enrollment capture session and can be generated by the mobile app.

Purpose:

- tie multiple enrollment images to one session
- help future debugging/review

---

## 12. Correction Workflow

Real attendance systems need correction support.

This backend now supports manual correction for missing punch-out cases.

Current supported correction:

- manual checkout for an open attendance record

Stored fields:

- correction reason
- correction action
- corrected timestamp
- corrected by user

This makes manual intervention auditable instead of silent.

---

## 13. Verification Attempt Review

Every important attendance verification outcome can now be reviewed later.

Examples:

- face mismatch
- outside warehouse
- ambiguous face match
- cooldown
- accepted check-in
- accepted check-out
- manual correction

This is useful for:

- HR investigations
- fraud review
- support debugging
- threshold tuning

---

## 14. Migrations History and Why They Matter

Important migration progression in this project:

- `0004`
  - attendance uniqueness and integrity improvements
- `0005`
  - introduced `EmployeeFaceEmbedding`
- `0006`
  - moved attendance FK to stable employee ID
- `0007`
  - added verification audit fields to attendance records
- `0008`
  - added attempt logging and sample metadata base fields
- `0009`
  - removed legacy employee-level embedding field
- `0010`
  - added correction fields and richer sample metadata

This migration chain represents the shift from prototype-level attendance to professional backend architecture.

---

## 15. Tests

Current backend tests live in [D:\BAMS\attendance\tests.py](/D:/BAMS/attendance/tests.py).

They cover:

- face matching rules
- malformed embedding handling
- image validation
- multi-photo enrollment
- face sample persistence
- claimed employee attendance check-in
- claimed employee attendance check-out
- mismatch rejection
- outside-warehouse rejection
- suspicious attempt review
- correction workflow

Run tests with:

```bash
python manage.py test attendance
```

Validation checks:

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
```

---

## 16. Environment Variables

Configured in [D:\BAMS\BAMS\settings.py](/D:/BAMS/BAMS/settings.py).

Important variables:

- `SECRET_KEY`
- `DEBUG`
- `ALLOWED_HOSTS`
- `CSRF_TRUSTED_ORIGINS`
- `DB_NAME`
- `DB_USER`
- `DB_PASSWORD`
- `DB_HOST`
- `DB_PORT`
- `CORS_ALLOW_ALL_ORIGINS`
- `CORS_ALLOWED_ORIGINS`
- `SECURE_SSL_REDIRECT`
- `SECURE_HSTS_SECONDS`
- `SECURE_HSTS_INCLUDE_SUBDOMAINS`
- `SECURE_HSTS_PRELOAD`
- `SESSION_COOKIE_SECURE`
- `CSRF_COOKIE_SECURE`
- `DRF_ANON_THROTTLE`
- `DRF_USER_THROTTLE`
- `DRF_ATTENDANCE_THROTTLE`
- `FACE_MATCH_THRESHOLD`
- `FACE_MATCH_MARGIN`
- `FACE_IMAGE_MAX_BYTES`
- `FACE_IMAGE_MAX_PIXELS`
- `DATA_UPLOAD_MAX_MEMORY_SIZE`
- `FILE_UPLOAD_MAX_MEMORY_SIZE`
- `MIN_FACE_ENROLLMENT_SAMPLES`

---

## 17. Local Setup

### Install dependencies

```bash
pip install -r requirements.txt
```

### Configure `.env`

Set your database and security environment variables.

### Run migrations

```bash
python manage.py migrate
```

### Run development server

```bash
python manage.py runserver
```

### Run tests

```bash
python manage.py test attendance
```

---

## 18. Production Deployment Notes

For deployment, this backend expects:

- Nginx as reverse proxy
- Gunicorn as WSGI server
- MySQL database
- HTTPS enabled

Checklist:

1. set `DEBUG=False`
2. use strong `SECRET_KEY`
3. configure correct `ALLOWED_HOSTS`
4. configure HTTPS and trusted origins
5. restrict CORS for production
6. apply all migrations
7. take DB backup before migration rollout
8. set throttling values
9. configure upload limits for face enrollment:

```bash
DATA_UPLOAD_MAX_MEMORY_SIZE=41943040
FILE_UPLOAD_MAX_MEMORY_SIZE=12582912
FACE_IMAGE_MAX_BYTES=12582912
FACE_IMAGE_MAX_PIXELS=25000000
```

10. set Nginx body size high enough for three face photos:

```nginx
client_max_body_size 40M;
```

11. monitor logs and Gunicorn worker behavior

---

## 19. What Is Finished on Backend

The backend is complete for the current product scope.

Completed foundation work:

- multi-sample enrollment architecture
- sample-only face storage
- safe verification flow
- open attendance with claimed identity verification
- warehouse geofence enforcement
- duplicate attendance protection
- verification audit fields
- suspicious attempt logging
- suspicious attempt review API
- manual correction workflow
- protected admin APIs
- tests for critical flows

This means the backend is stable enough for Flutter integration.

---

## 20. What Comes Next

The next phase is Flutter integration against this finalized backend.

The Flutter app should now be updated to:

1. capture multiple enrollment photos
2. send `photos[]`
3. optionally send `angle_labels[]`
4. optionally send `capture_session_id`
5. mark attendance with:
   - `currentlocation`
   - `frame`
   - `employeecode` or `attendancecode`
6. handle `ambiguous_face`
7. support HR/admin review and correction screens if needed

Potential future enhancements after Flutter:

- liveness / anti-spoof checks
- shift-based/night-shift attendance logic
- advanced reporting/export
- anomaly dashboards

---

## 21. Final Architectural Summary

This project started as a simpler face attendance backend and was upgraded into a more professional verification-based system.

The backend now follows these principles:

- do not trust the client
- do not blindly guess identity from face alone
- do not allow silent correction
- do not allow duplicate attendance records
- do log both accepted and rejected verification paths
- do keep face storage normalized and extensible
- do make HR review possible

That is the current shape of BAMS.
