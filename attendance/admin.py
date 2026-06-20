from django.contrib import admin
from .models import (
    Warehousedetail,
    RegisteredEmployee,
    EmployeeFaceEmbedding,
    AttendanceVerificationAttempt,
    EmployeeAttendanceApp
)

@admin.register(Warehousedetail)
class WarehousedetailAdmin(admin.ModelAdmin):
    list_display = ('short_name', 'full_name', 'status', 'created_at')
    search_fields = ('short_name', 'full_name')

@admin.register(RegisteredEmployee)
class RegisteredEmployeeAdmin(admin.ModelAdmin):
    list_display = ('employeecode', 'name', 'attendancecode', 'is_active', 'created_at')
    search_fields = ('employeecode', 'name', 'attendancecode')
    list_filter = ('is_active',)

@admin.register(EmployeeFaceEmbedding)
class EmployeeFaceEmbeddingAdmin(admin.ModelAdmin):
    list_display = ('employee', 'sample_label', 'is_primary', 'created_at')
    list_filter = ('is_primary', 'capture_source')
    search_fields = ('employee__employeecode', 'employee__name')

@admin.register(AttendanceVerificationAttempt)
class AttendanceVerificationAttemptAdmin(admin.ModelAdmin):
    list_display = ('submitted_employeecode', 'verification_decision', 'attempt_status', 'created_at')
    list_filter = ('verification_decision', 'attempt_status')
    search_fields = ('submitted_employeecode', 'submitted_attendancecode')

@admin.register(EmployeeAttendanceApp)
class EmployeeAttendanceAppAdmin(admin.ModelAdmin):
    list_display = ('employee', 'date', 'timein', 'fulltimeout', 'get_duration')
    list_filter = ('date',)
    search_fields = ('employee__employeecode', 'employee__name')
