from io import BytesIO
from unittest.mock import patch

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from PIL import Image
from rest_framework.test import APIClient

from . import face_utils, views
from .face_utils import FaceMatchResult
from .models import (
    AttendanceVerificationAttempt,
    EmployeeAttendanceApp,
    EmployeeFaceEmbedding,
    RegisteredEmployee,
    Warehousedetail,
)


class DummyFaceEmbedding:
    def __init__(self, embedding):
        self.embedding = embedding

    def get_embedding(self):
        if self.embedding == "bad":
            raise ValueError("bad embedding")
        return self.embedding


class DummyFaceEmbeddingManager:
    def __init__(self, samples):
        self.samples = samples

    def all(self):
        return self.samples


class DummyEmployee:
    def __init__(self, name, embedding=None, samples=None):
        self.name = name
        self.embedding = embedding
        self.face_embeddings = DummyFaceEmbeddingManager(samples or [])

    def get_embedding(self):
        if self.embedding == "bad":
            raise ValueError("bad embedding")
        return self.embedding


class FaceMatchingTests(SimpleTestCase):
    def setUp(self):
        self.original_threshold = face_utils.FACE_MATCH_THRESHOLD
        self.original_margin = face_utils.FACE_MATCH_MARGIN
        face_utils.FACE_MATCH_THRESHOLD = 0.78
        face_utils.FACE_MATCH_MARGIN = 0.10

    def tearDown(self):
        face_utils.FACE_MATCH_THRESHOLD = self.original_threshold
        face_utils.FACE_MATCH_MARGIN = self.original_margin

    def test_accepts_strong_clear_match(self):
        result = face_utils.find_matching_employee(
            [1, 0],
            [
                DummyEmployee("A", samples=[DummyFaceEmbedding([1, 0])]),
                DummyEmployee("B", samples=[DummyFaceEmbedding([0, 1])]),
            ],
        )

        self.assertTrue(result.is_match)
        self.assertFalse(result.is_ambiguous)
        self.assertEqual(result.employee.name, "A")  # type: ignore[union-attr]

    def test_rejects_ambiguous_resembling_faces(self):
        result = face_utils.find_matching_employee(
            [1, 0],
            [
                DummyEmployee("A", samples=[DummyFaceEmbedding([1, 0])]),
                DummyEmployee("B", samples=[DummyFaceEmbedding([0.996, 0.089])]),
            ],
        )

        self.assertFalse(result.is_match)
        self.assertTrue(result.is_ambiguous)
        self.assertEqual(result.decision, "ambiguous_match")

    def test_rejects_below_threshold_match(self):
        result = face_utils.find_matching_employee(
            [1, 0],
            [DummyEmployee("A", samples=[DummyFaceEmbedding([0.5, 0.866])])],
        )

        self.assertFalse(result.is_match)
        self.assertFalse(result.is_ambiguous)
        self.assertEqual(result.decision, "below_threshold")

    def test_ignores_malformed_stored_embedding(self):
        result = face_utils.find_matching_employee(
            [1, 0],
            [DummyEmployee("A", samples=[DummyFaceEmbedding("bad")])],
        )

        self.assertFalse(result.is_match)
        self.assertEqual(result.decision, "no_enrolled_faces")

    def test_ignores_wrong_vector_shape(self):
        result = face_utils.find_matching_employee(
            [1, 0],
            [DummyEmployee("A", samples=[DummyFaceEmbedding([1, 0, 0])])],
        )

        self.assertFalse(result.is_match)
        self.assertEqual(result.decision, "no_enrolled_faces")

    def test_prefers_best_sample_for_employee(self):
        result = face_utils.find_matching_employee(
            [1, 0],
            [
                DummyEmployee(
                    "A",
                    samples=[DummyFaceEmbedding([0.5, 0.866]), DummyFaceEmbedding([1, 0])],
                ),
                DummyEmployee("B", samples=[DummyFaceEmbedding([0, 1])]),
            ],
        )

        self.assertTrue(result.is_match)
        self.assertEqual(result.employee.name, "A")  # type: ignore[union-attr]

    def test_requires_face_samples_in_runtime_matcher(self):
        result = face_utils.find_matching_employee(
            [1, 0],
            [DummyEmployee("A", embedding=[1, 0], samples=[])],
        )

        self.assertFalse(result.is_match)
        self.assertEqual(result.decision, "no_enrolled_faces")


