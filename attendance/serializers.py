from rest_framework import serializers

from .models import EmployeeAttendanceApp, RegisteredEmployee, Warehousedetail


class WarehouseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Warehousedetail
        fields = ["id", "full_name", "short_name", "latlong", "status", "remarks"]


class RegisteredEmployeeSerializer(serializers.ModelSerializer):
    face_sample_count = serializers.SerializerMethodField()

    class Meta:
        model = RegisteredEmployee
        fields = [
            "id",
            "employeecode",
            "name",
            "fathername",
            "attendancecode",
            "warehouseid",
            "latlong",
            "face_sample_count",
            "is_active",
            "created_at",
        ]

    def get_face_sample_count(self, obj):
        face_embeddings = getattr(obj, "face_embeddings", None)
        if face_embeddings is not None:
            try:
                return len(face_embeddings.all())
            except Exception:
                pass
        return 0


class EmployeeAttendanceAppSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.name", read_only=True)
    duration = serializers.SerializerMethodField()

    class Meta:
        model = EmployeeAttendanceApp
        fields = [
            "id",
            "employee_name",
            "date",
            "timein",
            "fulltimeout",
            "duration",
            "deviceinfo",
            "assignedlocation",
            "checkinlocation",
            "checkoutlocation",
            "created_at",
        ]

    def get_duration(self, obj):
        return obj.get_duration()
