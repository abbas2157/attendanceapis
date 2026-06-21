import django.db.models.deletion
from django.db import migrations, models


def copy_attendance_employee_fk(apps, schema_editor):
    Attendance = apps.get_model("attendance", "EmployeeAttendanceApp")

    for attendance in Attendance.objects.select_related("employee").all():
        if attendance.employee_id:
            attendance.employee_new_id = attendance.employee.pk
            attendance.save(update_fields=["employee_new"])


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0005_employeefaceembedding"),
    ]

    operations = [
        migrations.AddField(
            model_name="employeeattendanceapp",
            name="employee_new",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="attendance_records_tmp",
                to="attendance.registeredemployee",
            ),
        ),
        migrations.RunPython(copy_attendance_employee_fk, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="employeeattendanceapp",
            name="unique_employee_attendance_per_date",
        ),
        migrations.RemoveIndex(
            model_name="employeeattendanceapp",
            name="att_emp_date_idx",
        ),
        migrations.RemoveField(
            model_name="employeeattendanceapp",
            name="employee",
        ),
        migrations.RenameField(
            model_name="employeeattendanceapp",
            old_name="employee_new",
            new_name="employee",
        ),
        migrations.AlterField(
            model_name="employeeattendanceapp",
            name="employee",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="attendance_records",
                to="attendance.registeredemployee",
            ),
        ),
        migrations.AddIndex(
            model_name="employeeattendanceapp",
            index=models.Index(fields=["employee", "date"], name="att_emp_date_idx"),
        ),
        migrations.AddConstraint(
            model_name="employeeattendanceapp",
            constraint=models.UniqueConstraint(
                fields=("employee", "date"),
                name="unique_employee_attendance_per_date",
            ),
        ),
    ]
