import logging
import os
from datetime import datetime, time
from math import atan2, cos, radians, sin, sqrt
from collections import Counter

from django.db import IntegrityError, transaction
from django.db.models import Count
from django.utils.dateparse import parse_datetime
from django.utils.dateparse import parse_date
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .face_utils import extract_embedding_from_file, find_matching_employee
from .models import AttendanceVerificationAttempt, EmployeeAttendanceApp, EmployeeFaceEmbedding, RegisteredEmployee, Warehousedetail
from .serializers import RegisteredEmployeeSerializer, WarehouseSerializer


logger = logging.getLogger(__name__)
MIN_FACE_ENROLLMENT_SAMPLES = int(os.environ.get("MIN_FACE_ENROLLMENT_SAMPLES", "3"))


def _clean_text(value) -> str:
    return str(value).strip() if value is not None else ""


def _clean_limited_text(value, max_length: int) -> str:
    return _clean_text(value)[:max_length]


def _parse_latlong(value: str) -> tuple[float, float]:
    parts = value.split(",")
    if len(parts) != 2:
        raise ValueError("latlong must contain exactly two comma-separated values")
    return float(parts[0].strip()), float(parts[1].strip())


def distance_in_meters(latlong1: str, latlong2: str) -> float:
    lat1, lng1 = _parse_latlong(latlong1)
    lat2, lng2 = _parse_latlong(latlong2)
    radius_meters = 6_371_000
    d_lat = radians(lat2 - lat1)
    d_lng = radians(lng2 - lng1)
    a = sin(d_lat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(d_lng / 2) ** 2
    return radius_meters * 2 * atan2(sqrt(a), sqrt(1 - a))


def _find_active_warehouse_for_location(current_loc: str):
    """
    Return the first active warehouse whose configured coordinates contain the
    submitted location. Warehouses with "0,0" are ignored because they are not
    real geofence anchors.
    """
    for warehouse in Warehousedetail.objects.filter(status=1).exclude(latlong="0,0"):
        try:
            if distance_in_meters(warehouse.latlong, current_loc) <= 35:
                return warehouse
        except ValueError:
            logger.warning("Invalid warehouse latlong: warehouse=%s latlong=%s", warehouse.pk, warehouse.latlong)
    return None


def _safe_match_payload(match_result) -> dict:
    return {
        "confidence": match_result.confidence,
        "match_margin": match_result.margin,
        "decision": match_result.decision,
    }


def _log_verification_attempt(
    *,
    employee,
    employee_code,
    attendance_code,
    current_loc,
    warehouse_name,
    device_info,
    match_result,
    attempt_status,
    failure_reason=None,
):
    AttendanceVerificationAttempt.objects.create(
        employee=employee,
        submitted_employeecode=employee_code or None,
        submitted_attendancecode=attendance_code or None,
        currentlocation=current_loc or None,
        matched_warehouse=warehouse_name,
        deviceinfo=device_info or None,
        verification_confidence=(match_result.confidence if match_result else None),
        verification_margin=(match_result.margin if match_result else None),
        verification_decision=(match_result.decision if match_result else "not_started"),
        verification_method="employee_code_face",
        attempt_status=attempt_status,
        failure_reason=failure_reason,
    )


def _extract_enrollment_embeddings(files):
    """
    Enrollment is now multi-sample. Each uploaded image must produce a reliable
    embedding before we allow the employee to be saved.
    """
    embeddings = []
    for index, image_file in enumerate(files, start=1):
        embedding = extract_embedding_from_file(image_file)
        if embedding is None:
            raise ValueError(f"Enrollment photo {index} did not contain a reliable face.")
        embeddings.append(embedding)
    return embeddings


def _get_uploaded_face_photos(request):
    """
    Accept both the new multi-photo field (`photos`) and the older single-photo
    field (`photo`). This lets old mobile/web clients fail with a clear minimum
    sample message instead of silently creating a weak one-image enrollment.
    """
    photos = request.FILES.getlist("photos")
    if not photos:
        single_photo = request.FILES.get("photo")
        if single_photo:
            photos = [single_photo]
    return photos


def _extract_angle_labels(request, sample_count: int) -> list[str | None]:
    """
    Accept repeated `angle_labels[]`/`angle_labels` or a comma-separated
    `angle_labels_csv` value. The mobile app can start simple and grow later.
    """
    angle_labels = []
    data_getlist = getattr(request.data, "getlist", None)
    if callable(data_getlist):
        angle_labels = data_getlist("angle_labels[]") or data_getlist("angle_labels")

    if not angle_labels:
        csv_value = _clean_text(request.data.get("angle_labels_csv"))
        if csv_value:
            angle_labels = [item.strip() for item in csv_value.split(",") if item.strip()]

    normalized = [_clean_text(label) or None for label in angle_labels[:sample_count]] # type: ignore
    if len(normalized) < sample_count:
        normalized.extend([None] * (sample_count - len(normalized)))
    return normalized


def _store_face_samples(
    employee,
    embeddings,
    angle_labels=None,
    capture_session_id=None,
    label_prefix="sample",
    capture_source="mobile_enrollment",
):
    """
    Persist every enrollment sample in EmployeeFaceEmbedding. The sample table
    is the runtime source of truth for verification.
    """
    employee.face_embeddings.all().delete()
    angle_labels = angle_labels or [None] * len(embeddings)
    for index, embedding in enumerate(embeddings, start=1):
        sample = EmployeeFaceEmbedding(
            employee=employee,
            sample_label=f"{label_prefix}_{index}",
            sample_order=index,
            angle_label=angle_labels[index - 1] if index - 1 < len(angle_labels) else None,
            capture_source=capture_source,
            capture_session_id=capture_session_id or None,
            quality_score=1.0,
            is_primary=index == 1,
        )
        sample.set_embedding(embedding)
        sample.save()


class WarehouseListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        warehouses = Warehousedetail.objects.filter(status=1).order_by("short_name")
        serializer = WarehouseSerializer(warehouses, many=True)
        return Response(serializer.data)


class WarehouseUpdateView(APIView):
    permission_classes = [IsAdminUser]

    def put(self, request, pk):
        try:
            warehouse = Warehousedetail.objects.get(pk=pk)
        except Warehousedetail.DoesNotExist:
            return Response({"status": "error", "message": "Warehouse not found."}, status=404)

        latlong = _clean_text(request.data.get("latlong"))
        if not latlong:
            return Response({"status": "error", "message": "latlong is required."}, status=400)

        try:
            latitude, longitude = _parse_latlong(latlong)
        except ValueError:
            return Response(
                {"status": "error", "message": 'latlong must be in "lat,lng" format.'},
                status=400,
            )
        if abs(latitude) > 90 or abs(longitude) > 180:
            return Response(
                {"status": "error", "message": "latlong coordinates are out of range."},
                status=400,
            )

        # RegisteredEmployee still carries a legacy copy of the warehouse
        # coordinates. Keep it synchronized until that duplicated field is
        # removed in a later schema cleanup.
        with transaction.atomic():
            warehouse.latlong = latlong
            warehouse.save(update_fields=["latlong"])
            RegisteredEmployee.objects.filter(warehouseid=warehouse.short_name).update(latlong=latlong)

        return Response(
            {
                "status": "success",
                "message": "Warehouse location updated successfully.",
                "data": WarehouseSerializer(warehouse).data,
            }
        )


class EmployeeView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        employees = (
            RegisteredEmployee.objects
            .filter(is_active=True)
            .prefetch_related("face_embeddings")
            .order_by("name")
        )
        serializer = RegisteredEmployeeSerializer(employees, many=True)
        return Response(serializer.data)

    def post(self, request):
        required_fields = ["name", "fathername", "employeecode", "attendancecode", "warehouseid"]
        cleaned = {field: _clean_text(request.data.get(field)) for field in required_fields}

        for field, value in cleaned.items():
            if not value:
                return Response(
                    {"status": "error", "message": f"{field} is required."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        photos = _get_uploaded_face_photos(request)

        if not photos:
            return Response(
                {"status": "error", "message": "At least one enrollment photo is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(photos) < MIN_FACE_ENROLLMENT_SAMPLES:
            return Response(
                {
                    "status": "error",
                    "message": f"At least {MIN_FACE_ENROLLMENT_SAMPLES} enrollment photos are required.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if RegisteredEmployee.objects.filter(employeecode=cleaned["employeecode"]).exists():
            return Response(
                {"status": "error", "message": "employeecode already exists."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if RegisteredEmployee.objects.filter(attendancecode=cleaned["attendancecode"]).exists():
            return Response(
                {"status": "error", "message": "attendancecode already exists."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            embeddings = _extract_enrollment_embeddings(photos)
        except ValueError as exc:
            return Response({"status": "error", "message": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        angle_labels = _extract_angle_labels(request, len(embeddings))
        capture_session_id = _clean_limited_text(request.data.get("capture_session_id"), 64) or None

        existing_employees = (
            RegisteredEmployee.objects
            .filter(is_active=True)
            .prefetch_related("face_embeddings")
        )
        for embedding in embeddings:
            match_result = find_matching_employee(embedding, existing_employees)
            if match_result.is_match and match_result.employee is not None:
                matched_employee = match_result.employee
                return Response(
                    {
                        "status": "error",
                        "message": (
                            f'This face is already registered as "{matched_employee.name}" ' # type: ignore
                            f"(Code: {matched_employee.employeecode})." # type: ignore
                        ),
                        **_safe_match_payload(match_result),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if match_result.is_ambiguous:
                return Response(
                    {
                        "status": "ambiguous_face",
                        "message": "This face is too similar to an existing employee. Retake clearer enrollment photos or send to HR review.",
                        **_safe_match_payload(match_result),
                    },
                    status=status.HTTP_409_CONFLICT,
                )

        employee = RegisteredEmployee(
            name=cleaned["name"],
            fathername=cleaned["fathername"],
            employeecode=cleaned["employeecode"],
            attendancecode=cleaned["attendancecode"],
            warehouseid=cleaned["warehouseid"],
            latlong=_clean_text(request.data.get("latlong")) or None,
        )
        employee.save()
        _store_face_samples(
            employee,
            embeddings,
            angle_labels=angle_labels,
            capture_session_id=capture_session_id,
            label_prefix="enrollment",
        )

        serializer = RegisteredEmployeeSerializer(employee)
        return Response(
            {
                "status": "success",
                "message": f"Employee '{employee.name}' registered successfully.",
                "data": serializer.data,
            },
            status=status.HTTP_201_CREATED,
        )


class EmployeeUpdateView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request, pk):
        try:
            employee = RegisteredEmployee.objects.get(pk=pk)
        except RegisteredEmployee.DoesNotExist:
            return Response({"status": "error", "message": "Employee not found."}, status=404)

        return Response(RegisteredEmployeeSerializer(employee).data)

    def put(self, request, pk):
        try:
            employee = RegisteredEmployee.objects.get(pk=pk)
        except RegisteredEmployee.DoesNotExist:
            return Response({"status": "error", "message": "Employee not found."}, status=404)

        with transaction.atomic():
            new_emp_code = _clean_text(request.data.get("employeecode"))
            if new_emp_code and new_emp_code != employee.employeecode:
                if RegisteredEmployee.objects.filter(employeecode=new_emp_code).exclude(pk=pk).exists():
                    return Response({"status": "error", "message": "employeecode already exists."}, status=400)
                employee.employeecode = new_emp_code

            new_att_code = _clean_text(request.data.get("attendancecode"))
            if new_att_code and new_att_code != employee.attendancecode:
                if employee.attendance_records.exists(): # type: ignore
                    return Response(
                        {
                            "status": "error",
                            "message": "attendancecode cannot be changed after attendance history exists.",
                        },
                        status=400,
                    )
                if RegisteredEmployee.objects.filter(attendancecode=new_att_code).exclude(pk=pk).exists():
                    return Response({"status": "error", "message": "attendancecode already exists."}, status=400)
                employee.attendancecode = new_att_code

            for field in ["name", "fathername", "warehouseid", "latlong"]:
                value = request.data.get(field)
                if value is not None:
                    setattr(employee, field, _clean_text(value) or None)

            photos = _get_uploaded_face_photos(request)

            if photos:
                if len(photos) < MIN_FACE_ENROLLMENT_SAMPLES:
                    return Response(
                        {
                            "status": "error",
                            "message": f"At least {MIN_FACE_ENROLLMENT_SAMPLES} enrollment photos are required.",
                        },
                        status=400,
                    )
                try:
                    embeddings = _extract_enrollment_embeddings(photos)
                except ValueError as exc:
                    return Response({"status": "error", "message": str(exc)}, status=400)
                angle_labels = _extract_angle_labels(request, len(embeddings))
                capture_session_id = _clean_limited_text(request.data.get("capture_session_id"), 64) or None

                comparison_employees = (
                    RegisteredEmployee.objects
                    .filter(is_active=True)
                    .exclude(pk=pk)
                    .prefetch_related("face_embeddings")
                )
                for embedding in embeddings:
                    match_result = find_matching_employee(embedding, comparison_employees)
                    if match_result.is_match or match_result.is_ambiguous:
                        return Response(
                            {
                                "status": "ambiguous_face" if match_result.is_ambiguous else "error",
                                "message": "Face is already registered or too similar to another employee.",
                                **_safe_match_payload(match_result),
                            },
                            status=409 if match_result.is_ambiguous else 400,
                        )
                employee.save()
                _store_face_samples(
                    employee,
                    embeddings,
                    angle_labels=angle_labels,
                    capture_session_id=capture_session_id,
                    label_prefix="profile_update",
                )

            employee.save()

        return Response({"status": "success", "data": RegisteredEmployeeSerializer(employee).data})

    def delete(self, request, pk):
        try:
            employee = RegisteredEmployee.objects.get(pk=pk)
        except RegisteredEmployee.DoesNotExist:
            return Response({"status": "error", "message": "Employee not found."}, status=404)

        employee.is_active = False
        employee.save(update_fields=["is_active"])
        return Response({"status": "success", "message": "Employee deactivated."})


class EmployeeFaceSampleUpdateView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, pk):
        """
        Re-enroll face samples for an existing employee without creating a new
        employee row. This is the production upgrade path for old employees who
        only have one legacy embedding.
        """
        try:
            employee = RegisteredEmployee.objects.prefetch_related("face_embeddings").get(pk=pk, is_active=True)
        except RegisteredEmployee.DoesNotExist:
            return Response({"status": "error", "message": "Active employee not found."}, status=404)

        photos = _get_uploaded_face_photos(request)
        if not photos:
            return Response(
                {"status": "error", "message": "At least one enrollment photo is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(photos) < MIN_FACE_ENROLLMENT_SAMPLES:
            return Response(
                {
                    "status": "error",
                    "message": f"At least {MIN_FACE_ENROLLMENT_SAMPLES} enrollment photos are required.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            embeddings = _extract_enrollment_embeddings(photos)
        except ValueError as exc:
            return Response({"status": "error", "message": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        # Never compare the employee's new samples against their own old samples.
        # We only use other active employees to detect duplicate or resembling faces.
        comparison_employees = (
            RegisteredEmployee.objects
            .filter(is_active=True)
            .exclude(pk=employee.pk)
            .prefetch_related("face_embeddings")
        )
        for embedding in embeddings:
            match_result = find_matching_employee(embedding, comparison_employees)
            if match_result.is_match and match_result.employee is not None:
                matched_employee = match_result.employee
                return Response(
                    {
                        "status": "error",
                        "message": (
                            f'This face is already registered as "{matched_employee.name}" ' # type: ignore
                            f"(Code: {matched_employee.employeecode})." # type: ignore
                        ),
                        **_safe_match_payload(match_result),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if match_result.is_ambiguous:
                return Response(
                    {
                        "status": "ambiguous_face",
                        "message": "This face is too similar to another employee. Retake clearer photos or send to HR review.",
                        **_safe_match_payload(match_result),
                    },
                    status=status.HTTP_409_CONFLICT,
                )

        angle_labels = _extract_angle_labels(request, len(embeddings))
        capture_session_id = _clean_limited_text(request.data.get("capture_session_id"), 64) or None
        capture_source = _clean_limited_text(request.data.get("capture_source"), 30) or "face_reenrollment"

        with transaction.atomic():
            previous_sample_count = employee.face_embeddings.count() # type: ignore
            _store_face_samples(
                employee,
                embeddings,
                angle_labels=angle_labels,
                capture_session_id=capture_session_id,
                label_prefix="reenrollment",
                capture_source=capture_source,
            )

        employee.refresh_from_db()
        return Response(
            {
                "status": "success",
                "message": "Employee face samples updated successfully.",
                "data": {
                    **RegisteredEmployeeSerializer(employee).data, # type: ignore
                    "previous_face_sample_count": previous_sample_count,
                    "capture_session_id": capture_session_id,
                },
            }
        )


class AttendancePrecheckView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "attendance_mark"

    def post(self, request):
        current_loc = _clean_text(request.data.get("currentlocation"))
        employee_code = _clean_text(request.data.get("employeecode"))
        attendance_code = _clean_text(request.data.get("attendancecode"))

        if not current_loc:
            return Response(
                {"status": "error", "allowed": False, "message": "currentlocation is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not employee_code and not attendance_code:
            return Response(
                {"status": "error", "allowed": False, "message": "employeecode or attendancecode is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            _parse_latlong(current_loc)
        except ValueError:
            return Response(
                {"status": "error", "allowed": False, "message": 'currentlocation must be in "lat,lng" format.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        active_warehouse = _find_active_warehouse_for_location(current_loc)
        if not active_warehouse:
            return Response(
                {"status": "error", "allowed": False, "message": "You are not within any warehouse premises."},
                status=status.HTTP_403_FORBIDDEN,
            )

        employee_filter = {"is_active": True}
        if attendance_code:
            employee_filter["attendancecode"] = attendance_code # type: ignore
        else:
            employee_filter["employeecode"] = employee_code # type: ignore

        try:
            employee = RegisteredEmployee.objects.get(**employee_filter)
        except RegisteredEmployee.DoesNotExist:
            return Response(
                {"status": "error", "allowed": False, "message": "Employee code not recognized."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response({
            "status": "success",
            "allowed": True,
            "message": "Code accepted. Face verification can start.",
            "warehouse": {
                "short_name": active_warehouse.short_name,
                "full_name": active_warehouse.full_name,
            },
            "employee": {
                "name": employee.name,
                "employeecode": employee.employeecode,
                "attendancecode": employee.attendancecode,
                "assigned_warehouseid": employee.warehouseid,
            },
        })


class MarkAttendanceView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "attendance_mark"

    def post(self, request):
        frame = request.FILES.get("frame")
        current_loc = _clean_text(request.data.get("currentlocation"))
        device_info = _clean_text(request.data.get("deviceinfo"))
        employee_code = _clean_text(request.data.get("employeecode"))
        attendance_code = _clean_text(request.data.get("attendancecode"))

        if not frame:
            return Response(
                {"status": "error", "message": "frame image is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not current_loc:
            return Response(
                {"status": "error", "message": "currentlocation is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not employee_code and not attendance_code:
            return Response(
                {"status": "error", "message": "employeecode or attendancecode is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            _parse_latlong(current_loc)
        except ValueError:
            return Response(
                {"status": "error", "message": 'currentlocation must be in "lat,lng" format.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        active_warehouse = _find_active_warehouse_for_location(current_loc)
        if not active_warehouse:
            _log_verification_attempt(
                employee=None,
                employee_code=employee_code,
                attendance_code=attendance_code,
                current_loc=current_loc,
                warehouse_name=None,
                device_info=device_info,
                match_result=None,
                attempt_status="rejected",
                failure_reason="outside_warehouse",
            )
            return Response(
                {"status": "error", "message": "You are not within any warehouse premises."},
                status=status.HTTP_403_FORBIDDEN,
            )

        employee_filter = {"is_active": True}
        if attendance_code:
            employee_filter["attendancecode"] = attendance_code # type: ignore
        else:
            employee_filter["employeecode"] = employee_code # type: ignore

        try:
            claimed_employee = RegisteredEmployee.objects.prefetch_related("face_embeddings").get(**employee_filter)
        except RegisteredEmployee.DoesNotExist:
            _log_verification_attempt(
                employee=None,
                employee_code=employee_code,
                attendance_code=attendance_code,
                current_loc=current_loc,
                warehouse_name=active_warehouse.short_name,
                device_info=device_info,
                match_result=None,
                attempt_status="rejected",
                failure_reason="employee_code_not_found",
            )
            return Response(
                {"status": "error", "message": "Employee code not recognized."},
                status=status.HTTP_404_NOT_FOUND,
            )

        embedding = extract_embedding_from_file(frame)
        if embedding is None:
            _log_verification_attempt(
                employee=claimed_employee,
                employee_code=employee_code,
                attendance_code=attendance_code,
                current_loc=current_loc,
                warehouse_name=active_warehouse.short_name,
                device_info=device_info,
                match_result=None,
                attempt_status="rejected",
                failure_reason="no_reliable_face",
            )
            return Response(
                {"status": "error", "message": "No reliable face detected. Please try again."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        match_result = find_matching_employee(embedding, [claimed_employee])
        matched_employee = match_result.employee

        logger.info(
            "FACE VERIFICATION RESULT: claimed=%s match=%s confidence=%s%% decision=%s",
            claimed_employee.pk,
            matched_employee.pk if matched_employee else "none", # type: ignore
            match_result.confidence,
            match_result.decision,
        )

        if match_result.is_ambiguous:
            _log_verification_attempt(
                employee=claimed_employee,
                employee_code=employee_code,
                attendance_code=attendance_code,
                current_loc=current_loc,
                warehouse_name=active_warehouse.short_name,
                device_info=device_info,
                match_result=match_result,
                attempt_status="ambiguous",
                failure_reason="ambiguous_face_match",
            )
            return Response(
                {
                    "status": "ambiguous_face",
                    "message": "Face match is not confident. Please retry with a clear face or contact HR.",
                    **_safe_match_payload(match_result),
                },
                status=status.HTTP_409_CONFLICT,
            )

        if not match_result.is_match or matched_employee is None:
            _log_verification_attempt(
                employee=claimed_employee,
                employee_code=employee_code,
                attendance_code=attendance_code,
                current_loc=current_loc,
                warehouse_name=active_warehouse.short_name,
                device_info=device_info,
                match_result=match_result,
                attempt_status="rejected",
                failure_reason="face_mismatch",
            )
            return Response(
                {
                    "status": "error",
                    "message": "Face does not match the submitted employee code.",
                    **_safe_match_payload(match_result),
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        now = timezone.now()
        last_log = (
            EmployeeAttendanceApp.objects
            .filter(employee=matched_employee)
            .order_by("-created_at")
            .first()
        )
        if last_log and (now - last_log.created_at).total_seconds() < 30:
            _log_verification_attempt(
                employee=matched_employee,
                employee_code=employee_code,
                attendance_code=attendance_code,
                current_loc=current_loc,
                warehouse_name=active_warehouse.short_name,
                device_info=device_info,
                match_result=match_result,
                attempt_status="cooldown",
                failure_reason="cooldown_window",
            )
            return Response({
                "status": "cooldown",
                "message": f"{matched_employee.name} was just recognized. Please wait.", # type: ignore
                "employee": matched_employee.name, # type: ignore
            })

        today = timezone.localdate(now)
        verification_payload = {
            "submitted_employeecode": employee_code or None,
            "submitted_attendancecode": attendance_code or None,
            "verification_confidence": match_result.confidence,
            "verification_margin": match_result.margin,
            "verification_decision": match_result.decision,
            "verification_method": "employee_code_face",
            "matched_warehouse": active_warehouse.short_name,
        }

        try:
            with transaction.atomic():
                record = (
                    EmployeeAttendanceApp.objects
                    .select_for_update()
                    .filter(employee=matched_employee, date=today)
                    .first()
                )

                if record is None:
                    EmployeeAttendanceApp.objects.create(
                        employee=matched_employee,
                        date=today,
                        timein=now,
                        deviceinfo=device_info,
                        assignedlocation=matched_employee.latlong, # type: ignore
                        checkinlocation=current_loc,
                        checkin_warehouse=active_warehouse.short_name,
                        **verification_payload,
                    )
                    _log_verification_attempt(
                        employee=matched_employee,
                        employee_code=employee_code,
                        attendance_code=attendance_code,
                        current_loc=current_loc,
                        warehouse_name=active_warehouse.short_name,
                        device_info=device_info,
                        match_result=match_result,
                        attempt_status="accepted_check_in",
                    )
                    logger.info("CHECK IN: %s", matched_employee.name) # type: ignore
                    return Response({
                        "status": "success",
                        "action": "check_in",
                        "message": f"Welcome, {matched_employee.name}!", # type: ignore
                        "employee": matched_employee.name, # type: ignore
                        "warehouseid": active_warehouse.short_name,
                        "assigned_warehouseid": matched_employee.warehouseid, # type: ignore
                        "time": timezone.localtime(now).strftime("%I:%M %p"),
                        "confidence": match_result.confidence,
                    })

                if record.fulltimeout is None:
                    record.fulltimeout = now
                    record.checkoutlocation = current_loc
                    record.checkout_warehouse = active_warehouse.short_name
                    for field, value in verification_payload.items():
                        setattr(record, field, value)
                    record.save(update_fields=[
                        "fulltimeout",
                        "checkoutlocation",
                        "checkout_warehouse",
                        "submitted_employeecode",
                        "submitted_attendancecode",
                        "verification_confidence",
                        "verification_margin",
                        "verification_decision",
                        "verification_method",
                        "matched_warehouse",
                        "updated_at",
                    ])

                    duration = record.get_duration() or "-"
                    _log_verification_attempt(
                        employee=matched_employee,
                        employee_code=employee_code,
                        attendance_code=attendance_code,
                        current_loc=current_loc,
                        warehouse_name=active_warehouse.short_name,
                        device_info=device_info,
                        match_result=match_result,
                        attempt_status="accepted_check_out",
                    )
                    logger.info("CHECK OUT: %s duration=%s", matched_employee.name, duration) # type: ignore
                    return Response({
                        "status": "success",
                        "action": "check_out",
                        "message": f"Goodbye, {matched_employee.name}!", # type: ignore
                        "employee": matched_employee.name, # type: ignore
                        "warehouseid": active_warehouse.short_name,
                        "assigned_warehouseid": matched_employee.warehouseid, # type: ignore
                        "time": timezone.localtime(now).strftime("%I:%M %p"),
                        "hours_worked": duration,
                        "confidence": match_result.confidence,
                    })

                _log_verification_attempt(
                    employee=matched_employee,
                    employee_code=employee_code,
                    attendance_code=attendance_code,
                    current_loc=current_loc,
                    warehouse_name=active_warehouse.short_name,
                    device_info=device_info,
                    match_result=match_result,
                    attempt_status="already_done",
                    failure_reason="attendance_already_completed",
                )
                return Response({
                    "status": "already_done",
                    "message": f"{matched_employee.name} has already completed attendance for today.", # type: ignore
                    "check_in": timezone.localtime(record.timein).strftime("%I:%M %p") if record.timein else None,
                    "check_out": timezone.localtime(record.fulltimeout).strftime("%I:%M %p") if record.fulltimeout else None,
                })
        except IntegrityError:
            _log_verification_attempt(
                employee=matched_employee,
                employee_code=employee_code,
                attendance_code=attendance_code,
                current_loc=current_loc,
                warehouse_name=active_warehouse.short_name,
                device_info=device_info,
                match_result=match_result,
                attempt_status="error",
                failure_reason="attendance_conflict",
            )
            return Response(
                {"status": "error", "message": "Attendance request conflicted. Please retry."},
                status=status.HTTP_409_CONFLICT,
            )


class AttendanceRecordsView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        today = timezone.localdate()
        date_filter = request.query_params.get("date", str(today))
        start_date = _clean_text(request.query_params.get("start_date"))
        end_date = _clean_text(request.query_params.get("end_date"))
        code_filter = request.query_params.get("attendancecode")

        qs = (
            EmployeeAttendanceApp.objects
            .select_related("employee")
            .order_by("-timein")
        )
        if start_date or end_date:
            if start_date:
                qs = qs.filter(date__gte=start_date)
            if end_date:
                qs = qs.filter(date__lte=end_date)
        else:
            qs = qs.filter(date=date_filter)
        if code_filter:
            qs = qs.filter(employee__attendancecode=code_filter)

        data = [
            {
                "id": r.pk,
                "attendancecode": r.employee.attendancecode,
                "employeecode": r.employee.employeecode,
                "employee_name": r.employee.name,
                "date": str(r.date),
                "timein": timezone.localtime(r.timein).strftime("%I:%M %p") if r.timein else None,
                "fulltimeout": timezone.localtime(r.fulltimeout).strftime("%I:%M %p") if r.fulltimeout else None,
                "duration": r.get_duration(),
                "submitted_employeecode": r.submitted_employeecode,
                "submitted_attendancecode": r.submitted_attendancecode,
                "verification_confidence": r.verification_confidence,
                "verification_margin": r.verification_margin,
                "verification_decision": r.verification_decision,
                "verification_method": r.verification_method,
                "matched_warehouse": r.matched_warehouse,
                "checkin_warehouse": r.checkin_warehouse,
                "checkout_warehouse": r.checkout_warehouse,
                "correction_action": r.correction_action,
                "correction_reason": r.correction_reason,
                "corrected_at": timezone.localtime(r.corrected_at).isoformat() if r.corrected_at else None,
                "corrected_by": r.corrected_by.username if r.corrected_by else None,
                "checkinlocation": r.checkinlocation,
                "checkoutlocation": r.checkoutlocation,
            }
            for r in qs
        ]
        return Response(data)


class AttendanceAttemptReviewView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        start_date = _clean_text(request.query_params.get("start_date"))
        end_date = _clean_text(request.query_params.get("end_date"))
        attempt_status = _clean_text(request.query_params.get("status"))
        warehouse = _clean_text(request.query_params.get("warehouse"))
        employee_code = _clean_text(request.query_params.get("employeecode"))
        attendance_code = _clean_text(request.query_params.get("attendancecode"))

        qs = AttendanceVerificationAttempt.objects.select_related("employee")
        if start_date:
            parsed_start = parse_date(start_date)
            if parsed_start:
                start_dt = timezone.make_aware(
                    datetime.combine(parsed_start, time.min),
                    timezone.get_current_timezone(),
                )
                qs = qs.filter(created_at__gte=start_dt)
        if end_date:
            parsed_end = parse_date(end_date)
            if parsed_end:
                end_dt = timezone.make_aware(
                    datetime.combine(parsed_end, time.max),
                    timezone.get_current_timezone(),
                )
                qs = qs.filter(created_at__lte=end_dt)
        if attempt_status:
            qs = qs.filter(attempt_status=attempt_status)
        if warehouse:
            qs = qs.filter(matched_warehouse=warehouse)
        if employee_code:
            qs = qs.filter(submitted_employeecode=employee_code)
        if attendance_code:
            qs = qs.filter(submitted_attendancecode=attendance_code)

        attempts = list(qs.order_by("-created_at")[:200])
        summary = dict(Counter(attempt.attempt_status for attempt in attempts))
        failure_summary = dict(
            Counter(
                (attempt.failure_reason or "none")
                for attempt in attempts
                if attempt.attempt_status in {"rejected", "ambiguous", "error", "cooldown"}
            )
        )

        data = [
            {
                "id": attempt.pk,
                "created_at": timezone.localtime(attempt.created_at).isoformat(),
                "attempt_status": attempt.attempt_status,
                "failure_reason": attempt.failure_reason,
                "submitted_employeecode": attempt.submitted_employeecode,
                "submitted_attendancecode": attempt.submitted_attendancecode,
                "matched_employee_id": attempt.employee_id, # type: ignore
                "matched_employee_name": attempt.employee.name if attempt.employee else None,
                "matched_warehouse": attempt.matched_warehouse,
                "currentlocation": attempt.currentlocation,
                "deviceinfo": attempt.deviceinfo,
                "verification_confidence": attempt.verification_confidence,
                "verification_margin": attempt.verification_margin,
                "verification_decision": attempt.verification_decision,
                "verification_method": attempt.verification_method,
            }
            for attempt in attempts
        ]
        return Response({
            "summary": summary,
            "failure_summary": failure_summary,
            "results": data,
        })


class AttendanceCorrectionView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, pk):
        try:
            record = EmployeeAttendanceApp.objects.select_related("employee").get(pk=pk)
        except EmployeeAttendanceApp.DoesNotExist:
            return Response({"status": "error", "message": "Attendance record not found."}, status=404)

        if record.timein is None:
            return Response(
                {"status": "error", "message": "Attendance record has no check-in time to correct."},
                status=400,
            )
        if record.fulltimeout is not None:
            return Response(
                {"status": "error", "message": "Attendance record already has a check-out time."},
                status=400,
            )

        correction_reason = _clean_text(request.data.get("correction_reason"))
        if not correction_reason:
            return Response(
                {"status": "error", "message": "correction_reason is required."},
                status=400,
            )

        fulltimeout_raw = _clean_text(request.data.get("fulltimeout"))
        corrected_timeout = parse_datetime(fulltimeout_raw)
        if corrected_timeout is None:
            return Response(
                {"status": "error", "message": "fulltimeout must be a valid ISO datetime."},
                status=400,
            )
        if timezone.is_naive(corrected_timeout):
            corrected_timeout = timezone.make_aware(corrected_timeout, timezone.get_current_timezone())

        corrected_timeout = timezone.localtime(corrected_timeout)
        timein = timezone.localtime(record.timein)
        if corrected_timeout <= timein:
            return Response(
                {"status": "error", "message": "fulltimeout must be after timein."},
                status=400,
            )

        record.fulltimeout = corrected_timeout
        record.checkoutlocation = _clean_text(request.data.get("checkoutlocation")) or record.checkoutlocation
        record.correction_reason = correction_reason
        record.correction_action = "manual_check_out"
        record.corrected_at = timezone.now()
        record.corrected_by = request.user
        record.verification_decision = "manual_correction"
        record.verification_method = "admin_manual_correction"
        record.save(update_fields=[
            "fulltimeout",
            "checkoutlocation",
            "correction_reason",
            "correction_action",
            "corrected_at",
            "corrected_by",
            "verification_decision",
            "verification_method",
            "updated_at",
        ])

        AttendanceVerificationAttempt.objects.create(
            employee=record.employee,
            submitted_employeecode=record.submitted_employeecode,
            submitted_attendancecode=record.submitted_attendancecode,
            currentlocation=record.checkoutlocation,
            matched_warehouse=record.matched_warehouse or record.employee.warehouseid,
            verification_decision="manual_correction",
            verification_method="admin_manual_correction",
            attempt_status="manual_correction",
            failure_reason=correction_reason,
        )

        return Response({
            "status": "success",
            "message": "Attendance record corrected successfully.",
            "data": {
                "id": record.pk,
                "employee_name": record.employee.name,
                "date": str(record.date),
                "timein": timezone.localtime(record.timein).isoformat() if record.timein else None,
                "fulltimeout": timezone.localtime(record.fulltimeout).isoformat() if record.fulltimeout else None,
                "duration": record.get_duration(),
                "correction_action": record.correction_action,
                "correction_reason": record.correction_reason,
                "corrected_at": timezone.localtime(record.corrected_at).isoformat() if record.corrected_at else None,
                "corrected_by": request.user.username,
            },
        })


class DashboardStatsView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        today = timezone.localdate()

        total_employees = RegisteredEmployee.objects.filter(is_active=True).count()
        present_today = (
            EmployeeAttendanceApp.objects
            .filter(date=today)
            .values("employee_id")
            .distinct()
            .count()
        )
        checked_out = EmployeeAttendanceApp.objects.filter(date=today).exclude(fulltimeout=None).count()
        checked_in = EmployeeAttendanceApp.objects.filter(date=today, fulltimeout=None).count()

        warehouse_stats = [
            {"warehouseid": row["checkin_warehouse"] or row["matched_warehouse"] or "-", "present": row["present"]}
            for row in (
                EmployeeAttendanceApp.objects
                .filter(date=today)
                .values("checkin_warehouse", "matched_warehouse")
                .annotate(present=Count("id"))
                .order_by("checkin_warehouse", "matched_warehouse")
            )
        ]

        recent_qs = (
            EmployeeAttendanceApp.objects
            .select_related("employee")
            .filter(date=today)
            .order_by("-created_at")[:10]
        )

        def fmt(value) -> str:
            return timezone.localtime(value).strftime("%I:%M %p") if value is not None else "-"

        recent_activity = [
            {
                "name": r.employee.name,
                "action": "Check Out" if r.fulltimeout is not None else "Check In",
                "time": fmt(r.fulltimeout) if r.fulltimeout is not None else fmt(r.timein),
                "warehouse": (
                    r.checkout_warehouse
                    if r.fulltimeout is not None
                    else r.checkin_warehouse
                ) or r.matched_warehouse or r.employee.warehouseid or "-",
            }
            for r in recent_qs
        ]

        return Response({
            "total_employees": total_employees,
            "present_today": present_today,
            "absent_today": total_employees - present_today,
            "checked_in": checked_in,
            "checked_out": checked_out,
            "warehouse_stats": warehouse_stats,
            "recent_activity": recent_activity,
            "last_updated": timezone.localtime(timezone.now()).strftime("%I:%M:%S %p"),
        })

# =========================================================
# DJANGO HTML ADMIN PANEL VIEWS
# =========================================================
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages

def panel_login(request):
    if request.user.is_authenticated and request.user.is_staff:
        next_url = request.GET.get('next')
        if next_url:
            return redirect(next_url)
        return redirect('panel_dashboard')
        
    if request.method == 'POST':
        u = request.POST.get('username')
        p = request.POST.get('password')
        user = authenticate(request, username=u, password=p)
        if user is not None:
            if user.is_staff:
                login(request, user)
                next_url = request.GET.get('next')
                if next_url:
                    return redirect(next_url)
                return redirect('panel_dashboard')
            else:
                messages.error(request, "You do not have admin privileges.")
        else:
            messages.error(request, "Invalid username or password.")
            
    return render(request, 'login.html')

@login_required(login_url='panel_login')
def panel_dashboard(request):
    today = timezone.localdate()
    total_emp = RegisteredEmployee.objects.count()
    present_today = EmployeeAttendanceApp.objects.filter(date=today).count()
    
    stats = {
        'total_employees': total_emp,
        'present_today': present_today,
        'absent': total_emp - present_today,
    }
    
    recent_records = EmployeeAttendanceApp.objects.filter(date=today).order_by('-timein')[:10]
    
    return render(request, 'dashboard.html', {'stats': stats, 'recent_records': recent_records})

def panel_logout(request):
    logout(request)
    return redirect('panel_login')