class UploadedImageValidationTests(SimpleTestCase):
    def _make_image_upload(self, name="face.jpg", size=(16, 16), image_format="JPEG"):
        buffer = BytesIO()
        image = Image.new("RGB", size, color="white")
        image.save(buffer, format=image_format)
        return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")

    def test_rejects_invalid_image_payload(self):
        upload = SimpleUploadedFile("bad.jpg", b"not-an-image", content_type="image/jpeg")

        with self.assertRaisesMessage(ValueError, "Uploaded file is not a valid image."):
            face_utils.validate_uploaded_image(upload)

    def test_rejects_unsupported_extension(self):
        upload = self._make_image_upload(name="face.gif")

        with self.assertRaisesMessage(ValueError, "Unsupported image format."):
            face_utils.validate_uploaded_image(upload)

    def test_accepts_valid_image(self):
        upload = self._make_image_upload()
        face_utils.validate_uploaded_image(upload)


class AttendanceApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin_client = APIClient()
        self.user_model = get_user_model()
        self.admin_user = self.user_model.objects.create_user(
            username="admin",
            password="testpass123",
            is_staff=True,
        )
        self.admin_client.force_authenticate(self.admin_user)

        self.warehouse = Warehousedetail.objects.create(
            full_name="Main Warehouse",
            short_name="WH1",
            latlong="31.5000,74.3000",
            status=1,
        )
        self.employee = RegisteredEmployee.objects.create(
            employeecode="EMP001",
            name="Ali",
            fathername="Khan",
            attendancecode="ATT001",
            warehouseid="WH1",
            latlong="31.5000,74.3000",
            is_active=True,
        )

    def _image_upload(self, name="face.jpg"):
        buffer = BytesIO()
        image = Image.new("RGB", (32, 32), color="white")
        image.save(buffer, format="JPEG")
        return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")

    def _match_result(self, employee=None, is_match=True, is_ambiguous=False, decision="accepted", confidence=91.0, margin=0.25):
        return FaceMatchResult(
            employee=employee,
            confidence=confidence,
            best_score=0.91,
            second_best_score=0.66 if margin is not None else None,
            margin=margin,
            is_match=is_match,
            is_ambiguous=is_ambiguous,
            decision=decision,
        )

    @patch.object(views, "MIN_FACE_ENROLLMENT_SAMPLES", 3)
    @patch("attendance.views.find_matching_employee")
    @patch("attendance.views.extract_embedding_from_file")
    def test_employee_registration_requires_multiple_photos_and_creates_samples(self, mock_extract, mock_match):
        mock_extract.side_effect = [[1, 0], [0.9, 0.1], [0.8, 0.2]]
        mock_match.return_value = self._match_result(employee=None, is_match=False, decision="below_threshold", confidence=55.0)

        response = self.admin_client.post(
            "/api/employees",
            data={
                "employeecode": "EMP200",
                "name": "Bilal",
                "fathername": "Ahmed",
                "attendancecode": "ATT200",
                "warehouseid": "WH1",
                "latlong": "31.5000,74.3000",
                "angle_labels": ["front", "left", "right"],
                "capture_session_id": "session-123",
                "photos": [self._image_upload("a.jpg"), self._image_upload("b.jpg"), self._image_upload("c.jpg")],
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 201) # type: ignore
        created = RegisteredEmployee.objects.get(employeecode="EMP200")
        self.assertEqual(created.face_embeddings.count(), 3) # type: ignore
        self.assertEqual(response.data["data"]["face_sample_count"], 3) # type: ignore
        first_sample = created.face_embeddings.order_by("sample_order").first() # type: ignore
        self.assertEqual(first_sample.sample_order, 1)  # type: ignore[union-attr]
        self.assertEqual(first_sample.angle_label, "front")  # type: ignore[union-attr]
        self.assertEqual(first_sample.capture_source, "mobile_enrollment")  # type: ignore[union-attr]
        self.assertEqual(first_sample.capture_session_id, "session-123")  # type: ignore[union-attr]
        self.assertEqual(first_sample.quality_score, 1.0)  # type: ignore[union-attr]

    @patch.object(views, "MIN_FACE_ENROLLMENT_SAMPLES", 3)
    def test_employee_registration_rejects_too_few_photos(self):
        response = self.admin_client.post(
            "/api/employees",
            data={
                "employeecode": "EMP201",
                "name": "Usman",
                "fathername": "Ashraf",
                "attendancecode": "ATT201",
                "warehouseid": "WH1",
                "photos": [self._image_upload("a.jpg")],
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 400) # type: ignore
        self.assertIn("At least 3 enrollment photos are required.", response.data["message"]) # type: ignore

    @patch.object(views, "MIN_FACE_ENROLLMENT_SAMPLES", 3)
    @patch("attendance.views.find_matching_employee")
    @patch("attendance.views.extract_embedding_from_file")
    def test_admin_can_update_employee_face_samples(self, mock_extract, mock_match):
        EmployeeFaceEmbedding.objects.create(
            employee=self.employee,
            sample_label="legacy_1",
            sample_order=1,
            capture_source="legacy",
        )
        mock_extract.side_effect = [[1, 0], [0.9, 0.1], [0.8, 0.2]]
        mock_match.return_value = self._match_result(employee=None, is_match=False, decision="below_threshold", confidence=52.0)

        response = self.admin_client.post(
            f"/api/employees/{self.employee.pk}/face-samples",
            data={
                "angle_labels_csv": "front,left,right",
                "capture_session_id": "reenroll-001",
                "capture_source": "web_hr_upload",
                "photos": [self._image_upload("a.jpg"), self._image_upload("b.jpg"), self._image_upload("c.jpg")],
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 200) # type: ignore
        self.assertEqual(response.data["status"], "success") # type: ignore
        self.assertEqual(response.data["data"]["previous_face_sample_count"], 1) # type: ignore
        self.assertEqual(response.data["data"]["face_sample_count"], 3) # type: ignore

        samples = list(self.employee.face_embeddings.order_by("sample_order")) # type: ignore
        self.assertEqual(len(samples), 3)
        self.assertEqual(samples[0].sample_label, "reenrollment_1")
        self.assertEqual(samples[0].angle_label, "front")
        self.assertEqual(samples[0].capture_source, "web_hr_upload")
        self.assertEqual(samples[0].capture_session_id, "reenroll-001")

    def test_face_sample_update_requires_admin(self):
        response = self.client.post(
            f"/api/employees/{self.employee.pk}/face-samples",
            data={"photos": [self._image_upload("a.jpg")]},
            format="multipart",
        )

        self.assertEqual(response.status_code, 401) # type: ignore

    @patch.object(views, "MIN_FACE_ENROLLMENT_SAMPLES", 3)
    @patch("attendance.views.find_matching_employee")
    @patch("attendance.views.extract_embedding_from_file")
    def test_face_sample_update_rejects_face_matching_other_employee(self, mock_extract, mock_match):
        other_employee = RegisteredEmployee.objects.create(
            employeecode="EMP999",
            name="Other Employee",
            fathername="Other Father",
            attendancecode="ATT999",
            warehouseid="WH1",
            latlong="31.5000,74.3000",
            is_active=True,
        )
        mock_extract.side_effect = [[1, 0], [0.9, 0.1], [0.8, 0.2]]
        mock_match.return_value = self._match_result(employee=other_employee, is_match=True, confidence=93.0)

        response = self.admin_client.post(
            f"/api/employees/{self.employee.pk}/face-samples",
            data={
                "photos": [self._image_upload("a.jpg"), self._image_upload("b.jpg"), self._image_upload("c.jpg")],
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 400) # type: ignore
        self.assertIn("Other Employee", response.data["message"]) # type: ignore

    @patch("attendance.views.find_matching_employee")
    @patch("attendance.views.extract_embedding_from_file")
    def test_attendance_marks_check_in_for_claimed_employee(self, mock_extract, mock_match):
        mock_extract.return_value = [1, 0]
        mock_match.return_value = self._match_result(employee=self.employee)

        response = self.client.post(
            "/api/attendance/mark",
            data={
                "employeecode": "EMP001",
                "currentlocation": "31.5000,74.3000",
                "frame": self._image_upload("frame.jpg"),
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["action"], "check_in") # type: ignore
        self.assertEqual(EmployeeAttendanceApp.objects.count(), 1)
        record = EmployeeAttendanceApp.objects.get()
        self.assertEqual(record.submitted_employeecode, "EMP001")
        self.assertIsNone(record.submitted_attendancecode)
        self.assertEqual(record.verification_confidence, 91.0)
        self.assertEqual(record.verification_margin, 0.25)
        self.assertEqual(record.verification_decision, "accepted")
        self.assertEqual(record.matched_warehouse, "WH1")
        self.assertEqual(record.checkin_warehouse, "WH1")
        attempt = AttendanceVerificationAttempt.objects.get()
        self.assertEqual(attempt.attempt_status, "accepted_check_in")
        self.assertEqual(attempt.failure_reason, None)
        self.assertEqual(attempt.employee, self.employee)

    @patch("attendance.views.find_matching_employee")
    @patch("attendance.views.extract_embedding_from_file")
    def test_attendance_rejects_wrong_face_for_submitted_code(self, mock_extract, mock_match):
        mock_extract.return_value = [1, 0]
        mock_match.return_value = self._match_result(
            employee=None,
            is_match=False,
            is_ambiguous=False,
            decision="below_threshold",
            confidence=44.0,
            margin=None, # type: ignore
        )

        response = self.client.post(
            "/api/attendance/mark",
            data={
                "employeecode": "EMP001",
                "currentlocation": "31.5000,74.3000",
                "frame": self._image_upload("frame.jpg"),
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["message"], "Face does not match the submitted employee code.") # type: ignore
        attempt = AttendanceVerificationAttempt.objects.get()
        self.assertEqual(attempt.attempt_status, "rejected")
        self.assertEqual(attempt.failure_reason, "face_mismatch")

    @patch("attendance.views.find_matching_employee")
    @patch("attendance.views.extract_embedding_from_file")
    def test_attendance_allows_employee_from_any_active_warehouse_premises(self, mock_extract, mock_match):
        other_employee = RegisteredEmployee.objects.create(
            employeecode="EMP002",
            name="Hamza",
            fathername="Naeem",
            attendancecode="ATT002",
            warehouseid="WH2",
            latlong="31.5000,74.3000",
            is_active=True,
        )
        mock_extract.return_value = [1, 0]
        mock_match.return_value = self._match_result(employee=other_employee)

        response = self.client.post(
            "/api/attendance/mark",
            data={
                "employeecode": "EMP002",
                "currentlocation": "31.5000,74.3000",
                "frame": self._image_upload("frame.jpg"),
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["action"], "check_in") # type: ignore
        self.assertEqual(response.data["warehouseid"], "WH1") # type: ignore
        self.assertEqual(response.data["assigned_warehouseid"], "WH2") # type: ignore
        attempt = AttendanceVerificationAttempt.objects.get()
        self.assertEqual(attempt.attempt_status, "accepted_check_in")
        record = EmployeeAttendanceApp.objects.get(employee=other_employee)
        self.assertEqual(record.matched_warehouse, "WH1")
        self.assertEqual(record.checkin_warehouse, "WH1")

    def test_attendance_precheck_accepts_code_inside_any_warehouse(self):
        response = self.client.post(
            "/api/attendance/precheck",
            data={
                "employeecode": "EMP001",
                "currentlocation": "31.5000,74.3000",
            },
        )

        self.assertEqual(response.status_code, 200) # type: ignore
        self.assertTrue(response.data["allowed"]) # type: ignore
        self.assertEqual(response.data["warehouse"]["short_name"], "WH1") # type: ignore
        self.assertEqual(response.data["employee"]["employeecode"], "EMP001") # type: ignore

    @patch("attendance.views.find_matching_employee")
    @patch("attendance.views.extract_embedding_from_file")
    def test_attendance_marks_check_out_for_open_record(self, mock_extract, mock_match):
        record = EmployeeAttendanceApp.objects.create(
            employee=self.employee,
            date=views.timezone.localdate(),
            timein=views.timezone.now(),
            deviceinfo="device-1",
            assignedlocation="31.5000,74.3000",
            checkinlocation="31.5000,74.3000",
        )
        aged_created_at = views.timezone.now() - timedelta(seconds=31)
        EmployeeAttendanceApp.objects.filter(pk=record.pk).update(created_at=aged_created_at)
        mock_extract.return_value = [1, 0]
        mock_match.return_value = self._match_result(employee=self.employee)

        response = self.client.post(
            "/api/attendance/mark",
            data={
                "attendancecode": "ATT001",
                "currentlocation": "31.5000,74.3000",
                "frame": self._image_upload("frame.jpg"),
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["action"], "check_out") # type: ignore
        self.assertEqual(EmployeeAttendanceApp.objects.count(), 1)
        updated = EmployeeAttendanceApp.objects.get(pk=record.pk)
        self.assertEqual(updated.submitted_attendancecode, "ATT001")
        self.assertIsNone(updated.submitted_employeecode)
        self.assertEqual(updated.verification_confidence, 91.0)
        self.assertEqual(updated.verification_margin, 0.25)
        self.assertEqual(updated.verification_decision, "accepted")
        self.assertEqual(updated.matched_warehouse, "WH1")
        self.assertEqual(updated.checkout_warehouse, "WH1")
        attempt = AttendanceVerificationAttempt.objects.get(attempt_status="accepted_check_out")
        self.assertEqual(attempt.employee, self.employee)

    @patch("attendance.views.find_matching_employee")
    @patch("attendance.views.extract_embedding_from_file")
    def test_attendance_can_check_out_from_different_active_warehouse(self, mock_extract, mock_match):
        Warehousedetail.objects.create(
            full_name="Second Warehouse",
            short_name="WH2",
            latlong="31.6000,74.4000",
            status=1,
        )
        record = EmployeeAttendanceApp.objects.create(
            employee=self.employee,
            date=views.timezone.localdate(),
            timein=views.timezone.now(),
            deviceinfo="device-1",
            assignedlocation="31.5000,74.3000",
            checkinlocation="31.5000,74.3000",
            checkin_warehouse="WH1",
            matched_warehouse="WH1",
        )
        aged_created_at = views.timezone.now() - timedelta(seconds=31)
        EmployeeAttendanceApp.objects.filter(pk=record.pk).update(created_at=aged_created_at)
        mock_extract.return_value = [1, 0]
        mock_match.return_value = self._match_result(employee=self.employee)

        response = self.client.post(
            "/api/attendance/mark",
            data={
                "employeecode": "EMP001",
                "currentlocation": "31.6000,74.4000",
                "frame": self._image_upload("frame.jpg"),
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["action"], "check_out") # type: ignore
        updated = EmployeeAttendanceApp.objects.get(pk=record.pk)
        self.assertEqual(updated.checkin_warehouse, "WH1")
        self.assertEqual(updated.checkout_warehouse, "WH2")
        self.assertEqual(updated.matched_warehouse, "WH2")

    def test_attendance_records_support_date_range_filter(self):
        yesterday = views.timezone.localdate() - timedelta(days=1)
        EmployeeAttendanceApp.objects.create(
            employee=self.employee,
            date=yesterday,
            timein=views.timezone.now() - timedelta(days=1),
            checkin_warehouse="WH1",
            matched_warehouse="WH1",
        )
        EmployeeAttendanceApp.objects.create(
            employee=self.employee,
            date=views.timezone.localdate(),
            timein=views.timezone.now(),
            checkin_warehouse="WH1",
            matched_warehouse="WH1",
        )

        response = self.admin_client.get(
            f"/api/attendance/records?start_date={yesterday}&end_date={views.timezone.localdate()}"
        )

        self.assertEqual(response.status_code, 200) # type: ignore
        self.assertEqual(len(response.data), 2) # type: ignore

    def test_employee_list_requires_admin(self):
        response = self.client.get("/api/employees")
        self.assertEqual(response.status_code, 401)

    def test_admin_can_update_warehouse_location_and_sync_employee_copy(self):
        response = self.admin_client.put(
            f"/api/warehouses/{self.warehouse.pk}",
            data={"latlong": "31.6000,74.4000"},
        )

        self.assertEqual(response.status_code, 200) # type: ignore
        self.warehouse.refresh_from_db()
        self.employee.refresh_from_db()
        self.assertEqual(self.warehouse.latlong, "31.6000,74.4000")
        self.assertEqual(self.employee.latlong, "31.6000,74.4000")

    def test_warehouse_location_update_requires_admin(self):
        response = self.client.put(
            f"/api/warehouses/{self.warehouse.pk}",
            data={"latlong": "31.6000,74.4000"},
        )

        self.assertEqual(response.status_code, 401) # type: ignore

    def test_attempt_review_api_returns_summary(self):
        AttendanceVerificationAttempt.objects.create(
            employee=self.employee,
            submitted_employeecode="EMP001",
            matched_warehouse="WH1",
            deviceinfo="android | samsung | A55",
            verification_decision="below_threshold",
            verification_method="employee_code_face",
            attempt_status="rejected",
            failure_reason="face_mismatch",
        )
        AttendanceVerificationAttempt.objects.create(
            employee=self.employee,
            submitted_employeecode="EMP001",
            matched_warehouse="WH1",
            verification_decision="accepted",
            verification_method="employee_code_face",
            attempt_status="accepted_check_in",
        )

        response = self.admin_client.get(f"/api/attendance/attempts?warehouse=WH1&start_date={views.timezone.localdate()}&end_date={views.timezone.localdate()}")

        self.assertEqual(response.status_code, 200) # type: ignore
        self.assertEqual(response.data["summary"]["rejected"], 1) # type: ignore
        self.assertEqual(response.data["failure_summary"]["face_mismatch"], 1) # type: ignore
        self.assertEqual(len(response.data["results"]), 2) # type: ignore
        self.assertEqual(response.data["results"][1]["deviceinfo"], "android | samsung | A55") # type: ignore

    def test_admin_can_correct_missing_checkout(self):
        record = EmployeeAttendanceApp.objects.create(
            employee=self.employee,
            date=views.timezone.localdate(),
            timein=views.timezone.now() - timedelta(hours=8),
            deviceinfo="device-1",
            assignedlocation="31.5000,74.3000",
            checkinlocation="31.5000,74.3000",
            submitted_employeecode="EMP001",
            verification_decision="accepted",
            verification_method="employee_code_face",
            matched_warehouse="WH1",
        )
        fulltimeout = (views.timezone.now() - timedelta(hours=1)).isoformat()

        response = self.admin_client.post(
            f"/api/attendance/records/{record.pk}/correct-checkout",
            data={
                "fulltimeout": fulltimeout,
                "correction_reason": "Forgot to punch out before leaving shift.",
                "checkoutlocation": "31.5001,74.3001",
            },
        )

        self.assertEqual(response.status_code, 200) # type: ignore
        record.refresh_from_db()
        self.assertIsNotNone(record.fulltimeout)
        self.assertEqual(record.correction_action, "manual_check_out")
        self.assertEqual(record.correction_reason, "Forgot to punch out before leaving shift.")
        self.assertEqual(record.corrected_by, self.admin_user)
        self.assertEqual(record.verification_decision, "manual_correction")
        self.assertEqual(record.verification_method, "admin_manual_correction")
        attempt = AttendanceVerificationAttempt.objects.get(attempt_status="manual_correction")
        self.assertEqual(attempt.employee, self.employee)
