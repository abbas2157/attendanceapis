import json

from django.db import models
from django.utils import timezone


class Warehousedetail(models.Model):
    full_name = models.CharField(max_length=200)
    short_name = models.CharField(max_length=50)
    latlong = models.CharField(max_length=50, default="0,0")
    status = models.IntegerField(default=1)
    remarks = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.short_name} - {self.full_name}"

    class Meta:
        db_table = "Warehousedetails"


class RegisteredEmployee(models.Model):
    employeecode = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    fathername = models.CharField(max_length=100)
    attendancecode = models.CharField(max_length=50, unique=True)
    warehouseid = models.CharField(max_length=50, null=True, blank=True)
    latlong = models.CharField(max_length=50, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.employeecode})"

    class Meta:
        db_table = "RegisteredEmployee"


class EmployeeFaceEmbedding(models.Model):
    # Each employee can hold multiple enrollment samples. Verification becomes
    # more stable because the matcher can compare the live face against more
    # than one historical angle/lighting sample.
    employee = models.ForeignKey(
        RegisteredEmployee,
        on_delete=models.CASCADE,
        related_name="face_embeddings",
    )
    embedding = models.TextField()
    sample_label = models.CharField(max_length=50, default="primary")
    sample_order = models.PositiveSmallIntegerField(default=1)
    angle_label = models.CharField(max_length=30, null=True, blank=True)
    capture_source = models.CharField(max_length=30, default="mobile_enrollment")
    capture_session_id = models.CharField(max_length=64, null=True, blank=True)
    quality_score = models.FloatField(null=True, blank=True)
    is_primary = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def set_embedding(self, embedding_list):
        self.embedding = json.dumps(embedding_list)

    def get_embedding(self):
        if self.embedding:
            try:
                return json.loads(self.embedding)
            except (TypeError, json.JSONDecodeError):
                return None
        return None

    class Meta:
        db_table = "EmployeeFaceEmbedding"
        ordering = ["-is_primary", "created_at"]


class AttendanceVerificationAttempt(models.Model):
    employee = models.ForeignKey(
        RegisteredEmployee,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="verification_attempts",
    )
    submitted_employeecode = models.CharField(max_length=50, null=True, blank=True)
    submitted_attendancecode = models.CharField(max_length=50, null=True, blank=True)
    currentlocation = models.CharField(max_length=100, null=True, blank=True)
    matched_warehouse = models.CharField(max_length=50, null=True, blank=True)
    deviceinfo = models.CharField(max_length=200, null=True, blank=True)
    verification_confidence = models.FloatField(null=True, blank=True)
    verification_margin = models.FloatField(null=True, blank=True)
    verification_decision = models.CharField(max_length=50)
    verification_method = models.CharField(max_length=30, default="employee_code_face")
    attempt_status = models.CharField(max_length=30)
    failure_reason = models.CharField(max_length=120, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "AttendanceVerificationAttempt"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["created_at"], name="verify_attempt_time_idx"),
            models.Index(fields=["attempt_status"], name="verify_attempt_status_idx"),
        ]


class EmployeeAttendanceApp(models.Model):
    employee = models.ForeignKey(
        RegisteredEmployee,
        on_delete=models.PROTECT,
        related_name="attendance_records",
    )

    date = models.DateField()
    # Attendance stores full aware datetimes so check-in/check-out calculations
    # are performed on server time instead of trusting the mobile device clock.
    timein = models.DateTimeField(null=True, blank=True)
    fulltimeout = models.DateTimeField(null=True, blank=True)

    deviceinfo = models.CharField(max_length=200, null=True, blank=True)
    assignedlocation = models.CharField(max_length=100, null=True, blank=True)
    checkinlocation = models.CharField(max_length=100, null=True, blank=True)
    checkoutlocation = models.CharField(max_length=100, null=True, blank=True)
    checkin_warehouse = models.CharField(max_length=50, null=True, blank=True)
    checkout_warehouse = models.CharField(max_length=50, null=True, blank=True)
    # Verification audit fields make disputed punches explainable after the
    # event. These values capture what code was submitted and how the backend
    # decided to accept the face verification.
    submitted_employeecode = models.CharField(max_length=50, null=True, blank=True)
    submitted_attendancecode = models.CharField(max_length=50, null=True, blank=True)
    verification_confidence = models.FloatField(null=True, blank=True)
    verification_margin = models.FloatField(null=True, blank=True)
    verification_decision = models.CharField(max_length=50, null=True, blank=True)
    verification_method = models.CharField(max_length=30, default="employee_code_face")
    matched_warehouse = models.CharField(max_length=50, null=True, blank=True)
    correction_reason = models.TextField(null=True, blank=True)
    correction_action = models.CharField(max_length=50, null=True, blank=True)
    corrected_at = models.DateTimeField(null=True, blank=True)
    corrected_by = models.ForeignKey(
        "auth.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="attendance_corrections",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def get_duration(self):
        if not self.timein or not self.fulltimeout:
            return None

        dt_in = timezone.localtime(self.timein)
        dt_out = timezone.localtime(self.fulltimeout)
        delta = dt_out - dt_in

        hours = int(delta.total_seconds() // 3600)
        minutes = int((delta.total_seconds() % 3600) // 60)
        return f"{hours}h {minutes}m"

    def __str__(self):
        return f"{self.employee.name} | {self.date}"

    class Meta:
        db_table = "EmployeeAttendanceApp"
        ordering = ["-date", "-timein"]
        constraints = [
            models.UniqueConstraint(
                fields=["employee", "date"],
                name="unique_employee_attendance_per_date",
            ),
        ]
        indexes = [
            models.Index(fields=["employee", "date"], name="att_emp_date_idx"),
            models.Index(fields=["date"], name="att_date_idx"),
        ]
