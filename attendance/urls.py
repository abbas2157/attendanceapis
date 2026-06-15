from django.urls import path
from .views import (
    WarehouseListView, WarehouseUpdateView, EmployeeView,
    EmployeeUpdateView, EmployeeFaceSampleUpdateView, AttendancePrecheckView, MarkAttendanceView, AttendanceRecordsView,
    AttendanceAttemptReviewView, AttendanceCorrectionView, DashboardStatsView
)

urlpatterns = [
    path('warehouses',         WarehouseListView.as_view()),
    path('warehouses/<int:pk>', WarehouseUpdateView.as_view()),
    path('warehouses/<int:pk>/', WarehouseUpdateView.as_view()),
    path('employees',          EmployeeView.as_view()),
    path('employees/<int:pk>',  EmployeeUpdateView.as_view()),
    path('employees/<int:pk>/', EmployeeUpdateView.as_view()),
    path('employees/<int:pk>/face-samples', EmployeeFaceSampleUpdateView.as_view()),
    path('employees/<int:pk>/face-samples/', EmployeeFaceSampleUpdateView.as_view()),
    path('attendance/precheck', AttendancePrecheckView.as_view()),
    path('attendance/mark',    MarkAttendanceView.as_view()),
    path('attendance/records', AttendanceRecordsView.as_view()),
    path('attendance/records/<int:pk>/correct-checkout', AttendanceCorrectionView.as_view()),
    path('attendance/attempts', AttendanceAttemptReviewView.as_view()),
    path('hr/stats',           DashboardStatsView.as_view()),
]
