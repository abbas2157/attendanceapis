from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from rest_framework_simplejwt.views import TokenRefreshView
from attendance.views_auth import CustomTokenObtainPairView, RegisterView
from attendance.views import panel_login, panel_dashboard, panel_logout

urlpatterns = [
    # Custom panel dashboard/logout — BEFORE admin/ catch-all
    path('admin/dashboard/', panel_dashboard, name='panel_dashboard'),
    path('admin/logout/',    panel_logout,    name='panel_logout'),

    # Django built-in admin (has its own /admin/login/)
    path('admin/', admin.site.urls),

    path('api/', include('attendance.urls')),

    # Auth APIs
    path('api/auth/login/',    CustomTokenObtainPairView.as_view(),  name='token_obtain_pair'),
    path('api/auth/refresh/',  TokenRefreshView.as_view(),           name='token_refresh'),
    path('api/auth/register/', RegisterView.as_view(),               name='auth_register'),

    # Custom panel login (uses panel/ prefix to avoid conflict with Django admin login)
    path('panel/login/',  panel_login,  name='panel_login'),

] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)